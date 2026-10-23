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
print('PASS: three styles, source variation, phase/rest guards, bank/pitch/unknown protection, headroom, partial receipts, independent I/O, no stacking and cancellation')
