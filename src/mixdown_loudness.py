"""Loudness of final PCM and bounded comparisons of the two audition mixes."""
from __future__ import annotations

import math
from pathlib import Path
from vwa_io import resilient_tempdir

from mixdown_metering import (
    INTEGRATION_SECONDS,
    OVERLOAD_MARKER_LIMIT,
    PreLimiterOverloads,
    _summary_number,
    measure_file,
)

MAX_COMPARE_MS = 30_000


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
        temporary = resources.enter_context(resilient_tempdir(work_dir, 'vwa-mix-ab-'))
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
