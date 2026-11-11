"""Optional standalone composition and independent prediction routing."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from common import ROOT,atomic_json,load_config
from roformer import combine_independent
import pipeline

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='independent-') as folder:
    job=Path(folder);(job/'audio').mkdir()
    t=np.arange(4410)/44100
    lead=np.tile((.2*np.sin(2*np.pi*220*t))[:,None],(1,2)).astype('float32')
    backing=np.tile((.1*np.sin(2*np.pi*330*t))[:,None],(1,2)).astype('float32')
    mix=lead+backing
    for name,wave in [('source',mix),('vocals',mix),('instrumental',mix*0),('prediction',lead)]:
        sf.write(job/'audio'/(name+'.wav'),wave,44100,subtype='FLOAT')
    report=combine_independent(job/'audio/vocals.wav',job/'audio/prediction.wav',job/'audio',job)
    actual,_=sf.read(job/'audio/backing.wav',dtype='float32',always_2d=True)
    assert np.max(abs(actual-backing))<1e-7 and report['sum_max_error']<1e-7
    for flow,kind in [('cascade','vocals'),('independent','mix')]:
        calls=[]
        def predictor(source,directory,task,device,*,input_kind):
            calls.append((source,input_kind));Path(directory).mkdir(parents=True,exist_ok=True)
            for name,wave in [('lead',lead),('backing',backing)]: sf.write(Path(directory)/(name+'.wav'),wave,44100,subtype='FLOAT')
            return {'input_kind':input_kind}
        with patch('mdx.separate',return_value={'model':'fixture'}),patch('roformer.separate_vocals',side_effect=predictor):
            pipeline.separate(job,{'voice_mode':'roformer','separation_flow':flow,'tools':load_config(False)})
        saved=json.loads((job/'separation-report.json').read_text(encoding='utf-8'))
        assert calls==[(job/('audio/source.wav' if kind=='mix' else 'audio/vocals.wav'),kind)]
        assert saved['maximum_model_depth']==(1 if kind=='mix' else 2) and saved['model_invocations']==2
        if flow=='independent': assert not list((job/'temp/direct-lead').glob('*.wav'))
    (job/'cancel.flag').touch()
    try: combine_independent(job/'audio/vocals.wav',job/'audio/prediction.wav',job/'cancelled',job)
    except InterruptedError: pass
    else: raise AssertionError('Cancellation ignored')
    assert not list((job/'cancelled').glob('*.pending.wav'))
    (job/'cancel.flag').unlink()
    sf.write(job/'empty.wav',np.zeros((0,2)),44100,subtype='FLOAT')
    try: combine_independent(job/'empty.wav',job/'empty.wav',job/'empty-out',job)
    except ValueError: pass
    else: raise AssertionError('Empty predictions accepted')

print('PASS: standalone composition, publication handoff and residual conservation')
