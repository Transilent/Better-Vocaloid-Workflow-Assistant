"""Deterministic reference-audio and measured-F0 review helpers.

This module reads user-supplied WAV, MIDI and pitch sidecar files.  It does not
run inference, synthesis, transcription or a provider, and it never changes a
score from measured pitch evidence.
"""
from __future__ import annotations

import hashlib
import heapq
import io
import json
import math
import os
import struct
import tempfile
import wave
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from agent_score import read_score
from common import native_path


SCHEMA_VERSION = 1
EXCERPT_KIND = "vwa-reference-audio-excerpt-receipt"
MAX_ANALYSIS_SECONDS = 30.0
MAX_WINDOW_PCM_BYTES = 256 * 1024 * 1024
MAX_NPZ_FILE_BYTES = 64 * 1024 * 1024
MAX_NPZ_MEMBER_BYTES = 96 * 1024 * 1024
MAX_NPZ_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_NPZ_COMPRESSION_RATIO = 5000
MAX_PITCH_FRAMES = 6_000_000
MAX_PAGE_SIZE = 500
MIN_VOICED_FRAMES = 3
MIN_COVERAGE = 0.50
MAX_STABLE_CENTRAL_SPAN_SEMITONES = 1.25
OFFSET_HINT_CENTS = 50.0
OCTAVE_TOLERANCE_CENTS = 60.0
SUPPORTED_PCM_WIDTHS = (1, 2, 3, 4)
_PITCH_MEMBERS = {"midi_pitch.npy", "voiced_mask.npy", "time_step.npy", "schema.npy"}
_PITCH_ARRAY_KEYS = {name.removesuffix(".npy") for name in _PITCH_MEMBERS}


def _io_path(path: Any) -> Path:
    return Path(native_path(path))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    text = json.dumps(value, **options)
    if pretty:
        text += "\n"
    return text.encode("utf-8")


def _canonical_digest(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _round(value: float | None, digits: int = 9) -> float | None:
    return None if value is None else round(float(value), digits)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(native_path(path), "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"无法读取文件以计算 SHA-256：{exc}") from exc
    return digest.hexdigest()


def _resolve_file(value: Any, label: str) -> Path:
    if value is None or isinstance(value, (bool, bytes)):
        raise ValueError(f"{label} 路径无效。")
    try:
        path = Path(value)
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError(f"{label} 路径无效。") from exc
    try:
        if not _io_path(path).is_file():
            raise ValueError(f"{label} 不存在或不是普通文件。")
        return path.resolve(strict=True)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"无法解析{label}路径：{exc}") from exc


def _resolve_output_root(value: Any) -> Path:
    if value is None or isinstance(value, (bool, bytes)):
        raise ValueError("output_root 必须是目录路径。")
    try:
        if isinstance(value, str) and not value.strip():
            raise ValueError("output_root 不能为空。")
        root = Path(value)
        native = _io_path(root)
        if native.exists() and not native.is_dir():
            raise ValueError("output_root 已存在但不是目录。")
        native.mkdir(parents=True, exist_ok=True)
        return root.resolve(strict=True)
    except ValueError:
        raise
    except (TypeError, OSError) as exc:
        raise ValueError(f"output_root 无效：{exc}") from exc


def _strict_int(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} 必须是整数。")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label} 超出允许范围。")
    return value


def _finite_number(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数值。")
    return number


def _parse_pcm_header(path: Path, file_size: int) -> dict[str, Any]:
    try:
        stream = open(native_path(path), "rb")
    except OSError as exc:
        raise ValueError(f"无法打开 WAV：{exc}") from exc
    with stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError("只支持 RIFF/WAVE 文件。")
        position = 12
        fmt = None
        data_offset = None
        data_size = None
        while position + 8 <= file_size:
            stream.seek(position)
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            payload_offset = position + 8
            payload_end = payload_offset + chunk_size
            if payload_end > file_size:
                raise ValueError("WAV 数据块长度超出文件边界。")
            if chunk_id == b"fmt ":
                if chunk_size < 16 or chunk_size > 65536:
                    raise ValueError("WAV fmt 块长度无效。")
                payload = stream.read(chunk_size)
                if len(payload) != chunk_size:
                    raise ValueError("WAV fmt 块被截断。")
                fmt = struct.unpack("<HHIIHH", payload[:16])
            elif chunk_id == b"data":
                if fmt is None:
                    raise ValueError("WAV data 块出现在 fmt 块之前。")
                data_offset = payload_offset
                data_size = chunk_size
                break
            position = payload_end + (chunk_size & 1)
        if fmt is None:
            raise ValueError("WAV 缺少 fmt 块。")
        if data_offset is None or data_size is None:
            raise ValueError("WAV 缺少 data 块。")
    format_tag, channels, sample_rate, byte_rate, block_align, bits = fmt
    if format_tag != 1:
        raise ValueError("只支持未压缩 PCM WAV（format tag 1）。")
    if not 1 <= channels <= 32:
        raise ValueError("WAV 声道数超出支持范围。")
    if not 1 <= sample_rate <= 384_000:
        raise ValueError("WAV 采样率超出支持范围。")
    sample_width = bits // 8
    if bits % 8 or sample_width not in SUPPORTED_PCM_WIDTHS:
        raise ValueError("只支持 8、16、24 或 32 位整数 PCM WAV。")
    if block_align != channels * sample_width:
        raise ValueError("WAV block alignment 与声道/位宽不一致。")
    if byte_rate != sample_rate * block_align:
        raise ValueError("WAV byte rate 与采样格式不一致。")
    if data_size % block_align:
        raise ValueError("WAV data 长度不是完整 PCM 帧。")
    return {
        "container": "RIFF/WAVE",
        "encoding": "PCM",
        "format_tag": format_tag,
        "channels": channels,
        "sample_rate_hz": sample_rate,
        "byte_rate": byte_rate,
        "block_align_bytes": block_align,
        "bits_per_sample": bits,
        "sample_width_bytes": sample_width,
        "data_offset": data_offset,
        "data_size_bytes": data_size,
    }


def _wav_metadata(path: Path) -> dict[str, Any]:
    try:
        file_size = _io_path(path).stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取 WAV 文件信息：{exc}") from exc
    original = _parse_pcm_header(path, file_size)
    try:
        with open(native_path(path), "rb") as raw_stream:
            with wave.open(raw_stream, "rb") as stream:
                if stream.getcomptype() != "NONE":
                    raise ValueError("WAV 使用了非 PCM 压缩。")
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                frame_count = stream.getnframes()
    except ValueError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"无法解析 WAV：{exc}") from exc
    expected = (
        original["channels"],
        original["sample_width_bytes"],
        original["sample_rate_hz"],
    )
    if (channels, sample_width, sample_rate) != expected:
        raise ValueError("WAV fmt 块与 Python wave 解析结果不一致。")
    source_sha = _sha256_file(path)
    return {
        "path": path,
        "source_sha256": source_sha,
        "file_size_bytes": file_size,
        "frame_count": frame_count,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate_hz": sample_rate,
        "duration_seconds": frame_count / sample_rate,
        "original_format": {
            key: value
            for key, value in original.items()
            if key not in {"data_offset", "data_size_bytes"}
        },
    }


