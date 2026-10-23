"""Align a finished mix to the source video, with explicit ambiguity handling."""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile

from common import atomic_json, cancelled, file_hash, load_config


def envelope(path, cancel=lambda: None):
    import numpy as np
    import soundfile as sf
    rows = []
    with sf.SoundFile(str(path)) as audio:
        hop = max(1, round(audio.samplerate * .01))
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


def estimate_offset(reference, mixdown, max_shift=30., cancel=lambda: None):
    """Positive offset delays the mix; negative offset trims its leading audio."""
    import numpy as np
    if not math.isfinite(max_shift) or not .01 <= max_shift <= 120:
        raise ValueError('自动对齐搜索范围应为 0.01–120 秒。')
    a, b = envelope(reference, cancel), envelope(mixdown, cancel)
    if min(len(a), len(b)) < 500 or min(float(a.std()), float(b.std())) < 1e-6:
        raise ValueError('自动对齐需要至少 5 秒且有明显变化的音频，请手动填写偏移。')
    nfft = 1 << (len(a) + len(b) - 2).bit_length()
    cross = np.fft.irfft(np.fft.rfft(a, nfft) * np.fft.rfft(b[::-1], nfft), nfft)
    sums = [np.r_[0., np.cumsum(x)] for x in (a, b, a*a, b*b)]
    lags = np.arange(-round(max_shift*100), round(max_shift*100) + 1)
    left_a, left_b = np.maximum(lags, 0), np.maximum(-lags, 0)
    count = np.minimum(len(a)-left_a, len(b)-left_b)
    valid = count >= max(500, min(len(a), len(b)) * .5)
    scores = np.full(len(lags), -1.)
    for index in np.flatnonzero(valid):
        if index % 100 == 0:
            cancel()
        x, y, size = left_a[index], left_b[index], count[index]
        sa, sb, qa, qb = [v[start+size] - v[start] for v, start in zip(sums, (x, y, x, y))]
        denominator = math.sqrt(max(0., qa-sa*sa/size) * max(0., qb-sb*sb/size))
        if denominator > 1e-10:
            scores[index] = (cross[lags[index]+len(b)-1]-sa*sb/size) / denominator
    best = int(np.argmax(scores))
    alternatives = scores[abs(lags - lags[best]) >= 50]
    second = float(np.max(alternatives, initial=-1))
    score, margin = float(scores[best]), float(scores[best]-second)
    accepted = score >= .55 and margin >= .08 and best not in (0, len(lags)-1)
    return {'offset_seconds': round(float(lags[best]) / 100, 3),
            'correlation': round(score, 5), 'margin': round(margin, 5),
            'accepted': accepted, 'overlap_seconds': round(float(count[best]) / 100, 2),
            'method': 'normalized_stereo_energy_correlation', 'resolution_seconds': .01}


def _render(job, video, mixdown, output, *, offset=None, reference=None, normalize=False, max_shift=30):
    from pipeline import run_process
    job, video, mixdown, output = [Path(p).resolve() for p in (job, video, mixdown, output)]
    reference = Path(reference).resolve() if reference else video
    if any(not path.is_file() for path in (video, mixdown, reference)):
        raise FileNotFoundError('请选择存在的原视频、Mixdown 与参考音频。')
    if output.exists() or output in (video, mixdown, reference):
        raise FileExistsError('成品文件已存在，请选择新的文件名。')
    if output.suffix.lower() != '.mp4':
        raise ValueError('合成视频请使用 .mp4 文件名。')
    if offset is not None and (not math.isfinite(offset) or abs(offset) > 3600):
        raise ValueError('手动偏移应在正负 3600 秒内。')
    ffmpeg = load_config()['ffmpeg']
    inputs = {str(p): file_hash(p) for p in (video, mixdown, reference)}
    measured = {'offset_seconds': offset, 'method': 'manual', 'accepted': True}
    output.parent.mkdir(parents=True, exist_ok=True)
    (job / 'temp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='video-mix-', dir=job/'temp') as folder:
        folder = Path(folder)
        if offset is None:
            wavs = []
            for index, path in enumerate((reference, mixdown)):
                wav = folder / (str(index)+'.wav')
                run_process([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(path),
                             '-map', '0:a:0', '-vn', '-ar', '4000', '-ac', '2', '-c:a', 'pcm_f32le', str(wav)],
                            job, 'align_decode_'+str(index))
                wavs.append(wav)
            measured = estimate_offset(*wavs, max_shift=max_shift, cancel=lambda: cancelled(job))
            if not measured['accepted']:
                atomic_json(job/'video-alignment.json', measured)
                raise ValueError('音频自动对齐结果不够明确，请试听后手动填写偏移。候选结果见 video-alignment.json。')
            offset = measured['offset_seconds']
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
            if pending.stat().st_size < 1024 or any(file_hash(Path(path)) != digest for path, digest in inputs.items()):
                raise ValueError('合成期间输入文件发生变化或输出不完整。')
            cancelled(job)
            # rename() refuses an existing destination on Windows.
            pending.rename(output)
        finally:
            pending.unlink(missing_ok=True)
    report = {'version': 1, 'output': str(output), 'sha256': file_hash(output), 'inputs': inputs,
              'alignment': measured, 'normalized': bool(normalize), 'loudness_measurement': loudness,
              'video_policy': 'copy_full_video_replace_original_audio'}
    atomic_json(output.with_suffix('.mixdown.json'), report)
    return report


def render(job, video, mixdown, output, *, reset_cancel=True, **options):
    from pipeline import job_lock
    job = Path(job).resolve()
    with job_lock(job):
        if reset_cancel: (job/'cancel.flag').unlink(missing_ok=True)
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
