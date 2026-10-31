"""Interactive four-band equalizer curve for the VWA mixdown inspector.

The widget is deliberately backend-neutral. It owns only the visible band
dictionaries, paints with QPainter, and reports user edits. Audio generation,
audition throttling, and persistence stay with the host UI; visible strings use
the shared project catalog.

Band dictionaries follow the mixdown request contract::

    {"type": "bell", "freq_hz": 3200.0, "gain_db": 2.5,
     "q": 0.8, "enabled": True}

Filter curves use RBJ Audio-EQ-Cookbook digital biquads. Shelf filters use the
same Q parameter as the other bands so one wheel gesture has a consistent
meaning across all four node types.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import copy
import math
from typing import Any, Optional

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                         QPalette, QPen)
from PyQt5.QtWidgets import QSizePolicy, QWidget

from i18n import tr as _tr


MIN_FREQUENCY_HZ = 20.0
MAX_FREQUENCY_HZ = 20000.0
MIN_GAIN_DB = -18.0
MAX_GAIN_DB = 18.0
MIN_Q = 0.1
MAX_Q = 18.0
DEFAULT_SAMPLE_RATE = 48000.0
RESPONSE_POINTS = 240
NODE_DIAMETER = 12.0
HIT_RADIUS = 22.0
MIN_WIDGET_WIDTH = 320
MIN_WIDGET_HEIGHT = 190
MAX_BANDS = 4
GAIN_LOCKED_TYPES = frozenset(('highpass',))
Q_LOCKED_TYPES = frozenset(('highpass',))
HIGHPASS_FIXED_Q = 0.707
BAND_TYPES = ('highpass', 'lowshelf', 'bell', 'highshelf')

FREQUENCY_GRID = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
GAIN_GRID_DB = (-18, -12, -6, 0, 6, 12, 18)

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

_BAND_COLORS = {
    'highpass': QColor('#49c6e5'),
    'lowshelf': QColor('#44d19d'),
    'bell': QColor('#8a72ff'),
    'highshelf': QColor('#f1b85b'),
}
_BAND_NAMES = {
    'highpass': '高通',
    'lowshelf': '低架',
    'bell': '峰值',
    'highshelf': '高架',
}

__all__ = [
    'BAND_TYPES', 'DEFAULT_BANDS', 'DEFAULT_SAMPLE_RATE', 'EQCurveView',
    'GAIN_GRID_DB', 'GAIN_LOCKED_TYPES', 'HIGHPASS_FIXED_Q', 'HIT_RADIUS',
    'MAX_BANDS', 'MAX_FREQUENCY_HZ', 'MAX_GAIN_DB', 'MAX_Q',
    'MIN_FREQUENCY_HZ', 'MIN_GAIN_DB', 'MIN_Q', 'MIN_WIDGET_HEIGHT',
    'MIN_WIDGET_WIDTH', 'NODE_DIAMETER', 'Q_LOCKED_TYPES',
    'RESPONSE_POINTS', 'band_response_db', 'biquad_coefficients', 'default_bands',
    'frequency_response_db', 'frequency_to_unit', 'logarithmic_frequencies',
    'normalise_band', 'normalize_band', 'response_curve', 'unit_to_frequency',
]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{label} 必须为有限数字。') from exc
    if not math.isfinite(number):
        raise ValueError(f'{label} 必须为有限数字。')
    return number


def _default_spec(index: int) -> dict[str, Any]:
    if 0 <= index < len(DEFAULT_BANDS):
        return DEFAULT_BANDS[index]
    return DEFAULT_BANDS[2]


def default_bands() -> list[dict[str, Any]]:
    """Return an independent copy of the four factory bands."""
    return copy.deepcopy(list(DEFAULT_BANDS))


def _default_q(band_type: str) -> float:
    return 0.8 if band_type == 'bell' else 0.707


def normalize_band(band: Mapping[str, Any], index: int = 0) -> dict[str, Any]:
    """Validate and normalise one band dictionary.

    Values are constrained to the widget's visible editing range. Unknown
    keys are ignored, and the result never shares containers with ``band``.
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

    normalized_gain = _clamp(gain, MIN_GAIN_DB, MAX_GAIN_DB)
    normalized_q = _clamp(q_value, MIN_Q, MAX_Q)
    # The DSP contract ignores gain and uses a fixed Q for the high-pass.
    # Keeping those values canonical makes the painted node match audition.
    if band_type in GAIN_LOCKED_TYPES:
        normalized_gain = 0.0
    if band_type in Q_LOCKED_TYPES:
        normalized_q = HIGHPASS_FIXED_Q

    return {
        'type': band_type,
        'freq_hz': _clamp(frequency, MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ),
        'gain_db': normalized_gain,
        'q': normalized_q,
        'enabled': enabled,
    }