def _range(
    start: Any,
    end: Any,
    duration: float,
    *,
    require_both: bool,
    analysis: bool,
) -> dict[str, Any] | None:
    if start is None and end is None:
        if require_both:
            raise ValueError("start 和 end 都是必需参数。")
        return None
    if require_both and (start is None or end is None):
        raise ValueError("start 和 end 都是必需参数。")
    first = 0.0 if start is None else _finite_number(start, "start")
    last = duration if end is None else _finite_number(end, "end")
    if first < 0 or last > duration + 1e-12:
        raise ValueError("音频范围超出 WAV 边界。")
    if last <= first:
        raise ValueError("音频范围必须满足 end > start。")
    if analysis and last - first > MAX_ANALYSIS_SECONDS + 1e-12:
        raise ValueError("音频分析范围最多 30 秒。")
    return {
        "requested_start_seconds": _round(first),
        "requested_end_seconds": _round(last),
        "requested_duration_seconds": _round(last - first),
    }
def _frame_alignment(
    requested: dict[str, Any],
    sample_rate: int,
    frame_count: int,
) -> dict[str, int | float]:
    first_seconds = float(requested["requested_start_seconds"])
    last_seconds = float(requested["requested_end_seconds"])
    first = max(0, min(frame_count, int(math.floor(first_seconds * sample_rate + 1e-12))))
    last = max(0, min(frame_count, int(math.ceil(last_seconds * sample_rate - 1e-12))))
    if last <= first and first < frame_count:
        last = first + 1
    if last <= first:
        raise ValueError("音频范围无法对齐到至少一个 PCM 帧。")
    return {
        "start_frame": first,
        "end_frame": last,
        "frame_count": last - first,
        "start_seconds": _round(first / sample_rate),
        "end_seconds": _round(last / sample_rate),
        "duration_seconds": _round((last - first) / sample_rate),
    }


def _check_window_bound(metadata: dict[str, Any], aligned: dict[str, Any]) -> None:
    required = int(aligned["frame_count"]) * int(metadata["channels"]) * int(metadata["sample_width_bytes"])
    if required > MAX_WINDOW_PCM_BYTES:
        raise ValueError("选中的 PCM 窗口过大；请缩短范围或降低采样规格。")


def _decode_pcm(raw: bytes, sample_width: int, channels: int) -> np.ndarray:
    if not raw or len(raw) % (sample_width * channels):
        raise ValueError("PCM 分块不是完整帧。")
    if sample_width == 1:
        values = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0
        scale = 128.0
    elif sample_width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        scale = 32768.0
    elif sample_width == 3:
        triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        values = values.astype(np.float64)
        values[values >= 8_388_608] -= 16_777_216
        scale = 8_388_608.0
    elif sample_width == 4:
        values = np.frombuffer(raw, dtype="<i4").astype(np.float64)
        scale = 2_147_483_648.0
    else:
        raise ValueError("不支持的 PCM 位宽。")
    return (values / scale).reshape(-1, channels)


def _measure_pcm_range(metadata: dict[str, Any], aligned: dict[str, Any]) -> dict[str, Any]:
    _check_window_bound(metadata, aligned)
    channels = int(metadata["channels"])
    sample_width = int(metadata["sample_width_bytes"])
    block_align = channels * sample_width
    total_frames = int(aligned["frame_count"])
    threshold_dbfs = -60.0
    threshold = 10.0 ** (threshold_dbfs / 20.0)
    peak = 0.0
    square_sum = 0.0
    sample_count = 0
    silent_frames = 0
    frames_read = 0
    frames_per_chunk = max(1, min(65536, 1_048_576 // channels))
    try:
        with open(native_path(metadata["path"]), "rb") as raw_stream:
            with wave.open(raw_stream, "rb") as stream:
                stream.setpos(int(aligned["start_frame"]))
                while frames_read < total_frames:
                    take = min(frames_per_chunk, total_frames - frames_read)
                    raw = stream.readframes(take)
                    expected = take * block_align
                    if len(raw) != expected:
                        raise ValueError("WAV 在选定范围内被截断。")
                    samples = _decode_pcm(raw, sample_width, channels)
                    if samples.shape[0] != take:
                        raise ValueError("PCM 解码帧数不一致。")
                    magnitude = np.abs(samples)
                    peak = max(peak, float(np.max(magnitude, initial=0.0)))
                    square_sum += float(np.sum(samples * samples, dtype=np.float64))
                    sample_count += samples.size
                    silent_frames += int(np.count_nonzero(np.max(magnitude, axis=1) <= threshold))
                    frames_read += take
    except ValueError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"无法读取选定 WAV 范围：{exc}") from exc
    if frames_read != total_frames or sample_count != total_frames * channels:
        raise ValueError("WAV 范围读取不完整。")
    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    peak_dbfs = 20.0 * math.log10(peak) if peak > 0 else None
    rms_dbfs = 20.0 * math.log10(rms) if rms > 0 else None
    return {
        "status": "measured_facts_only",
        "window": {
            "start_frame": int(aligned["start_frame"]),
            "end_frame": int(aligned["end_frame"]),
            "frame_count": total_frames,
            "start_seconds": aligned["start_seconds"],
            "end_seconds": aligned["end_seconds"],
            "duration_seconds": aligned["duration_seconds"],
        },
        "frames_measured": frames_read,
        "samples_measured": sample_count,
        "peak_normalized": _round(peak),
        "peak_dbfs": _round(peak_dbfs, 6),
        "rms_normalized": _round(rms),
        "rms_dbfs": _round(rms_dbfs, 6),
        "silence_threshold_dbfs": threshold_dbfs,
        "silent_frames": silent_frames,
        "silent_frame_fraction": _round(silent_frames / total_frames if total_frames else 0.0),
        "semantic_interpretation": None,
    }


def inspect_reference_audio(path, start=None, end=None) -> dict:
    """Inspect PCM WAV headers and optionally measure a bounded time range."""
    source = _resolve_file(path, "参考音频")
    metadata = _wav_metadata(source)
    requested = _range(
        start,
        end,
        float(metadata["duration_seconds"]),
        require_both=False,
        analysis=True,
    )
    metrics = None
    effective = None
    if requested is not None:
        effective = _frame_alignment(
            requested,
            int(metadata["sample_rate_hz"]),
            int(metadata["frame_count"]),
        )
        metrics = _measure_pcm_range(metadata, effective)
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vwa-reference-audio-inspection",
        "source_path": str(source),
        "source_sha256": metadata["source_sha256"],
        "file_size_bytes": metadata["file_size_bytes"],
        "frame_count": metadata["frame_count"],
        "sample_rate_hz": metadata["sample_rate_hz"],
        "channels": metadata["channels"],
        "sample_width_bytes": metadata["sample_width_bytes"],
        "bits_per_sample": metadata["original_format"]["bits_per_sample"],
        "duration_seconds": _round(metadata["duration_seconds"]),
        "original_format": metadata["original_format"],
        "range": None if requested is None else {**requested, "effective": effective},
        "metrics": metrics,
        "header_analysis_performed": True,
        "frame_analysis_performed": metrics is not None,
        "inference_performed": False,
        "limitations": ["峰值、RMS 与静音比例是信号测量，不代表音高、语义或情绪。"],
    }
    if result["range"] is None:
        result["warnings"] = ["未请求范围；仅检查 WAV 头，未进行逐帧分析。"]
    else:
        result["warnings"] = ["范围已按 PCM 帧向外对齐；实际边界见 range.effective。"]
    return result


