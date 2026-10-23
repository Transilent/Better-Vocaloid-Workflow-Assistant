"""Dense interpolation must retain the earlier fit's detail and knot values."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from calibration_refinement import dense_points,detail_curves,finalize_expression,finalize_svp
from pitch_accuracy import audit_svp_pitch

t=np.arange(500)*.02
y=.13*np.sin(2*np.pi*5.5*t)+.03*np.sin(t*.8)
original=list(zip(t.tolist(),y.tolist()))
dense=np.asarray(dense_points(original))
assert len(dense)>len(original)*3.8
assert np.array_equal(dense[np.searchsorted(dense[:,0],t),1],y)
assert dense[:,1].min()>=y.min()-1e-12 and dense[:,1].max()<=y.max()+1e-12
assert abs(np.std(dense[:,1])-np.std(y))<.01,'Vibrato variation was flattened'
for a,b,ya,yb in zip(t[:-1],t[1:],y[:-1],y[1:]):
    local=dense[(dense[:,0]>=a)&(dense[:,0]<=b),1]
    assert local.min()>=min(ya,yb)-1e-12 and local.max()<=max(ya,yb)+1e-12

active=np.ones(len(t),bool);active[:10]=False;active[-10:]=False;active[150:153]=False
reference=np.zeros((len(t),12));reference[:,0]=-20;reference[:,3]=.95
curves,mask,report=detail_curves({'tension':y,'voicing':1-abs(y)/2},t,active,[(0.,10.,60)],reference)
assert report['original_supported_values_preserved'] and mask[150:153].all()
assert np.array_equal(curves['tension'][active],y[active])
assert np.array_equal(curves['voicing'][active],(1-abs(y)/2)[active])
assert not np.any(curves['tension'][:10])
_,rest,_=detail_curves({'tension':y},t,active,[(0.,3.,60),(3.1,10.,60)],reference)
assert not rest[150:153].any()
quiet=reference.copy();quiet[150:153,0]=-100
assert not detail_curves({'tension':y},t,active,[(0.,10.,60)],quiet)[1][150:153].any()
assert dense_points([(0.,0.),(1.,0.),(2.,0.)])==[(0.,0.),(2.,0.)]

# Reproduce the reported pitch artifact: neutral stretches with exact
# semitone/octave spikes, rather than a measured singing contour.
x=np.arange(500,dtype=float);bad=np.zeros(500);bad[::12]=1200
track=lambda values:{'mainGroup':{'parameters':{'pitchDelta':{'mode':'linear','points':np.column_stack((x,values)).ravel().tolist()}}}}
assert audit_svp_pitch(track(bad))['status']=='suspicious'
assert audit_svp_pitch(track(y*100))['status']=='ok'
assert audit_svp_pitch({})['status']=='absent'
assert audit_svp_pitch({'mainGroup':{'parameters':{'pitchDelta':{'points':[0,float('nan')]}}}})['status']=='invalid'
for points in ([(0,0),(0,1)],[(0,0),(1,float('nan'))],[]):
    try:dense_points(points);raise AssertionError('Invalid curve accepted')
    except ValueError:pass
try:dense_points(original,cancel=lambda:(_ for _ in ()).throw(InterruptedError('cancel')));raise AssertionError('Cancellation ignored')
except InterruptedError:pass

# Audit actual serialized SVP points, not just the pre-serialization fit grid.
import copy
import json
import tempfile
from expression_calibration import CONTROLS
from vocal_expression import TempoMap,SV_QUARTER
from unittest.mock import patch
clock=TempoMap([{'position':0,'bpm':120},{'position':2*SV_QUARTER,'bpm':83}])
offset=SV_QUARTER//3
position=lambda t:clock.to_position(t)-offset
curve_times=[0.,.2,.4,.6,1.5,2.,2.4,3.,4.,5.,1000.]
values=[0.,0.,.1,-.1,.15,-.12,.08,0.,0.,0.,0.]
params={name:{'mode':'linear','points':[v for pair in zip([position(t) for t in curve_times],
    [neutral+step*(abs(y) if name=='voicing' else y) for y in values]) for v in pair]} for name,(_,_,neutral,step) in CONTROLS.items()}
params['pitchDelta']={'mode':'cubic','points':[position(0.),-10.,position(1.),18.,position(3.),-6.]}
params['vibratoEnv']={'mode':'linear','points':[0,.5,SV_QUARTER,.8]}
project={'time':{'tempo':[{'position':0,'bpm':120},{'position':2*SV_QUARTER,'bpm':83}]},
    'library':[{'uuid':'keep-library','parameters':copy.deepcopy(params)}],
    'tracks':[{'name':'Lead','mainRef':{'blickOffset':offset,'database':{'name':'Test Voice'}},
        'mainGroup':{'notes':[{'onset':position(.2),'duration':position(5.)-position(.2),
            'pitch':60,'lyrics':'held','attributes':{'dF0Vbr':.2}}],
            'parameters':params},'groups':[{'groupID':'keep-library'}]},
        {'mainRef':{'isInstrumental':True,'audio':{'filename':'unchanged.wav'}},
         'mainGroup':{'parameters':copy.deepcopy(params)}}]}
original=copy.deepcopy(project)
report=finalize_expression(project)
assert report['step_seconds']==.005 and report['pitch_and_notes_untouched']
assert len(report['tracks'])==1
for name in CONTROLS:
    before=original['tracks'][0]['mainGroup']['parameters'][name]['points']
    after=project['tracks'][0]['mainGroup']['parameters'][name]['points']
    lookup=dict(zip(after[::2],after[1::2]))
    assert all(lookup[x]==v for x,v in zip(before[::2],before[1::2]))
    state=report['tracks'][0]['controls'][name]
    assert 700<state['added_points']<1200,'A long neutral rest was unnecessarily expanded'
    assert state['max_changing_gap_ms']<=5.00001
    assert min(after[1::2])==min(before[1::2]) and max(after[1::2])==max(before[1::2])
    # Original editor interpolation metadata and every unrelated project field survive.
    project['tracks'][0]['mainGroup']['parameters'][name]=copy.deepcopy(original['tracks'][0]['mainGroup']['parameters'][name])
assert project==original
finalize_expression(project)
once=copy.deepcopy(project);second=finalize_expression(project)
assert project==once and all(c['added_points']==0 for c in second['tracks'][0]['controls'].values())

# Keep nonlinear curves and absent controls; flat held notes receive real density.
unchanged=copy.deepcopy(original)
unchanged['tracks'][0]['mainGroup']['parameters']={
    'tension':{'mode':'cubic','points':[0,0,SV_QUARTER,.1]},
    'voicing':{'mode':'linear','points':[0,1,SV_QUARTER,1,2*SV_QUARTER,1]}}
prior=copy.deepcopy(unchanged);kept=finalize_expression(unchanged)
assert unchanged['tracks'][0]['mainGroup']['parameters']['tension']==prior['tracks'][0]['mainGroup']['parameters']['tension']
assert kept['tracks'][0]['controls']['tension']['status']=='preserved_nonlinear'
assert kept['status']=='partial_unsupported' and kept['tracks'][0]['controls']['voicing']['added_points']>10
assert all(v==1 for v in unchanged['tracks'][0]['mainGroup']['parameters']['voicing']['points'][1::2])
# Invalid later controls or cancellation cannot partially replace earlier curves.
invalid=copy.deepcopy(original);invalid['tracks'][0]['mainGroup']['parameters']['toneShift']['points']=[0,0,0,1]
prior=copy.deepcopy(invalid)
try:finalize_expression(invalid);raise AssertionError('Invalid curve accepted')
except ValueError:pass
assert invalid==prior
cancelled=copy.deepcopy(original);calls=[0]
def stop():
    calls[0]+=1
    if calls[0]>4:raise InterruptedError('cancel')
try:finalize_expression(cancelled,cancel=stop);raise AssertionError('Cancellation ignored')
except InterruptedError:pass
assert cancelled==original
with tempfile.TemporaryDirectory(prefix='bvwa-density-') as folder:
    path=Path(folder)/'curves.svp'
    raw=json.dumps(original,ensure_ascii=False).encode()+b'\x00';path.write_bytes(raw)
    written=finalize_svp(path)
    assert written['changed'] and path.read_bytes().endswith(b'\x00')
    assert json.loads(path.read_bytes()[:-1])==once
    serialized=path.read_bytes();stamp=path.stat().st_mtime_ns
    assert not finalize_svp(path)['changed']
    assert path.read_bytes()==serialized and path.stat().st_mtime_ns==stamp
    path.write_bytes(raw)
    with patch('os.replace',side_effect=OSError('simulated disk failure')):
        try:finalize_svp(path);raise AssertionError('Failed commit accepted')
        except OSError:pass
    assert path.read_bytes()==raw and not list(Path(folder).glob('*.pending'))
print('PASS: dense 5-ms curves retain original knots, vibrato and amplitude without overshoot; gaps/rests and pitch-quality diagnostics')
