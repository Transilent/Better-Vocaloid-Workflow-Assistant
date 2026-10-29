"""Actual additive pitch gestures, unsupported preservation and cache boundaries."""
import copy,json,sys,tempfile
from pathlib import Path
import numpy as np
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
import style_presets as styles
import style_pitch as pitches
from style_pitch import enabled
from vocal_expression import TempoMap,SV_QUARTER as Q

def project():
    notes=[{'onset':0,'duration':Q,'pitch':60,'lyrics':'你'},
           {'onset':Q,'duration':Q,'pitch':64,'lyrics':'好'},
           {'onset':2*Q,'duration':3*Q,'pitch':62,'lyrics':'啊','attributes':{'dF0Vbr':0}},
           {'onset':7*Q,'duration':Q,'pitch':65,'lyrics':'啦'}]
    track={'name':'Lead Vocal','mainRef':{'blickOffset':Q,'isInstrumental':False,'voice':{'preset':'keep'}},
           'mainGroup':{'notes':notes,'parameters':{
               'pitchDelta':{'mode':'linear','points':[0,2.,Q,3.,2*Q,4.,5*Q,6.,9*Q,1.],'metadata':'keep'},
               'vibratoEnv':{'mode':'cubic','points':[0,1.,8*Q,.8]},
               'unknown':{'mode':'cosine','points':[0,4.,Q,5.]}}},'retakes':[{'pitch':77}]}
    backing=copy.deepcopy(track);backing['name']='Backing Vocal'
    return {'time':{'tempo':[{'position':0,'bpm':120.},{'position':4*Q,'bpm':90.}],
                    'meter':[{'index':0,'numerator':4,'denominator':4}]},
            'tracks':[track,backing],'library':[{'private':'keep'}],'other':[1,2,3]}

assert pitches.VERSION==6
assert pitches.RECIPES['gentle']=={'entry':(-20.,.11),'connection':(1.8,16.,.07),'release':(-12.,.09),'vibrato':None}
assert pitches.RECIPES['sad']=={'entry':(-12.,.18),'connection':(4.,32.,.13),'release':(-58.,.30),'vibrato':None}
assert pitches.RECIPES['melancholy']=={'entry':(-16.,.13),'connection':(1.5,10.,.09),'release':(-24.,.20),'vibrato':None}
assert pitches.RECIPES['fragile_tender']=={'entry':(6.,.08),'connection':(1.,8.,.06),'release':(24.,.16),'vibrato':None}
assert pitches.RECIPES['bright_joy']=={'entry':(12.,.06),'connection':(1.7,11.,.055),'release':(-7.,.08),'vibrato':None}
assert pitches.RECIPES['chinese_opera']=={'entry':(-220.,.24),'connection':(20.,180.,.20),'release':(160.,.30),'vibrato':(48.,5.3,.30)}

# The technique recipe adds structural gestures while existing vibrato, notes,
# and the caller's project object remain untouched. Its pitch switch is explicit.
source=project();frozen=copy.deepcopy(source)
output,report=styles.apply_style(source,'chinese_opera',strength=.55,track_indices=[0],
                                 pitch_enabled=True,density_mode='original')
row=report['pitch']['tracks'][0]
assert source==frozen and report['category']=='singing_technique'
assert {'phrase_entry','interval_connection','phrase_release'}<={g['kind'] for g in row['gestures']}
assert not any(g['kind']=='delayed_vibrato' for g in row['gestures'])
assert output['tracks'][0]['mainGroup']['parameters']['vibratoEnv']==source['tracks'][0]['mainGroup']['parameters']['vibratoEnv']
assert output['tracks'][0]['mainGroup']['notes']==source['tracks'][0]['mainGroup']['notes']
voice_only,voice_report=styles.apply_style(source,'chinese_opera',strength=.55,track_indices=[0],
                                          pitch_enabled=False,density_mode='original')