def _read_pcm_frames(metadata: dict[str, Any], aligned: dict[str, Any]) -> bytes:
    _check_window_bound(metadata, aligned)
    channels = int(metadata["channels"])
    width = int(metadata["sample_width_bytes"])
    block_align = channels * width
    expected = int(aligned["frame_count"]) * block_align
    try:
        with open(native_path(metadata["path"]), "rb") as raw_stream:
            with wave.open(raw_stream, "rb") as stream:
                stream.setpos(int(aligned["start_frame"]))
                data = stream.readframes(int(aligned["frame_count"]))
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"无法读取 PCM 摘录：{exc}") from exc
    if len(data) != expected:
        raise ValueError("PCM 摘录读取不完整。")
    return data


def _wav_bytes(pcm: bytes, metadata: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(int(metadata["channels"]))
        stream.setsampwidth(int(metadata["sample_width_bytes"]))
        stream.setframerate(int(metadata["sample_rate_hz"]))
        stream.setcomptype("NONE", "not compressed")
        stream.writeframes(pcm)
    return buffer.getvalue()


def _publish_exclusive(path: Path, data: bytes) -> None:
    """Create one complete file without replacing an existing path."""
    path = _io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(str(temporary), str(path))
            return
        except FileExistsError:
            return
        except OSError as link_error:
            if os.name != "nt":
                raise ValueError(f"无法原子发布摘录文件：{link_error}") from link_error
            try:
                if os.path.lexists(path):
                    return
            except (AttributeError, OSError) as exc:
                raise ValueError(f"无法安全检查发布目标：{exc}") from exc
        try:
            os.rename(str(temporary), str(path))
        except FileExistsError:
            return
        except OSError as exc:
            raise ValueError(f"无法通过 Windows 原子重命名发布摘录：{exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _existing_bytes(path: Path, label: str) -> bytes | None:
    native = _io_path(path)
    try:
        if os.path.islink(native):
            raise ValueError(f"{label} 是符号链接，拒绝信任可能指向别处的文件。")
        if not os.path.lexists(native):
            return None
        if not native.is_file():
            raise ValueError(f"{label} 已存在但不是普通文件。")
        return native.read_bytes()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"无法校验已有 {label}：{exc}") from exc
def create_audio_excerpt(path, start, end, output_root) -> dict:
    """Create an exact-frame PCM WAV excerpt while preserving the source."""
    source = _resolve_file(path, "参考音频")
    initial = _wav_metadata(source)
    requested = _range(
        start,
        end,
        float(initial["duration_seconds"]),
        require_both=True,
        analysis=True,
    )
    assert requested is not None
    effective = _frame_alignment(
        requested,
        int(initial["sample_rate_hz"]),
        int(initial["frame_count"]),
    )
    pcm = _read_pcm_frames(initial, effective)
    candidate_bytes = _wav_bytes(pcm, initial)
    candidate_sha = _sha256_bytes(candidate_bytes)
    if _sha256_file(source) != initial["source_sha256"]:
        raise ValueError("源 WAV 在读取期间发生变化；未发布摘录。")

    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": initial["source_sha256"],
        "requested": requested,
        "effective_frames": {
            "start_frame": int(effective["start_frame"]),
            "end_frame": int(effective["end_frame"]),
        },
        "original_format": initial["original_format"],
    }
    identity = _canonical_digest(identity_payload)
    root = _resolve_output_root(output_root)
    candidate_path = (root / f"vwa-reference-excerpt-{identity}.wav").resolve()
    receipt_path = (root / f"vwa-reference-excerpt-{identity}.json").resolve()
    if candidate_path == source or receipt_path == source:
        raise ValueError("摘录输出路径与源 WAV 冲突。")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": EXCERPT_KIND,
        "identity_sha256": identity,
        "source_path": str(source),
        "source_sha256": initial["source_sha256"],
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_sha,
        "receipt_path": str(receipt_path),
        "requested_range": requested,
        "effective_range": effective,
        "original_format": initial["original_format"],
        "frame_preservation": {
            "method": "direct PCM frame copy",
            "resampled": False,
            "pitch_changed": False,
            "frame_count": int(effective["frame_count"]),
        },
        "source_write_performed": False,
        "inference_performed": False,
        "warnings": ["范围边界按 PCM 帧向外对齐；未重采样或改变音高。"],
    }
    receipt = dict(payload)
    receipt["receipt_sha256"] = _canonical_digest(payload)
    receipt_bytes = _canonical_bytes(receipt, pretty=True)

    existing_candidate = _existing_bytes(candidate_path, "摘录候选")
    existing_receipt = _existing_bytes(receipt_path, "摘录 receipt")
    if existing_candidate is not None and existing_candidate != candidate_bytes:
        raise ValueError("摘录候选路径已有不同内容，拒绝覆盖或信任该碰撞。")
    if existing_receipt is not None and existing_receipt != receipt_bytes:
        raise ValueError("摘录 receipt 已有不同内容，拒绝覆盖或信任该碰撞。")
    if _sha256_file(source) != initial["source_sha256"]:
        raise ValueError("源 WAV 在发布前发生变化；未完成摘录。")
    if existing_candidate is None:
        _publish_exclusive(candidate_path, candidate_bytes)
    if existing_receipt is None:
        _publish_exclusive(receipt_path, receipt_bytes)
    if _existing_bytes(candidate_path, "摘录候选") != candidate_bytes:
        raise ValueError("摘录候选发布后字节校验失败。")
    if _existing_bytes(receipt_path, "摘录 receipt") != receipt_bytes:
        raise ValueError("摘录 receipt 发布后字节校验失败。")
    if _sha256_file(source) != initial["source_sha256"]:
        raise ValueError("源 WAV 在发布期间发生变化；拒绝返回不可复验的 receipt。")
    return receipt


