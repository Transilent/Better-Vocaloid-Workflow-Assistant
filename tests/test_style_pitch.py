"""Actual additive pitch gestures, unsupported preservation and cache boundaries."""
import copy,json,sys,tempfile
from pathlib import Path
import numpy as np
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
import style_presets as styles
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
source=project();curve=source['tracks'][0]['mainGroup']['parameters']['pitchDelta'];curve['mode']='cubic'
output,r=styles.apply_style(source,'soft_ballad',pitch_enabled=True,track_indices=[0])
assert r['status']=='partial' and r['pitch']['status']=='skipped'
assert output['tracks'][0]['mainGroup']['parameters']['pitchDelta']==curve

# Unknown vibrato depth and rapid / overlapping / breath notes are not assigned
# generic ornaments. The underlying original contours stay intact.
source=project();source['tracks'][0]['mainGroup']['notes']=[
    {'onset':0,'duration':Q//10,'pitch':60,'lyrics':'快'},
    {'onset':Q,'duration':Q,'pitch':60,'lyrics':'br'},
    {'onset':2*Q,'duration':2*Q,'pitch':60,'lyrics':'啊'},
    {'onset':3*Q,'duration':2*Q,'pitch':64,'lyrics':'哈'}]
out,r=styles.apply_style(source,'power_pop',pitch_enabled=True,track_indices=[0])
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
print('PASS: optional pitch gestures, signed connections, original pitch detail, safe vibrato, nonlinear preservation and independent IO')
