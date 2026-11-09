"""Inspect exact SVP sidecar receipts without following linked paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from typing import Any


MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_PROJECT_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_REPARSE_POINT = 0x400
_CHUNK = 1024 * 1024
_SIDECARS = (
    ("tuning", ".tuning.json", "vwa.tuning-result"),
    ("style", ".style.json", "vwa.style-candidate"),
)


class _DuplicateKey(ValueError):
    pass


def _native(path: str) -> str:
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _sha(value: Any) -> str | None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        return None
    return value.lower()


def _path_issue(path: str) -> str | None:
    """Check the path itself and every parent without resolving links."""
    value = os.path.abspath(path)
    while True:
        try:
            attributes = os.lstat(_native(value))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return "路径无法进行安全检查"
        else:
            if stat.S_ISLNK(attributes.st_mode):
                return "路径包含符号链接"
            if getattr(attributes, "st_file_attributes", 0) & _REPARSE_POINT:
                return "路径包含重解析点"
        parent = os.path.dirname(value)
        if parent == value:
            return None
        value = parent


def _open_regular(path: str, limit: int) -> tuple[int | None, int, str | None]:
    issue = _path_issue(path)
    if issue:
        return None, 0, issue
    native = _native(path)
    try:
        before = os.lstat(native)
    except FileNotFoundError:
        return None, 0, "文件不存在"
    except (OSError, ValueError):
        return None, 0, "文件无法检查"
    if not stat.S_ISREG(before.st_mode):
        return None, 0, "不是普通文件"
    if getattr(before, "st_file_attributes", 0) & _REPARSE_POINT:
        return None, 0, "不是可读取的普通文件"
    if before.st_size > limit:
        return None, 0, "超过读取上限"
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(native, flags)
    except OSError:
        return None, 0, "文件无法打开"
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_file_attributes", 0) & _REPARSE_POINT):
            os.close(descriptor)
            return None, 0, "不是可读取的普通文件"
        if opened.st_size > limit:
            os.close(descriptor)
            return None, 0, "超过读取上限"
        return descriptor, opened.st_size, None
    except BaseException:
        os.close(descriptor)
        raise


def _finish_read(descriptor: int, opened_size: int, total: int) -> str | None:
    try:
        after = os.fstat(descriptor)
    except OSError:
        return "读取期间文件状态无法核对"
    if after.st_size != opened_size or total != opened_size:
        return "读取期间文件发生变化"
    return None


def _read_limited(path: str, limit: int) -> tuple[bytes | None, str | None, str | None]:
    descriptor, size, issue = _open_regular(path, limit)
    if issue:
        return None, None, issue
    assert descriptor is not None
    pieces: list[bytes] = []
    total = 0
    try:
        while total <= limit:
            chunk = os.read(descriptor, min(_CHUNK, limit + 1 - total))
            if not chunk:
                break
            pieces.append(chunk)
            total += len(chunk)
        if total > limit:
            return None, None, "超过读取上限"
        issue = _finish_read(descriptor, size, total)
        if issue:
            return None, None, issue
        data = b"".join(pieces)
        return data, hashlib.sha256(data).hexdigest(), None
    except OSError:
        return None, None, "文件读取失败"
    finally:
        os.close(descriptor)


def _hash_limited(path: str, limit: int) -> tuple[str | None, str | None]:
    descriptor, size, issue = _open_regular(path, limit)
    if issue:
        return None, issue
    assert descriptor is not None
    digest = hashlib.sha256()
    total = 0
    try:
        while total <= limit:
            chunk = os.read(descriptor, min(_CHUNK, limit + 1 - total))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        if total > limit:
            return None, "超过读取上限"
        issue = _finish_read(descriptor, size, total)
        if issue:
            return None, issue
        return digest.hexdigest(), None
    except OSError:
        return None, "文件读取失败"
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _parse_receipt(raw: bytes) -> dict[str, Any] | None:
    def reject_constant(value: str) -> None:
        raise ValueError(value)

    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _project_identity(project: Any) -> tuple[str | None, str | None]:
    declared = None
    if isinstance(project, Mapping):
        path = project.get("path")
        declared = _sha(project.get("sha256"))
    elif isinstance(project, (str, os.PathLike)):
        path = project
    else:
        return None, None
    if not isinstance(path, (str, os.PathLike)):
        return None, declared
    try:
        value = os.fspath(path)
    except TypeError:
        return None, declared
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        return None, declared
    return os.path.abspath(value), declared


def _receipt_name(path: str) -> str:
    return os.path.basename(path)[:255]


def _result(kind: str, verified: bool, candidate_sha: str | None,
            receipt_sha: str | None, receipt_name: str,
            source_sha: str | None = None, reason: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": kind,
        "verified": verified,
        "candidate_sha256": candidate_sha,
    }
    if source_sha is not None:
        value["source_sha256"] = source_sha
    value["receipt_sha256"] = receipt_sha
    value["receipt_name"] = receipt_name
    if reason is not None:
        value["reason"] = reason[:240]
    return value


def _unverified(candidate_sha: str | None, receipt_sha: str | None,
                receipt_name: str, reason: str,
                source_sha: str | None = None) -> dict[str, Any]:
    return _result("unverified_candidate", False, candidate_sha, receipt_sha,
                   receipt_name, source_sha, reason)


def inspect_candidate_origin(project: Any, *, sha256: str | None = None) -> dict[str, Any] | None:
    """Return a bounded receipt summary for one exact sibling SVP sidecar.

    The tuning sidecar wins when both supported sidecars exist. Only the exact
    appended names ``<project>.tuning.json`` and ``<project>.style.json`` are
    inspected. Receipt paths and other fields are never used to open files.
    ``None`` means that neither sibling was observed; it is not proof that the
    project has no tuning or style history.
    """
    candidate, declared_sha = _project_identity(project)
    if candidate is None:
        return None

    selected: tuple[str, str, str] | None = None
    for record in _SIDECARS:
        sidecar = candidate + record[1]
        if os.path.lexists(_native(sidecar)):
            selected = record
            break
    if selected is None:
        return None

    expected_kind, suffix, receipt_kind = selected
    receipt_path = candidate + suffix
    receipt_name = _receipt_name(receipt_path)

    supplied_sha = sha256
    if supplied_sha is not None and _sha(supplied_sha) is None:
        return _unverified(declared_sha, None, receipt_name,
                           "调用方提供的候选 SHA-256 格式无效。")

    candidate_sha, issue = _hash_limited(candidate, MAX_PROJECT_BYTES)
    if issue is not None:
        return _unverified(declared_sha, None, receipt_name,
                           "无法核对当前工程字节：" + issue + "。")
    if supplied_sha is not None and _sha(supplied_sha) != candidate_sha:
        return _unverified(candidate_sha, None, receipt_name,
                           "当前工程字节与调用方读取的 SHA-256 不一致。")

    raw, receipt_sha, issue = _read_limited(receipt_path, MAX_RECEIPT_BYTES)
    if issue is not None:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "回执无法使用：" + issue + "。")

    receipt = _parse_receipt(raw or b"")
    if receipt is None:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "回执不是有效 UTF-8 JSON 对象。")

    source_sha = _sha(receipt.get("source_sha256"))
    output_sha = _sha(receipt.get("output_sha256"))
    actual_kind = receipt.get("kind")
    if actual_kind == receipt_kind.replace("vwa.", "bvwa.", 1):
        actual_kind = receipt_kind
    schema = receipt.get("schema")
    if type(schema) is not int or schema != 1 or actual_kind != receipt_kind:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "回执类型或格式无法识别。", source_sha)
    if source_sha is None or output_sha is None:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "回执缺少有效的来源或候选 SHA-256。", source_sha)
    if expected_kind == "tuning" and receipt.get("receipt_verified") is not True:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "调音回执自身尚未通过验证。", source_sha)
    if output_sha != candidate_sha:
        return _unverified(candidate_sha, receipt_sha, receipt_name,
                           "回执记录的候选 SHA-256 与当前工程字节不一致。", source_sha)

    return _result(expected_kind, True, candidate_sha, receipt_sha,
                   receipt_name, source_sha)
