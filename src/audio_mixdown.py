"""Offline vocal/instrumental mixdown with deterministic, editable-safe output.

The public entry point is :func:`render`. Audio is decoded to temporary
streaming float32 files with the application FFmpeg binary, processed in
bounded blocks with Pedalboard 0.9.26, and published as a new PCM24 WAV.

The backing sample rate is the project rate. Mono+mono produces mono; any
stereo source produces stereo. One to three vocals are shifted, gained and
summed before the shared vocal effects process their bus. Positive per-vocal
offsets delay a vocal, while negative offsets trim its head without seeking
processor state. Preview requests rerun the same chain from sample zero and
select the requested sample interval from the resulting full-program waveform.
Sample peaks are protected at -1 dBFS; this is not a loudness-normalization or
true-peak calibrated stage.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping
import unicodedata


PEDALBOARD_VERSION = "0.9.26"
CHUNK_FRAMES = 32768
PEDALBOARD_BUFFER_SIZE = 8192
PEAK_CEILING_DBFS = -1.0
PEAK_CEILING = 10.0 ** (PEAK_CEILING_DBFS / 20.0)
TAIL_FLOOR_DBFS = -90.0
# Reverb decay budget after the separately quantized pre-delay.
MAX_REVERB_TAIL_SECONDS = 8.0
TAIL_MARGIN_SECONDS = 0.12
LIMITER_GUARD_SECONDS = 0.25
MAX_SOURCE_SECONDS = 6 * 60 * 60.0
MAX_OFFSET_SECONDS = 6 * 60 * 60.0
MAX_OUTPUT_BYTES = 16 * 1024 ** 3
MAX_VOCALS = 3
SILENCE_DBFS = -144.0
REPORT_SCHEMA = "vwa.audio_mixdown.render/v3"
REPORT_SCHEMA_V2 = "vwa.audio_mixdown.render/v2"
EQ_BANDS_MAX = 4
EQ_BAND_TYPES = frozenset(("highpass", "lowshelf", "bell", "highshelf"))


class MixdownError(RuntimeError):
    """A handled mixdown failure suitable for display to the user."""


class MixdownCancelled(InterruptedError):
    """Raised when a caller-provided cancellation callback requests a stop."""


@dataclass(frozen=True)
class _AudioInfo:
    path: Path
    sample_rate: int
    channels: int
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / float(self.sample_rate)


@dataclass(frozen=True)
class _EqBand:
    kind: str
    freq_hz: float
    gain_db: float
    q: float
    enabled: bool


@dataclass(frozen=True)
class _VocalTrack:
    path: Path
    gain_db: float
    offset_seconds: float
    mute: bool = False
    solo: bool = False
    pan: float = 0.0
    reverb_send: float = 1.0


@dataclass(frozen=True)
class _Settings:
    vocals: tuple[_VocalTrack, ...]
    instrumental_path: Path
    output_path: Path
    instrumental_db: float
    instrumental_mute: bool
    instrumental_solo: bool
    instrumental_pan: float
    eq_low_cut_hz: float
    eq_presence_db: float
    compression_strength: float
    reverb_mix: float
    reverb_profile: str
    preview_start_seconds: float | None
    preview_duration_seconds: float | None
    eq_bands: tuple[_EqBand, ...] | None = None
    reverb_predelay_ms: float = 0.0
    width: float = 1.0
    uses_v3: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def has_preview(self) -> bool:
        return (self.preview_start_seconds is not None
                or self.preview_duration_seconds is not None)


def _check_cancel(cancel: Callable[[], Any] | None) -> None:
    if cancel is None:
        return
    requested = cancel()
    if requested:
        raise MixdownCancelled("混音已取消。")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    try:
        from process_control import terminate_tree
        terminate_tree(process)
    except Exception:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _run_ffmpeg(argv: list[str], log_path: Path, cancel: Callable[[], Any] | None,
                failure_message: str, *, timeout: float | None = None,
                allow_failure: bool = False) -> tuple[int, str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + timeout if timeout is not None else None
            while True:
                try:
                    return_code = process.wait(timeout=0.10)
                    break
                except subprocess.TimeoutExpired:
                    try:
                        _check_cancel(cancel)
                    except BaseException:
                        _terminate_process(process)
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        _terminate_process(process)
                        raise MixdownError(failure_message + " FFmpeg 处理超时。") from None
    except OSError as error:
        raise MixdownError(failure_message + f" 无法启动 FFmpeg：{error}") from error

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if return_code and not allow_failure:
        detail = text.strip()
        if len(detail) > 2500:
            detail = detail[-2500:]
        suffix = f" FFmpeg：{detail}" if detail else f" FFmpeg 返回代码 {return_code}。"
        raise MixdownError(failure_message + suffix)
    return return_code, text


def _ffmpeg_executable() -> Path:
    candidates: list[Path] = []

    # Prefer the application's configured tool.  Importing common can fail when
    # this module is run directly from src/, so also inspect both supported
    # config.json locations without relying on the current working directory.
    try:
        from common import load_config
        candidates.append(Path(load_config()["ffmpeg"]))
    except Exception:
        pass

    source_root = Path(__file__).resolve().parent
    for root in (source_root, source_root.parent):
        config_path = root / "config.json"
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            value = data.get("ffmpeg")
            if value:
                candidate = Path(str(value)).expanduser()
                candidates.append(candidate if candidate.is_absolute() else root / candidate)
        except (OSError, ValueError, TypeError):
            pass

    executable = shutil.which("ffmpeg")
    if executable:
        candidates.append(Path(executable))

    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate.is_file():
            return candidate
    raise MixdownError("找不到应用配置的 FFmpeg，请先检查 VWA 的运行时依赖。")


def _load_pedalboard() -> Any:
    try:
        import pedalboard
    except ImportError as error:
        raise MixdownError(
            f"简易混音需要 Pedalboard {PEDALBOARD_VERSION}，当前 Python 环境未安装该依赖。"
        ) from error
    version = str(getattr(pedalboard, "__version__", ""))
    if version != PEDALBOARD_VERSION:
        shown = version or "未知"
        raise MixdownError(
            f"简易混音固定使用 Pedalboard {PEDALBOARD_VERSION}，当前版本为 {shown}。"
        )
    required = ("Pedalboard", "HighpassFilter", "PeakFilter", "Compressor", "Reverb")
    missing = [name for name in required if not hasattr(pedalboard, name)]
    if missing:
        raise MixdownError("Pedalboard 缺少简易混音所需组件：" + "、".join(missing) + "。")
    return pedalboard


def _finite_number(request: Mapping[str, Any], name: str, minimum: float,
                   maximum: float, *, label: str | None = None) -> float:
    shown = label or name
    if name not in request:
        raise ValueError(f"混音参数缺少 {shown}。")
    value = request[name]
    if isinstance(value, bool):
        raise ValueError(f"{shown} 必须是有限数字。")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{shown} 必须是有限数字。") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{shown} 应在 {minimum:g} 到 {maximum:g} 之间，且不能是 NaN 或无穷大。")
    return number


def _boolean_value(request: Mapping[str, Any], name: str, default: bool = False,
                    *, label: str | None = None) -> bool:
    shown = label or name
    if name not in request:
        return default
    value = request[name]
    if type(value) is not bool:
        raise ValueError(f"{shown} 必须是布尔值。")
    return value


def _optional_number(request: Mapping[str, Any], name: str, default: float,
                     minimum: float, maximum: float, *,
                     label: str | None = None) -> float:
    shown = label or name
    if name not in request:
        return default
    value = request[name]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{shown} 必须是有限数字。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{shown} 必须是有限数字。") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(
            f"{shown} 应在 {minimum:g} 到 {maximum:g} 之间，且不能是 NaN 或无穷大。")
    return number


def _strict_number(request: Mapping[str, Any], name: str, minimum: float,
                   maximum: float, *, label: str | None = None) -> float:
    shown = label or name
    if name not in request:
        raise ValueError(f"混音参数缺少 {shown}。")
    value = request[name]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{shown} 必须是有限数字。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{shown} 必须是有限数字。") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(
            f"{shown} 应在 {minimum:g} 到 {maximum:g} 之间，且不能是 NaN 或无穷大。")
    return number


def _eq_bands_value(request: Mapping[str, Any]) -> tuple[_EqBand, ...] | None:
    if "eq_bands" not in request:
        return None
    raw = request["eq_bands"]
    if not isinstance(raw, list):
        raise ValueError("eq_bands 必须是包含 0–4 个 EQ 段对象的列表。")
    if len(raw) > EQ_BANDS_MAX:
        raise ValueError(f"eq_bands 最多包含 {EQ_BANDS_MAX} 段。")
    bands: list[_EqBand] = []
    for index, item in enumerate(raw):
        prefix = f"eq_bands[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{prefix} 必须是对象。")
        for field_name in ("type", "freq_hz", "gain_db", "q", "enabled"):
            if field_name not in item:
                raise ValueError(f"{prefix} 缺少 {field_name}。")
        kind = item["type"]
        if not isinstance(kind, str) or kind not in EQ_BAND_TYPES:
            raise ValueError(
                f"{prefix}.type 必须是 highpass、lowshelf、bell 或 highshelf。")
        frequency = _strict_number(
            item, "freq_hz", 10.0, 20000.0, label=f"{prefix}.freq_hz")
        gain = _strict_number(
            item, "gain_db", -24.0, 24.0, label=f"{prefix}.gain_db")
        q = _strict_number(item, "q", 0.1, 18.0, label=f"{prefix}.q")
        enabled = _boolean_value(item, "enabled", label=f"{prefix}.enabled")
        bands.append(_EqBand(kind, frequency, gain, q, enabled))
    return tuple(bands)


def _uses_v3(request: Mapping[str, Any]) -> bool:
    if any(name in request for name in (
            "eq_bands", "reverb_predelay_ms", "width", "instrumental_mute",
            "instrumental_solo", "instrumental_pan")):
        return True
    vocals_raw = request.get("vocals")
    if isinstance(vocals_raw, list):
        return any(
            isinstance(item, Mapping)
            and any(name in item for name in ("mute", "solo", "pan", "reverb_send"))
            for item in vocals_raw
        )
    return False


def _optional_seconds(request: Mapping[str, Any], name: str, *, positive: bool) -> float | None:
    value = request.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是有限秒数。")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是有限秒数。") from None
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        relation = "大于 0" if positive else "不小于 0"
        raise ValueError(f"{name} 必须是{relation}的有限秒数。")
    return number


def _path_value(request: Mapping[str, Any], name: str, *,
                label: str | None = None) -> Path:
    shown = label or name
    value = request.get(name)
    if value is None or isinstance(value, bool):
        raise ValueError(f"混音参数缺少有效路径：{shown}。")
    try:
        raw = os.fspath(value)
    except TypeError:
        raise ValueError(f"混音参数缺少有效路径：{shown}。") from None
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError(f"混音参数缺少有效路径：{shown}。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve(strict=False)
    except OSError as error:
        raise ValueError(f"混音路径无效：{path}。") from error


def _normal_path(path: Path) -> str:
    value = os.path.normcase(os.path.abspath(os.fspath(path)))
    return unicodedata.normalize("NFC", value)


def _vocal_tracks(request: Mapping[str, Any]) -> tuple[_VocalTrack, ...]:
    legacy_names = ("vocal_path", "vocal_db", "offset_seconds")
    if "vocals" in request:
        if any(name in request for name in legacy_names):
            raise ValueError(
                "vocals 不能与旧版 vocal_path、vocal_db 或 offset_seconds 同时提供。"
            )
        value = request["vocals"]
        if not isinstance(value, list):
            raise ValueError("vocals 必须是包含 1–3 个歌声轨对象的列表。")
        if not 1 <= len(value) <= MAX_VOCALS:
            raise ValueError(f"vocals 必须包含 1–{MAX_VOCALS} 个歌声轨。")
        tracks = []
        for index, item in enumerate(value):
            prefix = f"vocals[{index}]"
            if not isinstance(item, Mapping):
                raise ValueError(f"{prefix} 必须是对象。")
            tracks.append(_VocalTrack(
                path=_path_value(item, "path", label=f"{prefix}.path"),
                gain_db=_finite_number(
                    item, "gain_db", -120.0, 24.0, label=f"{prefix}.gain_db"
                ),
                offset_seconds=_finite_number(
                    item, "offset_seconds", -MAX_OFFSET_SECONDS, MAX_OFFSET_SECONDS,
                    label=f"{prefix}.offset_seconds",
                ),
                mute=_boolean_value(item, "mute", False, label=f"{prefix}.mute"),
                solo=_boolean_value(item, "solo", False, label=f"{prefix}.solo"),
                pan=_optional_number(
                    item, "pan", 0.0, -1.0, 1.0, label=f"{prefix}.pan"
                ),
                reverb_send=_optional_number(
                    item, "reverb_send", 1.0, 0.0, 1.0,
                    label=f"{prefix}.reverb_send",
                ),
            ))
        return tuple(tracks)

    return (_VocalTrack(
        path=_path_value(request, "vocal_path"),
        gain_db=_finite_number(request, "vocal_db", -120.0, 24.0),
        offset_seconds=_finite_number(
            request, "offset_seconds", -MAX_OFFSET_SECONDS, MAX_OFFSET_SECONDS
        ),
    ),)


def _settings(request: Mapping[str, Any]) -> _Settings:
    if not isinstance(request, Mapping):
        raise TypeError("render 的 request 必须是字典。")

    vocals = _vocal_tracks(request)
    instrumental_path = _path_value(request, "instrumental_path")
    output_path = _path_value(request, "output_path")

    if output_path.suffix.lower() != ".wav":
        raise ValueError("混音输出必须使用 .wav 文件名。")
    sources = [
        (track.path, f"歌声轨 {index + 1}")
        for index, track in enumerate(vocals)
    ]
    sources.append((instrumental_path, "伴奏"))
    for source, label in sources:
        if not source.is_file():
            raise FileNotFoundError(f"找不到{label}文件：{source}")
        if _normal_path(output_path) == _normal_path(source):
            raise ValueError("混音输出不能与任一人声或伴奏源文件重叠。")
    if os.path.lexists(output_path):
        raise FileExistsError(f"输出文件已经存在，为避免覆盖已拒绝：{output_path}")

    instrumental_db = _finite_number(request, "instrumental_db", -120.0, 24.0)
    instrumental_mute = _boolean_value(request, "instrumental_mute", False)
    instrumental_solo = _boolean_value(request, "instrumental_solo", False)
    instrumental_pan = _optional_number(
        request, "instrumental_pan", 0.0, -1.0, 1.0
    )
    eq_bands = _eq_bands_value(request)
    if eq_bands is None:
        low_cut = _finite_number(request, "eq_low_cut_hz", 0.0, 20000.0)
        presence = _finite_number(request, "eq_presence_db", -24.0, 24.0)
    else:
        # The old two-parameter EQ remains accepted and validated when supplied,
        # but eq_bands is the authoritative chain once present.
        low_cut = _optional_number(request, "eq_low_cut_hz", 0.0, 0.0, 20000.0)
        presence = _optional_number(request, "eq_presence_db", 0.0, -24.0, 24.0)
    if 0.0 < low_cut < 10.0:
        raise ValueError("eq_low_cut_hz 必须为 0（旁通）或不小于 10 Hz。")
    compression = _finite_number(request, "compression_strength", 0.0, 1.0)
    reverb = _finite_number(request, "reverb_mix", 0.0, 1.0)
    reverb_predelay = _optional_number(
        request, "reverb_predelay_ms", 0.0, 0.0, 120.0)
    width = _optional_number(request, "width", 1.0, 0.0, 2.0)
    reverb_profile = request.get("reverb_profile", "natural")
    if reverb_profile not in ("small", "natural", "wide"):
        raise ValueError("混响空间类型无效。")
    preview_start = _optional_seconds(request, "preview_start_seconds", positive=False)
    preview_duration = _optional_seconds(request, "preview_duration_seconds", positive=True)
    uses_v3 = _uses_v3(request)

    return _Settings(
        vocals=vocals,
        instrumental_path=instrumental_path,
        output_path=output_path,
        instrumental_db=instrumental_db,
        instrumental_mute=instrumental_mute,
        instrumental_solo=instrumental_solo,
        instrumental_pan=instrumental_pan,
        eq_low_cut_hz=low_cut,
        eq_presence_db=presence,
        compression_strength=compression,
        reverb_mix=reverb,
        reverb_profile=reverb_profile,
        preview_start_seconds=preview_start,
        preview_duration_seconds=preview_duration,
        eq_bands=eq_bands,
        reverb_predelay_ms=reverb_predelay,
        width=width,
        uses_v3=uses_v3,
    )


def _valid_audio_info(info: _AudioInfo) -> _AudioInfo:
    if info.sample_rate < 8000 or info.sample_rate > 384000:
        raise ValueError(f"音频采样率 {info.sample_rate} Hz 不受支持（允许 8000–384000 Hz）。")
    if info.channels not in (1, 2):
        raise ValueError(f"简易混音仅支持单声道或双声道音频，当前为 {info.channels} 声道。")
    if info.frames <= 0:
        raise ValueError("音频没有可解码的采样。")
    if info.duration > MAX_SOURCE_SECONDS:
        raise ValueError("音频超过 6 小时资源限制，已安全拒绝。")
    return info


def _probe_with_ffmpeg(path: Path, ffmpeg: Path, log_path: Path,
                       cancel: Callable[[], Any] | None) -> _AudioInfo:
    _, text = _run_ffmpeg(
        [str(ffmpeg), "-nostdin", "-hide_banner", "-i", str(path)],
        log_path,
        cancel,
        f"无法读取音频信息：{path.name}。",
        timeout=30.0,
        allow_failure=True,
    )
    audio_lines = [line for line in text.splitlines() if "Audio:" in line]
    if not audio_lines:
        raise ValueError(f"文件中没有可用音频流：{path.name}。")
    audio_line = audio_lines[0]
    rate_match = re.search(r"(\d+)\s*Hz", audio_line, re.IGNORECASE)
    if not rate_match:
        raise ValueError(f"无法确认音频采样率：{path.name}。")
    sample_rate = int(rate_match.group(1))
    if re.search(r"\bmono\b", audio_line, re.IGNORECASE):
        channels = 1
    elif re.search(r"\bstereo\b", audio_line, re.IGNORECASE):
        channels = 2
    else:
        channel_match = re.search(r"(\d+)\s+channels", audio_line, re.IGNORECASE)
        if not channel_match:
            raise ValueError(f"无法确认音频声道数：{path.name}。")
        channels = int(channel_match.group(1))

    duration = None
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    frames = round(duration * sample_rate) if duration is not None else 0
    return _valid_audio_info(_AudioInfo(path, sample_rate, channels, frames))


def _probe_audio(path: Path, ffmpeg: Path, log_path: Path,
                 cancel: Callable[[], Any] | None) -> _AudioInfo:
    _check_cancel(cancel)
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return _valid_audio_info(_AudioInfo(
            path=path,
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
            frames=int(info.frames),
        ))
    except (ImportError, RuntimeError, OSError, ValueError):
        # Some containers are decoded by FFmpeg but not by libsndfile.  The
        # fallback still obtains the values needed to plan the render.
        return _probe_with_ffmpeg(path, ffmpeg, log_path, cancel)


def _decode_to_float_wav(source: Path, target: Path, sample_rate: int, channels: int,
                         ffmpeg: Path, log_path: Path,
                         cancel: Callable[[], Any] | None) -> _AudioInfo:
    _check_cancel(cancel)
    _run_ffmpeg(
        [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-threads", "1", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "pcm_f32le",
            "-rf64", "auto", str(target),
        ],
        log_path,
        cancel,
        f"解码失败：{source.name}。",
    )
    try:
        import soundfile as sf
        info = sf.info(str(target))
    except Exception as error:
        raise MixdownError(f"无法读取 FFmpeg 解码结果：{source.name}。") from error
    if int(info.samplerate) != sample_rate or int(info.channels) != channels:
        raise MixdownError("解码结果与目标采样率或声道数不一致，已停止混音。")
    return _valid_audio_info(_AudioInfo(target, sample_rate, channels, int(info.frames)))


def _read_frames(handle: Any, start: int, count: int, channels: int, numpy: Any) -> Any:
    if count <= 0:
        return numpy.empty((0, channels), dtype=numpy.float32)
    handle.seek(start)
    data = handle.read(count, dtype="float32", always_2d=True)
    array = numpy.asarray(data, dtype=numpy.float32)
    if array.ndim != 2 or array.shape != (count, channels):
        raise MixdownError("解码音频帧数不足，文件可能不完整。")
    if not numpy.isfinite(array).all():
        raise ValueError("音频包含 NaN 或无穷大采样，已停止输出。")
    return numpy.ascontiguousarray(array)


def _pedalboard_process(plugin: Any, block: Any, sample_rate: int, first: bool,
                        numpy: Any) -> Any:
    """Process an (frames, channels) block while preserving plugin state."""

    frames, channels = block.shape
    # Pedalboard infers channels from the smaller array dimension.  The small
    # zero pad removes ambiguity for a final one- or two-frame block.
    padded_frames = max(frames, 32, channels * 2)
    source = numpy.ascontiguousarray(block.T, dtype=numpy.float32)
    if padded_frames > frames:
        padded = numpy.zeros((channels, padded_frames), dtype=numpy.float32)
        padded[:, :frames] = source
        source = padded
    output = plugin.process(
        source,
        sample_rate,
        buffer_size=PEDALBOARD_BUFFER_SIZE,
        reset=first,
    )
    output = numpy.asarray(output, dtype=numpy.float32)
    if output.ndim != 2 or output.shape[0] != channels or output.shape[1] < frames:
        raise MixdownError("Pedalboard 返回的音频形状异常，已停止输出。")
    output = output[:, :frames].T
    if not numpy.isfinite(output).all():
        raise ValueError("人声处理产生了 NaN 或无穷大采样，已停止输出。")
    return numpy.ascontiguousarray(output, dtype=numpy.float32)


def _make_vocal_processors(pedalboard: Any, settings: _Settings, sample_rate: int) -> tuple[Any | None, Any | None]:
    plugins = []
    nyquist = sample_rate / 2.0
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
    if settings.compression_strength > 0.0:
        strength = settings.compression_strength
        plugins.append(pedalboard.Compressor(
            threshold_db=-8.0 - 12.0 * strength,
            ratio=1.0 + 2.0 * strength,
            attack_ms=25.0 - 10.0 * strength,
            release_ms=120.0 + 80.0 * strength,
        ))
    dry_chain = pedalboard.Pedalboard(plugins) if plugins else None
    reverb = None
    if settings.reverb_mix > 0.0:
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
    return dry_chain, reverb


def _amplitude_to_dbfs(value: float) -> float:
    return (max(SILENCE_DBFS, 20.0 * math.log10(value))
            if value > 0.0 else SILENCE_DBFS)


def _rms(block: Any, numpy: Any) -> float:
    if not block.size:
        return 0.0
    return float(numpy.sqrt(numpy.mean(numpy.square(block, dtype=numpy.float64))))


def _gain_ratio_db(before: float, after: float) -> float:
    if before <= 1e-12:
        return 0.0
    if after <= 1e-12:
        return -30.0
    value = 20.0 * math.log10(after / before)
    return max(-30.0, min(0.0, float(value)))


def _apply_width(block: Any, width: float, numpy: Any) -> Any:
    """Apply mid/side width while leaving a centered signal unchanged."""
    if width == 1.0 or block.shape[1] < 2:
        return block
    mid = (block[:, 0] + block[:, 1]) * 0.5
    side = (block[:, 0] - block[:, 1]) * 0.5
    result = numpy.empty_like(block)
    result[:, 0] = mid + side * width
    result[:, 1] = mid - side * width
    return numpy.ascontiguousarray(result, dtype=numpy.float32)


def _delay_block(block: Any, delay_frames: int, state: Any, numpy: Any) -> tuple[Any, Any]:
    if delay_frames <= 0:
        return block, state
    if state is None:
        state = numpy.zeros((delay_frames, block.shape[1]), dtype=numpy.float32)
    combined = numpy.concatenate((state, block), axis=0)
    output = numpy.ascontiguousarray(combined[:block.shape[0]], dtype=numpy.float32)
    state = numpy.ascontiguousarray(combined[block.shape[0]:], dtype=numpy.float32)
    return output, state


def _predelay_frames(predelay_ms: float, sample_rate: int) -> int:
    """Quantize pre-delay once so audition and export share one sample grid."""
    frames = float(predelay_ms) * int(sample_rate) / 1000.0
    return max(0, int(math.floor(frames + 0.5)))


def _reverb_tail_budget_frames(predelay_ms: float, sample_rate: int) -> int:
    """Maximum render extension: post-delay decay plus its pre-delay."""
    return (int(round(MAX_REVERB_TAIL_SECONDS * sample_rate))
            + _predelay_frames(predelay_ms, sample_rate))


def _safe_warning(settings: _Settings, message: str) -> None:
    if message not in settings.warnings:
        settings.warnings.append(message)


def _make_eq_chain(pedalboard: Any, settings: _Settings, sample_rate: int) -> Any | None:
    plugins: list[Any] = []
    nyquist = sample_rate / 2.0
    if settings.eq_bands is None:
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
        return pedalboard.Pedalboard(plugins) if plugins else None

    max_frequency = min(20000.0, sample_rate * 0.45)
    highpass_default_q = 0.707
    for index, band in enumerate(settings.eq_bands):
        if not band.enabled:
            continue
        frequency = band.freq_hz
        if frequency > max_frequency:
            clamped = max(10.0, max_frequency)
            _safe_warning(
                settings,
                f"eq_bands[{index}].freq_hz {frequency:g} Hz was clamped to "
                f"{clamped:g} Hz for the {sample_rate} Hz output rate.",
            )
            frequency = clamped
        if band.kind == "highpass":
            if abs(band.gain_db) > 1e-12:
                _safe_warning(
                    settings,
                    f"eq_bands[{index}].gain_db is not used by the highpass "
                    "filter; the requested gain was not applied.",
                )
            if abs(band.q - highpass_default_q) > 1e-6:
                _safe_warning(
                    settings,
                    f"eq_bands[{index}].q is not adjustable for the highpass "
                    "filter; the fixed default Q was used.",
                )
            plugins.append(pedalboard.HighpassFilter(
                cutoff_frequency_hz=float(frequency)
            ))
            continue
        if band.kind == "bell":
            plugin_type = getattr(pedalboard, "PeakFilter", None)
        elif band.kind == "lowshelf":
            plugin_type = getattr(pedalboard, "LowShelfFilter", None)
        else:
            plugin_type = getattr(pedalboard, "HighShelfFilter", None)
        if plugin_type is None:
            _safe_warning(
                settings,
                f"eq_bands[{index}].type {band.kind} is unavailable in Pedalboard "
                "0.9.26; a PeakFilter approximation was used.",
            )
            plugin_type = getattr(pedalboard, "PeakFilter", None)
            if plugin_type is None:
                raise ValueError(
                    f"eq_bands[{index}] cannot be processed: no shelf or peak filter.")
        plugins.append(plugin_type(
            cutoff_frequency_hz=float(frequency),
            gain_db=float(band.gain_db),
            q=float(band.q),
        ))
    return pedalboard.Pedalboard(plugins) if plugins else None


def _make_compressor(pedalboard: Any, settings: _Settings) -> Any | None:
    if settings.compression_strength <= 0.0:
        return None
    strength = settings.compression_strength
    return pedalboard.Compressor(
        threshold_db=-8.0 - 12.0 * strength,
        ratio=1.0 + 2.0 * strength,
        attack_ms=25.0 - 10.0 * strength,
        release_ms=120.0 + 80.0 * strength,
    )


def _make_reverb(pedalboard: Any, settings: _Settings) -> Any | None:
    if settings.reverb_mix <= 0.0:
        return None
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


def _make_predelay(pedalboard: Any, settings: _Settings) -> None:
    """Use the frame-driven local delay in _render_mix_v3.

    Pedalboard 0.9.26 converts delay seconds through a float and truncates to
    frames internally.  That can land one frame before the shared half-up
    quantization at common 44.1 kHz settings, so both render paths use
    _delay_block instead.
    """
    return None


def _make_v3_processors(pedalboard: Any, settings: _Settings,
                        sample_rate: int) -> tuple[Any | None, Any | None, Any | None, Any | None]:
    eq_chain = _make_eq_chain(pedalboard, settings, sample_rate)
    compressor = _make_compressor(pedalboard, settings)
    reverb = _make_reverb(pedalboard, settings)
    predelay = _make_predelay(pedalboard, settings)
    return eq_chain, compressor, reverb, predelay


def _emit_progress(callback: Callable[[dict[str, Any]], Any] | None, *,
                   phase: str, frames_done: int, frames_total: int,
                   tracks: list[dict[str, Any]], bus_peak: float,
                   elapsed_ms: float) -> None:
    if callback is None:
        return
    total = max(1, int(frames_total))
    event = {
        "phase": str(phase),
        "fraction": max(0.0, min(1.0, float(frames_done) / total)),
        "frames_done": int(frames_done),
        "frames_total": int(frames_total),
        "tracks": tracks,
        "bus_peak": float(bus_peak),
        "elapsed_ms": float(elapsed_ms),
    }
    try:
        callback(event)
    except Exception:
        # Progress is observational. A UI/pipe failure must not abort a render.
        pass


def _source_fingerprint(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError as error:
        raise MixdownError(f"无法读取源音频状态：{path.name}。") from error
    return (
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _file_sha256(path: Path, cancel: Callable[[], Any] | None) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            _check_cancel(cancel)
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _install_new_file(staged: Path, destination: Path) -> None:
    """Atomically publish a completed file without replacing an existing path."""

    if os.path.lexists(destination):
        raise FileExistsError(f"输出文件已经存在，为避免覆盖已拒绝：{destination}")
    try:
        os.link(staged, destination)
    except FileExistsError:
        raise FileExistsError(f"输出文件已经被其他任务创建，已保留原文件：{destination}") from None
    except OSError as error:
        # Windows rename is an atomic MoveFileEx call without
        # MOVEFILE_REPLACE_EXISTING, so it also refuses a destination that
        # appeared after the initial check.  It supports filesystems that do
        # not implement hard links (for example some removable media).
        if os.name == "nt":
            try:
                os.rename(staged, destination)
                return
            except FileExistsError:
                raise FileExistsError(
                    f"输出文件已经被其他任务创建，已保留原文件：{destination}"
                ) from None
            except OSError as rename_error:
                raise MixdownError(
                    f"无法以原子方式发布输出文件：{destination}"
                ) from rename_error
        raise MixdownError(
            f"无法以原子方式发布输出文件，目标所在文件系统不支持安全的新文件提交：{destination}"
        ) from error
    try:
        staged.unlink()
    except OSError:
        # The destination is already a complete hard link; cleanup of the
        # temporary name must not turn a successful render into a failure.
        pass


def _audibility(settings: _Settings) -> tuple[list[bool], bool]:
    any_solo = (any(track.solo and not track.mute for track in settings.vocals)
                or (settings.instrumental_solo and not settings.instrumental_mute))
    vocal_audible = [
        not track.mute and (not any_solo or track.solo)
        for track in settings.vocals
    ]
    backing_audible = (
        not settings.instrumental_mute
        and (not any_solo or settings.instrumental_solo)
    )
    return vocal_audible, backing_audible


def _output_channels(settings: _Settings, source_channels: list[int]) -> int:
    """Choose the same bus layout for preview and final export."""
    vocal_audible, backing_audible = _audibility(settings)
    has_pan = ((backing_audible and settings.instrumental_pan != 0.0)
               or any(audible and track.pan != 0.0
                      for track, audible in zip(settings.vocals, vocal_audible)))
    force_stereo = settings.uses_v3 and settings.width != 1.0
    return 2 if max(source_channels) == 2 or has_pan or force_stereo else 1


def _render_mix(settings: _Settings, vocal_inputs: list[tuple[Path, int]],
                instrumental_path: Path, sample_rate: int, channels: int,
                instrumental_frames: int, mix_path: Path, pedalboard: Any,
                numpy: Any, soundfile: Any, cancel: Callable[[], Any] | None,
                progress: Callable[[dict[str, Any]], Any] | None = None
                ) -> tuple[int, int, int | None, int | None, list[float], float,
                           float, list[float], float, float]:
    if len(vocal_inputs) != len(settings.vocals):
        raise MixdownError("内部歌声轨数量不一致，已停止混音。")
    vocal_audible, backing_audible = _audibility(settings)
    has_audible_vocal = any(vocal_audible)

    def pan_gains(value):
        if channels < 2 or value == 0.0:
            return 1.0, 1.0
        if value < 0.0:
            return 1.0, 1.0 + value
        return 1.0 - value, 1.0

    offsets = [
        int(round(track.offset_seconds * sample_rate))
        for track in settings.vocals
    ]
    vocal_ends = [
        max(0, frames + offset)
        for (_, frames), offset in zip(vocal_inputs, offsets)
    ]
    base_frames = max([instrumental_frames, *vocal_ends])
    if base_frames <= 0:
        raise ValueError("伴奏和歌声轨均没有可混音内容。")

    tail_limit = (_reverb_tail_budget_frames(
                      settings.reverb_predelay_ms, sample_rate)
                  if settings.reverb_mix > 0 and has_audible_vocal else 0)
    maximum_frames = base_frames + tail_limit
    guard = int(round(LIMITER_GUARD_SECONDS * sample_rate))

    preview_start = None
    preview_duration = None
    if settings.has_preview:
        preview_start = int(round((settings.preview_start_seconds or 0.0) * sample_rate))
        if preview_start >= maximum_frames:
            raise ValueError("预览起点超过可渲染音频范围。")
        if settings.preview_duration_seconds is not None:
            preview_duration = max(1, int(round(settings.preview_duration_seconds * sample_rate)))

    if settings.has_preview:
        meter_start_frame = preview_start or 0
        meter_end_frame = (meter_start_frame + preview_duration
                           if preview_duration is not None else maximum_frames)
    else:
        meter_start_frame, meter_end_frame = 0, maximum_frames

    if not settings.has_preview:
        render_frames = maximum_frames
    elif preview_duration is None:
        render_frames = maximum_frames
    else:
        requested_end = preview_start + preview_duration
        if requested_end <= base_frames:
            render_frames = min(maximum_frames, requested_end + guard)
        else:
            # Determine the true end of the reverb tail so a requested preview
            # extending into the tail has the same length as the full render.
            render_frames = maximum_frames

    try:
        dry_chain, reverb = _make_vocal_processors(pedalboard, settings, sample_rate)
        if not has_audible_vocal:
            reverb = None
    except Exception as error:
        raise MixdownError("无法创建 Pedalboard 人声处理链。") from error
    vocal_gains = [10.0 ** (track.gain_db / 20.0) for track in settings.vocals]
    vocal_pans = [pan_gains(track.pan) for track in settings.vocals]
    instrumental_gain = 10.0 ** (settings.instrumental_db / 20.0)
    instrumental_pans = pan_gains(settings.instrumental_pan)
    tail_threshold = 10.0 ** (TAIL_FLOOR_DBFS / 20.0)
    last_tail_frame: int | None = None
    first = True
    vocal_peaks = [0.0 for _ in settings.vocals]
    track_after_peaks = [0.0 for _ in settings.vocals]
    instrumental_peak = 0.0
    bus_peak = 0.0
    bus_after_effects_peak = 0.0
    gr_values: list[float] = []
    started = time.monotonic()

    def progress_tracks(gr_value: float | None) -> list[dict[str, Any]]:
        result = [
            {
                "i": index,
                "role": "vocal",
                "peak": _amplitude_to_dbfs(vocal_peaks[index]),
                "gr": gr_value,
            }
            for index in range(len(settings.vocals))
        ]
        result.append({
            "i": len(settings.vocals),
            "role": "backing",
            "peak": _amplitude_to_dbfs(instrumental_peak),
            "gr": None,
        })
        return result

    try:
        with ExitStack() as stack:
            vocals = [
                stack.enter_context(soundfile.SoundFile(str(source), "r"))
                for source, _ in vocal_inputs
            ]
            instrumental = stack.enter_context(
                soundfile.SoundFile(str(instrumental_path), "r")
            )
            output = stack.enter_context(soundfile.SoundFile(
                str(mix_path), "w", samplerate=sample_rate, channels=channels,
                format="WAV", subtype="FLOAT",
            ))
            for start in range(0, render_frames, CHUNK_FRAMES):
                _check_cancel(cancel)
                count = min(CHUNK_FRAMES, render_frames - start)
                meter_lo = max(0, meter_start_frame - start)
                meter_hi = min(count, meter_end_frame - start)
                vocal_block = numpy.zeros((count, channels), dtype=numpy.float32)
                instrumental_block = numpy.zeros((count, channels), dtype=numpy.float32)
                block_track_peaks = [0.0 for _ in settings.vocals]

                for track_index, (handle, (_, vocal_frames), offset_samples, gain,
                                  track_audible, track_pan) in enumerate(zip(
                        vocals, vocal_inputs, offsets, vocal_gains,
                        vocal_audible, vocal_pans)):
                    if not track_audible:
                        continue
                    source_start = start - offset_samples
                    destination_start = max(0, -source_start)
                    source_index = max(0, source_start)
                    vocal_count = min(
                        count - destination_start, vocal_frames - source_index
                    )
                    track_block = numpy.zeros((count, channels), dtype=numpy.float32)
                    if vocal_count > 0:
                        track_block[
                            destination_start:destination_start + vocal_count
                        ] = _read_frames(
                            handle, source_index, vocal_count, channels, numpy
                        )
                    track_block *= numpy.float32(gain)
                    if channels == 2:
                        track_block[:, 0] *= numpy.float32(track_pan[0])
                        track_block[:, 1] *= numpy.float32(track_pan[1])
                    if meter_hi > meter_lo:
                        measured = track_block[meter_lo:meter_hi]
                        block_track_peaks[track_index] = float(
                            numpy.max(numpy.abs(measured)))
                        vocal_peaks[track_index] = max(
                            vocal_peaks[track_index],
                            block_track_peaks[track_index],
                        )
                    vocal_block = numpy.add(
                        vocal_block, track_block, dtype=numpy.float32
                    )

                if not numpy.isfinite(vocal_block).all():
                    raise ValueError("人声增益或偏移求和产生了 NaN 或无穷大采样。")

                if backing_audible and start < instrumental_frames:
                    instrumental_count = min(count, instrumental_frames - start)
                    instrumental_block[:instrumental_count] = _read_frames(
                        instrumental, start, instrumental_count, channels, numpy
                    )

                raw_vocal_rms = _rms(vocal_block, numpy)
                if dry_chain is not None:
                    vocal_block = _pedalboard_process(
                        dry_chain, vocal_block, sample_rate, first, numpy
                    )
                processed_vocal_rms = _rms(vocal_block, numpy)
                ratio = (processed_vocal_rms / raw_vocal_rms
                         if raw_vocal_rms > 1e-12 else 1.0)
                bus_gr: float | None = None
                if meter_hi > meter_lo:
                    for track_index in range(len(settings.vocals)):
                        track_after_peaks[track_index] = max(
                            track_after_peaks[track_index],
                            block_track_peaks[track_index] * ratio,
                        )
                    if settings.compression_strength > 0.0:
                        bus_gr = _gain_ratio_db(raw_vocal_rms, processed_vocal_rms)
                        gr_values.append(bus_gr)
                if reverb is not None:
                    wet = _pedalboard_process(
                        reverb, vocal_block, sample_rate, first, numpy
                    )
                    mix_value = settings.reverb_mix
                    vocal_block = (
                        vocal_block * (1.0 - mix_value) + wet * mix_value
                    ).astype(numpy.float32, copy=False)
                first = False

                if backing_audible:
                    instrumental_block *= numpy.float32(instrumental_gain)
                    if channels == 2:
                        instrumental_block[:, 0] *= numpy.float32(instrumental_pans[0])
                        instrumental_block[:, 1] *= numpy.float32(instrumental_pans[1])
                if meter_hi > meter_lo:
                    measured_backing = instrumental_block[meter_lo:meter_hi]
                    instrumental_peak = max(
                        instrumental_peak,
                        float(numpy.max(numpy.abs(measured_backing))),
                    )
                mixed = numpy.add(vocal_block, instrumental_block, dtype=numpy.float32)
                if not numpy.isfinite(mixed).all():
                    raise ValueError("混音产生了 NaN 或无穷大采样，已停止输出。")
                if meter_hi > meter_lo:
                    measured_bus = mixed[meter_lo:meter_hi]
                    measured_peak = float(numpy.max(numpy.abs(measured_bus)))
                    bus_peak = max(bus_peak, measured_peak)
                    bus_after_effects_peak = max(bus_after_effects_peak, measured_peak)

                output.write(mixed)
                if reverb is not None and start + count > base_frames:
                    tail_start = max(0, base_frames - start)
                    peak = numpy.max(numpy.abs(mixed[tail_start:]), axis=1)
                    audible = numpy.flatnonzero(peak > tail_threshold)
                    if audible.size:
                        last_tail_frame = start + tail_start + int(audible[-1])
                _emit_progress(
                    progress,
                    phase="mix",
                    frames_done=start + count,
                    frames_total=render_frames,
                    tracks=progress_tracks(bus_gr),
                    bus_peak=_amplitude_to_dbfs(bus_peak),
                    elapsed_ms=(time.monotonic() - started) * 1000.0,
                )
    except MixdownError:
        raise
    except InterruptedError:
        raise
    except Exception as error:
        raise MixdownError("混音处理失败，未生成目标文件。") from error

    full_frames = base_frames
    if reverb is not None and last_tail_frame is not None:
        margin = int(round(TAIL_MARGIN_SECONDS * sample_rate))
        full_frames = min(maximum_frames, max(base_frames, last_tail_frame + 1 + margin))
    gr_db_p95 = 0.0
    if gr_values:
        gr_db_p95 = float(numpy.percentile(numpy.asarray(gr_values), 95))
    return (base_frames, full_frames, preview_start, preview_duration,
            vocal_peaks, instrumental_peak, bus_peak, track_after_peaks,
            bus_after_effects_peak, gr_db_p95)


def _render_mix_v3(settings: _Settings, vocal_inputs: list[tuple[Path, int]],
                   instrumental_path: Path, sample_rate: int, channels: int,
                   instrumental_frames: int, mix_path: Path, pedalboard: Any,
                   numpy: Any, soundfile: Any, cancel: Callable[[], Any] | None,
                   progress: Callable[[dict[str, Any]], Any] | None = None
                   ) -> tuple[int, int, int | None, int | None, list[float], float,
                              float, list[float], float, float]:
    """Render the v3 effects graph with a single send/return reverb instance."""
    if len(vocal_inputs) != len(settings.vocals):
        raise MixdownError("内部歌声轨数量不一致，已停止混音。")
    vocal_audible, backing_audible = _audibility(settings)
    has_audible_vocal = any(vocal_audible)

    def pan_gains(value: float) -> tuple[float, float]:
        if channels < 2 or value == 0.0:
            return 1.0, 1.0
        if value < 0.0:
            return 1.0, 1.0 + value
        return 1.0 - value, 1.0

    offsets = [
        int(round(track.offset_seconds * sample_rate))
        for track in settings.vocals
    ]
    vocal_ends = [
        max(0, frames + offset)
        for (_, frames), offset in zip(vocal_inputs, offsets)
    ]
    base_frames = max([instrumental_frames, *vocal_ends])
    if base_frames <= 0:
        raise ValueError("伴奏和歌声轨均没有可混音内容。")

    has_reverb_send = any(
        audible and track.reverb_send > 0.0
        for track, audible in zip(settings.vocals, vocal_audible)
    )
    tail_limit = (_reverb_tail_budget_frames(
                      settings.reverb_predelay_ms, sample_rate)
                  if settings.reverb_mix > 0 and has_audible_vocal
                  and has_reverb_send else 0)
    maximum_frames = base_frames + tail_limit
    guard = int(round(LIMITER_GUARD_SECONDS * sample_rate))

    preview_start = None
    preview_duration = None
    if settings.has_preview:
        preview_start = int(round((settings.preview_start_seconds or 0.0) * sample_rate))
        if preview_start >= maximum_frames:
            raise ValueError("预览起点超过可渲染音频范围。")
        if settings.preview_duration_seconds is not None:
            preview_duration = max(
                1, int(round(settings.preview_duration_seconds * sample_rate)))

    if settings.has_preview:
        meter_start_frame = preview_start or 0
        meter_end_frame = (meter_start_frame + preview_duration
                           if preview_duration is not None else maximum_frames)
    else:
        meter_start_frame, meter_end_frame = 0, maximum_frames

    if not settings.has_preview:
        render_frames = maximum_frames
    elif preview_duration is None:
        render_frames = maximum_frames
    else:
        requested_end = preview_start + preview_duration
        if requested_end <= base_frames:
            render_frames = min(maximum_frames, requested_end + guard)
        else:
            render_frames = maximum_frames

    try:
        eq_chain, compressor, reverb, predelay = _make_v3_processors(
            pedalboard, settings, sample_rate)
        if not has_audible_vocal or not has_reverb_send:
            reverb = None
            predelay = None
    except Exception as error:
        raise MixdownError("无法创建 Pedalboard 人声处理链。") from error

    vocal_gains = [10.0 ** (track.gain_db / 20.0) for track in settings.vocals]
    vocal_pans = [pan_gains(track.pan) for track in settings.vocals]
    vocal_sends = [float(track.reverb_send) for track in settings.vocals]
    instrumental_gain = 10.0 ** (settings.instrumental_db / 20.0)
    instrumental_pans = pan_gains(settings.instrumental_pan)
    tail_threshold = 10.0 ** (TAIL_FLOOR_DBFS / 20.0)
    last_tail_frame: int | None = None
    first = True
    delay_state = None
    delay_frames = _predelay_frames(settings.reverb_predelay_ms, sample_rate)
    vocal_peaks = [0.0 for _ in settings.vocals]
    track_after_peaks = [0.0 for _ in settings.vocals]
    instrumental_peak = 0.0
    bus_peak = 0.0
    bus_after_effects_peak = 0.0
    gr_values: list[float] = []
    started = time.monotonic()

    def progress_tracks(gr_value: float | None) -> list[dict[str, Any]]:
        result = [
            {
                "i": index,
                "role": "vocal",
                "peak": _amplitude_to_dbfs(vocal_peaks[index]),
                "gr": gr_value,
            }
            for index in range(len(settings.vocals))
        ]
        result.append({
            "i": len(settings.vocals),
            "role": "backing",
            "peak": _amplitude_to_dbfs(instrumental_peak),
            "gr": None,
        })
        return result

    try:
        with ExitStack() as stack:
            vocals = [
                stack.enter_context(soundfile.SoundFile(str(source), "r"))
                for source, _ in vocal_inputs
            ]
            instrumental = stack.enter_context(
                soundfile.SoundFile(str(instrumental_path), "r")
            )
            output = stack.enter_context(soundfile.SoundFile(
                str(mix_path), "w", samplerate=sample_rate, channels=channels,
                format="WAV", subtype="FLOAT",
            ))
            for start in range(0, render_frames, CHUNK_FRAMES):
                _check_cancel(cancel)
                count = min(CHUNK_FRAMES, render_frames - start)
                meter_lo = max(0, meter_start_frame - start)
                meter_hi = min(count, meter_end_frame - start)
                vocal_block = numpy.zeros((count, channels), dtype=numpy.float32)
                send_block = numpy.zeros((count, channels), dtype=numpy.float32)
                block_track_peaks = [0.0 for _ in settings.vocals]
                instrumental_block = numpy.zeros(
                    (count, channels), dtype=numpy.float32)

                for track_index, (handle, (_, vocal_frames), offset_samples, gain,
                                  track_audible, track_pan, send) in enumerate(zip(
                        vocals, vocal_inputs, offsets, vocal_gains,
                        vocal_audible, vocal_pans, vocal_sends)):
                    if not track_audible:
                        continue
                    source_start = start - offset_samples
                    destination_start = max(0, -source_start)
                    source_index = max(0, source_start)
                    vocal_count = min(
                        count - destination_start, vocal_frames - source_index
                    )
                    track_block = numpy.zeros(
                        (count, channels), dtype=numpy.float32)
                    if vocal_count > 0:
                        track_block[
                            destination_start:destination_start + vocal_count
                        ] = _read_frames(
                            handle, source_index, vocal_count, channels, numpy
                        )
                    track_block *= numpy.float32(gain)
                    if channels == 2:
                        track_block[:, 0] *= numpy.float32(track_pan[0])
                        track_block[:, 1] *= numpy.float32(track_pan[1])
                    if meter_hi > meter_lo:
                        measured = track_block[meter_lo:meter_hi]
                        block_track_peaks[track_index] = float(
                            numpy.max(numpy.abs(measured)))
                        vocal_peaks[track_index] = max(
                            vocal_peaks[track_index], block_track_peaks[track_index])
                    vocal_block = numpy.add(
                        vocal_block, track_block, dtype=numpy.float32
                    )
                    if send > 0.0:
                        send_block = numpy.add(
                            send_block,
                            track_block * numpy.float32(send),
                            dtype=numpy.float32,
                        )

                if not numpy.isfinite(vocal_block).all():
                    raise ValueError("人声增益或偏移求和产生了 NaN 或无穷大采样。")
                if not numpy.isfinite(send_block).all():
                    raise ValueError("混响发送产生了 NaN 或无穷大采样。")

                if backing_audible and start < instrumental_frames:
                    instrumental_count = min(count, instrumental_frames - start)
                    instrumental_block[:instrumental_count] = _read_frames(
                        instrumental, start, instrumental_count, channels, numpy
                    )

                raw_vocal = vocal_block
                before_effect_rms = _rms(raw_vocal, numpy)
                if eq_chain is not None:
                    vocal_block = _pedalboard_process(
                        eq_chain, vocal_block, sample_rate, first, numpy
                    )
                before_compression_rms = _rms(vocal_block, numpy)
                if compressor is not None:
                    vocal_block = _pedalboard_process(
                        compressor, vocal_block, sample_rate, first, numpy
                    )
                after_compression_rms = _rms(vocal_block, numpy)
                block_ratio = (
                    after_compression_rms / before_effect_rms
                    if before_effect_rms > 1e-12 else 1.0
                )
                if meter_hi > meter_lo:
                    for track_index in range(len(settings.vocals)):
                        track_after_peaks[track_index] = max(
                            track_after_peaks[track_index],
                            block_track_peaks[track_index] * block_ratio,
                        )
                    if compressor is not None:
                        gr_values.append(_gain_ratio_db(
                            before_compression_rms, after_compression_rms))
                        bus_gr = gr_values[-1]
                    else:
                        bus_gr = 0.0
                else:
                    bus_gr = 0.0
                if compressor is not None and meter_hi <= meter_lo:
                    bus_gr = _gain_ratio_db(
                        before_compression_rms, after_compression_rms)

                dry_vocal = vocal_block
                if reverb is not None:
                    if predelay is not None:
                        delayed_send = _pedalboard_process(
                            predelay, send_block, sample_rate, first, numpy)
                    else:
                        delayed_send, delay_state = _delay_block(
                            send_block, delay_frames, delay_state, numpy)
                    wet = _pedalboard_process(
                        reverb, delayed_send, sample_rate, first, numpy
                    )
                    mix_value = settings.reverb_mix
                    vocal_block = (
                        dry_vocal * (1.0 - mix_value) + wet * mix_value
                    ).astype(numpy.float32, copy=False)
                first = False

                if backing_audible:
                    instrumental_block *= numpy.float32(instrumental_gain)
                    if channels == 2:
                        instrumental_block[:, 0] *= numpy.float32(instrumental_pans[0])
                        instrumental_block[:, 1] *= numpy.float32(instrumental_pans[1])
                if meter_hi > meter_lo:
                    measured_backing = instrumental_block[meter_lo:meter_hi]
                    instrumental_peak = max(
                        instrumental_peak,
                        float(numpy.max(numpy.abs(measured_backing))),
                    )
                mixed = numpy.add(vocal_block, instrumental_block, dtype=numpy.float32)
                mixed = _apply_width(mixed, settings.width, numpy)
                if not numpy.isfinite(mixed).all():
                    raise ValueError("混音产生了 NaN 或无穷大采样，已停止输出。")
                if meter_hi > meter_lo:
                    measured_bus = mixed[meter_lo:meter_hi]
                    measured_peak = float(numpy.max(numpy.abs(measured_bus)))
                    bus_peak = max(bus_peak, measured_peak)
                    bus_after_effects_peak = max(bus_after_effects_peak, measured_peak)

                output.write(mixed)
                if reverb is not None and start + count > base_frames:
                    tail_start = max(0, base_frames - start)
                    peak = numpy.max(numpy.abs(mixed[tail_start:]), axis=1)
                    audible = numpy.flatnonzero(peak > tail_threshold)
                    if audible.size:
                        last_tail_frame = start + tail_start + int(audible[-1])
                _emit_progress(
                    progress,
                    phase="mix",
                    frames_done=start + count,
                    frames_total=render_frames,
                    tracks=progress_tracks(
                        bus_gr if compressor is not None else None),
                    bus_peak=_amplitude_to_dbfs(bus_peak),
                    elapsed_ms=(time.monotonic() - started) * 1000.0,
                )
    except MixdownError:
        raise
    except InterruptedError:
        raise
    except Exception as error:
        raise MixdownError("混音处理失败，未生成目标文件。") from error

    full_frames = base_frames
    if reverb is not None and last_tail_frame is not None:
        margin = int(round(TAIL_MARGIN_SECONDS * sample_rate))
        full_frames = min(maximum_frames, max(base_frames, last_tail_frame + 1 + margin))
    gr_db_p95 = 0.0
    if gr_values:
        gr_db_p95 = float(numpy.percentile(numpy.asarray(gr_values), 95))
    return (base_frames, full_frames, preview_start, preview_duration,
            vocal_peaks, instrumental_peak, bus_peak, track_after_peaks,
            bus_after_effects_peak, gr_db_p95)


def _encode_final(premix: Path, output: Path, sample_rate: int, channels: int,
                  start_frame: int, end_frame: int, ffmpeg: Path, log_path: Path,
                  cancel: Callable[[], Any] | None, *, codec: str = 'pcm_s24le') -> None:
    if end_frame <= start_frame:
        raise ValueError("预览区间没有可输出的采样。")
    if codec not in ('pcm_s24le', 'pcm_f32le'):
        raise ValueError("不支持此混音 WAV 编码。")
    filters = (
        f"alimiter=limit={PEAK_CEILING:.9f}:attack=5:release=100:level=false:latency=true,"
        f"atrim=start_sample={start_frame}:end_sample={end_frame},asetpts=PTS-STARTPTS"
    )
    _run_ffmpeg(
        [
            str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-fflags", "+bitexact", "-i", str(premix), "-af", filters,
            "-ar", str(sample_rate), "-ac", str(channels), "-c:a", codec,
            "-rf64", "auto", "-map_metadata", "-1", str(output),
        ],
        log_path,
        cancel,
        "最终 PCM24 WAV 编码失败，未生成目标文件。",
    )


def _inspect_final(path: Path, sample_rate: int, channels: int,
                   expected_frames: int, soundfile: Any, numpy: Any,
                   cancel: Callable[[], Any] | None, *,
                   allowed_subtypes: tuple[str, ...] = ("PCM_24",)) -> float:
    try:
        info = soundfile.info(str(path))
    except Exception as error:
        raise MixdownError("无法读取最终 WAV 文件。") from error
    if str(getattr(info, "format", "")).upper() not in {"WAV", "WAVEX", "RF64"}:
        raise MixdownError("最终文件不是 WAV/WAVEX/RF64 容器。")
    if (int(info.samplerate) != sample_rate or int(info.channels) != channels
            or int(info.frames) != expected_frames):
        raise MixdownError("最终 WAV 的采样率、声道数或帧数验证失败。")
    subtype = str(getattr(info, "subtype", "")).upper()
    allowed = {str(value).strip().upper() for value in allowed_subtypes}
    if subtype not in allowed:
        expected = "/".join(sorted(allowed))
        raise MixdownError(
            f"最终 WAV 编码不符（期望 {expected}，实际为 {subtype or '未知'}）。"
        )

    peak = 0.0
    try:
        with soundfile.SoundFile(str(path), "r") as handle:
            while True:
                _check_cancel(cancel)
                block = handle.read(CHUNK_FRAMES, dtype="float32", always_2d=True)
                if len(block) == 0:
                    break
                array = numpy.asarray(block, dtype=numpy.float32)
                if not numpy.isfinite(array).all():
                    raise ValueError("最终 WAV 包含 NaN 或无穷大采样。")
                if array.size:
                    peak = max(peak, float(numpy.max(numpy.abs(array))))
    except InterruptedError:
        raise
    except ValueError:
        raise
    except Exception as error:
        raise MixdownError("最终 WAV 完整性检查失败。") from error
    if peak > PEAK_CEILING + 1e-5:
        raise MixdownError("最终峰值保护未通过，输出已拒绝发布。")
    return max(SILENCE_DBFS, 20.0 * math.log10(peak)) if peak > 0.0 else SILENCE_DBFS


def render(request: Mapping[str, Any], cancel: Callable[[], Any] | None = None,
           progress: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Render one to three vocals and one required backing to a new PCM24 WAV.

    New callers provide ``vocals`` as a list of ``{path, gain_db,
    offset_seconds}`` objects. The legacy single-vocal fields
    ``vocal_path``/``vocal_db``/``offset_seconds`` remain accepted when
    ``vocals`` is absent. Per-vocal offsets are rounded to output samples.
    Positive values delay a vocal; negative values trim its beginning. The
    optional ``progress`` callback receives one throttled dictionary per
    rendered block; its observations never alter the returned report. Preview
    rendering runs the same chain from the start of the song and returns a
    sample-aligned excerpt.
    """

    if cancel is not None and not callable(cancel):
        raise TypeError("cancel 必须是无参数可调用对象或 None。")
    if progress is not None and not callable(progress):
        raise TypeError("progress 必须是无参数可调用对象或 None。")
    settings = _settings(request)
    _check_cancel(cancel)
    ffmpeg = _ffmpeg_executable()
    pedalboard = _load_pedalboard()

    output = settings.output_path
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=".audio-mixdown-", dir=str(output.parent)))
    except OSError as error:
        raise MixdownError(f"无法在输出目录建立临时工作区：{output.parent}。") from error
    try:
        source_paths = [track.path for track in settings.vocals]
        source_paths.append(settings.instrumental_path)
        source_fingerprints = {
            path: _source_fingerprint(path)
            for path in source_paths
        }
        source_hashes = {
            str(path): _file_sha256(path, cancel)
            for path in source_fingerprints
        }

        vocal_probes = []
        for index, track in enumerate(settings.vocals):
            vocal_probes.append(_probe_audio(
                track.path, ffmpeg,
                temp_root / f"probe-vocal-{index + 1}.log", cancel,
            ))
        instrumental_probe = _probe_audio(
            settings.instrumental_path, ffmpeg,
            temp_root / "probe-instrumental.log", cancel,
        )
        sample_rate = instrumental_probe.sample_rate
        source_channels = [
            instrumental_probe.channels,
            *(probe.channels for probe in vocal_probes),
        ]
        vocal_audible, backing_audible = _audibility(settings)
        # v3 uses a stereo-capable bus only when a stereo source, an audible
        # pan, or a non-unit width needs one. All-mono, centered, width=1
        # requests may safely collapse back to mono.
        channels = _output_channels(settings, source_channels)
        if settings.uses_v3 and max(source_channels) == 1 and settings.width != 1.0:
            _safe_warning(
                settings,
                "width is centered and therefore inaudible for mono sources; "
                "the requested value was retained in the report.",
            )

        vocal_inputs: list[tuple[Path, int]] = []
        for index, track in enumerate(settings.vocals):
            vocal_float = temp_root / f"vocal-{index + 1}-f32.wav"
            vocal_info = _decode_to_float_wav(
                track.path, vocal_float, sample_rate, channels, ffmpeg,
                temp_root / f"decode-vocal-{index + 1}.log", cancel,
            )
            vocal_inputs.append((vocal_float, vocal_info.frames))
        instrumental_float = temp_root / "instrumental-f32.wav"
        instrumental_info = _decode_to_float_wav(
            settings.instrumental_path, instrumental_float, sample_rate, channels,
            ffmpeg, temp_root / "decode-instrumental.log", cancel,
        )

        try:
            import numpy
            import soundfile
        except ImportError as error:
            raise MixdownError("简易混音缺少 NumPy 或 SoundFile 运行时依赖。") from error

        premix = temp_root / "premix-f32.wav"
        render_function = _render_mix_v3 if settings.uses_v3 else _render_mix
        (base_frames, full_frames, preview_start, preview_duration,
         vocal_peaks, instrumental_peak, bus_peak, track_after_peaks,
         bus_after_effects_peak, gr_db_p95) = render_function(
            settings, vocal_inputs, instrumental_float, sample_rate, channels,
            instrumental_info.frames, premix, pedalboard, numpy, soundfile, cancel,
            progress=progress,
        )

        if preview_start is None:
            start_frame, end_frame = 0, full_frames
        else:
            start_frame = preview_start
            if start_frame >= full_frames:
                raise ValueError("预览起点超过实际混音长度。")
            if preview_duration is None:
                end_frame = full_frames
            else:
                end_frame = min(full_frames, start_frame + preview_duration)
        expected_frames = end_frame - start_frame
        if expected_frames <= 0:
            raise ValueError("预览时长不足一个采样。")
        estimated_bytes = expected_frames * channels * 3
        if estimated_bytes > MAX_OUTPUT_BYTES:
            raise ValueError("输出超过 16 GiB 资源限制，已安全拒绝。")

        staged = temp_root / "final-pcm24.wav"
        _encode_final(
            premix, staged, sample_rate, channels, start_frame, end_frame,
            ffmpeg, temp_root / "encode-final.log", cancel,
        )
        peak_dbfs = _inspect_final(
            staged, sample_rate, channels, expected_frames, soundfile, numpy, cancel
        )
        _check_cancel(cancel)

        for path, fingerprint in source_fingerprints.items():
            if (_source_fingerprint(path) != fingerprint
                    or _file_sha256(path, cancel) != source_hashes[str(path)]):
                raise MixdownError(f"渲染期间源音频发生变化，已停止输出：{path.name}")
        output_sha256 = _file_sha256(staged, cancel)
        _check_cancel(cancel)
        if os.path.lexists(output):
            raise FileExistsError(f"输出文件已经存在，为避免覆盖已拒绝：{output}")
        _install_new_file(staged, output)

        vocal_audible, backing_audible = _audibility(settings)
        global_gr = float(gr_db_p95)
        tracks = []
        for index, track in enumerate(settings.vocals):
            track_peak = _amplitude_to_dbfs(vocal_peaks[index])
            tracks.append({
                "role": "vocal",
                "index": index,
                "path": str(track.path),
                "gain_db": track.gain_db,
                "offset_seconds": track.offset_seconds,
                "mute": track.mute,
                "solo": track.solo,
                "pan": track.pan,
                "reverb_send": float(track.reverb_send),
                "audible": vocal_audible[index],
                "peak_dbfs": track_peak,
                "peak_after_effects_dbfs": _amplitude_to_dbfs(
                    track_after_peaks[index]),
                "gr_db_p95": global_gr if vocal_audible[index] else 0.0,
                "input_sha256": source_hashes[str(track.path)],
                "output_sha256": output_sha256,
            })
        backing_peak = _amplitude_to_dbfs(instrumental_peak)
        tracks.append({
            "role": "backing",
            "index": 0,
            "path": str(settings.instrumental_path),
            "gain_db": settings.instrumental_db,
            "offset_seconds": 0.0,
            "mute": settings.instrumental_mute,
            "solo": settings.instrumental_solo,
            "pan": settings.instrumental_pan,
            "reverb_send": 0.0,
            "audible": backing_audible,
            "peak_dbfs": backing_peak,
            "peak_after_effects_dbfs": backing_peak,
            "gr_db_p95": 0.0,
            "input_sha256": source_hashes[str(settings.instrumental_path)],
            "output_sha256": output_sha256,
        })
        meter_tracks = [
            {
                "role": track["role"],
                "index": track["index"],
                "audible": track["audible"],
                "peak_dbfs": track["peak_dbfs"],
            }
            for track in tracks
        ]

        return {
            "schema": REPORT_SCHEMA if settings.uses_v3 else REPORT_SCHEMA_V2,
            "output_path": str(output),
            "duration_seconds": expected_frames / float(sample_rate),
            "peak_dbfs": float(peak_dbfs),
            "sample_rate": sample_rate,
            "channels": channels,
            "tracks": tracks,
            "meters": {
                "tracks": meter_tracks,
                "bus_peak_dbfs": _amplitude_to_dbfs(bus_peak),
            },
            "gr_db_p95": global_gr,
            "peak_after_effects_dbfs": _amplitude_to_dbfs(bus_after_effects_peak),
            "uses_v3": bool(settings.uses_v3),
            "warnings": list(settings.warnings),
            "source_sha256": source_hashes,
            "output_sha256": output_sha256,
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


__all__ = ["MixdownCancelled", "MixdownError", "render"]
