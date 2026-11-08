"""Persistent mix bus plus the export-compatible lookahead limiter.

The public cursor counts latency-compensated media frames. Source/DSP read
ahead and limiter history belong to this graph, never to the audio callback.
"""
from __future__ import annotations

import math
from pathlib import Path
import threading

import numpy as np

from mixdown_realtime_renderer import PreLimiterMixSession
from mixdown_limiter import StreamingLimiter


class LimitedMixSession:
    OUTPUT_STAGE = 'post_limiter'

    def __init__(self, request, cache_dir, dll_path, *, ffmpeg=None,
                 renderer_factory=None, limiter_factory=None, **runtime):
        self._lock = threading.RLock()
        self._closed = False
        self._volume = 1.0
        self._pre = (renderer_factory or PreLimiterMixSession)(
            request, cache_dir, ffmpeg=ffmpeg, **runtime)
        self.sample_rate = int(self._pre.sample_rate)
        self.channels = int(self._pre.channels)
        try:
            self._limiter = (limiter_factory or StreamingLimiter)(
                Path(dll_path).with_name('vwa_limiter.dll'),
                sample_rate=self.sample_rate, channels=self.channels)
        except Exception:
            self._pre.close()
            raise
        self.duration_frames = int(self._pre.duration_frames)
        self.position_frame = 0
        self.parameter_first_frame = 0
        self._fifo = np.empty((0, self.channels), dtype=np.float32)
        self._finished = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError('limited mix session is closed')

    def _fill(self, frames):
        while len(self._fifo) < frames and not self._finished:
            remaining = self.duration_frames - int(self._pre.position_frame)
            if remaining > 0:
                missing = frames - len(self._fifo)
                initial_delay = max(0, int(self._limiter.latency_frames)
                                    - int(self._limiter.frames_received))
                # Read only the needed output plus unavoidable lookahead. A
                # whole extra DSP quantum here would delay every live change.
                count = min(512, remaining, max(4, missing + initial_delay))
                source = self._pre.render(count)
                block = self._limiter.process(source)
            else:
                block = self._limiter.finish()
                self._finished = True
            block = np.asarray(block, dtype=np.float32)
            if block.ndim != 2 or block.shape[1] != self.channels:
                raise ValueError('limiter returned an invalid PCM shape')
            if len(block):
                self._fifo = np.concatenate((self._fifo, block))

    def _read(self, frames):
        self._fill(frames)
        count = min(frames, len(self._fifo))
        result = np.zeros((frames, self.channels), dtype=np.float32)
        result[:count] = self._fifo[:count]
        self._fifo = self._fifo[count:].copy()
        self.position_frame += frames
        return result

    def render(self, frames):
        if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
            raise ValueError('frames must be a positive integer')
        with self._lock:
            self._ensure_open()
            return np.ascontiguousarray(self._read(frames) * self._volume,
                                       dtype=np.float32)

    def seek(self, frame, preroll_frames=None):
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise ValueError('frame must be an integer')
        with self._lock:
            self._ensure_open()
            if not 0 <= frame <= self.duration_frames:
                raise ValueError('seek is outside the song')
            # Rebuild a bounded release history as well as source/effect history.
            # Limiter state from a different location must never leak across seek.
            history = int(math.ceil(self.sample_rate * .1))
            start = max(0, frame - history)
            self._pre.seek(start, preroll_frames=preroll_frames)
            self._limiter.reset()
            self._fifo = np.empty((0, self.channels), dtype=np.float32)
            self._finished = False
            self.position_frame = start
            while self.position_frame < frame:
                self._read(min(512, frame - self.position_frame))
            self.parameter_first_frame = frame

    def prepare_parameters(self, request):
        # Canonical path/source checks intentionally happen outside this lock.
        return self._pre.prepare_parameters(request)

    def apply_parameters(self, plan):
        with self._lock:
            self._ensure_open()
            old_duration = self.duration_frames
            self._pre.apply_parameters(plan)
            self.duration_frames = int(self._pre.duration_frames)
            self.parameter_first_frame = int(self._pre.position_frame)
            if self._finished or self.duration_frames < old_duration:
                self.seek(min(self.position_frame, self.duration_frames))

    def set_parameters(self, request):
        self.apply_parameters(self.prepare_parameters(request))

    def set_volume(self, volume):
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            raise ValueError('volume must be between 0 and 100')
        value = float(volume)
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('volume must be between 0 and 100')
        with self._lock:
            self._ensure_open()
            self._volume = value / 100.0

    def snapshot(self):
        with self._lock:
            self._ensure_open()
            return dict(stage=self.OUTPUT_STAGE, sample_rate=self.sample_rate,
                        channels=self.channels, duration_frames=self.duration_frames,
                        position_frame=self.position_frame,
                        source_read_frame=int(self._pre.position_frame),
                        fifo_frames=len(self._fifo),
                        limiter_latency_frames=int(self._limiter.latency_frames),
                        seek_history='bounded 100ms limiter history plus FX warmup',
                        output_volume=self._volume)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._limiter.close()
            finally:
                self._pre.close()