assert not voice_report['pitch_changed']
assert voice_only['tracks'][0]['mainGroup']['parameters']['pitchDelta']==source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
assert voice_only['tracks'][0]['mainGroup']['parameters']['vibratoEnv']==source['tracks'][0]['mainGroup']['parameters']['vibratoEnv']

# Opera: changes follow the observed transition direction without copying the
# X Studio sample's extreme excursions. Protect authored slides and keep
# short intervening notes from being misidentified as adjacent.
opera=project();opera['tracks'][0]['mainGroup']['notes']=[
    {'onset':i*Q,'duration':Q,'pitch':p,'lyrics':'字'}
    for i,p in enumerate((60,62,55,55))]
opera['tracks'][0]['mainGroup']['parameters']['pitchDelta']={'mode':'linear','points':[0,0.,4*Q,0.]}
before=copy.deepcopy(opera)
out,audit=styles.apply_style(opera,'chinese_opera',strength=1.,track_indices=[0],
                             pitch_enabled=True,density_mode='original')
gestures=audit['pitch']['tracks'][0]['gestures']
connections=[g for g in gestures if g['kind']=='interval_connection']
assert [g['note_index'] for g in connections]==[1,2,3]
assert connections[0]['cents_at_full_strength']>0>connections[1]['cents_at_full_strength']
assert connections[2]['cents_at_full_strength']<0
assert connections[1]['start']<1.5<connections[1]['end']
assert np.isclose(connections[1]['start']+(connections[1]['end']-connections[1]['start'])*
                  connections[1]['peak_fraction'],1.5)
