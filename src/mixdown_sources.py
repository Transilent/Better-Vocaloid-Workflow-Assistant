"""Decoded source cache and shared DSP construction for continuous mixing."""
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
import mixdown_contract as contract


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


__all__ = []  # Internal source preparation and DSP helpers only; no render service.
