"""Safe media handling for portable SVP projects.

Media paths in export plans are internal, control-layer authorized inputs. This
module never presents those paths as a public API. It copies audio beside an
SVP, rewrites the project reference to a relative filename, and returns evidence
that can be audited without loading a whole audio file into memory.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from common import native_path


class MediaError(ValueError):
    component = "agent_media"
    retryable = False
    remediation = ["vwa_exports_plan", "vwa_artifacts_inspect"]

    def __init__(self, code: str, message: str, *, details: Any = None,
                 retryable: bool | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        if retryable is not None:
            self.retryable = retryable


_CHUNK = 1024 * 1024
_WAV_SUFFIXES = frozenset({".wav", ".wave"})
_MEDIA_SUFFIXES = frozenset({".wav", ".wave", ".flac", ".mp3", ".ogg", ".opus"})


def _io_path(path: Any) -> Path:
    return Path(native_path(path))


def _resolved_file(value: Any, label: str) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, bytes, os.PathLike)):
        raise MediaError("media_path_invalid", label + "必须是路径。")
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise MediaError("media_path_invalid", label + "无效。", details={"path": str(value)}) from error
    if not _io_path(path).is_file() or _io_path(path).is_symlink():
        raise MediaError("media_missing", label + "不存在或不是普通文件。", details={"path": str(path)})
    return path


def _root_paths(values: Sequence[Any] | None, defaults: Sequence[Any]) -> list[Path]:
    source = defaults if values is None else values
    if not isinstance(source, (list, tuple)) or not source:
        raise MediaError("media_roots_invalid", "allowed_roots 必须是非空目录列表。")
    roots: list[Path] = []
    for value in source:
        try:
            roots.append(Path(value).expanduser().resolve())
        except (OSError, TypeError, ValueError) as error:
            raise MediaError("media_roots_invalid", "允许目录无效。") from error
    return roots


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_reparse(path: Path, root: Path) -> None:
    """Reject links/reparse points from root through path on Windows."""
    root = root.resolve()
    current = path
    while True:
        try:
            attributes = _io_path(current).lstat()
        except FileNotFoundError:
            attributes = None
        except OSError as error:
            raise MediaError("media_path_unreadable", "无法检查媒体路径。", details={"path": str(current)}) from error
        if attributes is not None:
            flags = getattr(attributes, "st_file_attributes", 0)
            if _io_path(current).is_symlink() or flags & 0x400:
                raise MediaError("media_path_unsafe", "媒体路径包含链接或重解析点。", details={"path": str(current)})
        if current == root:
            break
        parent = current.parent
        if parent == current:
            raise MediaError("media_path_outside_roots", "媒体路径越出允许目录。", details={"path": str(path)})
        current = parent


def _resolved_file_target(value: Any) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, bytes, os.PathLike)):
        raise MediaError("media_path_invalid", "目标伴奏路径无效。")
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise MediaError("media_path_invalid", "目标伴奏路径无效。") from error
    if not path.name:
        raise MediaError("media_path_invalid", "目标伴奏路径缺少文件名。")
    return path


def _authorized_path(value: Any, roots: Sequence[Path], label: str) -> Path:
    path = _resolved_file(value, label)
    if not any(_inside(path, root) for root in roots):
        raise MediaError(
            "media_path_outside_roots",
            label + "不在调用方允许的目录中。",
            details={"path": str(path), "allowed_roots": [str(root) for root in roots]},
        )
    for root in roots:
        if _inside(path, root):
            _reject_reparse(path, root)
            break
    return path


def file_sha256(path: Any, *, cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Hash a large file in bounded blocks and reject mid-read changes."""
    source = _resolved_file(path, "媒体文件")
    try:
        before = _io_path(source).stat()
        digest = hashlib.sha256()
        with _io_path(source).open("rb") as stream:
            for block in iter(lambda: stream.read(_CHUNK), b""):
                if cancel is not None and bool(cancel()):
                    raise InterruptedError("已取消媒体读取。")
                digest.update(block)
        after = _io_path(source).stat()
    except InterruptedError:
        raise
    except OSError as error:
        raise MediaError("media_unreadable", "无法读取媒体文件。", details={"path": str(source)}) from error
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise MediaError("media_changed", "媒体文件在读取期间发生变化。", retryable=True,
                         details={"path": str(source)})
    return {"sha256": digest.hexdigest(), "bytes": after.st_size, "path": str(source)}