curve=out['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
transition=np.asarray(curve[::2]); values=np.asarray(curve[1::2])
assert np.interp(2*Q,transition,values)<-100
assert abs(np.interp(2*Q-1000,transition,values)-np.interp(2*Q+1000,transition,values))<1
assert audit['pitch']['tracks'][0]['maximum_applied_delta_cents']<300
assert opera==before and out['tracks'][0]['mainGroup']['notes']==before['tracks'][0]['mainGroup']['notes']

protected=copy.deepcopy(opera)
protected['tracks'][0]['mainGroup']['notes'].insert(1,
    {'onset':Q,'duration':Q//10,'pitch':61,'lyrics':'短'})
protected['tracks'][0]['mainGroup']['notes'][2]['onset']=Q+Q//10
protected['tracks'][0]['mainGroup']['notes'][2]['duration']=Q-Q//10
_,guard=styles.apply_style(protected,'chinese_opera',strength=1.,track_indices=[0],
                           pitch_enabled=True,density_mode='original')
assert not any(g['kind']=='interval_connection' and g['note_index']==2
               for g in guard['pitch']['tracks'][0]['gestures'])

sliding=copy.deepcopy(opera)
sliding['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']=[
    0,0.,Q,0.,Q+Q//6,150.,2*Q,0.,4*Q,0.]
_,guard=styles.apply_style(sliding,'chinese_opera',strength=1.,track_indices=[0],
                           pitch_enabled=True,density_mode='original')
assert any(g['reason']=='authored_transition_preserved'
           for g in guard['pitch']['tracks'][0]['omitted_gestures'])

joy=copy.deepcopy(opera)
joy['tracks'][0]['mainGroup']['notes'][1]['pitch']=60
_,joy_receipt=styles.apply_style(joy,'bold',strength=1.,track_indices=[0],
                                 pitch_enabled=True,density_mode='original')
joy_row=joy_receipt['pitch']['tracks'][0]
assert any(g['kind']=='interval_connection' and g['note_index']==1 and
           0<g['cents_at_full_strength']<=10 for g in joy_row['gestures'])
assert not any(g['kind']=='delayed_vibrato' for g in joy_row['gestures'])

long_note=copy.deepcopy(opera)
long_note['tracks'][0]['mainGroup']['notes']=[{'onset':0,'duration':3*Q,'pitch':60,'lyrics':'啊'}]
long_params=long_note['tracks'][0]['mainGroup']['parameters']
long_params.pop('vibratoEnv')
long_params['pitchDelta']['points']=[0,0.,4*Q,0.]
long_before=copy.deepcopy(long_note)
long_out,long_receipt=styles.apply_style(long_note,'chinese_opera',strength=1.,track_indices=[0],
                                         pitch_enabled=True,density_mode='original')
long_row=long_receipt['pitch']['tracks'][0]
assert any(g['kind']=='delayed_vibrato' and g['cents_at_full_strength']==48.
           for g in long_row['gestures'])
assert long_row['maximum_applied_delta_cents']<300 and long_note==long_before
assert long_out['tracks'][0]['mainGroup']['notes']==long_before['tracks'][0]['mainGroup']['notes']
long_note['tracks'][0]['mainGroup']['notes'][0]['attributes']={'dF0Vbr':.4}
_,existing=styles.apply_style(long_note,'chinese_opera',strength=1.,track_indices=[0],
                               pitch_enabled=True,density_mode='original')
assert not any(g['kind']=='delayed_vibrato' for g in existing['pitch']['tracks'][0]['gestures'])
for preset in ('soft_ballad','intimate_narration','power_pop'):
    source=project();frozen=copy.deepcopy(source)
    voice,rv=styles.apply_style(source,preset,strength=.6,track_indices=[0],pitch_enabled=False)
    output,report=styles.apply_style(source,preset,strength=.6,track_indices=[0],pitch_enabled=True)
    assert source==frozen and rv['notes_and_pitch_unchanged']
    assert report['pitch']['status']=='applied' and report['pitch_changed']
    assert not report['notes_and_pitch_unchanged'] and report['notes_unchanged']
    actual=output['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    old=source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    assert actual['metadata']==old['metadata'] and set(old['points'][::2])<=set(actual['points'][::2])
    # Only pitch distinguishes the two otherwise identical trials.
    expected=copy.deepcopy(output)
    expected['tracks'][0]['mainGroup']['parameters']['pitchDelta']=old
    assert expected==voice
    row=report['pitch']['tracks'][0]
    assert 0.<row['maximum_applied_delta_cents']<=28.*.6+1e-8
    kinds={g['kind'] for g in row['gestures']}
    assert {'phrase_entry','interval_connection','phrase_release'}<=kinds
    clock=TempoMap(source['time']['tempo']);offset=Q
    grid=np.asarray(actual['points'][::2]);times=np.array([clock.to_seconds(int(x)+offset) for x in grid])
    delta=np.asarray(actual['points'][1::2])-np.interp(grid,old['points'][::2],old['points'][1::2])
    inside=np.zeros(len(grid),dtype=bool)
    for g in row['gestures']:inside|=(times>=g['start']-1e-8)&(times<=g['end']+1e-8)
    assert np.max(abs(delta[~inside]),initial=0.)<1e-10,'Pitch outside gesture support changed'
    # Genuine signed connections: ascending from below, descending from above.
    connections=[g for g in row['gestures'] if g['kind']=='interval_connection']
    assert connections[0]['cents_at_full_strength']<0<connections[1]['cents_at_full_strength']
    if preset=='intimate_narration':assert 'delayed_vibrato' not in kinds
    else:assert 'delayed_vibrato' in kinds
    again,r2=styles.apply_style(source,preset,strength=.6,track_indices=[0],pitch_enabled=True)
    assert again==output and r2==report

# Preserve expressive vibrato and nonlinear authored pitch rather than replacing
# either with the preset's default. Other compatible expression can still apply.
source=project();curve=source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
curve['points']=[.25,2.,float(Q)+.125,3.,float(2*Q)+.5,4.,float(5*Q)+.25,6.,float(9*Q)+.75,1.]
out,r=styles.apply_style(source,'soft_ballad',pitch_enabled=True,track_indices=[0])
result=out['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
assert r['pitch_changed'] and set(curve['points'][::2])<=set(result[::2])
assert all(type(x) is float for x in result[::2] if x in set(curve['points'][::2]))
assert source['tracks'][0]['mainGroup']['parameters']['pitchDelta']==curve
source=project();source['tracks'][0]['mainGroup']['notes'][2]['attributes']['dF0Vbr']=.3
_,r=styles.apply_style(source,'power_pop',pitch_enabled=True,track_indices=[0])
assert 'delayed_vibrato' not in {g['kind'] for g in r['pitch']['tracks'][0]['gestures']}
assert any(x['reason']=='existing_or_unknown_vibrato_preserved' for x in r['pitch']['tracks'][0]['skipped_notes'])
for preset in ('soft_ballad','chinese_opera'):
    source=project();curve=source['tracks'][0]['mainGroup']['parameters']['pitchDelta'];curve['mode']='cubic'
    output,r=styles.apply_style(source,preset,pitch_enabled=True,track_indices=[0])
    assert r['status']=='partial' and r['pitch']['status']=='skipped'
    assert output['tracks'][0]['mainGroup']['parameters']['pitchDelta']==curve

# Unknown vibrato depth and rapid / overlapping / breath notes are not assigned
# generic ornaments. The underlying original contours stay intact.
source=project();source['tracks'][0]['mainGroup']['notes']=[
    {'onset':0,'duration':Q//10,'pitch':60,'lyrics':'快'},
    {'onset':Q,'duration':Q,'pitch':60,'lyrics':'br'},
    {'onset':2*Q,'duration':2*Q,'pitch':60,'lyrics':'啊'},
    {'onset':3*Q,'duration':2*Q,'pitch':64,'lyrics':'哈'}]
for preset in ('power_pop','chinese_opera'):
    out,r=styles.apply_style(source,preset,pitch_enabled=True,track_indices=[0])
    assert not r['pitch_changed'] and r['pitch']['status']=='skipped'
    assert out['tracks'][0]['mainGroup']['parameters']['pitchDelta']==source['tracks'][0]['mainGroup']['parameters']['pitchDelta']

source=project();source['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']=[0,1199.,10*Q,1199.]
out,r=styles.apply_style(source,'power_pop',pitch_enabled=True,track_indices=[0],strength=1.)
row=r['pitch']['tracks'][0]
assert 0<row['headroom_scale']<=1
assert any(0<g['headroom_scale']<1 for g in row['gestures'])
assert max(out['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points'][1::2])<=1200.+1e-8
source=project();source['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']=[0,1300.,Q//2,0.,10*Q,0.]
out,r=styles.apply_style(source,'power_pop',pitch_enabled=True,track_indices=[0])
row=r['pitch']['tracks'][0]
assert row['status']=='partial' and row['omitted_gestures'] and row['gestures']
assert r['pitch_changed'] and r['status']=='partial'
assert out['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points'][1]==1300.
for bad in (1,None,'true',[],{}):
    try:enabled({'style_pitch':bad});raise AssertionError('Invalid boolean accepted')
    except ValueError:pass

# The canonical cards have different signed entries and transition scales,
# without adding a second vibrato over authored/editor-generated modulation.
for preset,sign in (('gentle',-1),('sad',-1),('bold',1)):
    source=project()
    out,r=styles.apply_style(source,preset,track_indices=[0],pitch_enabled=True,density_mode='original')
    row=r['pitch']['tracks'][0]
    entries=[g for g in row['gestures'] if g['kind']=='phrase_entry']
    assert entries and all(g['cents_at_full_strength']*sign>0 for g in entries)
    assert not any(g['kind']=='delayed_vibrato' for g in row['gestures'])
    assert out['tracks'][0]['mainGroup']['parameters']['vibratoEnv']==source['tracks'][0]['mainGroup']['parameters']['vibratoEnv']
    _,off=styles.apply_style(source,preset,track_indices=[0],pitch_enabled=True,
                              detail={'connection':0.},density_mode='original')
    assert not any(g['kind']=='interval_connection' for g in off['pitch']['tracks'][0]['gestures'])
    assert any(g['kind']=='phrase_entry' for g in off['pitch']['tracks'][0]['gestures'])
    bounds=(.51,.56)
    partial,rp=styles.apply_style(source,preset,track_indices=[0],pitch_enabled=True,
                                  time_range=bounds,density_mode='original')
    old=source['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
    new=partial['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
    xs=np.asarray(new[::2]);clock=TempoMap(source['time']['tempo'])
    times=np.array([clock.to_seconds(float(x)+Q) for x in xs])
    delta=np.asarray(new[1::2])-np.interp(xs,old[::2],old[1::2])
    outside=(times<=bounds[0]+1e-10)|(times>=bounds[1]-1e-10)
    assert np.max(abs(delta[outside]),initial=0.)<1e-10
    assert np.max(abs(delta),initial=0.)>0
for preset,amount in (('none',.6),('power_pop',0.)):
    out,r=styles.apply_style(project(),preset,strength=amount,pitch_enabled=True)
    assert out==project() and r['status']=='not_selected'

with tempfile.TemporaryDirectory(dir=APP/'work') as temporary:
    a=Path(temporary)/'A.svp';b=Path(temporary)/'B.svp'
    a.write_text(json.dumps(project(),ensure_ascii=False),encoding='utf-8')
    original=a.read_bytes();r=styles.apply_style_svp(a,b,preset='power_pop',pitch_enabled=True)
    assert r['pitch_producer_sha256'] and r['pitch_changed']
    assert json.loads(b.read_text(encoding='utf-8'))!=project() and a.read_bytes()==original
    assert styles.apply_style_svp(a,b,preset='power_pop',pitch_enabled=True)==r
emotion_outputs=[]
for preset in ('sad','melancholy','bright_joy','fragile_tender'):
    source=project();frozen=copy.deepcopy(source)
    output,report=styles.apply_style(source,preset,strength=.6,track_indices=[0])
    assert report['category']=='emotion' and report['pitch']['enabled'] and report['pitch_changed']
    assert report['notes_unchanged'] and source==frozen and output['tracks'][1:]==source['tracks'][1:]
    assert output['tracks'][0]['mainRef']==source['tracks'][0]['mainRef']
    assert output['tracks'][0]['mainGroup']['notes']==source['tracks'][0]['mainGroup']['notes']
    assert report['applied_controls']>1
    voice_only,rv=styles.apply_style(source,preset,strength=.6,track_indices=[0],pitch_enabled=False)
    assert not rv['pitch_changed'] and voice_only['tracks'][0]['mainGroup']['parameters']['pitchDelta']==source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    silent,rs=styles.apply_style(source,preset,strength=0.)
    assert silent==source and rs['status']=='not_selected'
    clock=TempoMap(source['time']['tempo']);curve=output['tracks'][0]['mainGroup']['parameters']['pitchDelta'];old=source['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    grid=np.asarray(curve['points'][::2]);times=np.array([clock.to_seconds(float(x)+Q) for x in grid])
    delta=np.asarray(curve['points'][1::2])-np.interp(grid,old['points'][::2],old['points'][1::2])
    active=np.zeros(len(grid),dtype=bool)
    for gesture in report['pitch']['tracks'][0]['gestures']:
        active|=(times>=gesture['start']-1e-8)&(times<=gesture['end']+1e-8)
    assert np.max(abs(delta[~active]),initial=0.)<1e-10
    assert set(old['points'][::2])<=set(curve['points'][::2])
    emotion_outputs.append(output)
assert all(emotion_outputs[a]!=emotion_outputs[b] for a in range(len(emotion_outputs)) for b in range(a))
print('PASS: canonical sad pitch, exact legacy recipes, four-phase guards, protected authored curves and joint emotion/timbre defaults with explicit opt-out')