def _validate_npz_archive(raw: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("pitch NPZ 含重复成员，拒绝歧义归档。")
            if set(names) != _PITCH_MEMBERS:
                missing = sorted(_PITCH_MEMBERS - set(names))
                extra = sorted(set(names) - _PITCH_MEMBERS)
                raise ValueError(f"pitch NPZ 成员不严格；缺少={missing}，额外={extra}。")
            total = 0
            shapes = {}
            for info in infos:
                if info.is_dir() or info.flag_bits & 1:
                    raise ValueError("pitch NPZ 含目录或加密成员。")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("pitch NPZ 使用了不支持的压缩方式。")
                if info.file_size > MAX_NPZ_MEMBER_BYTES:
                    raise ValueError("pitch NPZ 单个成员解压后过大。")
                total += info.file_size
                if total > MAX_NPZ_UNCOMPRESSED_BYTES:
                    raise ValueError("pitch NPZ 解压总量过大。")
                ratio = info.file_size / max(info.compress_size, 1)
                if info.file_size and (info.compress_size == 0 or ratio > MAX_NPZ_COMPRESSION_RATIO):
                    raise ValueError("pitch NPZ 压缩比异常，拒绝潜在 ZIP bomb。")
                # ZIP byte limits alone do not bound the allocation requested
                # by a forged NPY header. Check shape/dtype and exact payload
                # length before np.load can create any of these arrays.
                with archive.open(info,'r') as member:
                    version=np.lib.format.read_magic(member)
                    if version==(1,0):
                        shape,fortran,dtype=np.lib.format.read_array_header_1_0(member)
                    elif version==(2,0):
                        shape,fortran,dtype=np.lib.format.read_array_header_2_0(member)
                    else:
                        raise ValueError('pitch NPZ NPY header版本不支持。')
                    if dtype.hasobject or any(type(size) is not int or size<0 for size in shape):
                        raise ValueError('pitch NPZ NPY header含不安全dtype或维度。')
                    if info.filename=='midi_pitch.npy':
                        if len(shape)!=1 or shape[0]>MAX_PITCH_FRAMES or dtype.kind!='f' or dtype.itemsize not in (4,8):
                            raise ValueError('pitch NPY header帧数、维度或dtype超出限制。')
                    elif info.filename=='voiced_mask.npy':
                        if len(shape)!=1 or shape[0]>MAX_PITCH_FRAMES or dtype!=np.dtype(np.bool_):
                            raise ValueError('mask NPY header帧数、维度或dtype无效。')
                    elif shape or dtype.itemsize>8 or dtype.kind not in ('iu' if info.filename=='schema.npy' else 'iuf'):
                        raise ValueError('NPY标量header维度或dtype无效。')
                    count=math.prod(shape)
                    if member.tell()+count*dtype.itemsize!=info.file_size:
                        raise ValueError('NPY header声明大小与实际payload不一致。')
                    shapes[info.filename]=shape
            if shapes['midi_pitch.npy']!=shapes['voiced_mask.npy']:
                raise ValueError('pitch与mask的NPY header形状不一致。')
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"pitch NPZ 成员 CRC 无效：{bad_member}")
    except ValueError:
        raise
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError(f"pitch 文件不是有效 NPZ/ZIP：{exc}") from exc


def _read_pitch(path: Path) -> dict[str, Any]:
    try:
        size = _io_path(path).stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取 pitch NPZ 文件信息：{exc}") from exc
    if size > MAX_NPZ_FILE_BYTES:
        raise ValueError("pitch NPZ 文件过大。")
    try:
        raw = _io_path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"无法读取 pitch NPZ：{exc}") from exc
    pitch_sha = _sha256_bytes(raw)
    _validate_npz_archive(raw)
    try:
        with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
            if set(archive.files) != _PITCH_ARRAY_KEYS:
                raise ValueError("pitch NPZ 在 numpy 读取时成员集合发生变化。")
            schema_array = np.asarray(archive["schema"])
            step_array = np.asarray(archive["time_step"])
            pitch_array = np.asarray(archive["midi_pitch"])
            mask_array = np.asarray(archive["voiced_mask"])
    except ValueError:
        raise
    except (OSError, EOFError, TypeError) as exc:
        raise ValueError(f"pitch NPZ 无法安全读取：{exc}") from exc

    if schema_array.ndim != 0 or schema_array.dtype.kind not in "iu":
        raise ValueError("pitch NPZ schema 必须是整数标量。")
    schema = schema_array.item()
    if type(schema) is not int or schema != 1:
        raise ValueError("只支持 pitch NPZ schema 1。")
    if step_array.ndim != 0 or step_array.dtype.kind not in "iuf":
        raise ValueError("pitch NPZ time_step 必须是数值标量。")
    step = float(step_array.item())
    if not math.isfinite(step) or not 0.001 <= step <= 0.1:
        raise ValueError("pitch NPZ time_step 必须在 0.001 到 0.1 秒之间。")
    if pitch_array.ndim != 1 or pitch_array.dtype.kind != "f" or pitch_array.dtype.itemsize not in (4, 8):
        raise ValueError("midi_pitch 必须是一维 float32/float64 数组。")
    if mask_array.dtype != np.bool_ or mask_array.shape != pitch_array.shape:
        raise ValueError("voiced_mask 必须是形状相同的 bool 数组。")
    if pitch_array.size > MAX_PITCH_FRAMES:
        raise ValueError("pitch NPZ 帧数超出支持上限。")
    if pitch_array.nbytes + mask_array.nbytes > MAX_NPZ_UNCOMPRESSED_BYTES:
        raise ValueError("pitch 数组解压后过大。")
    mask = np.array(mask_array, dtype=np.bool_, copy=True)
    pitch = np.array(pitch_array, dtype=np.float64, copy=True)
    voiced = pitch[mask]
    if not np.isfinite(voiced).all():
        raise ValueError("voiced_mask 标记的 F0 帧必须全部有限。")
    if voiced.size and ((voiced < 0) | (voiced > 127)).any():
        raise ValueError("voiced_mask 标记的 F0 超出 MIDI 0..127。")
    return {
        "path": path,
        "source_sha256": pitch_sha,
        "schema_version": 1,
        "time_step_seconds": step,
        "pitch": pitch,
        "voiced_mask": mask,
        "frame_count": int(pitch.size),
        "voiced_frame_count": int(np.count_nonzero(mask)),
        "file_size_bytes": len(raw),
        "pitch_dtype": pitch_array.dtype.str,
        "mask_dtype": mask_array.dtype.str,
    }


def inspect_reference_pitch(path) -> dict:
    """Validate a schema-1 pitch NPZ and return bounded metadata only."""
    source = _resolve_file(path, "pitch NPZ")
    data = _read_pitch(source)
    mask = data["voiced_mask"]
    indices = np.flatnonzero(mask)
    voiced_values = data["pitch"][mask]
    voiced_index_range = None
    voiced_relative_time_range = None
    voiced_pitch_range = None
    if indices.size:
        first = int(indices[0])
        last = int(indices[-1])
        step = float(data["time_step_seconds"])
        voiced_index_range = {"first": first, "last_inclusive": last}
        voiced_relative_time_range = {
            "start_seconds_from_frame_zero": _round(first * step),
            "end_seconds_from_frame_zero_exclusive": _round((last + 1) * step),
        }
        voiced_pitch_range = {
            "minimum_midi": _round(float(np.min(voiced_values))),
            "maximum_midi": _round(float(np.max(voiced_values))),
        }
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vwa-reference-pitch-inspection",
        "format": "pitch-npz-schema1",
        "container": "npz",
        "source_path": str(source),
        "source_sha256": data["source_sha256"],
        "file_size_bytes": data["file_size_bytes"],
        "pitch_schema": data["schema_version"],
        "pitch_dtype": data["pitch_dtype"],
        "mask_dtype": data["mask_dtype"],
        "time_step_seconds": data["time_step_seconds"],
        "frame_count": data["frame_count"],
        "voiced_frame_count": data["voiced_frame_count"],
        "unvoiced_frame_count": data["frame_count"] - data["voiced_frame_count"],
        "voiced_frame_index_range": voiced_index_range,
        "voiced_relative_time_range": voiced_relative_time_range,
        "voiced_midi_pitch_range": voiced_pitch_range,
        "time_origin_seconds": None,
        "arrays_returned": False,
        "midi_read": False,
        "inference_performed": False,
        "synthesis_performed": False,
        "transcription_performed": False,
        "provider_access_performed": False,
        "warnings": [
            "时间范围仅相对 F0 第 0 帧；真实对齐时间仍须由调用方显式提供 time_origin。",
            "元数据校验不证明该 NPZ 来自当前 MIDI 或任何推理任务。",
        ],
    }
    result["inspection_sha256"] = _canonical_digest(result)
    return result


