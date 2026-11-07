"""Bounded, local, immutable preset document library.

The module deliberately depends only on the public preset document API.  Qt,
engines, agent control, accounts and network code stay outside this boundary.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from common import SidecarBusy, exclusive_sidecar, native_path
from preset_documents import (
    ID_PATTERN,
    MAX_DOCUMENT_BYTES,
    PresetError,
    VERSION_PATTERN,
    decode_document,
    document_digest,
    encode_document,
    read_regular_bytes,
    validate_document,
)


INDEX_FORMAT = "vwa.preset-library"
INDEX_VERSION = 1
INDEX_FILENAME = "library.json"
PREVIOUS_INDEX_FILENAME = "library.previous.json"
LOCK_FILENAME = "library.lock"
DOCUMENTS_DIRECTORY = "documents"
HISTORY_DIRECTORY = "history"

MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_LIBRARY_ENTRIES = 256
MAX_LIBRARY_BYTES = 64 * 1024 * 1024
MAX_ALIAS_CHARACTERS = 80
MAX_VERSION_BLOBS = 512

PRESET_TYPES = frozenset({"preparation", "mixdown", "tuning", "style_recipe"})
_REPARSE_ATTRIBUTE = 0x400
_ID_PATTERN = re.compile(ID_PATTERN)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BLOB_NAME_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")
_SEMVER_PATTERN = re.compile(VERSION_PATTERN)


def _failure(code: str, message: str, **details: Any) -> PresetError:
    return PresetError(code, message, **details)


def _version_conflict(
    identifier: str,
    version: str,
    installed_sha256: str,
    incoming_sha256: str,
) -> PresetError:
    return _failure(
        "preset_version_conflict",
        "同一预设 ID 与版本已有不同内容；不会覆盖。若要保留两份，"
        "请在导入时启用“创建独立副本”（as_copy）。",
        id=identifier,
        version=version,
        installed_sha256=installed_sha256,
        incoming_sha256=incoming_sha256,
        alternative="as_copy",
        preserve_existing=True,
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _absolute_path(value: os.PathLike[str] | str) -> Path:
    text = os.path.expanduser(os.fspath(value))
    return Path(os.path.abspath(text))


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(native_path(path))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _failure(
            "library_io_error",
            "无法检查预设库路径状态。",
            reason=str(error),
        ) from error


def _is_reparse(path: Path, info: os.stat_result | None = None) -> bool:
    info = _lstat(path) if info is None else info
    if info is None:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
        return True
    try:
        return path.is_symlink() or path.is_junction()
    except (AttributeError, OSError):
        return bool(path.is_symlink())


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(stat.S_IFMT(info.st_mode)))


@dataclass(frozen=True)
class _PathGuard:
    items: tuple[tuple[Path, tuple[int, int, int]], ...]


def _guard_path(path: Path) -> _PathGuard:
    """Inspect every existing component without resolving links."""

    path = _absolute_path(path)
    parts = path.parts
    current = Path(parts[0])
    items: list[tuple[Path, tuple[int, int, int]]] = []
    for part in parts:
        if part == parts[0]:
            continue
        current = current / part
        info = _lstat(current)
        if info is None:
            break
        if _is_reparse(current, info):
            raise _failure(
                "library_unsafe_path",
                "预设库路径包含符号链接、junction 或其他重解析点。",
                path=str(current),
            )
        items.append((current, _identity(info)))
    return _PathGuard(tuple(items))


def _recheck_guard(guard: _PathGuard) -> None:
    for path, expected_identity in guard.items:
        info = _lstat(path)
        if info is None or _is_reparse(path, info) or _identity(info) != expected_identity:
            raise _failure(
                "library_unsafe_path",
                "预设库路径在操作期间发生变化，已停止写入。",
                path=str(path),
            )


def _regular_info(path: Path, *, code: str, label: str) -> os.stat_result | None:
    info = _lstat(path)
    if info is None:
        return None
    if _is_reparse(path, info) or not stat.S_ISREG(info.st_mode):
        raise _failure(code, f"{label}不是安全的普通文件。")
    return info


def _read_bytes(path: Path, *, limit: int, code: str, label: str) -> bytes:
    try:
        _guard_path(path)
        return read_regular_bytes(path, limit=limit)
    except PresetError:
        raise
    except (OSError, ValueError) as error:
        raise _failure(code, f"无法安全读取{label}。", reason=str(error)) from error

_UNSET = object()



def _publish_new(temporary, destination):
    """Publish complete bytes atomically, preserving an existing destination."""
    if os.name == "nt":
        # Windows rename fails if destination exists; both paths share a parent.
        os.rename(native_path(temporary), native_path(destination))
    else:
        os.link(native_path(temporary), native_path(destination))


def _leaf_unchanged(
    path: Path, before: os.stat_result | None
) -> bool:
    current = _lstat(path)
    if before is None:
        return current is None
    if current is None or _is_reparse(path, current):
        return False
    return _identity(current) == _identity(before)


def _atomic_replace(path: Path, raw: bytes, *, expected: Any = _UNSET) -> None:
    """Atomically replace a file, optionally enforcing exact existing bytes.

    ``expected`` omitted permits replacing an existing regular file. Passing
    ``None`` requires that the target not exist; passing bytes requires an
    exact byte-for-byte match. Parent and leaf identities are checked against
    the same snapshots before and after publication.
    """

    parent = path.parent
    guard = _guard_path(parent)
    leaf_before = _lstat(path)
    if leaf_before is not None:
        _regular_info(path, code="library_unsafe_path", label=path.name)
        if expected is None:
            raise _failure(
                "library_stale",
                "库索引在写入期间发生变化，请刷新后重试。",
            )
        if expected is not _UNSET and expected is not None:
            current_raw = _read_bytes(
                path,
                limit=max(len(expected), MAX_INDEX_BYTES),
                code="library_unsafe_path",
                label=path.name,
            )
            if current_raw != expected:
                raise _failure(
                    "library_stale",
                    "库索引在写入期间发生变化，请刷新后重试。",
                )
    elif expected is not _UNSET and expected is not None:
        raise _failure(
            "library_stale",
            "库索引在写入期间被移除，请刷新后重试。",
        )

    name = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=native_path(parent)
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _recheck_guard(guard)
        if not _leaf_unchanged(path, leaf_before):
            raise _failure(
                "library_stale",
                "库索引目标在写入期间发生变化，请刷新后重试。",
            )
        if expected is not _UNSET and expected is not None:
            current_raw = _read_bytes(
                path,
                limit=max(len(expected), MAX_INDEX_BYTES),
                code="library_unsafe_path",
                label=path.name,
            )
            if current_raw != expected:
                raise _failure(
                    "library_stale",
                    "库索引在写入期间发生变化，请刷新后重试。",
                )
        os.replace(native_path(name), native_path(path))
        name = None
        _recheck_guard(guard)
        _regular_info(path, code="library_corrupt", label=path.name)
        if _read_bytes(
            path,
            limit=max(len(raw), MAX_INDEX_BYTES),
            code="library_corrupt",
            label=path.name,
        ) != raw:
            raise _failure("library_corrupt", "原子写入后的文件字节校验失败。")
    except PresetError:
        raise
    except OSError as error:
        raise _failure(
            "library_io_error",
            "无法原子写入预设库元数据。",
            reason=str(error),
        ) from error
    finally:
        if name is not None:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass


def _publish_immutable(path: Path, raw: bytes) -> bool:
    """Publish an immutable file without overwriting; return True if new."""

    parent = path.parent
    guard = _guard_path(parent)
    leaf_before = _regular_info(path, code="library_corrupt", label="预设文档")
    if leaf_before is not None:
        stored = _read_bytes(
            path,
            limit=MAX_DOCUMENT_BYTES,
            code="library_corrupt",
            label="预设文档",
        )
        if stored != raw:
            raise _failure(
                "library_corrupt",
                "不可变预设文档路径已存在不同内容。",
            )
        return False

    name = None
    descriptor = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=native_path(parent)
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _recheck_guard(guard)
        if not _leaf_unchanged(path, leaf_before):
            raise _failure(
                "library_corrupt",
                "不可变预设文档目标在写入期间发生变化。",
            )
        try:
            _publish_new(name, path)
        except FileExistsError:
            stored = _read_bytes(
                path,
                limit=MAX_DOCUMENT_BYTES,
                code="library_corrupt",
                label="预设文档",
            )
            if stored != raw:
                raise _failure(
                    "library_corrupt",
                    "不可变预设文档路径已存在不同内容。",
                )
        _recheck_guard(guard)
        _regular_info(path, code="library_corrupt", label="预设文档")
        stored = _read_bytes(
            path,
            limit=MAX_DOCUMENT_BYTES,
            code="library_corrupt",
            label="预设文档",
        )
        if stored != raw:
            raise _failure(
                "library_corrupt",
                "新发布的预设文档内容校验失败。",
            )
        return True
    except PresetError:
        raise
    except FileExistsError as error:
        stored = _read_bytes(
            path,
            limit=MAX_DOCUMENT_BYTES,
            code="library_corrupt",
            label="预设文档",
        )
        if stored == raw:
            return False
        raise _failure(
            "library_corrupt",
            "不可变预设文档路径已存在不同内容。",
        ) from error
    except OSError as error:
        raise _failure(
            "library_io_error",
            "无法发布不可变预设文档。",
            reason=str(error),
        ) from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if name is not None:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass

def _strict_json(raw: bytes, *, label: str) -> Any:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("nonfinite JSON number")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as error:
        raise _failure(
            "library_corrupt",
            f"{label}不是严格、有限的 UTF-8 JSON。",
            reason=str(error),
        ) from error


def _json_depth(value: Any, limit: int = 16) -> int:
    stack = [(value, 1)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100_000 or depth > limit:
            raise _failure("library_corrupt", "预设库索引嵌套或条目过多。")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return count


def _valid_identity(identifier: Any, version: Any) -> bool:
    if (
        type(identifier) is not str
        or len(identifier) > 128
        or _ID_PATTERN.fullmatch(identifier) is None
        or type(version) is not str
        or len(version) > 80
    ):
        return False
    return _SEMVER_PATTERN.fullmatch(version) is not None


def _valid_alias(alias: Any) -> bool:
    if type(alias) is not str or len(alias) > MAX_ALIAS_CHARACTERS:
        return False
    if alias != alias.strip():
        return False
    return all(
        not (
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
        )
        for character in alias
    )

def _validate_index_entries(entries: Any) -> list[dict[str, Any]]:
    if type(entries) is not list:
        raise _failure("library_corrupt", "预设库索引 entries 必须是数组。")
    if len(entries) > MAX_LIBRARY_ENTRIES:
        raise _failure("library_full", "预设库活动条目超过数量上限。")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    total_bytes = 0
    required = {"id", "version", "sha256", "type", "bytes", "alias", "favorite"}
    for position, value in enumerate(entries):
        if type(value) is not dict or set(value) != required:
            raise _failure(
                "library_corrupt",
                "预设库索引条目字段无效。",
                position=position,
            )
        identifier = value["id"]
        version = value["version"]
        if not _valid_identity(identifier, version):
            raise _failure(
                "library_corrupt",
                "预设库索引包含无效的 ID 或版本。",
                position=position,
            )
        if identifier.startswith("builtin."):
            raise _failure(
                "library_corrupt",
                "用户索引不能声明内置预设身份。",
                id=identifier,
                version=version,
            )
        key = (identifier, version)
        if key in seen:
            raise _failure(
                "library_corrupt",
                "预设库索引包含重复的 ID 与版本。",
                id=identifier,
                version=version,
            )
        seen.add(key)
        if type(value["type"]) is not str or value["type"] not in PRESET_TYPES:
            raise _failure(
                "library_corrupt",
                "预设库索引包含未知预设类型。",
                id=identifier,
                version=version,
            )
        if (
            type(value["bytes"]) is not int
            or value["bytes"] <= 0
            or value["bytes"] > MAX_DOCUMENT_BYTES
        ):
            raise _failure(
                "library_corrupt",
                "预设库索引文件大小无效。",
                id=identifier,
                version=version,
            )
        if (
            type(value["sha256"]) is not str
            or _SHA256_PATTERN.fullmatch(value["sha256"]) is None
        ):
            raise _failure(
                "library_corrupt",
                "预设库索引摘要无效。",
                id=identifier,
                version=version,
            )
        if not _valid_alias(value["alias"]):
            raise _failure(
                "library_corrupt",
                "预设库索引别名无效。",
                id=identifier,
                version=version,
            )
        if type(value["favorite"]) is not bool:
            raise _failure(
                "library_corrupt",
                "预设库收藏标记无效。",
                id=identifier,
                version=version,
            )
        total_bytes += value["bytes"]
        if total_bytes > MAX_LIBRARY_BYTES:
            raise _failure("library_full", "预设库活动文档超过存储上限。")
        result.append(dict(value))
    result.sort(key=lambda entry: (entry["id"], entry["version"]))
    return result


def _encode_index(entries: list[dict[str, Any]]) -> bytes:
    value = {
        "format": INDEX_FORMAT,
        "index_version": INDEX_VERSION,
        "entries": entries,
    }
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _decode_index(raw: bytes) -> list[dict[str, Any]]:
    if type(raw) is not bytes:
        raise _failure("library_corrupt", "预设库索引字节无效。")
    if len(raw) > MAX_INDEX_BYTES:
        raise _failure("library_corrupt", "预设库索引超过字节上限。")
    value = _strict_json(raw, label="预设库索引")
    _json_depth(value)
    if type(value) is not dict or set(value) != {"format", "index_version", "entries"}:
        raise _failure("library_corrupt", "预设库索引根字段无效。")
    if value["format"] != INDEX_FORMAT or type(value["index_version"]) is not int:
        raise _failure("library_corrupt", "预设库索引格式或版本无效。")
    if value["index_version"] != INDEX_VERSION:
        raise _failure(
            "library_corrupt",
            "不支持此预设库索引版本。",
            index_version=value["index_version"],
        )
    try:
        entries = _validate_index_entries(value["entries"])
    except PresetError as error:
        if error.code == "library_full":
            raise _failure(
                "library_corrupt",
                "预设库索引超过容量上限。",
                reason=str(error),
            ) from error
        raise
    if raw != _encode_index(entries):
        raise _failure("library_corrupt", "预设库索引不是规范字节。")
    return entries


@dataclass(frozen=True)
class _Record:
    row: dict[str, Any]
    raw: bytes
    document: dict[str, Any]
    entry: dict[str, Any]


@dataclass(frozen=True)
class _State:
    revision: str | None
    raw: bytes | None
    entries: list[dict[str, Any]]
    records: tuple[_Record, ...]


def _document_name(document: dict[str, Any], identifier: str) -> str:
    metadata = document.get("metadata")
    if type(metadata) is dict and type(metadata.get("name")) is str:
        name = metadata["name"]
        if name.strip():
            return name
    return identifier


def _document_path(root: Path, identifier: str, version: str, digest: str) -> Path:
    identifier_key = _sha256(identifier.encode("ascii"))
    version_key = _sha256(version.encode("ascii"))
    return (
        root
        / DOCUMENTS_DIRECTORY
        / identifier_key
        / version_key
        / (digest + ".json")
    )

class PresetLibrary:
    """A bounded local library over immutable preset documents."""

    def __init__(self, root: Path | str | None = None):
        if root is None:
            # Resolve beside common.py, matching the source-relative default
            # without consulting mutable workspace preferences.
            from common import ROOT

            root = ROOT / "user-presets"
        self.root = _absolute_path(root)
        self.index_path = self.root / INDEX_FILENAME
        self.previous_index_path = self.root / PREVIOUS_INDEX_FILENAME
        self.lock_path = self.root / LOCK_FILENAME
        self.history_path = self.root / HISTORY_DIRECTORY

    def _inspect_root(self) -> _PathGuard:
        return _guard_path(self.root)

    def _prepare_root(self) -> _PathGuard:
        guard = _guard_path(self.root)
        info = _lstat(self.root)
        if info is None:
            try:
                Path(native_path(self.root)).mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise _failure(
                    "library_io_error",
                    "无法创建预设库目录。",
                    reason=str(error),
                ) from error
            _recheck_guard(guard)
            guard = _guard_path(self.root)
        elif _is_reparse(self.root, info) or not stat.S_ISDIR(info.st_mode):
            raise _failure(
                "library_unsafe_path",
                "预设库根路径必须是安全的普通目录。",
            )
        return guard

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        guard = self._prepare_root()
        if _lstat(self.lock_path) is not None and _is_reparse(self.lock_path):
            raise _failure(
                "library_unsafe_path",
                "预设库锁文件不能是符号链接或重解析点。",
            )
        try:
            with exclusive_sidecar(
                self.lock_path,
                create=True,
                blocking=False,
            ):
                _recheck_guard(guard)
                if _is_reparse(self.lock_path):
                    raise _failure(
                        "library_unsafe_path",
                        "预设库锁文件不是安全的普通文件。",
                    )
                yield
        except SidecarBusy as error:
            raise _failure(
                "library_busy",
                "预设库正被其他操作占用，请稍后重试。",
                retryable=True,
            ) from error
        except PresetError:
            raise
        except OSError as error:
            busy = (
                isinstance(error, PermissionError)
                or error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EWOULDBLOCK}
                or getattr(error, "winerror", None) in {32, 33}
            )
            if busy:
                raise _failure(
                    "library_busy",
                    "预设库正被其他操作占用，请稍后重试。",
                    retryable=True,
                ) from error
            raise _failure(
                "library_io_error",
                "无法锁定预设库。",
                reason=str(error),
            ) from error

    def _read_current(self) -> _State:
        _guard_path(self.root)
        info = _lstat(self.index_path)
        if info is None:
            return _State(None, None, [], ())
        if _is_reparse(self.index_path, info) or not stat.S_ISREG(info.st_mode):
            raise _failure(
                "library_corrupt",
                "预设库索引不是安全的普通文件。",
            )
        try:
            raw = _read_bytes(
                self.index_path,
                limit=MAX_INDEX_BYTES,
                code="library_corrupt",
                label="预设库索引",
            )
        except PresetError as error:
            if error.code == "library_corrupt":
                raise
            raise _failure(
                "library_corrupt",
                "无法读取预设库索引。",
                reason=str(error),
            ) from error
        entries = _decode_index(raw)
        records = tuple(self._load_record(row) for row in entries)
        return _State(_sha256(raw), raw, entries, records)

    def _read_previous(self) -> _State:
        _guard_path(self.root)
        info = _lstat(self.previous_index_path)
        if info is None:
            raise _failure(
                "library_recovery_unavailable",
                "预设库没有可恢复的上一索引。",
            )
        if _is_reparse(self.previous_index_path, info) or not stat.S_ISREG(info.st_mode):
            raise _failure(
                "library_corrupt",
                "上一预设库索引不是安全的普通文件。",
                source="previous",
            )
        try:
            raw = _read_bytes(
                self.previous_index_path,
                limit=MAX_INDEX_BYTES,
                code="library_corrupt",
                label="上一预设库索引",
            )
            entries = _decode_index(raw)
            records = tuple(self._load_record(row) for row in entries)
        except PresetError as error:
            if error.code == "library_corrupt" and error.details.get("source") == "previous":
                raise
            raise _failure(
                "library_corrupt",
                "上一预设库索引或引用文档无效。",
                source="previous",
                reason=str(error),
            ) from error
        return _State(_sha256(raw), raw, entries, records)

    def _load_record(self, row: dict[str, Any]) -> _Record:
        identifier = row["id"]
        version = row["version"]
        digest = row["sha256"]
        path = _document_path(self.root, identifier, version, digest)
        try:
            _guard_path(path)
            raw = read_regular_bytes(path, limit=MAX_DOCUMENT_BYTES)
        except (PresetError, OSError, ValueError) as error:
            raise _failure(
                "library_corrupt",
                "已安装预设文档缺失或无法安全读取。",
                id=identifier,
                version=version,
                sha256=digest,
                reason=str(error),
            ) from error
        if len(raw) != row["bytes"] or document_digest(raw) != digest:
            raise _failure(
                "library_corrupt",
                "已安装预设文档的大小或摘要与索引不符。",
                id=identifier,
                version=version,
                sha256=digest,
            )
        try:
            document = decode_document(raw, allow_builtin=False)
        except PresetError as error:
            raise _failure(
                "library_corrupt",
                "已安装预设文档无法通过严格校验。",
                id=identifier,
                version=version,
                sha256=digest,
                reason=str(error),
            ) from error
        if (
            document.get("id") != identifier
            or document.get("version") != version
            or document.get("type") != row["type"]
        ):
            raise _failure(
                "library_corrupt",
                "已安装预设文档身份与索引不符。",
                id=identifier,
                version=version,
                sha256=digest,
            )
        entry = {
            "id": identifier,
            "version": version,
            "sha256": digest,
            "type": row["type"],
            "name": row["alias"] or _document_name(document, identifier),
            "builtin": False,
            "favorite": row["favorite"],
            "alias": row["alias"],
            "bytes": row["bytes"],
        }
        return _Record(dict(row), raw, document, entry)

    def _builtin_records(self) -> list[_Record]:
        try:
            from preset_builtin_documents import builtin_documents

            values = builtin_documents()
        except (ImportError, OSError, ValueError, TypeError) as error:
            raise _failure(
                "library_corrupt",
                "内置预设文档不可用。",
                reason=str(error),
            ) from error
        if type(values) is not list:
            raise _failure("library_corrupt", "内置预设文档列表无效。")
        records: list[_Record] = []
        seen: set[tuple[str, str]] = set()
        for position, value in enumerate(values):
            try:
                document = validate_document(value, allow_builtin=True)
                identifier = document["id"]
                version = document["version"]
                preset_type = document["type"]
                if not identifier.startswith("builtin.") or preset_type not in PRESET_TYPES:
                    raise _failure(
                        "library_corrupt",
                        "内置预设身份或类型无效。",
                        position=position,
                    )
                key = (identifier, version)
                if key in seen:
                    raise _failure(
                        "library_corrupt",
                        "内置预设包含重复身份。",
                        id=identifier,
                        version=version,
                    )
                seen.add(key)
                raw = encode_document(document, allow_builtin=True)
                digest = document_digest(raw)
            except PresetError as error:
                if error.code == "library_corrupt":
                    raise
                raise _failure(
                    "library_corrupt",
                    "内置预设文档未通过严格校验。",
                    position=position,
                    reason=str(error),
                ) from error
            entry = {
                "id": identifier,
                "version": version,
                "sha256": digest,
                "type": preset_type,
                "name": _document_name(document, identifier),
                "builtin": True,
                "favorite": False,
                "alias": "",
                "bytes": len(raw),
            }
            row = {
                "id": identifier,
                "version": version,
                "sha256": digest,
                "type": preset_type,
                "bytes": len(raw),
                "alias": "",
                "favorite": False,
            }
            records.append(_Record(row, raw, document, entry))
        return records

    def _snapshot_from(self, state: _State) -> dict[str, Any]:
        builtins = self._builtin_records()
        entries = [dict(record.entry) for record in builtins]
        entries.extend(dict(record.entry) for record in state.records)
        return {"revision": state.revision, "entries": entries}

    def snapshot(self) -> dict[str, Any]:
        """Return builtins plus local rows without creating or writing anything."""

        self._inspect_root()
        return self._snapshot_from(self._read_current())

    def _assert_revision(
        self, state: _State, expected_revision: str | None
    ) -> None:
        if expected_revision is not None and (
            type(expected_revision) is not str
            or _SHA256_PATTERN.fullmatch(expected_revision) is None
        ):
            raise _failure(
                "library_stale",
                "expected_revision 必须是上一快照的精确修订或 None。",
                expected_revision=expected_revision,
                current_revision=state.revision,
            )
        if state.revision != expected_revision:
            raise _failure(
                "library_stale",
                "预设库索引已变化，请刷新后重新决定。",
                expected_revision=expected_revision,
                current_revision=state.revision,
            )

    def _find_record(
        self, state: _State, identifier: str, version: str
    ) -> _Record | None:
        for record in state.records:
            if (
                record.row["id"] == identifier
                and record.row["version"] == version
            ):
                return record
        return None

    def _check_version_history(
        self,
        identifier: str,
        version: str,
        raw: bytes,
        digest: str,
    ) -> None:
        directory = _document_path(self.root, identifier, version, digest).parent
        if _lstat(directory) is None:
            return
        _guard_path(directory)
        count = 0
        try:
            with os.scandir(native_path(directory)) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_VERSION_BLOBS:
                        raise _failure(
                            "library_full",
                            "同一预设版本的历史文档数量超过安全上限。",
                            id=identifier,
                            version=version,
                            limit=MAX_VERSION_BLOBS,
                        )
                    name = entry.name
                    item = directory / name
                    info = _lstat(item)
                    if info is None:
                        raise _failure(
                            "library_corrupt",
                            "预设版本目录在检查期间发生变化。",
                            id=identifier,
                            version=version,
                        )
                    if _is_reparse(item, info) or not stat.S_ISREG(info.st_mode):
                        raise _failure(
                            "library_corrupt",
                            "预设版本目录包含非普通文件。",
                            id=identifier,
                            version=version,
                        )
                    if name.startswith(".") and name.endswith(".tmp"):
                        continue
                    if _BLOB_NAME_PATTERN.fullmatch(name) is None:
                        raise _failure(
                            "library_corrupt",
                            "预设版本目录包含无效文件名。",
                            id=identifier,
                            version=version,
                        )
                    historical_digest = name[:-5]
                    if historical_digest != digest:
                        raise _version_conflict(
                            identifier,
                            version,
                            historical_digest,
                            digest,
                        )
                    if _read_bytes(
                        item,
                        limit=MAX_DOCUMENT_BYTES,
                        code="library_corrupt",
                        label="历史预设文档",
                    ) != raw:
                        raise _failure(
                            "library_corrupt",
                            "历史预设文档内容与摘要路径不符。",
                            id=identifier,
                            version=version,
                            sha256=digest,
                        )
        except PresetError:
            raise
        except OSError as error:
            raise _failure(
                "library_io_error",
                "无法检查预设版本历史。",
                reason=str(error),
            ) from error

    def _ensure_document(
        self, raw: bytes, identifier: str, version: str, digest: str
    ) -> Path:
        path = _document_path(self.root, identifier, version, digest)
        identifier_directory = path.parent.parent
        version_directory = path.parent
        try:
            guard = _guard_path(identifier_directory)
            if _lstat(identifier_directory) is None:
                Path(native_path(identifier_directory)).mkdir(parents=True, exist_ok=True)
            _recheck_guard(guard)
            guard = _guard_path(version_directory)
            if _lstat(version_directory) is None:
                Path(native_path(version_directory)).mkdir(parents=True, exist_ok=True)
            _recheck_guard(guard)
        except PresetError:
            raise
        except OSError as error:
            raise _failure(
                "library_io_error",
                "无法创建预设文档目录。",
                reason=str(error),
            ) from error
        _publish_immutable(path, raw)
        return path

    def _write_index(
        self, entries: list[dict[str, Any]], old_raw: bytes | None
    ) -> str:
        normalized = _validate_index_entries(entries)
        raw = _encode_index(normalized)
        if len(raw) > MAX_INDEX_BYTES:
            raise _failure("library_full", "预设库索引超过字节上限。")
        if old_raw is not None:
            _atomic_replace(self.previous_index_path, old_raw)
        _atomic_replace(self.index_path, raw, expected=old_raw)
        return _sha256(raw)
    def _put_raw(
        self,
        raw: bytes,
        document: dict[str, Any],
        *,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        identifier = document["id"]
        version = document["version"]
        if identifier.startswith("builtin."):
            raise _failure(
                "preset_builtin_forbidden",
                "内置预设身份不能直接写入用户库；导入时会自动创建独立用户副本。",
                id=identifier,
                version=version,
            )
        digest = document_digest(raw)
        with self._write_lock():
            state = self._read_current()
            self._assert_revision(state, expected_revision)
            existing = self._find_record(state, identifier, version)
            if existing is not None:
                if existing.row["sha256"] == digest and existing.raw == raw:
                    return {
                        "revision": state.revision,
                        "entry": dict(existing.entry),
                        "already_present": True,
                    }
                raise _version_conflict(
                    identifier,
                    version,
                    existing.row["sha256"],
                    digest,
                )
            self._check_version_history(identifier, version, raw, digest)
            if len(state.entries) >= MAX_LIBRARY_ENTRIES:
                raise _failure(
                    "library_full",
                    f"预设库最多保存 {MAX_LIBRARY_ENTRIES} 个活动条目。",
                    limit=MAX_LIBRARY_ENTRIES,
                )
            current_bytes = sum(row["bytes"] for row in state.entries)
            if current_bytes + len(raw) > MAX_LIBRARY_BYTES:
                raise _failure(
                    "library_full",
                    "预设库活动文档超过存储上限。",
                    limit=MAX_LIBRARY_BYTES,
                )
            self._ensure_document(raw, identifier, version, digest)
            row = {
                "id": identifier,
                "version": version,
                "sha256": digest,
                "type": document["type"],
                "bytes": len(raw),
                "alias": "",
                "favorite": False,
            }
            revision = self._write_index([*state.entries, row], state.raw)
            record = self._load_record(row)
            return {
                "revision": revision,
                "entry": dict(record.entry),
                "already_present": False,
            }

    def get_document(self, identifier: str, version: str) -> dict[str, Any]:
        if not _valid_identity(identifier, version):
            raise _failure(
                "preset_not_found",
                "预设 ID 或版本无效。",
                id=identifier,
                version=version,
            )
        for record in self._builtin_records():
            if record.row["id"] == identifier and record.row["version"] == version:
                return copy.deepcopy(record.document)
        state = self._read_current()
        record = self._find_record(state, identifier, version)
        if record is None:
            raise _failure(
                "preset_not_found",
                "预设不存在。",
                id=identifier,
                version=version,
            )
        return copy.deepcopy(record.document)

    def import_file(
        self,
        path: Path | str,
        *,
        expected_revision: str | None,
        expected_sha256: str | None = None,
        as_copy: bool = False,
    ) -> dict[str, Any]:
        """Append a validated file; built-in identities always become copies.

        The default path preserves the incoming ID, version and exact bytes.
        Exact duplicates are idempotent, while a same-identity conflict never
        overwrites the existing row. as_copy=True instead installs a canonical
        user.UUID/0.1.0 copy and leaves the source file untouched.
        """
        if type(as_copy) is not bool:
            raise _failure(
                "preset_invalid_copy_option",
                "as_copy 必须是布尔值。",
            )
        raw = read_regular_bytes(path, limit=MAX_DOCUMENT_BYTES)
        actual_sha256 = document_digest(raw)
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise _failure(
                "file_changed",
                "预览后预设文件内容已经变化，请重新选择并预览。",
                expected_sha256=expected_sha256,
                actual_sha256=actual_sha256,
            )
        document = decode_document(raw, allow_builtin=True)
        if as_copy or document["id"].startswith("builtin."):
            result = self._copy_validated_document(
                document,
                name=None,
                expected_revision=expected_revision,
            )
            result["source_sha256"] = actual_sha256
            result["copied"] = True
            return result
        result = self._put_raw(raw, document, expected_revision=expected_revision)
        result["source_sha256"] = actual_sha256
        result["copied"] = False
        return result

    def put_document(
        self,
        document: dict[str, Any],
        *,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        if (
            type(document) is dict
            and type(document.get("id")) is str
            and document["id"].startswith("builtin.")
        ):
            raise _failure(
                "preset_builtin_forbidden",
                "内置预设身份不能通过 put_document 写入用户库；请导入为独立用户副本。",
                id=document["id"],
                version=document.get("version"),
            )
        raw = encode_document(document, allow_builtin=False)
        validated = decode_document(raw, allow_builtin=False)
        return self._put_raw(raw, validated, expected_revision=expected_revision)

    def _copy_validated_document(
        self,
        source_document: dict[str, Any],
        *,
        name: str | None,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        """Install a detached validated document through the common copy path."""
        if name is not None and (
            type(name) is not str or not name.strip() or len(name) > 80
        ):
            raise _failure("preset_invalid_name", "副本名称必须为 1 到 80 个字符。")
        for _attempt in range(8):
            copied = copy.deepcopy(source_document)
            copied["id"] = "user." + str(uuid.uuid4())
            copied["version"] = "0.1.0"
            if name is not None:
                metadata = copy.deepcopy(copied.get("metadata", {}))
                metadata["name"] = name.strip()
                copied["metadata"] = metadata
            try:
                raw = encode_document(copied, allow_builtin=False)
                document = decode_document(raw, allow_builtin=False)
            except PresetError as error:
                raise _failure(
                    "preset_copy_invalid",
                    "无法从源文档创建可校验的用户副本。",
                    reason=str(error),
                ) from error
            try:
                result = self._put_raw(
                    raw,
                    document,
                    expected_revision=expected_revision,
                )
            except PresetError as error:
                if error.code == "preset_version_conflict":
                    continue
                raise
            result["already_present"] = False
            return result
        raise _failure("preset_copy_conflict", "无法生成唯一的用户预设副本 ID。")

    def copy(
        self,
        identifier: str,
        version: str,
        *,
        name: str,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        source_document = self.get_document(identifier, version)
        return self._copy_validated_document(
            source_document,
            name=name,
            expected_revision=expected_revision,
        )


    def _mutate(
        self,
        identifier: str,
        version: str,
        expected_revision: str | None,
        change,
    ):
        with self._write_lock():
            state = self._read_current()
            self._assert_revision(state, expected_revision)
            record = self._find_record(state, identifier, version)
            if record is None:
                if _valid_identity(identifier, version) and identifier.startswith("builtin."):
                    raise _failure(
                        "preset_builtin_readonly",
                        "内置预设不可修改；请先复制为用户预设。",
                        id=identifier,
                        version=version,
                    )
                raise _failure(
                    "preset_not_found",
                    "预设不存在。",
                    id=identifier,
                    version=version,
                )
            new_entries = [dict(row) for row in state.entries]
            target = next(
                row
                for row in new_entries
                if row["id"] == identifier and row["version"] == version
            )
            change(target)
            revision = self._write_index(new_entries, state.raw)
            updated = self._load_record(target)
            return revision, updated

    def rename(
        self,
        identifier: str,
        version: str,
        *,
        alias: str,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        if not _valid_alias(alias):
            raise _failure(
                "preset_invalid_alias",
                f"别名必须为空或 1 到 {MAX_ALIAS_CHARACTERS} 个有效字符。",
            )
        revision, record = self._mutate(
            identifier,
            version,
            expected_revision,
            lambda row: row.__setitem__("alias", alias),
        )
        return {"revision": revision, "entry": dict(record.entry)}

    def set_favorite(
        self,
        identifier: str,
        version: str,
        *,
        favorite: bool,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        if type(favorite) is not bool:
            raise _failure("preset_invalid_favorite", "favorite 必须是布尔值。")
        revision, record = self._mutate(
            identifier,
            version,
            expected_revision,
            lambda row: row.__setitem__("favorite", favorite),
        )
        return {"revision": revision, "entry": dict(record.entry)}

    def delete(
        self,
        identifier: str,
        version: str,
        *,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        with self._write_lock():
            state = self._read_current()
            self._assert_revision(state, expected_revision)
            if self._find_record(state, identifier, version) is None:
                if _valid_identity(identifier, version) and identifier.startswith("builtin."):
                    raise _failure(
                        "preset_builtin_readonly",
                        "内置预设不可删除。",
                        id=identifier,
                        version=version,
                    )
                raise _failure(
                    "preset_not_found",
                    "预设不存在。",
                    id=identifier,
                    version=version,
                )
            entries = [
                dict(row)
                for row in state.entries
                if not (row["id"] == identifier and row["version"] == version)
            ]
            revision = self._write_index(entries, state.raw)
            return {"revision": revision}

    def export_file(
        self,
        identifier: str,
        version: str,
        destination: Path | str,
    ) -> Path:
        if not _valid_identity(identifier, version):
            raise _failure(
                "preset_not_found",
                "预设 ID 或版本无效。",
                id=identifier,
                version=version,
            )
        source: bytes | None = None
        for record in self._builtin_records():
            if record.row["id"] == identifier and record.row["version"] == version:
                source = record.raw
                break
        if source is None:
            state = self._read_current()
            record = self._find_record(state, identifier, version)
            if record is None:
                raise _failure(
                    "preset_not_found",
                    "预设不存在。",
                    id=identifier,
                    version=version,
                )
            source = record.raw

        target = _absolute_path(destination)
        parent = target.parent
        parent_info = _lstat(parent)
        if (
            parent_info is None
            or _is_reparse(parent, parent_info)
            or not stat.S_ISDIR(parent_info.st_mode)
        ):
            raise _failure(
                "library_unsafe_path",
                "导出目标父目录必须是已存在的安全普通目录。",
                destination=str(target),
            )
        if _lstat(target) is not None:
            raise _failure(
                "export_exists",
                "导出目标已存在；不会覆盖。",
                destination=str(target),
            )
        guard = _guard_path(parent)
        leaf_before = _lstat(target)
        name = None
        descriptor = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=native_path(parent)
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            _recheck_guard(guard)
            if not _leaf_unchanged(target, leaf_before):
                raise _failure(
                    "export_exists",
                    "导出目标在写入期间出现；不会覆盖。",
                    destination=str(target),
                )
            try:
                _publish_new(name, target)
            except FileExistsError as error:
                raise _failure(
                    "export_exists",
                    "导出目标已存在；不会覆盖。",
                    destination=str(target),
                ) from error
            _recheck_guard(guard)
            _regular_info(target, code="library_unsafe_path", label="导出文件")
            exported = _read_bytes(
                target,
                limit=MAX_DOCUMENT_BYTES,
                code="library_corrupt",
                label="导出文件",
            )
            if exported != source:
                raise _failure(
                    "library_corrupt",
                    "导出后字节校验失败。",
                    destination=str(target),
                )
            return target
        except PresetError:
            raise
        except FileExistsError as error:
            raise _failure(
                "export_exists",
                "导出目标已存在；不会覆盖。",
                destination=str(target),
            ) from error
        except OSError as error:
            raise _failure(
                "library_io_error",
                "无法导出预设文档。",
                reason=str(error),
            ) from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if name is not None:
                try:
                    Path(name).unlink(missing_ok=True)
                except OSError:
                    pass

    def _archive_index_bytes(self, raw: bytes, *, corrupt: bool) -> Path:
        try:
            Path(native_path(self.history_path)).mkdir(exist_ok=True)
        except OSError as error:
            raise _failure(
                "library_io_error",
                "无法创建预设库历史目录。",
                reason=str(error),
            ) from error
        _guard_path(self.history_path)
        suffix = "corrupt" if corrupt else "previous"
        destination = self.history_path / f"index-{_sha256(raw)}-{suffix}.json"
        if _lstat(destination) is None:
            _atomic_replace(destination, raw, expected=None)
        else:
            stored = _read_bytes(
                destination,
                limit=MAX_INDEX_BYTES,
                code="library_corrupt",
                label="预设库历史索引",
            )
            if stored != raw:
                raise _failure(
                    "library_corrupt",
                    "预设库历史索引摘要路径包含不同内容。",
                )
        return destination

    def recover_previous(self) -> dict[str, Any]:
        with self._write_lock():
            self._read_previous()
            current_info = _lstat(self.index_path)
            current_raw: bytes | None = None
            current_corrupt = False
            if current_info is not None:
                if (
                    _is_reparse(self.index_path, current_info)
                    or not stat.S_ISREG(current_info.st_mode)
                ):
                    raise _failure(
                        "library_corrupt",
                        "当前预设库索引不是安全的普通文件。",
                    )
                current_raw = _read_bytes(
                    self.index_path,
                    limit=MAX_INDEX_BYTES,
                    code="library_corrupt",
                    label="当前预设库索引",
                )
                try:
                    _decode_index(current_raw)
                    self._read_current()
                except PresetError:
                    current_corrupt = True
                self._archive_index_bytes(current_raw, corrupt=current_corrupt)
            # Re-read and revalidate the recovery source immediately before CAS
            # promotion; document bytes remain immutable throughout.
            previous = self._read_previous()
            _atomic_replace(
                self.index_path,
                previous.raw,
                expected=current_raw,
            )
            return self._snapshot_from(previous)


__all__ = [
    "MAX_ALIAS_CHARACTERS",
    "MAX_INDEX_BYTES",
    "MAX_LIBRARY_BYTES",
    "MAX_LIBRARY_ENTRIES",
    "PresetLibrary",
]
