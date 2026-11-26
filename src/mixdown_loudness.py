"""Loudness of final PCM and bounded comparisons of the two audition mixes."""
from __future__ import annotations

import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory


INTEGRATION_SECONDS = .4
MAX_COMPARE_MS = 30_000
OVERLOAD_MARKER_LIMIT = 16


def _summary_number(summary, expression):
    match = re.search(expression, summary, re.MULTILINE)
    if not match:
        raise ValueError('FFmpeg 未返回完整的响度分析结果。')
    value = float(match.group(1))
    return value if math.isfinite(value) else None


def measure_file(path, ffmpeg, log_path, sample_rate, frames, cancel=None, *, true_peak=True):
    """Measure a fixed, post-limiter WAV using the bundled FFmpeg ebur128 filter."""
    from mixdown_ffmpeg import _run_ffmpeg

    peaks = ':peak=true' if true_peak else ''
    _, report = _run_ffmpeg(
        [str(ffmpeg), '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'info',
         '-i', str(path), '-af', f'ebur128=framelog=verbose{peaks}',
         '-f', 'null', '-'],
        Path(log_path), cancel, '无法测量混音响度或真峰值。',
        timeout=max(20.0, frames / sample_rate),
    )
    summary = report.rsplit('Summary:', 1)[-1]
    integrated = _summary_number(summary, r'^\s*I:\s+([^\s]+)\s+LUFS')
    peak = (_summary_number(summary, r'True peak:\s*\n\s*Peak:\s+([^\s]+)\s+dBFS')
            if true_peak else None)
    status = 'measured'
    if frames < math.ceil(sample_rate * INTEGRATION_SECONDS):
        status = 'too_short'
    elif integrated is None or integrated <= -70.0:
        status = 'below_gate'
    return {
        'status': status, 'method': 'ffmpeg_ebur128',
        'integrated_lufs': integrated if status == 'measured' else None,
        'true_peak_dbtp': peak,
    }


class PreLimiterOverloads:
    """Summarize full-scale crossings in a bounded number of time markers."""
    def __init__(self, sample_rate):
        self._rate = sample_rate
        self._frames = 0
        self._markers = []
        self._last_second = -1

    def observe(self, block, peak, start_frame, numpy):
        if peak <= 1.0:
            return
        frames = numpy.flatnonzero(numpy.any(numpy.abs(block) > 1.0, axis=1))
        self._frames += len(frames)
        if len(self._markers) == OVERLOAD_MARKER_LIMIT:
            return
        for second in range(start_frame // self._rate,
                            (start_frame + len(block) - 1) // self._rate + 1):
            if second <= self._last_second:
                continue
            index = frames.searchsorted(second * self._rate - start_frame)
            if index == len(frames):
                break
            frame = start_frame + int(frames[index])
            if frame // self._rate != second:
                continue
            self._markers.append(round(frame * 1000 / self._rate))
            self._last_second = second
            if len(self._markers) == OVERLOAD_MARKER_LIMIT:
                break

    def report(self):
        return {'pre_limiter_overload_frames': self._frames,
                'pre_limiter_overload_ms': list(self._markers)}


def compare_range(before, after, start_ms, end_ms, cache_dir, dll_path, cancel=None, *, work_dir=None):
    """Read the same device-graph PCM for both mixes without opening a device."""
    from contextlib import ExitStack

    import soundfile

    from mixdown_ffmpeg import _check_cancel, _ffmpeg_executable
    from mixdown_realtime_graph import LimitedMixSession

    if not (type(start_ms) is int and type(end_ms) is int
            and start_ms >= 0 and 0 < end_ms - start_ms <= MAX_COMPARE_MS):
        raise ValueError('响度匹配需要有效的试听范围，最长 30 秒。')

    ffmpeg = _ffmpeg_executable()
    with ExitStack() as resources:
        temporary = Path(resources.enter_context(TemporaryDirectory(prefix='vwa-mix-ab-', dir=work_dir)))
        sessions = []
        for request in (before, after):
            _check_cancel(cancel)
            session = LimitedMixSession(
                request, cache_dir, dll_path, ffmpeg=ffmpeg,
                cancel=cancel, measure_reverb=False,
            )
            resources.callback(session.close)
            sessions.append(session)
        a, b = sessions
        if a.sample_rate != b.sample_rate or a.source_token != b.source_token:
            raise ValueError('源音频已变化，请重新测量响度。')
        rate = a.sample_rate
        start = round(start_ms * rate / 1000)
        end = min(round(end_ms * rate / 1000), a.duration_frames, b.duration_frames)
        if end - start < math.ceil(rate * INTEGRATION_SECONDS):
            return {'status': 'too_short'}
        values = {}
        for key, session in zip(('A', 'B'), sessions):
            _check_cancel(cancel)
            path = temporary / f'{key}.wav'
            session.seek(start)
            with soundfile.SoundFile(str(path), 'w', rate, session.channels, subtype='FLOAT') as output:
                remaining = end - start
                while remaining:
                    _check_cancel(cancel)
                    frames = min(8192, remaining)
                    output.write(session.render_without_telemetry(frames))
                    remaining -= frames
            measured = measure_file(path, ffmpeg, temporary / f'{key}.log',
                                    rate, end - start, cancel, true_peak=False)
            values[key] = measured['integrated_lufs']
        if None in values.values():
            return {'status': 'below_gate'}
        reference = min(values.values())
        return {'status': 'ready', 'lufs': values,
                'trim_db': {key: round(reference - value, 1) for key, value in values.items()},
                'start_ms': round(start * 1000 / rate), 'end_ms': round(end * 1000 / rate)}
