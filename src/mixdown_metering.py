"""Pure loudness metering and pre-limiter overload tracker (no realtime graph dependency)."""
from __future__ import annotations

import math
from pathlib import Path
import re

INTEGRATION_SECONDS = 0.4
OVERLOAD_MARKER_LIMIT = 16


def _summary_number(summary, expression):
    match = re.search(expression, summary, re.MULTILINE)
    if not match:
        raise ValueError("FFmpeg 未返回完整的响度分析结果。")
    value = float(match.group(1))
    return value if math.isfinite(value) else None


def measure_file(path, ffmpeg, log_path, sample_rate, frames, cancel=None, *, true_peak=True):
    """Measure a fixed, post-limiter WAV using the bundled FFmpeg ebur128 filter."""
    from mixdown_ffmpeg import _run_ffmpeg

    peaks = ":peak=true" if true_peak else ""
    _, report = _run_ffmpeg(
        [
            str(ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "info",
            "-i",
            str(path),
            "-af",
            f"ebur128=framelog=verbose{peaks}",
            "-f",
            "null",
            "-",
        ],
        Path(log_path),
        cancel,
        "无法测量混音响度或真峰值。",
        timeout=max(20.0, frames / sample_rate),
    )
    summary = report.rsplit("Summary:", 1)[-1]
    integrated = _summary_number(summary, r"^\s*I:\s+([^\s]+)\s+LUFS")
    peak = _summary_number(summary, r"True peak:\s*\n\s*Peak:\s+([^\s]+)\s+dBFS") if true_peak else None
    status = "measured"
    if frames < math.ceil(sample_rate * INTEGRATION_SECONDS):
        status = "too_short"
    elif integrated is None or integrated <= -70.0:
        status = "below_gate"
    return {
        "status": status,
        "method": "ffmpeg_ebur128",
        "integrated_lufs": integrated if status == "measured" else None,
        "true_peak_dbtp": peak,
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
        for second in range(
            start_frame // self._rate,
            (start_frame + len(block) - 1) // self._rate + 1,
        ):
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
        return {
            "pre_limiter_overload_frames": self._frames,
            "pre_limiter_overload_ms": list(self._markers),
        }
