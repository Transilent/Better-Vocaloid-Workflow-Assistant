"""Align a finished mix to the source video, with explicit ambiguity handling."""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile

from common import atomic_json, cancelled, file_hash, load_config


ALIGNMENT_METHOD = 'normalized_stereo_energy_correlation'
ALIGNMENT_RESOLUTION = .01
MIN_ALIGNMENT_SECONDS = 5.
MIN_CORRELATION = .55
MIN_MARGIN = .08
MIN_OVERLAP_RATIO = .5
PEAK_SEPARATION_SECONDS = .5
DECODE_SAMPLE_RATE = 4000


def envelope(path, cancel=lambda: None):
    """Return one mono RMS energy value per 10 ms, independent of file rate."""
    import numpy as np
    import soundfile as sf
    rows = []
    with sf.SoundFile(str(path)) as audio:
        hop = max(1, round(audio.samplerate * ALIGNMENT_RESOLUTION))
        for block in audio.blocks(blocksize=hop * 1000, dtype='float32', always_2d=True):
            cancel()
            if not np.isfinite(block).all():
                raise ValueError('对齐音频包含无效采样。')
            count = len(block) // hop
            if count:
                rows.append(np.sqrt(np.mean(block[:count*hop].reshape(count, hop, -1) ** 2, axis=(1, 2))))
    if not rows:
        raise ValueError('音频过短，无法自动对齐。')
    return np.concatenate(rows).astype('float64')


def _alignment_message(code):
    return {
        'matched': '对齐结果通过质量与唯一性检查。',
        'too_short': '参考音频或 Mixdown 少于 5 秒，无法可靠自动对齐。',
        'reference_constant': '参考音频没有明显能量变化，无法可靠自动对齐。',
        'mixdown_constant': 'Mixdown 没有明显能量变化，无法可靠自动对齐。',
        'insufficient_overlap': '搜索范围内没有达到最低重叠比例的候选。',
        'search_boundary': '最佳候选位于搜索边界，无法确认真实偏移。',
        'weak_correlation': '最佳候选相关度不足，无法可靠确认偏移。',
        'ambiguous_peak': '存在相近的多个峰值，无法唯一确认偏移。',
    }.get(code, '无法可靠确认自动对齐偏移。')


