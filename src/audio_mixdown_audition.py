"""Fast segmented audition rendering for the interactive mix console.

The export renderer remains the source of truth.  This module keeps a decoded
cache for the current source set and renders only a short window around the
playhead.  It is intentionally approximate at segment boundaries: a short
pre-roll warms the shared vocal processors, but the render does not replay the
entire song before every adjustment.  That trade is what makes live audition
possible without changing export results.

Audition reports also contain meter-ready short envelopes, approximate
compressor gain reduction, and a measured reverb energy-decay curve.  These are
analysis results for the UI; they never participate in the final mixdown.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, fields, replace
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
import uuid

import audio_mixdown as backend


AUDITION_CACHE_SCHEMA = "vwa.audio_mixdown.audition-cache/v3"
AUDITION_REPORT_SCHEMA = "vwa.audio_mixdown.audition/v1"
AUDITION_API_VERSION = 2
AUDITION_IDENTITY_SCHEMA = "vwa.audio_mixdown.audition-identity/v1"
ENVELOPE_SCHEMA = "vwa.audio_mixdown.short-envelope/v1"
REVERB_EDC_SCHEMA = "vwa.audio_mixdown.reverb-edc/v1"
SPECTRUM_SCHEMA = "vwa.audio_mixdown.spectrum/v1"
AUDITION_PREROLL_SECONDS = 0.35
# Bound only pathological low-frequency/high-Q EQ history.
AUDITION_EQ_PREROLL_MAX_SECONDS = 8.0
# The export limiter has a 5 ms lookahead. Render a little beyond the visible
# segment so a following transient influences its end just as in a full mix.
AUDITION_LOOKAHEAD_SECONDS = 0.02
AUDITION_DEFAULT_SEGMENT_SECONDS = 12.0
AUDITION_MAX_SEGMENT_SECONDS = 30.0
AUDITION_ENVELOPE_HZ = 30.0
AUDITION_EDC_TAIL_SECONDS = 2.0
AUDITION_EDC_HZ = 120.0
AUDITION_EDC_MIN_DB = -60.0
AUDITION_SPECTRUM_INTERVAL_MS = 100.0
AUDITION_SPECTRUM_MAX_BINS = 128
AUDITION_SPECTRUM_MIN_HZ = 20.0
AUDITION_SPECTRUM_MAX_HZ = 20000.0
AUDITION_SPECTRUM_MIN_FFT_SIZE = 256
AUDITION_SPECTRUM_MAX_FFT_SIZE = 16384
AUDITION_SPECTRUM_DB_DECIMALS = 3
AUDITION_SPECTRUM_FREQUENCY_DECIMALS = 4
AUDITION_MEMORY_CACHE_GENERATIONS = 2
_SILENCE = float(backend.SILENCE_DBFS)
_VARIANTS = ("before", "after", "both")


@dataclass(frozen=True)
class _PreparedSources:
    token: str
    sample_rate: int
    channels: int
    generation: Path
    vocal_paths: tuple[Path, ...]
    vocal_frames: tuple[int, ...]
    backing_path: Path
    backing_frames: int
    cache_hit: bool
    cache_source: str


@dataclass(frozen=True)
class _Window:
    requested_start: int
    render_start: int
    preroll_frames: int
    end: int
    render_end: int
    dry_frames: int
    base_frames: int

    @property
    def frames(self) -> int:
        return self.render_end - self.render_start

    @property
    def duration_frames(self) -> int:
        return self.end - self.requested_start


@dataclass(frozen=True)
class _EnvelopeSpec:
    rate: int
    duration_frames: int
    bucket_frames: int
    count: int
    time_seconds: tuple[float, ...]

    @property
    def bucket_seconds(self) -> float:
        return self.bucket_frames / float(self.rate)


class _SpectrumCollector:
    """Bounded real-time FFT aggregation for one vocal signal tap.

    Each frame analyzes one interval of the rendered audition timeline.  The
    collector ignores pre-roll and trailing render data, so ``frame_start``
    values are always relative to the audible segment, not the warmed render
    start.  Display frequencies are logarithmically spaced centers.  Every
    value is the maximum amplitude among all real FFT bins assigned to that
    center's adjacent logarithmic band, so narrow peaks between centers remain
    visible.  No curve or synthetic amplitude is generated.
    """

    def __init__(self, sample_rate: int, duration_frames: int, channels: int,
                 *, stage: str, signal_sample_point: str, variant: str,
                 numpy: Any):
        self.sample_rate = int(sample_rate)
        self.duration_frames = int(duration_frames)
        self.channels = int(channels)
        self.stage = str(stage)
        self.signal_sample_point = str(signal_sample_point)
        self.variant = str(variant)
        self.numpy = numpy
        self.interval_seconds = AUDITION_SPECTRUM_INTERVAL_MS / 1000.0
        self.hop_frames = max(
            1, int(round(self.sample_rate * self.interval_seconds))
        )
        self.total_frames = max(
            1, int(math.ceil(self.duration_frames / float(self.hop_frames)))
        )
        requested_fft = 1 << max(0, self.hop_frames - 1).bit_length()
        self.fft_size = max(
            AUDITION_SPECTRUM_MIN_FFT_SIZE,
            min(AUDITION_SPECTRUM_MAX_FFT_SIZE, requested_fft),
        )
        self.window_samples = min(self.hop_frames, self.fft_size)
        self.window = numpy.hanning(self.window_samples).astype(numpy.float64)
        all_frequencies = numpy.fft.rfftfreq(
            self.fft_size, d=1.0 / float(self.sample_rate)
        )
        maximum = min(
            AUDITION_SPECTRUM_MAX_HZ, float(all_frequencies[-1]) * 0.98
        )
        minimum = min(AUDITION_SPECTRUM_MIN_HZ, maximum)
        indices = numpy.flatnonzero(
            (all_frequencies >= minimum) & (all_frequencies <= maximum)
        )
        if not len(indices):
            indices = numpy.flatnonzero(all_frequencies > 0.0)
        if not len(indices):
            indices = numpy.asarray([0], dtype=numpy.int64)
        available_indices = indices.astype(numpy.int64)
        if len(available_indices) > AUDITION_SPECTRUM_MAX_BINS:
            low = float(all_frequencies[int(available_indices[0])])
            high = float(all_frequencies[int(available_indices[-1])])
            targets = numpy.geomspace(
                low, high, num=AUDITION_SPECTRUM_MAX_BINS
            )
            centers = numpy.searchsorted(
                all_frequencies, targets, side="left"
            )
            centers = numpy.clip(
                centers, int(available_indices[0]), int(available_indices[-1])
            )
            centers = numpy.unique(centers.astype(numpy.int64))
        else:
            centers = available_indices
        self.frequency_bin_indices = centers.astype(numpy.int64)
        self.frequencies_hz = all_frequencies[self.frequency_bin_indices]
        available_frequencies = all_frequencies[available_indices]
        if len(self.frequencies_hz) > 1:
            band_edges = numpy.sqrt(
                self.frequencies_hz[:-1] * self.frequencies_hz[1:]
            )
            self.frequency_band_edges_hz = numpy.concatenate((
                available_frequencies[:1], band_edges,
                available_frequencies[-1:],
            ))
            band_ids = numpy.searchsorted(
                band_edges, available_frequencies, side="left"
            )
            band_ids = numpy.clip(
                band_ids, 0, len(self.frequencies_hz) - 1
            ).astype(numpy.int64)
        else:
            self.frequency_band_edges_hz = numpy.asarray([
                float(available_frequencies[0]),
                float(available_frequencies[-1]),
            ])
            band_ids = numpy.zeros(len(available_frequencies), dtype=numpy.int64)
        counts = numpy.bincount(
            band_ids, minlength=len(self.frequencies_hz)
        ).astype(numpy.int64)
        if numpy.any(counts <= 0):
            raise RuntimeError("频谱对数频段未能覆盖全部真实 FFT bin。")
        self.available_bin_indices = available_indices
        self._band_offsets = numpy.concatenate((
            numpy.asarray([0], dtype=numpy.int64), numpy.cumsum(counts)
        ))
        self.frequency_band_bin_ranges = []
        for index in range(len(self.frequencies_hz)):
            start = int(self._band_offsets[index])
            end = int(self._band_offsets[index + 1])
            group = available_indices[start:end]
            self.frequency_band_bin_ranges.append(
                [int(group[0]), int(group[-1])]
            )
        self.frame_count = self.total_frames
        self.output_frames = self.duration_frames
        self.frames_dbfs = numpy.full(
            (self.total_frames, len(self.frequency_bin_indices)),
            _SILENCE,
            dtype=numpy.float32,
        )
        self._bucket = numpy.zeros(
            (self.hop_frames, self.channels), dtype=numpy.float32
        )
        self._bucket_frames = 0
        self._current_frame = 0
        self.analysis_seconds = 0.0

    def _analyze_frame(self, frame_index: int, cancel: Callable[[], Any] | None,
                       numpy: Any) -> None:
        if frame_index < 0 or frame_index >= self.total_frames:
            return
        start = time.perf_counter()
        try:
            backend._check_cancel(cancel)
            count = min(self._bucket_frames, self.window_samples)
            if count <= 0:
                return
            chunk = numpy.asarray(
                self._bucket[:count], dtype=numpy.float64
            )
            weights = self.window[:count]
            gain = float(numpy.sum(weights))
            if gain <= 0.0:
                return
            windowed = chunk * weights[:, None]
            spectrum = numpy.fft.rfft(
                windowed, n=self.fft_size, axis=0
            )
            magnitudes = numpy.abs(spectrum) * (2.0 / gain)
            amplitudes = numpy.sqrt(
                numpy.mean(numpy.square(magnitudes), axis=1)
            )
            available_amplitudes = amplitudes[self.available_bin_indices]
            selected = numpy.maximum.reduceat(
                available_amplitudes, self._band_offsets[:-1]
            )
            floor = 10.0 ** (_SILENCE / 20.0)
            values = 20.0 * numpy.log10(numpy.maximum(selected, floor))
            self.frames_dbfs[frame_index] = numpy.round(
                numpy.maximum(float(_SILENCE), values),
                AUDITION_SPECTRUM_DB_DECIMALS,
            ).astype(numpy.float32)
        finally:
            self.analysis_seconds += time.perf_counter() - start

    def feed(self, samples: Any, sample_offset: int,
             cancel: Callable[[], Any] | None = None) -> None:
        """Consume one processed block while discarding pre-roll/out-of-range."""
        source = self.numpy.asarray(samples)
        if source.ndim != 2 or int(source.shape[1]) != self.channels:
            raise ValueError("频谱输入必须是当前试听声道的二维音频块。")
        start = max(0, -int(sample_offset))
        end = min(
            int(source.shape[0]), self.duration_frames - int(sample_offset)
        )
        if end <= start:
            return
        appended = start
        while appended < end:
            position = int(sample_offset) + appended
            frame_index = position // self.hop_frames
            if frame_index >= self.total_frames:
                break
            inside = position - frame_index * self.hop_frames
            take = min(end - appended, self.hop_frames - inside)
            self._bucket[inside:inside + take] = source[
                appended:appended + take
            ]
            self._bucket_frames = max(self._bucket_frames, inside + take)
            appended += take
            if inside + take == self.hop_frames:
                self._analyze_frame(frame_index, cancel, self.numpy)
                self._current_frame = frame_index + 1
                self._bucket.fill(0.0)
                self._bucket_frames = 0

    def finalize(self, output_frames: int,
                 cancel: Callable[[], Any] | None = None) -> None:
        """Finalize the requested segment prefix and discard render-only tail."""
        self.output_frames = max(0, min(self.duration_frames, int(output_frames)))
        self.frame_count = max(
            1, int(math.ceil(self.output_frames / float(self.hop_frames)))
        )
        self.frame_count = min(self.frame_count, self.total_frames)
        if (self._bucket_frames and self._current_frame < self.frame_count):
            self._analyze_frame(
                self._current_frame, cancel, self.numpy
            )

    def payload(self, source_start_sample: int,
                cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
        backend._check_cancel(cancel)
        build_start = time.perf_counter()
        frame_starts = [
            index * self.hop_frames for index in range(self.frame_count)
        ]
        frames = self.frames_dbfs[:self.frame_count]
        payload: dict[str, Any] = {
            "schema": SPECTRUM_SCHEMA,
            "interval_ms": float(AUDITION_SPECTRUM_INTERVAL_MS),
            "frequencies_hz": [
                round(float(value), AUDITION_SPECTRUM_FREQUENCY_DECIMALS)
                for value in self.frequencies_hz.tolist()
            ],
            "frames_dbfs": [
                [round(float(value), AUDITION_SPECTRUM_DB_DECIMALS)
                 for value in row]
                for row in frames.tolist()
            ],
            "stage": self.stage,
            "fft_size": int(self.fft_size),
            "variant": self.variant,
            "bypass_processing": self.variant == "before",
            "signal_sample_point": self.signal_sample_point,
            "sample_rate": int(self.sample_rate),
            "analysis_interval_seconds": round(
                self.hop_frames / float(self.sample_rate), 6
            ),
            "timeline_origin": "audition_segment_start",
            "timeline_start_seconds": 0.0,
            "segment_source_start_sample": int(source_start_sample),
            "hop_samples": int(self.hop_frames),
            "frame_start_samples": frame_starts,
            "frame_start_seconds": [
                round(value / float(self.sample_rate), 6)
                for value in frame_starts
            ],
            "frame_count": int(self.frame_count),
            "bin_count": int(len(self.frequency_bin_indices)),
            "frequency_resolution_hz": round(
                self.sample_rate / float(self.fft_size), 6
            ),
            "frequency_bin_indices": [
                int(value) for value in self.frequency_bin_indices.tolist()
            ],
            "frequency_band_edges_hz": [
                round(float(value), AUDITION_SPECTRUM_FREQUENCY_DECIMALS)
                for value in self.frequency_band_edges_hz.tolist()
            ],
            "frequency_band_bin_ranges_inclusive":
                self.frequency_band_bin_ranges,
            "band_aggregation": "max",
            "band_aggregation_detail":
                "maximum FFT-bin amplitude among adjacent logarithmic bands",
            "serialization_decimals": {
                "frames_dbfs": AUDITION_SPECTRUM_DB_DECIMALS,
                "frequencies_hz": AUDITION_SPECTRUM_FREQUENCY_DECIMALS,
            },
            "window": "hann",
            "window_samples": int(self.window_samples),
            "channel_aggregation": "root_mean_square_of_channel_amplitudes",
            "amplitude_reference": "coherent_gain_compensated_full_scale_sine",
            "analysis_floor_dbfs": float(_SILENCE),
            "data_size": {
                "float32_time_frequency_matrix_bytes": (
                    int(self.frame_count)
                    * int(len(self.frequency_bin_indices))
                    * 4
                ),
                "float32_frequency_axis_bytes": (
                    int(len(self.frequency_bin_indices)) * 4
                ),
                "compact_json_bytes": 0,
            },
        }
        payload["instrumentation_ms"] = round(
            (self.analysis_seconds + time.perf_counter() - build_start) * 1000.0,
            3,
        )
        for _ in range(6):
            size = len(json.dumps(payload, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"))
            if payload["data_size"]["compact_json_bytes"] == size:
                break
            payload["data_size"]["compact_json_bytes"] = size
        return payload


@dataclass(frozen=True)
class _V3Options:
    uses_v3: bool
    eq_bands: tuple[dict[str, Any], ...] | None
    reverb_sends: tuple[float, ...]
    reverb_predelay_ms: float
    width: float
    backend_fields: dict[str, bool]
    shimmed_fields: tuple[str, ...]
    status: str


_MEMORY_LOCK = threading.Lock()
_PREPARED_MEMORY: OrderedDict[tuple[str, str], _PreparedSources] = OrderedDict()
_SOURCE_INFO_MEMORY: OrderedDict[str, tuple[int, tuple[int, ...]]] = OrderedDict()


def _append_warning(warnings: list[str], message: str) -> None:
    if message and message not in warnings:
        warnings.append(message)


def _source_rows(settings: backend._Settings) -> list[dict[str, Any]]:
    sources = [
        ("vocal", index, track.path)
        for index, track in enumerate(settings.vocals)
    ]
    sources.append(("backing", 0, settings.instrumental_path))
    rows = []
    for role, index, path in sources:
        fingerprint = backend._source_fingerprint(path)
        rows.append({
            "role": role,
            "index": index,
            "path": backend._normal_path(path),
            "device": int(fingerprint[0]),
            "inode": int(fingerprint[1]),
            "bytes": int(fingerprint[2]),
            "mtime_ns": int(fingerprint[3]),
        })
    return rows


def _decoder_identity(ffmpeg: Path) -> dict[str, Any]:
    """Invalidate decoded audio if the selected FFmpeg executable changes."""
    fingerprint = backend._source_fingerprint(ffmpeg)
    return {"path": backend._normal_path(ffmpeg), "device": int(fingerprint[0]),
            "inode": int(fingerprint[1]), "bytes": int(fingerprint[2]),
            "mtime_ns": int(fingerprint[3])}


def _cache_token(rows: list[dict[str, Any]], channels: int | None = None,
                 decoder: dict[str, Any] | None = None) -> str:
    encoded = json.dumps(
        {"schema": AUDITION_CACHE_SCHEMA, "sources": rows, "channels": channels,
         "decoder": decoder},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_cached_name(value: Any) -> bool:
    name = str(value)
    return bool(name) and Path(name).name == name and name not in (".", "..")


def _memory_key(cache_root: Path, token: str) -> tuple[str, str]:
    return (backend._normal_path(cache_root), token)


def _memory_get(cache_root: Path, token: str) -> _PreparedSources | None:
    key = _memory_key(cache_root, token)
    with _MEMORY_LOCK:
        cached = _PREPARED_MEMORY.get(key)
        if cached is not None:
            paths = (*cached.vocal_paths, cached.backing_path)
            if all(path.is_file() for path in paths):
                _PREPARED_MEMORY.move_to_end(key)
                return replace(cached, cache_hit=True, cache_source="memory")
            _PREPARED_MEMORY.pop(key, None)
    return None


def _memory_put(cache_root: Path, prepared: _PreparedSources) -> None:
    key = _memory_key(cache_root, prepared.token)
    with _MEMORY_LOCK:
        _PREPARED_MEMORY[key] = replace(
            prepared, cache_hit=True, cache_source="disk"
        )
        _PREPARED_MEMORY.move_to_end(key)
        while len(_PREPARED_MEMORY) > AUDITION_MEMORY_CACHE_GENERATIONS:
            _PREPARED_MEMORY.popitem(last=False)


def _valid_cached_sources(cache_root: Path, rows: list[dict[str, Any]],
                          token: str, soundfile: Any, channels: int,
                          decoder: dict[str, Any]) -> _PreparedSources | None:
    generation = cache_root / token[:16]
    manifest_path = generation / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("schema") != AUDITION_CACHE_SCHEMA:
        return None
    if manifest.get("sources") != rows:
        return None
    if manifest.get('channels') != channels:
        return None
    if manifest.get('decoder') != decoder:
        return None
    try:
        sample_rate = int(manifest["sample_rate"])
        vocal_names = list(manifest["vocals"])
        backing_name = str(manifest["backing"])
        if (not vocal_names or len(vocal_names) != len(rows) - 1
                or not all(_safe_cached_name(name) for name in vocal_names)
                or not _safe_cached_name(backing_name)):
            return None
        vocal_paths = tuple(generation / str(name) for name in vocal_names)
        backing_path = generation / backing_name
        vocal_frames = []
        for path in vocal_paths:
            info = soundfile.info(str(path))
            if int(info.samplerate) != sample_rate or int(info.channels) != channels:
                return None
            vocal_frames.append(int(info.frames))
        backing_info = soundfile.info(str(backing_path))
        if int(backing_info.samplerate) != sample_rate or int(backing_info.channels) != channels:
            return None
        if tuple(vocal_frames) != tuple(int(value) for value in manifest["vocal_frames"]):
            return None
        if int(backing_info.frames) != int(manifest["backing_frames"]):
            return None
        return _PreparedSources(
            token=token,
            sample_rate=sample_rate,
            channels=channels,
            generation=generation,
            vocal_paths=vocal_paths,
            vocal_frames=tuple(vocal_frames),
            backing_path=backing_path,
            backing_frames=int(backing_info.frames),
            cache_hit=True,
            cache_source="disk",
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        return None


def _prepare_sources(settings: backend._Settings, cache_root: Path,
                     ffmpeg: Path, soundfile: Any,
                     cancel: Callable[[], Any] | None,
                     rows: list[dict[str, Any]] | None = None) -> _PreparedSources:
    rows = list(rows) if rows is not None else _source_rows(settings)
    decoder = _decoder_identity(ffmpeg)
    info_token = _cache_token(rows, decoder=decoder)
    with _MEMORY_LOCK:
        source_info = _SOURCE_INFO_MEMORY.get(info_token)
    if source_info is None:
        cache_root.mkdir(parents=True, exist_ok=True)
        infos = []
        for index, path in enumerate([*(track.path for track in settings.vocals), settings.instrumental_path]):
            log = cache_root / ('probe-' + uuid.uuid4().hex + '.log')
            try:
                infos.append(backend._probe_audio(path, ffmpeg, log, cancel))
            finally:
                try: log.unlink()
                except OSError: pass
        source_info = (int(infos[-1].sample_rate), tuple(int(info.channels) for info in infos))
        with _MEMORY_LOCK:
            _SOURCE_INFO_MEMORY[info_token] = source_info
            _SOURCE_INFO_MEMORY.move_to_end(info_token)
            while len(_SOURCE_INFO_MEMORY) > 16:
                _SOURCE_INFO_MEMORY.popitem(last=False)
    sample_rate, source_channels = source_info
    channels = backend._output_channels(settings, list(source_channels))
    token = _cache_token(rows, channels, decoder)
    cached = _memory_get(cache_root, token)
    if cached is not None:
        backend._check_cancel(cancel)
        return cached

    cache_root.mkdir(parents=True, exist_ok=True)
    cached = _valid_cached_sources(cache_root, rows, token, soundfile, channels, decoder)
    if cached is not None:
        backend._check_cancel(cancel)
        _memory_put(cache_root, cached)
        return cached

    generation = cache_root / token[:16]
    generation.mkdir(parents=True, exist_ok=True)
    stage = generation / ("s-" + uuid.uuid4().hex[:8])
    stage.mkdir(parents=True, exist_ok=False)
    try:
        vocal_paths = []
        vocal_frames = []
        for index, track in enumerate(settings.vocals):
            target = stage / ("v%d.wav" % (index + 1))
            info = backend._decode_to_float_wav(
                track.path, target, sample_rate, channels, ffmpeg,
                stage / ("decode-vocal-%d.log" % (index + 1)), cancel,
            )
            vocal_paths.append(target)
            vocal_frames.append(int(info.frames))
        backing_stage = stage / "b.wav"
        backing_info = backend._decode_to_float_wav(
            settings.instrumental_path, backing_stage, sample_rate, channels, ffmpeg,
            stage / "decode-backing.log", cancel,
        )
        backend._check_cancel(cancel)

        final_vocals = tuple(
            generation / ("v%d.wav" % (index + 1))
            for index in range(len(vocal_paths))
        )
        final_backing = generation / "b.wav"
        for source, destination in zip(
            (*vocal_paths, backing_stage), (*final_vocals, final_backing),
        ):
            os.replace(source, destination)
        manifest = {
            "schema": AUDITION_CACHE_SCHEMA,
            "sources": rows,
            "decoder": decoder,
            "sample_rate": sample_rate,
            "channels": channels,
            "vocals": [path.name for path in final_vocals],
            "vocal_frames": vocal_frames,
            "backing": final_backing.name,
            "backing_frames": int(backing_info.frames),
        }
        manifest_temp = generation / ("manifest-%s.tmp" % uuid.uuid4().hex)
        manifest_temp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(manifest_temp, generation / "manifest.json")
        prepared = _PreparedSources(
            token=token,
            sample_rate=sample_rate,
            channels=channels,
            generation=generation,
            vocal_paths=final_vocals,
            vocal_frames=tuple(vocal_frames),
            backing_path=final_backing,
            backing_frames=int(backing_info.frames),
            cache_hit=False,
            cache_source="none",
        )
        _memory_put(cache_root, prepared)
        return prepared
    finally:
        try:
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()
        except OSError:
            pass

def _v3_number(item: Mapping[str, Any], name: str, default: float,
               minimum: float, maximum: float, label: str) -> float:
    if name not in item:
        return float(default)
    return backend._finite_number(item, name, minimum, maximum, label=label)


def _parse_eq_bands(request: Mapping[str, Any]) -> tuple[dict[str, Any], ...] | None:
    if "eq_bands" not in request:
        return None
    value = request["eq_bands"]
    if not isinstance(value, list):
        raise ValueError("eq_bands 必须是包含至多 4 段的列表。")
    if len(value) > 4:
        raise ValueError("eq_bands 最多包含 4 段。")
    allowed = {"highpass", "lowshelf", "bell", "highshelf"}
    bands = []
    for index, item in enumerate(value):
        prefix = "eq_bands[%d]" % index
        if not isinstance(item, Mapping):
            raise ValueError(prefix + " 必须是对象。")
        band_type = item.get("type")
        if band_type not in allowed:
            raise ValueError(prefix + ".type 必须是 highpass、lowshelf、bell 或 highshelf。")
        bands.append({
            "type": str(band_type),
            "freq_hz": backend._finite_number(
                item, "freq_hz", 10.0, 20000.0, label=prefix + ".freq_hz"
            ),
            "gain_db": _v3_number(
                item, "gain_db", 0.0, -24.0, 24.0, prefix + ".gain_db"
            ),
            "q": _v3_number(item, "q", 0.707, 0.1, 18.0, prefix + ".q"),
            "enabled": backend._boolean_value(
                item, "enabled", True, label=prefix + ".enabled"
            ),
        })
    return tuple(bands)


def _backend_capabilities(settings: backend._Settings) -> dict[str, bool]:
    try:
        setting_names = {field.name for field in fields(settings)}
    except TypeError:
        setting_names = set()
    try:
        track_names = {field.name for field in fields(settings.vocals[0])}
    except (TypeError, IndexError):
        track_names = set()
    return {
        "eq_bands": "eq_bands" in setting_names,
        "reverb_predelay_ms": "reverb_predelay_ms" in setting_names,
        "width": "width" in setting_names,
        "vocal_reverb_send": "reverb_send" in track_names,
    }


def _v3_options(request: Mapping[str, Any], settings: backend._Settings) -> _V3Options:
    raw_vocals = request.get("vocals")
    vocal_send_present = (
        isinstance(raw_vocals, list)
        and any(isinstance(item, Mapping) and "reverb_send" in item for item in raw_vocals)
    )
    uses_v3 = bool(settings.uses_v3 or backend._uses_v3(request))
    eq_bands = _parse_eq_bands(request)

    sends = []
    for index, _track in enumerate(settings.vocals):
        item = (
            raw_vocals[index]
            if isinstance(raw_vocals, list) and index < len(raw_vocals)
            else None
        )
        if isinstance(item, Mapping):
            sends.append(backend._optional_number(
                item, "reverb_send", 1.0, 0.0, 1.0,
                label="vocals[%d].reverb_send" % index,
            ))
        else:
            sends.append(1.0)

    predelay = backend._optional_number(
        request, "reverb_predelay_ms", 0.0, 0.0, 120.0
    )
    width = backend._optional_number(request, "width", 1.0, 0.0, 2.0)
    capabilities = _backend_capabilities(settings)
    shimmed = []
    if uses_v3:
        if eq_bands is not None and not capabilities["eq_bands"]:
            shimmed.append("eq_bands")
        if vocal_send_present and not capabilities["vocal_reverb_send"]:
            shimmed.append("vocals[].reverb_send")
        if "reverb_predelay_ms" in request and not capabilities["reverb_predelay_ms"]:
            shimmed.append("reverb_predelay_ms")
        if "width" in request and not capabilities["width"]:
            shimmed.append("width")
    if not uses_v3:
        status = "not_requested"
    elif shimmed:
        status = "audition_shim"
    else:
        status = "native"
    return _V3Options(
        uses_v3=uses_v3,
        eq_bands=eq_bands,
        reverb_sends=tuple(sends),
        reverb_predelay_ms=predelay,
        width=width,
        backend_fields=capabilities,
        shimmed_fields=tuple(shimmed),
        status=status,
    )


def _identity_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_cache_identity(settings: backend._Settings, options: _V3Options,
                           window: _Window, prepared: _PreparedSources,
                           variant: str) -> dict[str, Any]:
    """Return pure request identities without caching processed audio.

    Decoded PCM is source-only cache data.  Processed audition audio is never
    reused, so every accepted parameter/viewport change renders afresh; these
    keys make that separation auditable and prevent a future processed-audio
    cache from omitting any v3 tone parameter.
    """
    vocals = [
        {
            "path": backend._normal_path(track.path),
            "gain_db": float(track.gain_db),
            "offset_seconds": float(track.offset_seconds),
            "mute": bool(track.mute),
            "solo": bool(track.solo),
            "pan": float(track.pan),
            "reverb_send": float(options.reverb_sends[index]),
        }
        for index, track in enumerate(settings.vocals)
    ]
    tone_payload = {
        "schema": AUDITION_IDENTITY_SCHEMA + "/tone",
        "source_token": prepared.token,
        "vocals": vocals,
        "instrumental": {
            "path": backend._normal_path(settings.instrumental_path),
            "gain_db": float(settings.instrumental_db),
            "mute": bool(settings.instrumental_mute),
            "solo": bool(settings.instrumental_solo),
            "pan": float(settings.instrumental_pan),
        },
        "uses_v3": bool(options.uses_v3),
        "eq_bands": (
            None if options.eq_bands is None
            else [dict(band) for band in options.eq_bands]
        ),
        "eq_low_cut_hz": float(settings.eq_low_cut_hz),
        "eq_presence_db": float(settings.eq_presence_db),
        "compression_strength": float(settings.compression_strength),
        "reverb_mix": float(settings.reverb_mix),
        "reverb_profile": str(settings.reverb_profile),
        "reverb_predelay_ms": float(options.reverb_predelay_ms),
        "width": float(options.width),
    }
    segment_payload = {
        "schema": AUDITION_IDENTITY_SCHEMA + "/segment",
        "source_token": prepared.token,
        "sample_rate": int(prepared.sample_rate),
        "start_frame": int(window.requested_start),
        "duration_frames": int(window.duration_frames),
    }
    tone_key = _identity_digest(tone_payload)
    segment_key = _identity_digest(segment_payload)
    render_key = _identity_digest({
        "schema": AUDITION_IDENTITY_SCHEMA + "/render",
        "tone_key": tone_key,
        "segment_key": segment_key,
        "variant": variant,
    })
    return {
        "schema": AUDITION_IDENTITY_SCHEMA,
        "tone_cache_key": tone_key,
        "segment_cache_key": segment_key,
        "render_cache_key": render_key,
        "tone_fields": [
            "eq_bands", "vocals[].reverb_send", "compression_strength",
            "width", "reverb_predelay_ms", "reverb_mix", "reverb_profile",
            "vocal_routing", "instrumental_routing",
        ],
        "processed_audio_cached": False,
        "reuse_policy": "render every accepted tone or segment identity",
    }


def _make_eq_chain(pedalboard: Any, settings: backend._Settings,
                   options: _V3Options, sample_rate: int,
                   warnings: list[str]) -> Any | None:
    nyquist = sample_rate / 2.0
    plugins = []
    if options.eq_bands is not None:
        for index, band in enumerate(options.eq_bands):
            if not band["enabled"]:
                continue
            frequency = float(band["freq_hz"])
            max_frequency = min(20000.0, sample_rate * 0.45)
            if frequency > max_frequency:
                clamped = max(10.0, max_frequency)
                _append_warning(
                    warnings,
                    "eq_bands[%d] 的 %.1f Hz 超过当前采样率可用范围，试听与导出均限制到 %.1f Hz。"
                    % (index, frequency, clamped),
                )
                frequency = clamped
            band_type = str(band["type"])
            try:
                if band_type == "highpass":
                    if not hasattr(pedalboard, "HighpassFilter"):
                        raise AttributeError("HighpassFilter")
                    plugins.append(pedalboard.HighpassFilter(
                        cutoff_frequency_hz=frequency,
                    ))
                    if abs(float(band['q']) - .707) > 1e-6 or abs(float(band['gain_db'])) > 1e-12:
                        _append_warning(
                            warnings,
                            "Pedalboard 0.9.26 的 HighpassFilter 不支持自定义 Q 或增益，试听使用固定参数。",
                        )
                elif band_type == "bell":
                    plugins.append(pedalboard.PeakFilter(
                        cutoff_frequency_hz=frequency,
                        gain_db=float(band["gain_db"]),
                        q=float(band["q"]),
                    ))
                elif band_type == "lowshelf":
                    if hasattr(pedalboard, "LowShelfFilter"):
                        plugins.append(pedalboard.LowShelfFilter(
                            cutoff_frequency_hz=frequency,
                            gain_db=float(band["gain_db"]),
                            q=float(band["q"]),
                        ))
                    else:
                        plugins.append(pedalboard.PeakFilter(
                            cutoff_frequency_hz=frequency,
                            gain_db=float(band["gain_db"]),
                            q=float(band["q"]),
                        ))
                        _append_warning(
                            warnings,
                            "Pedalboard 缺少 LowShelfFilter，实时试听以 PeakFilter 近似。",
                        )
                elif band_type == "highshelf":
                    if hasattr(pedalboard, "HighShelfFilter"):
                        plugins.append(pedalboard.HighShelfFilter(
                            cutoff_frequency_hz=frequency,
                            gain_db=float(band["gain_db"]),
                            q=float(band["q"]),
                        ))
                    else:
                        plugins.append(pedalboard.PeakFilter(
                            cutoff_frequency_hz=frequency,
                            gain_db=float(band["gain_db"]),
                            q=float(band["q"]),
                        ))
                        _append_warning(
                            warnings,
                            "Pedalboard 缺少 HighShelfFilter，实时试听以 PeakFilter 近似。",
                        )
            except (AttributeError, TypeError, ValueError) as error:
                raise backend.MixdownError(
                    "eq_bands[%d] 的 %s 无法用于试听（%s）。"
                    % (index, band_type, str(error) or type(error).__name__)
                ) from error
    else:
        if settings.eq_low_cut_hz:
            if settings.eq_low_cut_hz >= nyquist * 0.95:
                raise ValueError("高通频率必须低于输出采样率的奈奎斯特频率。")
            plugins.append(pedalboard.HighpassFilter(
                cutoff_frequency_hz=float(settings.eq_low_cut_hz)
            ))
        if settings.eq_presence_db:
            center = min(3200.0, nyquist * 0.65)
            plugins.append(pedalboard.PeakFilter(
                cutoff_frequency_hz=center,
                gain_db=float(settings.eq_presence_db),
                q=0.75,
            ))
    if not plugins:
        return None
    try:
        return pedalboard.Pedalboard(plugins)
    except Exception as error:
        raise backend.MixdownError("无法创建实时试听 EQ 链。") from error


def _make_compressor(pedalboard: Any, settings: backend._Settings,
                     warnings: list[str]) -> Any | None:
    if settings.compression_strength <= 0.0:
        return None
    strength = settings.compression_strength
    try:
        return pedalboard.Compressor(
            threshold_db=-8.0 - 12.0 * strength,
            ratio=1.0 + 2.0 * strength,
            attack_ms=25.0 - 10.0 * strength,
            release_ms=120.0 + 80.0 * strength,
        )
    except Exception as error:
        raise backend.MixdownError(
            "当前 Pedalboard 无法创建试听压缩器（%s）。"
            % (str(error) or type(error).__name__)
        ) from error


def _make_reverb(pedalboard: Any, settings: backend._Settings) -> Any | None:
    if settings.reverb_mix <= 0.0:
        return None
    if not hasattr(pedalboard, "Reverb"):
        raise backend.MixdownError("当前 Pedalboard 缺少 Reverb 组件。")
    room_size, damping = {
        "small": (0.25, 0.65),
        "natural": (0.58, 0.52),
        "wide": (0.82, 0.42),
    }[settings.reverb_profile]
    return pedalboard.Reverb(
        room_size=room_size,
        damping=damping,
        wet_level=1.0,
        dry_level=0.0,
        width=0.9,
    )


class _DelayedProcessor:
    """A stateful pre-delay in front of one Pedalboard processor."""

    def __init__(self, plugin: Any, delay_frames: int, channels: int, numpy: Any):
        self.plugin = plugin
        self.delay_frames = max(0, int(delay_frames))
        self.channels = int(channels)
        self.numpy = numpy
        self.carry = numpy.zeros((self.delay_frames, channels), dtype=numpy.float32)

    def process(self, block: Any, sample_rate: int, first: bool, numpy: Any) -> Any:
        if self.delay_frames <= 0:
            return backend._pedalboard_process(
                self.plugin, block, sample_rate, first, numpy
            )
        combined = numpy.concatenate((self.carry, block), axis=0)
        current = combined[:len(block)]
        self.carry = numpy.ascontiguousarray(combined[len(block):], dtype=numpy.float32)
        return backend._pedalboard_process(
            self.plugin, current, sample_rate, first, numpy
        )

def _eq_history_seconds(settings: backend._Settings, sample_rate: int) -> float:
    """Estimate the slowest supported IIR resonance down to the tail floor."""
    max_frequency = min(20000.0, sample_rate * 0.45)
    filters: list[tuple[float, float]] = []
    if settings.eq_bands is None:
        if settings.eq_low_cut_hz:
            filters.append((settings.eq_low_cut_hz, 0.707))
        if settings.eq_presence_db:
            filters.append((min(3200.0, sample_rate * 0.325), 0.75))
    else:
        for band in settings.eq_bands:
            if not band.enabled:
                continue
            if band.kind == "highpass":
                q = 0.707
            else:
                if abs(band.gain_db) <= 1e-12:
                    continue
                q = band.q
            filters.append((band.freq_hz, q))
    if not filters:
        return 0.0
    slowest_tau = max(
        max(0.0, float(q)) / (math.pi * max(10.0, min(float(frequency), max_frequency)))
        for frequency, q in filters
    )
    amplitude_floor = 10.0 ** (backend.TAIL_FLOOR_DBFS / 20.0)
    settling = slowest_tau * -math.log(amplitude_floor)
    return min(AUDITION_EQ_PREROLL_MAX_SECONDS, max(0.0, settling))


def _audition_preroll_seconds(settings: backend._Settings, sample_rate: int,
                              has_reverb_send: bool) -> float:
    required = max(AUDITION_PREROLL_SECONDS, _eq_history_seconds(settings, sample_rate))
    if settings.reverb_mix > 0.0 and has_reverb_send:
        predelay_seconds = (backend._predelay_frames(
            settings.reverb_predelay_ms, sample_rate
        ) / float(sample_rate))
        required = max(
            required,
            backend.MAX_REVERB_TAIL_SECONDS + predelay_seconds + AUDITION_PREROLL_SECONDS,
        )
    return required


def _pan_gains(value: float) -> tuple[float, float]:
    if value < 0.0:
        return 1.0, 1.0 + value
    if value > 0.0:
        return 1.0 - value, 1.0
    return 1.0, 1.0


def _audition_window(settings: backend._Settings,
                     prepared: _PreparedSources) -> _Window:
    rate = prepared.sample_rate
    offsets = [
        int(round(track.offset_seconds * rate)) for track in settings.vocals
    ]
    vocal_ends = [
        max(0, frames + offset)
        for frames, offset in zip(prepared.vocal_frames, offsets)
    ]
    dry_frames = max([prepared.backing_frames, *vocal_ends])
    if dry_frames <= 0:
        raise ValueError("伴奏和歌声轨均没有可混音内容。")
    audible, _ = backend._audibility(settings)
    has_send = any(on and (not settings.uses_v3 or track.reverb_send > 0)
                   for track, on in zip(settings.vocals, audible))
    has_reverb = settings.reverb_mix > 0 and has_send
    base_frames = dry_frames + (
        backend._reverb_tail_budget_frames(settings.reverb_predelay_ms, rate)
        if has_reverb else 0
    )
    requested_start = int(round((settings.preview_start_seconds or 0.0) * rate))
    requested_start = max(0, min(base_frames - 1, requested_start))
    duration = settings.preview_duration_seconds or AUDITION_DEFAULT_SEGMENT_SECONDS
    duration_frames = max(1, int(round(float(duration) * rate)))
    duration_frames = min(
        duration_frames,
        base_frames - requested_start,
        int(round(AUDITION_MAX_SEGMENT_SECONDS * rate)),
    )
    if duration_frames <= 0:
        raise ValueError("试听区间没有可输出的采样。")
    # Preserve the normal 350 ms path, extending only for a resonant EQ or
    # the same pre-delay-plus-decay state that the export can retain.
    preroll_seconds = _audition_preroll_seconds(settings, rate, has_send)
    preroll = min(int(round(preroll_seconds * rate)), requested_start)
    requested_end = requested_start + duration_frames
    # When a preview reaches the dry end, inspect the complete possible tail.
    # This lets the UI stop at the same audible end as export instead of
    # treating the maximum tail budget as eight seconds of real audio.
    render_end = (base_frames if has_reverb and requested_end >= dry_frames else
                  min(base_frames, requested_end
                      + int(math.ceil(AUDITION_LOOKAHEAD_SECONDS * rate))))
    return _Window(
        requested_start=requested_start,
        render_start=requested_start - preroll,
        preroll_frames=preroll,
        end=requested_end,
        render_end=render_end,
        dry_frames=dry_frames,
        base_frames=base_frames,
    )


def _envelope_spec(rate: int, duration_frames: int) -> _EnvelopeSpec:
    bucket_frames = max(1, int(round(rate / AUDITION_ENVELOPE_HZ)))
    count = max(1, int(math.ceil(duration_frames / float(bucket_frames))))
    return _EnvelopeSpec(
        rate=rate,
        duration_frames=duration_frames,
        bucket_frames=bucket_frames,
        count=count,
        time_seconds=tuple(
            index * bucket_frames / float(rate) for index in range(count)
        ),
    )


def _clip_envelope_start(spec: _EnvelopeSpec, samples: Any,
                         sample_offset: int, numpy: Any) -> tuple[Any, int, int]:
    sample_count = int(samples.shape[0])
    start = max(0, -int(sample_offset))
    end = min(sample_count, spec.duration_frames - int(sample_offset))
    if end <= start:
        return samples[:0], 0, 0
    absolute_start = max(0, int(sample_offset))
    absolute_end = absolute_start + (end - start)
    return samples[start:end], absolute_start, absolute_end


def _accumulate_peak(target: Any, samples: Any, sample_offset: int,
                     spec: _EnvelopeSpec, numpy: Any) -> None:
    view, absolute_start, absolute_end = _clip_envelope_start(
        spec, samples, sample_offset, numpy
    )
    if not len(view):
        return
    peaks = numpy.max(numpy.abs(view), axis=1)
    first = absolute_start // spec.bucket_frames
    last = (absolute_end - 1) // spec.bucket_frames
    for bucket in range(first, last + 1):
        lo = max(absolute_start, bucket * spec.bucket_frames)
        hi = min(absolute_end, (bucket + 1) * spec.bucket_frames)
        local_lo = lo - absolute_start
        local_hi = hi - absolute_start
        target[bucket] = max(
            float(target[bucket]),
            float(numpy.max(peaks[local_lo:local_hi], initial=0.0)),
        )


def _accumulate_gr(target: Any, before: Any, after: Any, sample_offset: int,
                   spec: _EnvelopeSpec, numpy: Any) -> None:
    view_before, absolute_start, absolute_end = _clip_envelope_start(
        spec, before, sample_offset, numpy
    )
    view_after, after_start, after_end = _clip_envelope_start(
        spec, after, sample_offset, numpy
    )
    if not len(view_before) or (after_start, after_end) != (absolute_start, absolute_end):
        return
    first = absolute_start // spec.bucket_frames
    last = (absolute_end - 1) // spec.bucket_frames
    epsilon = 1e-12
    for bucket in range(first, last + 1):
        lo = max(absolute_start, bucket * spec.bucket_frames)
        hi = min(absolute_end, (bucket + 1) * spec.bucket_frames)
        local_lo = lo - absolute_start
        local_hi = hi - absolute_start
        input_rms = math.sqrt(max(
            0.0,
            float(numpy.mean(numpy.square(
                view_before[local_lo:local_hi], dtype=numpy.float64
            ))),
        ))
        output_rms = math.sqrt(max(
            0.0,
            float(numpy.mean(numpy.square(
                view_after[local_lo:local_hi], dtype=numpy.float64
            ))),
        ))
        if input_rms <= epsilon:
            reduction = 0.0
        elif output_rms <= epsilon:
            reduction = 60.0
        else:
            reduction = max(
                0.0, min(60.0, 20.0 * math.log10(input_rms / output_rms))
            )
        target[bucket] = max(float(target[bucket]), reduction)


def _dbfs(value: float) -> float:
    return max(_SILENCE, 20.0 * math.log10(value)) if value > 0.0 else _SILENCE


def _array_dbfs(values: Any, numpy: Any) -> list[float]:
    return [round(_dbfs(float(value)), 4) for value in numpy.asarray(values).tolist()]


def _apply_width(block: Any, width: float, numpy: Any) -> Any:
    if width == 1.0 or block.shape[1] != 2:
        return block
    mid = (block[:, 0] + block[:, 1]) * numpy.float32(0.5)
    side = (block[:, 0] - block[:, 1]) * numpy.float32(0.5 * width)
    output = numpy.empty_like(block)
    output[:, 0] = mid + side
    output[:, 1] = mid - side
    return output


def _edc_signature(profile: str, predelay_ms: float, sample_rate: int) -> dict[str, Any]:
    return {
        "schema": REVERB_EDC_SCHEMA,
        "profile": profile,
        "predelay_ms": round(float(predelay_ms), 4),
        "sample_rate": int(sample_rate),
        "reverb_width": 0.9,
        "tail_seconds": AUDITION_EDC_TAIL_SECONDS,
        "points_hz": AUDITION_EDC_HZ,
    }


def _edc_key(signature: Mapping[str, Any]) -> str:
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return "%s|%0.3fms|%dHz|%s" % (
        signature["profile"],
        float(signature["predelay_ms"]),
        int(signature["sample_rate"]),
        digest,
    )


def _valid_edc(value: Any, signature: Mapping[str, Any],
               key: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != REVERB_EDC_SCHEMA:
        return None
    if value.get("signature") != dict(signature) or value.get("key") != key:
        return None
    times = value.get("time_seconds")
    curve = value.get("edc_db")
    if not isinstance(times, list) or not isinstance(curve, list) or len(times) != len(curve):
        return None
    if len(times) < 2:
        return None
    try:
        numbers_time = [float(item) for item in times]
        numbers_curve = [float(item) for item in curve]
    except (TypeError, ValueError):
        return None
    if (not all(math.isfinite(item) for item in (*numbers_time, *numbers_curve))
            or any(b < a for a, b in zip(numbers_time, numbers_time[1:]))
            or any(item > 0.001 or item < AUDITION_EDC_MIN_DB - 0.001
                   for item in numbers_curve)):
        return None
    return dict(value)


def _measure_reverb_edc(pedalboard: Any, settings: backend._Settings,
                        sample_rate: int, cache_root: Path, numpy: Any,
                        cancel: Callable[[], Any] | None, *,
                        predelay_ms: float) -> dict[str, Any]:
    signature = _edc_signature(
        settings.reverb_profile, predelay_ms, sample_rate
    )
    key = _edc_key(signature)
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    analysis_dir = cache_root / "analysis"
    cache_path = analysis_dir / ("reverb-edc-%s.json" % digest)
    try:
        cached = _valid_edc(
            json.loads(cache_path.read_text(encoding="utf-8")), signature, key
        )
    except (OSError, ValueError):
        cached = None
    if cached is not None:
        cached["cache_hit"] = True
        return cached

    backend._check_cancel(cancel)
    if not hasattr(pedalboard, "Reverb"):
        raise backend.MixdownError("当前 Pedalboard 缺少 Reverb，无法测量 EDC。")
    room_size, damping = {
        "small": (0.25, 0.65),
        "natural": (0.58, 0.52),
        "wide": (0.82, 0.42),
    }[settings.reverb_profile]
    reverb = pedalboard.Reverb(
        room_size=room_size,
        damping=damping,
        wet_level=1.0,
        dry_level=0.0,
        width=0.9,
    )
    predelay_frames = backend._predelay_frames(predelay_ms, sample_rate)
    frames = max(
        64, predelay_frames + int(round(AUDITION_EDC_TAIL_SECONDS * sample_rate))
    )
    impulse = numpy.zeros((frames, 2), dtype=numpy.float32)
    impulse[min(predelay_frames, frames - 1), :] = numpy.float32(1.0)
    backend._check_cancel(cancel)
    wet = backend._pedalboard_process(reverb, impulse, sample_rate, True, numpy)
    backend._check_cancel(cancel)
    energy = numpy.mean(numpy.square(wet.astype(numpy.float64)), axis=1)
    reverse = numpy.cumsum(energy[::-1])[::-1]
    reference = float(reverse[0]) if len(reverse) else 0.0
    if reference <= 0.0:
        raise backend.MixdownError("混响脉冲测试没有测得可分析的尾音。")
    curve = 10.0 * numpy.log10(numpy.maximum(reverse / reference, 1e-12))
    curve = numpy.clip(curve, AUDITION_EDC_MIN_DB, 0.0)
    curve[0] = min(0.0, float(curve[0]))
    points = max(2, int(round(AUDITION_EDC_TAIL_SECONDS * AUDITION_EDC_HZ)) + 1)
    indices = numpy.rint(numpy.linspace(0, frames - 1, points)).astype(numpy.int64)
    times = indices.astype(numpy.float64) / float(sample_rate)
    measured = {
        "schema": REVERB_EDC_SCHEMA,
        "measured": True,
        "profile": settings.reverb_profile,
        "predelay_ms": float(predelay_ms),
        "sample_rate": int(sample_rate),
        "key": key,
        "signature": signature,
        "curve_range_db": [AUDITION_EDC_MIN_DB, 0.0],
        "time_seconds": [round(float(item), 6) for item in times],
        "edc_db": [round(float(item), 4) for item in curve[indices]],
        "cache_hit": False,
    }
    temp_path = None
    try:
        analysis_dir.mkdir(parents=True, exist_ok=True)
        cached_payload = dict(measured)
        cached_payload.pop("cache_hit", None)
        temp_path = analysis_dir / ("edc-%s.tmp" % uuid.uuid4().hex)
        temp_path.write_text(
            json.dumps(cached_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, cache_path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
    return measured


def _resolve_output(value: str | os.PathLike[str] | None, label: str,
                    sources: list[Path]) -> Path:
    if value is None:
        raise ValueError("%s 输出路径不能为空。" % label)
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".wav":
        raise ValueError("试听输出必须使用 .wav 文件名。")
    if path.exists():
        raise FileExistsError("试听输出已存在，已拒绝覆盖：%s" % path)
    if any(backend._normal_path(path) == backend._normal_path(source) for source in sources):
        raise ValueError("试听输出不能与源音频相同。")
    return path

def _render_variant(settings: backend._Settings, prepared: _PreparedSources,
                    window: _Window, output: Path, *, bypass_processing: bool,
                    pedalboard: Any, numpy: Any, soundfile: Any, ffmpeg: Path,
                    options: _V3Options, reverb_edc: dict[str, Any] | None,
                    warnings: list[str],
                    cancel: Callable[[], Any] | None,
                    known_duration_frames: int | None = None,
                    known_duration_exact: bool = False) -> dict[str, Any]:
    backend._check_cancel(cancel)
    try:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise backend.MixdownError("无法建立试听输出目录。") from error
    if output.exists():
        raise FileExistsError("试听分段输出已存在，已拒绝覆盖：%s" % output)
    raw_fd, raw_name = tempfile.mkstemp(
        prefix=".audition-", suffix=".wav", dir=str(output.parent),
    )
    os.close(raw_fd)
    os.unlink(raw_name)
    raw = Path(raw_name)
    rate, channels = prepared.sample_rate, prepared.channels
    offsets = [
        int(round(track.offset_seconds * rate)) for track in settings.vocals
    ]
    vocal_gains = [10.0 ** (track.gain_db / 20.0) for track in settings.vocals]
    vocal_pans = [_pan_gains(track.pan) for track in settings.vocals]
    backing_gain = 10.0 ** (settings.instrumental_db / 20.0)
    backing_pans = _pan_gains(settings.instrumental_pan)
    vocal_audible, backing_audible = backend._audibility(settings)
    vocal_peaks = [0.0 for _ in settings.vocals]
    backing_peak = 0.0
    bus_peak = 0.0
    spec = _envelope_spec(rate, window.duration_frames)
    spectrum_collector = _SpectrumCollector(
        rate,
        window.duration_frames,
        channels,
        stage=("vocal_before_eq" if bypass_processing else "vocal_after_eq"),
        signal_sample_point=(
            "vocal_bus_after_track_gain_and_pan_before_effects"
            if bypass_processing else
            "vocal_bus_after_eq_before_compressor_and_reverb"
        ),
        variant=("before" if bypass_processing else "after"),
        numpy=numpy,
    )
    track_envelopes = [
        numpy.zeros(spec.count, dtype=numpy.float32)
        for _ in settings.vocals
    ]
    backing_envelope = numpy.zeros(spec.count, dtype=numpy.float32)
    bus_envelope = numpy.zeros(spec.count, dtype=numpy.float32)
    gr_envelope = numpy.zeros(spec.count, dtype=numpy.float32)

    eq_chain = None
    compressor = None
    reverb = None
    reverb_stage = None
    last_tail_frame = None
    if not bypass_processing:
        if pedalboard is None:
            raise backend.MixdownError("实时试听处理后端不可用。")
        eq_chain = _make_eq_chain(pedalboard, settings, options, rate, warnings)
        compressor = _make_compressor(pedalboard, settings, warnings)
        reverb_sends = options.reverb_sends
        if options.uses_v3:
            reverb_needed = any(
                send > 0.0 and audible
                for send, audible in zip(reverb_sends, vocal_audible)
            )
        else:
            reverb_needed = any(vocal_audible)
        if reverb_needed:
            reverb = _make_reverb(pedalboard, settings)
            if reverb is not None:
                delay_frames = backend._predelay_frames(
                    options.reverb_predelay_ms, rate)
                reverb_stage = _DelayedProcessor(reverb, delay_frames, channels, numpy)

    first = True
    try:
        with backend.ExitStack() as stack:
            vocals = [
                stack.enter_context(soundfile.SoundFile(str(path), "r"))
                for path in prepared.vocal_paths
            ]
            backing = stack.enter_context(soundfile.SoundFile(str(prepared.backing_path), "r"))
            mixed_file = stack.enter_context(soundfile.SoundFile(
                str(raw), "w", samplerate=rate, channels=channels,
                format="WAV", subtype="FLOAT",
            ))
            for block_start in range(0, window.frames, backend.CHUNK_FRAMES):
                backend._check_cancel(cancel)
                block_frames = min(backend.CHUNK_FRAMES, window.frames - block_start)
                absolute_start = window.render_start + block_start
                envelope_offset = absolute_start - window.requested_start
                vocal_block = numpy.zeros((block_frames, channels), dtype=numpy.float32)
                backing_block = numpy.zeros((block_frames, channels), dtype=numpy.float32)
                reverb_send_block = numpy.zeros((block_frames, channels), dtype=numpy.float32)

                for index, (handle, frames, offset, gain, pan, audible) in enumerate(zip(
                        vocals, prepared.vocal_frames, offsets, vocal_gains,
                        vocal_pans, vocal_audible)):
                    if not audible:
                        continue
                    source_start = absolute_start - offset
                    destination_start = max(0, -source_start)
                    source_index = max(0, source_start)
                    count = min(block_frames - destination_start, frames - source_index)
                    if count <= 0:
                        continue
                    block = numpy.zeros((block_frames, channels), dtype=numpy.float32)
                    block[destination_start:destination_start + count] = backend._read_frames(
                        handle, source_index, count, channels, numpy,
                    )
                    block *= numpy.float32(gain)
                    if channels == 2:
                        block[:, 0] *= numpy.float32(pan[0])
                        block[:, 1] *= numpy.float32(pan[1])
                    _accumulate_peak(
                        track_envelopes[index], block, envelope_offset, spec, numpy
                    )
                    meter_start = max(0, window.preroll_frames - block_start)
                    meter_end = min(
                        block_frames,
                        window.preroll_frames + window.duration_frames - block_start,
                    )
                    if meter_end > meter_start:
                        vocal_peaks[index] = max(
                            vocal_peaks[index],
                            float(numpy.max(numpy.abs(block[meter_start:meter_end]))),
                        )
                    vocal_block = numpy.add(vocal_block, block, dtype=numpy.float32)
                    if options.uses_v3 and options.reverb_sends[index] > 0.0:
                        reverb_send_block = numpy.add(
                            reverb_send_block,
                            block * numpy.float32(options.reverb_sends[index]),
                            dtype=numpy.float32,
                        )

                if backing_audible:
                    source_index = absolute_start
                    count = min(block_frames, prepared.backing_frames - source_index)
                    if count > 0:
                        backing_block[:count] = backend._read_frames(
                            backing, source_index, count, channels, numpy,
                        )
                        backing_block *= numpy.float32(backing_gain)
                        if channels == 2:
                            backing_block[:, 0] *= numpy.float32(backing_pans[0])
                            backing_block[:, 1] *= numpy.float32(backing_pans[1])
                _accumulate_peak(
                    backing_envelope, backing_block, envelope_offset, spec, numpy
                )
                meter_start = max(0, window.preroll_frames - block_start)
                meter_end = min(
                    block_frames,
                    window.preroll_frames + window.duration_frames - block_start,
                )
                if meter_end > meter_start:
                    backing_peak = max(
                        backing_peak,
                        float(numpy.max(numpy.abs(
                            backing_block[meter_start:meter_end]
                        ))),
                    )

                if bypass_processing:
                    spectrum_collector.feed(
                        vocal_block, envelope_offset, cancel
                    )
                else:
                    dry_vocal = vocal_block
                    if eq_chain is not None:
                        dry_vocal = backend._pedalboard_process(
                            eq_chain, dry_vocal, rate, first, numpy,
                        )
                    equalized_vocal = dry_vocal
                    spectrum_collector.feed(
                        equalized_vocal, envelope_offset, cancel
                    )
                    if compressor is not None:
                        dry_vocal = backend._pedalboard_process(
                            compressor, dry_vocal, rate, first, numpy,
                        )
                        _accumulate_gr(
                            gr_envelope, equalized_vocal, dry_vocal,
                            envelope_offset, spec, numpy,
                        )
                    if reverb_stage is not None:
                        reverb_source = (
                            reverb_send_block if options.uses_v3 else dry_vocal
                        )
                        wet = reverb_stage.process(
                            reverb_source, rate, first, numpy,
                        )
                        mix_value = settings.reverb_mix
                        dry_vocal = (
                            dry_vocal * (1.0 - mix_value) + wet * mix_value
                        ).astype(numpy.float32, copy=False)
                    vocal_block = dry_vocal
                    first = False

                mixed = numpy.add(vocal_block, backing_block, dtype=numpy.float32)
                # A/B bypasses vocal effects, not the shared mix bus routing.
                # Width is part of that routing and must affect both versions.
                if options.width != 1.0:
                    mixed = _apply_width(mixed, options.width, numpy)
                if not numpy.isfinite(mixed).all():
                    raise ValueError("实时试听产生了 NaN 或无穷大采样。")
                if (reverb is not None and window.render_end == window.base_frames
                        and absolute_start + block_frames > window.dry_frames):
                    tail_start = max(0, window.dry_frames - absolute_start)
                    tail_peaks = numpy.max(numpy.abs(mixed[tail_start:]), axis=1)
                    audible_tail = numpy.flatnonzero(
                        tail_peaks > 10.0 ** (backend.TAIL_FLOOR_DBFS / 20.0))
                    if audible_tail.size:
                        last_tail_frame = absolute_start + tail_start + int(audible_tail[-1])
                _accumulate_peak(bus_envelope, mixed, envelope_offset, spec, numpy)
                if meter_end > meter_start:
                    bus_peak = max(
                        bus_peak,
                        float(numpy.max(numpy.abs(mixed[meter_start:meter_end]))),
                    )
                mixed_file.write(mixed)

        backend._check_cancel(cancel)
        duration_frames = window.dry_frames
        duration_exact = window.base_frames == window.dry_frames
        if known_duration_frames is not None:
            duration_frames = int(known_duration_frames)
            duration_exact = bool(known_duration_exact)
        elif reverb is not None and window.render_end == window.base_frames:
            # Export uses this same pre-limiter threshold and margin. History
            # remains bounded by the preview preroll for responsiveness.
            if last_tail_frame is not None:
                margin = int(round(backend.TAIL_MARGIN_SECONDS * rate))
                duration_frames = min(window.base_frames, max(
                    window.dry_frames, last_tail_frame + 1 + margin))
            duration_exact = True
        output_end = min(window.end, duration_frames) if duration_exact else window.end
        output_frames = output_end - window.requested_start
        if output_frames <= 0:
            raise backend.MixdownError("试听起点超过实际混音长度，请重新选择片段。")
        spectrum_collector.finalize(output_frames, cancel)
        if output_frames != window.duration_frames:
            spec = _envelope_spec(rate, output_frames)
            track_envelopes = [value[:spec.count] for value in track_envelopes]
            backing_envelope = backing_envelope[:spec.count]
            bus_envelope = bus_envelope[:spec.count]
            gr_envelope = gr_envelope[:spec.count]
        staged_fd, staged_name = tempfile.mkstemp(
            prefix=".audition-final-", suffix=".wav", dir=str(output.parent),
        )
        os.close(staged_fd)
        os.unlink(staged_name)
        staged = Path(staged_name)
        encode_log = staged.with_suffix('.log')
        encode_start = time.perf_counter()
        try:
            # Match the export limiter. BrickwallLimiter's per-call lookahead
            # compensation shifts subsequent processing blocks by 5 ms.
            backend._encode_final(
                raw, staged, rate, channels, window.preroll_frames,
                window.preroll_frames + output_frames, ffmpeg, encode_log,
                cancel, codec='pcm_f32le',
            )
            encode_ms = _elapsed_ms(encode_start)
            inspect_start = time.perf_counter()
            peak_dbfs = backend._inspect_final(
                staged, rate, channels, output_frames,
                soundfile, numpy, cancel, allowed_subtypes=("FLOAT",),
            )
            inspect_ms = _elapsed_ms(inspect_start)
            backend._check_cancel(cancel)
            tracks = []
            for index, (track, audible, peak) in enumerate(zip(
                    settings.vocals, vocal_audible, vocal_peaks)):
                tracks.append({
                    "role": "vocal",
                    "index": index,
                    "peak_dbfs": _dbfs(peak),
                    "mute": track.mute,
                    "solo": track.solo,
                    "pan": track.pan,
                    "reverb_send": options.reverb_sends[index],
                    "audible": bool(audible),
                    "gain_db": track.gain_db,
                    "offset_seconds": track.offset_seconds,
                })
            tracks.append({
                "role": "backing",
                "index": 0,
                "peak_dbfs": _dbfs(backing_peak),
                "mute": settings.instrumental_mute,
                "solo": settings.instrumental_solo,
                "pan": settings.instrumental_pan,
                "audible": bool(backing_audible),
                "gain_db": settings.instrumental_db,
                "offset_seconds": 0.0,
            })
            spectrum_envelope = spectrum_collector.payload(
                window.requested_start, cancel
            )
            peak_envelope = {
                "schema": ENVELOPE_SCHEMA,
                "kind": "peak",
                "unit": "dBFS",
                "stage": "pre_limiter",
                "hz": AUDITION_ENVELOPE_HZ,
                "bucket_seconds": spec.bucket_seconds,
                "alignment": "bucket_start",
                "timeline_start_seconds": window.requested_start / float(rate),
                "time_seconds": list(spec.time_seconds),
                "tracks": [
                    {
                        "role": "vocal",
                        "index": index,
                        "audible": bool(vocal_audible[index]),
                        "values_dbfs": _array_dbfs(track_envelopes[index], numpy),
                    }
                    for index in range(len(settings.vocals))
                ] + [{
                    "role": "backing",
                    "index": 0,
                    "audible": bool(backing_audible),
                    "values_dbfs": _array_dbfs(backing_envelope, numpy),
                }],
                "bus": {
                    "role": "bus",
                    "index": 0,
                    "values_dbfs": _array_dbfs(bus_envelope, numpy),
                },
            }
            active_gr = numpy.asarray(gr_envelope, dtype=numpy.float64)
            active_gr = active_gr[active_gr > 0.0]
            gr_p95 = (
                float(numpy.percentile(active_gr, 95.0))
                if active_gr.size else 0.0
            )
            gr_payload = {
                "schema": ENVELOPE_SCHEMA,
                "kind": "gain_reduction",
                "unit": "dB",
                "scope": "vocal_bus",
                "method": "chunk_rms_db_difference",
                "enabled": compressor is not None,
                "hz": AUDITION_ENVELOPE_HZ,
                "bucket_seconds": spec.bucket_seconds,
                "alignment": "bucket_start",
                "timeline_start_seconds": window.requested_start / float(rate),
                "time_seconds": list(spec.time_seconds),
                "values_db": [round(float(value), 4) for value in gr_envelope.tolist()],
                "gr_db_p95": round(gr_p95, 4),
            }
            report = {
                "output_path": str(output),
                "sample_rate": rate,
                "channels": channels,
                "output_encoding": {
                    "container": "WAV",
                    "subtype": "FLOAT",
                    "writer": "ffmpeg",
                },
                "timings_ms": {
                    "encode": encode_ms,
                    "inspect": inspect_ms,
                    "spectrum": spectrum_envelope["instrumentation_ms"],
                },
                "duration_seconds": output_frames / float(rate),
                "preview_start_seconds": window.requested_start / float(rate),
                "source_duration_seconds": duration_frames / float(rate),
                "source_duration_measured": duration_exact,
                "maximum_source_duration_seconds": window.base_frames / float(rate),
                "peak_dbfs": peak_dbfs,
                "bypass_processing": bool(bypass_processing),
                "tracks": tracks,
                "meters": {
                    "tracks": tracks,
                    "bus_peak_dbfs": _dbfs(bus_peak),
                    "peak_envelope": peak_envelope,
                    "gr_envelope": gr_payload,
                    "gr_db_p95": round(gr_p95, 4),
                },
                "spectrum_envelope": spectrum_envelope,
                "peak_envelope": peak_envelope,
                "gr_envelope": gr_payload,
                "gr_db_p95": round(gr_p95, 4),
                "width": options.width,
                "warnings": list(warnings),
            }
            if not bypass_processing:
                report["reverb_edc"] = reverb_edc
            backend._check_cancel(cancel)
            backend._install_new_file(staged, output)
            return report
        finally:
            try:
                staged.unlink()
            except OSError:
                pass
            try:
                encode_log.unlink()
            except OSError:
                pass
    finally:
        try:
            raw.unlink()
        except OSError:
            pass


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def render_audition_pair(
        request: Mapping[str, Any],
        before_output: str | os.PathLike[str] | None,
        after_output: str | os.PathLike[str] | None,
        cache_dir: str | os.PathLike[str],
        cancel: Callable[[], Any] | None = None,
        *,
        variant: str = "both") -> dict[str, Any]:
    """Render an A/B audition pair or only the requested side.

    The original five-argument, positional ``variant='both'`` contract is
    unchanged.  New callers may pass ``variant='before'`` or ``'after'`` to
    avoid rendering the unused side.  Unused output arguments are then ignored.
    Each published WAV is staged, verified and atomically installed.
    """
    total_start = time.perf_counter()
    if cancel is not None and not callable(cancel):
        raise TypeError("cancel 必须是无参数可调用对象或 None。")
    if not isinstance(variant, str) or variant.strip().lower() not in _VARIANTS:
        raise ValueError("variant 必须是 before、after 或 both。")
    variant = variant.strip().lower()
    render_before = variant in ("before", "both")
    render_after = variant in ("after", "both")

    settings = backend._settings(request)
    sources = [track.path for track in settings.vocals]
    sources.append(settings.instrumental_path)
    outputs: dict[str, Path] = {}
    if render_before:
        outputs["before"] = _resolve_output(before_output, "处理前", sources)
    if render_after:
        outputs["after"] = _resolve_output(after_output, "处理后", sources)
    if len(outputs) == 2 and (
            backend._normal_path(outputs["before"]) == backend._normal_path(outputs["after"])):
        raise ValueError("处理前与处理后试听必须写入不同文件。")

    backend._check_cancel(cancel)
    ffmpeg = backend._ffmpeg_executable()
    try:
        import numpy
        import soundfile
    except ImportError as error:
        raise backend.MixdownError("实时试听缺少 NumPy 或 SoundFile 运行时依赖。") from error

    cache_root = Path(cache_dir).expanduser().resolve()
    initial_rows = _source_rows(settings)
    prepare_start = time.perf_counter()
    prepared = _prepare_sources(
        settings, cache_root, ffmpeg, soundfile, cancel, rows=initial_rows
    )
    prepare_ms = _elapsed_ms(prepare_start)
    window = _audition_window(settings, prepared)
    options = _v3_options(request, settings)
    cache_identity = _render_cache_identity(
        settings, options, window, prepared, variant,
    )
    warnings: list[str] = []
    if options.shimmed_fields:
        _append_warning(
            warnings,
            "主后端尚未合入 v3 字段：%s；实时试听已在 audition 兼容层应用，导出后端未改变。"
            % "、".join(options.shimmed_fields),
        )

    pedalboard = None
    reverb_edc = ({
        "schema": REVERB_EDC_SCHEMA,
        "measured": False,
        "profile": settings.reverb_profile,
        "predelay_ms": float(options.reverb_predelay_ms),
        "sample_rate": prepared.sample_rate,
        "cache_hit": False,
        "reason": "after_variant_not_requested",
    } if not render_after else None)
    edc_ms = 0.0
    if render_after:
        pedalboard = backend._load_pedalboard()
        edc_start = time.perf_counter()
        try:
            reverb_edc = _measure_reverb_edc(
                pedalboard, settings, prepared.sample_rate, cache_root, numpy, cancel,
                predelay_ms=options.reverb_predelay_ms,
            )
        except InterruptedError:
            raise
        except Exception as error:
            _append_warning(
                warnings,
                "混响 EDC 测量不可用，实时音频继续渲染（%s）。"
                % (str(error) or type(error).__name__),
            )
            reverb_edc = {
                "schema": REVERB_EDC_SCHEMA,
                "measured": False,
                "profile": settings.reverb_profile,
                "predelay_ms": float(options.reverb_predelay_ms),
                "key": "%s|%0.3fms|%dHz" % (
                    settings.reverb_profile,
                    options.reverb_predelay_ms,
                    prepared.sample_rate,
                ),
                "cache_hit": False,
                "reason": str(error) or type(error).__name__,
            }
        edc_ms = _elapsed_ms(edc_start)

    results: dict[str, dict[str, Any]] = {}
    published: list[Path] = []
    render_times: dict[str, float] = {}
    try:
        # The after side measures the real tail when the segment reaches it.
        # Render before second with the same end, so both files stay aligned.
        for side in (("after", "before") if render_after else ("before",)):
            if side not in outputs:
                continue
            side_start = time.perf_counter()
            after_result = results.get("after") if side == "before" else None
            known_duration_frames = (int(round(after_result["source_duration_seconds"]
                                               * prepared.sample_rate))
                                     if after_result else None)
            result = _render_variant(
                settings, prepared, window, outputs[side],
                bypass_processing=(side == "before"),
                pedalboard=pedalboard, numpy=numpy, soundfile=soundfile, ffmpeg=ffmpeg,
                options=options, reverb_edc=reverb_edc, warnings=warnings,
                cancel=cancel, known_duration_frames=known_duration_frames,
                known_duration_exact=bool(after_result and
                                          after_result["source_duration_measured"]),
            )
            results[side] = result
            published.append(outputs[side])
            render_times[side] = _elapsed_ms(side_start)
            backend._check_cancel(cancel)
        if _source_rows(settings) != initial_rows:
            raise backend.MixdownError("实时试听期间源音频发生变化，已丢弃该分段。")
    except BaseException:
        for path in published:
            try:
                path.unlink()
            except OSError:
                pass
        raise

    result: dict[str, Any] = {
        "schema": AUDITION_REPORT_SCHEMA,
        "api_version": AUDITION_API_VERSION,
        "requested_variant": variant,
        "rendered_variants": [
            side for side in ("before", "after") if side in results
        ],
        "cache_dir": str(cache_root),
        "cache_generation": prepared.generation.name,
        "cache_hit": bool(prepared.cache_hit),
        "decode_cache_hit": bool(prepared.cache_hit),
        "decode_cache_source": prepared.cache_source,
        "cache_identity": cache_identity,
        "preview_start_seconds": window.requested_start / float(prepared.sample_rate),
        "preview_duration_seconds": (results.get("after") or results["before"])["duration_seconds"],
        "request_features": {
            "uses_v3": options.uses_v3,
            "eq_band_count": 0 if options.eq_bands is None else len(options.eq_bands),
            "reverb_sends": list(options.reverb_sends),
            "reverb_predelay_ms": options.reverb_predelay_ms,
            "width": options.width,
        },
        "v3_compatibility": {
            "uses_v3": options.uses_v3,
            "status": options.status,
            "backend_fields": dict(options.backend_fields),
            "shimmed_fields": list(options.shimmed_fields),
        },
        "reverb_edc": reverb_edc,
        "warnings": list(warnings),
        "timings_ms": {
            "prepare_sources": prepare_ms,
            "reverb_edc": edc_ms,
            "before": render_times.get("before"),
            "after": render_times.get("after"),
        },
    }
    result.update(results)
    spectrum_summary: dict[str, Any] = {}
    for side in ("before", "after"):
        side_result = results.get(side)
        if side_result is None:
            continue
        envelope = side_result["spectrum_envelope"]
        spectrum_summary[side] = {
            "schema": envelope["schema"],
            "stage": envelope["stage"],
            "variant": envelope["variant"],
            "signal_sample_point": envelope["signal_sample_point"],
            "interval_ms": envelope["interval_ms"],
            "sample_rate": envelope["sample_rate"],
            "fft_size": envelope["fft_size"],
            "frame_count": envelope["frame_count"],
            "bin_count": envelope["bin_count"],
            "compact_json_bytes": envelope["data_size"]["compact_json_bytes"],
            "instrumentation_ms": envelope["instrumentation_ms"],
        }
    default_side = "after" if "after" in results else "before"
    result["spectrum_envelope"] = results[default_side]["spectrum_envelope"]
    result["spectrum_data_summary"] = {
        "schema": SPECTRUM_SCHEMA,
        "selection": "use the envelope attached to the currently playing variant",
        "default_variant": default_side,
        "variants": spectrum_summary,
    }
    result["timings_ms"]["spectrum"] = {
        side: values["instrumentation_ms"]
        for side, values in spectrum_summary.items()
    }
    result["timings_ms"]["total"] = _elapsed_ms(total_start)
    return result


def warm_audition(request: Mapping[str, Any],
                  cache_dir: str | os.PathLike[str],
                  cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Decode and validate the source cache without rendering an output WAV.

    This entry point lets a worker prewarm the source generation in advance.
    It does not import Pedalboard and performs no processing.
    """
    if cancel is not None and not callable(cancel):
        raise TypeError("cancel 必须是无参数可调用对象或 None。")
    start = time.perf_counter()
    settings = backend._settings(request)
    ffmpeg = backend._ffmpeg_executable()
    try:
        import soundfile
    except ImportError as error:
        raise backend.MixdownError("实时试听缺少 SoundFile 运行时依赖。") from error
    cache_root = Path(cache_dir).expanduser().resolve()
    rows = _source_rows(settings)
    prepared = _prepare_sources(
        settings, cache_root, ffmpeg, soundfile, cancel, rows=rows
    )
    if _source_rows(settings) != rows:
        raise backend.MixdownError("预热期间源音频发生变化，已丢弃该缓存。")
    return {
        "schema": "vwa.audio_mixdown.audition-warm/v1",
        "cache_dir": str(cache_root),
        "cache_generation": prepared.generation.name,
        "cache_hit": bool(prepared.cache_hit),
        "decode_cache_source": prepared.cache_source,
        "sample_rate": prepared.sample_rate,
        "timings_ms": {"total": _elapsed_ms(start)},
    }


__all__ = ["render_audition_pair", "warm_audition"]
