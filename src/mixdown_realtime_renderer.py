"""Persistent PRE-LIMITER block renderer for the interactive mix console.

This module owns the DSP session between the native audio output and the
existing mixdown implementation.  Source audio is decoded once through the
audition cache; each later ``render`` call consumes a small block while the EQ,
shared-bus compressor, reverb, and pre-delay retain their processor state.

The emitted bus is explicitly PRE-LIMITER.  It never invokes the FFmpeg
``alimiter`` stage and it does not hard-clip samples.  Consequently it is not a
substitute for, or a parity claim about, the existing final export path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Mapping
import uuid

import audio_mixdown as backend
import mixdown_sources as audition


OUTPUT_STAGE = "pre_limiter"
OUTPUT_SCHEMA = "vwa.audio_mixdown.pre-limiter-session/v1"
TELEMETRY_SCHEMA = "vwa.audio_mixdown.realtime-telemetry/v1"
TELEMETRY_BLOCK_SCHEMA = "vwa.audio_mixdown.realtime-telemetry-block/v1"
TELEMETRY_SOURCE = "realtime_dsp"
TELEMETRY_HISTORY_BLOCKS = 256
TELEMETRY_SPECTRUM_INTERVAL_MS = audition.AUDITION_SPECTRUM_INTERVAL_MS
REALTIME_DEFAULT_WARMUP_MAX_SECONDS = 0.5


@dataclass(frozen=True)
class _PreparedParameters:
    owner: object
    settings: Any
    options: Any
    effects: Any = None
    effects_structure: Any = None
    reverb_edc: Any = None

MIN_STATEFUL_BLOCK_FRAMES = 4
MONO_UPNMIX_DBFS = -3.010299956639812
MONO_UPNMIX_GAIN = 10.0 ** (MONO_UPNMIX_DBFS / 20.0)
MONO_ROUTING_NOTE = (
    "Mono sources are decoded once into equal stereo channels. FFmpeg's "
    "energy-preserving mono-to-stereo upmix applies -3.0103 dB per channel; "
    "the stereo playback bus therefore preserves the mono source's total "
    "energy before pan, width, and effects."
)


def _peak_dbfs(block: Any, numpy: Any) -> float:
    samples = numpy.asarray(block)
    peak = float(numpy.max(numpy.abs(samples), initial=0.0)) if samples.size else 0.0
    return (max(backend.SILENCE_DBFS, 20.0 * math.log10(peak))
            if peak > 0.0 else backend.SILENCE_DBFS)


def _rms_dbfs(block: Any, numpy: Any) -> float:
    samples = numpy.asarray(block)
    if not samples.size:
        return backend.SILENCE_DBFS
    square = numpy.square(samples, dtype=numpy.float64)
    power = float(numpy.mean(square, dtype=numpy.float64))
    rms = math.sqrt(max(0.0, power))
    return (max(backend.SILENCE_DBFS, 20.0 * math.log10(rms))
            if rms > 0.0 else backend.SILENCE_DBFS)


def _meter(block: Any, numpy: Any) -> dict[str, float]:
    return {
        "peak_dbfs": round(_peak_dbfs(block, numpy), 4),
        "rms_dbfs": round(_rms_dbfs(block, numpy), 4),
    }


def _gr_metrics(before: Any, after: Any, numpy: Any) -> dict[str, float]:
    input_rms = _rms_dbfs(before, numpy)
    output_rms = _rms_dbfs(after, numpy)
    input_peak = _peak_dbfs(before, numpy)
    output_peak = _peak_dbfs(after, numpy)
    return {
        "gr_db": round(max(0.0, input_rms - output_rms), 4),
        "gr_peak_db": round(max(0.0, input_peak - output_peak), 4),
        "input_rms_dbfs": round(input_rms, 4),
        "output_rms_dbfs": round(output_rms, 4),
    }


def _power_from_dbfs(value: float) -> float:
    return 10.0 ** (float(value) / 10.0)


def _merge_meter(values: list[tuple[dict[str, float], int]]) -> dict[str, float]:
    total = sum(max(0, int(frames)) for _, frames in values)
    if total <= 0:
        return {"peak_dbfs": backend.SILENCE_DBFS, "rms_dbfs": backend.SILENCE_DBFS}
    peak = max(float(value.get("peak_dbfs", backend.SILENCE_DBFS)) for value, _ in values)
    power = sum(_power_from_dbfs(value.get("rms_dbfs", backend.SILENCE_DBFS))
                * max(0, int(frames)) for value, frames in values) / total
    rms = 10.0 * math.log10(max(power, 10.0 ** (backend.SILENCE_DBFS / 10.0)))
    return {"peak_dbfs": round(peak, 4), "rms_dbfs": round(rms, 4)}


def _merge_gr(values: list[tuple[dict[str, Any], int]]) -> dict[str, Any]:
    total = sum(max(0, int(frames)) for _, frames in values)
    if total <= 0:
        return {"gr_db": 0.0, "gr_peak_db": 0.0}
    input_power = sum(_power_from_dbfs(value.get("input_rms_dbfs", backend.SILENCE_DBFS))
                      * max(0, int(frames)) for value, frames in values) / total
    output_power = sum(_power_from_dbfs(value.get("output_rms_dbfs", backend.SILENCE_DBFS))
                       * max(0, int(frames)) for value, frames in values) / total
    gr = 10.0 * math.log10(max(input_power, 1.0e-30) / max(output_power, 1.0e-30))
    result = dict(values[-1][0])
    result["gr_db"] = round(max(0.0, gr), 4)
    result["gr_peak_db"] = round(max(
        float(value.get("gr_peak_db", 0.0)) for value, _ in values
    ), 4)
    result["input_rms_dbfs"] = round(
        10.0 * math.log10(max(input_power, 1.0e-30)), 4
    )
    result["output_rms_dbfs"] = round(
        10.0 * math.log10(max(output_power, 1.0e-30)), 4
    )
    return result


class _LiveSpectrumAnalyzer:
    """Bounded 100 ms Hann/RFFT tap for the actual pre/post EQ vocal bus."""

    def __init__(self, sample_rate: int, channels: int, numpy: Any) -> None:
        self.numpy = numpy
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.interval_seconds = TELEMETRY_SPECTRUM_INTERVAL_MS / 1000.0
        self.hop_frames = max(1, int(round(self.sample_rate * self.interval_seconds)))
        requested_fft = 1 << max(0, self.hop_frames - 1).bit_length()
        self.fft_size = max(
            audition.AUDITION_SPECTRUM_MIN_FFT_SIZE,
            min(audition.AUDITION_SPECTRUM_MAX_FFT_SIZE, requested_fft),
        )
        self.window_samples = min(self.hop_frames, self.fft_size)
        self.window = numpy.hanning(self.window_samples).astype(numpy.float64)
        frequencies = numpy.fft.rfftfreq(
            self.fft_size, d=1.0 / float(self.sample_rate)
        )
        maximum = min(audition.AUDITION_SPECTRUM_MAX_HZ, float(frequencies[-1]) * 0.98)
        minimum = min(audition.AUDITION_SPECTRUM_MIN_HZ, maximum)
        available = numpy.flatnonzero(
            (frequencies >= minimum) & (frequencies <= maximum)
        ).astype(numpy.int64)
        if not len(available):
            available = numpy.flatnonzero(frequencies > 0.0).astype(numpy.int64)
        if not len(available):
            available = numpy.asarray([0], dtype=numpy.int64)
        if len(available) > audition.AUDITION_SPECTRUM_MAX_BINS:
            low = float(frequencies[int(available[0])])
            high = float(frequencies[int(available[-1])])
            targets = numpy.geomspace(
                low, high, num=audition.AUDITION_SPECTRUM_MAX_BINS
            )
            centers = numpy.searchsorted(frequencies, targets, side="left")
            centers = numpy.clip(centers, int(available[0]), int(available[-1]))
            selected = numpy.unique(centers.astype(numpy.int64))
        else:
            selected = available
        self.available_indices = available
        self.selected_indices = selected.astype(numpy.int64)
        self.frequencies_hz = frequencies[self.selected_indices]
        available_frequencies = frequencies[available]
        if len(self.frequencies_hz) > 1:
            band_edges = numpy.sqrt(
                self.frequencies_hz[:-1] * self.frequencies_hz[1:]
            )
            band_ids = numpy.searchsorted(
                band_edges, available_frequencies, side="left"
            )
            band_ids = numpy.clip(
                band_ids, 0, len(self.frequencies_hz) - 1
            ).astype(numpy.int64)
        else:
            band_ids = numpy.zeros(len(available), dtype=numpy.int64)
        counts = numpy.bincount(
            band_ids, minlength=len(self.frequencies_hz)
        ).astype(numpy.int64)
        self.band_offsets = numpy.concatenate((
            numpy.asarray([0], dtype=numpy.int64), numpy.cumsum(counts),
        ))
        self._before = numpy.zeros((self.hop_frames, self.channels), dtype=numpy.float32)
        self._after = numpy.zeros((self.hop_frames, self.channels), dtype=numpy.float32)
        self._filled = 0
        self._window_start = None
        self.last: dict[str, Any] | None = None

    def reset(self) -> None:
        self._filled = 0
        self._window_start = None
        self.last = None

    def _analyze(self, start: int) -> dict[str, Any]:
        numpy = self.numpy
        weights = self.window[:self._filled]
        gain = float(numpy.sum(weights))
        if gain <= 0.0:
            return {}
        selected = []
        for source in (self._before, self._after):
            windowed = source[:self._filled].astype(numpy.float64) * weights[:, None]
            spectrum = numpy.fft.rfft(windowed, n=self.fft_size, axis=0)
            magnitudes = numpy.abs(spectrum) * (2.0 / gain)
            amplitudes = numpy.sqrt(numpy.mean(numpy.square(magnitudes), axis=1))
            available = amplitudes[self.available_indices]
            reduced = numpy.maximum.reduceat(available, self.band_offsets[:-1])
            floor = 10.0 ** (backend.SILENCE_DBFS / 20.0)
            values = 20.0 * numpy.log10(numpy.maximum(reduced, floor))
            selected.append(numpy.maximum(float(backend.SILENCE_DBFS), values))
        before = [round(float(value), audition.AUDITION_SPECTRUM_DB_DECIMALS)
                  for value in selected[0].tolist()]
        after = [round(float(value), audition.AUDITION_SPECTRUM_DB_DECIMALS)
                 for value in selected[1].tolist()]
        return {
            "schema": "vwa.audio_mixdown.realtime-eq-spectrum/v1",
            "source": "dsp_fft_pre_post_eq",
            "method": "hann_rfft_power_band_max",
            "frequency_hz": [
                round(float(value), audition.AUDITION_SPECTRUM_FREQUENCY_DECIMALS)
                for value in self.frequencies_hz.tolist()
            ],
            "before_dbfs": before,
            "after_dbfs": after,
            "bins": len(before),
            "window_start_frame": int(start),
            "window_end_frame": int(start + self._filled),
            "interval_ms": float(TELEMETRY_SPECTRUM_INTERVAL_MS),
            "fft_size": int(self.fft_size),
            "sample_rate": int(self.sample_rate),
            "before_stage": "vocal_bus_after_gain_pan_pre_eq",
            "after_stage": "vocal_bus_after_eq_before_compressor",
        }

    def feed(self, frame_start: int, before: Any, after: Any) -> None:
        numpy = self.numpy
        before_values = numpy.asarray(before)
        after_values = numpy.asarray(after)
        if before_values.shape != after_values.shape or before_values.ndim != 2:
            raise ValueError("EQ telemetry taps must have matching 2-D shapes")
        offset = 0
        while offset < len(before_values):
            if self._window_start is None:
                self._window_start = int(frame_start) + offset
            take = min(len(before_values) - offset, self.hop_frames - self._filled)
            self._before[self._filled:self._filled + take] = before_values[offset:offset + take]
            self._after[self._filled:self._filled + take] = after_values[offset:offset + take]
            self._filled += take
            offset += take
            if self._filled == self.hop_frames:
                self.last = self._analyze(int(self._window_start))
                self._filled = 0
                self._window_start = None

    def payload(self) -> dict[str, Any]:
        if self.last is not None:
            return dict(self.last)
        return {
            "schema": "vwa.audio_mixdown.realtime-eq-spectrum/v1",
            "source": "dsp_fft_pre_post_eq",
            "method": "hann_rfft_power_band_max",
            "frequency_hz": [
                round(float(value), audition.AUDITION_SPECTRUM_FREQUENCY_DECIMALS)
                for value in self.frequencies_hz.tolist()
            ],
            "before_dbfs": [],
            "after_dbfs": [],
            "bins": int(len(self.frequencies_hz)),
            "window_start_frame": None,
            "window_end_frame": None,
            "interval_ms": float(TELEMETRY_SPECTRUM_INTERVAL_MS),
            "fft_size": int(self.fft_size),
            "sample_rate": int(self.sample_rate),
            "before_stage": "vocal_bus_after_gain_pan_pre_eq",
            "after_stage": "vocal_bus_after_eq_before_compressor",
        }


def merge_telemetry_blocks(blocks: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Merge contiguous DSP block taps without inventing sparse values."""
    if not blocks:
        return None
    start = min(int(block["frame_start"]) for block in blocks)
    end = max(int(block["frame_end"]) for block in blocks)
    frame_count = max(0, end - start)
    track_values: dict[tuple[str, int], list[tuple[Mapping[str, Any], int]]] = {}
    bus_values: list[tuple[Mapping[str, Any], int]] = []
    compressor_values: list[tuple[Mapping[str, Any], int]] = []
    send_values: list[tuple[Mapping[str, Any], int]] = []
    wet_values: list[tuple[Mapping[str, Any], int]] = []
    dry_values: list[tuple[Mapping[str, Any], int]] = []
    for block in blocks:
        count = max(0, int(block["frame_end"]) - int(block["frame_start"]))
        for row in block.get("tracks", ()):
            key = (str(row["role"]), int(row["index"]))
            track_values.setdefault(key, []).append((row, count))
        bus = block.get("bus", {}).get("pre_limiter")
        if isinstance(bus, Mapping):
            bus_values.append((bus, count))
        compressor = block.get("dynamics", {}).get("compressor")
        if isinstance(compressor, Mapping):
            compressor_values.append((compressor, count))
        reverb = block.get("reverb", {}).get("live")
        if isinstance(reverb, Mapping):
            send = reverb.get("send_meter")
            wet = reverb.get("wet_meter")
            dry = reverb.get("dry_meter")
            if isinstance(send, Mapping):
                send_values.append((send, count))
            if isinstance(wet, Mapping):
                wet_values.append((wet, count))
            if isinstance(dry, Mapping):
                dry_values.append((dry, count))
    tracks = []
    for (role, index), values in sorted(
            track_values.items(), key=lambda item: (item[0][0] != "vocal", item[0][1])):
        first = dict(values[-1][0])
        meter = _merge_meter([(value, frames) for value, frames in values])
        first.update(meter)
        first["audible"] = any(bool(value.get("audible", False)) for value, _ in values)
        first["gr_db"] = round(max(float(value.get("gr_db", 0.0)) for value, _ in values), 4)
        tracks.append(first)
    spectrum = None
    for block in reversed(blocks):
        candidate = block.get("spectrum")
        if isinstance(candidate, Mapping) and candidate.get("window_end_frame") is not None:
            spectrum = dict(candidate)
            break
    if spectrum is None:
        candidate = next((block.get("spectrum") for block in reversed(blocks)
                          if isinstance(block.get("spectrum"), Mapping)), None)
        spectrum = dict(candidate or {})
    reverb_blocks = [block.get("reverb", {}) for block in blocks
                     if isinstance(block.get("reverb"), Mapping)]
    reverb = dict(reverb_blocks[-1]) if reverb_blocks else {"enabled": False}
    send_meter = _merge_meter(send_values)
    wet_meter = _merge_meter(wet_values)
    dry_meter = _merge_meter(dry_values)
    mix_value = float(reverb.get("mix", 0.0))
    dry_power = _power_from_dbfs(dry_meter["rms_dbfs"]) * (1.0 - mix_value) ** 2
    wet_power = _power_from_dbfs(wet_meter["rms_dbfs"]) * mix_value ** 2
    wet_fraction = (
        wet_power / (dry_power + wet_power)
        if wet_power + dry_power > 0.0 else None
    )
    reverb["live"] = {
        "send_meter": send_meter,
        "wet_meter": wet_meter,
        "dry_meter": dry_meter,
        "send_peak_dbfs": send_meter["peak_dbfs"],
        "send_rms_dbfs": send_meter["rms_dbfs"],
        "wet_peak_dbfs": wet_meter["peak_dbfs"],
        "wet_rms_dbfs": wet_meter["rms_dbfs"],
        "dry_rms_dbfs": dry_meter["rms_dbfs"],
        "measured_wet_fraction": (
            round(wet_fraction, 6) if wet_fraction is not None else None
        ),
        "level_ratio_method": "reverb_blend_energy_fraction",
        "method": "aligned_processed_wet_return",
        "source": "actual_reverb_send_dsp_output_and_dry_input",
    }
    wet_rms = float(wet_meter["rms_dbfs"])
    dry_rms = float(dry_meter["rms_dbfs"])
    reverb["live"]["wet_to_dry_db"] = (
        round(wet_rms - dry_rms, 4)
        if wet_rms > backend.SILENCE_DBFS and dry_rms > backend.SILENCE_DBFS
        else None
    )
    compressor = _merge_gr([(value, frames) for value, frames in compressor_values])
    compressor.setdefault("enabled", bool(compressor_values and compressor_values[-1][0].get("enabled")))
    compressor["scope"] = "shared_vocal_bus"
    compressor["method"] = "aligned_rms_db_difference"
    return {
        "schema": TELEMETRY_BLOCK_SCHEMA,
        "source": TELEMETRY_SOURCE,
        "frame_start": int(start),
        "frame_end": int(end),
        "frame_count": int(frame_count),
        "sample_rate": int(blocks[0]["sample_rate"]),
        "channels": int(blocks[0]["channels"]),
        "tracks": tracks,
        "bus": {"pre_limiter": _merge_meter(bus_values)},
        "dynamics": {"compressor": compressor},
        "spectrum": spectrum,
        "reverb": reverb,
    }


