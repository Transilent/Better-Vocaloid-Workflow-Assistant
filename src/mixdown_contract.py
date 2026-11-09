"""Small shared contracts for the mixdown EQ and meter reports.

This module deliberately has no Qt or audio-runtime imports.  The request
validator, editable controls, analytic curve, export renderer, and audition
renderer use the same limits and semantics from here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any


EQ_BAND_TYPES = ("highpass", "lowshelf", "bell", "highshelf")
MAX_VOCALS = 3
MAX_OFFSET_SECONDS = 6.0 * 60.0 * 60.0
MAX_EQ_BANDS = 4
REVERB_PROFILES = ("small", "natural", "wide")
MIN_PAN, MAX_PAN = -1.0, 1.0
MIN_REVERB_SEND, MAX_REVERB_SEND = 0.0, 1.0
MIN_WIDTH, MAX_WIDTH = 0.0, 2.0
MIN_REVERB_PREDELAY_MS, MAX_REVERB_PREDELAY_MS = 0.0, 120.0
EQ_MIN_FREQUENCY_HZ = 10.0
EQ_MAX_FREQUENCY_HZ = 20_000.0
EQ_MIN_GAIN_DB = -24.0
EQ_MAX_GAIN_DB = 24.0
EQ_MIN_Q = 0.1
EQ_MAX_Q = 18.0
EQ_HIGHPASS_GAIN_DB = 0.0
EQ_HIGHPASS_Q = 0.707
EQ_NYQUIST_HEADROOM = 0.95
DEFAULT_SAMPLE_RATE = 48_000.0

GAIN_REDUCTION_UNIT = "dB"
GAIN_REDUCTION_SCOPE = "vocal_bus"
GAIN_REDUCTION_SEMANTICS = "positive_reduction"
GAIN_REDUCTION_MAX_DB = 60.0


def finite_number(value: Any, label: str) -> float:
    """Return a finite float, rejecting booleans and non-numeric values."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def valid_sample_rate(sample_rate: Any) -> float:
    """Return a positive finite sample rate in hertz."""
    rate = finite_number(sample_rate, "sample_rate")
    if rate <= 0.0:
        raise ValueError("sample_rate must be greater than zero")
    return rate