def _track_filter(score: dict[str, Any], track_indices: Any) -> list[int] | None:
    if track_indices is None:
        return None
    if isinstance(track_indices, (str, bytes)) or not isinstance(track_indices, (list, tuple)):
        raise ValueError("track_indices 必须是整数数组或 null。")
    if not track_indices:
        raise ValueError("track_indices 不能是空数组；全部轨道请传 null。")
    values = [_strict_int(value, "track_indices[]") for value in track_indices]
    if len(values) != len(set(values)):
        raise ValueError("track_indices 不能重复。")
    existing = {int(track["track_index"]) for track in score["tracks"]}
    unknown = sorted(set(values) - existing)
    if unknown:
        raise ValueError(f"未知 track_indices：{unknown}。")
    return sorted(values)


def _overlap_map(notes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    timed = []
    for note in notes:
        onset = note.get("onset_seconds")
        end = note.get("end_seconds")
        if type(onset) in (int, float) and type(end) in (int, float) and end > onset:
            timed.append((float(onset), float(end), str(note["note_id"])))
    timed.sort(key=lambda row: (row[0], row[1], row[2]))
    active: list[tuple[float, str]] = []
    overlaps: dict[str, set[str]] = {}
    for onset, end, note_id in timed:
        while active and active[0][0] <= onset + 1e-12:
            heapq.heappop(active)
        for _, peer_id in active:
            overlaps.setdefault(note_id, set()).add(peer_id)
            overlaps.setdefault(peer_id, set()).add(note_id)
        heapq.heappush(active, (end, note_id))
    return {
        note_id: {
            "count": len(peers),
            "note_ids": sorted(peers)[:20],
            "truncated": len(peers) > 20,
        }
        for note_id, peers in overlaps.items()
    }


def _ceil_grid(value: float) -> int:
    return int(math.ceil(value - 1e-10))


def _hint(
    note: dict[str, Any],
    *,
    kind: str,
    score_sha: str,
    pitch_sha: str,
    observed: dict[str, Any],
    frame_window: dict[str, int],
) -> dict[str, Any]:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "note_id": note["note_id"],
        "score_sha256": score_sha,
        "pitch_sha256": pitch_sha,
        "frame_start": frame_window["start_frame"],
        "frame_end": frame_window["end_frame"],
    }
    hint_id = "refhint-" + _canonical_digest(identity)[:24]
    return {
        **identity,
        "hint_id": hint_id,
        "severity": "warning" if kind == "reference_octave_discrepancy" else "info",
        "observed": observed,
        "automatic_patch": False,
        "patch_plan": None,
        "recommended_action": "人工核对参考来源、音符归属或时间偏移；无需自动修改 MIDI。",
        "confidence": "conditional",
    }