def _process_exact(plugin: Any, block: Any, sample_rate: int, first: bool,
                   numpy: Any, *, allow_terminal_padding: bool = False) -> Any:
    """Process a continuous block without advancing hidden zero padding.

    Pedalboard 0.9.26 uses its smaller array dimension for channel inference.
    Shapes ``(2, 1)`` and ``(2, 2)`` are therefore ambiguous.  Continuous
    stateful blocks use an explicit minimum of four frames, where the channel
    dimension is unambiguously smaller.  A final 1–3 frame block that reaches
    ``duration_frames`` may use the shared padding helper because no later
    processor state exists.  Any hidden padding in a continuous block is
    rejected instead of silently advancing FX history.
    """
    frames = len(block)
    if frames < MIN_STATEFUL_BLOCK_FRAMES:
        if not allow_terminal_padding:
            raise ValueError(
                "连续 PRE-LIMITER 块必须至少 "
                + str(MIN_STATEFUL_BLOCK_FRAMES) + " 帧。"
            )
        return backend._pedalboard_process(
            plugin, block, sample_rate, first, numpy,
        )
    if frames >= 32:
        return backend._pedalboard_process(
            plugin, block, sample_rate, first, numpy,
        )
    channels = block.shape[1]
    source = numpy.ascontiguousarray(block.T, dtype=numpy.float32)
    output = plugin.process(
        source,
        sample_rate,
        buffer_size=backend.PEDALBOARD_BUFFER_SIZE,
        reset=first,
    )
    output = numpy.asarray(output, dtype=numpy.float32)
    if output.ndim != 2 or output.shape[0] != channels or output.shape[1] < frames:
        raise backend.MixdownError("Pedalboard 返回的音频形状异常，已停止输出。")
    output = output[:, :frames].T
    if not numpy.isfinite(output).all():
        raise ValueError("人声处理产生了 NaN 或无穷大采样，已停止输出。")
    return numpy.ascontiguousarray(output, dtype=numpy.float32)


