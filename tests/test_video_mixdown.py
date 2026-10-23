"""Real FFmpeg alignment/mux, both offset signs, ambiguity and immutable inputs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np
import soundfile as sf
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import load_config, file_hash
from video_mixdown import estimate_offset, render

ffmpeg = load_config()['ffmpeg']
def ff(*arguments):
    result = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *map(str, arguments)],
                            capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result

with tempfile.TemporaryDirectory(dir=APP/'work', prefix='mixdown-') as folder:
    job = Path(folder)
    rate, length = 8000, 16
    random = np.random.default_rng(20)
    levels = np.interp(np.arange(rate*length)/rate, np.arange(0, length+.1, .1),
                       random.uniform(.01, .4, round(length/.1)+1))
    audio = (np.sin(np.arange(rate*length)*2*np.pi*321/rate)*levels).astype('float32')
    source = job/'reference.wav'; sf.write(source, audio, rate, subtype='FLOAT')
    late, early = job/'late.wav', job/'early.wav'
    sf.write(late, np.r_[np.zeros(rate), audio], rate, subtype='FLOAT')
    sf.write(early, audio[rate:], rate, subtype='FLOAT')
    for candidate, expected in ((late, -1.), (early, 1.)):
        report = estimate_offset(source, candidate, max_shift=3)
        assert report['accepted'] and abs(report['offset_seconds']-expected) <= .02, report
    silent = job/'silence.wav'; sf.write(silent, np.zeros(rate*length), rate)
    try:
        estimate_offset(source, silent)
        raise AssertionError('Silence accepted as an alignment anchor')
    except ValueError: pass
    unrelated = job/'unrelated.wav'; sf.write(unrelated, random.normal(0, .05, rate*length), rate)
    assert not estimate_offset(source, unrelated, max_shift=3)['accepted']
    video = job/'source.mp4'
    ff('-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=16', '-i', source,
       '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', video)
    original = {path: file_hash(path) for path in (source, video, late, early)}
    def video_digest(path):
        return ff('-i', path, '-map', '0:v:0', '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-').stdout
    original_video = video_digest(video)
    for name, mix, offset, normalize in (('auto', late, None, False), ('delay', early, 1., True),
                                          ('trim', late, -1., False)):
        output = job/(name+'.mp4')
        report = render(job, video, mix, output, offset=offset, reference=source, normalize=normalize, max_shift=3)
        assert video_digest(output) == original_video, 'Video packets or full video duration changed'
        assert report['sha256'] == file_hash(output) and output.with_suffix('.mixdown.json').is_file()
        decoded = job/(name+'.wav'); ff('-i', output, '-map', '0:a:0', '-ar', rate, '-ac', '1', decoded)
        info = sf.info(str(decoded))
        assert 15.95 <= info.duration <= 16.1, info
        if name == 'delay':
            data, _ = sf.read(str(decoded))
            assert np.max(np.abs(data[:round(.95*rate)]), initial=0) < .001
        else:
            alignment = estimate_offset(source, decoded, max_shift=3)
            assert alignment['accepted'] and abs(alignment['offset_seconds']) <= .03, alignment
    assert all(file_hash(path) == digest for path, digest in original.items())
    try:
        render(job, video, late, video, offset=0)
        raise AssertionError('Original video was overwritten')
    except FileExistsError: pass
    assert not list(job.glob('*.pending.*')) and not list((job/'temp').iterdir())
print('PASS: signed offset, ambiguous/silent rejection, real auto/manual FFmpeg mux, two-pass loudnorm, full video packet preservation and cleanup')