def _review_note(
    note: dict[str, Any],
    pitch_data: dict[str, Any],
    origin: float,
    overlaps: dict[str, dict[str, Any]],
    score_sha: str,
    pitch_sha: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pitch = pitch_data["pitch"]
    mask = pitch_data["voiced_mask"]
    step = float(pitch_data["time_step_seconds"])
    overlap = overlaps.get(str(note["note_id"]))
    base = {
        "note_id": note["note_id"],
        "track_index": int(note["track_index"]),
        "channel": int(note["channel"]),
        "pitch": int(note["pitch"]),
        "lyric": note.get("lyric"),
        "score_onset_seconds": note.get("onset_seconds"),
        "score_end_seconds": note.get("end_seconds"),
        "score_duration_seconds": note.get("duration_seconds"),
        "mapping_status": "ambiguous_polyphonic_overlap" if overlap else "single_interval_association",
    }
    onset = note.get("onset_seconds")
    end = note.get("end_seconds")
    if type(onset) not in (int, float) or type(end) not in (int, float) or end <= onset:
        row = {
            **base,
            "status": "ambiguous" if overlap else "unknown",
            "reason_codes": ["polyphonic_overlap"] if overlap else ["unclosed_or_invalid_note_interval"],
            "frames": None,
            "coverage": None,
            "reference": None,
            "hint_ids": [],
        }
        if overlap:
            row["overlap"] = overlap
        return row, []

    raw_start = _ceil_grid((float(onset) - origin) / step)
    raw_end = _ceil_grid((float(end) - origin) / step)
    expected_frames = max(0, raw_end - raw_start)
    start = max(0, raw_start)
    end_index = min(int(pitch.size), raw_end)
    frame_window = {
        "start_frame": raw_start,
        "end_frame": raw_end,
        "available_start_frame": start,
        "available_end_frame": end_index,
    }
    segment_mask = mask[start:end_index] if start < end_index else np.zeros(0, dtype=np.bool_)
    voiced_values = pitch[start:end_index][segment_mask] if start < end_index else np.zeros(0, dtype=np.float64)
    voiced_frames = int(voiced_values.size)
    coverage = voiced_frames / expected_frames if expected_frames else 0.0
    reason_codes: list[str] = []
    hints: list[dict[str, Any]] = []
    reference = {
        "voiced_frames": voiced_frames,
        "unvoiced_or_missing_frames": max(0, expected_frames - voiced_frames),
        "expected_note_frames": expected_frames,
    }
    median = None
    central_span = None
    mad = None
    cents = None
    if voiced_frames:
        median = float(np.median(voiced_values))
        p10, p90 = np.percentile(voiced_values, [10.0, 90.0])
        central_span = float(p90 - p10)
        mad = float(np.median(np.abs(voiced_values - median)))
        cents = (median - float(note["pitch"])) * 100.0
        reference.update({
            "median_midi_pitch": _round(median),
            "cents_from_score": _round(cents, 6),
            "central_10_90_span_semitones": _round(central_span),
            "median_absolute_deviation_semitones": _round(mad),
        })
    else:
        reference.update({
            "median_midi_pitch": None,
            "cents_from_score": None,
            "central_10_90_span_semitones": None,
            "median_absolute_deviation_semitones": None,
        })

    if expected_frames < MIN_VOICED_FRAMES:
        reason_codes.append("short_note_window")
    if voiced_frames < MIN_VOICED_FRAMES:
        reason_codes.append("insufficient_voiced_frames")
    if coverage < MIN_COVERAGE:
        reason_codes.append("insufficient_coverage")
    unstable = (
        voiced_frames >= MIN_VOICED_FRAMES
        and central_span is not None
        and central_span > MAX_STABLE_CENTRAL_SPAN_SEMITONES
    )
    if unstable:
        reason_codes.append("unstable_reference_pitch")

    if voiced_frames >= MIN_VOICED_FRAMES and central_span is not None and central_span >= MAX_STABLE_CENTRAL_SPAN_SEMITONES:
        observed = {
            "central_10_90_span_semitones": _round(central_span),
            "median_midi_pitch": _round(median),
            "voiced_frames": voiced_frames,
        }
        hints.append(_hint(
            note,
            kind="reference_glide_or_wide_pitch_motion",
            score_sha=score_sha,
            pitch_sha=pitch_sha,
            observed=observed,
            frame_window=frame_window,
        ))

    if overlap:
        status = "ambiguous"
        reason_codes.insert(0, "polyphonic_overlap")
    elif reason_codes:
        status = "unknown"
    else:
        status = "reviewed"
        if cents is not None and abs(cents) >= OFFSET_HINT_CENTS:
            octave = int(round(cents / 1200.0))
            residual = cents - octave * 1200.0
            kind = (
                "reference_octave_discrepancy"
                if octave and abs(residual) <= OCTAVE_TOLERANCE_CENTS
                else "reference_pitch_center_offset"
            )
            observed = {
                "median_midi_pitch": _round(median),
                "cents_from_score": _round(cents, 6),
                "coverage": _round(coverage),
            }
            hints.append(_hint(
                note,
                kind=kind,
                score_sha=score_sha,
                pitch_sha=pitch_sha,
                observed=observed,
                frame_window=frame_window,
            ))

    row = {
        **base,
        "status": status,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "frames": frame_window,
        "coverage": _round(coverage),
        "reference": reference,
        "hint_ids": [hint["hint_id"] for hint in hints],
    }
    if overlap:
        row["overlap"] = overlap
    return row, hints


def review_reference_pitch(
    score_path,
    pitch_path,
    track_indices=None,
    time_origin=0.0,
    limit=100,
    offset=0,
) -> dict:
    """Compare schema-1 measured F0 with MIDI notes using explicit time mapping."""
    score_source = _resolve_file(score_path, "乐谱")
    pitch_source = _resolve_file(pitch_path, "pitch NPZ")
    score = read_score(score_source)
    if score.get("format") != "midi":
        raise ValueError("reference pitch review 只接受 MIDI 乐谱。")
    if any(issue.get("code") == "conflicting_tempo_at_tick" for issue in score["integrity_issues"]):
        raise ValueError("MIDI 在同一 tick 存在冲突 tempo，秒数映射有歧义，拒绝音高复核。")
    pitch_data = _read_pitch(pitch_source)
    tracks = _track_filter(score, track_indices)
    origin = _finite_number(time_origin, "time_origin")
    if abs(origin) > 1_000_000_000:
        raise ValueError("time_origin 超出支持范围。")
    page_limit = _strict_int(limit, "limit", minimum=1, maximum=MAX_PAGE_SIZE)
    page_offset = _strict_int(offset, "offset", minimum=0, maximum=10_000_000)
    selected = [
        note for note in score["notes"]
        if tracks is None or int(note["track_index"]) in tracks
    ]
    selected.sort(key=lambda note: (
        math.inf if note.get("onset_seconds") is None else float(note["onset_seconds"]),
        int(note["track_index"]),
        int(note["pitch"]),
        str(note["note_id"]),
    ))
    overlaps = _overlap_map(score["notes"])
    reviewed_rows = []
    all_hints = []
    for note in selected:
        row, hints = _review_note(
            note,
            pitch_data,
            origin,
            overlaps,
            str(score["source_sha256"]),
            str(pitch_data["source_sha256"]),
        )
        reviewed_rows.append(row)
        all_hints.extend(hints)
    page_rows = reviewed_rows[page_offset:page_offset + page_limit]
    page_note_ids = {row["note_id"] for row in page_rows}
    page_hints = [hint for hint in all_hints if hint["note_id"] in page_note_ids]
    status_counts = Counter(row["status"] for row in reviewed_rows)
    reason_counts = Counter(reason for row in reviewed_rows for reason in row["reason_codes"])
    total = len(reviewed_rows)
    returned = len(page_rows)
    if not total or status_counts["reviewed"] == 0:
        status = "unknown"
    elif status_counts["unknown"] or status_counts["ambiguous"]:
        status = "partial"
    else:
        status = "reviewed"
    next_offset = page_offset + returned if page_offset + returned < total else None
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vwa-reference-pitch-review",
        "status": status,
        "score": {
            "path": str(score_source),
            "sha256": score["source_sha256"],
            "format": "midi",
            "ticks_per_beat": score["ticks_per_beat"],
            "tempo_events": score["timing"]["tempo_events"],
        },
        "pitch": {
            "path": str(pitch_source),
            "sha256": pitch_data["source_sha256"],
            "schema_version": 1,
            "pitch_dtype": pitch_data["pitch_dtype"],
            "mask_dtype": pitch_data["mask_dtype"],
            "time_step_seconds": pitch_data["time_step_seconds"],
            "time_origin_seconds": _round(origin),
            "frame_count": pitch_data["frame_count"],
            "voiced_frame_count": pitch_data["voiced_frame_count"],
            "provenance": {
                "status": "unverified_source",
                "reason": "API 未提供 pitch 来源 receipt；不能声称该缓存来自当前 MIDI 或某次验证过的推理。",
            },
        },
        "tracks": tracks,
        "time_mapping": {
            "frame_zero_seconds_in_score": _round(origin),
            "frame_seconds": pitch_data["time_step_seconds"],
            "selection": "half-open [onset, end), clipped to available pitch frames",
            "score_seconds_basis": "actual MIDI set_tempo playback map",
        },
        "thresholds": {
            "minimum_voiced_frames": MIN_VOICED_FRAMES,
            "minimum_coverage": MIN_COVERAGE,
            "maximum_stable_central_10_90_span_semitones": MAX_STABLE_CENTRAL_SPAN_SEMITONES,
            "offset_hint_cents": OFFSET_HINT_CENTS,
            "octave_tolerance_cents": OCTAVE_TOLERANCE_CENTS,
        },
        "summary": {
            "notes_selected": total,
            "reviewed": status_counts["reviewed"],
            "unknown": status_counts["unknown"],
            "ambiguous": status_counts["ambiguous"],
            "hints": len(all_hints),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "total": total,
            "returned": returned,
            "next_offset": next_offset,
        },
        "notes": page_rows,
        "hints": page_hints,
        "warnings": [
            "pitch provenance 未验证；除非另行提供并绑定来源 receipt，否则保持 unverified_source。",
            "单路 F0 不能可靠拆分和弦或复调；重叠区间只记录歧义，不伪造音符关联。",
            "提示来自稳健中位数与覆盖统计，不会自动修复或改写乐谱。",
        ],
        "inference_performed": False,
        "synthesis_performed": False,
        "transcription_performed": False,
        "provider_access_performed": False,
        "patch_applied": False,
        "original_hashes": {
            "score_sha256": score["source_sha256"],
            "pitch_sha256": pitch_data["source_sha256"],
        },
    }
    result["review_sha256"] = _canonical_digest(result)
    return result

