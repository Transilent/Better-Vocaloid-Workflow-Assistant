"""Starter mix presets for the VWA P1 mixing workspace.

These recipes follow appendix A of the 2026-10-30 mixing plan.  They are
starting points, not claims of a finished mix; the UI copy should ask the user
to audition and adjust each source.  The module is pure standard library and
returns JSON-compatible copies so callers cannot mutate the catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import math
import unicodedata
from typing import Any

from mixer_state import (
    EQ_BAND_TYPES,
    MAX_PAN,
    MAX_REVERB_PREDELAY_MS,
    MAX_REVERB_SEND,
    MAX_WIDTH,
    MIN_PAN,
    MIN_REVERB_PREDELAY_MS,
    MIN_REVERB_SEND,
    MIN_WIDTH,
    MixState,
    REVERB_PROFILES,
    normalize_eq_bands,
)


__all__ = [
    "PRESET_KEYS",
    "apply_preset",
    "catalog",
    "resolve_preset",
]


DESCRIPTION = "起点预设，需按素材微调。"
PRESET_KEYS = (
    "clean_lead",
    "thick_harmony",
    "intimate",
    "wide_chorus",
    "opera_bright",
    "duet_balance",
)
_PAN_MODES = ("center", "alternate", "lead_harmony")


def _band(
    band_type: str,
    freq_hz: float,
    gain_db: float = 0.0,
    q: float = 0.707,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "type": band_type,
        "freq_hz": float(freq_hz),
        "gain_db": float(gain_db),
        "q": float(q),
        "enabled": enabled,
    }


def _eq(
    highpass_hz: float,
    lowshelf_hz: float | None,
    lowshelf_db: float,
    bell_hz: float,
    bell_db: float,
    bell_q: float,
    highshelf_hz: float | None,
    highshelf_db: float,
) -> list[dict[str, Any]]:
    return [
        _band("highpass", highpass_hz),
        _band(
            "lowshelf",
            lowshelf_hz if lowshelf_hz is not None else 260.0,
            lowshelf_db,
            enabled=lowshelf_hz is not None,
        ),
        _band("bell", bell_hz, bell_db, bell_q),
        _band(
            "highshelf",
            highshelf_hz if highshelf_hz is not None else 10_000.0,
            highshelf_db,
            enabled=highshelf_hz is not None,
        ),
    ]


_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "clean_lead",
        "label": "清透主唱",
        "description": DESCRIPTION,
        "eq_bands": _eq(90, None, 0.0, 3_200, 2.5, 0.8, 10_000, 1.5),
        "compression_strength": 0.30,
        "reverb_profile": "natural",
        "reverb_mix": 0.14,
        "reverb_predelay_ms": 20.0,
        "width": 1.0,
        "pan": 0.0,
        "harmony_pan": 0.0,
        "pan_mode": "center",
        "reverb_send": 0.80,
    },
    {
        "key": "thick_harmony",
        "label": "厚实和声",
        "description": DESCRIPTION,
        "eq_bands": _eq(140, 260, 1.5, 2_500, 1.0, 0.8, None, 0.0),
        "compression_strength": 0.20,
        "reverb_profile": "natural",
        "reverb_mix": 0.18,
        "reverb_predelay_ms": 24.0,
        "width": 1.0,
        "pan": -0.20,
        "harmony_pan": 0.20,
        "pan_mode": "alternate",
        "reverb_send": 0.55,
    },
    {
        "key": "intimate",
        "label": "贴耳近讲",
        "description": DESCRIPTION,
        "eq_bands": _eq(110, None, 0.0, 4_000, 3.0, 1.0, None, 0.0),
        "compression_strength": 0.45,
        "reverb_profile": "small",
        "reverb_mix": 0.08,
        "reverb_predelay_ms": 6.0,
        "width": 1.0,
        "pan": 0.0,
        "harmony_pan": 0.0,
        "pan_mode": "center",
        "reverb_send": 0.35,
    },
    {
        "key": "wide_chorus",
        "label": "宽阔副歌",
        "description": DESCRIPTION,
        "eq_bands": _eq(80, None, 0.0, 3_000, 1.5, 0.8, 12_000, 3.0),
        "compression_strength": 0.25,
        "reverb_profile": "wide",
        "reverb_mix": 0.26,
        "reverb_predelay_ms": 30.0,
        "width": 1.3,
        "pan": 0.0,
        "harmony_pan": 0.0,
        "pan_mode": "center",
        "reverb_send": 0.90,
    },
    {
        "key": "opera_bright",
        "label": "戏腔清亮",
        "description": DESCRIPTION,
        "eq_bands": _eq(160, None, 0.0, 5_000, 3.5, 1.2, 11_000, 2.0),
        "compression_strength": 0.18,
        "reverb_profile": "natural",
        "reverb_mix": 0.12,
        "reverb_predelay_ms": 16.0,
        "width": 1.0,
        "pan": 0.0,
        "harmony_pan": 0.0,
        "pan_mode": "center",
        "reverb_send": 0.60,
    },
    {
        "key": "duet_balance",
        "label": "对唱平衡",
        "description": DESCRIPTION,
        "eq_bands": _eq(100, None, 0.0, 3_200, 2.0, 0.8, 10_000, 1.0),
        "compression_strength": 0.28,
        "reverb_profile": "natural",
        "reverb_mix": 0.15,
        "reverb_predelay_ms": 22.0,
        "width": 1.0,
        "pan": 0.0,
        "harmony_pan": 0.25,
        "pan_mode": "lead_harmony",
        "reverb_send": 0.75,
    },
)

_ALIASES = {
    "clean_lead": "clean_lead",
    "clear_lead": "clean_lead",
    "clear_vocal": "clean_lead",
    "清透主唱": "clean_lead",
    "thick_harmony": "thick_harmony",
    "rich_harmony": "thick_harmony",
    "厚实和声": "thick_harmony",
    "intimate": "intimate",
    "close_mic": "intimate",
    "贴耳近讲": "intimate",
    "wide_chorus": "wide_chorus",
    "宽阔副歌": "wide_chorus",
    "opera_bright": "opera_bright",
    "bright_opera": "opera_bright",
    "戏腔清亮": "opera_bright",
    "duet_balance": "duet_balance",
    "duet": "duet_balance",
    "对唱平衡": "duet_balance",
}


def _bounded(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite number.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(
            f"{label} must be between {minimum:g} and {maximum:g} inclusive."
        )
    return number


def _validated(raw: Mapping[str, Any]) -> dict[str, Any]:
    key = raw.get("key")
    label = raw.get("label")
    if key not in PRESET_KEYS or not isinstance(label, str) or not label:
        raise ValueError("Invalid built-in mix preset identity.")
    bands = normalize_eq_bands(raw.get("eq_bands"))
    if len(bands or []) != 4:
        raise ValueError(f"{key} must define exactly four EQ bands.")
    if tuple(band["type"] for band in (bands or [])) != EQ_BAND_TYPES:
        raise ValueError(
            f"{key} EQ bands must be highpass, lowshelf, bell, highshelf."
        )
    profile = raw.get("reverb_profile")
    if not isinstance(profile, str) or profile not in REVERB_PROFILES:
        raise ValueError(f"{key} has an invalid reverb profile.")
    pan_mode = raw.get("pan_mode", "center")
    if pan_mode not in _PAN_MODES:
        raise ValueError(f"{key} has an invalid pan mode.")
    pan = _bounded(raw.get("pan", 0.0), f"{key}.pan", MIN_PAN, MAX_PAN)
    harmony_pan = _bounded(
        raw.get("harmony_pan", 0.0),
        f"{key}.harmony_pan",
        MIN_PAN,
        MAX_PAN,
    )
    reverb_send = _bounded(
        raw.get("reverb_send", 1.0),
        f"{key}.reverb_send",
        MIN_REVERB_SEND,
        MAX_REVERB_SEND,
    )
    return {
        "key": key,
        "label": label,
        "description": str(raw.get("description", DESCRIPTION)),
        "eq_bands": bands,
        "compression_strength": _bounded(
            raw.get("compression_strength", 0.0),
            f"{key}.compression_strength",
            0.0,
            1.0,
        ),
        "reverb_profile": profile,
        "reverb_mix": _bounded(
            raw.get("reverb_mix", 0.0),
            f"{key}.reverb_mix",
            0.0,
            1.0,
        ),
        "reverb_predelay_ms": _bounded(
            raw.get("reverb_predelay_ms", 0.0),
            f"{key}.reverb_predelay_ms",
            MIN_REVERB_PREDELAY_MS,
            MAX_REVERB_PREDELAY_MS,
        ),
        "width": _bounded(
            raw.get("width", 1.0),
            f"{key}.width",
            MIN_WIDTH,
            MAX_WIDTH,
        ),
        "pan": pan,
        "harmony_pan": harmony_pan,
        "pan_mode": pan_mode,
        "reverb_send": reverb_send,
    }


_PRESETS = {raw["key"]: _validated(raw) for raw in _DEFINITIONS}
if tuple(_PRESETS) != PRESET_KEYS:
    raise RuntimeError("Built-in mix preset order is not stable.")


def _canonical_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("Preset key must be a string.")
    normalized = unicodedata.normalize("NFC", key).strip().casefold()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    canonical = _ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(
            "Unknown preset. Available keys: " + ", ".join(PRESET_KEYS) + "."
        )
    return canonical


def catalog() -> list[dict[str, Any]]:
    """Return fresh, deterministic preset documents in UI order."""

    return [copy.deepcopy(_PRESETS[key]) for key in PRESET_KEYS]


def resolve_preset(key: str) -> dict[str, Any]:
    """Resolve a canonical key, English alias, or Chinese label."""

    return copy.deepcopy(_PRESETS[_canonical_key(key)])


def _suggested_pan(
    preset: Mapping[str, Any],
    index: int,
    total: int,
) -> float:
    mode = preset["pan_mode"]
    if mode == "alternate":
        return preset["harmony_pan"] if index % 2 else -preset["harmony_pan"]
    if mode == "lead_harmony" and index > 0:
        return preset["harmony_pan"] if index % 2 else -preset["harmony_pan"]
    return preset["pan"]


def apply_preset(
    state: MixState,
    key: str,
    lock_mix: bool = True,
) -> MixState:
    """Apply one starter preset to ``state`` in place and return it.

    Processing fields are always replaced.  With ``lock_mix=True`` the
    user-owned gain, offset, pan, send, mute, and solo values remain untouched.
    With ``lock_mix=False`` the preset's suggested pan placement and
    ``reverb_send`` are also applied.  Presets intentionally do not reset fader
    gain or timing offsets because appendix A specifies neither.
    """

    if not isinstance(state, MixState):
        raise TypeError("state must be a MixState.")
    if type(lock_mix) is not bool:
        raise ValueError("lock_mix must be a boolean.")
    preset = resolve_preset(key)
    bands = normalize_eq_bands(preset["eq_bands"])
    assert bands is not None
    state.eq_bands = bands
    highpass = next(band for band in bands if band["type"] == "highpass")
    bell = next(band for band in bands if band["type"] == "bell")
    state.eq_low_cut_hz = (
        highpass["freq_hz"] if highpass["enabled"] else 0.0
    )
    state.eq_presence_db = bell["gain_db"] if bell["enabled"] else 0.0
    state.compression_strength = preset["compression_strength"]
    state.reverb_profile = preset["reverb_profile"]
    state.reverb_mix = preset["reverb_mix"]
    state.reverb_predelay_ms = preset["reverb_predelay_ms"]
    state.width = preset["width"]
    state.active_preset = preset["key"]
    if not lock_mix:
        total = len(state.vocals)
        for index, track in enumerate(state.vocals):
            track.pan = _suggested_pan(preset, index, total)
            track.reverb_send = preset["reverb_send"]
    return state