def _wav_info(path: Path) -> dict[str, Any]:
    """Read RIFF/WAVE headers without decoding the audio payload."""
    try:
        with _io_path(path).open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12 or header[:4] not in (b"RIFF", b"RF64") or header[8:12] != b"WAVE":
                raise MediaError("media_format_unsupported", "伴奏不是可识别的 RIFF/WAVE 文件。")
            fmt: tuple[int, int, int, int, int, int] | None = None
            data_size: int | None = None
            while True:
                chunk_header = stream.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id, size = struct.unpack("<4sI", chunk_header)
                if chunk_id == b"fmt ":
                    payload = stream.read(size)
                    if len(payload) != size or size < 16:
                        raise MediaError("media_format_invalid", "WAV fmt 区块不完整。")
                    fmt = struct.unpack_from("<HHIIHH", payload)
                    if fmt[0] == 0xFFFE and size >= 40:
                        fmt = (struct.unpack_from("<H", payload, 24)[0], *fmt[1:])
                    if size & 1:
                        stream.seek(1, os.SEEK_CUR)
                elif chunk_id == b"data":
                    data_size = size
                    if fmt is not None:
                        break
                    stream.seek(size + (size & 1), os.SEEK_CUR)
                else:
                    stream.seek(size + (size & 1), os.SEEK_CUR)
            if fmt is None or data_size is None:
                raise MediaError("media_format_invalid", "WAV 缺少 fmt 或 data 区块。")
            audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
            if audio_format not in (1, 3) or not channels or not sample_rate or not byte_rate or not block_align:
                raise MediaError("media_format_unsupported", f"不支持的 WAV 编码：{audio_format}。")
            if data_size <= 0 or data_size == 0xFFFFFFFF:
                raise MediaError("media_format_unsupported", "WAV data 长度缺失；首版不接受 RF64 流式长度。")
            duration = data_size / byte_rate
            if not math.isfinite(duration) or duration <= 0:
                raise MediaError("media_duration_invalid", "WAV 时长无效。")
            return {
                "format": "wav", "audio_format": audio_format, "channels": channels,
                "sample_rate": sample_rate, "block_align": block_align, "bits": bits,
                "frames": data_size // block_align, "duration_seconds": duration,
            }
    except MediaError:
        raise
    except (OSError, struct.error, OverflowError) as error:
        raise MediaError("media_format_invalid", "无法解析 WAV 音频头。", details={"path": str(path)}) from error


def audio_info(path: Any) -> dict[str, Any]:
    source = _resolved_file(path, "媒体文件")
    suffix = source.suffix.lower()
    if suffix not in _MEDIA_SUFFIXES:
        raise MediaError("media_format_unsupported", "不支持的伴奏格式：" + (suffix or "(无扩展名)"),
                         details={"path": str(source)})
    if suffix in _WAV_SUFFIXES:
        info = _wav_info(source)
    else:
        try:
            import soundfile as sf
            value = sf.info(str(source))
        except (ImportError, RuntimeError, OSError, ValueError) as error:
            raise MediaError(
                "media_duration_unavailable",
                "该音频格式需要本地 soundfile 元数据支持才能验证时长。",
                details={"path": str(source), "format": suffix[1:]},
            ) from error
        info = {
            "format": suffix[1:], "channels": int(value.channels),
            "sample_rate": int(value.samplerate), "frames": int(value.frames),
            "duration_seconds": float(value.duration),
        }
        if not math.isfinite(info["duration_seconds"]) or info["duration_seconds"] <= 0:
            raise MediaError("media_duration_invalid", "音频时长无效。", details={"path": str(source)})
    info.update(path=str(source), bytes=_io_path(source).stat().st_size)
    return info


