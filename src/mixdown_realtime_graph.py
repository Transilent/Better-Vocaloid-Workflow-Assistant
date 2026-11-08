"""Persistent mix bus plus the export-compatible lookahead limiter.

The public cursor counts latency-compensated media frames. Source/DSP read
ahead and limiter history belong to this graph, never to the audio callback.
"""
from __future__ import annotations

from collections import deque
import math
from pathlib import Path
import threading

import numpy as np

from mixdown_realtime_renderer import (
    PreLimiterMixSession,
    TELEMETRY_BLOCK_SCHEMA,
    TELEMETRY_HISTORY_BLOCKS,
    TELEMETRY_SCHEMA,
    TELEMETRY_SOURCE,
    _gr_metrics,
    _merge_meter,
    _meter,
    merge_telemetry_blocks,
)
from mixdown_limiter import StreamingLimiter


LIMITER_TELEMETRY_HISTORY_FRAMES = 32768


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
        self._telemetry_records: deque[dict] = deque()
        self._telemetry_index: dict[tuple[int, int], dict] = {}
        self._limiter_input_history: deque[tuple[int, np.ndarray]] = deque()
        self._limiter_input_frames = 0
        self._limiter_output_frames = 0
        self._limiter_stream_start = 0

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError('limited mix session is closed')

    def _store_telemetry_record(self, record):
        key = (int(record['frame_start']), int(record['frame_end']))
        self._telemetry_records.append(record)
        self._telemetry_index[key] = record
        while len(self._telemetry_records) > TELEMETRY_HISTORY_BLOCKS:
            removed = self._telemetry_records.popleft()
            removed_key = (int(removed['frame_start']), int(removed['frame_end']))
            if self._telemetry_index.get(removed_key) is removed:
                self._telemetry_index.pop(removed_key, None)

    def _reset_limiter_history(self, stream_start):
        self._telemetry_records.clear()
        self._telemetry_index.clear()
        self._limiter_input_history.clear()
        self._limiter_input_frames = 0
        self._limiter_output_frames = 0
        self._limiter_stream_start = int(stream_start)

    def _append_limiter_input(self, start, samples):
        block = np.ascontiguousarray(samples, dtype=np.float32)
        self._limiter_input_history.append((int(start), block))
        retained = sum(len(value) for _, value in self._limiter_input_history)
        while retained > LIMITER_TELEMETRY_HISTORY_FRAMES and len(self._limiter_input_history) > 1:
            _, removed = self._limiter_input_history.popleft()
            retained -= len(removed)

    def _limiter_input_for_range(self, start, end):
        start, end = int(start), int(end)
        wanted = max(0, end - start)
        if not wanted:
            return np.empty((0, self.channels), dtype=np.float32)
        result = np.zeros((wanted, self.channels), dtype=np.float32)
        covered = np.zeros(wanted, dtype=bool)
        for block_start, value in self._limiter_input_history:
            block_end = block_start + len(value)
            lo, hi = max(start, block_start), min(end, block_end)
            if hi <= lo:
                continue
            destination = lo - start
            source = lo - block_start
            count = hi - lo
            result[destination:destination + count] = value[source:source + count]
            covered[destination:destination + count] = True
        return result if bool(np.all(covered)) else None

    def _record_limiter_output(self, start, block):
        end = int(start + len(block))
        before = self._limiter_input_for_range(start, end)
        limiter = (_gr_metrics(before, block, np) if before is not None else {
            'gr_db': 0.0, 'gr_peak_db': 0.0,
            'input_rms_dbfs': 0.0, 'output_rms_dbfs': 0.0,
            'available': False,
            'reason': 'aligned_limiter_input_was_not_retained',
        })
        limiter.update({
            'enabled': True,
            'scope': 'limiter_bus_pre_output_volume',
            'method': 'aligned_rms_db_difference',
            'latency_frames': int(self._limiter.latency_frames),
        })
        pre_telemetry = None
        get_range = getattr(self._pre, 'telemetry_for_range', None)
        if callable(get_range):
            pre_telemetry = get_range(start, end)
        tracks = list((pre_telemetry or {}).get('tracks', ()))
        pre_bus = ((pre_telemetry or {}).get('bus') or {}).get('pre_limiter')
        if not isinstance(pre_bus, dict):
            pre_bus = _meter(before if before is not None else block, np)
        compressor = ((pre_telemetry or {}).get('dynamics') or {}).get('compressor', {
            'enabled': False, 'gr_db': 0.0, 'gr_peak_db': 0.0,
        })
        self._store_telemetry_record({
            'schema': TELEMETRY_BLOCK_SCHEMA,
            'source': TELEMETRY_SOURCE,
            'frame_start': int(start),
            'frame_end': int(end),
            'frame_count': int(len(block)),
            'sample_rate': self.sample_rate,
            'channels': self.channels,
            'tracks': tracks,
            'bus': {
                'pre_limiter': dict(pre_bus),
                'post_limiter': _meter(block, np),
            },
            'dynamics': {
                'compressor': dict(compressor),
                'limiter': limiter,
            },
            'spectrum': dict((pre_telemetry or {}).get('spectrum') or {}),
            'reverb': dict((pre_telemetry or {}).get('reverb') or {}),
        })

    def _merge_output_telemetry(self, start, end, samples):
        records = []
        for record in reversed(self._telemetry_records):
            record_start = int(record.get('frame_start', -1))
            record_end = int(record.get('frame_end', -1))
            if record_end <= int(start):
                break
            if (record_start >= int(start) and record_end <= int(end)
                    and 'post_limiter' in (record.get('bus') or {})):
                records.append(record)
        records.reverse()
        if not records:
            return None
        merged = merge_telemetry_blocks(records) or {}
        post_values = []
        limiter_values = []
        for record in records:
            count = int(record['frame_count'])
            post_values.append((record['bus']['post_limiter'], count))
            limiter = (record.get('dynamics') or {}).get('limiter')
            if isinstance(limiter, dict):
                limiter_values.append((limiter, count))
        limiter = {}
        if limiter_values:
            total = sum(count for _, count in limiter_values)
            input_power = sum(10.0 ** (float(value.get('input_rms_dbfs', -144.0)) / 10.0)
                              * count for value, count in limiter_values) / total
            output_power = sum(10.0 ** (float(value.get('output_rms_dbfs', -144.0)) / 10.0)
                               * count for value, count in limiter_values) / total
            limiter = dict(limiter_values[-1][0])
            limiter['gr_db'] = round(max(
                0.0, 10.0 * math.log10(max(input_power, 1e-30) / max(output_power, 1e-30))
            ), 4)
            limiter['gr_peak_db'] = round(max(
                float(value.get('gr_peak_db', 0.0)) for value, _ in limiter_values
            ), 4)
            limiter['input_rms_dbfs'] = round(
                10.0 * math.log10(max(input_power, 1e-30)), 4
            )
            limiter['output_rms_dbfs'] = round(
                10.0 * math.log10(max(output_power, 1e-30)), 4
            )
        final_output = _meter(samples, np)
        merged.update({
            'schema': TELEMETRY_SCHEMA,
            'source': TELEMETRY_SOURCE,
            'frame_start': int(start),
            'frame_end': int(end),
            'frame_count': int(end - start),
            'sample_rate': self.sample_rate,
            'channels': self.channels,
            'bus': {
                'pre_limiter': dict(merged.get('bus', {}).get('pre_limiter', {})),
                'post_limiter': _merge_meter(post_values),
                'final_output': final_output,
                'peak_dbfs': final_output['peak_dbfs'],
                'rms_dbfs': final_output['rms_dbfs'],
                'limiter_gr_db': float(limiter.get('gr_db', 0.0)),
            },
            'output_volume': float(self._volume),
            'dynamics': {
                'compressor': dict(merged.get('dynamics', {}).get('compressor', {})),
                'limiter': limiter,
            },
        })
        return merged

    def _fill(self, frames, *, capture_telemetry=True):
        while len(self._fifo) < frames and not self._finished:
            remaining = self.duration_frames - int(self._pre.position_frame)
            if remaining > 0:
                missing = frames - len(self._fifo)
                initial_delay = max(0, int(self._limiter.latency_frames)
                                    - int(self._limiter.frames_received))
                # Read only the needed output plus unavoidable lookahead. A
                # whole extra DSP quantum here would delay every live change.
                count = min(512, remaining, max(4, missing + initial_delay))
                source_start = int(self._pre.position_frame)
                if capture_telemetry:
                    source = self._pre.render(count)
                else:
                    render_without = getattr(
                        self._pre, 'render_without_telemetry', None
                    )
                    source = (
                        render_without(count)
                        if callable(render_without) else self._pre.render(count)
                    )
                self._append_limiter_input(source_start, source)
                self._limiter_input_frames += len(source)
                block = self._limiter.process(source)
            else:
                block = self._limiter.finish()
                self._finished = True
            block = np.asarray(block, dtype=np.float32)
            if block.ndim != 2 or block.shape[1] != self.channels:
                raise ValueError('limiter returned an invalid PCM shape')
            if len(block):
                output_start = self._limiter_stream_start + self._limiter_output_frames
                if capture_telemetry:
                    self._record_limiter_output(output_start, block)
                self._limiter_output_frames += len(block)
                self._fifo = np.concatenate((self._fifo, block))

    def _read(self, frames, *, capture_telemetry=True):
        self._fill(frames, capture_telemetry=capture_telemetry)
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
            start = int(self.position_frame)
            output = np.ascontiguousarray(
                self._read(frames) * self._volume, dtype=np.float32
            )
            telemetry = self._merge_output_telemetry(
                start, start + frames, output
            )
            if telemetry is not None:
                self._store_telemetry_record(telemetry)
            return output

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
            self._reset_limiter_history(start)
            self._fifo = np.empty((0, self.channels), dtype=np.float32)
            self._finished = False
            self.position_frame = start
            while self.position_frame < frame:
                self._read(
                    min(512, frame - self.position_frame),
                    capture_telemetry=False,
                )
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

    def reverb_measurement(self):
        with self._lock:
            self._ensure_open()
            getter = getattr(self._pre, 'reverb_measurement', None)
            return dict(getter()) if callable(getter) else None

    def telemetry_for_range(self, frame_start, frame_end):
        start, end = int(frame_start), int(frame_end)
        if end <= start:
            return None
        with self._lock:
            self._ensure_open()
            record = self._telemetry_index.get((start, end))
            if (record is not None
                    and 'post_limiter' in (record.get('bus') or {})):
                return dict(record)
        return None

    def snapshot(self):
        with self._lock:
            self._ensure_open()
            return dict(stage=self.OUTPUT_STAGE, telemetry_schema=TELEMETRY_SCHEMA,
                        telemetry_history_blocks=TELEMETRY_HISTORY_BLOCKS,
                        sample_rate=self.sample_rate,
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
            self._telemetry_records.clear()
            self._telemetry_index.clear()
            self._limiter_input_history.clear()
            try:
                self._limiter.close()
            finally:
                self._pre.close()
