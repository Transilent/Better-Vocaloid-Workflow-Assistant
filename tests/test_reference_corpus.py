"""Source filtering, exact supervision and leakage protection for reference learning."""
import copy
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/research')]
from reference_corpus import inspect,curve_info,score_fingerprint,song_clusters,song_splits,sample_groups,fit_priors
from common import atomic_json,file_hash

Q=705600000
def data():
    return {'time':{'tempo':[{'position':0,'bpm':120}]},'library':[],
        'tracks':[{'name':'Lead','mainRef':{'database':{'name':'Test Voice'}},'groups':[],
            'mainGroup':{'uuid':'main','notes':[{'onset':i*Q,'duration':Q,'pitch':60+i%3,
                'lyrics':'ka','attributes':{'evenSyllableDuration':True,'dF0Vbr':0.,'tF0Left':.08}} for i in range(20)],
                'parameters':{'pitchDelta':{'mode':'linear','points':[0,0,Q,0,2*Q,0]}}}}]}
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='reference-corpus-') as folder:
    path=Path(folder)/'test.svp';base=data();atomic_json(path,base);sha=file_hash(path)
    assert not inspect(path)['eligible'],'Zero placeholders/uniform converter presets entered supervision'
    assert file_hash(path)==sha
    changed=copy.deepcopy(base)
    changed['tracks'][0]['mainGroup']['parameters']['tension']={'mode':'cubic','points':[0,-.1,Q,.2,2*Q,-.1]}
    atomic_json(path,changed);row=inspect(path)
    assert row['eligible'] and row['varying_curves']==1
    static=copy.deepcopy(base);static['tracks'][0]['mainGroup']['parameters']['voicing']={'mode':'linear','points':[0,.8,Q,.8]}
    atomic_json(path,static);row=inspect(path)
    assert not row['eligible'] and row['reason']=='static_only'
    edits=copy.deepcopy(base);edits['tracks'][0]['mainGroup']['notes'][1]['attributes']['tNoteOffset']=.02
    edits['tracks'][0]['mainGroup']['notes'][2]['attributes']['tNoteOffset']=-.01
    atomic_json(path,edits);row=inspect(path)
    assert row['eligible'] and row['edited_attribute_groups']==1
    # Unused library snapshots do not become teacher data.
    unused=data();g=copy.deepcopy(changed['tracks'][0]['mainGroup']);g['uuid']='library'
    unused['library']=[g];atomic_json(path,unused)
    assert not inspect(path)['eligible']
    unused['tracks'][0]['groups']=[{'groupID':'library'},{'groupID':'library','blickOffset':10*Q}]
    atomic_json(path,unused);row=inspect(path)
    assert row['eligible'] and len(row['groups'])==2,'Repeated library instance counted twice'
    # A malformed curve is excluded locally, while real note edits survive.
    edits['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']=[0,0,Q,2,Q,-2]
    atomic_json(path,edits);row=inspect(path)
    assert row['eligible'] and row['warnings']
    # Exact cubic knots are valid labels. Never invent intermediate labels with
    # a substitute interpolation and then claim they are native editor values.
    teacher=data();pts=[]
    for i in range(20):
        pts.extend([i*Q,.1 if i%2 else -.2,i*Q+Q//2,.2 if i%2 else -.1])
    teacher['tracks'][0]['mainGroup']['parameters']['tension']={'mode':'cubic','points':pts}
    atomic_json(path,teacher);record=inspect(path);record.update(title='Teacher',bvid='BV-test')
    samples=list(sample_groups(record,'tension'))
    assert len(samples)==1 and samples[0][2]['label_sampling']=='exact_source_knots_inside_notes'
    assert len(samples[0][1])==40
    assert not list(sample_groups(record,'breathiness'))
    # Different tuning, UUID, time offset and transposition of one score must
    # stay in the same partition, even with different uploaded video IDs.
    notes=teacher['tracks'][0]['mainGroup']['notes'];transposed=copy.deepcopy(notes)
    for n in transposed:n['onset']+=Q;n['pitch']+=5;n['uuid']='fresh'
    assert score_fingerprint(notes)==score_fingerprint(transposed)
    a={'sha256':'a','title':'Song SVP','bvid':'A','groups':[{'fingerprint':'params-a','score_fingerprint':score_fingerprint(notes)}]}
    b={'sha256':'b','title':'Other name','bvid':'B','groups':[{'fingerprint':'params-b','score_fingerprint':score_fingerprint(transposed)}]}
    split=song_splits([a,b]);assert split['a']==split['b']
    cluster=song_clusters([a,b]);assert cluster['a']==cluster['b']
    c={'sha256':'c','title':'Different','bvid':'A','groups':[]}
    split=song_splits([a,b,c]);assert len(set(split.values()))==1
    # Training artifacts are always shadow results, never automatic runtime
    # activation based on score-only labels or one positive error metric.
    rows=[]
    for i in range(4):
        r=copy.deepcopy(record);r['sha256']='teacher-'+str(i);rows.append(r)
    splits={r['sha256']:('train' if i<3 else 'holdout') for i,r in enumerate(rows)}
    result=fit_priors(rows,splits)
    assert all(not r['runtime_enabled'] for r in result['reports'].values())
    json.dumps(result,allow_nan=False)
print('PASS: effective expression, unused groups, source preservation, exact knots, grouped holdout and shadow-model protection')

