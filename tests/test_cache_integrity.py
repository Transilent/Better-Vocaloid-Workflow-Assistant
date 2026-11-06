"""Exercise cache boundaries, damaged audio, legacy results and bounded scans."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
import common
import pipeline
import task_store
import workflow
from agent_control import ControlError, file_lock
from optional_components import use_ready
from artifact_cache import receipt, valid_receipt
from midi_bridge import silent_audio

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='cache-integrity-') as tmp:
    root = Path(tmp)
    source = root / 'input.wav'
    wave = np.full((44100 * 2, 2), .04, dtype='float32')
    sf.write(source, wave, 44100, subtype='FLOAT')
    stems = {}
    for voice in ('lead', 'backing', 'instrumental'):
        stems[voice] = str(root / (voice + '.wav'))
        sf.write(stems[voice], wave / 3, 44100, subtype='FLOAT')
    job = pipeline.create_job(local_music=source, parent=root / 'jobs', remember=False, voice_mode='import',
        imported_stems=stems, workflow_graph=workflow.builtins()['内置 · 仅分离音频']['graph'])
    pipeline.run(job)
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    state = json.loads((job / 'status.json').read_text(encoding='utf-8'))
    assert all(step.get('receipt') for step in state['steps'].values())
    files = pipeline.requirements('separation', request)
    saved = state['steps']['separation']['receipt']
    assert valid_receipt(job, request, 'separation', files, saved)
    changed = dict(request, project_formats=['ustx'], midi_backend='v2')
    signature = common.midi_signature(job, changed, 'lead')
    assert signature == common.midi_signature(job, dict(changed, project_formats=['svp']), 'lead')
    assert signature == common.midi_signature(job, dict(changed, reference_dynamics=True, dynamics_strength=.3), 'lead')
    assert signature == common.midi_signature(job, dict(changed, tools=dict(changed['tools'], theme='dark', download_workers=8)), 'lead')
    for edit in ({'lyrics': '改变歌词'}, {'language': 'ja'}, {'midi_steps': 32}, {'zh_lyric_mode': 'pinyin'}, {'export_pitch': True}):
        assert signature != common.midi_signature(job, dict(changed, **edit), 'lead'), edit
    # MIDI-only v2 still verifies f0. Its sidecar is checked and participates in
    # backing deduplication even when no editor curve is requested.
    from artifact_cache import inference_inputs
    midi_only=dict(changed,project_formats=[],export_pitch=False,
        tools=dict(changed['tools'],vocal2midi=str(APP/'dependencies/vocal2midi2')))
    inputs=inference_inputs(job,midi_only,'lead')
    assert 'rmvpe_model' in inputs['models'] and 'pitch_adapter' in inputs
    folder=job/'midi/lead';folder.mkdir(parents=True,exist_ok=True)
    (folder/'lead.mid').write_bytes(b'cached-midi')
    sidecar=folder/'lead.pitch.npz';sidecar.write_bytes(b'original-pitch')
    common.atomic_json(folder/'report.json',{'input_signature':common.midi_signature(job,midi_only,'lead'),
        'midi_sha256':common.file_hash(folder/'lead.mid'),'pitch_source':'absolute_rmvpe',
        'output_sha256':{'pitch.npz':common.file_hash(sidecar)}})
    assert common.completed_voice(job,midi_only,'lead')
    before=common.midi_signature(job,midi_only,'backing');sidecar.write_bytes(b'changed-pitch')
    assert not common.completed_voice(job,midi_only,'lead')
    assert before!=common.midi_signature(job,midi_only,'backing')
    # Header-correct changes and nonempty non-audio files must both be rejected.
    sf.write(job / 'audio/backing.wav', wave / 5, 44100, subtype='FLOAT')
    assert not valid_receipt(job, request, 'separation', files, saved)
    (job / 'audio/backing.wav').write_bytes(b'nonempty damaged WAV')
    assert not valid_receipt(job, request, 'separation', files, saved)
    with contextlib.redirect_stdout(io.StringIO()) as log:
        pipeline.run(job)
    assert '正在分离' in log.getvalue()
    assert sf.info(job / 'audio/backing.wav').frames == len(wave)
    # Adding receipt metadata is compatible with historical immutable jobs.
    state = json.loads((job / 'status.json').read_text(encoding='utf-8'))
    for step in state['steps'].values(): step.pop('receipt', None)
    common.atomic_json(job / 'status.json', state)
    with contextlib.redirect_stdout(io.StringIO()) as log:
        pipeline.run(job)
    assert log.getvalue().count('已完成，跳过') == 4
    # Reprocessing must verify the source stage before certifying a copied cache.
    with contextlib.redirect_stdout(io.StringIO()):pipeline.run(job)
    sf.write(job/'audio/source.wav',wave*2,44100,subtype='FLOAT')
    newer=pipeline.reuse_download(job,parent=root/'reprocessed',voice_mode='import',imported_stems=stems,
        workflow_graph=workflow.builtins()['内置 · 仅分离音频']['graph'],lyrics='新的歌词')
    assert set(json.loads((newer/'status.json').read_text(encoding='utf-8'))['steps'])=={'metadata','download'}
    assert (newer/'参考歌词.txt').read_text(encoding='utf-8')=='新的歌词'
    with contextlib.redirect_stdout(io.StringIO()):pipeline.run(newer)
    assert np.array_equal(sf.read(newer/'audio/source.wav',dtype='float32')[0],wave)
    early=pipeline.create_job(local_music=source,parent=root/'early',remember=False,voice_mode='import',imported_stems=stems,
        workflow_graph=workflow.builtins()['内置 · 仅分离音频']['graph'])
    with contextlib.redirect_stdout(io.StringIO()):pipeline.run(early,until='metadata')
    restart=pipeline.reuse_download(early,parent=root/'early-restart',voice_mode='import',imported_stems=stems,
        workflow_graph=workflow.builtins()['内置 · 仅分离音频']['graph'])
    assert json.loads((restart/'request.json').read_text(encoding='utf-8'))['source_audio']==common.saved_path(source)
    # The old one-byte Windows protocol still coordinates new task reads,
    # Agent locks and component users. An empty sidecar belongs to the holder.
    lock_path = early / 'run.lock'
    lock_path.write_bytes(b'')
    holder = subprocess.Popen([sys.executable, '-B', '-c',
        "import msvcrt, sys\n"
        "with open(sys.argv[1], 'r+b') as lock:\n"
        "    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.readline()\n", str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    blocked = root / 'blocked-reuse'
    try:
        assert holder.stdout.readline().strip() == 'held'
        assert task_store.locked(early)
        try:
            pipeline.reuse_download(early, parent=blocked, voice_mode='single')
            raise AssertionError('Reused a job with a concurrent writer')
        except RuntimeError as error:
            assert '已经在运行' in str(error), error
        try:
            with use_ready(early):
                raise AssertionError('Used a component with a concurrent installer')
        except RuntimeError as error:
            assert '正在安装或使用' in str(error), error
        try:
            with file_lock(lock_path, blocking=False):
                raise AssertionError('Agent ignored legacy run.lock')
        except ControlError as error:
            assert error.code == 'busy'
        assert not blocked.exists() and lock_path.stat().st_size == 0
    finally:
        holder.communicate(input='release\n', timeout=15)
    assert holder.returncode == 0 and not task_store.locked(early)
    assert not task_store.locked(blocked) and not blocked.exists()
    with pipeline.job_lock(early):
        assert lock_path.stat().st_size == 1

    # Reprocessing retains caller provenance without upgrading the claim;
    # changing/removing the declaration regenerates metadata for the same WAV.
    claim = {'kind': 'caller_supplied_unverified', 'url': 'https://www.bilibili.com/video/BV1xx411c7mD/'}
    identity = common.file_hash(source)
    declared = pipeline.create_job(local_music=source, parent=root/'declared', remember=False,
        voice_mode='single', source_provenance=claim, local_audio_sha256=identity)
    with contextlib.redirect_stdout(io.StringIO()):
        pipeline.run(declared, until='audio')
    original_info = (declared/'source-info.json').read_bytes()
    override = dict(claim, url='https://www.bilibili.com/video/BV1xx411c7mE/')
    for wanted, options in ((claim, {}), (override, {'source_provenance': override}),
                            (None, {'source_provenance': None})):
        result = pipeline.reuse_download(declared, parent=root/'declared-reuse', voice_mode='single', **options)
        request2 = json.loads((result/'request.json').read_text(encoding='utf-8'))
        info2 = json.loads((result/'source-info.json').read_text(encoding='utf-8'))
        steps2 = json.loads((result/'status.json').read_text(encoding='utf-8'))['steps']
        assert steps2['download']['reused_from'] == steps2['audio']['reused_from'] == str(declared)
        assert ('reused_from' in steps2['metadata']) == (wanted == claim)
        for evidence in (request2, info2):
            assert evidence.get('source_provenance') == wanted
            assert evidence.get('local_audio_sha256') == (identity if wanted else None)
        assert valid_receipt(result, request2, 'metadata', pipeline.requirements('metadata', request2),
                             steps2['metadata']['receipt'])
    assert (declared/'source-info.json').read_bytes() == original_info
    try:
        pipeline.reuse_download(declared, parent=blocked, voice_mode='single', local_audio_sha256='0'*64)
        raise AssertionError('Accepted a conflicting source hash')
    except ValueError as error:
        assert '摘要' in str(error)
    assert not blocked.exists()
    # Silence checking must inspect late blocks and avoid a whole-file array.
    path = root / 'silence.wav'
    block = np.zeros((65536, 2), dtype='float32')
    with sf.SoundFile(path, 'w', samplerate=44100, channels=2, subtype='FLOAT') as stream:
        for _ in range(40): stream.write(block)
    tracemalloc.start()
    assert silent_audio(path)
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert peak < 4 * 1024**2, peak
    with sf.SoundFile(path, 'r+') as stream:
        stream.seek(stream.frames - 1); stream.write(np.array([[2e-6, 0]], dtype='float32'))
    assert not silent_audio(path)
    with sf.SoundFile(path, 'r+') as stream:
        stream.seek(stream.frames - 1); stream.write(np.array([[float('nan'), 0]], dtype='float32'))
    try:
        silent_audio(path)
        raise AssertionError('Nonfinite audio accepted')
    except ValueError: pass

print('PASS: narrow inference inputs, stage receipts, corrupt-WAV recovery, legacy migration and bounded silence scans')
