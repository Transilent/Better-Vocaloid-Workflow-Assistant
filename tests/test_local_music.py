"""Local import, decoding, resume and publishing without a network request."""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'vendor')]
import pipeline
from common import file_hash, load_config
from tests.support import ensure_fixture
from publication import load_bundle, prepare

def no_network(*args, **kwargs):
    raise AssertionError('Local music must not fetch Bilibili data or cover art')

pipeline.get_view = no_network
pipeline.urllib.request.urlopen = no_network
scratch = APP / 'work/local-music-tests'
scratch.mkdir(parents=True, exist_ok=True)
ffmpeg = load_config()['ffmpeg']
reports = {}
for extension, codec in [('wav', 'pcm_s16le'), ('mp3', 'libmp3lame'), ('flac', 'flac'), ('m4a', 'aac')]:
    music = scratch / ('音乐 with spaces.' + extension)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000', '-t', '1', '-ac', '1', '-c:a', codec, str(music)],
                   check=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    original_hash = file_hash(music)
    job = pipeline.create_job(local_music=music, parent=scratch / 'jobs', recognize_lyrics=False, remember=False)
    pipeline.run(job, until='audio')
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    assert request['bv'] is None and request['url'] is None and request['source_kind'] == 'local'
    assert file_hash(job / request['input_file']) == original_hash == file_hash(music)
    assert not (job / 'cover.jpg').exists() and not (job / 'video/source.mp4').exists()
    info = pipeline.check_audio(job / 'audio/source.wav')
    assert info.samplerate == 44100 and info.channels == 2 and .9 < info.duration < 1.1
    # Moving the original must not break resume or reprocessing of a snapshot.
    music.unlink()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        pipeline.run(job, until='audio')
    assert output.getvalue().count('已完成，跳过') == 3
    copied = pipeline.reuse_download(job, parent=scratch / 'jobs', voice_mode='single', recognize_lyrics=False)
    with contextlib.redirect_stdout(io.StringIO()):
        pipeline.run(copied, until='audio')
    assert file_hash(copied / 'audio/source.wav') == file_hash(job / 'audio/source.wav')
    reports[extension] = True

for invalid in ('', scratch / 'missing.wav', scratch):
    try:
        pipeline.create_job(local_music=invalid, parent=scratch / 'jobs', remember=False)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid local music accepted')
bad = scratch / 'invalid.mp3'
bad.write_bytes(b'not audio')
job = pipeline.create_job(local_music=bad, parent=scratch / 'jobs', remember=False)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        pipeline.run(job, until='audio')
except RuntimeError:
    pass
else:
    raise AssertionError('Invalid media accepted')
assert json.loads((job / 'status.json').read_text(encoding='utf-8'))['state'] == 'failed'

# A local music source can prepare a finished video with a chosen image.
fixture = ensure_fixture(APP)
data = load_bundle(fixture, 'bilibili')
local = copied
bundle = prepare(local, data['video']['path'], data['content'], cover_file=fixture / 'cover.jpg', parent=scratch / 'bundles')
assert load_bundle(bundle, 'bilibili')['source_bv'] is None and bundle.name.startswith('Local_')
assert '待填写' in (bundle / '发布前核对.txt').read_text(encoding='utf-8')
print(json.dumps({'formats': reports, 'no_network': True, 'snapshot_resume_after_original_deleted': True,
                  'reprocessing': True, 'invalid_input_failure': True, 'local_publication_with_custom_cover': True}, ensure_ascii=False))