def _max_shift_value(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('自动对齐搜索范围应为 0.01–120 秒。') from None
    if not math.isfinite(value) or not .01 <= value <= 120:
        raise ValueError('自动对齐搜索范围应为 0.01–120 秒。')
    return value


def _estimate_offset(reference, mixdown, max_shift, cancel, *, strict):
    import numpy as np
    limit = _max_shift_value(max_shift)
    a, b = envelope(reference, cancel), envelope(mixdown, cancel)
    min_frames = round(MIN_ALIGNMENT_SECONDS / ALIGNMENT_RESOLUTION)
    overlap_frames = min(len(a), len(b))
    result = {
        'offset_seconds': 0.0,
        'correlation': 0.0,
        'margin': 0.0,
        'accepted': False,
        'overlap_seconds': round(overlap_frames * ALIGNMENT_RESOLUTION, 2),
        'method': ALIGNMENT_METHOD,
        'resolution_seconds': ALIGNMENT_RESOLUTION,
        'reason_code': 'too_short',
        'message': _alignment_message('too_short'),
        'reason': _alignment_message('too_short'),
        'thresholds': {
            'minimum_seconds': MIN_ALIGNMENT_SECONDS,
            'minimum_correlation': MIN_CORRELATION,
            'minimum_margin': MIN_MARGIN,
            'minimum_overlap_ratio': MIN_OVERLAP_RATIO,
            'peak_separation_seconds': PEAK_SEPARATION_SECONDS,
        },
        'diagnostics': {
            'reference_seconds': round(len(a) * ALIGNMENT_RESOLUTION, 3),
            'mixdown_seconds': round(len(b) * ALIGNMENT_RESOLUTION, 3),
            'reference_energy_std': round(float(a.std()), 8),
            'mixdown_energy_std': round(float(b.std()), 8),
            'search_seconds': limit,
            'valid_candidates': 0,
            'overlap_ratio': 0.0,
        },
    }
    if overlap_frames < min_frames:
        if strict:
            raise ValueError('自动对齐需要至少 5 秒且有明显变化的音频，请手动填写偏移。')
        return result
    a_std, b_std = float(a.std()), float(b.std())
    if min(a_std, b_std) < 1e-6:
        code = 'reference_constant' if a_std < 1e-6 else 'mixdown_constant'
        result.update(reason_code=code, message=_alignment_message(code), reason=_alignment_message(code))
        if strict:
            raise ValueError('自动对齐需要至少 5 秒且有明显变化的音频，请手动填写偏移。')
        return result

    nfft = 1 << (len(a) + len(b) - 2).bit_length()
    cross = np.fft.irfft(np.fft.rfft(a, nfft) * np.fft.rfft(b[::-1], nfft), nfft)
    sums = [np.r_[0., np.cumsum(x)] for x in (a, b, a*a, b*b)]
    lag_limit = round(limit / ALIGNMENT_RESOLUTION)
    lags = np.arange(-lag_limit, lag_limit + 1)
    left_a, left_b = np.maximum(lags, 0), np.maximum(-lags, 0)
    count = np.minimum(len(a)-left_a, len(b)-left_b)
    valid = count >= max(min_frames, min(len(a), len(b)) * MIN_OVERLAP_RATIO)
    valid_indexes = np.flatnonzero(valid)
    if not len(valid_indexes):
        result.update(reason_code='insufficient_overlap', message=_alignment_message('insufficient_overlap'),
                      reason=_alignment_message('insufficient_overlap'))
        return result
    scores = np.full(len(lags), np.nan)
    for index in valid_indexes:
        if index % 100 == 0:
            cancel()
        x, y, size = left_a[index], left_b[index], count[index]
        sa, sb, qa, qb = [v[start+size] - v[start] for v, start in zip(sums, (x, y, x, y))]
        denominator = math.sqrt(max(0., qa-sa*sa/size) * max(0., qb-sb*sb/size))
        if denominator > 1e-10:
            scores[index] = (cross[lags[index]+len(b)-1]-sa*sb/size) / denominator
    candidate_indexes = valid_indexes[np.isfinite(scores[valid_indexes])]
    if not len(candidate_indexes):
        result.update(reason_code='insufficient_overlap', message=_alignment_message('insufficient_overlap'),
                      reason=_alignment_message('insufficient_overlap'))
        return result
    best = int(candidate_indexes[np.argmax(scores[candidate_indexes])])
    separation = round(PEAK_SEPARATION_SECONDS / ALIGNMENT_RESOLUTION)
    alternatives = scores[abs(lags - lags[best]) >= separation]
    alternatives = alternatives[np.isfinite(alternatives)]
    second = float(np.max(alternatives, initial=-1))
    score, margin = float(scores[best]), float(scores[best] - second)
    ratio = float(count[best] / min(len(a), len(b)))
    boundary = best in (0, len(lags) - 1)
    result.update(
        offset_seconds=round(float(lags[best]) / round(1 / ALIGNMENT_RESOLUTION), 3),
        correlation=round(score, 5),
        margin=round(margin, 5),
        overlap_seconds=round(float(count[best]) * ALIGNMENT_RESOLUTION, 2),
    )
    result['diagnostics'].update(valid_candidates=int(len(candidate_indexes)), overlap_ratio=round(ratio, 5))
    if boundary:
        code = 'search_boundary'
    elif score < MIN_CORRELATION:
        code = 'weak_correlation'
    elif margin < MIN_MARGIN:
        code = 'ambiguous_peak'
    else:
        code = 'matched'
    result.update(accepted=code == 'matched', reason_code=code, message=_alignment_message(code),
                  reason=_alignment_message(code))
    return result


def estimate_offset(reference, mixdown, max_shift=30., cancel=lambda: None):
    """Positive offset delays the mix; negative offset trims its leading audio.

    Short, silent, or constant inputs raise ValueError for compatibility.
    Weak or non-unique candidates return accepted=False with diagnostics.
    """
    return _estimate_offset(reference, mixdown, max_shift, cancel, strict=True)


def _input_paths(job, video, mixdown, reference):
    job = Path(job).resolve()
    video, mixdown = Path(video).resolve(), Path(mixdown).resolve()
    reference = video if reference is None else Path(reference).resolve()
    if any(not path.is_file() for path in (video, mixdown, reference)):
        raise FileNotFoundError('请选择存在的原视频、Mixdown 与参考音频。')
    return job, video, mixdown, reference


def _snapshot_inputs(paths):
    """Hash each distinct absolute path once and bind all requested roles."""
    digests = {}
    roles = {}
    for role, path in paths:
        key = str(Path(path).resolve())
        if key not in digests:
            digests[key] = file_hash(key)
        roles[role] = {'path': key, 'sha256': digests[key]}
    return roles, digests


def _same_inputs(expected, current):
    if not isinstance(expected, dict):
        return False
    for role, record in current.items():
        saved = expected.get(role)
        if not isinstance(saved, dict):
            return False
        try:
            same_path = Path(saved.get('path', '')).resolve() == Path(record['path']).resolve()
        except (OSError, TypeError, ValueError):
            return False
        if not same_path or saved.get('sha256') != record['sha256']:
            return False
    return True


def _alignment_error(report):
    detail = report.get('message') or _alignment_message(report.get('reason_code'))
    return '音频自动对齐结果不够明确（' + detail + '），请试听后手动填写偏移。候选结果见 video-alignment.json。'


def _analyze_alignment(job, video, mixdown, *, reference=None, max_shift=30,
                       expected_inputs=None, persist=True):
    from pipeline import run_process
    job, video, mixdown, reference = _input_paths(job, video, mixdown, reference)
    limit = _max_shift_value(max_shift)
    roles = (('video', video), ('mixdown', mixdown), ('reference', reference))
    fingerprints, path_hashes = expected_inputs or _snapshot_inputs(roles)
    (job / 'temp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='video-alignment-', dir=job / 'temp') as folder:
        folder = Path(folder)
        decoded = []
        for index, (role, path) in enumerate((('reference', reference), ('mixdown', mixdown))):
            wav = folder / (role + '.wav')
            try:
                run_process([load_config()['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                             '-i', str(path), '-map', '0:a:0', '-vn', '-ar', str(DECODE_SAMPLE_RATE),
                             '-ac', '2', '-c:a', 'pcm_f32le', str(wav)],
                            job, 'alignment_decode_' + role)
            except RuntimeError as error:
                raise ValueError('无法从{}解码首条音轨：{}'.format(
                    '参考音频' if role == 'reference' else 'Mixdown', error)) from error
            decoded.append(wav)
        cancelled(job)
        measured = _estimate_offset(*decoded, limit, lambda: cancelled(job), strict=False)
        measured['decoded_audio'] = {
            'sample_rate': DECODE_SAMPLE_RATE,
            'channels': 2,
            'sample_format': 'pcm_f32le',
        }
    current, _ = _snapshot_inputs(roles)
    if not _same_inputs(fingerprints, current):
        raise ValueError('对齐分析期间输入文件发生变化，请重新选择文件后分析。')
    cancelled(job)
    measured['inputs'] = path_hashes
    measured['input_fingerprints'] = fingerprints
    if persist:
        atomic_json(job / 'video-alignment.json', measured)
    return measured


def analyze_alignment(job, video, mixdown, *, reference=None, max_shift=30, reset_cancel=True):
    """Analyze audio alignment without creating a video and bind the result to its inputs."""
    from pipeline import job_lock
    job = Path(job).resolve()
    with job_lock(job):
        if reset_cancel:
            (job / 'cancel.flag').unlink(missing_ok=True)
        cancelled(job)
        return _analyze_alignment(job, video, mixdown, reference=reference, max_shift=max_shift)


def _report_state(report, fingerprints, max_shift):
    if not isinstance(report, dict):
        return 'invalid'
    if not _same_inputs(report.get('input_fingerprints'), fingerprints):
        return 'stale'
    if report.get('accepted') is False:
        return 'low'
    if report.get('accepted') is not True or report.get('method') != ALIGNMENT_METHOD:
        return 'invalid'
    try:
        offset = float(report['offset_seconds'])
        correlation = float(report['correlation'])
        margin = float(report['margin'])
        overlap = float(report['overlap_seconds'])
        resolution = float(report['resolution_seconds'])
    except (KeyError, TypeError, ValueError):
        return 'invalid'
    if (not all(math.isfinite(value) for value in (offset, correlation, margin, overlap, resolution))
            or report.get('reason_code') != 'matched'
            or resolution != ALIGNMENT_RESOLUTION or overlap < MIN_ALIGNMENT_SECONDS
            or correlation < MIN_CORRELATION or correlation > 1.00001
            or margin < MIN_MARGIN or margin > 2.00001
            or abs(offset) > float(max_shift) + ALIGNMENT_RESOLUTION / 2):
        return 'invalid'
    return 'usable'


def _render(job, video, mixdown, output, *, offset=None, reference=None, normalize=False,
            max_shift=30, alignment_report=None):
    from pipeline import run_process
    job, video, mixdown, output = [Path(p).resolve() for p in (job, video, mixdown, output)]
    reference = Path(reference).resolve() if reference else video
    if any(not path.is_file() for path in (video, mixdown, reference)):
        raise FileNotFoundError('请选择存在的原视频、Mixdown 与参考音频。')
    if output.exists() or output in (video, mixdown, reference):
        raise FileExistsError('成品文件已存在，请选择新的文件名。')
    if output.suffix.lower() != '.mp4':
        raise ValueError('合成视频请使用 .mp4 文件名。')
    if offset is not None:
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            raise ValueError('手动偏移应在正负 3600 秒内。') from None
        if not math.isfinite(offset) or abs(offset) > 3600:
            raise ValueError('手动偏移应在正负 3600 秒内。')
    limit = None if offset is not None else _max_shift_value(max_shift)
    roles = (('video', video), ('mixdown', mixdown), ('reference', reference))
    fingerprints, path_hashes = _snapshot_inputs(roles)
    measured = {'offset_seconds': offset, 'method': 'manual', 'accepted': True}
    auto = offset is None
    if auto:
        state = _report_state(alignment_report, fingerprints, limit) if alignment_report is not None else 'absent'
        if state == 'usable':
            measured = alignment_report
        elif state == 'low':
            current, _ = _snapshot_inputs(roles)
            if not _same_inputs(fingerprints, current):
                raise ValueError('对齐分析期间输入文件发生变化，请重新选择文件后分析。')
            cancelled(job)
            atomic_json(job / 'video-alignment.json', alignment_report)
            raise ValueError(_alignment_error(alignment_report))
        else:
            measured = _analyze_alignment(job, video, mixdown, reference=reference, max_shift=limit,
                                          expected_inputs=(fingerprints, path_hashes), persist=False)
        if not measured.get('accepted'):
            current, _ = _snapshot_inputs(roles)
            if not _same_inputs(fingerprints, current):
                raise ValueError('对齐分析期间输入文件发生变化，请重新选择文件后分析。')
            cancelled(job)
            atomic_json(job / 'video-alignment.json', measured)
            raise ValueError(_alignment_error(measured))
        offset = float(measured['offset_seconds'])

    ffmpeg = load_config()['ffmpeg']
    output.parent.mkdir(parents=True, exist_ok=True)
    (job / 'temp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='video-mix-', dir=job/'temp') as folder:
        folder = Path(folder)
        filters = [f'atrim=start={-offset:.6f}'] if offset < 0 else []
        filters.append('asetpts=PTS-STARTPTS')
        if offset > 0:
            filters.append(f'adelay={offset*1000:.3f}:all=1')
        loudness = None
        if normalize:
            first = ','.join(filters+['loudnorm=I=-14:TP=-1:LRA=11:print_format=json'])
            run_process([ffmpeg, '-nostdin', '-hide_banner', '-y', '-i', str(mixdown), '-vn',
                         '-af', first, '-f', 'null', '-'], job, 'mix_loudness')
            text = (job/'mix_loudness.log').read_text(encoding='utf-8', errors='replace')
            blocks = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', text, re.S)
            if not blocks:
                raise ValueError('未能读取响度测量结果。')
            loudness = json.loads(blocks[-1])
            names = ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')
            if any(not math.isfinite(float(loudness[name])) for name in names):
                raise ValueError('Mixdown 没有可测量的有效响度，请关闭响度处理或检查音频。')
            filters.append('loudnorm=I=-14:TP=-1:LRA=11:linear=true:measured_I={input_i}:'
                'measured_TP={input_tp}:measured_LRA={input_lra}:measured_thresh={input_thresh}:'
                'offset={target_offset}'.format(**loudness))
        filters.append('apad')  # Keep the full video when the mix has a shorter tail.
        pending = output.with_name(output.stem+'.pending.mp4')
        if pending.exists():
            raise FileExistsError('发现同名合成临时文件，请选择其他输出文件名。')
        try:
            run_process([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                         '-i', str(video), '-i', str(mixdown), '-map', '0:v:0', '-map', '1:a:0',
                         '-c:v', 'copy', '-af', ','.join(filters), '-c:a', 'aac', '-b:a', '320k',
                         '-ar', '44100', '-shortest', '-movflags', '+faststart', str(pending)], job, 'mix_video')
            # Decode both mapped streams before exposing a finished video.
            run_process([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(pending),
                         '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], job, 'verify_mix_video')
            current, _ = _snapshot_inputs(roles)
            if pending.stat().st_size < 1024 or not _same_inputs(fingerprints, current):
                raise ValueError('合成期间输入文件发生变化或输出不完整。')
            cancelled(job)
            if auto:
                atomic_json(job / 'video-alignment.json', measured)
            # rename() refuses an existing destination on Windows.
            pending.rename(output)
        finally:
            pending.unlink(missing_ok=True)
    report = {'version': 1, 'output': str(output), 'sha256': file_hash(output), 'inputs': path_hashes,
              'alignment': measured, 'normalized': bool(normalize), 'loudness_measurement': loudness,
              'video_policy': 'copy_full_video_replace_original_audio'}
    atomic_json(output.with_suffix('.mixdown.json'), report)
    return report


def render(job, video, mixdown, output, *, reset_cancel=True, **options):
    from pipeline import job_lock
    job = Path(job).resolve()
    with job_lock(job):
        if reset_cancel:
            (job/'cancel.flag').unlink(missing_ok=True)
        cancelled(job)
        return _render(job, video, mixdown, output, **options)


def main():
    parser = argparse.ArgumentParser(description='Mixdown 与原视频对齐合成')
    for name in ('job', 'video', 'mixdown', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--offset', type=float, help='正数延后音频，负数提前；留空为自动对齐')
    parser.add_argument('--normalize', action='store_true')
    args = vars(parser.parse_args())
    render(**args)


if __name__ == '__main__':
    main()