@dataclass(frozen=True)
class _BuiltEffects:
    structure: Any
    eq_chain: Any
    compressor: Any
    reverb: Any
    reverb_stage: Any
    warnings: list[str]


class _ExactDelayedProcessor(audition._DelayedProcessor):
    """Reuse audition delay state while avoiding sub-32-frame padding."""

    def process(self, block: Any, sample_rate: int, first: bool,
                numpy: Any, *, allow_terminal_padding: bool = False) -> Any:
        if self.delay_frames <= 0:
            return _process_exact(
                self.plugin, block, sample_rate, first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
        combined = numpy.concatenate((self.carry, block), axis=0)
        current = combined[:len(block)]
        self.carry = numpy.ascontiguousarray(
            combined[len(block):], dtype=numpy.float32,
        )
        return _process_exact(
            self.plugin, current, sample_rate, first, numpy,
            allow_terminal_padding=allow_terminal_padding,
        )


def _measure_reverb_edc_chunked(pedalboard: Any, settings: Any,
                               sample_rate: int, cache_root: Path,
                               numpy: Any, predelay_ms: float) -> dict[str, Any]:
    """Measure the configured kernel using bounded 4096-frame native calls.

    This belongs to the isolated DSP worker's control path, never the GUI or
    device callback. Reuse the shared cache identity and preserve filter state
    between chunks.
    """
    signature = audition._edc_signature(
        settings.reverb_profile, predelay_ms, sample_rate
    )
    key = audition._edc_key(signature)
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    analysis_dir = Path(cache_root) / "analysis"
    cache_path = analysis_dir / ("reverb-edc-%s.json" % digest)
    try:
        cached = audition._valid_edc(
            json.loads(cache_path.read_text(encoding="utf-8")), signature, key,
        )
    except (OSError, ValueError):
        cached = None
    if cached is not None:
        cached["cache_hit"] = True
        return cached
    if not hasattr(pedalboard, "Reverb"):
        raise backend.MixdownError("当前 Pedalboard 缺少 Reverb，无法测量 EDC。")
    room_size, damping = {
        "small": (0.25, 0.65),
        "natural": (0.58, 0.52),
        "wide": (0.82, 0.42),
    }[settings.reverb_profile]
    reverb = pedalboard.Reverb(
        room_size=room_size, damping=damping,
        wet_level=1.0, dry_level=0.0, width=0.9,
    )
    predelay_frames = backend._predelay_frames(predelay_ms, sample_rate)
    frames = max(
        64, predelay_frames + int(round(
            audition.AUDITION_EDC_TAIL_SECONDS * sample_rate
        )),
    )
    impulse = numpy.zeros((frames, 2), dtype=numpy.float32)
    impulse[min(predelay_frames, frames - 1), :] = numpy.float32(1.0)
    parts = []
    for start in range(0, frames, 4096):
        count = min(4096, frames - start)
        parts.append(_process_exact(
            reverb, impulse[start:start + count], sample_rate,
            start == 0, numpy,
        ))
    wet = numpy.concatenate(parts, axis=0)
    energy = numpy.mean(numpy.square(wet.astype(numpy.float64)), axis=1)
    reverse = numpy.cumsum(energy[::-1])[::-1]
    reference = float(reverse[0]) if len(reverse) else 0.0
    if reference <= 0.0:
        raise backend.MixdownError("混响脉冲测试没有测得可分析的尾音。")
    curve = 10.0 * numpy.log10(numpy.maximum(reverse / reference, 1e-12))
    curve = numpy.clip(
        curve, audition.AUDITION_EDC_MIN_DB, 0.0
    )
    curve[0] = min(0.0, float(curve[0]))
    points = max(2, int(round(
        audition.AUDITION_EDC_TAIL_SECONDS * audition.AUDITION_EDC_HZ
    )) + 1)
    indices = numpy.rint(numpy.linspace(0, frames - 1, points)).astype(numpy.int64)
    measured = {
        "schema": audition.REVERB_EDC_SCHEMA,
        "measured": True,
        "profile": settings.reverb_profile,
        "predelay_ms": float(predelay_ms),
        "sample_rate": int(sample_rate),
        "key": key,
        "signature": signature,
        "curve_range_db": [audition.AUDITION_EDC_MIN_DB, 0.0],
        "time_seconds": [
            round(float(index) / float(sample_rate), 6) for index in indices
        ],
        "edc_db": [round(float(item), 4) for item in curve[indices]],
        "cache_hit": False,
    }
    temp_path = None
    try:
        analysis_dir.mkdir(parents=True, exist_ok=True)
        temp_path = analysis_dir / ("edc-%s.tmp" % uuid.uuid4().hex)
        temp_path.write_text(
            json.dumps(measured, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, cache_path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
    return measured


class PreLimiterMixSession:
    """A stateful stereo vocal/instrumental bus rendered in caller-sized blocks.

    The public block contract is intentionally small:

    ``render(frames)`` returns exactly ``(frames, 2)`` float32 samples and
    advances ``position_frame`` by the same count.  Samples after the complete
    source/reverb budget are exact zeros.  ``seek`` performs source seeking and
    optional graph warm-up synchronously; callers must invoke it on a control
    path rather than from a device callback.  ``set_parameters`` accepts the
    existing mixdown request shape while keeping the source set and rate fixed.

    ``channels`` is always two because it is the stereo device bus.  Mono
    sources are decoded once to equal channels by the FFmpeg conversion inside
    :func:`mixdown_sources._prepare_sources`; its energy-preserving
    mono-to-stereo upmix is -3.0103 dB per channel.  Pan and width therefore
    operate on an already stable stereo layout and never trigger re-decoding.
    """

    OUTPUT_STAGE = OUTPUT_STAGE

    def __init__(self, request: Mapping[str, Any], cache_dir: str | Path,
                 numpy: Any = None, soundfile: Any = None,
                 pedalboard: Any = None, ffmpeg: str | Path | None = None):
        self._lock = threading.RLock()
        self._parameter_owner = object()
        if numpy is None:
            try:
                import numpy as numpy_module
            except ImportError as error:
                raise backend.MixdownError(
                    "实时 PRE-LIMITER 渲染缺少 NumPy。"
                ) from error
            numpy = numpy_module
        if soundfile is None:
            try:
                import soundfile as soundfile_module
            except ImportError as error:
                raise backend.MixdownError(
                    "实时 PRE-LIMITER 渲染缺少 SoundFile。"
                ) from error
            soundfile = soundfile_module
        if pedalboard is None:
            pedalboard = backend._load_pedalboard()
        required = ("HighpassFilter", "PeakFilter", "Compressor", "Reverb")
        missing = [name for name in required if not hasattr(pedalboard, name)]
        if missing:
            raise backend.MixdownError(
                "Pedalboard 缺少 PRE-LIMITER 所需组件：" + "、".join(missing) + "。"
            )
        version = str(getattr(pedalboard, "__version__", ""))
        if version != backend.PEDALBOARD_VERSION:
            raise backend.MixdownError(
                "PRE-LIMITER 实时渲染固定使用 Pedalboard "
                + backend.PEDALBOARD_VERSION + "，当前版本为 "
                + (version or "未知") + "。"
            )

        self._numpy = numpy
        self._soundfile = soundfile
        self._pedalboard = pedalboard
        settings = backend._settings(request)
        options = audition._v3_options(request, settings)
        if ffmpeg is None:
            ffmpeg = backend._ffmpeg_executable()
        else:
            ffmpeg = Path(ffmpeg).expanduser().resolve()
            if not ffmpeg.is_file():
                raise backend.MixdownError(
                    "指定的 FFmpeg 不存在或不是文件：" + str(ffmpeg)
                )
        cache_root = Path(cache_dir).expanduser().resolve()
        self._cache_root = cache_root
        # Device playback always receives a stereo bus.  Forcing this only in
        # the cache preparation settings makes mono sources explicit,
        # energy-preserving stereo without changing caller routing parameters.
        preparation_settings = replace(
            settings, width=0.0, uses_v3=True,
        )
        prepared = audition._prepare_sources(
            preparation_settings, cache_root, ffmpeg, soundfile, None,
        )

        decoder_identity = audition._decoder_identity(ffmpeg)
        source_rows = audition._source_rows(settings)
        post_token = audition._cache_token(
            source_rows, 2, decoder_identity,
        )
        if post_token != prepared.token:
            raise backend.MixdownError(
                "源文件在解码准备期间发生变化；未建立实时会话。"
            )
        self._request_settings = settings
        self._options = options
        self._prepared = prepared
        self._ffmpeg = ffmpeg
        self.source_token = str(prepared.token)
        self.sample_rate = int(prepared.sample_rate)
        self.channels = 2
        self.source_end_frame = self._source_end(settings)
        self.duration_frames = self._duration(settings)
        self.position_frame = 0
        self._source_paths = self._source_identity(settings)
        self._source_fingerprints = tuple(
            (
                int(row["device"]), int(row["inode"]),
                int(row["bytes"]), int(row["mtime_ns"]),
            )
            for row in source_rows
        )
        self._handles: list[Any] = []
        self._closed = False
        self._first = True
        self._effects_signature = self._effect_structure(options, settings)
        self._eq_chain = None
        self._compressor = None
        self._reverb = None
        self._reverb_stage = None
        self._warnings: list[str] = []
        self._telemetry_records: deque[dict[str, Any]] = deque(
            maxlen=TELEMETRY_HISTORY_BLOCKS
        )
        self._spectrum = None
        self._reverb_kernel_edc: dict[str, Any] = {
            "available": False, "measured": False,
            "reason": "reverb_is_disabled",
        }
        self._reverb_ir_lock = threading.Lock()
        self._reverb_kernel_signature = None
        self._reverb_kernel_cache: dict[Any, dict[str, Any]] = {}
        self.effect_rebuild_count = 0
        self.last_warmup: dict[str, Any] = {
            "reason": "initial",
            "requested_frame": 0,
            "warmup_start_frame": 0,
            "warmup_frames": 0,
            "bounded": False,
            "strategy": "fresh_graph_at_source_start",
            "skipped_below_safe_quantum": False,
        }
        try:
            self._handles = [
                soundfile.SoundFile(str(path), "r")
                for path in (*prepared.vocal_paths, prepared.backing_path)
            ]
            self._rebuild_effects(measure_ir=True)
        except BaseException:
            self._close_handles()
            raise

    @property
    def output_stage(self) -> str:
        return OUTPUT_STAGE

    def _source_end(self, settings: backend._Settings) -> int:
        offsets = [
            int(round(track.offset_seconds * self.sample_rate))
            for track in settings.vocals
        ]
        ends = [
            max(0, frames + offset)
            for frames, offset in zip(
                self._prepared.vocal_frames, offsets,
            )
        ]
        return max([int(self._prepared.backing_frames), *ends])

    def _reverb_enabled(self, settings: backend._Settings) -> bool:
        return float(settings.reverb_mix) > 0.0

    def _duration(self, settings: backend._Settings) -> int:
        end = self._source_end(settings)
        if self._reverb_enabled(settings):
            end += backend._reverb_tail_budget_frames(
                self._options.reverb_predelay_ms, self.sample_rate,
            )
        return int(end)

    @staticmethod
    def _source_identity(settings: backend._Settings) -> tuple[str, ...]:
        return tuple(
            backend._normal_path(path)
            for path in (
                *(track.path for track in settings.vocals),
                settings.instrumental_path,
            )
        )

    def _effect_structure(self, options: Any,
                          settings: backend._Settings) -> tuple[Any, ...]:
        eq_identity = (
            tuple(options.eq_bands)
            if options.eq_bands is not None else None
        )
        return (
            eq_identity,
            float(settings.eq_low_cut_hz),
            float(settings.eq_presence_db),
            float(settings.compression_strength),
            bool(float(settings.reverb_mix) > 0.0),
            str(settings.reverb_profile),
            int(backend._predelay_frames(
                options.reverb_predelay_ms, self.sample_rate)),
        )

    def _reverb_signature(self, reverb: Any, settings: Any, options: Any) -> tuple:
        return (
            bool(reverb is not None),
            str(settings.reverb_profile),
            float(options.reverb_predelay_ms),
            int(self.sample_rate),
        )

    def _prepare_reverb_edc(self, built: _BuiltEffects, settings: Any,
                            options: Any) -> dict[str, Any]:
        signature = self._reverb_signature(built.reverb, settings, options)
        if built.reverb is None or float(settings.reverb_mix) <= 0.0:
            return {
                "available": False,
                "measured": False,
                "measuring": False,
                "reason": "reverb_is_disabled",
                "method": "actual_pedalboard_reverb_impulse_response",
                "excitation": "unit_impulse",
                "measurement_scope": "configured_dsp_kernel_not_source_material",
            }
        with self._reverb_ir_lock:
            cached = self._reverb_kernel_cache.get(signature)
            if cached is not None:
                return dict(cached)
        try:
            measured = _measure_reverb_edc_chunked(
                self._pedalboard, settings, self.sample_rate,
                self._cache_root, self._numpy, options.reverb_predelay_ms,
            )
            result = dict(measured)
            result.update({
                "available": True,
                "measured": True,
                "measuring": False,
                "method": "actual_pedalboard_reverb_impulse_response",
                "excitation": "unit_impulse",
                "measurement_scope": "configured_dsp_kernel_not_source_material",
            })
        except Exception as error:
            result = {
                "available": False,
                "measured": False,
                "measuring": False,
                "reason": "%s: %s" % (type(error).__name__, error),
                "method": "actual_pedalboard_reverb_impulse_response",
                "excitation": "unit_impulse",
                "measurement_scope": "configured_dsp_kernel_not_source_material",
            }
        with self._reverb_ir_lock:
            self._reverb_kernel_cache[signature] = dict(result)
        return result

    def _measure_reverb_kernel_now(self) -> None:
        reverb = self._reverb
        signature = self._reverb_signature(
            reverb, self._request_settings, self._options
        )
        if reverb is None or float(self._request_settings.reverb_mix) <= 0.0:
            with self._reverb_ir_lock:
                self._reverb_kernel_signature = signature
                self._reverb_kernel_edc = {
                    "available": False,
                    "measured": False,
                    "measuring": False,
                    "reason": "reverb_is_disabled",
                    "effects_rebuild_count": int(self.effect_rebuild_count),
                }
            return
        if signature == self._reverb_kernel_signature:
            return
        try:
            measured = _measure_reverb_edc_chunked(
                self._pedalboard, self._request_settings, self.sample_rate,
                self._cache_root, self._numpy,
                self._options.reverb_predelay_ms,
            )
            result = dict(measured)
            result.update({
                "available": True,
                "measured": True,
                "measuring": False,
                "method": "actual_pedalboard_reverb_impulse_response",
                "excitation": "unit_impulse",
                "measurement_scope": "configured_dsp_kernel_not_source_material",
                "effects_rebuild_count": int(self.effect_rebuild_count),
            })
        except Exception as error:
            result = {
                "available": False,
                "measured": False,
                "measuring": False,
                "reason": "%s: %s" % (type(error).__name__, error),
                "method": "actual_pedalboard_reverb_impulse_response",
                "excitation": "unit_impulse",
                "measurement_scope": "configured_dsp_kernel_not_source_material",
                "effects_rebuild_count": int(self.effect_rebuild_count),
            }
        with self._reverb_ir_lock:
            self._reverb_kernel_signature = signature
            self._reverb_kernel_cache[signature] = dict(result)
            self._reverb_kernel_edc = result

    def _build_effects(self, settings, options) -> _BuiltEffects:
        warnings: list[str] = []
        try:
            eq_chain = audition._make_eq_chain(
                self._pedalboard, settings, options, self.sample_rate, warnings,
            )
            compressor = audition._make_compressor(
                self._pedalboard, settings, warnings,
            )
            reverb = audition._make_reverb(self._pedalboard, settings)
        except Exception as error:
            raise backend.MixdownError(
                "无法创建 PRE-LIMITER 人声处理链。"
            ) from error
        reverb_stage = None
        if reverb is not None:
            delay_frames = backend._predelay_frames(
                options.reverb_predelay_ms, self.sample_rate,
            )
            reverb_stage = _ExactDelayedProcessor(
                reverb, delay_frames, self.channels, self._numpy,
            )
        return _BuiltEffects(
            structure=self._effect_structure(options, settings),
            eq_chain=eq_chain,
            compressor=compressor,
            reverb=reverb,
            reverb_stage=reverb_stage,
            warnings=warnings,
        )

    def _rebuild_effects(self, built: _BuiltEffects | None = None, *,
                         measure_ir: bool = False,
                         reverb_edc: dict[str, Any] | None = None) -> None:
        if built is None:
            built = self._build_effects(self._request_settings, self._options)
        elif built.structure != self._effect_structure(
                self._options, self._request_settings):
            raise ValueError("候选 DSP 效果计划与当前参数不一致。")
        self._eq_chain = built.eq_chain
        self._compressor = built.compressor
        self._reverb = built.reverb
        self._reverb_stage = built.reverb_stage
        self._warnings = list(built.warnings)
        self._first = True
        self.effect_rebuild_count += 1
        if self._spectrum is None:
            self._spectrum = _LiveSpectrumAnalyzer(
                self.sample_rate, self.channels, self._numpy
            )
        signature = (
            bool(built.reverb is not None),
            str(self._request_settings.reverb_profile),
            float(self._options.reverb_predelay_ms),
            int(self.sample_rate),
        )
        if measure_ir:
            self._measure_reverb_kernel_now()
        elif signature == self._reverb_kernel_signature:
            pass
        elif reverb_edc is not None:
            result = dict(reverb_edc)
            result["effects_rebuild_count"] = int(self.effect_rebuild_count)
            with self._reverb_ir_lock:
                self._reverb_kernel_signature = signature
                self._reverb_kernel_cache[signature] = dict(result)
                self._reverb_kernel_edc = result
        elif signature != self._reverb_kernel_signature:
            with self._reverb_ir_lock:
                cached = self._reverb_kernel_cache.get(signature)
                if cached is not None:
                    result = dict(cached)
                    result["effects_rebuild_count"] = int(self.effect_rebuild_count)
                    self._reverb_kernel_signature = signature
                    self._reverb_kernel_edc = result
                else:
                    raise RuntimeError(
                        "结构参数缺少 control 线程生成的真实 IR 测量计划。"
                    )

    def _process_block(self, start: int, frames: int, *,
                       allow_terminal_padding: bool,
                       capture_telemetry: bool = True) -> Any:
        numpy = self._numpy
        settings = self._request_settings
        channels = self.channels
        vocal_audible, backing_audible = backend._audibility(settings)
        offsets = [
            int(round(track.offset_seconds * self.sample_rate))
            for track in settings.vocals
        ]
        vocal_gains = [
            10.0 ** (track.gain_db / 20.0) for track in settings.vocals
        ]
        vocal_pans = [audition._pan_gains(track.pan) for track in settings.vocals]
        backing_gain = 10.0 ** (settings.instrumental_db / 20.0)
        backing_pans = audition._pan_gains(settings.instrumental_pan)

        vocal_block = numpy.zeros((frames, channels), dtype=numpy.float32)
        send_block = numpy.zeros((frames, channels), dtype=numpy.float32)
        backing_block = numpy.zeros((frames, channels), dtype=numpy.float32)
        track_telemetry = ([
            {
                "role": "vocal",
                "index": index,
                "peak_dbfs": backend.SILENCE_DBFS,
                "rms_dbfs": backend.SILENCE_DBFS,
                "gr_db": 0.0,
                "audible": bool(audible),
                "frames": int(frames),
                "stage": "post_track_gain_pan_pre_vocal_fx",
            }
            for index, audible in enumerate(vocal_audible)
        ] if capture_telemetry else None)
        backing_telemetry = ({
            "role": "backing",
            "index": 0,
            "peak_dbfs": backend.SILENCE_DBFS,
            "rms_dbfs": backend.SILENCE_DBFS,
            "gr_db": 0.0,
            "audible": bool(backing_audible),
            "frames": int(frames),
            "stage": "post_track_gain_pan_pre_mix_bus",
        } if capture_telemetry else None)

        for track_index, (handle, source_frames, offset, gain, pan, audible, track) in enumerate(zip(
                self._handles[:-1], self._prepared.vocal_frames, offsets,
                vocal_gains, vocal_pans, vocal_audible, settings.vocals,
        )):
            if not audible:
                continue
            source_start = start - offset
            destination_start = max(0, -source_start)
            source_index = max(0, source_start)
            count = min(frames - destination_start, source_frames - source_index)
            if count <= 0:
                continue
            track_block = numpy.zeros((frames, channels), dtype=numpy.float32)
            track_block[destination_start:destination_start + count] = backend._read_frames(
                handle, source_index, count, channels, numpy,
            )
            track_block *= numpy.float32(gain)
            track_block[:, 0] *= numpy.float32(pan[0])
            track_block[:, 1] *= numpy.float32(pan[1])
            if track_telemetry is not None:
                track_telemetry[track_index].update(_meter(track_block, numpy))
            vocal_block = numpy.add(vocal_block, track_block, dtype=numpy.float32)
            send = float(track.reverb_send)
            if send > 0.0:
                send_block = numpy.add(
                    send_block, track_block * numpy.float32(send),
                    dtype=numpy.float32,
                )

        if backing_audible:
            count = min(frames, self._prepared.backing_frames - start)
            if count > 0:
                backing_block[:count] = backend._read_frames(
                    self._handles[-1], start, count, channels, numpy,
                )
                backing_block *= numpy.float32(backing_gain)
                backing_block[:, 0] *= numpy.float32(backing_pans[0])
                backing_block[:, 1] *= numpy.float32(backing_pans[1])
                if backing_telemetry is not None:
                    backing_telemetry.update(_meter(backing_block, numpy))

        vocal = vocal_block
        if self._eq_chain is not None:
            vocal = _process_exact(
                self._eq_chain, vocal, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
        after_eq = vocal if capture_telemetry else None
        compressor_metrics = None
        if capture_telemetry:
            compressor_metrics = {
                "enabled": self._compressor is not None,
                "gr_db": 0.0,
                "gr_peak_db": 0.0,
                "input_rms_dbfs": round(_rms_dbfs(after_eq, numpy), 4),
                "output_rms_dbfs": round(_rms_dbfs(after_eq, numpy), 4),
                "scope": "shared_vocal_bus",
                "method": "aligned_rms_db_difference",
            }
        if self._compressor is not None:
            before_compressor = vocal if capture_telemetry else None
            vocal = _process_exact(
                self._compressor, vocal, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
            if capture_telemetry:
                compressor_metrics.update(_gr_metrics(before_compressor, vocal, numpy))
                for row in track_telemetry:
                    row["gr_db"] = compressor_metrics["gr_db"]
        dry_before_reverb = vocal if capture_telemetry else None
        wet = None
        if self._reverb_stage is not None:
            wet = self._reverb_stage.process(
                send_block, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
            mix_value = float(settings.reverb_mix)
            vocal = (
                vocal * (1.0 - mix_value) + wet * mix_value
            ).astype(numpy.float32, copy=False)
        self._first = False
        if capture_telemetry:
            if self._spectrum is None:
                self._spectrum = _LiveSpectrumAnalyzer(
                    self.sample_rate, self.channels, numpy
                )
            self._spectrum.feed(start, vocal_block, after_eq)

        mixed = numpy.add(vocal, backing_block, dtype=numpy.float32)
        mixed = backend._apply_width(mixed, float(settings.width), numpy)
        if not numpy.isfinite(mixed).all():
            raise ValueError("PRE-LIMITER 混音产生了 NaN 或无穷大采样。")
        mixed = numpy.ascontiguousarray(mixed, dtype=numpy.float32)
        if not capture_telemetry:
            return mixed
        send_meter = _meter(send_block, numpy)
        wet_meter = _meter(wet if wet is not None else numpy.zeros_like(send_block), numpy)
        dry_meter = _meter(dry_before_reverb, numpy)
        mix_value = float(settings.reverb_mix)
        dry_power = (_power_from_dbfs(dry_meter["rms_dbfs"])
                     * (1.0 - mix_value) ** 2)
        wet_power = _power_from_dbfs(wet_meter["rms_dbfs"]) * mix_value ** 2
        wet_fraction = (
            wet_power / (dry_power + wet_power)
            if wet_power + dry_power > 0.0 else None
        )
        self._telemetry_records.append({
            "schema": TELEMETRY_BLOCK_SCHEMA,
            "source": TELEMETRY_SOURCE,
            "frame_start": int(start),
            "frame_end": int(start + frames),
            "frame_count": int(frames),
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
            "tracks": [*track_telemetry, backing_telemetry],
            "bus": {"pre_limiter": _meter(mixed, numpy)},
            "dynamics": {"compressor": compressor_metrics},
            "spectrum": self._spectrum.payload(),
            "reverb": {
                "enabled": self._reverb_stage is not None,
                "mix": float(settings.reverb_mix),
                "live": {
                    "send_meter": send_meter,
                    "wet_meter": wet_meter,
                    "dry_meter": dry_meter,
                    "send_peak_dbfs": send_meter["peak_dbfs"],
                    "send_rms_dbfs": send_meter["rms_dbfs"],
                    "measured_wet_fraction": (
                        round(wet_fraction, 6) if wet_fraction is not None else None
                    ),
                    "level_ratio_method": "reverb_blend_energy_fraction",
                    "wet_peak_dbfs": wet_meter["peak_dbfs"],
                    "wet_rms_dbfs": wet_meter["rms_dbfs"],
                    "dry_rms_dbfs": dry_meter["rms_dbfs"],
                    "wet_to_dry_db": (
                        round(wet_meter["rms_dbfs"] - dry_meter["rms_dbfs"], 4)
                        if wet_meter["rms_dbfs"] > backend.SILENCE_DBFS
                        and dry_meter["rms_dbfs"] > backend.SILENCE_DBFS else None
                    ),
                    "method": "aligned_processed_wet_return",
                    "source": "actual_reverb_dsp_output_and_dry_input",
                },
                "kernel_edc": self.reverb_measurement(),
            },
        })
        return mixed

    def _render_locked(self, frames: int, *,
                       capture_telemetry: bool = True) -> Any:
        output = self._numpy.zeros(
            (frames, self.channels), dtype=self._numpy.float32,
        )
        start = int(self.position_frame)
        process_frames = min(frames, max(0, self.duration_frames - start))
        if process_frames > 0:
            terminal = start + process_frames >= self.duration_frames
            output[:process_frames] = self._process_block(
                start, process_frames,
                allow_terminal_padding=terminal,
                capture_telemetry=capture_telemetry,
            )
        self.position_frame = start + frames
        return self._numpy.ascontiguousarray(output, dtype=self._numpy.float32)

    def _render_validated(self, frames: int, capture_telemetry: bool) -> Any:
        if isinstance(frames, bool) or not isinstance(frames, int):
            raise TypeError("frames 必须是整数。")
        if frames < 1:
            raise ValueError("frames 必须至少为 1。")
        with self._lock:
            self._ensure_open()
            start = int(self.position_frame)
            remaining = max(0, int(self.duration_frames) - start)
            if (0 < frames < MIN_STATEFUL_BLOCK_FRAMES
                    and frames < remaining):
                raise ValueError(
                    "连续状态渲染块必须至少 "
                    + str(MIN_STATEFUL_BLOCK_FRAMES)
                    + " 帧；1–3 帧只允许用于触及 duration_frames 的终块。"
                )
            return self._render_locked(
                frames, capture_telemetry=capture_telemetry
            )

    def render(self, frames: int) -> Any:
        """Render exactly ``frames`` contiguous stereo float32 samples.

        Continuous stateful blocks require at least four frames so Pedalboard
        cannot misinfer ``(2, 1)`` or ``(2, 2)`` as mono.  A 1–3 frame final
        remainder is accepted only when the block reaches ``duration_frames``;
        later calls return zeros and advance no processor state.  The samples
        remain pre-limiter and can exceed the final export ceiling; the pump
        owns safe-ceiling rejection.
        """
        return self._render_validated(frames, True)

    def render_without_telemetry(self, frames: int) -> Any:
        """Advance DSP state for seek/warmup without publishing display taps."""
        return self._render_validated(frames, False)

    def reverb_measurement(self) -> dict[str, Any]:
        with self._reverb_ir_lock:
            return dict(self._reverb_kernel_edc)

    def telemetry_for_range(self, frame_start: int, frame_end: int) -> dict[str, Any] | None:
        """Return actual DSP taps fully covering a produced media-frame range."""
        start, end = int(frame_start), int(frame_end)
        if end <= start:
            return None
        with self._lock:
            self._ensure_open()
            records = []
            for record in reversed(self._telemetry_records):
                record_start = int(record["frame_start"])
                record_end = int(record["frame_end"])
                if record_end <= start:
                    break
                if record_start < end:
                    records.append(record)
            if not records:
                return None
            records.reverse()
            return merge_telemetry_blocks(records)

    def _default_preroll_frames(self, target: int) -> int:
        has_reverb_send = any(
            audible and track.reverb_send > 0.0
            for track, audible in zip(
                self._request_settings.vocals,
                backend._audibility(self._request_settings)[0],
            )
        )
        seconds = min(
            REALTIME_DEFAULT_WARMUP_MAX_SECONDS,
            audition._audition_preroll_seconds(
                self._request_settings, self.sample_rate, has_reverb_send,
            ),
        )
        return min(target, max(0, int(math.ceil(seconds * self.sample_rate))))

    def _warm_to(self, target: int, requested_preroll: int | None,
                 reason: str) -> None:
        if requested_preroll is None:
            warmup_frames = self._default_preroll_frames(target)
            bounded = warmup_frames < target
            strategy = "bounded_eq_compressor_predelay_reverb_history"
        else:
            if isinstance(requested_preroll, bool) or not isinstance(requested_preroll, int):
                raise TypeError("preroll_frames 必须是整数或 None。")
            if requested_preroll < 0:
                raise ValueError("preroll_frames 不能为负数。")
            warmup_frames = min(target, requested_preroll)
            bounded = warmup_frames < target
            strategy = "explicit_preroll"
        skipped_short = False
        if (0 < warmup_frames < MIN_STATEFUL_BLOCK_FRAMES
                and target < self.duration_frames):
            # A non-terminal history shorter than four frames cannot identify
            # two channels safely.  Start with a fresh graph instead of feeding
            # hidden padding into FX state.
            warmup_frames = 0
            bounded = True
            skipped_short = True
            strategy = "skipped_history_below_safe_stateful_quantum"
        warmup_start = max(0, target - warmup_frames)
        self.position_frame = warmup_start
        remaining = target - warmup_start
        while remaining > 0:
            count = min(backend.CHUNK_FRAMES, remaining)
            tail = remaining - count
            if 0 < tail < MIN_STATEFUL_BLOCK_FRAMES:
                count -= MIN_STATEFUL_BLOCK_FRAMES - tail
            self._render_locked(count, capture_telemetry=False)
            remaining -= count
        self.position_frame = target
        self.last_warmup = {
            "reason": reason,
            "requested_frame": int(target),
            "warmup_start_frame": int(warmup_start),
            "warmup_frames": int(warmup_frames),
            "bounded": bool(bounded),
            "strategy": strategy,
            "skipped_below_safe_quantum": bool(skipped_short),
        }

    def seek(self, frame: int, preroll_frames: int | None = None) -> None:
        """Reset effects, seek sources, and synchronously warm bounded history."""
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise TypeError("frame 必须是整数。")
        if frame < 0:
            raise ValueError("frame 不能为负数。")
        with self._lock:
            self._ensure_open()
            self._telemetry_records.clear()
            if self._spectrum is not None:
                self._spectrum.reset()
            self._rebuild_effects()
            self._warm_to(frame, preroll_frames, "seek")

    def set_parameters(self, request: Mapping[str, Any]) -> None:
        """Apply validated parameters without rendering an intermediate file.

        Gain, pan, reverb send, mute/solo and width change only future block
        scalars.  A structural EQ/compressor/reverb/pre-delay change rebuilds
        that graph and warms bounded source history at the current position.
        The source paths, order, content fingerprint, sample rate, and stereo
        channel layout are immutable for the lifetime of the session.
        """
        self.apply_parameters(self.prepare_parameters(request))

    def prepare_parameters(self, request: Mapping[str, Any]) -> _PreparedParameters:
        """Validate on a control thread while the DSP worker keeps supplying audio.

        Source cache identity is immutable. The same canonical request parser
        and source/decoder fingerprint checks are used by the synchronous API;
        no path normalization, stat or decoder query is repeated at apply time.
        """
        with self._lock:
            self._ensure_open()
            source_paths, token = self._source_paths, self.source_token
            channels, ffmpeg = self.channels, self._ffmpeg
            owner = self._parameter_owner
        settings = backend._settings(request)
        options = audition._v3_options(request, settings)
        identity = self._source_identity(settings)
        if identity != source_paths:
            raise ValueError(
                "PreLimiterMixSession 的源路径或轨道顺序不可在会话中改变。"
            )
        rows = audition._source_rows(settings)
        source_token = audition._cache_token(
            rows, channels, audition._decoder_identity(ffmpeg))
        if source_token != token:
            raise ValueError(
                "PreLimiterMixSession 只接受同一源文件状态、顺序、"
                "解码器、采样率与双声道布局。"
            )
        structure = self._effect_structure(options, settings)
        with self._lock:
            rebuild = structure != self._effects_signature
        built = self._build_effects(settings, options) if rebuild else None
        reverb_edc = (
            self._prepare_reverb_edc(built, settings, options)
            if built is not None else None
        )
        return _PreparedParameters(
            owner, settings, options, effects=built,
            effects_structure=structure, reverb_edc=reverb_edc,
        )

    def apply_parameters(self, prepared: _PreparedParameters) -> None:
        """Apply an already validated, session-owned plan at a block boundary."""
        with self._lock:
            self._ensure_open()
            if not isinstance(prepared, _PreparedParameters) or prepared.owner is not self._parameter_owner:
                raise ValueError('参数计划不属于当前 PRE-LIMITER 会话。')
            settings, options = prepared.settings, prepared.options
            previous_position = int(self.position_frame)
            self._request_settings = settings
            self._options = options
            self.source_end_frame = self._source_end(settings)
            self.duration_frames = self._duration(settings)
            structure = self._effect_structure(options, settings)
            if structure != self._effects_signature:
                built = prepared.effects
                if built is None or built.structure != structure:
                    raise ValueError(
                        "候选 DSP 计划与当前结构不一致，请重新准备参数。"
                    )
                self._effects_signature = structure
                self._telemetry_records.clear()
                if self._spectrum is not None:
                    self._spectrum.reset()
                self._rebuild_effects(built, reverb_edc=prepared.reverb_edc)
                self._warm_to(
                    previous_position, None,
                    "structural_parameter_change",
                )

    def snapshot(self) -> dict[str, Any]:
        """Return control-path state for orchestration and diagnostics."""
        with self._lock:
            self._ensure_open()
            return {
                "schema": OUTPUT_SCHEMA,
                "stage": OUTPUT_STAGE,
                "sample_rate": int(self.sample_rate),
                "channels": int(self.channels),
                "mono_upmix_dbfs": MONO_UPNMIX_DBFS,
                "mono_upmix_gain": MONO_UPNMIX_GAIN,
                "mono_routing": MONO_ROUTING_NOTE,
                "source_duration_frames": int(self.source_end_frame),
                "duration_frames": int(self.duration_frames),
                "tail_frames": int(
                    self.duration_frames - self.source_end_frame
                ),
                "tail_policy": (
                    "source_end_plus_quantized_predelay_and_reverb_budget"
                    if self.duration_frames > self.source_end_frame
                    else "source_end_without_reverb_tail"
                ),
                "position_frame": int(self.position_frame),
                "min_continuous_block_frames": MIN_STATEFUL_BLOCK_FRAMES,
                "terminal_short_policy": (
                    "accept_1_to_3_frames_only_when_reaching_duration"
                ),
                "effect_rebuild_count": int(self.effect_rebuild_count),
                "telemetry_schema": TELEMETRY_SCHEMA,
                "telemetry_history_blocks": TELEMETRY_HISTORY_BLOCKS,
                "telemetry_spectrum_interval_ms": TELEMETRY_SPECTRUM_INTERVAL_MS,
                "reverb_kernel_edc": self.reverb_measurement(),
                "last_warmup": dict(self.last_warmup),
                "warnings": list(self._warnings),
                "cache_hit": bool(self._prepared.cache_hit),
                "cache_source": str(self._prepared.cache_source),
                "source_token": self.source_token,
                "ffmpeg_path": backend._normal_path(self._ffmpeg),
                "source_fingerprints": [
                    {
                        "device": int(value[0]),
                        "inode": int(value[1]),
                        "bytes": int(value[2]),
                        "mtime_ns": int(value[3]),
                    }
                    for value in self._source_fingerprints
                ],
            }

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("PreLimiterMixSession 已关闭。")

    def _close_handles(self) -> None:
        for handle in self._handles:
            try:
                handle.close()
            except Exception:
                pass
        self._handles = []

    def close(self) -> None:
        """Release decoded handles; safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._close_handles()
            self._eq_chain = None
            self._compressor = None
            self._reverb = None
            self._reverb_stage = None
            self._telemetry_records.clear()
            self._spectrum = None
            self._closed = True


def pre_limiter_renderer(request: Mapping[str, Any], cache_dir: str | Path,
                         numpy: Any = None, soundfile: Any = None,
                         pedalboard: Any = None,
                         ffmpeg: str | Path | None = None
                         ) -> PreLimiterMixSession:
    """Convenience factory with the same explicit injection contract."""
    return PreLimiterMixSession(
        request, cache_dir, numpy=numpy, soundfile=soundfile,
        pedalboard=pedalboard, ffmpeg=ffmpeg,
    )