def _publish_without_overwrite(temporary: Path, target: Path) -> bool:
    target_io = _io_path(target)
    temporary_io = _io_path(temporary)
    target_io.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(str(temporary_io), str(target_io))
        return True
    except FileExistsError:
        return False
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) not in (1, 5, 17, 32, 33, 50, 1142):
            raise
        if os.name == "nt":
            try:
                os.rename(str(temporary_io), str(target_io))
                return True
            except FileExistsError:
                return False
        # POSIX rename would replace an existing path. Fall back to an exclusive
        # create and copy rather than weakening the no-overwrite contract.
        try:
            descriptor = os.open(str(target_io), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as output, temporary_io.open("rb") as source:
                for block in iter(lambda: source.read(_CHUNK), b""):
                    output.write(block)
                output.flush(); os.fsync(output.fileno())
            return True
        except BaseException:
            try: target_io.unlink(missing_ok=True)
            except OSError: pass
            raise


def copy_media_exclusive(source_path: Any, target_path: Any, *,
                         allowed_roots: Sequence[Any] | None = None,
                         cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Stream-copy media to a new path, never replacing an unknown file."""
    source = _resolved_file(source_path, "源伴奏")
    roots = _root_paths(allowed_roots, [source.parent])
    source = _authorized_path(source, roots, "源伴奏")
    target = _resolved_file_target(target_path)
    target_root = next((root for root in roots if _inside(target, root)), None)
    if target_root is None:
        raise MediaError(
            "media_path_outside_roots", "目标伴奏路径不在调用方允许的目录中。",
            details={"path": str(target), "allowed_roots": [str(root) for root in roots]},
        )
    if _io_path(target.parent).exists():
        _reject_reparse(target.parent, target_root)
    source_before = _io_path(source).stat()
    digest = hashlib.sha256()
    copied = False
    temporary: Path | None = None
    try:
        if _io_path(target).exists():
            existing = _resolved_file(target, "目标伴奏")
            existing_sha = file_sha256(existing, cancel=cancel)["sha256"]
            source_sha = file_sha256(source, cancel=cancel)["sha256"]
            if existing_sha != source_sha:
                raise MediaError(
                    "media_target_conflict",
                    "目标位置已有不同内容的媒体文件，拒绝覆盖。",
                    details={"target": str(target), "source_sha256": source_sha,
                             "existing_sha256": existing_sha},
                )
            info = audio_info(source)
            return {**info, "sha256": source_sha, "path": str(target),
                    "source_path": str(source), "copied": False, "reused": True}
        _io_path(target.parent).mkdir(parents=True, exist_ok=True)
        _reject_reparse(target.parent, target_root)
        descriptor, name = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp",
                                            dir=str(_io_path(target.parent)))
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as output, _io_path(source).open("rb") as stream:
            for block in iter(lambda: stream.read(_CHUNK), b""):
                if cancel is not None and bool(cancel()):
                    raise InterruptedError("已取消媒体复制。")
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        source_after = _io_path(source).stat()
        if (source_before.st_size, source_before.st_mtime_ns) != (
                source_after.st_size, source_after.st_mtime_ns):
            raise MediaError("media_changed", "复制期间源伴奏发生变化。", retryable=True,
                             details={"path": str(source)})
        copied = _publish_without_overwrite(temporary, target)
        if not copied:
            existing_sha = file_sha256(target, cancel=cancel)["sha256"]
            if existing_sha != digest.hexdigest():
                raise MediaError("media_target_conflict", "复制目标被另一份不同内容占用。",
                                 details={"target": str(target)})
        verified = file_sha256(target, cancel=cancel)
        if verified["sha256"] != digest.hexdigest():
            raise MediaError("media_copy_mismatch", "媒体复制后 SHA-256 不一致。",
                             retryable=True, details={"target": str(target)})
        info = audio_info(target)
        return {**info, "sha256": verified["sha256"], "path": str(target),
                "source_path": str(source), "copied": copied, "reused": not copied}
    finally:
        if temporary is not None:
            try:
                _io_path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MediaError("media_project_invalid", "SVP JSON 含重复字段：" + key)
        result[key] = value
    return result


def _load_svp(path: Path) -> tuple[bytes, dict[str, Any], bool]:
    try:
        raw = _io_path(path).read_bytes()
        trailing_nul = raw.endswith(b"\0")
        text = raw.rstrip(b"\0").decode("utf-8-sig")
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(
                               ValueError("非有限 JSON 数值：" + value)))
    except MediaError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise MediaError("media_project_invalid", "SVP 不是有效 UTF-8 JSON。",
                         details={"path": str(path)}) from error
    if not isinstance(value, dict) or not isinstance(value.get("tracks"), list):
        raise MediaError("media_project_invalid", "SVP 缺少 tracks 数组。",
                         details={"path": str(path)})
    return raw, value, trailing_nul


def _encode_svp(value: Mapping[str, Any], trailing_nul: bool) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=False, allow_nan=False,
                          separators=(",", ":"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise MediaError("media_project_invalid", "SVP 媒体引用无法序列化。") from error
    return text.encode("utf-8") + (b"\0" if trailing_nul else b"")


def _replace_project(path: Path, value: Mapping[str, Any], trailing_nul: bool) -> None:
    data = _encode_svp(value, trailing_nul)
    target = _io_path(path)
    descriptor, name = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp",
                                        dir=str(target.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    finally:
        try:
            _io_path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


def _instrumental_refs(value: Mapping[str, Any]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    result = []
    tracks = value.get("tracks")
    if not isinstance(tracks, list):
        raise MediaError("media_project_invalid", "SVP tracks 无效。")
    for index, track in enumerate(tracks):
        if not isinstance(track, dict):
            raise MediaError("media_project_invalid", f"SVP tracks[{index}] 不是对象。")
        ref = track.get("mainRef")
        if isinstance(ref, dict) and ref.get("isInstrumental") is True:
            audio = ref.get("audio")
            if not isinstance(audio, dict):
                raise MediaError("media_project_invalid", f"SVP tracks[{index}] 缺少 mainRef.audio。")
            result.append((index, ref, audio))
    return result


def _resolve_reference(svp: Path, filename: str, roots: Sequence[Path]) -> Path:
    if not isinstance(filename, str) or not filename.strip():
        raise MediaError("media_reference_invalid", "伴奏引用为空。")
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = svp.parent / candidate
    return _authorized_path(candidate, roots, "SVP 伴奏引用")


def inspect_svp_media(svp_path: Any, *, allowed_roots: Sequence[Any] | None = None,
                      require_relative: bool = True,
                      expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Re-read SVP media references, file identity, duration and vocal tracks."""
    svp = _resolved_file(svp_path, "SVP 工程")
    if svp.suffix.lower() != ".svp":
        raise MediaError("media_project_invalid", "媒体回读目标必须是 .svp。")
    roots = _root_paths(allowed_roots, [svp.parent])
    raw, project, _ = _load_svp(svp)
    refs = _instrumental_refs(project)
    vocals = []
    for index, track in enumerate(project["tracks"]):
        ref = track.get("mainRef") if isinstance(track, dict) else None
        if not isinstance(ref, dict) or ref.get("isInstrumental") is not True:
            vocals.append({"track_index": index,
                           "name": track.get("name") if isinstance(track, dict) else None,
                           "notes": len((track.get("mainGroup") or {}).get("notes", []))
                           if isinstance(track, dict) else 0})
    items = []
    for track_index, _ref, audio in refs:
        filename = audio.get("filename")
        if require_relative and isinstance(filename, str) and Path(filename).is_absolute():
            raise MediaError("media_reference_not_relative", "可移动 SVP 的伴奏引用必须是相对路径。",
                             details={"track_index": track_index, "filename": filename})
        source = _resolve_reference(svp, filename, roots)
        identity = file_sha256(source)
        info = audio_info(source)
        declared_duration = audio.get("duration")
        if type(declared_duration) not in (int, float) or not math.isfinite(float(declared_duration)):
            raise MediaError("media_duration_missing", "SVP 伴奏引用缺少有效 duration。",
                             details={"track_index": track_index})
        tolerance = max(1.0 / max(int(info["sample_rate"]), 1), 1e-6)
        if abs(float(declared_duration) - float(info["duration_seconds"])) > tolerance:
            raise MediaError("media_duration_mismatch", "SVP 伴奏时长与媒体文件不一致。",
                             details={"track_index": track_index,
                                      "declared_seconds": float(declared_duration),
                                      "actual_seconds": info["duration_seconds"]})
        if not isinstance(filename, str):
            raise MediaError("media_reference_invalid", "SVP 伴奏文件名不是字符串。")
        if ".." in Path(filename).parts:
            raise MediaError("media_reference_invalid", "SVP 伴奏引用含父目录跳转。")
        normalized = Path(filename).as_posix()
        items.append({
            "track_index": track_index,
            "track_name": project["tracks"][track_index].get("name"),
            "reference": filename, "relative_path": normalized,
            "path": str(source), "sha256": identity["sha256"], "bytes": identity["bytes"],
            "duration_seconds": info["duration_seconds"], "sample_rate": info["sample_rate"],
            "channels": info["channels"], "format": info["format"], "verified": True,
        })
    if expected is not None:
        if not isinstance(expected, Mapping):
            raise MediaError("media_expected_invalid", "expected media 必须是对象。")
        if bool(expected.get("included")):
            if len(items) != 1:
                raise MediaError("instrumental_track_mismatch", "SVP 应含且仅含一条伴奏轨。",
                                 details={"actual_tracks": len(items)})
            item = items[0]
            declared_sha = expected.get("sha256") or expected.get("media_sha256")
            if declared_sha is not None and item["sha256"] != declared_sha:
                raise MediaError("media_sha256_mismatch", "SVP 伴奏文件 SHA-256 与回执不一致。",
                                 details={"declared": declared_sha, "actual": item["sha256"]})
            declared_duration = expected.get("duration_seconds")
            if declared_duration is not None:
                tolerance = max(1.0 / max(int(item["sample_rate"]), 1), 1e-6)
                if abs(float(declared_duration) - item["duration_seconds"]) > tolerance:
                    raise MediaError("media_duration_mismatch", "伴奏回执时长与文件不一致。")
        elif items:
            raise MediaError("instrumental_unexpected", "回执声明未含伴奏，但 SVP 含伴奏轨。")
    summary = {
        "included": bool(items), "count": len(items), "tracks": items,
        "vocals": vocals, "project_sha256": hashlib.sha256(raw).hexdigest(),
        "readback_verified": True,
    }
    if items:
        summary.update({
            "sha256": items[0]["sha256"], "duration_seconds": items[0]["duration_seconds"],
            "relative_paths": [row["relative_path"] for row in items],
        })
    return summary


def attach_instrumental(svp_path: Any, source_path: Any, *,
                        allowed_roots: Sequence[Any] | None = None,
                        target_name: str | None = None,
                        cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Copy authorized audio beside an SVP and make its reference portable."""
    svp = _resolved_file(svp_path, "SVP 工程")
    source = _resolved_file(source_path, "源伴奏")
    roots = _root_paths(allowed_roots, [source.parent, svp.parent])
    source = _authorized_path(source, roots, "源伴奏")
    raw, project, trailing_nul = _load_svp(svp)
    refs = _instrumental_refs(project)
    if len(refs) != 1:
        raise MediaError("instrumental_track_mismatch",
                         "导出转换必须产生且仅产生一条 SVP 伴奏轨。",
                         details={"actual_tracks": len(refs)})
    name = target_name or source.name
    if Path(name).name != name or name in (".", ".."):
        raise MediaError("media_target_name_invalid", "伴奏目标文件名无效。")
    info_before = audio_info(source)
    identity_before = file_sha256(source, cancel=cancel)
    target = svp.parent / name
    copy = copy_media_exclusive(source, target, allowed_roots=[svp.parent, source.parent],
                                cancel=cancel)
    if copy["duration_seconds"] != info_before["duration_seconds"] or copy["sha256"] != identity_before["sha256"]:
        raise MediaError("media_copy_mismatch", "复制后的伴奏身份或时长不一致。")
    audio = refs[0][2]
    audio["filename"] = name
    audio["duration"] = copy["duration_seconds"]
    render = project.get("renderConfig")
    if isinstance(render, dict) and int(copy.get("channels") or 1) > 1:
        render["numChannels"] = 2
    _replace_project(svp, project, trailing_nul)
    evidence = inspect_svp_media(
        svp, allowed_roots=[svp.parent], require_relative=True,
        expected={"included": True, "sha256": copy["sha256"],
                  "duration_seconds": copy["duration_seconds"]},
    )
    return {
        **evidence, "source_sha256": copy["sha256"], "relative_path": name,
        "copied": copy["copied"], "reused": copy["reused"],
        "path": str(svp.parent / name),
    }


def apply_svp_metadata(svp_path: Any, *, track_names: Sequence[str] | None = None,
                       project_name: str | None = None) -> dict[str, Any]:
    """Apply optional names without changing notes, lyrics or media identities."""
    svp = _resolved_file(svp_path, "SVP 工程")
    raw, project, trailing_nul = _load_svp(svp)
    vocal_tracks = [track for track in project["tracks"]
                    if isinstance(track, dict)
                    and not (isinstance(track.get("mainRef"), dict)
                             and track["mainRef"].get("isInstrumental") is True)]
    applied: list[str] = []
    if track_names is not None:
        names = list(track_names)
        if len(names) != len(vocal_tracks):
            raise MediaError("track_names_mismatch", "track_names 数量必须与演唱轨一致。",
                             details={"expected": len(vocal_tracks), "actual": len(names)})
        for track, name in zip(vocal_tracks, names):
            track["name"] = name
        applied.extend(names)
    if project_name is not None:
        render = project.get("renderConfig")
        if not isinstance(render, dict):
            raise MediaError("media_project_invalid", "SVP 缺少 renderConfig，无法写入工程名。")
        render["filename"] = project_name
        applied.append(project_name)
    if applied:
        _replace_project(svp, project, trailing_nul)
    after_raw, _, _ = _load_svp(svp)
    return {
        "track_names": [track.get("name") for track in vocal_tracks],
        "project_name": project_name, "applied": bool(applied),
        "project_name_field": "renderConfig.filename" if project_name is not None else None,
        "svp_sha256": hashlib.sha256(after_raw).hexdigest(),
    }


def relocate_svp_media(source_svp: Any, candidate_svp: Any,
                       allowed_roots: Sequence[Any] | None = None) -> dict[str, Any]:
    """Safely relocate supported SVP accompaniment beside a candidate project."""
    source_path = _resolved_file(source_svp, "源 SVP")
    candidate_path = _resolved_file(candidate_svp, "候选 SVP")
    if source_path.suffix.lower() != ".svp" or candidate_path.suffix.lower() != ".svp":
        raise MediaError("media_project_invalid", "relocate_svp_media 仅接受 .svp 文件。")
    roots = _root_paths(allowed_roots, [source_path.parent, candidate_path.parent])
    source_initial_sha = hashlib.sha256(_io_path(source_path).read_bytes()).hexdigest()
    _authorized_path(source_path, roots, "源 SVP")
    _authorized_path(candidate_path, roots, "候选 SVP")
    _unused, source_project, _ = _load_svp(source_path)
    source_refs = _instrumental_refs(source_project)
    if not source_refs:
        return {"included": False, "reason": "source_has_no_supported_media", "moved": []}
    before_candidate_raw, candidate_project, trailing_nul = _load_svp(candidate_path)
    candidate_refs = _instrumental_refs(candidate_project)
    if len(candidate_refs) != len(source_refs):
        raise MediaError("instrumental_track_mismatch", "候选 SVP 的伴奏轨数量与源工程不一致。",
                         details={"source": len(source_refs), "candidate": len(candidate_refs)})
    instrumental_indices = {row[0] for row in candidate_refs}
    vocals_before = [copy for index, copy in enumerate(candidate_project.get("tracks", []))
                     if index not in instrumental_indices]
    moved = []
    same_project = source_path == candidate_path
    for source_row, candidate_row in zip(source_refs, candidate_refs):
        source_audio = source_row[2]
        source_media = _resolve_reference(source_path, source_audio.get("filename"), roots)
        target_name = source_media.name
        info_before = audio_info(source_media)
        identity_before = file_sha256(source_media)
        target = candidate_path.parent / target_name
        copy = copy_media_exclusive(
            source_media, target, allowed_roots=[*roots, candidate_path.parent],
        )
        if copy["sha256"] != identity_before["sha256"] or copy["duration_seconds"] != info_before["duration_seconds"]:
            raise MediaError("media_copy_mismatch", "重定位后的伴奏身份或时长不一致。")
        candidate_audio = candidate_row[2]
        candidate_audio["filename"] = target_name
        candidate_audio["duration"] = copy["duration_seconds"]
        moved.append({"source_track_index": source_row[0],
                      "candidate_track_index": candidate_row[0],
                      "relative_path": target_name, "sha256": copy["sha256"],
                      "duration_seconds": copy["duration_seconds"],
                      "copied": copy["copied"], "reused": copy["reused"]})
    if not same_project:
        vocals_after = [copy for index, copy in enumerate(candidate_project.get("tracks", []))
                        if index not in instrumental_indices]
        if vocals_after != vocals_before:
            raise MediaError("vocal_tracks_changed", "媒体重定位意外改变了候选演唱轨。")
    _replace_project(candidate_path, candidate_project, trailing_nul)
    evidence = inspect_svp_media(candidate_path, allowed_roots=[candidate_path.parent],
                                 require_relative=True)
    if evidence["count"] != len(source_refs):
        raise MediaError("instrumental_track_mismatch", "重定位回读的伴奏轨数量不一致。")
    return {
        **evidence, "moved": moved, "source_svp_sha256": source_initial_sha,
        "candidate_before_sha256": hashlib.sha256(before_candidate_raw).hexdigest(),
        "same_project": same_project,
    }


__all__ = [
    "MediaError", "apply_svp_metadata", "attach_instrumental", "audio_info",
    "copy_media_exclusive", "file_sha256", "inspect_svp_media",
    "relocate_svp_media",
]