normalise_band = normalize_band


def frequency_to_unit(frequency_hz: float) -> float:
    """Map 20..20000 Hz to the widget's 0..1 logarithmic X coordinate."""
    frequency = _clamp(_finite_number(frequency_hz, 'frequency_hz'),
                       MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
    span = math.log10(MAX_FREQUENCY_HZ / MIN_FREQUENCY_HZ)
    return math.log10(frequency / MIN_FREQUENCY_HZ) / span


def unit_to_frequency(unit: float) -> float:
    """Map a 0..1 logarithmic X coordinate to 20..20000 Hz."""
    position = _clamp(_finite_number(unit, 'unit'), 0.0, 1.0)
    span = math.log10(MAX_FREQUENCY_HZ / MIN_FREQUENCY_HZ)
    return MIN_FREQUENCY_HZ * (10.0 ** (position * span))


def logarithmic_frequencies(points: int = RESPONSE_POINTS) -> list[float]:
    """Return evenly spaced frequencies on a logarithmic axis."""
    if isinstance(points, bool) or int(points) != points or int(points) < 2:
        raise ValueError('points 必须为不小于 2 的整数。')
    count = int(points)
    ratio = MAX_FREQUENCY_HZ / MIN_FREQUENCY_HZ
    values = [MIN_FREQUENCY_HZ * (ratio ** (index / (count - 1.0)))
              for index in range(count)]
    values[-1] = MAX_FREQUENCY_HZ
    return values


def _valid_sample_rate(sample_rate: float) -> float:
    rate = _finite_number(sample_rate, 'sample_rate')
    if rate <= 0.0:
        raise ValueError('sample_rate 必须大于 0 Hz。')
    return rate


def _display_sample_rate(sample_rate: float) -> float:
    rate = _valid_sample_rate(sample_rate)
    if rate <= MAX_FREQUENCY_HZ * 2.0:
        raise ValueError('sample_rate 必须高于 40 kHz，才能显示到 20 kHz。')
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
    for raw_band in bands:
        band = normalize_band(raw_band)
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
    frequencies = logarithmic_frequencies(points)
    values = frequency_response_db(
        bands, frequencies, sample_rate=sample_rate, enabled_only=enabled_only
    )
    return frequencies, values

class EQCurveView(QWidget):
    """Four-band EQ editor rendered directly with QPainter.

    User interactions
    -----------------
    * Drag a node: continuous logarithmic frequency and linear gain.
    * Wheel over a node: Q; hold Shift for a finer step.
    * Alt-click a node or click the visible bypass pill: enable/bypass.
    * Double-click a node, or press Enter: restore that band's defaults.
    * Arrow keys: frequency/gain; Ctrl+Up/Down or PageUp/Down: Q;
      hold Shift for finer steps. Space toggles bypass.

    ``bandsChanged`` emits an independent band-list copy for every accepted
    edit, including every mouse-move during a drag. ``editingStarted(index)``
    starts a pointer edit and ``editingFinished(index, final_bands)`` ends it,
    allowing the host to merge/render only once at release while auditioning
    every intermediate ``bandsChanged`` snapshot.
    """

    bandsChanged = pyqtSignal(object)
    bandSelected = pyqtSignal(int)
    editingStarted = pyqtSignal(int)
    editingFinished = pyqtSignal(int, object)

    MIN_FREQUENCY_HZ = MIN_FREQUENCY_HZ
    MAX_FREQUENCY_HZ = MAX_FREQUENCY_HZ
    MIN_GAIN_DB = MIN_GAIN_DB
    MAX_GAIN_DB = MAX_GAIN_DB
    MIN_Q = MIN_Q
    MAX_Q = MAX_Q
    HIT_RADIUS = HIT_RADIUS
    NODE_DIAMETER = NODE_DIAMETER
    MINIMUM_WIDTH = MIN_WIDGET_WIDTH
    MINIMUM_HEIGHT = MIN_WIDGET_HEIGHT

    def __init__(self, parent: Optional[QWidget] = None,
                 sample_rate: float = DEFAULT_SAMPLE_RATE) -> None:
        super().__init__(parent)
        self._sample_rate = _display_sample_rate(sample_rate)
        self._bands: list[dict[str, Any]] = default_bands()
        self._selected = 0 if self._bands else -1
        self._hover = -1
        self._drag_index = -1
        self._drag_origin = QPointF()
        self._drag_frequency = 0.0
        self._drag_gain = 0.0
        self._playhead: Optional[float] = None
        self._response_cache: Optional[
            tuple[tuple[float, ...], tuple[tuple[float, ...], ...]]
        ] = None

        self.setObjectName('mixdownEqCurve')
        self.setMinimumSize(MIN_WIDGET_WIDTH, MIN_WIDGET_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(_tr('四段交互均衡器'))
        self.setToolTip(self.controls_help())
        self.setWhatsThis(self.controls_help())
        self._refresh_accessibility()

    # ------------------------------------------------------------------
    # Public data and display API
    # ------------------------------------------------------------------
    def set_bands(self, bands: Iterable[Mapping[str, Any]]) -> None:
        """Replace the visible bands without emitting ``bandsChanged``.

        Four bands is this control's designed capacity; fewer values are
        accepted for legacy/empty states. Returned/pushed dictionaries are
        always independent from widget state.
        """
        if isinstance(bands, (str, bytes)) or not isinstance(bands, Iterable):
            raise TypeError('bands 必须为最多四段的字典序列。')
        values = list(bands)
        if len(values) > MAX_BANDS:
            raise ValueError('EQ 最多支持四段。')
        drag_index = self._drag_index
        self._bands = [normalize_band(value, index)
                       for index, value in enumerate(values)]
        self._selected = min(self._selected, len(self._bands) - 1)
        if self._selected < 0 and self._bands:
            self._selected = 0
        # Hosts commonly echo bandsChanged back through set_bands. Preserve
        # the active drag gesture so one pointer move advances only once.
        if 0 <= drag_index < len(self._bands):
            self._drag_index = drag_index
        else:
            self._drag_index = -1
            self._hover = -1
        self._invalidate_response_cache()
        self._refresh_accessibility()
        self.update()

    def bands(self) -> list[dict[str, Any]]:
        """Return a deep copy of the current band list."""
        return copy.deepcopy(self._bands)

    def set_playhead(self, position: Optional[float] = None) -> None:
        """Show an optional cursor at a normalised logarithmic X position.

        ``None`` hides it. The EQ axis is frequency rather than time, so this
        cursor intentionally uses 0..1 across 20..20000 Hz, not seconds.
        """
        if position is None:
            self._playhead = None
        else:
            self._playhead = _clamp(
                _finite_number(position, 'playhead'), 0.0, 1.0
            )
        self.update()

    def playhead(self) -> Optional[float]:
        return self._playhead

    def set_sample_rate(self, sample_rate: float) -> None:
        """Set the sample rate used only for analytic curve preview."""
        rate = _display_sample_rate(sample_rate)
        if rate != self._sample_rate:
            self._sample_rate = rate
            self._invalidate_response_cache()
            self.update()

    def sample_rate(self) -> float:
        return self._sample_rate

    def selected_band_index(self) -> int:
        return self._selected

    def select_band(self, index: int, emit: bool = False) -> None:
        """Select a band programmatically; ``-1`` clears the selection."""
        self._select_band(index, emit=emit)

    @staticmethod
    def controls_help() -> str:
        """Return the compact discoverable interaction legend."""
        return _tr(
            '拖动节点：频率/增益；滚轮：Q（高通固定，Shift 精调）；'
            'Alt+点击节点或“旁路”按钮：启停；双击/Enter：恢复默认；'
            '方向键：调频/增益，Shift 更细；Ctrl+↑↓ 或 PageUp/Down：调 Q；'
            'Space：旁路。'
        )

    def band_limits(self, index: int) -> dict[str, Any]:
        """Return per-band editable limits; ``None`` means DSP-locked."""
        band = self._bands[index]
        return {
            'freq_hz': (MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ),
            'gain_db': (None if band['type'] in GAIN_LOCKED_TYPES
                        else (MIN_GAIN_DB, MAX_GAIN_DB)),
            'q': (None if band['type'] in Q_LOCKED_TYPES
                  else (MIN_Q, MAX_Q)),
        }

    def toggle_band_enabled(self, index: Optional[int] = None) -> bool:
        """Toggle one band's bypass state and emit ``bandsChanged``."""
        target = self._selected if index is None else int(index)
        if not (0 <= target < len(self._bands)):
            return False
        return self._update_band(
            target, enabled=not self._bands[target]['enabled']
        )

    def reset_band(self, index: Optional[int] = None) -> bool:
        """Restore one band to its factory defaults."""
        target = self._selected if index is None else int(index)
        if not (0 <= target < len(self._bands)):
            return False
        factory = normalize_band(_default_spec(target), target)
        if factory == self._bands[target]:
            return False
        self._bands[target] = factory
        self._invalidate_response_cache()
        self._refresh_accessibility()
        self.update()
        self.bandsChanged.emit(self.bands())
        return True

    def bypass_rect(self) -> QRectF:
        """Return the visible, clickable bypass control rectangle."""
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        width = min(94.0, max(64.0, outer.width() * 0.25))
        return QRectF(outer.right() - width - 5.0, outer.top() + 3.0,
                      width, 22.0)

    def hit_radius(self) -> float:
        """Touch-friendly hit radius used around every EQ node."""
        return HIT_RADIUS

    def band_node_position(self, index: int) -> QPointF:
        band = self._bands[index]
        return self._band_position(band)

    def node_rect(self, index: int) -> QRectF:
        center = self.band_node_position(index)
        radius = NODE_DIAMETER / 2.0
        return QRectF(center.x() - radius, center.y() - radius,
                      NODE_DIAMETER, NODE_DIAMETER)

    def plot_rect(self) -> QRectF:
        outer = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        left = outer.left() + 42.0
        top = outer.top() + 29.0
        right = outer.right() - 12.0
        bottom = outer.bottom() - 28.0
        return QRectF(left, top, max(1.0, right - left),
                      max(1.0, bottom - top))

    def visible_frequency_grid(self) -> tuple[int, ...]:
        """Return ruler labels that fit the current compact/full width."""
        return (FREQUENCY_GRID if self.plot_rect().width() >= 420.0
                else (20, 100, 1000, 10000))

    def frequency_to_x(self, frequency_hz: float) -> float:
        plot = self.plot_rect()
        return plot.left() + plot.width() * frequency_to_unit(frequency_hz)

    def x_to_frequency(self, x_position: float) -> float:
        plot = self.plot_rect()
        unit = (float(x_position) - plot.left()) / max(plot.width(), 1.0)
        return unit_to_frequency(unit)

    def gain_to_y(self, gain_db: float) -> float:
        plot = self.plot_rect()
        gain = _clamp(_finite_number(gain_db, 'gain_db'),
                      MIN_GAIN_DB, MAX_GAIN_DB)
        unit = (MAX_GAIN_DB - gain) / (MAX_GAIN_DB - MIN_GAIN_DB)
        return plot.top() + plot.height() * unit

    def y_to_gain(self, y_position: float) -> float:
        plot = self.plot_rect()
        unit = (float(y_position) - plot.top()) / max(plot.height(), 1.0)
        return _clamp(MAX_GAIN_DB - unit * (MAX_GAIN_DB - MIN_GAIN_DB),
                      MIN_GAIN_DB, MAX_GAIN_DB)

    def bubble_text(self, index: int) -> str:
        """Return the compact numeric node description."""
        band = self._bands[index]
        frequency = band['freq_hz']
        if frequency >= 1000.0:
            frequency_text = f'{frequency / 1000.0:.2f} kHz'
        else:
            frequency_text = f'{frequency:.1f} Hz'
        state = '' if band['enabled'] else f' · {_tr("旁路")}'
        gain_text = (_tr('增益固定') if band['type'] in GAIN_LOCKED_TYPES
                     else f'{band["gain_db"]:+.2f} dB')
        q_text = (_tr('Q 固定') if band['type'] in Q_LOCKED_TYPES
                  else f'Q {band["q"]:.3f}')
        name = _tr(_BAND_NAMES[band['type']])
        return (f'{name} {frequency_text}  '
                f'{gain_text}  {q_text}{state}')

    def frequency_response(self, frequencies_hz: Iterable[float]) -> list[float]:
        """Evaluate the current enabled bands through the public pure helper."""
        return frequency_response_db(
            self._bands, frequencies_hz, sample_rate=self._sample_rate
        )

    def response_curve(self, points: int = RESPONSE_POINTS,
                       enabled_only: bool = True
                       ) -> tuple[list[float], list[float]]:
        """Return the current analytic curve without requiring a paint pass."""
        return response_curve(
            self._bands, sample_rate=self._sample_rate, points=points,
            enabled_only=enabled_only,
        )

    def sizeHint(self) -> QSize:
        return QSize(560, 320)

    def minimumSizeHint(self) -> QSize:
        return QSize(MIN_WIDGET_WIDTH, MIN_WIDGET_HEIGHT)

    # ------------------------------------------------------------------
    # Geometry and editing helpers
    # ------------------------------------------------------------------
    def _invalidate_response_cache(self) -> None:
        self._response_cache = None

    def _response_data(
        self,
    ) -> tuple[tuple[float, ...], tuple[tuple[float, ...], ...]]:
        """Cache analytic samples; repaint alone never re-evaluates them."""
        if self._response_cache is None:
            frequencies = tuple(logarithmic_frequencies(RESPONSE_POINTS))
            responses = tuple(
                tuple(band_response_db(band, frequencies, self._sample_rate))
                for band in self._bands
            )
            self._response_cache = (frequencies, responses)
        return self._response_cache

    def _band_position(self, band: Mapping[str, Any]) -> QPointF:
        return QPointF(self.frequency_to_x(band['freq_hz']),
                       self.gain_to_y(band['gain_db']))

    def _hit_index(self, position: QPointF) -> int:
        radius = self.hit_radius()
        closest = -1
        closest_distance = radius
        for index, band in enumerate(self._bands):
            center = self._band_position(band)
            distance = math.hypot(center.x() - position.x(),
                                  center.y() - position.y())
            if distance <= closest_distance:
                closest = index
                closest_distance = distance
        return closest

    def _select_band(self, index: int, *, emit: bool,
                     force: bool = False) -> None:
        if not self._bands:
            index = -1
        if index < -1 or index >= len(self._bands):
            raise IndexError('EQ band index out of range.')
        changed = index != self._selected
        self._selected = index
        self._refresh_accessibility()
        self.update()
        if emit and (changed or force) and index >= 0:
            self.bandSelected.emit(index)

    def _update_band(self, index: int, **changes: Any) -> bool:
        band = self._bands[index]
        changed = False
        for key, value in changes.items():
            if key not in ('freq_hz', 'gain_db', 'q', 'enabled'):
                continue
            before = band[key]
            if key == 'enabled':
                value = bool(value)
            elif key == 'freq_hz':
                value = round(_clamp(float(value), MIN_FREQUENCY_HZ,
                                     MAX_FREQUENCY_HZ), 2)
            elif key == 'gain_db':
                value = (0.0 if band['type'] in GAIN_LOCKED_TYPES else
                         round(_clamp(float(value), MIN_GAIN_DB,
                                      MAX_GAIN_DB), 2))
            else:
                value = (HIGHPASS_FIXED_Q
                         if band['type'] in Q_LOCKED_TYPES else
                         round(_clamp(float(value), MIN_Q, MAX_Q), 4))
            if before != value:
                band[key] = value
                changed = True
        if not changed:
            return False
        self._invalidate_response_cache()
        self._refresh_accessibility()
        # This schedules only this widget. It never invokes application-wide
        # style refreshes or window repaints.
        self.update()
        self.bandsChanged.emit(self.bands())
        return True

    def accessible_summary(self, index: Optional[int] = None) -> str:
        """Return a compact screen-reader summary and input legend."""
        target = self._selected if index is None else int(index)
        if not (0 <= target < len(self._bands)):
            return _tr('当前没有可编辑的均衡器频段。')
        band = self._bands[target]
        name = _tr(_BAND_NAMES[band['type']])
        state = _tr('启用') if band['enabled'] else _tr('旁路')
        gain = (_tr('增益固定') if band['type'] in GAIN_LOCKED_TYPES
                else f'{_tr("增益")} {band["gain_db"]:+.2f} dB')
        q_text = (_tr('Q 固定') + ' 0.707' if band['type'] in Q_LOCKED_TYPES
                  else f'Q {band["q"]:.3f}')
        return _tr(
            '第 {0} 段：{1}，{2}，频率 {3:.2f} Hz，{4}，{5}。',
            target + 1, name, state, band['freq_hz'], gain, q_text,
        ) + self.controls_help()


    def _refresh_accessibility(self) -> None:
        self.setAccessibleDescription(self.accessible_summary())
    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def _start_band_edit(self, index: int, position: QPointF) -> None:
        band = self._bands[index]
        self._drag_index = index
        self._drag_origin = QPointF(position)
        self._drag_frequency = band['freq_hz']
        self._drag_gain = band['gain_db']
        self.setCursor(Qt.ClosedHandCursor)
        self.editingStarted.emit(index)

    def _drag_position(self, position: QPointF) -> None:
        if self._drag_index < 0:
            return
        band = self._bands[self._drag_index]
        plot = self.plot_rect()
        delta_x = position.x() - self._drag_origin.x()
        delta_y = position.y() - self._drag_origin.y()
        span = math.log10(MAX_FREQUENCY_HZ / MIN_FREQUENCY_HZ)
        frequency = self._drag_frequency * (
            10.0 ** (span * delta_x / max(plot.width(), 1.0)))
        changes: dict[str, Any] = {'freq_hz': frequency}
        if band['type'] not in GAIN_LOCKED_TYPES:
            changes['gain_db'] = self._drag_gain - (
                delta_y / max(plot.height(), 1.0)
            ) * (MAX_GAIN_DB - MIN_GAIN_DB)
        self._update_band(self._drag_index, **changes)

    def _finish_band_edit(self, position: Optional[QPointF] = None) -> None:
        if self._drag_index < 0:
            return
        if position is not None:
            self._drag_position(position)
        index = self._drag_index
        self._drag_index = -1
        self._hover = self._hit_index(position) if position is not None else -1
        if self._hover >= 0:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        self.update()
        self.editingFinished.emit(index, self.bands())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        position = QPointF(event.pos())
        self.setFocus(Qt.MouseFocusReason)
        if (0 <= self._selected < len(self._bands)
                and self.bypass_rect().contains(position)):
            self._select_band(self._selected, emit=True, force=True)
            self.toggle_band_enabled(self._selected)
            event.accept()
            return
        index = self._hit_index(position)
        if index < 0:
            super().mousePressEvent(event)
            return
        self._select_band(index, emit=True, force=True)
        if event.modifiers() & Qt.AltModifier:
            self.toggle_band_enabled(index)
            event.accept()
            return
        self._start_band_edit(index, position)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        position = QPointF(event.pos())
        if self._drag_index >= 0:
            self._drag_position(position)
            event.accept()
            return

        hover = self._hit_index(position)
        if hover != self._hover:
            self._hover = hover
            if hover >= 0:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_index >= 0:
            self._finish_band_edit(QPointF(event.pos()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            index = self._hit_index(QPointF(event.pos()))
            if index >= 0:
                self.setFocus(Qt.MouseFocusReason)
                self._select_band(index, emit=True)
                self.reset_band(index)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            delta = event.pixelDelta().y() * 8
        if not delta:
            super().wheelEvent(event)
            return
        position = QPointF(event.pos())
        index = self._hit_index(position)
        if index < 0:
            index = self._selected
        if index < 0:
            super().wheelEvent(event)
            return
        if index != self._selected:
            self._select_band(index, emit=True)
        self.setFocus(Qt.MouseFocusReason)
        if self._bands[index]['type'] in Q_LOCKED_TYPES:
            event.accept()
            return
        notches = delta / 120.0
        fine = bool(event.modifiers() & Qt.ShiftModifier)
        factor = (1.035 if fine else 1.18) ** notches
        self._update_band(index, q=self._bands[index]['q'] * factor)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if not (0 <= self._selected < len(self._bands)):
            super().keyPressEvent(event)
            return
        index = self._selected
        band = self._bands[index]
        modifiers = event.modifiers()
        fine = bool(modifiers & Qt.ShiftModifier)
        ctrl = bool(modifiers & Qt.ControlModifier)
        frequency_ratio = 2.0 ** (1.0 / (240.0 if fine else 24.0))
        gain_step = 0.1 if fine else 0.5
        q_factor = 1.01 if fine else 1.06

        if event.key() == Qt.Key_Space:
            self.toggle_band_enabled(index)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.reset_band(index)
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self._update_band(index, freq_hz=band['freq_hz'] / frequency_ratio)
            event.accept()
            return
        if event.key() == Qt.Key_Right:
            self._update_band(index, freq_hz=band['freq_hz'] * frequency_ratio)
            event.accept()
            return
        if (event.key() in (Qt.Key_PageUp, Qt.Key_PageDown)
                or (ctrl and event.key() in (Qt.Key_Up, Qt.Key_Down))):
            if band['type'] in Q_LOCKED_TYPES:
                event.accept()
                return
            multiplier = q_factor if event.key() in (
                Qt.Key_PageUp, Qt.Key_Up) else 1.0 / q_factor
            self._update_band(index, q=band['q'] * multiplier)
            event.accept()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            if band['type'] in GAIN_LOCKED_TYPES:
                event.accept()
                return
            direction = gain_step if event.key() == Qt.Key_Up else -gain_step
            self._update_band(index, gain_db=band['gain_db'] + direction)
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    @staticmethod
    def _color_with_alpha(color: QColor, alpha: int) -> QColor:
        result = QColor(color)
        result.setAlpha(alpha)
        return result

    @staticmethod
    def _readable_surface(text: QColor, alpha: int) -> QColor:
        """A contrast scrim for transparent palettes, never opaque black."""
        result = QColor('#1b1d24' if text.lightness() > 127 else '#f7f8fb')
        result.setAlpha(alpha)
        return result

    @staticmethod
    def _contrast_ink(background: QColor) -> QColor:
        return QColor('#181a20' if background.lightness() > 158 else '#ffffff')

    @staticmethod
    def _frequency_label(frequency: int) -> str:
        if frequency >= 1000:
            return f'{frequency // 1000}k'
        return str(frequency)

    @staticmethod
    def _path_for_values(
        frequencies: Iterable[float], values: Iterable[float], plot: QRectF,
    ) -> QPainterPath:
        frequencies_list = list(frequencies)
        values_list = list(values)
        path = QPainterPath()
        if not frequencies_list:
            return path
        for index, (frequency, gain) in enumerate(
                zip(frequencies_list, values_list)):
            x = plot.left() + plot.width() * frequency_to_unit(frequency)
            y = plot.top() + ((MAX_GAIN_DB - float(gain))
                              / (MAX_GAIN_DB - MIN_GAIN_DB)) * plot.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        return path

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        palette = self.palette()
        text = palette.color(QPalette.Text)
        if not text.isValid() or text.alpha() == 0:
            text = palette.color(QPalette.WindowText)
        if not text.isValid() or text.alpha() == 0:
            text = QColor('#e8eaf2')
        base = palette.color(QPalette.Base)
        window = palette.color(QPalette.Window)
        accent = palette.color(QPalette.Highlight)
        if not accent.isValid() or accent.alpha() == 0:
            accent = QColor('#7c66ff')
        muted = self._color_with_alpha(text, 160)
        faint = self._color_with_alpha(text, 38)
        border = self._color_with_alpha(text, 72)
        plot = self.plot_rect()
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        surface = QColor(0, 0, 0, 0)
        for candidate in (base, window):
            if candidate.isValid() and candidate.alpha() > 0:
                surface = QColor(candidate)
                break
        transparent_surface = surface.alpha() == 0
        if transparent_surface:
            # Keep the host background visible while providing a stable
            # contrast scrim for rulers and labels on arbitrary backdrops.
            background = self._readable_surface(text, 145)
        else:
            background = QColor(surface)
            background.setAlpha(min(235, max(60, surface.alpha())))
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(outer, 10.0, 10.0)
        painter.fillRect(
            plot, self._color_with_alpha(text, 8 if transparent_surface else 12)
        )

        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        metrics = QFontMetricsF(font)

        painter.save()
        painter.setClipRect(plot)
        painter.setPen(QPen(faint, 1.0))
        for frequency in FREQUENCY_GRID:
            x = self.frequency_to_x(frequency)
            painter.drawLine(QPointF(x, plot.top()),
                             QPointF(x, plot.bottom()))
        for gain in GAIN_GRID_DB:
            y = self.gain_to_y(gain)
            if gain == 0:
                painter.setPen(QPen(self._color_with_alpha(text, 70), 1.0))
            else:
                painter.setPen(QPen(faint, 1.0))
            painter.drawLine(QPointF(plot.left(), y),
                             QPointF(plot.right(), y))

        frequencies, responses = self._response_data()
        enabled_values = None
        if any(band['enabled'] for band in self._bands):
            enabled_values = [0.0 for _ in frequencies]
            for band, response in zip(self._bands, responses):
                if band['enabled']:
                    for index, value in enumerate(response):
                        enabled_values[index] += value

        # Individual, semi-transparent band traces first.
        for band, response in zip(self._bands, responses):
            color = QColor(_BAND_COLORS.get(band['type'], accent))
            color.setAlpha(135 if band['enabled'] else 45)
            pen = QPen(color, 1.5)
            if not band['enabled']:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self._path_for_values(frequencies, response,
                                                   plot))

        if enabled_values is not None:
            total_path = self._path_for_values(frequencies, enabled_values,
                                               plot)
            fill_path = QPainterPath(total_path)
            zero_y = self.gain_to_y(0.0)
            fill_path.lineTo(plot.right(), zero_y)
            fill_path.lineTo(plot.left(), zero_y)
            fill_path.closeSubpath()
            painter.fillPath(fill_path, self._color_with_alpha(accent, 30))
            painter.setPen(QPen(accent, 2.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(total_path)
        else:
            painter.setPen(QPen(self._color_with_alpha(text, 85), 1.5,
                                Qt.DashLine))
            painter.drawLine(QPointF(plot.left(), self.gain_to_y(0.0)),
                             QPointF(plot.right(), self.gain_to_y(0.0)))

        if self._playhead is not None:
            playhead_x = plot.left() + plot.width() * self._playhead
            painter.setPen(QPen(self._color_with_alpha(accent, 150), 1.0,
                                Qt.DashLine))
            painter.drawLine(QPointF(playhead_x, plot.top()),
                             QPointF(playhead_x, plot.bottom()))
        painter.restore()
        # Axis labels sit outside the clipped drawing area.
        painter.setPen(muted)
        for gain in GAIN_GRID_DB:
            y = self.gain_to_y(gain)
            painter.drawText(
                QRectF(outer.left() + 2.0, y - 7.0,
                       plot.left() - outer.left() - 8.0, 14.0),
                Qt.AlignRight | Qt.AlignVCenter,
                f'{gain:+d}' if gain else '0',
            )
        for frequency in self.visible_frequency_grid():
            x = self.frequency_to_x(frequency)
            label = self._frequency_label(frequency)
            label_width = max(22.0, metrics.horizontalAdvance(label) + 8.0)
            label_x = _clamp(x - label_width / 2.0,
                             outer.left() + 1.0,
                             outer.right() - label_width - 1.0)
            painter.drawText(
                QRectF(label_x, plot.bottom() + 3.0, label_width, 14.0),
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )
        painter.drawText(
            QRectF(outer.left() + 45.0, outer.top() + 2.0,
                   max(86.0, plot.width() - 150.0), 18.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            f'{MIN_FREQUENCY_HZ:.0f} Hz - {MAX_FREQUENCY_HZ / 1000.0:.0f} kHz',
        )

        # A persistent pill makes bypass discoverable without a right-click or
        # hidden gesture. It is in the reserved top strip, never over a node.
        if 0 <= self._selected < len(self._bands):
            selected_band = self._bands[self._selected]
            button = self.bypass_rect()
            button_color = QColor(_BAND_COLORS.get(
                selected_band['type'], accent))
            button_background = (
                self._readable_surface(text, 228) if transparent_surface
                else QColor(background)
            )
            if button_background.alpha() < 220:
                button_background.setAlpha(235)
            painter.setPen(QPen(self._color_with_alpha(button_color, 190),
                                1.0))
            painter.setBrush(button_background)
            painter.drawRoundedRect(button, 5.0, 5.0)
            painter.setPen(text)
            painter.drawText(
                button.adjusted(6.0, 0.0, -6.0, 0.0),
                Qt.AlignCenter,
                ((_tr('旁路') if selected_band['enabled']
                  else _tr('启用')) + '  Space'),
            )

        # Nodes, selected last so it remains visible above its neighbours.
        order = [index for index in range(len(self._bands))
                 if index != self._selected]
        if 0 <= self._selected < len(self._bands):
            order.append(self._selected)
        for index in order:
            band = self._bands[index]
            center = self._band_position(band)
            color = QColor(_BAND_COLORS.get(band['type'], accent))
            if index == self._selected:
                painter.setPen(Qt.NoPen)
                painter.setBrush(self._color_with_alpha(color, 55))
                painter.drawEllipse(center, 9.0, 9.0)
            node_rect = QRectF(center.x() - NODE_DIAMETER / 2.0,
                               center.y() - NODE_DIAMETER / 2.0,
                               NODE_DIAMETER, NODE_DIAMETER)
            painter.setPen(QPen(
                color if band['enabled']
                else self._color_with_alpha(color, 125), 1.6))
            if band['enabled']:
                node_fill = color
            elif transparent_surface:
                node_fill = self._readable_surface(text, 230)
            else:
                node_fill = QColor(background)
                node_fill.setAlpha(230)
            painter.setBrush(node_fill)
            painter.drawEllipse(node_rect)
            if band['enabled']:
                painter.setPen(Qt.NoPen)
                painter.setBrush(self._contrast_ink(color))
                painter.drawEllipse(center, 1.4, 1.4)

        bubble_index = self._drag_index if self._drag_index >= 0 else self._hover
        if bubble_index < 0:
            bubble_index = self._selected
        if 0 <= bubble_index < len(self._bands):
            band = self._bands[bubble_index]
            center = self._band_position(band)
            bubble_text = self.bubble_text(bubble_index)
            bubble_width = max(128.0,
                               metrics.horizontalAdvance(bubble_text) + 18.0)
            bubble_height = 23.0
            bubble_x = center.x() - bubble_width / 2.0
            bubble_x = max(outer.left() + 5.0,
                           min(bubble_x, outer.right() - bubble_width - 5.0))
            bubble_y = center.y() - bubble_height - 10.0
            if bubble_y < outer.top() + 18.0:
                bubble_y = center.y() + 10.0
            bubble_y = max(outer.top() + 5.0,
                           min(bubble_y, outer.bottom() - bubble_height - 5.0))
            bubble_rect = QRectF(bubble_x, bubble_y, bubble_width,
                                 bubble_height)
            bubble_color = QColor(_BAND_COLORS.get(band['type'], accent))
            bubble_background = (
                self._readable_surface(text, 238) if transparent_surface
                else QColor(background)
            )
            if bubble_background.alpha() < 230:
                bubble_background.setAlpha(242)
            painter.setPen(QPen(self._color_with_alpha(bubble_color, 150),
                                1.0))
            painter.setBrush(bubble_background)
            painter.drawRoundedRect(bubble_rect, 6.0, 6.0)
            painter.setPen(text)
            painter.drawText(bubble_rect.adjusted(7.0, 0.0, -7.0, 0.0),
                             Qt.AlignCenter, bubble_text)

        if self.hasFocus():
            painter.setPen(QPen(self._color_with_alpha(accent, 210), 1.5,
                                Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(outer.adjusted(1.0, 1.0, -1.0, -1.0),
                                    9.0, 9.0)
        painter.end()