SUSTAINED_OFFSET_CATEGORIES = {
    "warning": "warnings",
    "unevaluated": "unevaluated_notes",
    "ambiguous": "not_assessed_intervals",
}
MAX_SUSTAINED_OFFSET_PAGE_SIZE = 100
MAX_SUSTAINED_RELATED_NOTES = 20
MAX_SUSTAINED_TEMPO_EVENTS = 32
MAX_SUSTAINED_TEXT = 256


def _bounded_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= MAX_SUSTAINED_TEXT else text[:MAX_SUSTAINED_TEXT]


def _sustained_revision(
    score_sha256: str,
    pitch_sha256: str,
    track_index: int,
    time_origin: float,
    analysis: dict[str, Any],
    code_revision: str,
) -> str:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vwa-sustained-pitch-offset-revision",
        "code_revision": code_revision,
        "score_sha256": score_sha256,
        "pitch_sha256": pitch_sha256,
        "track_index": track_index,
        "time_origin": float(time_origin),
        "algorithm": analysis.get("algorithm"),
        "threshold_cents": analysis.get("threshold_cents"),
        "minimum_duration_seconds": analysis.get("minimum_duration_seconds"),
        "minimum_frames": analysis.get("minimum_frames"),
        "edge_guard_seconds": analysis.get("edge_guard_seconds"),
        "warning_count": analysis.get("warning_count"),
        "unevaluated_count": analysis.get("unevaluated_count"),
        "ambiguous_interval_count": analysis.get("not_assessed_interval_count"),
    }
    return "posrev-" + _canonical_digest(identity)


