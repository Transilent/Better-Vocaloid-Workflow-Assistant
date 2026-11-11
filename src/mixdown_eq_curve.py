"""Qt5 four-band equalizer curve for the VWA mixdown inspector.

The Qt-free band model, normalization, logarithmic frequency mapping, and RBJ
response math live in :mod:`mixdown_eq_math`. This module re-exports that API
for existing Widgets callers and owns only QPainter interaction plus live
spectrum display state. Audio generation, spectrum measurement, audition
throttling, and persistence stay with the host UI; visible strings use the
shared project catalog. Live spectra never fall back to an older audition
report.

Band dictionaries follow the mixdown request contract::

    {"type": "bell", "freq_hz": 3200.0, "gain_db": 2.5,
     "q": 0.8, "enabled": True}
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
from mixdown_eq_math import (
    BAND_TYPES,
    DEFAULT_BANDS,
    DEFAULT_SAMPLE_RATE,
    FREQUENCY_GRID,
    GAIN_GRID_DB,
    GAIN_LOCKED_TYPES,
    HIGHPASS_FIXED_Q,
    MAX_BANDS,
    MAX_FREQUENCY_HZ,
    MAX_GAIN_DB,
    MAX_Q,
    MIN_FREQUENCY_HZ,
    MIN_GAIN_DB,
    MIN_Q,
    Q_LOCKED_TYPES,
    RESPONSE_POINTS,
    _clamp,
    _default_spec,
    _display_sample_rate,
    _finite_number,
    band_response_db,
    biquad_coefficients,
    default_bands,
    eq_frequency_limit_hz,
    frequency_response_db,
    frequency_to_unit,
    logarithmic_frequencies,
    normalise_band,
    normalize_band,
    response_curve,
    unit_to_frequency,
)


SPECTRUM_SCHEMA = 'vwa.audio_mixdown.spectrum/v1'
SPECTRUM_STAGE_BEFORE = 'vocal_before_eq'
SPECTRUM_STAGE_AFTER = 'vocal_after_eq'
SPECTRUM_STAGES = (SPECTRUM_STAGE_BEFORE, SPECTRUM_STAGE_AFTER)
# Kept as the default/backward-compatible stage name for existing callers.
SPECTRUM_STAGE = SPECTRUM_STAGE_AFTER
SPECTRUM_MIN_DBFS = -96.0
SPECTRUM_MAX_DBFS = 0.0
SPECTRUM_DBFS_GRID = (0.0, -24.0, -48.0, -72.0, -96.0)
LIVE_SPECTRUM_MAX_BINS = 4096
LIVE_SPECTRUM_SOURCE_MAX_LENGTH = 96
# Previous-frame weights for temporal display smoothing. Both are deliberately
# below the cap; measured frames are never replaced by generated values.
LIVE_SPECTRUM_ATTACK_WEIGHT = 0.38
LIVE_SPECTRUM_RELEASE_WEIGHT = 0.72
LIVE_SPECTRUM_MAX_SMOOTHING_WEIGHT = 0.82
LIVE_SPECTRUM_RESET_AFTER_SECONDS = 0.75
SPECTRUM_AXIS_WIDTH = 40.0
NODE_DIAMETER = 12.0
HIT_RADIUS = 22.0
MIN_WIDGET_WIDTH = 320
MIN_WIDGET_HEIGHT = 190

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
_SPECTRUM_STAGE_LABELS = {
    SPECTRUM_STAGE_BEFORE: 'A  处理前',
    SPECTRUM_STAGE_AFTER: 'B  处理后',
}
_SPECTRUM_STAGE_COLORS = {
    SPECTRUM_STAGE_BEFORE: QColor('#e3b45f'),
    SPECTRUM_STAGE_AFTER: QColor('#62cfdf'),
}

__all__ = [
    'BAND_TYPES', 'DEFAULT_BANDS', 'DEFAULT_SAMPLE_RATE', 'EQCurveView',
    'GAIN_GRID_DB', 'GAIN_LOCKED_TYPES', 'HIGHPASS_FIXED_Q', 'HIT_RADIUS',
    'MAX_BANDS', 'MAX_FREQUENCY_HZ', 'MAX_GAIN_DB', 'MAX_Q',
    'MIN_FREQUENCY_HZ', 'MIN_GAIN_DB', 'MIN_Q', 'MIN_WIDGET_HEIGHT',
    'MIN_WIDGET_WIDTH', 'NODE_DIAMETER', 'Q_LOCKED_TYPES',
    'LIVE_SPECTRUM_ATTACK_WEIGHT', 'LIVE_SPECTRUM_MAX_BINS',
    'LIVE_SPECTRUM_MAX_SMOOTHING_WEIGHT',
    'LIVE_SPECTRUM_RELEASE_WEIGHT', 'LIVE_SPECTRUM_RESET_AFTER_SECONDS',
    'LIVE_SPECTRUM_SOURCE_MAX_LENGTH', 'RESPONSE_POINTS',
    'SPECTRUM_AXIS_WIDTH', 'SPECTRUM_DBFS_GRID',
    'SPECTRUM_MAX_DBFS', 'SPECTRUM_MIN_DBFS',
    'SPECTRUM_SCHEMA', 'SPECTRUM_STAGE', 'SPECTRUM_STAGE_AFTER',
    'SPECTRUM_STAGE_BEFORE', 'SPECTRUM_STAGES', 'band_response_db',
    'biquad_coefficients', 'default_bands',
    'frequency_response_db', 'frequency_to_unit', 'logarithmic_frequencies',
    'normalise_band', 'normalize_band', 'response_curve', 'unit_to_frequency',
]


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

    A live DSP measurement is supplied one frame at a time with
    :meth:`set_live_spectrum`; the same frame contains both pre-EQ and post-EQ
    dBFS arrays and is painted behind the EQ traces and nodes. The older
    ``set_spectrum_envelope`` path remains available for legacy report callers,
    but a live update never falls back to that report data.
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
        self._live_spectrum: Optional[dict[str, Any]] = None
        self._live_spectrum_display: Optional[dict[str, Any]] = None
        self._spectrum_envelope: Optional[dict[str, Any]] = None
        self._spectrum_frame_index: Optional[int] = None
        self._spectrum_playhead_ms: Optional[float] = None
        self._spectrum_playing = False
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
        self._bands = [
            normalize_band(value, index, sample_rate=self._sample_rate)
            for index, value in enumerate(values)
        ]
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
        """Set the render rate used by both the curve and its frequency axis."""
        rate = _display_sample_rate(sample_rate)
        if rate != self._sample_rate:
            self._sample_rate = rate
            self._bands = [
                normalize_band(band, index, sample_rate=rate)
                for index, band in enumerate(self._bands)
            ]
            self._invalidate_response_cache()
            self._refresh_accessibility()
            self.update()

    def sample_rate(self) -> float:
        return self._sample_rate

    def frequency_limit_hz(self) -> float:
        """Effective upper frequency shared with the renderer at this rate."""
        return eq_frequency_limit_hz(self._sample_rate)

    def set_live_spectrum(
        self, frame: Optional[Mapping[str, Any]],
    ) -> bool:
        """Replace the display with one measured live DSP spectrum frame.

        The expected shape is::

            {
                'source': 'pedalboard_eq',       # actual active DSP
                'sample_rate': 48000,
                'media_frame': 123456,
                'freq_hz': (20.0, 23.4, ...),
                'before_dbfs': (-84.0, -79.2, ...),
                'after_dbfs': (-80.0, -74.1, ...),
            }

        Frequency bins must be ascending and lie at or below Nyquist. A single
        leading DC bin at 0 Hz is accepted and omitted from the log-frequency
        display. ``None`` clears the live trace. Invalid frames clear all
        spectrum data and return ``False``; stale live data and legacy report
        envelopes are never used as a fallback in a player callback.
        """
        if frame is None:
            self._clear_spectrum_state()
            self._refresh_accessibility()
            self.update()
            return False
        try:
            normalized = self._normalize_live_spectrum_frame(frame)
        except (TypeError, ValueError, OverflowError):
            self._clear_spectrum_state()
            self._refresh_accessibility()
            self.update()
            return False

        self.set_sample_rate(normalized['sample_rate'])
        display = self._smooth_live_spectrum(normalized)
        # A live frame is authoritative even when a legacy report was present.
        self._spectrum_envelope = None
        self._spectrum_frame_index = None
        self._spectrum_playhead_ms = None
        self._spectrum_playing = False
        self._live_spectrum = normalized
        self._live_spectrum_display = display
        self._refresh_accessibility()
        self.update()
        return True

    def set_live_spectrum_frame(
        self, frame: Optional[Mapping[str, Any]],
    ) -> bool:
        """Compatibility alias for :meth:`set_live_spectrum`."""
        return self.set_live_spectrum(frame)

    def clear_live_spectrum(self) -> None:
        """Remove the live and legacy spectrum traces."""
        self.set_live_spectrum(None)

    def has_live_spectrum(self) -> bool:
        return self._live_spectrum is not None

    @staticmethod
    def _public_live_frame(
        frame: Optional[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if frame is None:
            return None
        return {
            'source': frame['source'],
            'sample_rate': float(frame['sample_rate']),
            'media_frame': int(frame['media_frame']),
            'freq_hz': [float(value) for value in frame['freq_hz']],
            'before_dbfs': [float(value) for value in frame['before_dbfs']],
            'after_dbfs': [float(value) for value in frame['after_dbfs']],
        }

    def live_spectrum(self) -> Optional[dict[str, Any]]:
        """Return a copy of the latest accepted, unsmoothed DSP measurement."""
        return self._public_live_frame(self._live_spectrum)

    def live_spectrum_display(self) -> Optional[dict[str, Any]]:
        """Return a copy of the bounded temporal smoothing actually painted."""
        return self._public_live_frame(self._live_spectrum_display)

    def live_spectrum_source(self) -> Optional[str]:
        frame = self._live_spectrum
        return None if frame is None else frame['source']

    def live_spectrum_media_frame(self) -> Optional[int]:
        frame = self._live_spectrum
        return None if frame is None else frame['media_frame']

    def live_spectrum_status_text(self) -> Optional[str]:
        frame = self._live_spectrum
        if frame is None:
            return None
        return _tr(
            '实时频谱 · A 处理前 / B 处理后 · {0:.1f} kHz · {1}',
            frame['sample_rate'] / 1000.0,
            frame['source'],
        )

    @staticmethod
    def _normalize_live_spectrum_frame(
        frame: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(frame, Mapping):
            raise TypeError('live spectrum frame 必须为字典。')

        source = frame.get('source')
        if not isinstance(source, str):
            raise TypeError('source 必须为实际 DSP 名称。')
        source = source.strip()
        if (not source or len(source) > LIVE_SPECTRUM_SOURCE_MAX_LENGTH
                or any(ord(character) < 32 or ord(character) == 127
                       for character in source)):
            raise ValueError('source 必须为非空的可显示 DSP 名称。')

        sample_rate = _display_sample_rate(frame.get('sample_rate'))
        media_frame_value = _finite_number(frame.get('media_frame'),
                                           'media_frame')
        if (media_frame_value < 0.0
                or int(media_frame_value) != media_frame_value):
            raise ValueError('media_frame 必须为非负整数。')

        raw_frequencies = frame.get('freq_hz')
        if (isinstance(raw_frequencies, (str, bytes))
                or not isinstance(raw_frequencies, Iterable)):
            raise TypeError('freq_hz 必须为递增数字序列。')
        try:
            frequencies = list(raw_frequencies)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError('freq_hz 必须为递增数字序列。') from exc
        if not frequencies:
            raise ValueError('freq_hz 不能为空。')
        if len(frequencies) > LIVE_SPECTRUM_MAX_BINS + 1:
            raise ValueError('freq_hz 超过实时频谱显示上限。')

        normalized_frequencies: list[float] = []
        nyquist = sample_rate / 2.0
        nyquist_tolerance = max(1.0, sample_rate * 1e-6)
        for index, raw_frequency in enumerate(frequencies):
            frequency = _finite_number(raw_frequency, 'freq_hz')
            if frequency < 0.0:
                raise ValueError('freq_hz 不能为负数。')
            if frequency == 0.0 and index != 0:
                raise ValueError('0 Hz 只能作为首个直流频点。')
            if normalized_frequencies and frequency <= normalized_frequencies[-1]:
                raise ValueError('freq_hz 必须严格递增。')
            if frequency > nyquist + nyquist_tolerance:
                raise ValueError('freq_hz 不能超过奈奎斯特频率。')
            normalized_frequencies.append(frequency)

        def levels(key: str) -> list[float]:
            raw_values = frame.get(key)
            if (isinstance(raw_values, (str, bytes))
                    or not isinstance(raw_values, Iterable)):
                raise TypeError(f'{key} 必须为 dBFS 数字序列。')
            try:
                values = list(raw_values)
            except (TypeError, ValueError, OverflowError) as exc:
                raise TypeError(f'{key} 必须为 dBFS 数字序列。') from exc
            if len(values) != len(frequencies):
                raise ValueError(f'{key} 必须与 freq_hz 长度一致。')
            return [_finite_number(value, key) for value in values]

        before = levels('before_dbfs')
        after = levels('after_dbfs')
        if normalized_frequencies and normalized_frequencies[0] == 0.0:
            normalized_frequencies.pop(0)
            before.pop(0)
            after.pop(0)
        if len(normalized_frequencies) < 2:
            raise ValueError('实时频谱至少需要两个可显示频点。')
        if len(normalized_frequencies) > LIVE_SPECTRUM_MAX_BINS:
            raise ValueError('freq_hz 超过实时频谱显示上限。')

        return {
            'source': source,
            'sample_rate': sample_rate,
            'media_frame': int(media_frame_value),
            'freq_hz': tuple(normalized_frequencies),
            'before_dbfs': tuple(before),
            'after_dbfs': tuple(after),
        }

    @staticmethod
    def _smooth_live_levels(
        previous: tuple[float, ...], current: tuple[float, ...],
    ) -> tuple[float, ...]:
        smoothed = []
        maximum = _clamp(LIVE_SPECTRUM_MAX_SMOOTHING_WEIGHT, 0.0, 1.0)
        for old, new in zip(previous, current):
            weight = (LIVE_SPECTRUM_ATTACK_WEIGHT if new >= old
                      else LIVE_SPECTRUM_RELEASE_WEIGHT)
            weight = _clamp(weight, 0.0, maximum)
            smoothed.append(weight * old + (1.0 - weight) * new)
        return tuple(smoothed)

    def _smooth_live_spectrum(
        self, frame: Mapping[str, Any],
    ) -> dict[str, Any]:
        previous = self._live_spectrum_display
        contiguous = False
        if previous is not None:
            reset_span = (frame['sample_rate']
                          * LIVE_SPECTRUM_RESET_AFTER_SECONDS)
            contiguous = (
                previous['source'] == frame['source']
                and previous['sample_rate'] == frame['sample_rate']
                and previous['freq_hz'] == frame['freq_hz']
                and previous['media_frame'] <= frame['media_frame']
                and frame['media_frame'] - previous['media_frame'] <= reset_span
            )
        if contiguous:
            before = self._smooth_live_levels(
                previous['before_dbfs'], frame['before_dbfs'])
            after = self._smooth_live_levels(
                previous['after_dbfs'], frame['after_dbfs'])
        else:
            before = tuple(frame['before_dbfs'])
            after = tuple(frame['after_dbfs'])
        return {
            'source': frame['source'],
            'sample_rate': frame['sample_rate'],
            'media_frame': frame['media_frame'],
            'freq_hz': tuple(frame['freq_hz']),
            'before_dbfs': before,
            'after_dbfs': after,
        }

    def _clear_spectrum_state(self) -> None:
        self._live_spectrum = None
        self._live_spectrum_display = None
        self._spectrum_envelope = None
        self._spectrum_frame_index = None
        self._spectrum_playhead_ms = None
        self._spectrum_playing = False

    def set_spectrum_envelope(
        self, envelope: Optional[Mapping[str, Any]],
    ) -> bool:
        """Replace the segment-relative spectrum envelope safely.

        ``envelope`` is the value of ``report['spectrum_envelope']``.  The
        widget copies and validates it, then clears any playhead/frame from the
        previous audition segment.  Malformed data clears the old spectrum and
        returns ``False`` instead of leaving stale audio visible or raising in
        a player callback.  Empty frames and missing/non-finite bins are valid
        gaps and are never replaced with generated audio values.
        """
        self._live_spectrum = None
        self._live_spectrum_display = None
        normalized = None
        if envelope is not None:
            try:
                normalized = self._normalize_spectrum_envelope(envelope)
            except (TypeError, ValueError, OverflowError):
                normalized = None
        self._spectrum_envelope = normalized
        self._spectrum_frame_index = None
        self._spectrum_playhead_ms = None
        self._spectrum_playing = False
        self._refresh_accessibility()
        self.update()
        return normalized is not None

    def clear_spectrum_envelope(self) -> None:
        """Remove the spectrum data and its segment-relative playhead."""
        self.set_spectrum_envelope(None)

    def spectrum_envelope(self) -> Optional[dict[str, Any]]:
        """Return an independent copy of the normalised spectrum envelope."""
        return copy.deepcopy(self._spectrum_envelope)

    def spectrum_stage(self) -> Optional[str]:
        """Return the exact A/B data stage, or ``None`` when no data exists."""
        envelope = self._spectrum_envelope
        return None if envelope is None else envelope['stage']

    def spectrum_stage_label(self) -> Optional[str]:
        """Return the translated visible A/B label for the current stage."""
        stage = self.spectrum_stage()
        label = _SPECTRUM_STAGE_LABELS.get(stage)
        return _tr(label) if label is not None else None

    def _spectrum_stage_color(self) -> Optional[QColor]:
        stage = self.spectrum_stage()
        return self._spectrum_color_for_stage(stage)

    def _spectrum_color_for_stage(self, stage: Optional[str]) -> Optional[QColor]:
        color = _SPECTRUM_STAGE_COLORS.get(stage)
        # The pale cyan/amber used on dark panels is too faint on the light
        # theme. Keep the A/B distinction while increasing contrast there.
        if color is not None and self.palette().color(QPalette.Text).lightness() < 128:
            color = (QColor('#a76716') if stage == SPECTRUM_STAGE_BEFORE
                     else QColor('#16899a'))
        return QColor(color) if color is not None else None

    def set_spectrum_playhead(
        self, position_ms: Optional[float], *, playing: bool = True,
    ) -> bool:
        """Select and paint the spectrum frame at a segment-relative time.

        Frame zero starts at 0 ms and each frame lasts ``interval_ms``.
        Positions are clamped to the available frames.  Playing state affects
        only the exposed transport flag: every explicitly supplied position,
        including a paused seek or an A/B replacement, immediately selects its
        measured frame.  This widget owns no timer, so a paused host advances
        nothing by simply not sending new positions.  It never synthesises
        frames between measurements.
        """
        self._spectrum_playing = bool(playing)
        if position_ms is None:
            self._spectrum_playing = False
            self._spectrum_playhead_ms = None
            self._spectrum_frame_index = None
            self.update()
            return False
        try:
            position = max(0.0, _finite_number(position_ms, 'position_ms'))
        except ValueError:
            self._spectrum_playing = False
            self._spectrum_playhead_ms = None
            self._spectrum_frame_index = None
            self.update()
            return False

        self._spectrum_playhead_ms = position
        envelope = self._spectrum_envelope
        if envelope is None or not envelope['frames_dbfs']:
            self._spectrum_frame_index = None
            self.update()
            return False

        frame_count = len(envelope['frames_dbfs'])
        frame_index = min(
            int(position // envelope['interval_ms']), frame_count - 1,
        )
        paint_changed = frame_index != self._spectrum_frame_index
        self._spectrum_frame_index = frame_index
        drawable = self._selected_spectrum_frame() is not None
        if paint_changed:
            self.update()
        return drawable

    def set_spectrum_playback(
        self, position_ms: Optional[float], *, playing: bool = True,
    ) -> bool:
        """Alias for hosts that name their transport callback ``playback``."""
        return self.set_spectrum_playhead(position_ms, playing=playing)

    def spectrum_playhead_ms(self) -> Optional[float]:
        """Return the last accepted segment-relative playhead in milliseconds."""
        return self._spectrum_playhead_ms

    def spectrum_is_playing(self) -> bool:
        return self._spectrum_playing

    def spectrum_frame(self) -> Optional[list[Optional[float]]]:
        """Return the selected frame as a copy; ``None`` means no drawable bin."""
        frame = self._selected_spectrum_frame()
        if frame is None:
            return None
        return [None if value is None else float(value) for value in frame]

    @staticmethod
    def _frame_has_values(
        frame: Optional[Iterable[Optional[float]]],
    ) -> bool:
        return frame is not None and any(value is not None for value in frame)

    @classmethod
    def _normalize_spectrum_envelope(
        cls, envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(envelope, Mapping):
            raise TypeError('spectrum_envelope 必须为字典。')
        if envelope.get('schema') != SPECTRUM_SCHEMA:
            raise ValueError('不支持的频谱包络版本。')
        stage = envelope.get('stage')
        if stage not in SPECTRUM_STAGES:
            raise ValueError(
                '频谱包络 stage 必须为 vocal_before_eq 或 vocal_after_eq。'
            )

        interval = _finite_number(envelope.get('interval_ms'), 'interval_ms')
        if interval <= 0.0:
            raise ValueError('interval_ms 必须大于 0。')
        fft_size = _finite_number(envelope.get('fft_size'), 'fft_size')
        if fft_size < 1.0 or int(fft_size) != fft_size:
            raise ValueError('fft_size 必须为正整数。')

        raw_frequencies = envelope.get('frequencies_hz')
        if (isinstance(raw_frequencies, (str, bytes))
                or not isinstance(raw_frequencies, Iterable)):
            raise TypeError('frequencies_hz 必须为递增数字序列。')
        frequencies = []
        previous = None
        for raw_frequency in raw_frequencies:
            frequency = _finite_number(raw_frequency, 'frequencies_hz')
            if frequency <= 0.0 or (previous is not None
                                    and frequency <= previous):
                raise ValueError('frequencies_hz 必须严格递增且大于 0 Hz。')
            frequencies.append(frequency)
            previous = frequency

        raw_frames = envelope.get('frames_dbfs')
        if (isinstance(raw_frames, (str, bytes))
                or not isinstance(raw_frames, Iterable)):
            raise TypeError('frames_dbfs 必须为帧序列。')
        frames = []
        bin_count = len(frequencies)
        for raw_frame in raw_frames:
            try:
                values = list(raw_frame)
            except (TypeError, ValueError, OverflowError):
                values = []
            normalised: list[Optional[float]] = []
            for index in range(bin_count):
                if index >= len(values):
                    normalised.append(None)
                    continue
                try:
                    value = float(values[index])
                except (TypeError, ValueError, OverflowError):
                    normalised.append(None)
                else:
                    normalised.append(value if math.isfinite(value) else None)
            frames.append(normalised)

        return {
            'schema': SPECTRUM_SCHEMA,
            'interval_ms': interval,
            'frequencies_hz': frequencies,
            'frames_dbfs': frames,
            'stage': stage,
            'fft_size': int(fft_size),
        }

    def _selected_spectrum_frame(
        self,
    ) -> Optional[list[Optional[float]]]:
        envelope = self._spectrum_envelope
        index = self._spectrum_frame_index
        if envelope is None or index is None:
            return None
        frames = envelope['frames_dbfs']
        if not (0 <= index < len(frames)):
            return None
        frame = frames[index]
        return frame if self._frame_has_values(frame) else None

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
            'freq_hz': (MIN_FREQUENCY_HZ, self.frequency_limit_hz()),
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
        factory = normalize_band(
            _default_spec(target), target, sample_rate=self._sample_rate)
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
        # The left ruler is EQ gain; the right ruler is measured dBFS. Keeping
        # them separate prevents the two very different units sharing labels.
        right = outer.right() - SPECTRUM_AXIS_WIDTH
        bottom = outer.bottom() - 28.0
        return QRectF(left, top, max(1.0, right - left),
                      max(1.0, bottom - top))

    def _axis_grid_frequencies(self) -> tuple[float, ...]:
        limit = self.frequency_limit_hz()
        values = [float(value) for value in FREQUENCY_GRID if value <= limit]
        if limit < MAX_FREQUENCY_HZ - 0.5:
            values.append(limit)
        result = []
        for value in values:
            if not result or abs(result[-1] - value) > 0.5:
                result.append(value)
        return tuple(result)

    def visible_frequency_grid(self) -> tuple[int, ...]:
        """Return ruler labels that fit the current compact/full width."""
        limit = self.frequency_limit_hz()
        if self.plot_rect().width() >= 420.0:
            values = list(self._axis_grid_frequencies())
        else:
            values = [float(value) for value in (10, 100, 1000, 10000)
                      if value <= limit]
            if limit < MAX_FREQUENCY_HZ - 0.5:
                values.append(limit)
        return tuple(dict.fromkeys(int(round(value)) for value in values))

    def frequency_to_x(self, frequency_hz: float) -> float:
        plot = self.plot_rect()
        return plot.left() + plot.width() * frequency_to_unit(
            frequency_hz, self._sample_rate)

    def x_to_frequency(self, x_position: float) -> float:
        plot = self.plot_rect()
        unit = (float(x_position) - plot.left()) / max(plot.width(), 1.0)
        return unit_to_frequency(unit, self._sample_rate)

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

    def spectrum_db_to_y(self, level_dbfs: float) -> float:
        """Map the fixed spectrum display range onto the EQ plot rectangle."""
        plot = self.plot_rect()
        level = _clamp(_finite_number(level_dbfs, 'level_dbfs'),
                       SPECTRUM_MIN_DBFS, SPECTRUM_MAX_DBFS)
        unit = ((SPECTRUM_MAX_DBFS - level)
                / (SPECTRUM_MAX_DBFS - SPECTRUM_MIN_DBFS))
        return plot.top() + plot.height() * unit

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
            frequencies = tuple(logarithmic_frequencies(
                RESPONSE_POINTS, sample_rate=self._sample_rate))
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
                                     self.frequency_limit_hz()), 2)
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
        description = self.accessible_summary()
        live_label = self.live_spectrum_status_text()
        stage_label = self.spectrum_stage_label()
        if live_label is not None:
            description = live_label + '。' + description
        elif stage_label is not None:
            description = stage_label + '。' + description
        self.setAccessibleDescription(description)
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
        span = math.log10(self.frequency_limit_hz() / MIN_FREQUENCY_HZ)
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
        value = float(frequency)
        if value >= 1000.0:
            return f'{value / 1000.0:.1f}'.rstrip('0').rstrip('.') + 'k'
        return f'{value:.0f}'

    def _path_for_values(
        self, frequencies: Iterable[float], values: Iterable[float], plot: QRectF,
    ) -> QPainterPath:
        frequencies_list = list(frequencies)
        values_list = list(values)
        path = QPainterPath()
        if not frequencies_list:
            return path
        for index, (frequency, gain) in enumerate(
                zip(frequencies_list, values_list)):
            x = plot.left() + plot.width() * frequency_to_unit(
                frequency, self._sample_rate)
            y = plot.top() + ((MAX_GAIN_DB - float(gain))
                              / (MAX_GAIN_DB - MIN_GAIN_DB)) * plot.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        return path

    def _spectrum_segments(self) -> list[list[QPointF]]:
        """Return contiguous drawable spectrum runs in widget coordinates."""
        if self._live_spectrum_display is not None:
            return self._live_spectrum_segments(SPECTRUM_STAGE_AFTER)
        frame = self._selected_spectrum_frame()
        envelope = self._spectrum_envelope
        if frame is None or envelope is None:
            return []
        segments: list[list[QPointF]] = []
        current: list[QPointF] = []
        for frequency, level in zip(envelope['frequencies_hz'], frame):
            if level is None:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(QPointF(self.frequency_to_x(frequency),
                                   self.spectrum_db_to_y(level)))
        if current:
            segments.append(current)
        return [segment for segment in segments if len(segment) >= 2]

    def _live_spectrum_segments(
        self, stage: str,
    ) -> list[list[QPointF]]:
        """Return one measured live stage on the logarithmic frequency axis."""
        if stage == SPECTRUM_STAGE_BEFORE:
            key = 'before_dbfs'
        elif stage == SPECTRUM_STAGE_AFTER:
            key = 'after_dbfs'
        else:
            raise ValueError('未知的实时频谱阶段。')
        frame = self._live_spectrum_display
        if frame is None:
            return []
        limit = self.frequency_limit_hz()
        points = [
            QPointF(self.frequency_to_x(frequency),
                    self.spectrum_db_to_y(level))
            for frequency, level in zip(frame['freq_hz'], frame[key])
            if MIN_FREQUENCY_HZ <= frequency <= limit
        ]
        return [points] if len(points) >= 2 else []

    def _paint_live_trace(
        self, painter: QPainter, plot: QRectF, points: list[QPointF],
        color: QColor, *, fill_alpha: int, line_alpha: int,
        line_width: float,
    ) -> None:
        if len(points) < 2:
            return
        curve = QPainterPath(points[0])
        for point in points[1:]:
            curve.lineTo(point)
        area = QPainterPath(curve)
        area.lineTo(points[-1].x(), plot.bottom())
        area.lineTo(points[0].x(), plot.bottom())
        area.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color_with_alpha(color, fill_alpha))
        painter.drawPath(area)
        pen = QPen(self._color_with_alpha(color, line_alpha), line_width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(curve)

    def _paint_live_spectrum(self, painter: QPainter, plot: QRectF) -> None:
        before_color = (self._spectrum_color_for_stage(
            SPECTRUM_STAGE_BEFORE) or QColor('#e3b45f'))
        after_color = (self._spectrum_color_for_stage(
            SPECTRUM_STAGE_AFTER) or QColor('#62cfdf'))
        before = self._live_spectrum_segments(SPECTRUM_STAGE_BEFORE)
        after = self._live_spectrum_segments(SPECTRUM_STAGE_AFTER)
        for points in before:
            self._paint_live_trace(
                painter, plot, points, before_color,
                fill_alpha=16, line_alpha=196, line_width=1.65,
            )
        for points in after:
            self._paint_live_trace(
                painter, plot, points, after_color,
                fill_alpha=24, line_alpha=232, line_width=2.15,
            )

    def _paint_spectrum(self, painter: QPainter, plot: QRectF) -> None:
        """Paint measured frames behind the editable EQ response."""
        if self._live_spectrum_display is not None:
            self._paint_live_spectrum(painter, plot)
            return
        segments = self._spectrum_segments()
        if not segments:
            return
        spectrum_color = self._spectrum_stage_color() or QColor('#62cfdf')
        fill_color = self._color_with_alpha(spectrum_color, 36)
        line_color = self._color_with_alpha(spectrum_color, 145)
        for points in segments:
            curve = QPainterPath(points[0])
            for point in points[1:]:
                curve.lineTo(point)
            area = QPainterPath(curve)
            area.lineTo(points[-1].x(), plot.bottom())
            area.lineTo(points[0].x(), plot.bottom())
            area.closeSubpath()
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill_color)
            painter.drawPath(area)
            painter.setPen(QPen(line_color, 1.25))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(curve)

    def _paint_dbfs_axis(
        self, painter: QPainter, plot: QRectF, outer: QRectF,
        text: QColor,
    ) -> None:
        """Draw a separate measured-amplitude scale in the right gutter."""
        if self._live_spectrum_display is None and self._spectrum_envelope is None:
            return
        live_color = self._spectrum_color_for_stage(SPECTRUM_STAGE_AFTER)
        axis_color = (live_color if self._live_spectrum_display is not None
                      else self._spectrum_stage_color())
        if axis_color is None:
            axis_color = text
        painter.save()
        painter.setPen(QPen(self._color_with_alpha(axis_color, 118), 1.0))
        for level in SPECTRUM_DBFS_GRID:
            y = self.spectrum_db_to_y(level)
            painter.drawLine(QPointF(plot.right(), y),
                             QPointF(plot.right() + 3.5, y))
        painter.setPen(self._color_with_alpha(text, 165))
        for level in SPECTRUM_DBFS_GRID:
            y = self.spectrum_db_to_y(level)
            painter.drawText(
                QRectF(plot.right() + 5.0, y - 7.0,
                       max(18.0, outer.right() - plot.right() - 8.0), 14.0),
                Qt.AlignLeft | Qt.AlignVCenter,
                f'{level:.0f}',
            )
        painter.translate(plot.right() + 31.0, plot.center().y())
        painter.rotate(-90.0)
        painter.setPen(self._color_with_alpha(axis_color, 190))
        painter.drawText(
            QRectF(-30.0, -7.0, 60.0, 14.0),
            Qt.AlignCenter,
            'dBFS',
        )
        painter.restore()

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
        for frequency in self._axis_grid_frequencies():
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

        self._paint_spectrum(painter, plot)
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
        self._paint_dbfs_axis(painter, plot, outer, text)
        frequency_limit = self.frequency_limit_hz()
        if frequency_limit >= 1000.0:
            upper_text = f'{frequency_limit / 1000.0:.1f} kHz'.replace(
                '.0 kHz', ' kHz')
        else:
            upper_text = f'{frequency_limit:.0f} Hz'
        range_text = f'{MIN_FREQUENCY_HZ:.0f} Hz - {upper_text}'
        live_header = self.live_spectrum_status_text()
        stage_label = self.spectrum_stage_label()
        stage_color = self._spectrum_stage_color()
        if live_header is not None:
            header_text = live_header
            painter.setPen(self._color_with_alpha(
                self._spectrum_color_for_stage(SPECTRUM_STAGE_AFTER)
                or accent, 225))
        elif stage_label is not None and stage_color is not None:
            header_text = f'{stage_label} · {range_text}'
            painter.setPen(self._color_with_alpha(stage_color, 220))
        else:
            header_text = range_text
        painter.drawText(
            QRectF(outer.left() + 45.0, outer.top() + 2.0,
                   max(86.0, plot.width() - 150.0), 18.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            header_text,
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
