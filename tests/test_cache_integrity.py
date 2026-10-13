"""Exercise cache boundaries, damaged audio, legacy results and bounded scans."""
import contextlib
import io
import json
from pathlib import Path
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
import workflow
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
    assert signature == common.midi_signature(job, dict(changed, tools=dict(changed['tools'], theme='dark', download_workers=8)), 'lead')
    for edit in ({'lyrics': '改变歌词'}, {'language': 'ja'}, {'midi_steps': 32}, {'zh_lyric_mode': 'pinyin'}, {'export_pitch': True}):
        assert signature != common.midi_signature(job, dict(changed, **edit), 'lead'), edit
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
