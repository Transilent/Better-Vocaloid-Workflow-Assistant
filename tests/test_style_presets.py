"""Independent style candidates preserve authored curves, score and recovery."""
from pathlib import Path
import copy,json,os,subprocess,sys,tempfile
from unittest.mock import patch
import numpy as np
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
import style_presets as styles
from vocal_expression import SV_QUARTER as Q,TempoMap


def project():
    p={'mode':'linear','points':[0,0.,Q,-.1,2*Q,.12,3*Q,-.04,4*Q,.08,8*Q,0.,10*Q,0.], 'custom':'keep'}
    track={'name':'Lead Vocal','mainRef':{'blickOffset':Q,'pitchOffset':0,'isInstrumental':False,
        'database':{'name':'test bank'},'voice':{'preset':'retain'}},
        'mainGroup':{'uuid':'kept','notes':[{'onset':0,'duration':8*Q,'pitch':60,'lyrics':'啊','attributes':{'retakes':[1,2]}}],
          'parameters':{'loudness':copy.deepcopy(p),'tension':copy.deepcopy(p),'breathiness':copy.deepcopy(p),
             'voicing':{'mode':'linear','points':[0,.9,2*Q,.86,4*Q,.94,10*Q,.9]},
             'pitchDelta':{'mode':'cubic','points':[0,0,Q,45,2*Q,-28]},
             'unknown':{'mode':'cosine','points':[0,1,Q,2]}}},'mixer':{'gainDecibel':-2}}
    backing=copy.deepcopy(track);backing['name']='Backing Vocal';backing['mainGroup']['uuid']='backing'
    return {'version':125,'time':{'tempo':[{'position':0,'bpm':120.},{'position':4*Q,'bpm':90.}],
        'meter':[{'index':0,'numerator':4,'denominator':4}]},'tracks':[track,backing,
        {'name':'Instrumental','mainRef':{'isInstrumental':True,'audio':{'filename':'../audio/inst.wav'}},'mainGroup':{'notes':[],'parameters':{}}}],
        'library':[{'unused':'retain me'}],'unknownRoot':[1,2,3]}


