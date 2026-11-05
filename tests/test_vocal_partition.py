"""Coherent leakage recovery, genuine harmonies and pipeline transactions."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import common
# Exercise the canonical source tree without waiting for root generated copies.
common.ROOT=ROOT
from vocal_partition import VocalPartitionError,coherent_transfer,repair,_checked_nonnegative_power
from agent_errors import error_payload
from common import atomic_json,file_hash,load_config
from artifact_cache import stage_signature
import pipeline
import vocal_partition

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

diagnostics={}
pure=coherent_transfer(main,harmony,sr,diagnostics=diagnostics)
assert energy(pure)<energy(harmony)*.003
assert diagnostics['spectral_dtype']=='complex128' and diagnostics['power_dtype']=='float64'
# Subtractive floating point can make a mathematically nonnegative moving
# power a few ulps negative. Clamp only that roundoff; NaN and material
# negative values remain hard failures rather than being silently replaced.
roundoff,guard=_checked_nonnegative_power(np.array([[-1e-25,1e-12]],dtype=np.float64),'fixture')
assert guard['negative_power_values_clamped']==1 and roundoff[0,0]==0
for invalid_power,code in ((np.array([[np.nan]]),'invalid_spectral_power'),(np.array([[-1e-8]]),'negative_spectral_power')):
    try:_checked_nonnegative_power(invalid_power,'fixture')
    except VocalPartitionError as exc:
        assert exc.code==code and 'first_index' in exc.details
        payload=error_payload(exc)
        assert payload['component']=='vocal_partition' and payload['retryable'] is False
        assert payload['remediation']==['vwa_jobs_get'] and payload['details']==exc.details
    else:raise AssertionError('Invalid spectral power accepted')
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
    assert report['input_sum_tolerance']==1e-4
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
    # The two output files publish as one transaction. If the second pending
    # file cannot be committed, restore both originals and clear our staging.
    write();before_tx={n:file_hash(audio/(n+'.wav')) for n in ('lead','backing')}
    original_replace=Path.replace
    def fail_second_pending(source,target):
        if source.name=='backing.partition.pending.wav':raise OSError('fixture commit failure')
        return original_replace(source,target)
    with patch.object(Path,'replace',new=fail_second_pending):
        try:repair(audio)
        except OSError:pass
        else:raise AssertionError('Second-file commit failure ignored')
    assert before_tx=={n:file_hash(audio/(n+'.wav')) for n in before_tx}
    assert not list(audio.glob('*.pending.wav'))
    assert not list(audio.glob('*.partition.previous.wav'))

    # Partition recovery names remain stable while the shared transaction owns
    # backup, publish, verification and rollback. Each case below uses a tiny
    # local fixture and never invokes a model.
    transaction_cases = job / 'transaction-cases'

    def partition_case(name):
        folder = transaction_cases / name
        folder.mkdir(parents=True)
        tx_time = np.arange(4096, dtype='float64') / sr
        tx_lead = (.2 * np.sin(2 * np.pi * 220 * tx_time))[:, None]
        tx_backing = (.05 * np.sin(2 * np.pi * 330 * tx_time))[:, None]
        tx_vocals = tx_lead + tx_backing
        for file_name, wave in (('vocals.wav', tx_vocals),
                                ('lead.wav', tx_lead),
                                ('backing.wav', tx_backing)):
            sf.write(folder / file_name, np.repeat(wave, 2, axis=1), sr, subtype='FLOAT')
        return folder

    stale_case = partition_case('stale')
    stale_backup = stale_case / 'lead.partition.previous.wav'
    stale_backup.write_bytes(b'unique-old-lead')
    stale_hashes = {name: file_hash(stale_case / name) for name in ('lead.wav', 'backing.wav')}
    try:
        repair(stale_case)
    except VocalPartitionError as exc:
        assert exc.code == 'stale_partition_backup'
        assert exc.details['files'] == [str(stale_backup)]
        assert exc.details['repair_hint']
    else:
        raise AssertionError('stale partition backup was overwritten')
    assert stale_backup.read_bytes() == b'unique-old-lead'
    assert stale_hashes == {name: file_hash(stale_case / name) for name in stale_hashes}
    assert not list(stale_case.glob('*.pending.wav'))

    input_gate = partition_case('input-complementarity-gate')
    input_backing, _ = sf.read(input_gate / 'backing.wav', dtype='float32', always_2d=True)
    input_backing[100, 0] += .01
    sf.write(input_gate / 'backing.wav', input_backing, sr, subtype='FLOAT')
    input_gate_hashes = {name: file_hash(input_gate / name)
                         for name in ('lead.wav', 'backing.wav')}
    try:
        repair(input_gate)
    except VocalPartitionError as exc:
        assert exc.code == 'non_complementary_inputs'
        assert exc.details['threshold'] == vocal_partition.SUM_RECONSTRUCTION_TOLERANCE
        assert exc.details['max_abs_error'] > exc.details['threshold']
    else:
        raise AssertionError('non-complementary partition input was accepted')
    assert input_gate_hashes == {name: file_hash(input_gate / name) for name in input_gate_hashes}
    assert not list(input_gate.glob('*.pending.wav'))
    assert not list(input_gate.glob('*.partition.previous.wav'))

    validation_case = partition_case('validation')
    validation_old = {name: file_hash(validation_case / name)
                      for name in ('lead.wav', 'backing.wav')}
    validation_seen = {}
    validation_error = VocalPartitionError(
        'fixture post-publish validation failure',
        code='non_complementary_inputs',
        details={'fixture': 'validation'},
    )

    def corrupt_published_lead(source, target):
        result = original_replace(source, target)
        if (source.name == 'lead.partition.pending.wav'
                and target.name == 'lead.wav'):
            frames = sf.info(str(target)).frames
            sf.write(target, np.zeros((frames, 2), dtype='float32'), sr, subtype='FLOAT')
            validation_seen['published'] = file_hash(target)
        return result

    with patch.object(Path, 'replace', new=corrupt_published_lead):
        try:
            repair(validation_case)
        except VocalPartitionError as exc:
            assert exc.code == 'non_complementary_inputs'
            assert exc.details['threshold'] == vocal_partition.SUM_RECONSTRUCTION_TOLERANCE
        else:
            raise AssertionError('post-publish partition validation failure was accepted')
    assert validation_seen['published']
    assert validation_old == {name: file_hash(validation_case / name) for name in validation_old}
    assert not list(validation_case.glob('*.pending.wav'))
    assert not list(validation_case.glob('*.partition.previous.wav'))

    rollback_case = partition_case('rollback-failure')
    rollback_old = {name: file_hash(rollback_case / name)
                    for name in ('lead.wav', 'backing.wav')}

    def fail_partition_validation(_paths, _check):
        raise validation_error

    def fail_partition_restore(source, target):
        if (source.name == 'lead.partition.previous.wav'
                and target.name == 'lead.wav'):
            raise OSError('fixture partition rollback failure')
        return original_replace(source, target)

    with patch.object(vocal_partition, '_validate_committed_partition',
                      new=fail_partition_validation), \
            patch.object(Path, 'replace', new=fail_partition_restore):
        try:
            repair(rollback_case)
        except VocalPartitionError as exc:
            assert exc.code == 'partition_commit_rollback_failed'
            assert exc.details['rollback_errors']
            assert exc.details['repair_hint']
            assert exc.__cause__.code == 'output_commit_rollback_failed'
        else:
            raise AssertionError('partition validation rollback failure was hidden')
    rollback_backup = rollback_case / 'lead.partition.previous.wav'
    assert file_hash(rollback_backup) == rollback_old['lead.wav']
    assert file_hash(rollback_case / 'backing.wav') == rollback_old['backing.wav']
    bad=lead.copy();bad[500,1]=np.nan
    sf.write(audio/'lead.wav',bad,sr,subtype='FLOAT')
    try:repair(audio)
    except VocalPartitionError as exc:
        assert exc.code=='nonfinite_input_samples'
        detail=exc.details['files'][0]
        assert detail['file'].endswith('lead.wav') and detail['channels']==[1]
        assert detail['sample_range']==[500,501] and detail['nonfinite_count']==1
        assert exc.details['repair_hint']
        payload=error_payload(exc)
        assert payload['component']=='vocal_partition' and payload['retryable'] is True
        assert payload['remediation']==['vwa_jobs_get','vwa_preparation_start']
        assert payload['details']['files'][0]['sample_range']==[500,501]
    else:raise AssertionError('Nonfinite input accepted')
    assert not list(audio.glob('*.pending.wav'))
    write();sf.write(audio/'lead.wav',lead[:-1],sr,subtype='FLOAT')
    try:repair(audio)
    except ValueError:pass
    else:raise AssertionError('Unequal timelines accepted')
    write()
    # Separation is stubbed below. Give cache-signature code its own tiny model
    # fixtures instead of requiring the user's multi-gigabyte installed runtime.
    model_fixtures = job / 'test-models'
    model_fixtures.mkdir()
    tools = load_config(False)
    for model_name in ('mdx_model', 'mdx_metadata', 'chorus_model', 'chorus_metadata'):
        fixture = model_fixtures / (model_name + '.bin')
        fixture.write_bytes(('fixture-' + model_name).encode('ascii'))
        tools[model_name] = str(fixture)
    request={'voice_mode':'dual','tools':tools}
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
