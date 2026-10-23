"""Temporal stability and voice-identity guards for measured calibration."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from calibration_refinement import refine_curves,FEATURE_WEIGHTS,CONTROL_PENALTIES
from expression_calibration import CONTROLS,fit_response


def raises(kind,call,*args,**kwargs):
    try:call(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)


t=np.arange(300)*.02
baseline=np.zeros((len(t),12));baseline[:,0]=-20;baseline[:,3]=.95
reference=baseline.copy();active=(t>=.2)&(t<5.8)
notes=[(.2,2.6,60),(2.6,5.8,62)]
slow=.075*np.sin(t*1.2)+.08
jitter=.065*np.where(np.arange(len(t))%2,1,-1)
raw={'tension':slow+jitter,'breathiness':np.full(len(t),.18),
     'voicing':np.full(len(t),.88),'gender':np.where(np.arange(len(t))%7<3,.15,-.15),
     'toneShift':np.where(np.arange(len(t))%7<3,120.,-120.),'loudness':1.2*np.sin(t*1.2)+jitter*10}
curves,support,report=refine_curves(raw,t,active,notes,reference,baseline,.6)
assert report['perceptual_improvement_verified'] is False
assert 'breathiness' not in curves and 'breathiness' in report['omitted_neutral_controls']
assert report['controls']['tension']['total_variation_after']<report['controls']['tension']['total_variation_before']*.15
body=(t>.8)&(t<5.2)
assert np.corrcoef(curves['tension'][body],slow[body])[0,1]>.96,'Lost slow expressive shape'
assert curves['tension'][body].std()>.025,'Long notes became flat'
assert curves['voicing'][body].min()>=1-.035*.6-1e-9
limits={'loudness':1.8,'tension':.255*.6,'breathiness':.15*.6,'voicing':.06*.6,'gender':.03,'toneShift':24.}
rates={'loudness':18.,'tension':1.4,'breathiness':.8,'voicing':.45,'gender':.10,'toneShift':70.}
for name,values in curves.items():
    neutral=CONTROLS[name][2]
    assert np.isfinite(values).all() and np.all(values[~support]==neutral)
    assert max(abs(values-neutral))<=limits[name]+1e-9
    assert max(abs(np.diff(np.r_[neutral,values,neutral])))<=rates[name]*.6*.02+1e-9

# Repair brief analysis loss in continuous audible singing without inventing
# evidence. Neither a score rest nor an unvoiced/quiet gap may be bridged.
gap=active.copy();gap[80:83]=False
curves,mask,stats=refine_curves({'tension':np.full(len(t),.12)},t,gap,notes,reference,baseline,.6)
assert mask[80:83].all() and stats['bridged_gaps']==1 and stats['bridged_seconds']==.06
assert min(curves['tension'][77:86])>.1
quiet=reference.copy();quiet[80:83,0]=-80
assert not refine_curves(raw,t,gap,notes,quiet,baseline,.6)[1][80:83].any()
unvoiced=reference.copy();unvoiced[80:83,3]=.1
assert not refine_curves(raw,t,gap,notes,unvoiced,baseline,.6)[1][80:83].any()
rest=[(.2,1.6,60),(1.66,5.8,62)]
assert not refine_curves(raw,t,gap,rest,reference,baseline,.6)[1][80:83].any()
long_gap=active.copy();long_gap[80:86]=False
assert not refine_curves(raw,t,long_gap,notes,reference,baseline,.6)[1][80:86].any()

# Sustained, jointly voiced/noisy evidence admits a small breath contribution.
breathy=reference.copy();breathy[:,2]+=3;breathy[:,3]=.87
both,_,_=refine_curves(raw,t,active,notes,breathy,baseline,.6)
assert 'breathiness' in both and 0<both['breathiness'][body].max()<=.045+1e-9
voice_only,_,_=refine_curves({'voicing':raw['voicing']},t,active,notes,breathy,baseline,.6)
overlap=body&(both['breathiness']>.03)
assert overlap.any() and np.all(both['voicing'][overlap]>voice_only['voicing'][overlap])
assert max(abs(np.diff(both['voicing'])))<=rates['voicing']*.6*.02+1e-9
weak_breath,_,_=refine_curves({'breathiness':np.full(len(t),.003),'voicing':raw['voicing']},
                            t,active,notes,breathy,baseline,.6)
assert 'breathiness' not in weak_breath and np.array_equal(weak_breath['voicing'],voice_only['voicing'])
impulse=reference.copy();impulse[100:103,2]=3;impulse[100:103,3]=.85
assert 'breathiness' not in refine_curves(raw,t,active,notes,impulse,baseline,.6)[0]
assert refine_curves(raw,t,active,notes,reference,baseline,0)[0]=={}
assert refine_curves(raw,t,np.zeros(len(t),bool),notes,reference,baseline,.6)[0]=={}
assert refine_curves({'tension':np.full(len(t),.1)},t,active,[(.2,.5,60)],reference,baseline,.6)[0], 'Brief notes lost all gestures'
for invalid in (np.zeros(len(t)),np.zeros((len(t),2))):
    raises(ValueError,refine_curves,raw,t,active,notes,invalid,invalid,.6)
raises(ValueError,refine_curves,raw,t,active,notes,reference,baseline,True)
raises(InterruptedError,refine_curves,raw,t,active,notes,reference,baseline,.6,
       cancel=lambda:(_ for _ in ()).throw(InterruptedError('cancel')))

# Identity penalties must constrain the solver itself, before postprocessing.
zero=np.zeros((len(t),12))
probes={name:zero+np.eye(12)[i]*3 for i,name in enumerate(CONTROLS)}
steps={name:CONTROLS[name][3] for name in probes}
target=sum((probes[name]*.4 for name in probes))
raw_fit,_=fit_response(target,zero,probes,steps)
guarded,_=fit_response(target,zero,probes,steps,feature_weights=FEATURE_WEIGHTS,control_penalties=CONTROL_PENALTIES)
assert np.max(abs(guarded.get('gender',np.zeros(len(t)))))<max(abs(raw_fit['gender']))*.65
assert np.max(abs(guarded.get('toneShift',np.zeros(len(t)))))<max(abs(raw_fit['toneShift']))*.65
for kwargs in ({'feature_weights':[1]*2},{'feature_weights':[float('nan')]*12},
               {'control_penalties':{'gender':0}},{'control_penalties':{'invented':2}}):
    raises(ValueError,fit_response,target,zero,probes,steps,**kwargs)
print('PASS: slower expressive contours, held-note stability, guarded dropouts/rests, bounded identity transfer, breath/voicing evidence, slew bounds, no neutral placeholders, weighted fitting and cancellation')
