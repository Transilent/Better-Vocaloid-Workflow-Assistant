"""Coherent leakage recovery, genuine harmonies and pipeline transactions."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vocal_partition import coherent_transfer,repair
from common import atomic_json,file_hash,load_config
from artifact_cache import stage_signature
import pipeline

sr=44100
t=np.arange(sr*12)/sr
envelope=.4+.2*np.sin(t*1.4)
# Harmonic, time-varying lead and a different genuine harmony.
phase=2*np.pi*(220*t+.12*np.sin(t*3))
main=(envelope*(np.sin(phase)+.3*np.sin(phase*2)))[:,None]
harmony=(.2*np.sin(2*np.pi*330*t))[:,None]
main=np.repeat(main,2,axis=1).astype('float32')
harmony=np.repeat(harmony,2,axis=1).astype('float32')

def energy(x):return float(np.sum(x.astype('float64')**2))

pure=coherent_transfer(main,harmony,sr)
assert energy(pure)<energy(harmony)*.003
assert not coherent_transfer(main*0,harmony,sr).any()
assert not coherent_transfer(main,harmony*0,sr).any()
# Opposite phase does not justify moving cancellation artifacts into the lead.
assert energy(coherent_transfer(main,-main*.4,sr))<energy(main)*1e-5
# A copied lead component can move without removing the separate harmony.
backing=.5*main+harmony
moved=coherent_transfer(.5*main,backing,sr)
assert energy(moved-.5*main)<energy(.5*main)*.025
assert energy(backing-moved-harmony)<energy(.5*main)*.025
for length in (1,100,2047):
    a=np.zeros((length,2),dtype='float32')
    assert coherent_transfer(a,a,sr).shape==a.shape

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='partition-') as temp:
    job=Path(temp);audio=job/'audio';audio.mkdir()
    lead=.5*main;total=lead+backing
    def write():
        for name,wave in [('source',total),('vocals',total),('lead',lead),('backing',backing),('instrumental',total*0)]:
            sf.write(audio/(name+'.wav'),wave,sr,subtype='FLOAT')
    write(); original_total=file_hash(audio/'vocals.wav')
    events=[]
    report=repair(audio,progress=lambda d,n:events.append((d,n)))
    a,_=sf.read(audio/'lead.wav',dtype='float32',always_2d=True)
    b,_=sf.read(audio/'backing.wav',dtype='float32',always_2d=True)
    assert events[-1]==(len(t),len(t)) and report['status']=='applied'
    assert report['sum_max_error']<5e-7 and np.max(abs(a+b-total))<5e-7
    assert file_hash(audio/'vocals.wav')==original_total
    assert a.shape==lead.shape and b.shape==backing.shape
    # Note deduplication needs fine f0 agreement, positive waveform agreement
    # and a recorded separation intervention. Equal MIDI keys alone are not
    # sufficient to discard a real unison harmony.
    from types import SimpleNamespace
    from vocal_partition import suppress_shared_notes
    f0=SimpleNamespace(time_step_seconds=.01,midi_pitch=np.full(1201,57.,dtype='float32'),voiced_mask=np.ones(1201,bool))
    note=SimpleNamespace(onset=1.,offset=2.,pitch=57.,lyric='字')
    sf.write(audio/'peer-lead.wav',main,sr,subtype='FLOAT')
    sf.write(audio/'peer-back.wav',main*.15,sr,subtype='FLOAT')
    context={'audio':audio/'peer-lead.wav','pitch':f0,'intervals':[{'start':0.,'end':12.}]}
    kept,audit=suppress_shared_notes([note],f0,context,audio/'peer-back.wav')
    assert not kept and len(audit)==1 and audit[0]['reason']=='residual_shared_lead'
    different=SimpleNamespace(time_step_seconds=.01,midi_pitch=f0.midi_pitch+4,voiced_mask=f0.voiced_mask)
    assert suppress_shared_notes([note],different,context,audio/'peer-back.wav')[0]==[note]
    same_key=SimpleNamespace(time_step_seconds=.01,midi_pitch=f0.midi_pitch+.35*np.sin(np.arange(1201)*.3),voiced_mask=f0.voiced_mask)
    assert suppress_shared_notes([note],same_key,context,audio/'peer-back.wav')[0]==[note]
    # A mostly copied lead body can still have a distinct harmony release.
    # The original whole-note 90% agreement rule incorrectly removed it.
    independent=f0.midi_pitch.copy();independent[192:200]+=.65
    own_tail=SimpleNamespace(time_step_seconds=.01,midi_pitch=independent,voiced_mask=f0.voiced_mask)
    assert suppress_shared_notes([note],own_tail,context,audio/'peer-back.wav')[0]==[note]
    # One isolated frame is not an independently sustained release.
    spike=f0.midi_pitch.copy();spike[197]+=.65
    single_frame=SimpleNamespace(time_step_seconds=.01,midi_pitch=spike,voiced_mask=f0.voiced_mask)
    assert not suppress_shared_notes([note],single_frame,context,audio/'peer-back.wav')[0]
    assert suppress_shared_notes([note],f0,dict(context,intervals=[]),audio/'peer-back.wav')[0]==[note]
    sf.write(audio/'peer-back.wav',harmony,sr,subtype='FLOAT')
    assert suppress_shared_notes([note],f0,context,audio/'peer-back.wav')[0]==[note]
    # Block edges agree with a full signal calculation; no hard seams.
    assert np.max(abs(a-(lead+moved)))<2e-5
    write();before={n:file_hash(audio/(n+'.wav')) for n in ('lead','backing')}
    calls=[0]
    def cancel():calls[0]+=1;return calls[0]>=3
    try:repair(audio,cancel_checker=cancel)
    except InterruptedError:pass
    else:raise AssertionError('Cancellation ignored')
    assert before=={n:file_hash(audio/(n+'.wav')) for n in before}
    assert not list(audio.glob('*.pending.wav'))
    bad=lead.copy();bad[500]=np.nan
    sf.write(audio/'lead.wav',bad,sr,subtype='FLOAT')
    try:repair(audio)
    except ValueError:pass
    else:raise AssertionError('Nonfinite input accepted')
    assert not list(audio.glob('*.pending.wav'))
    write();sf.write(audio/'lead.wav',lead[:-1],sr,subtype='FLOAT')
    try:repair(audio)
    except ValueError:pass
    else:raise AssertionError('Unequal timelines accepted')
    write();request={'voice_mode':'dual','tools':load_config(False)}
    def separate(source,directory,config,task,output_names=('vocals.wav','instrumental.wav')):
        if output_names[0]=='lead.wav':
            sf.write(audio/'lead.wav',lead,sr,subtype='FLOAT')
            sf.write(audio/'backing.wav',backing,sr,subtype='FLOAT')
        return {'model':'fixture'}
    with patch('mdx.separate',side_effect=separate):pipeline.separate(job,request)
    result=json.loads((job/'separation-report.json').read_text(encoding='utf-8'))
    assert result['partition']['status']=='applied'
    first=stage_signature(job,request,'separation')
    import artifact_cache
    original_hash=artifact_cache.file_hash
    with patch('artifact_cache.file_hash',side_effect=lambda p:'changed' if Path(p).name=='vocal_partition.py' else original_hash(p)):
        assert first!=stage_signature(job,request,'separation')

print('PASS: spectral leakage, distinct harmony, backing-only/quiet/antiphase, bounded chunks, cancellation, pipeline and receipts')
