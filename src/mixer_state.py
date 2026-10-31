"""Pure-state helpers for the VWA vocal mixdown workspace.

The module deliberately has no Qt or audio-backend imports.  It owns the
editable mix snapshot, the fader dB <-> normalised-position conversion, and the
JSON-compatible request boundary used by the mixdown UI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import copy
import json
import math
import os
from typing import Any


__all__ = [
    "DEFAULT_REVERB_PROFILE",
    "EQ_BAND_TYPES",
    "MAX_OFFSET_SECONDS",
    "MAX_VOCALS",
    "REVERB_PROFILES",
    "MixState",
    "TrackState",
    "db_to_norm",
    "norm_to_db",
    "normalize_eq_bands",
]


MAX_VOCALS = 3
MIN_GAIN_DB = -120.0
MAX_GAIN_DB = 24.0
MIN_OFFSET_SECONDS = -(6.0 * 60.0 * 60.0)
MAX_OFFSET_SECONDS = 6.0 * 60.0 * 60.0
MIN_PAN = -1.0
MAX_PAN = 1.0
MIN_REVERB_SEND = 0.0
MAX_REVERB_SEND = 1.0
MIN_WIDTH = 0.0
MAX_WIDTH = 2.0
MIN_EQ_FREQ_HZ = 10.0
MAX_EQ_FREQ_HZ = 20_000.0
MIN_EQ_GAIN_DB = -24.0
MAX_EQ_GAIN_DB = 24.0
MIN_EQ_Q = 0.1
MAX_EQ_Q = 18.0
MAX_EQ_BANDS = 4
MIN_REVERB_PREDELAY_MS = 0.0
MAX_REVERB_PREDELAY_MS = 120.0
EQ_BAND_TYPES = ("highpass", "lowshelf", "bell", "highshelf")
REVERB_PROFILES = ("small", "natural", "wide")
DEFAULT_REVERB_PROFILE = "natural"

# Appendix-derived fader anchors.  Positions inside each interval use a
# monotone cubic Hermite (PCHIP) segment, not a linear amplitude approximation.
_DB_ANCHORS = (-60.0, -48.0, -24.0, -12.0, -6.0, 0.0, 6.0, 12.0)
_NORM_ANCHORS = (0.235, 0.330, 0.505, 0.632, 0.712, 0.800, 0.905, 1.000)
_FADER_FLOOR_DB = -120.0


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
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


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")
    return value


def _path_text(value: Any, label: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{label} is required.")
        return ""
    try:
        text = os.fspath(value)
    except TypeError:
        raise ValueError(f"{label} must be a path string.") from None
    if not isinstance(text, str):
        raise ValueError(f"{label} must be a path string.")
    if required and not text.strip():
        raise ValueError(f"{label} is required.")
    if "\x00" in text:
        raise ValueError(f"{label} contains an invalid NUL character.")
    return text


def _optional_seconds(
    value: Any,
    label: str,
    *,
    strictly_positive: bool = False,
) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number of seconds.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite number of seconds.") from None
    if not math.isfinite(number) or number < 0.0 or (
        strictly_positive and number <= 0.0
    ):
        relation = "greater than zero" if strictly_positive else "non-negative"
        raise ValueError(
            f"{label} must be a finite number of seconds ({relation})."
        )
    return number


def normalize_eq_bands(value: Any) -> list[dict[str, Any]] | None:
    """Return a validated, JSON-ready copy of an EQ-band sequence.

    ``None`` means that the caller is using the legacy two-band EQ fields.
    A present list is treated as explicit v3 intent, including an empty list.
    """

    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("eq_bands must be a list of at most four bands.")
    if len(value) > MAX_EQ_BANDS:
        raise ValueError("eq_bands must contain at most four bands.")
    bands: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        prefix = f"eq_bands[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{prefix} must be an object.")
        band_type = item.get("type")
        if not isinstance(band_type, str) or band_type not in EQ_BAND_TYPES:
            raise ValueError(
                f"{prefix}.type must be one of: {', '.join(EQ_BAND_TYPES)}."
            )
        bands.append({
            "type": band_type,
            "freq_hz": _number(
                item.get("freq_hz"),
                f"{prefix}.freq_hz",
                MIN_EQ_FREQ_HZ,
                MAX_EQ_FREQ_HZ,
            ),
            "gain_db": _number(
                item.get("gain_db", 0.0),
                f"{prefix}.gain_db",
                MIN_EQ_GAIN_DB,
                MAX_EQ_GAIN_DB,
            ),
            "q": _number(
                item.get("q", 0.707),
                f"{prefix}.q",
                MIN_EQ_Q,
                MAX_EQ_Q,
            ),
            "enabled": _boolean(item.get("enabled", True), f"{prefix}.enabled"),
        })
    return bands


def _pchip_slopes(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    count = len(xs)
    widths = [xs[index + 1] - xs[index] for index in range(count - 1)]
    deltas = [
        (ys[index + 1] - ys[index]) / widths[index]
        for index in range(count - 1)
    ]
    slopes = [0.0] * count
    slopes[0] = deltas[0]
    slopes[-1] = deltas[-1]
    for index in range(1, count - 1):
        left = deltas[index - 1]
        right = deltas[index]
        if left == 0.0 or right == 0.0 or left * right < 0.0:
            slopes[index] = 0.0
        else:
            first = 2.0 * widths[index] + widths[index - 1]
            second = widths[index] + 2.0 * widths[index - 1]
            slopes[index] = (first + second) / (
                first / left + second / right
            )
    return slopes


_FADER_SLOPES = _pchip_slopes(_DB_ANCHORS, _NORM_ANCHORS)


def db_to_norm(value: float) -> float:
    """Map decibels to the fader's stable 0..1 display position.

    The eight appendix anchors are hit exactly; in particular, 0 dB maps to
    0.800.  Values below -60 dB continue linearly to the -120 dB digital floor,
    while the literal value ``-inf`` maps to the fader bottom (0.0).
    """

    if isinstance(value, bool):
        raise ValueError("dB must be a number.")
    try:
        decibels = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("dB must be a number.") from None
    if math.isnan(decibels) or decibels == math.inf:
        raise ValueError("dB cannot be NaN or positive infinity.")
    if decibels == -math.inf or decibels <= _FADER_FLOOR_DB:
        return 0.0
    if decibels <= _DB_ANCHORS[0]:
        ratio = (decibels - _FADER_FLOOR_DB) / (
            _DB_ANCHORS[0] - _FADER_FLOOR_DB
        )
        return max(0.0, min(_NORM_ANCHORS[0], _NORM_ANCHORS[0] * ratio))
    if decibels >= _DB_ANCHORS[-1]:
        return 1.0

    upper = 1
    while upper < len(_DB_ANCHORS) and decibels > _DB_ANCHORS[upper]:
        upper += 1
    lower = upper - 1
    x0, x1 = _DB_ANCHORS[lower], _DB_ANCHORS[upper]
    y0, y1 = _NORM_ANCHORS[lower], _NORM_ANCHORS[upper]
    h = x1 - x0
    t = (decibels - x0) / h
    t2 = t * t
    t3 = t2 * t
    value_norm = (
        (2.0 * t3 - 3.0 * t2 + 1.0) * y0
        + (t3 - 2.0 * t2 + t) * h * _FADER_SLOPES[lower]
        + (-2.0 * t3 + 3.0 * t2) * y1
        + (t3 - t2) * h * _FADER_SLOPES[upper]
    )
    return max(0.0, min(1.0, value_norm))


def norm_to_db(value: float) -> float:
    """Return the inverse of :func:`db_to_norm` inside the fader range."""

    if isinstance(value, bool):
        raise ValueError("Fader position must be a number.")
    try:
        position = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Fader position must be a number.") from None
    if not math.isfinite(position) or not 0.0 <= position <= 1.0:
        raise ValueError("Fader position must be between 0.0 and 1.0 inclusive.")
    if position == 0.0:
        return -math.inf
    if position <= _NORM_ANCHORS[0]:
        ratio = (position - 0.0) / _NORM_ANCHORS[0]
        return _FADER_FLOOR_DB + ratio * (_DB_ANCHORS[0] - _FADER_FLOOR_DB)
    if position >= _NORM_ANCHORS[-1]:
        return _DB_ANCHORS[-1]

    upper = 1
    while upper < len(_NORM_ANCHORS) and position > _NORM_ANCHORS[upper]:
        upper += 1
    target = position
    low = _DB_ANCHORS[upper - 1]
    high = _DB_ANCHORS[upper]
    for _ in range(64):
        middle = (low + high) / 2.0
        if db_to_norm(middle) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@dataclass
class TrackState:
    """One editable vocal track in the mix snapshot."""

    path: str = ""
    gain_db: float = 0.0
    offset_seconds: float = 0.0
    mute: bool = False
    solo: bool = False
    pan: float = 0.0
    reverb_send: float = 1.0
    eq_bypass: bool = False

    def __post_init__(self) -> None:
        self.path = _path_text(self.path, "path")
        self.gain_db = _number(
            self.gain_db, "gain_db", MIN_GAIN_DB, MAX_GAIN_DB
        )
        self.offset_seconds = _number(
            self.offset_seconds,
            "offset_seconds",
            MIN_OFFSET_SECONDS,
            MAX_OFFSET_SECONDS,
        )
        self.mute = _boolean(self.mute, "mute")
        self.solo = _boolean(self.solo, "solo")
        self.pan = _number(self.pan, "pan", MIN_PAN, MAX_PAN)
        self.reverb_send = _number(
            self.reverb_send,
            "reverb_send",
            MIN_REVERB_SEND,
            MAX_REVERB_SEND,
        )
        self.eq_bypass = _boolean(self.eq_bypass, "eq_bypass")

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "TrackState":
        if not isinstance(request, Mapping):
            raise ValueError("A track request must be an object.")
        return cls(
            path=request.get("path", ""),
            gain_db=request.get("gain_db", 0.0),
            offset_seconds=request.get("offset_seconds", 0.0),
            mute=request.get("mute", False),
            solo=request.get("solo", False),
            pan=request.get("pan", 0.0),
            reverb_send=request.get("reverb_send", 1.0),
            eq_bypass=request.get("eq_bypass", False),
        )

    from_dict = from_request

    def has_v3_fields(self) -> bool:
        return (
            self.mute
            or self.solo
            or self.pan != 0.0
            or self.reverb_send != 1.0
            or self.eq_bypass
        )

    def to_request(self) -> dict[str, Any]:
        """Return only non-default track fields, preserving the old trio."""

        result: dict[str, Any] = {
            "path": self.path,
            "gain_db": self.gain_db,
            "offset_seconds": self.offset_seconds,
        }
        if self.mute:
            result["mute"] = True
        if self.solo:
            result["solo"] = True
        if self.pan != 0.0:
            result["pan"] = self.pan
        if self.reverb_send != 1.0:
            result["reverb_send"] = self.reverb_send
        if self.eq_bypass:
            result["eq_bypass"] = True
        return result

    def to_legacy_fields(self) -> dict[str, Any]:
        if self.has_v3_fields():
            raise ValueError(
                "A legacy single-vocal request cannot carry v3 track fields."
            )
        return {
            "vocal_path": self.path,
            "vocal_db": self.gain_db,
            "offset_seconds": self.offset_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "gain_db": self.gain_db,
            "offset_seconds": self.offset_seconds,
            "mute": self.mute,
            "solo": self.solo,
            "pan": self.pan,
            "reverb_send": self.reverb_send,
            "eq_bypass": self.eq_bypass,
        }


@dataclass
class MixState:
    """A JSON-safe editable mix that can emit the compatible request shape."""

    vocals: list[TrackState] = field(default_factory=list)
    instrumental_path: str = ""
    output_path: str = ""
    instrumental_db: float = 0.0
    instrumental_mute: bool = False
    instrumental_solo: bool = False
    instrumental_pan: float = 0.0
    eq_low_cut_hz: float = 0.0
    eq_presence_db: float = 0.0
    eq_bands: list[dict[str, Any]] | None = None
    compression_strength: float = 0.0
    reverb_mix: float = 0.0
    reverb_profile: str = DEFAULT_REVERB_PROFILE
    reverb_predelay_ms: float = 0.0
    width: float = 1.0
    deesser_amount: float = 0.0
    preview_start_seconds: float | None = None
    preview_duration_seconds: float | None = None
    legacy_vocal_keys: bool = False
    active_preset: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if isinstance(self.vocals, (str, bytes)) or not isinstance(
            self.vocals, Sequence
        ):
            raise ValueError("vocals must be a list of track objects.")
        if len(self.vocals) > MAX_VOCALS:
            raise ValueError(f"vocals may contain at most {MAX_VOCALS} tracks.")
        normalized_vocals: list[TrackState] = []
        for index, track in enumerate(self.vocals):
            if isinstance(track, TrackState):
                normalized_vocals.append(track)
            elif isinstance(track, Mapping):
                normalized_vocals.append(TrackState.from_request(track))
            else:
                raise ValueError(f"vocals[{index}] must be a TrackState or object.")
        self.vocals = normalized_vocals

        self.instrumental_path = _path_text(
            self.instrumental_path, "instrumental_path"
        )
        self.output_path = _path_text(self.output_path, "output_path")
        self.instrumental_db = _number(
            self.instrumental_db,
            "instrumental_db",
            MIN_GAIN_DB,
            MAX_GAIN_DB,
        )
        self.instrumental_mute = _boolean(
            self.instrumental_mute, "instrumental_mute"
        )
        self.instrumental_solo = _boolean(
            self.instrumental_solo, "instrumental_solo"
        )
        self.instrumental_pan = _number(
            self.instrumental_pan,
            "instrumental_pan",
            MIN_PAN,
            MAX_PAN,
        )
        self.eq_low_cut_hz = _number(
            self.eq_low_cut_hz,
            "eq_low_cut_hz",
            0.0,
            MAX_EQ_FREQ_HZ,
        )
        if 0.0 < self.eq_low_cut_hz < 10.0:
            raise ValueError("eq_low_cut_hz must be 0 or at least 10 Hz.")
        self.eq_presence_db = _number(
            self.eq_presence_db,
            "eq_presence_db",
            MIN_EQ_GAIN_DB,
            MAX_EQ_GAIN_DB,
        )
        self.eq_bands = normalize_eq_bands(self.eq_bands)
        self.compression_strength = _number(
            self.compression_strength,
            "compression_strength",
            0.0,
            1.0,
        )
        self.reverb_mix = _number(
            self.reverb_mix, "reverb_mix", 0.0, 1.0
        )
        if (
            not isinstance(self.reverb_profile, str)
            or self.reverb_profile not in REVERB_PROFILES
        ):
            raise ValueError(
                "reverb_profile must be one of: "
                + ", ".join(REVERB_PROFILES)
                + "."
            )
        self.reverb_predelay_ms = _number(
            self.reverb_predelay_ms,
            "reverb_predelay_ms",
            MIN_REVERB_PREDELAY_MS,
            MAX_REVERB_PREDELAY_MS,
        )
        self.width = _number(self.width, "width", MIN_WIDTH, MAX_WIDTH)
        self.deesser_amount = _number(
            self.deesser_amount, "deesser_amount", 0.0, 1.0
        )
        self.preview_start_seconds = _optional_seconds(
            self.preview_start_seconds, "preview_start_seconds"
        )
        self.preview_duration_seconds = _optional_seconds(
            self.preview_duration_seconds,
            "preview_duration_seconds",
            strictly_positive=True,
        )
        self.legacy_vocal_keys = _boolean(
            self.legacy_vocal_keys, "legacy_vocal_keys"
        )
        if self.active_preset is not None and (
            not isinstance(self.active_preset, str) or not self.active_preset
        ):
            raise ValueError("active_preset must be a non-empty string or null.")

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "MixState":
        """Read a v2/v3 render request without changing its compatibility.

        The legacy top-level vocal trio is accepted only when ``vocals`` is
        absent.  New fields may be omitted; this is required by schema v3.
        """

        if not isinstance(request, Mapping):
            raise ValueError("The mix request must be an object.")
        legacy_names = ("vocal_path", "vocal_db", "offset_seconds")
        if "vocals" in request:
            if any(name in request for name in legacy_names):
                raise ValueError(
                    "vocals cannot be combined with vocal_path, vocal_db, "
                    "or top-level offset_seconds."
                )
            raw_vocals = request["vocals"]
            if isinstance(raw_vocals, (str, bytes)) or not isinstance(
                raw_vocals, Sequence
            ):
                raise ValueError("vocals must be a list of track objects.")
            vocals = [
                TrackState.from_request(item)
                for item in raw_vocals
            ]
            legacy_vocal_keys = False
        elif "vocal_path" in request:
            vocals = [TrackState(
                path=request.get("vocal_path", ""),
                gain_db=request.get("vocal_db", 0.0),
                offset_seconds=request.get("offset_seconds", 0.0),
            )]
            legacy_vocal_keys = True
        else:
            vocals = []
            legacy_vocal_keys = bool(request.get("legacy_vocal_keys", False))

        bands = normalize_eq_bands(request.get("eq_bands"))
        low_cut = request.get("eq_low_cut_hz", None)
        presence = request.get("eq_presence_db", None)
        if low_cut is None:
            highpass = next(
                (band for band in (bands or []) if band["type"] == "highpass"),
                None,
            )
            low_cut = (
                highpass["freq_hz"]
                if highpass is not None and highpass["enabled"]
                else 0.0
            )
        if presence is None:
            bell = next(
                (band for band in (bands or []) if band["type"] == "bell"),
                None,
            )
            presence = (
                bell["gain_db"]
                if bell is not None and bell["enabled"]
                else 0.0
            )

        return cls(
            vocals=vocals,
            instrumental_path=request.get("instrumental_path", ""),
            output_path=request.get("output_path", ""),
            instrumental_db=request.get("instrumental_db", 0.0),
            instrumental_mute=request.get("instrumental_mute", False),
            instrumental_solo=request.get("instrumental_solo", False),
            instrumental_pan=request.get("instrumental_pan", 0.0),
            eq_low_cut_hz=low_cut,
            eq_presence_db=presence,
            eq_bands=bands,
            compression_strength=request.get("compression_strength", 0.0),
            reverb_mix=request.get("reverb_mix", 0.0),
            reverb_profile=request.get(
                "reverb_profile", DEFAULT_REVERB_PROFILE
            ),
            reverb_predelay_ms=request.get("reverb_predelay_ms", 0.0),
            width=request.get("width", 1.0),
            deesser_amount=request.get("deesser_amount", 0.0),
            preview_start_seconds=request.get("preview_start_seconds"),
            preview_duration_seconds=request.get("preview_duration_seconds"),
            legacy_vocal_keys=legacy_vocal_keys,
            active_preset=request.get("active_preset"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MixState":
        return cls.from_request(value)

    @classmethod
    def from_json(cls, value: str | bytes | bytearray) -> "MixState":
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid mix-state JSON: {error}") from None
        return cls.from_request(decoded)

    def uses_v3(self) -> bool:
        """Whether emitting this state requires schema-v3 semantics."""

        return bool(
            self.eq_bands is not None
            or self.deesser_amount != 0.0
            or self.width != 1.0
            or self.reverb_predelay_ms != 0.0
            or self.instrumental_mute
            or self.instrumental_solo
            or self.instrumental_pan != 0.0
            or any(track.has_v3_fields() for track in self.vocals)
        )

    def to_request(self, *, legacy: bool | None = None) -> dict[str, Any]:
        """Build a render request while omitting default v3-only fields.

        ``legacy=None`` preserves the shape of a request read by
        :meth:`from_request`.  Passing ``legacy=True`` forces the old
        ``vocal_path``/``vocal_db``/``offset_seconds`` trio; it is valid only
        for one track with no v3 track fields.
        """

        use_legacy = self.legacy_vocal_keys if legacy is None else legacy
        use_legacy = _boolean(use_legacy, "legacy")
        result: dict[str, Any] = {}
        if use_legacy:
            if len(self.vocals) != 1:
                raise ValueError(
                    "The legacy vocal request shape requires exactly one track."
                )
            result.update(self.vocals[0].to_legacy_fields())
        else:
            result["vocals"] = [track.to_request() for track in self.vocals]

        result["instrumental_path"] = self.instrumental_path
        result["output_path"] = self.output_path
        result["instrumental_db"] = self.instrumental_db
        if self.instrumental_mute:
            result["instrumental_mute"] = True
        if self.instrumental_solo:
            result["instrumental_solo"] = True
        if self.instrumental_pan != 0.0:
            result["instrumental_pan"] = self.instrumental_pan
        result["eq_low_cut_hz"] = self.eq_low_cut_hz
        result["eq_presence_db"] = self.eq_presence_db
        if self.eq_bands is not None:
            result["eq_bands"] = copy.deepcopy(self.eq_bands)
        result["compression_strength"] = self.compression_strength
        result["reverb_mix"] = self.reverb_mix
        result["reverb_profile"] = self.reverb_profile
        if self.reverb_predelay_ms != 0.0:
            result["reverb_predelay_ms"] = self.reverb_predelay_ms
        if self.width != 1.0:
            result["width"] = self.width
        if self.deesser_amount != 0.0:
            result["deesser_amount"] = self.deesser_amount
        if self.preview_start_seconds is not None:
            result["preview_start_seconds"] = self.preview_start_seconds
        if self.preview_duration_seconds is not None:
            result["preview_duration_seconds"] = (
                self.preview_duration_seconds
            )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "vocals": [track.to_dict() for track in self.vocals],
            "instrumental_path": self.instrumental_path,
            "output_path": self.output_path,
            "instrumental_db": self.instrumental_db,
            "instrumental_mute": self.instrumental_mute,
            "instrumental_solo": self.instrumental_solo,
            "instrumental_pan": self.instrumental_pan,
            "eq_low_cut_hz": self.eq_low_cut_hz,
            "eq_presence_db": self.eq_presence_db,
            "eq_bands": copy.deepcopy(self.eq_bands),
            "compression_strength": self.compression_strength,
            "reverb_mix": self.reverb_mix,
            "reverb_profile": self.reverb_profile,
            "reverb_predelay_ms": self.reverb_predelay_ms,
            "width": self.width,
            "deesser_amount": self.deesser_amount,
            "preview_start_seconds": self.preview_start_seconds,
            "preview_duration_seconds": self.preview_duration_seconds,
            "legacy_vocal_keys": self.legacy_vocal_keys,
            "active_preset": self.active_preset,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )