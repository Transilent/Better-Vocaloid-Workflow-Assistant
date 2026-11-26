"""Shared canonical JSON, hashing, path, and numeric primitives for agent modules."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Type

from common import native_path


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
