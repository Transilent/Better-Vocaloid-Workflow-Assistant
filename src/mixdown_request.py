"""Pure mixdown processing snapshots and execution request construction.

The preset editor, continuous audition, and export renderer all start from the
same plain-dictionary processing snapshot.  This module has no Qt, media, or
file-probing dependencies; callers supply the actual render sample rate.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from typing import Any

from mixdown_contract import (
    MAX_VOCALS,
    REVERB_PROFILES,
    finite_number,
    normalize_eq_bands,
    project_eq_band,
    valid_sample_rate,
)


PROCESSING_APPLY_SCOPE = "vocal_processing_and_output_width"

__all__ = [
    "MAX_VOCALS",
    "PROCESSING_APPLY_SCOPE",
    "REVERB_PROFILES",
    "build_mixdown_request",
    "build_processing_state",
    "processing_request_fields",
]


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _bounded_number(value: Any, label: str, minimum: float,
                    maximum: float) -> float:
    number = finite_number(value, label)
    if not minimum <= number <= maximum:
        raise ValueError(
            f"{label} must be between {minimum:g} and {maximum:g} inclusive")
    return float(number)


def _path_text(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-empty path")
    try:
        text = os.fspath(value)
    except TypeError as error:
        raise ValueError(f"{label} must be a non-empty path") from error
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    text = str(text)
    if not text.strip():
        raise ValueError(f"{label} must be a non-empty path")
    if "\x00" in text:
        raise ValueError(f"{label} contains an invalid NUL character")
    return text


def _project_bands(value: Any, sample_rate: float) -> list[dict[str, Any]]:
    bands = normalize_eq_bands(value)
    if bands is None:
        return []
    return [
        project_eq_band(band, sample_rate=sample_rate)
        for band in bands
    ]


def build_processing_state(
    *,
    sample_rate: Any,
    eq_enabled: Any,
    eq_bands: Any,
    compression_enabled: Any,
    compression_strength: Any,
    reverb_enabled: Any,
    reverb_profile: Any,
    reverb_mix: Any,
    reverb_predelay_ms: Any,
    width: Any,
) -> dict[str, Any]:
    """Return a fresh portable processing snapshot.

    ``eq_bands`` are validated by :func:`normalize_eq_bands` and projected to
    the supplied source render sample rate.  The returned dictionaries and
    lists never alias caller-owned state.
    """
    rate = valid_sample_rate(sample_rate)
    profile = reverb_profile
    if not isinstance(profile, str) or profile not in REVERB_PROFILES:
        raise ValueError("reverb_profile must be small, natural, or wide")
    return {
        "apply_scope": PROCESSING_APPLY_SCOPE,
        "eq_enabled": _boolean(eq_enabled, "eq_enabled"),
        "eq_bands": _project_bands(eq_bands, rate),
        "compression_enabled": _boolean(
            compression_enabled, "compression_enabled"),
        "compression_strength": _bounded_number(
            compression_strength, "compression_strength", 0.0, 1.0),
        "reverb_enabled": _boolean(reverb_enabled, "reverb_enabled"),
        "reverb_profile": str(profile),
        "reverb_mix": _bounded_number(reverb_mix, "reverb_mix", 0.0, 1.0),
        "reverb_predelay_ms": int(round(_bounded_number(
            reverb_predelay_ms, "reverb_predelay_ms", 0.0, 120.0))),
        "width": _bounded_number(width, "width", 0.0, 2.0),
    }


def _processing_value(processing: Mapping[str, Any], name: str) -> Any:
    if name not in processing:
        raise ValueError(f"processing is missing {name}")
    return processing[name]


def processing_request_fields(
    processing: Mapping[str, Any],
    *,
    sample_rate: Any,
    bypass_processing: Any = False,
) -> dict[str, Any]:
    """Project one processing snapshot to renderer request fields.

    A/B bypass affects only EQ, compression, and reverb.  Width remains the
    active output setting exactly as it does in the established request path.
    """
    if not isinstance(processing, Mapping):
        raise TypeError("processing must be an object")
    bypass = _boolean(bypass_processing, "bypass_processing")
    scope = _processing_value(processing, "apply_scope")
    if scope != PROCESSING_APPLY_SCOPE:
        raise ValueError("processing apply_scope is not supported")

    snapshot = build_processing_state(
        sample_rate=sample_rate,
        eq_enabled=_processing_value(processing, "eq_enabled"),
        eq_bands=_processing_value(processing, "eq_bands"),
        compression_enabled=_processing_value(
            processing, "compression_enabled"),
        compression_strength=_processing_value(
            processing, "compression_strength"),
        reverb_enabled=_processing_value(processing, "reverb_enabled"),
        reverb_profile=_processing_value(processing, "reverb_profile"),
        reverb_mix=_processing_value(processing, "reverb_mix"),
        reverb_predelay_ms=_processing_value(
            processing, "reverb_predelay_ms"),
        width=_processing_value(processing, "width"),
    )

    use_eq = snapshot["eq_enabled"] and not bypass
    use_compression = snapshot["compression_enabled"] and not bypass
    use_reverb = snapshot["reverb_enabled"] and not bypass
    bands = snapshot["eq_bands"]
    if not use_eq:
        bands = [dict(band, enabled=False) for band in bands]

    highpass = next(
        (band for band in bands if band["type"] == "highpass"), None)
    bell = next((band for band in bands if band["type"] == "bell"), None)
    return {
        "eq_low_cut_hz": (
            float(highpass["freq_hz"]) if use_eq and highpass is not None
            else 0.0),
        "eq_presence_db": (
            float(bell["gain_db"]) if use_eq and bell is not None else 0.0),
        "eq_bands": bands,
        "compression_strength": (
            snapshot["compression_strength"] if use_compression else 0.0),
        "reverb_mix": snapshot["reverb_mix"] if use_reverb else 0.0,
        "reverb_profile": snapshot["reverb_profile"],
        "reverb_predelay_ms": (
            snapshot["reverb_predelay_ms"] if use_reverb else 0.0),
        "width": snapshot["width"],
    }


def _vocal_request(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"vocals[{index}] must be an object")
    required = (
        "path", "gain_db", "offset_seconds", "mute", "solo", "pan",
        "reverb_send",
    )
    missing = [name for name in required if name not in row]
    if missing:
        raise ValueError(
            f"vocals[{index}] is missing {', '.join(missing)}")
    return {
        "path": _path_text(row["path"], f"vocals[{index}].path"),
        "gain_db": _bounded_number(
            row["gain_db"], f"vocals[{index}].gain_db", -120.0, 24.0),
        "offset_seconds": _bounded_number(
            row["offset_seconds"], f"vocals[{index}].offset_seconds",
            -21_600.0, 21_600.0),
        "mute": _boolean(row["mute"], f"vocals[{index}].mute"),
        "solo": _boolean(row["solo"], f"vocals[{index}].solo"),
        "pan": _bounded_number(
            row["pan"], f"vocals[{index}].pan", -1.0, 1.0),
        "reverb_send": _bounded_number(
            row["reverb_send"], f"vocals[{index}].reverb_send", 0.0, 1.0),
    }


def build_mixdown_request(
    processing: Mapping[str, Any],
    *,
    vocals: Sequence[Mapping[str, Any]],
    instrumental_path: Any,
    output_path: Any,
    instrumental_db: Any,
    instrumental_mute: Any,
    instrumental_solo: Any,
    instrumental_pan: Any,
    sample_rate: Any,
    bypass_processing: Any = False,
) -> dict[str, Any]:
    """Build one fresh renderer request without probing any media path."""
    if isinstance(vocals, (str, bytes)) or not isinstance(vocals, Sequence):
        raise ValueError("vocals must be a sequence of track objects")
    if not 1 <= len(vocals) <= MAX_VOCALS:
        raise ValueError(f"vocals must contain between 1 and {MAX_VOCALS} tracks")
    tracks = [_vocal_request(row, index) for index, row in enumerate(vocals)]
    result = {
        "vocals": tracks,
        "instrumental_path": _path_text(
            instrumental_path, "instrumental_path"),
        "output_path": _path_text(output_path, "output_path"),
        "instrumental_db": _bounded_number(
            instrumental_db, "instrumental_db", -120.0, 24.0),
        "instrumental_mute": _boolean(
            instrumental_mute, "instrumental_mute"),
        "instrumental_solo": _boolean(
            instrumental_solo, "instrumental_solo"),
        "instrumental_pan": _bounded_number(
            instrumental_pan, "instrumental_pan", -1.0, 1.0),
    }
    result.update(processing_request_fields(
        processing,
        sample_rate=sample_rate,
        bypass_processing=bypass_processing,
    ))
    return result