def _sustained_page_items(
    items: list[dict[str, Any]],
    category: str,
    descriptors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        row = dict(item)
        row["category"] = category
        note = row.get("note")
        if isinstance(note, dict):
            bounded_note = dict(note)
            note_index = bounded_note.get("note_index")
            descriptor = (descriptors[note_index]
                          if type(note_index) is int and 0 <= note_index < len(descriptors)
                          else None)
            if descriptor is not None and descriptor["note_id"] == bounded_note.get("note_id"):
                for field in (
                    "channel", "velocity", "duration_tick", "lyric_event_index",
                    "pairing_ambiguous",
                ):
                    bounded_note[field] = descriptor.get(field)
                full_lyric = descriptor.get("lyric")
                bounded_note["lyric"] = _bounded_optional_text(full_lyric)
                bounded_note["lyric_truncated"] = (
                    full_lyric is not None and len(str(full_lyric)) > MAX_SUSTAINED_TEXT
                )
            else:
                bounded_note["lyric"] = _bounded_optional_text(bounded_note.get("lyric"))
            row["note"] = bounded_note
            row["note_id"] = bounded_note.get("note_id")
        elif category == "ambiguous":
            indices = row.pop("note_indices", [])
            indices = indices if isinstance(indices, list) else []
            safe_indices = [index for index in indices if type(index) is int]
            related = []
            for index in safe_indices[:MAX_SUSTAINED_RELATED_NOTES]:
                if 0 <= index < len(descriptors):
                    descriptor = descriptors[index]
                    related.append({
                        "note_id": descriptor["note_id"],
                        "track_index": descriptor["vocal_track_index"],
                        "note_index": descriptor["note_index"],
                        "channel": descriptor.get("channel"),
                        "pitch": descriptor["center_midi"],
                        "start_tick": descriptor["start_tick"],
                        "end_tick": descriptor["end_tick"],
                        "lyric": _bounded_optional_text(descriptor.get("lyric")),
                        "lyric_event_index": descriptor.get("lyric_event_index"),
                        "pairing_ambiguous": descriptor.get("pairing_ambiguous"),
                    })
            row["note_indices"] = safe_indices[:MAX_SUSTAINED_RELATED_NOTES]
            row["note_ids"] = [note["note_id"] for note in related]
            row["notes"] = related
            row["related_notes_truncated"] = len(safe_indices) > len(related)
        rows.append(row)
    return rows


def pitch_offset_review(
    score_path,
    pitch_path,
    track_index,
    time_origin,
    category,
    limit=20,
    offset=0,
    expected_revision=None,
) -> dict:
    """Read one bounded page of sustained pitch-offset evidence.

    The caller explicitly maps one MIDI track to the measured F0 sidecar.  The
    shared pitch-accuracy implementation computes note ownership and diagnostic
    pages.  This adapter only validates sources, supplies complete editable MIDI
    note IDs, and reports stable revision metadata; it never edits a score or
    decides whether the note or measured F0 curve is true.
    """
    from pitch_accuracy import analyze_sustained_pitch_offsets, assign_note_owners

    code_root = Path(__file__).resolve().parent
    code_files = ('agent_reference.py', 'pitch_accuracy.py', 'agent_score.py')
    code_revision = _canonical_digest({name: _sha256_file(code_root / name) for name in code_files})
    score_source = _resolve_file(score_path, "乐谱")
    pitch_source = _resolve_file(pitch_path, "pitch NPZ")
    score = read_score(score_source)
    if score.get("format") != "midi":
        raise ValueError("pitch offset review 只接受 MIDI 乐谱。")
    if any(issue.get("code") == "conflicting_tempo_at_tick"
           for issue in score["integrity_issues"]):
        raise ValueError("MIDI 在同一 tick 存在冲突 tempo，秒数映射有歧义，拒绝音高偏移核查。")

    pitch_data = _read_pitch(pitch_source)
    selected_track_index = _strict_int(track_index, "track_index", minimum=0)
    origin = _finite_number(time_origin, "time_origin")
    if abs(origin) > 1_000_000_000:
        raise ValueError("time_origin 超出支持范围。")
    if not isinstance(category, str) or category not in SUSTAINED_OFFSET_CATEGORIES:
        raise ValueError("category 必须是 warning、unevaluated 或 ambiguous。")
    page_limit = _strict_int(
        limit, "limit", minimum=1, maximum=MAX_SUSTAINED_OFFSET_PAGE_SIZE
    )
    page_offset = _strict_int(offset, "offset", minimum=0, maximum=10_000_000)
    if expected_revision is not None:
        if (not isinstance(expected_revision, str) or not expected_revision
                or len(expected_revision) > MAX_SUSTAINED_TEXT):
            raise ValueError("expected_revision 必须是非空短字符串或 null。")
    if page_offset and expected_revision is None:
        raise ValueError("非零 offset 需要上一页的 expected_revision，请从第 0 页读取。")

    tracks = [
        track for track in score["tracks"]
        if int(track["track_index"]) == selected_track_index
    ]
    if not tracks:
        raise ValueError(f"MIDI 中不存在 track_index={selected_track_index}。")
    track = tracks[0]
    selected_notes = [
        note for note in score["notes"]
        if int(note["track_index"]) == selected_track_index
    ]
    if not selected_notes or int(track.get("note_count", 0)) <= 0:
        raise ValueError(f"MIDI 轨道 {selected_track_index} 没有可核查音符。")
    selected_notes.sort(key=lambda note: (
        int(note["onset_tick"]), int(note["pitch"]), str(note["note_id"])
    ))

    intervals = []
    descriptors = []
    for note_index, note in enumerate(selected_notes):
        onset = note.get("onset_seconds")
        end = note.get("end_seconds")
        center = note.get("pitch")
        if (type(onset) not in (int, float) or type(end) not in (int, float)
                or type(center) not in (int, float) or end <= onset):
            raise ValueError(
                f"轨道 {selected_track_index} 的音符时轴或音高无效，拒绝生成不完整证据。"
            )
        note_id = note.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise ValueError("MIDI 音符缺少可用于编辑的完整 note_id。")
        intervals.append((float(onset), float(end)))
        descriptors.append({
            "vocal_track_index": selected_track_index,
            "note_index": note_index,
            "note_id": note_id,
            "start_tick": note.get("onset_tick"),
            "end_tick": note.get("end_tick"),
            "start_seconds": float(onset),
            "end_seconds": float(end),
            "center_midi": float(center),
            "center_cents": int(round(float(center) * 100.0)),
            "channel": note.get("channel"),
            "velocity": note.get("velocity"),
            "duration_tick": note.get("duration_tick"),
            "lyric": note.get("lyric"),
            "lyric_event_index": note.get("lyric_event_index"),
            "pairing_ambiguous": bool(note.get("pairing_ambiguous")),
        })

    step = float(pitch_data["time_step_seconds"])
    times = np.arange(int(pitch_data["frame_count"]), dtype=np.float64) * step + origin
    owner = assign_note_owners(times, intervals)
    selected_property = SUSTAINED_OFFSET_CATEGORIES[category]
    pagination = {
        "warnings": {"offset": 0, "limit": 1},
        "unevaluated_notes": {"offset": 0, "limit": 1},
        "not_assessed_intervals": {"offset": 0, "limit": 1},
    }
    pagination[selected_property] = {"offset": page_offset, "limit": page_limit}
    analysis = analyze_sustained_pitch_offsets(
        pitch_data["pitch"],
        pitch_data["voiced_mask"],
        owner,
        step,
        intervals,
        descriptors,
        origin,
        source_sha256=pitch_data["source_sha256"],
        vocal_track_index=selected_track_index,
        diagnostic_pagination=pagination,
    )

    score_sha256 = str(score["source_sha256"])
    pitch_sha256 = str(pitch_data["source_sha256"])
    if _sha256_file(score_source) != score_sha256:
        raise ValueError("MIDI 源在音高偏移核查期间发生变化，请重新读取。")
    if _sha256_file(pitch_source) != pitch_sha256:
        raise ValueError("pitch NPZ 源在音高偏移核查期间发生变化，请重新读取。")

    if code_revision != _canonical_digest({name: _sha256_file(code_root / name) for name in code_files}):
        raise ValueError("音高诊断代码在核查期间发生变化，请重新读取。")
    revision = _sustained_revision(
        score_sha256, pitch_sha256, selected_track_index, origin, analysis, code_revision
    )
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("音高偏移证据已变化；请从 offset 0 重新读取，并使用新的 revision。")

    page_metadata = analysis["diagnostic_pagination"][selected_property]
    items = _sustained_page_items(
        list(analysis.get(selected_property, [])), category, descriptors
    )
    if len(items) != page_metadata["returned"]:
        raise ValueError("共享音高偏移分页返回了不一致的条目数量。")

    tempo_events = list((score.get("timing") or {}).get("tempo_events") or [])
    bounded_tempos = [dict(row) for row in tempo_events[:MAX_SUSTAINED_TEMPO_EVENTS]]
    track_name = _bounded_optional_text(track.get("name"))
    instrument_name = _bounded_optional_text(track.get("instrument_name"))
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vwa-sustained-pitch-offset-review",
        "status": analysis["status"],
        "category": category,
        "items": items,
        "score_sha256": score_sha256,
        "pitch_sha256": pitch_sha256,
        "revision": revision,
        "code_revision": code_revision,
        "next_offset": page_metadata["next_offset"],
        "score": {
            "path": str(score_source),
            "sha256": score_sha256,
            "format": "midi",
            "ticks_per_beat": score["ticks_per_beat"],
            "tempo_event_count": len(tempo_events),
            "tempo_events": bounded_tempos,
            "tempo_events_truncated": len(tempo_events) > len(bounded_tempos),
        },
        "pitch": {
            "path": str(pitch_source),
            "sha256": pitch_sha256,
            "schema_version": 1,
            "time_step_seconds": step,
            "frame_count": pitch_data["frame_count"],
            "voiced_frame_count": pitch_data["voiced_frame_count"],
            "provenance": {
                "status": "unverified_source",
                "mapping_status": "caller_supplied_single_track",
            },
        },
        "track": {
            "track_index": selected_track_index,
            "name": track_name,
            "instrument_name": instrument_name,
            "note_count": len(selected_notes),
            "note_id_scheme": "agent_score.full_source_note_id",
            "ownership": "selected_track_only; overlap_within_track_is_ambiguous",
        },
        "time_mapping": {
            "frame_zero_seconds_in_score": _round(origin),
            "frame_seconds": step,
            "selection": "half-open [onset, end)",
            "score_seconds_basis": "actual MIDI set_tempo playback map",
        },
        "summary": {
            "warning_count": int(analysis["warning_count"]),
            "unevaluated_count": int(analysis["unevaluated_count"]),
            "ambiguous_interval_count": int(
                analysis.get("not_assessed_interval_count", 0)
            ),
            "evaluated_notes": int(analysis["evaluated_notes"]),
            "clear_notes": int(analysis["clear_notes"]),
            "ambiguous_note_frames": int(analysis["ambiguous_note_frames"]),
            "edge_guard_suppressed_frames": int(analysis["edge_guard_suppressed_frames"]),
            "short_offset_candidates": int(analysis["short_offset_candidates"]),
            "threshold_cents": analysis["threshold_cents"],
            "minimum_duration_seconds": analysis["minimum_duration_seconds"],
            "minimum_frames": analysis["minimum_frames"],
            "edge_guard_seconds": analysis["edge_guard_seconds"],
        },
        "pagination": {
            "category": category,
            **page_metadata,
        },
        "truth_determined": False,
        "automatic_change": False,
        "patch_applied": False,
        "inference_performed": False,
        "synthesis_performed": False,
        "transcription_performed": False,
        "provider_access_performed": False,
        "warnings": [
            "这是只读复核证据；持续偏离可能来自音符中心或 F0 曲线，不能由本接口判定真值。",
            "调用方声明的 MIDI track_index 是唯一轨道映射；pitch NPZ 来源仍未验证。",
            "重叠区间只返回歧义证据；完整 note_id 可在确认后用于既有 MIDI 编辑计划。",
        ],
    }
    result["review_sha256"] = _canonical_digest(result)
    return result