original=project();frozen=copy.deepcopy(original);clock=TempoMap(original['time']['tempo'])
outputs={}
for preset in ('soft_ballad','intimate_narration','power_pop'):
    out,receipt=styles.apply_style(original,preset,strength=.6,track_indices=[0],density_mode='dense')
    assert original==frozen and receipt['status']=='applied'
    assert out['tracks'][1:]==original['tracks'][1:]
    assert out['tracks'][0]['mainRef']==original['tracks'][0]['mainRef']
    assert out['tracks'][0]['mainGroup']['notes']==original['tracks'][0]['mainGroup']['notes']
    assert out['tracks'][0]['mainGroup']['parameters']['pitchDelta']==original['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    assert out['tracks'][0]['mainGroup']['parameters']['unknown']==original['tracks'][0]['mainGroup']['parameters']['unknown']
    assert out['library']==original['library'] and out['time']==original['time']
    for name,state in receipt['tracks'][0]['controls'].items():
        before=original['tracks'][0]['mainGroup']['parameters'][name];after=out['tracks'][0]['mainGroup']['parameters'][name]
        assert after.get('custom')==before.get('custom')
        assert set(before['points'][::2])<=set(after['points'][::2])
        values=dict(zip(after['points'][::2],after['points'][1::2]))
        assert values[10*Q]==before['points'][-1], 'Unselected rest changed'
        # Two source knots in the sustained core receive an equal deliberate
        # offset: the pre-existing oscillation must not be flattened away.
        if name=='tension':
            old=dict(zip(before['points'][::2],before['points'][1::2]))
            assert abs((values[3*Q]-values[2*Q])-(old[3*Q]-old[2*Q]))<1e-10
    outputs[preset]=out
assert outputs['soft_ballad']!=outputs['intimate_narration']!=outputs['power_pop']
for preset,amount in (('none',.6),('power_pop',0.)):
    out,r=styles.apply_style(original,preset,strength=amount)
    assert out==original and r['status']=='not_selected'

for invalid in ({'performance_style':'unknown'},{'performance_style':[]},{'style_strength':True},
                {'style_strength':float('nan')},{'style_strength':float('inf')},
                {'style_strength':-1},{'style_strength':1.01},{'style_scope':'backing'}):
    try:styles.settings(invalid);raise AssertionError(invalid)
    except ValueError:pass

# Legacy task/preset storage accepts the same style IDs without turning on
# pitch for an existing explicit opt-out or applying style during preparation.
import workflow
for preset in ('melancholy','bright_joy','fragile_tender'):
    preferences=workflow.validate_options({'performance_style':preset,'style_pitch':False})
    exported=workflow.export_preferences(preferences)
    restored=workflow.validate_options(exported)
    assert restored['performance_style']==preset and restored['style_pitch'] is False
    prepared=workflow.execution_options(preferences)
    assert prepared['prepare_only'] and prepared['performance_style']=='none' and not prepared['style_pitch']

# Headroom is shared across an entire control. Peaks retain their differences;
# the original curve is not clipped or renormalized into a new flat line.
limited=project();curve=limited['tracks'][0]['mainGroup']['parameters']['tension']
curve['points']=[0,.92,Q,.93,2*Q,.94,3*Q,.98,4*Q,.92,10*Q,.92]
out,r=styles.apply_style(limited,'power_pop',strength=1.,track_indices=[0],density_mode='original')
state=r['tracks'][0]['controls']['tension'];assert 0<state['headroom_scale']<1
new=dict(zip(out['tracks'][0]['mainGroup']['parameters']['tension']['points'][::2],out['tracks'][0]['mainGroup']['parameters']['tension']['points'][1::2]))
assert max(new.values())<=1+1e-12 and abs((new[3*Q]-new[2*Q])-.04)<1e-10

# Unsupported editor interpolation and out-of-nominal source values survive
# intact; the receipt must expose partial application rather than fake success.
mixed=project();mixed['tracks'][0]['mainGroup']['parameters']['tension']['mode']='cubic'
mixed['tracks'][0]['mainGroup']['parameters']['breathiness']['points']=[0,-2.,10*Q,2.]
out,r=styles.apply_style(mixed,'power_pop',track_indices=[0])
assert r['status']=='partial' and r['skipped_controls']==2
for name in ('tension','breathiness'):
    assert out['tracks'][0]['mainGroup']['parameters'][name]==mixed['tracks'][0]['mainGroup']['parameters'][name]

linked=project();linked['tracks'][0]['groups']=[{'groupID':'library'}]
try:styles.apply_style(linked,'soft_ballad',track_indices=[0]);raise AssertionError('Linked groups accepted')
except ValueError:pass
assert styles.apply_style(linked,'soft_ballad',track_indices=[1])[0]['tracks'][0]==linked['tracks'][0]
with patch.object(styles,'MAX_POINTS',10):
    try:styles.apply_style(original,'power_pop');raise AssertionError('Budget ignored')
    except ValueError:pass

with tempfile.TemporaryDirectory(prefix='style-presets-',dir=APP/'work') as folder:
    root=Path(folder);source=root/'A 原表现.svp';destination=root/'B 柔和.svp'
    source.write_bytes(json.dumps(original,ensure_ascii=False).encode('utf-8')+b'\x00')
    raw=source.read_bytes()
    first=styles.apply_style_svp(source,destination,preset='soft_ballad',track_indices=[0])
    contents=destination.read_bytes();second=styles.apply_style_svp(source,destination,preset='soft_ballad',track_indices=[0])
    assert first==second and contents==destination.read_bytes() and raw==source.read_bytes()
    command=subprocess.run([sys.executable,'-B',str(APP/'style_presets.py'),str(source),str(root/'CLI 候选.svp'),
        '--preset','power_pop','--tracks','0'],cwd=APP,capture_output=True,
        env={**os.environ,'PYTHONIOENCODING':'ascii'},timeout=30)
    assert command.returncode==0,command.stderr.decode('utf-8',errors='replace')
    assert '有力流行'.encode('utf-8') in command.stdout
    assert json.loads(destination.read_text(encoding='utf-8'))==outputs['soft_ballad']
    try:styles.apply_style_svp(destination,root/'C.svp',preset='soft_ballad');raise AssertionError('Candidate stacked')
    except ValueError:pass
    assert not (root/'C.svp').exists()
    destination.write_bytes(contents+b' ')
    try:styles.apply_style_svp(source,destination,preset='soft_ballad');raise AssertionError('User edit overwritten')
    except FileExistsError:pass
    assert destination.read_bytes()==contents+b' '
    try:styles.apply_style_svp(source,source,preset='power_pop');raise AssertionError('Input overwritten')
    except ValueError:pass
    def cancelled():raise InterruptedError('test cancellation')
    try:styles.apply_style_svp(source,root/'cancelled.svp',preset='power_pop',cancel=cancelled);raise AssertionError('Cancel ignored')
    except InterruptedError:pass
    assert not (root/'cancelled.svp').exists() and source.read_bytes()==raw

# Canonical cards use note-local envelopes. Old IDs still execute their
# original recipes; only the explicit UI mapping changes.
assert styles.VERSION==6
assert styles.visible_presets()==('none','gentle','sad','bold')
assert styles.PRESETS['gentle']['note_envelope']=={'onset':.14,'release':.22,'sustain_arc':{'loudness':.65,'tension':.04,'breathiness':-.015}}
assert styles.PRESETS['gentle']['controls']=={'loudness':(-3.2,-.8,-2.4),'tension':(-.36,-.10,-.26),'breathiness':(.20,.03,.20),'voicing':(-.04,0.,-.08)}
assert styles.PRESETS['sad']['note_envelope']=={'onset':.19,'release':.30,'sustain_arc':{'loudness':-.75,'tension':-.06,'breathiness':.035}}
assert styles.PRESETS['sad']['controls']=={'loudness':(-.8,-.15,-3.0),'tension':(-.06,-.03,-.25),'breathiness':(.04,0.,.18),'voicing':(-.015,0.,-.08)}

assert {styles.merged_preset(p) for p in ('soft_ballad','intimate_narration')}=={'gentle'}
assert {styles.merged_preset(p) for p in ('melancholy','fragile_tender')}=={'sad'}
assert {styles.merged_preset(p) for p in ('power_pop','bright_joy')}=={'bold'}
assert [styles.pitch_default(p) for p in ('none','gentle','sad','bold')]==[False,True,True,True]
assert styles.pitch_default('soft_ballad') is False and styles.pitch_default('intimate_narration') is False
assert all(styles.pitch_default(p) for p in ('melancholy','fragile_tender','bright_joy'))
for legacy in ('soft_ballad','intimate_narration','melancholy','fragile_tender','power_pop','bright_joy'):
    _,legacy_receipt=styles.apply_style(original,legacy,strength=0.,pitch_enabled=False)
    assert legacy_receipt['preset']==legacy and legacy_receipt['label']==styles.PRESETS[legacy]['label']
assert styles.detail_settings({'onset':.625})=={'onset':.625,'sustain':1.,'connection':1.,'release':1.}
for invalid in ([],False,{'unknown':.5},{'onset':True},{'sustain':float('nan')},{'release':1.01}):
    try:styles.detail_settings(invalid);raise AssertionError(invalid)
    except ValueError:pass

# Technique settings and regions remain explicit, while an old region keeps its
# original six-field receipt verbatim.
assert styles.technique_settings({})=={'style_technique':'none','technique_strength':.6,'technique_pitch':True}
assert styles.technique_settings({'style_technique':'chinese_opera','technique_strength':.35,'technique_pitch':False})=={
    'style_technique':'chinese_opera','technique_strength':.35,'technique_pitch':False}
assert styles.region_settings([{'start':.2,'end':.7,'preset':'gentle'}])[0]=={
    'start':.2,'end':.7,'preset':'gentle','strength':.6,'detail':dict(styles.DETAIL_DEFAULTS),'pitch':True}
assert styles.region_settings([{'start':.2,'end':.7,'preset':'none','technique':'chinese_opera',
    'technique_strength':.35,'technique_pitch':False}])[0]=={
    'start':.2,'end':.7,'preset':'none','strength':.6,'detail':dict(styles.DETAIL_DEFAULTS),'pitch':False,
    'technique':'chinese_opera','technique_strength':.35,'technique_pitch':False}
for invalid in ({'style_technique':None},{'style_technique':'unknown'},{'technique_strength':None},{'technique_strength':True},{'technique_strength':float('nan')},
                {'technique_strength':-0.01},{'technique_strength':1.01},{'technique_pitch':None},{'technique_pitch':1}):
    try:styles.technique_settings(invalid);raise AssertionError(invalid)
    except ValueError:pass
try:
    styles.region_settings([{'start':.2,'end':.7,'preset':'none'}]);raise AssertionError('Technique-free none region accepted')
except ValueError:pass
source=project();frozen_technique=copy.deepcopy(source)
solo,technique_receipt=styles.apply_style(source,'chinese_opera',strength=.45,track_indices=[0],
    pitch_enabled=False,density_mode='original')
assert source==frozen_technique
assert technique_receipt['category']=='singing_technique' and technique_receipt['preset']=='chinese_opera'
assert technique_receipt['status']=='applied' and not technique_receipt['pitch_changed']
assert solo['tracks'][0]['mainGroup']['parameters']['pitchDelta']==source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
assert solo['tracks'][0]['mainGroup']['notes']==source['tracks'][0]['mainGroup']['notes']
assert solo['tracks'][1:]==source['tracks'][1:]

for preset in ('gentle','sad','bold'):
    out,r=styles.apply_style(original,preset,track_indices=[0],density_mode='original',pitch_enabled=False)
    assert original==frozen and out['tracks'][1:]==original['tracks'][1:]
    assert r['tracks'][0]['envelope_scope']=='note' and r['notes_unchanged']
    assert r['tracks'][0]['controls']['loudness']['maximum_applied_delta']>0
    disabled,rd=styles.apply_style(original,preset,track_indices=[0],detail=dict.fromkeys(styles.DETAIL_DEFAULTS,0.))
    assert disabled==original and rd['status']=='not_selected'
    # Exact old coordinates remain, including fractional coordinates.
    for channel in styles.PRESETS[preset]['controls']:
        before=original['tracks'][0]['mainGroup']['parameters'][channel]
        after=out['tracks'][0]['mainGroup']['parameters'][channel]
        assert set(before['points'][::2])<=set(after['points'][::2])
        assert after.get('custom')==before.get('custom')

protected=project()
protected['tracks'][0]['mainGroup']['notes']=[
    {'onset':0,'duration':Q//8,'pitch':60,'lyrics':'快'},
    {'onset':Q,'duration':Q,'pitch':60,'lyrics':'br'},
    {'onset':2*Q,'duration':Q,'pitch':60,'lyrics':'啊','musicalType':'rap'},
    {'onset':3*Q,'duration':Q,'pitch':60,'lyrics':'啊','attributes':{'muted':True}},
    {'onset':4*Q,'duration':2*Q,'pitch':60,'lyrics':'啊'},
    {'onset':5*Q,'duration':2*Q,'pitch':64,'lyrics':'哈'}]
for preset in ('gentle','sad','bold'):
    out,r=styles.apply_style(protected,preset,track_indices=[0])
    assert out==protected and r['status']=='skipped' and r['protected_notes']==6

# A time window cuts the original envelope rather than inventing a new note
# onset at its left edge. Outside the window the original linear function is
# unchanged, with a smooth zero-delta boundary even through a sustained note.
source=project();bounds=(1.25,2.25)
range_outputs={}
for preset in ('gentle','sad','bold'):
    out,r=styles.apply_style(source,preset,track_indices=[0],time_range=bounds,
                             pitch_enabled=False,density_mode='original')
    channel_deltas={}
    for name in styles.PRESETS[preset]['controls']:
        old=source['tracks'][0]['mainGroup']['parameters'][name]['points']
        new=out['tracks'][0]['mainGroup']['parameters'][name]['points']
        xs=np.asarray(new[::2]);times=np.asarray([clock.to_seconds(float(p)+Q) for p in xs])
        difference=np.asarray(new[1::2])-np.interp(xs,old[::2],old[1::2])
        outside=(times<=bounds[0]+1e-10)|(times>=bounds[1]-1e-10)
        assert np.max(abs(difference[outside]),initial=0.)<1e-10
        channel_deltas[name]=np.max(abs(difference),initial=0.)
    assert all(channel_deltas[name]>0 for name in ('loudness','tension','breathiness'))
    assert channel_deltas.get('voicing',0.)==0.,'Sustained voiced core should retain its source voicing'
    range_outputs[preset]=out
assert len({json.dumps(v,sort_keys=True) for v in range_outputs.values()})==3

# Gentle is a voice treatment; sad is a separate emotion card. Their long-note
# arcs move in opposite directions, and only sad retains the deliberate long fall.
gentle_out,_=styles.apply_style(source,'gentle',track_indices=[0],pitch_enabled=False,density_mode='original')
sad_out,_=styles.apply_style(source,'sad',track_indices=[0],pitch_enabled=False,density_mode='original')
assert styles.PRESETS['gentle']['controls']['loudness'][-1]>styles.PRESETS['sad']['controls']['loudness'][-1]
assert styles.PRESETS['gentle']['note_envelope']['sustain_arc']['loudness']>0>styles.PRESETS['sad']['note_envelope']['sustain_arc']['loudness']
assert gentle_out!=sad_out

# Local phase factors change their own contribution, not the stored total.
full,rf=styles.apply_style(source,'bold',strength=.625,track_indices=[0],pitch_enabled=False,density_mode='original')
less,rl=styles.apply_style(source,'bold',strength=.625,detail={'onset':0.},track_indices=[0],pitch_enabled=False,density_mode='original')
assert rf['strength']==rl['strength']==.625
def delta_at(data,name,seconds):
    curve=data['tracks'][0]['mainGroup']['parameters'][name]['points']
    pos=clock.to_position(seconds)-Q
    return np.interp(pos,curve[::2],curve[1::2])
assert delta_at(less,'loudness',.52)<delta_at(full,'loudness',.52)
assert abs(delta_at(less,'loudness',2.)-delta_at(full,'loudness',2.))<1e-10
print('PASS: canonical cards, legacy recipes/mapping/defaults, sad region recipe, phase/range guards, protected notes, headroom, independent I/O, no stacking and cancellation')
