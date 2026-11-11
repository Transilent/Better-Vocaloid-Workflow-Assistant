"""Qt-free four-band EQ math shared by the VWA mixdown UIs.

The Widgets EQ curve and the Qt Quick bridge use the same factory bands,
request normalization, logarithmic frequency mapping, and RBJ Audio-EQ-Cookbook
responses from this module. Audio rendering and Qt painting remain outside it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy
import math
from typing import Any, Optional

from mixdown_contract import (
    DEFAULT_SAMPLE_RATE,
    EQ_BAND_TYPES,
    EQ_HIGHPASS_Q,
    EQ_MAX_FREQUENCY_HZ,
    EQ_MAX_GAIN_DB,
    EQ_MAX_Q,
    EQ_MIN_FREQUENCY_HZ,
    EQ_MIN_GAIN_DB,
    EQ_MIN_Q,
    MAX_EQ_BANDS,
    eq_frequency_limit_hz,
    finite_number as _contract_finite_number,
    project_eq_band,
    valid_sample_rate as _contract_valid_sample_rate,
)


MIN_FREQUENCY_HZ = EQ_MIN_FREQUENCY_HZ
MAX_FREQUENCY_HZ = EQ_MAX_FREQUENCY_HZ
MIN_GAIN_DB = EQ_MIN_GAIN_DB
MAX_GAIN_DB = EQ_MAX_GAIN_DB
MIN_Q = EQ_MIN_Q
MAX_Q = EQ_MAX_Q
RESPONSE_POINTS = 240
MAX_BANDS = MAX_EQ_BANDS
GAIN_LOCKED_TYPES = frozenset(('highpass',))
Q_LOCKED_TYPES = frozenset(('highpass',))
HIGHPASS_FIXED_Q = EQ_HIGHPASS_Q
BAND_TYPES = EQ_BAND_TYPES

FREQUENCY_GRID = (10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
                  10000, 20000)
GAIN_GRID_DB = (-24, -18, -12, -6, 0, 6, 12, 18, 24)

DEFAULT_BANDS = (
    {'type': 'highpass', 'freq_hz': 90.0, 'gain_db': 0.0,
     'q': 0.707, 'enabled': True},
    {'type': 'lowshelf', 'freq_hz': 260.0, 'gain_db': 0.0,
     'q': 0.707, 'enabled': False},
    {'type': 'bell', 'freq_hz': 3200.0, 'gain_db': 2.5,
     'q': 0.8, 'enabled': True},
    {'type': 'highshelf', 'freq_hz': 10000.0, 'gain_db': 1.5,
     'q': 0.707, 'enabled': False},
)

__all__ = [
    'BAND_TYPES', 'DEFAULT_BANDS', 'DEFAULT_SAMPLE_RATE', 'FREQUENCY_GRID',
    'GAIN_GRID_DB', 'GAIN_LOCKED_TYPES', 'HIGHPASS_FIXED_Q', 'MAX_BANDS',
    'MAX_FREQUENCY_HZ', 'MAX_GAIN_DB', 'MAX_Q', 'MIN_FREQUENCY_HZ',
    'MIN_GAIN_DB', 'MIN_Q', 'Q_LOCKED_TYPES', 'RESPONSE_POINTS',
    'band_response_db', 'biquad_coefficients', 'default_bands',
    'eq_frequency_limit_hz', 'frequency_response_db', 'frequency_to_unit',
    'logarithmic_frequencies', 'normalise_band', 'normalize_band',
    'response_curve', 'unit_to_frequency',
]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _finite_number(value: Any, label: str) -> float:
    try:
        return _contract_finite_number(value, label)
    except ValueError as exc:
        raise ValueError(f'{label} 必须为有限数字。') from exc


def _default_spec(index: int) -> dict[str, Any]:
    if 0 <= index < len(DEFAULT_BANDS):
        return DEFAULT_BANDS[index]
    return DEFAULT_BANDS[2]


def default_bands() -> list[dict[str, Any]]:
    """Return an independent copy of the four factory bands."""
    return copy.deepcopy(list(DEFAULT_BANDS))


def _default_q(band_type: str) -> float:
    return 0.8 if band_type == 'bell' else 0.707


def normalize_band(band: Mapping[str, Any], index: int = 0, *,
                   sample_rate: Optional[float] = None) -> dict[str, Any]:
    """Validate and project one band through the shared mixdown contract.

    Partial legacy dictionaries use per-index defaults. Invalid/non-finite
    values are rejected; absolute request limits and the render-rate
    Nyquist-safe limit are applied consistently.
    """
    if not isinstance(band, Mapping):
        raise TypeError('EQ band must be a mapping.')
    spec = _default_spec(index)
    band_type = str(band.get('type', spec['type'])).strip().lower()
    if band_type not in BAND_TYPES:
        raise ValueError(f'不支持的 EQ 类型：{band_type}')

    frequency = _finite_number(band.get('freq_hz', spec['freq_hz']), 'freq_hz')
    gain = _finite_number(band.get('gain_db', 0.0), 'gain_db')
    q_value = _finite_number(band.get('q', _default_q(band_type)), 'q')
    enabled = band.get('enabled', spec['enabled'])
    if not isinstance(enabled, bool):
        enabled = bool(enabled)

    return project_eq_band({
        'type': band_type,
        'freq_hz': _clamp(frequency, MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ),
        'gain_db': _clamp(gain, MIN_GAIN_DB, MAX_GAIN_DB),
        'q': _clamp(q_value, MIN_Q, MAX_Q),
        'enabled': enabled,
    }, sample_rate=sample_rate)


normalise_band = normalize_band


def frequency_to_unit(frequency_hz: float,
                      sample_rate: float = DEFAULT_SAMPLE_RATE) -> float:
    """Map the editable frequency band to a 0..1 logarithmic X coordinate."""
    maximum = eq_frequency_limit_hz(sample_rate)
    frequency = _clamp(_finite_number(frequency_hz, 'frequency_hz'),
                       MIN_FREQUENCY_HZ, maximum)
    span = math.log10(maximum / MIN_FREQUENCY_HZ)
    return math.log10(frequency / MIN_FREQUENCY_HZ) / span


def unit_to_frequency(unit: float,
                      sample_rate: float = DEFAULT_SAMPLE_RATE) -> float:
    """Map a 0..1 logarithmic X coordinate to the current effective range."""
    maximum = eq_frequency_limit_hz(sample_rate)
    position = _clamp(_finite_number(unit, 'unit'), 0.0, 1.0)
    span = math.log10(maximum / MIN_FREQUENCY_HZ)
    return MIN_FREQUENCY_HZ * (10.0 ** (position * span))


def logarithmic_frequencies(points: int = RESPONSE_POINTS, *,
                            sample_rate: float = DEFAULT_SAMPLE_RATE) -> list[float]:
    """Return evenly spaced frequencies through the render-safe upper limit."""
    if isinstance(points, bool) or int(points) != points or int(points) < 2:
        raise ValueError('points 必须为不小于 2 的整数。')
    count = int(points)
    maximum = eq_frequency_limit_hz(sample_rate)
    ratio = maximum / MIN_FREQUENCY_HZ
    values = [MIN_FREQUENCY_HZ * (ratio ** (index / (count - 1.0)))
              for index in range(count)]
    values[-1] = maximum
    return values


def _valid_sample_rate(sample_rate: float) -> float:
    try:
        return _contract_valid_sample_rate(sample_rate)
    except ValueError as exc:
        raise ValueError('sample_rate 必须大于 0 Hz。') from exc


def _display_sample_rate(sample_rate: float) -> float:
    rate = _valid_sample_rate(sample_rate)
    eq_frequency_limit_hz(rate)
    return rate


def biquad_coefficients(
    band: Mapping[str, Any], sample_rate: float = DEFAULT_SAMPLE_RATE
) -> tuple[float, float, float, float, float, float]:
    """Return ``(b0, b1, b2, a0, a1, a2)`` for one RBJ biquad.

    The Q-controlled shelf form is used for low/high shelves. Coefficients are
    returned unnormalised; the response evaluator divides by ``a0``.
    """
    values = normalize_band(band)
    band_type = values['type']
    frequency = values['freq_hz']
    gain_db = values['gain_db']
    q_value = values['q']
    rate = _valid_sample_rate(sample_rate)
    if frequency >= rate / 2.0:
        raise ValueError('中心频率必须低于奈奎斯特频率。')

    omega = 2.0 * math.pi * frequency / rate
    sine = math.sin(omega)
    cosine = math.cos(omega)
    alpha = sine / (2.0 * q_value)
    amplitude = 10.0 ** (gain_db / 40.0)

    if band_type == 'highpass':
        b0 = (1.0 + cosine) / 2.0
        b1 = -(1.0 + cosine)
        b2 = (1.0 + cosine) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cosine
        a2 = 1.0 - alpha
    elif band_type == 'bell':
        b0 = 1.0 + alpha * amplitude
        b1 = -2.0 * cosine
        b2 = 1.0 - alpha * amplitude
        a0 = 1.0 + alpha / amplitude
        a1 = -2.0 * cosine
        a2 = 1.0 - alpha / amplitude
    elif band_type == 'lowshelf':
        root_amplitude = math.sqrt(amplitude)
        b0 = amplitude * ((amplitude + 1.0)
                          - (amplitude - 1.0) * cosine
                          + 2.0 * root_amplitude * alpha)
        b1 = 2.0 * amplitude * ((amplitude - 1.0)
                                - (amplitude + 1.0) * cosine)
        b2 = amplitude * ((amplitude + 1.0)
                          - (amplitude - 1.0) * cosine
                          - 2.0 * root_amplitude * alpha)
        a0 = ((amplitude + 1.0) + (amplitude - 1.0) * cosine
              + 2.0 * root_amplitude * alpha)
        a1 = -2.0 * ((amplitude - 1.0) + (amplitude + 1.0) * cosine)
        a2 = ((amplitude + 1.0) + (amplitude - 1.0) * cosine
              - 2.0 * root_amplitude * alpha)
    else:  # highshelf
        root_amplitude = math.sqrt(amplitude)
        b0 = amplitude * ((amplitude + 1.0)
                          + (amplitude - 1.0) * cosine
                          + 2.0 * root_amplitude * alpha)
        b1 = -2.0 * amplitude * ((amplitude - 1.0)
                                 + (amplitude + 1.0) * cosine)
        b2 = amplitude * ((amplitude + 1.0)
                          + (amplitude - 1.0) * cosine
                          - 2.0 * root_amplitude * alpha)
        a0 = ((amplitude + 1.0) - (amplitude - 1.0) * cosine
              + 2.0 * root_amplitude * alpha)
        a1 = 2.0 * ((amplitude - 1.0) - (amplitude + 1.0) * cosine)
        a2 = ((amplitude + 1.0) - (amplitude - 1.0) * cosine
              - 2.0 * root_amplitude * alpha)

    if not all(math.isfinite(value) for value in (b0, b1, b2, a0, a1, a2)):
        raise ValueError('EQ 滤波器系数不是有限数字。')
    if abs(a0) < 1e-15:
        raise ValueError('EQ 滤波器系数退化。')
    return b0, b1, b2, a0, a1, a2


def _response_from_coefficients_db(
    coefficients: tuple[float, float, float, float, float, float],
    frequencies_hz: Iterable[float], sample_rate: float,
) -> list[float]:
    b0, b1, b2, a0, a1, a2 = coefficients
    rate = _valid_sample_rate(sample_rate)
    result = []
    for raw_frequency in frequencies_hz:
        frequency = _finite_number(raw_frequency, 'frequency_hz')
        if frequency <= 0.0 or frequency >= rate / 2.0:
            raise ValueError('响应频率必须位于 0 Hz 与奈奎斯特频率之间。')
        omega = 2.0 * math.pi * frequency / rate
        z1 = complex(math.cos(omega), -math.sin(omega))
        z2 = z1 * z1
        numerator = b0 + b1 * z1 + b2 * z2
        denominator = a0 + a1 * z1 + a2 * z2
        magnitude = abs(numerator) / max(abs(denominator), 1e-15)
        result.append(20.0 * math.log10(max(magnitude, 1e-15)))
    return result


def band_response_db(
    band: Mapping[str, Any], frequencies_hz: Iterable[float],
    sample_rate: float = DEFAULT_SAMPLE_RATE,
) -> list[float]:
    """Evaluate one band's magnitude response in dB."""
    rate = _valid_sample_rate(sample_rate)
    frequencies = list(frequencies_hz)
    coefficients = biquad_coefficients(band, rate)
    return _response_from_coefficients_db(coefficients, frequencies, rate)


def frequency_response_db(
    bands: Iterable[Mapping[str, Any]], frequencies_hz: Iterable[float],
    sample_rate: float = DEFAULT_SAMPLE_RATE, enabled_only: bool = True,
) -> list[float]:
    """Sum the magnitude responses of enabled bands at each frequency."""
    rate = _valid_sample_rate(sample_rate)
    frequencies = list(frequencies_hz)
    total = [0.0 for _ in frequencies]
    for index, raw_band in enumerate(bands):
        band = normalize_band(raw_band, index, sample_rate=rate)
        if enabled_only and not band['enabled']:
            continue
        response = band_response_db(band, frequencies, rate)
        for index, value in enumerate(response):
            total[index] += value
    return total


def response_curve(
    bands: Iterable[Mapping[str, Any]], sample_rate: float = DEFAULT_SAMPLE_RATE,
    points: int = RESPONSE_POINTS, enabled_only: bool = True,
) -> tuple[list[float], list[float]]:
    """Return ``(frequencies_hz, total_gain_db)`` for the painted curve."""
    frequencies = logarithmic_frequencies(points, sample_rate=sample_rate)
    values = frequency_response_db(
        bands, frequencies, sample_rate=sample_rate, enabled_only=enabled_only
    )
    return frequencies, values