def normalize_eq_bands(value: Any) -> list[dict[str, Any]] | None:
    """Copy editable bands using the same bounds as the renderer contract.

    Effective high-pass and Nyquist projection belongs to project_eq_band;
    this input validator preserves author values until an actual rate is known.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError('eq_bands must be a list of at most four bands')
    if len(value) > MAX_EQ_BANDS:
        raise ValueError('eq_bands must contain at most four bands')
    bands = []
    for index, item in enumerate(value):
        prefix = f'eq_bands[{index}]'
        if not isinstance(item, Mapping) or item.get('type') not in EQ_BAND_TYPES:
            raise ValueError(f'{prefix} must be a known EQ band')
        normalized = {'type': item['type']}
        for key, low, high, default in (
                ('freq_hz', EQ_MIN_FREQUENCY_HZ, EQ_MAX_FREQUENCY_HZ, None),
                ('gain_db', EQ_MIN_GAIN_DB, EQ_MAX_GAIN_DB, 0.0),
                ('q', EQ_MIN_Q, EQ_MAX_Q, EQ_HIGHPASS_Q)):
            number = finite_number(item.get(key, default), f'{prefix}.{key}')
            if not low <= number <= high:
                raise ValueError(f'{prefix}.{key} is outside the request contract')
            normalized[key] = number
        enabled = item.get('enabled', True)
        if type(enabled) is not bool:
            raise ValueError(f'{prefix}.enabled must be a boolean')
        normalized['enabled'] = enabled
        bands.append(normalized)
    return bands


def eq_frequency_limit_hz(sample_rate: Any) -> float:
    """Largest EQ frequency represented by the renderer at this rate.

    Pedalboard's filters are clamped to 95% of Nyquist in the export graph.
    Keeping that same limit for controls and the analytic curve prevents a
    displayed/edit value from being silently processed at another frequency.
    """
    rate = valid_sample_rate(sample_rate)
    limit = min(EQ_MAX_FREQUENCY_HZ, rate * 0.5 * EQ_NYQUIST_HEADROOM)
    if limit < EQ_MIN_FREQUENCY_HZ:
        raise ValueError("sample_rate is too low for the mixdown EQ")
    return float(limit)


def project_eq_band(band: Mapping[str, Any], *,
                    sample_rate: Any | None = None) -> dict[str, Any]:
    """Project a validated request band to its effective DSP values.

    Frequency ranges remain the existing request contract.  A render sample
    rate can additionally lower the effective upper limit to the Nyquist-safe
    boundary.  High-pass gain and Q are locked because the DSP does not use
    them; returning canonical values lets every view echo what will render.
    """
    if not isinstance(band, Mapping):
        raise ValueError("EQ band must be an object")
    band_type = str(band.get("type", "")).strip().lower()
    if band_type not in EQ_BAND_TYPES:
        raise ValueError(
            "EQ band type must be highpass, lowshelf, bell, or highshelf")
    frequency = finite_number(band.get("freq_hz"), "freq_hz")
    gain = finite_number(band.get("gain_db", 0.0), "gain_db")
    q_value = finite_number(band.get("q", EQ_HIGHPASS_Q), "q")
    if not EQ_MIN_FREQUENCY_HZ <= frequency <= EQ_MAX_FREQUENCY_HZ:
        raise ValueError("EQ frequency is outside the request contract")
    if not EQ_MIN_GAIN_DB <= gain <= EQ_MAX_GAIN_DB:
        raise ValueError("EQ gain is outside the request contract")
    if not EQ_MIN_Q <= q_value <= EQ_MAX_Q:
        raise ValueError("EQ Q is outside the request contract")
    if sample_rate is not None:
        frequency = min(frequency, eq_frequency_limit_hz(sample_rate))
    if band_type == "highpass":
        gain = EQ_HIGHPASS_GAIN_DB
        q_value = EQ_HIGHPASS_Q
    enabled = band.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("EQ enabled must be a boolean")
    return {
        "type": band_type,
        "freq_hz": float(frequency),
        "gain_db": float(gain),
        "q": float(q_value),
        "enabled": enabled,
    }


def gain_reduction_db(before_rms: Any, after_rms: Any, *,
                      maximum_db: float = GAIN_REDUCTION_MAX_DB) -> float:
    """Return positive compressor gain reduction in dB.

    ``before_rms`` and ``after_rms`` are linear RMS amplitudes.  Silence on
    the output side is represented by the bounded maximum reduction.  The
    schema meaning is always ``GAIN_REDUCTION_SEMANTICS`` at
    ``GAIN_REDUCTION_SCOPE`` in ``GAIN_REDUCTION_UNIT``.
    """
    before = finite_number(before_rms, "before_rms")
    after = finite_number(after_rms, "after_rms")
    ceiling = finite_number(maximum_db, "maximum_db")
    if ceiling < 0.0:
        raise ValueError("maximum_db must be non-negative")
    if before <= 1e-12:
        return 0.0
    if after <= 1e-12:
        return float(ceiling)
    change = 20.0 * math.log10(after / before)
    return float(max(0.0, min(ceiling, -change)))


__all__ = [
    "MAX_VOCALS", "MAX_OFFSET_SECONDS", "MAX_EQ_BANDS", "REVERB_PROFILES",
    "MIN_PAN", "MAX_PAN", "MIN_REVERB_SEND", "MAX_REVERB_SEND",
    "MIN_WIDTH", "MAX_WIDTH", "MIN_REVERB_PREDELAY_MS", "MAX_REVERB_PREDELAY_MS",
    "normalize_eq_bands",
    "DEFAULT_SAMPLE_RATE",
    "EQ_BAND_TYPES",
    "EQ_HIGHPASS_GAIN_DB",
    "EQ_HIGHPASS_Q",
    "EQ_MAX_FREQUENCY_HZ",
    "EQ_MAX_GAIN_DB",
    "EQ_MAX_Q",
    "EQ_MIN_FREQUENCY_HZ",
    "EQ_MIN_GAIN_DB",
    "EQ_MIN_Q",
    "EQ_NYQUIST_HEADROOM",
    "GAIN_REDUCTION_MAX_DB",
    "GAIN_REDUCTION_SCOPE",
    "GAIN_REDUCTION_SEMANTICS",
    "GAIN_REDUCTION_UNIT",
    "eq_frequency_limit_hz",
    "finite_number",
    "gain_reduction_db",
    "project_eq_band",
    "valid_sample_rate",
]
