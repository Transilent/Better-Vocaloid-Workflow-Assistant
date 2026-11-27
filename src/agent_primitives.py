"""Shared canonical JSON, hashing, path, and numeric primitives for agent modules."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Type
import uuid

from common import SidecarBusy, exclusive_sidecar, native_path

MAX_JSON = 16 * 1024**2


class ControlError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        if details is not None:
            self.details = details


def io_path(path: Any) -> Path:
    """Apply extended Windows path syntax only at filesystem boundaries."""
    return Path(native_path(path))


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def canonical_json(
    value: Any,
    *,
    pretty: bool = False,
    error_message: str = "值必须只包含有限的 JSON 数据。",
    error_factory: Callable[[Exception], Exception] | None = None,
) -> str:
    """Serialize *value* to deterministic JSON, rejecting NaN/Infinity."""
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    try:
        text = json.dumps(value, **options)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        if error_factory is not None:
            raise error_factory(error) from error
        raise ValueError(error_message) from error
    if pretty:
        text += "\n"
    return text


def canonical_json_bytes(
    value: Any,
    *,
    pretty: bool = False,
    error_message: str = "值必须只包含有限的 JSON 数据。",
    error_factory: Callable[[Exception], Exception] | None = None,
) -> bytes:
    """Serialize *value* to deterministic UTF-8 JSON bytes."""
    return canonical_json(
        value,
        pretty=pretty,
        error_message=error_message,
        error_factory=error_factory,
    ).encode("utf-8")


def json_digest(
    value: Any,
    *,
    error_message: str = "值必须只包含有限的 JSON 数据。",
) -> str:
    """Return the SHA-256 hex digest of *value*'s canonical JSON encoding."""
    return sha256_bytes(canonical_json_bytes(value, error_message=error_message))


def finite_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    preserve_int: bool = False,
    coerce_integral: bool = False,
    bool_note: bool = False,
    error_type: Type[Exception] = ValueError,
) -> int | float:
    """Validate that *value* is a real finite int/float (excluding bool)."""
    if type(value) not in (int, float):
        suffix = " 必须是有限数值；布尔值不算数值。" if bool_note else "必须是有限数值。" if minimum is not None else " 必须是有限数值。"
        raise error_type(f"{label}{suffix}")
    number = float(value)
    if not math.isfinite(number):
        sep = "" if minimum is not None else " "
        raise error_type(f"{label}{sep}必须是有限数值。")
    if minimum is not None and maximum is not None and not minimum <= number <= maximum:
        raise error_type(f"{label}必须在 {minimum:g} 到 {maximum:g} 之间。")
    if preserve_int and type(value) is int:
        return int(value)
    if coerce_integral and number.is_integer():
        return int(number)
    return number


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_path(value, roots=None):
    path = Path(value)
    if not path.is_absolute():
        raise ControlError('invalid_path', '需要明确的绝对路径。')
    path = path.absolute()
    for part in (path, *path.parents):
        try:
            attributes = io_path(part).lstat()
        except FileNotFoundError:
            continue
        if io_path(part).is_symlink() or getattr(attributes, 'st_file_attributes', 0) & 1024:
            raise ControlError('unsafe_path', '路径包含链接或重解析点。')
    path = path.resolve()
    if roots is not None and not any(path.is_relative_to(root) for root in roots):
        allowed_roots = [os.path.abspath(os.fspath(root)) for root in roots]
        raise ControlError(
            'outside_roots',
            '路径不在此服务配置允许的目录中。',
            details={
                'path': str(path),
                'allowed_roots': allowed_roots,
                'import_guidance': {
                    'copy_from_outside_roots': False,
                    'instruction': ('请由用户或调用方把文件复制或移动到任一 allowed_roots 内，'
                                    '再用新绝对路径重试。服务不会自动复制或扩大允许目录。'
                                    '本地音频准备应直接使用允许素材目录内的新路径；'
                                    'assets_import 仅用于其支持的素材和允许根。'),
                    'managed_snapshot': {'tool': 'vwa_input', 'action': 'assets_import'},
                },
            },
        )
    return path


def read_json(path, *, max_bytes=None):
    limit = MAX_JSON if max_bytes is None else max_bytes
    path = safe_path(path)
    if io_path(path).stat().st_size > limit:
        raise ControlError('too_large', 'JSON 记录超出读取限制。')
    value = json.loads(io_path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ControlError('invalid_record', '记录必须是 JSON 对象。')
    return value


def _json_payload(value, *, max_bytes=None):
    # Use the same byte budget as read_json. Validate before creating any files
    # so an oversized update cannot replace a readable record or leave a tmp.
    limit = MAX_JSON if max_bytes is None else max_bytes
    if not isinstance(value, dict):
        raise ControlError('invalid_record', '记录必须是 JSON 对象。')
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(payload) > limit:
        raise ControlError('too_large', 'JSON 记录超出读写限制。')
    return payload


def atomic_json(path, value, *, max_bytes=None):
    payload = _json_payload(value, max_bytes=max_bytes)
    path = safe_path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with io_path(temporary).open('xb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                os.replace(io_path(temporary), io_path(path))
                break
            except OSError as error:
                if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.01 * 2**attempt)
    finally:
        io_path(temporary).unlink(missing_ok=True)


def file_hash(path, cancel=lambda: None):
    sha = hashlib.sha256()
    with io_path(safe_path(path)).open('rb') as handle:
        while block := handle.read(1024 * 1024):
            cancel()
            sha.update(block)
    return sha.hexdigest()


@contextmanager
def file_lock(path, *, blocking=True, cancel=lambda: None):
    """Owned sidecar locks only. Never creates a lock in a user's job directory."""
    path = safe_path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_sidecar(path, blocking=blocking, cancel=cancel):
            yield
    except SidecarBusy:
        raise ControlError('busy', '该操作正在执行。') from None


def lock_held(path):
    try:
        with file_lock(path, blocking=False):
            return False
    except ControlError as error:
        if error.code != 'busy':
            raise
        return True
