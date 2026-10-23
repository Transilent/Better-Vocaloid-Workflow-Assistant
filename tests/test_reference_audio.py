"""Song-held-out acoustic trials and exact, locally supported source labels."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/research')]
from common import atomic_json
from reference_audio import choose_track,accepted_pair,acoustic_design,exact_acoustic_samples,leave_song_out,FEATURES


def match(track,seconds,agreement,transpose=0,parameterized=True):
    return {'track_index':track,'track_name':str(track),'compared_seconds':seconds,
            'score_voiced_coverage':.95,'pitch_agreement':agreement,'transpose_semitones':transpose,
            'offset_seconds':.5,'parameterized':parameterized}


lead=match(4,74,.85)
assert choose_track([lead,match(3,37,.88,3)])==lead,'Short shifted harmony replaced main melody'
assert choose_track([match(0,78,.74),match(1,9,.99)])['track_index']==0
assert choose_track([lead,match(1,100,.99,parameterized=False)])==lead
assert choose_track([match(0,10,.99,parameterized=False)]) is None
assert accepted_pair(lead) and not accepted_pair(match(0,78,.744))
assert not accepted_pair(match(0,9,.99))
assert not accepted_pair(None)

Q=705600000
times=np.arange(0,11,.02);pitches=60+(np.maximum(0,np.floor((times-.5)/.5)).astype(int)%3)+2
frames=np.column_stack([times,3*np.sin(times),-12+np.cos(times),.9+.05*np.sin(times),
                        -25+4*np.cos(times),pitches,np.ones(len(times))])
original=frames.copy();cues,derivative,valid,meta=acoustic_design(frames)
assert np.array_equal(frames,original),'Feature design modified source analysis'
assert cues.shape==(len(times),5) and np.isfinite(cues).all()
invalid=frames.copy();invalid[3,0]=invalid[2,0]
try:acoustic_design(invalid);raise AssertionError('Duplicate time grid accepted')
except ValueError:pass

notes=[{'onset':i*Q,'duration':Q,'pitch':60+i%3,'lyrics':'ka'} for i in range(20)]
points=[]
for i in range(20):
    for part,value in ((.25,-.2),(.5,.1),(.75,.3)):
        points.extend([int((i+part)*Q),value])
data={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[{
    'mainGroup':{'uuid':'g','notes':notes,'parameters':{'tension':{'mode':'cubic','points':points}}},
    'mainRef':{'database':{'name':'Fixture'}}}]}
pitch=SimpleNamespace(midi_pitch=pitches,voiced_mask=np.ones(len(times),dtype=bool),time_step_seconds=.02)
selection=match(0,60,.9,2)
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='reference-audio-') as folder:
    path=Path(folder)/'teacher.svp';atomic_json(path,data)
    record={'file':str(path),'sha256':'fixture','bvid':'A','id':1}
    samples=exact_acoustic_samples(record,selection,frames,pitch)
    assert len(samples['y'])==60 and samples['x'].shape==(60,len(FEATURES))
    assert set(np.round(samples['y'],5))=={-.3,0.,.2},'Invented or interpolated source labels'
    assert abs(samples['times'][0]-.625)<1e-9,'Audio offset or tempo conversion failed'
    assert samples['globally_accepted']
    missing=copy.deepcopy(pitch);missing.voiced_mask[:]=False
    assert not len(exact_acoustic_samples(record,selection,frames,missing)['y'])
    mismatched=copy.deepcopy(pitch);mismatched.midi_pitch=mismatched.midi_pitch+3
    assert not len(exact_acoustic_samples(record,selection,frames,mismatched)['y'])
    # Rejected global pairs stay rejected even when a few knots match locally.
    unverified=dict(selection,pitch_agreement=.74)
    assert not exact_acoustic_samples(record,unverified,frames,pitch)['globally_accepted']

datasets=[]
for i,song in enumerate(('A','A','B','C')):
    x=np.ones((20,len(FEATURES)));x[:,1]=np.linspace(-1,1,20)
    datasets.append({'song':song,'id':i,'x':x,'y':x[:,1]*.2,'globally_accepted':True})
result=leave_song_out(datasets)
assert result['songs']==3 and len(result['folds'])==3 and not result['runtime_enabled']
for fold in result['folds']:
    assert fold['heldout_song'] not in fold['training_songs']
    if fold['heldout_song']=='A':assert fold['heldout_ids']==[0,1]
assert not leave_song_out(datasets[:2])['runtime_enabled']
robust=leave_song_out(datasets,objective='pseudo_huber')
assert robust['songs']==3 and not robust['runtime_enabled']
assert all(f['heldout_song'] not in f['training_songs'] for f in robust['folds'])
try:leave_song_out(datasets,objective='unknown');raise AssertionError('Unknown fit objective accepted')
except ValueError:pass
json.dumps(result,allow_nan=False)
json.dumps(robust,allow_nan=False)
print('PASS: melody selection, exact labels, local support, immutable features and whole-song validation')
