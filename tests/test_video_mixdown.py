"""Real FFmpeg alignment/mux, signed offsets, diagnostics and bound analysis reuse."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import file_hash, load_config
import video_mixdown
from video_mixdown import analyze_alignment, estimate_offset, render


ffmpeg = load_config()['ffmpeg']


def ff(*arguments):
    result = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *map(str, arguments)],
                            capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result


def varied(rate, seconds, seed):
    time = np.arange(round(rate * seconds)) / rate
    random = np.random.default_rng(seed)
    knots = np.arange(0., seconds + .1001, .1)
    levels = np.interp(time, knots, random.uniform(.02, .35, len(knots)))
    return (np.sin(2 * np.pi * 317 * time) * levels).astype('float32')


def expect(error, function, text):
    try:
        function()
    except error:
        return
    raise AssertionError(text)


def video_digest(path):
    return ff('-i', path, '-map', '0:v:0', '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-').stdout


def decoded(path, rate):
    wav = path.with_suffix(path.suffix + '.decoded.wav')
    ff('-i', path, '-map', '0:a:0', '-ar', rate, '-ac', '1', wav)
    data, actual_rate = sf.read(str(wav), dtype='float32')
    assert actual_rate == rate
    return data


with tempfile.TemporaryDirectory(dir=APP/'work', prefix='mixdown-') as folder:
    root = Path(folder)
    rate, length = 8000, 12
    source = root/'reference.wav'
    source_audio = varied(rate, length, 20)
    sf.write(source, source_audio, rate, subtype='FLOAT')

    late = root/'late-16k.wav'
    late16 = varied(16000, length, 20)
    sf.write(late, np.r_[np.zeros(16000, dtype='float32'), late16 / 2], 16000, subtype='FLOAT')
    early = root/'early.wav'
    sf.write(early, source_audio[rate:], rate, subtype='FLOAT')

    # Head/tail padding and unlike sample rates must stay on the same 10 ms timeline.
    blank_reference, blank_mix = root/'blank-reference.wav', root/'blank-mix.wav'
    interior = varied(8000, 8, 31)
    sf.write(blank_reference, np.r_[np.zeros(4000, dtype='float32'), interior,
                                    np.zeros(8000, dtype='float32')], 8000, subtype='FLOAT')
    sf.write(blank_mix, np.r_[np.zeros(16000, dtype='float32'), varied(16000, 8, 31),
                              np.zeros(12000, dtype='float32')], 16000, subtype='FLOAT')

    for candidate, expected in ((late, -1.), (early, 1.), (blank_mix, -.5)):
        reference = blank_reference if candidate == blank_mix else source
        alignment = estimate_offset(reference, candidate, max_shift=3)
        assert alignment['accepted'] and abs(alignment['offset_seconds'] - expected) <= .02, alignment
        assert alignment['reason_code'] == 'matched' and alignment['diagnostics']['overlap_ratio'] >= .5

    silent = root/'silence.wav'
    sf.write(silent, np.zeros(rate * length, dtype='float32'), rate, subtype='FLOAT')
    expect(ValueError, lambda: estimate_offset(source, silent), 'Silence was accepted as an alignment anchor')
    short = root/'short.wav'
    sf.write(short, varied(rate, 2, 8), rate, subtype='FLOAT')
    expect(ValueError, lambda: estimate_offset(source, short), 'Short audio was accepted as an alignment anchor')

    unrelated = root/'unrelated.wav'
    sf.write(unrelated, varied(rate, length, 90), rate, subtype='FLOAT')
    unrelated_result = estimate_offset(source, unrelated, max_shift=3)
    assert not unrelated_result['accepted'] and unrelated_result['reason_code'] in ('weak_correlation', 'ambiguous_peak')

    periodic = root/'periodic.wav'
    pattern = varied(rate, 1, 91)
    sf.write(periodic, np.tile(pattern, length).astype('float32'), rate, subtype='FLOAT')
    periodic_result = estimate_offset(periodic, periodic, max_shift=2.5)
    assert not periodic_result['accepted'] and periodic_result['reason_code'] == 'ambiguous_peak', periodic_result

    boundary = estimate_offset(source, early, max_shift=.01)
    assert not boundary['accepted'] and boundary['reason_code'] == 'search_boundary', boundary

    video = root/'source.mp4'
    ff('-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=12', '-i', source,
       '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', video)
    original_video = video_digest(video)
    original = {path: file_hash(path) for path in (source, video, late, early, silent)}

    analysis_job = root/'analysis-job'
    analysis_job.mkdir()
    hash_calls = []
    real_file_hash = video_mixdown.file_hash

    def counted_hash(path):
        hash_calls.append(str(Path(path).resolve()))
        return real_file_hash(path)

    with patch.object(video_mixdown, 'file_hash', side_effect=counted_hash):
        alignment = analyze_alignment(analysis_job, video, late, max_shift=3)
    required = {'offset_seconds', 'correlation', 'margin', 'accepted', 'overlap_seconds',
                'method', 'resolution_seconds'}
    assert required <= set(alignment) and alignment['accepted'], alignment
    assert alignment['reason'] == alignment['message']
    assert alignment['thresholds'] == {
        'minimum_seconds': 5., 'minimum_correlation': .55, 'minimum_margin': .08,
        'minimum_overlap_ratio': .5, 'peak_separation_seconds': .5,
    }
    assert abs(alignment['offset_seconds'] + 1.) <= .02, alignment
    assert alignment['method'] == 'normalized_stereo_energy_correlation'
    assert alignment['inputs'][str(video.resolve())] == file_hash(video)
    assert alignment['input_fingerprints']['video']['sha256'] == alignment['input_fingerprints']['reference']['sha256']
    assert hash_calls.count(str(video.resolve())) == 2, hash_calls  # one preflight and one final verification
    saved = json.loads((analysis_job/'video-alignment.json').read_text(encoding='utf-8'))
    assert saved == alignment and not (analysis_job/'analysis.mp4').exists()

    short_job = root/'short-job'; short_job.mkdir()
    short_report = analyze_alignment(short_job, video, short, reference=source, max_shift=3)
    assert not short_report['accepted'] and short_report['reason_code'] == 'too_short'
    assert (short_job/'video-alignment.json').is_file()

    silent_job = root/'silent-job'; silent_job.mkdir()
    silent_report = analyze_alignment(silent_job, video, silent, max_shift=3)
    assert not silent_report['accepted'] and silent_report['reason_code'] == 'mixdown_constant'
    assert json.loads((silent_job/'video-alignment.json').read_text(encoding='utf-8')) == silent_report

    cancel_job = root/'cancel-job'; cancel_job.mkdir()
    (cancel_job/'cancel.flag').write_text('stop', encoding='utf-8')
    expect(InterruptedError, lambda: analyze_alignment(cancel_job, video, late, max_shift=3, reset_cancel=False),
           'Pending cancellation was cleared by a non-resetting analysis')
    assert not (cancel_job/'video-alignment.json').exists()

    from pipeline import job_lock
    lock_job = root/'lock-job'; lock_job.mkdir()
    with job_lock(lock_job):
        expect(RuntimeError, lambda: analyze_alignment(lock_job, video, late, max_shift=3),
               'A second analysis bypassed job_lock')

    bad = copy.deepcopy(alignment); bad['correlation'] = .1
    assert video_mixdown._report_state(bad, alignment['input_fingerprints'], 3) == 'invalid'
    bad = copy.deepcopy(alignment); bad['offset_seconds'] = 3.1
    assert video_mixdown._report_state(bad, alignment['input_fingerprints'], 3) == 'invalid'
    bad = copy.deepcopy(alignment); bad['overlap_seconds'] = 4.99
    assert video_mixdown._report_state(bad, alignment['input_fingerprints'], 3) == 'invalid'
    bad = copy.deepcopy(alignment); bad['reason_code'] = 'ambiguous_peak'
    assert video_mixdown._report_state(bad, alignment['input_fingerprints'], 3) == 'invalid'
    bad = copy.deepcopy(alignment); bad['input_fingerprints']['mixdown']['sha256'] = '0' * 64
    assert video_mixdown._report_state(bad, alignment['input_fingerprints'], 3) == 'stale'

    auto_output = root/'auto.mp4'
    with patch.object(video_mixdown, '_analyze_alignment', side_effect=AssertionError('valid report was reanalyzed')):
        auto = render(analysis_job, video, late, auto_output, alignment_report=alignment, reset_cancel=False)
    assert auto['alignment'] == alignment and video_digest(auto_output) == original_video
    auto_audio = decoded(auto_output, rate)
    auto_check = estimate_offset(source, auto_output.with_suffix('.mp4.decoded.wav'), max_shift=3)
    assert auto_check['accepted'] and abs(auto_check['offset_seconds']) <= .03, auto_check
    assert auto['sha256'] == file_hash(auto_output) and auto_output.with_suffix('.mixdown.json').is_file()

    direct_output = root/'direct-auto.mp4'
    direct = render(analysis_job, video, late, direct_output, reference=source, max_shift=3)
    assert direct['alignment']['accepted'] and direct['alignment']['offset_seconds'] == -1., direct['alignment']
    assert video_digest(direct_output) == original_video

    manual_output = root/'manual.mp4'
    manual = render(analysis_job, video, early, manual_output, offset=1., reference=source,
                    normalize=True, max_shift=3)
    assert manual['alignment']['method'] == 'manual' and video_digest(manual_output) == original_video
    manual_audio = decoded(manual_output, rate)
    assert np.max(np.abs(manual_audio[:round(.95*rate)]), initial=0) < .001
    assert manual['loudness_measurement'] is not None

    stale_output = root/'stale.mp4'
    stale = render(analysis_job, video, early, stale_output, reference=source,
                   alignment_report=alignment, max_shift=3)
    assert stale['alignment']['method'] == 'normalized_stereo_energy_correlation'
    assert stale['alignment']['offset_seconds'] > .9, stale['alignment']
    stale_audio = decoded(stale_output, rate)
    assert np.max(np.abs(stale_audio[:round(.95*rate)]), initial=0) < .001
    assert stale['alignment']['input_fingerprints']['mixdown']['sha256'] == file_hash(early)

    rejected_output = root/'rejected.mp4'
    expect(ValueError, lambda: render(silent_job, video, silent, rejected_output, alignment_report=silent_report),
           'Low-confidence analysis was used to render')
    assert not rejected_output.exists()
    expect(FileNotFoundError, lambda: analyze_alignment(short_job, video, root/'missing.wav'),
           'Missing input was accepted')

    expect(FileExistsError, lambda: render(analysis_job, video, late, video, offset=0),
           'Original video was overwritten')
    assert all(file_hash(path) == digest for path, digest in original.items())
    assert not list(root.glob('*.pending.*')) and not list((analysis_job/'temp').iterdir())

print('PASS: signed alignment, rate/padding/timing diagnostics, rejection, cancellation/locking, bound stale-analysis reanalysis and atomic mux')
