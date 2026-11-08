"""Simple vocal/instrumental mixdown dialog with cancellable background renders."""
from datetime import datetime
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from PyQt5.QtCore import QEvent, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import (QColor, QDesktopServices, QFontMetrics, QKeySequence,
                         QLinearGradient, QPainter, QPainterPath, QPen)
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QDial, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                             QSpinBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                             QPushButton, QScrollArea, QShortcut, QSizePolicy,
                             QSlider, QStyle, QStyleOptionSlider, QTabWidget,
                             QToolButton, QVBoxLayout, QWidget)

import audio_mixdown as _mixdown_backend
import desktop_theme as _desktop_theme
from desktop_theme import button, card, fold, label
from mixdown_control_panel import (EqBandStripControls, PerTrackSendControls,
                                   PresetBar, WidthControl)
from mixdown_eq_curve import EQCurveView, default_bands
from mixdown_workspace import MixerWorkspace as _MixerWorkspace
from mixdown_contract import (
    DEFAULT_SAMPLE_RATE,
    GAIN_REDUCTION_SCOPE,
    eq_frequency_limit_hz,
    project_eq_band,
    valid_sample_rate,
)
from mixdown_live_meters import GrHistoryView, MeterEnvelope, ReverbDecayView
from mixdown_realtime_player import MediaStatus, PlaybackState
from mixdown_presets import catalog as preset_catalog, resolve_preset
from mixer_state import normalize_eq_bands
from i18n import tr as _tr
from ui_components import AdaptiveColumns, FlowLayout


_PREVIEW_PREFIX = 'vwa-mx-'
_PREVIEW_CACHE_PREFIX = 'vwa-mxl-'
_PLAYER_SEEK_COALESCE_MS = 60
_OPTIONAL_PACKAGES = None
_MAX_VOCAL_TRACKS = 3
_VOCAL_FILTER = _tr('歌声干声 WAV (*.wav)')
_INSTRUMENTAL_FILTER = _tr('音频 (*.wav *.flac *.mp3 *.ogg *.m4a *.aac)')
_WAV_FILTER = _tr('WAV 音频 (*.wav)')
# Stable object names for offscreen UI checks and host-level automation.
CONTROL_NAMES = {
    'task_entry': 'taskFilesMixdownButton',
    'dialog': 'mixdownDialog',
    'support_panel': 'mixdownSupportPanel',
    'support_state': 'mixdownSupportState',
    'install_dependency': 'mixdownInstallDependency',
    'vocal_tracks': 'mixdownVocalTracks',
    'add_vocal_track': 'mixdownAddVocalTrack',
    'vocal_path': 'mixdownVocalPath',
    'vocal_2_path': 'mixdownVocal2Path',
    'vocal_3_path': 'mixdownVocal3Path',
    'instrumental_path': 'mixdownInstrumentalPath',
    'output_path': 'mixdownOutputPath',
    'browse_vocal': 'mixdownBrowseVocal',
    'browse_vocal_2': 'mixdownBrowseVocal2',
    'browse_vocal_3': 'mixdownBrowseVocal3',
    'browse_instrumental': 'mixdownBrowseInstrumental',
    'browse_output': 'mixdownBrowseOutput',
    'vocal_state': 'mixdownVocalState',
    'vocal_2_state': 'mixdownVocal2State',
    'vocal_3_state': 'mixdownVocal3State',
    'instrumental_state': 'mixdownInstrumentalState',
    'output_state': 'mixdownOutputState',
    'vocal_db': 'mixdownVocalDb',
    'vocal_2_db': 'mixdownVocal2Db',
    'vocal_3_db': 'mixdownVocal3Db',
    'instrumental_db': 'mixdownInstrumentalDb',
    'vocal_slider': 'mixdownVocalLevelSlider',
    'vocal_2_slider': 'mixdownVocal2LevelSlider',
    'vocal_3_slider': 'mixdownVocal3LevelSlider',
    'instrumental_slider': 'mixdownInstrumentalLevelSlider',
    'offset': 'mixdownOffset',
    'offset_2': 'mixdownOffset2',
    'offset_3': 'mixdownOffset3',
    'eq_enabled': 'mixdownEqEnabled',
    'compression_enabled': 'mixdownCompressionEnabled',
    'reverb_enabled': 'mixdownReverbEnabled',
    'low_cut': 'mixdownLowCut',
    'presence': 'mixdownPresence',
    'compression': 'mixdownCompression',
    'reverb': 'mixdownReverb',
    'advanced_toggle': 'mixdownAdvancedToggle',
    'preview_start': 'mixdownPreviewStart',
    'preview_duration': 'mixdownPreviewDuration',
    'preview_range_toggle': 'mixdownPreviewRangeToggle',
    'preview_render': 'mixdownPreviewRender',
    'preview_before': 'mixdownPreviewBefore',
    'preview_after': 'mixdownPreviewAfter',
    'preview_status': 'mixdownPreviewStatus',
    'preview_before_state': 'mixdownPreviewBeforeState',
    'preview_after_state': 'mixdownPreviewAfterState',
    'player_loop': 'mixdownPlayerLoop',
    'system_player_fallback': 'mixdownSystemPlayerFallback',
    'result_title': 'mixdownResultTitle',
    'result_status': 'mixdownResultStatus',
    'export': 'mixdownExport',
    'cancel': 'mixdownCancel',
    'register': 'mixdownRegister',
    'register_video': 'mixdownRegisterAndVideo',
    'status': 'mixdownStatus',
    'progress': 'mixdownProgress',
    'close': 'mixdownClose',
}


def _set_role(widget, role):
    role = role or ''
    if widget.property('role') == role:
        return
    widget.setProperty('role', role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _Cancelled(RuntimeError):
    pass


def _db_to_fraction(db, minimum=-24.0, maximum=12.0):
    """Map dB to a fader taper; meters ending at 0 dB remain linear."""
    value = max(minimum, min(maximum, float(db)))
    zero = 0.0 if minimum <= 0.0 <= maximum else minimum
    if maximum <= zero:
        return (value - minimum) / max(1e-12, zero - minimum)
    zero_fraction = 0.78 if zero > minimum else 0.0
    if value <= zero and zero > minimum:
        return zero_fraction * (value - minimum) / (zero - minimum)
    if value <= zero:
        return 0.0
    return zero_fraction + (1.0 - zero_fraction) * (value - zero) / (maximum - zero)


def _fraction_to_db(fraction, minimum=-24.0, maximum=12.0):
    fraction = max(0.0, min(1.0, float(fraction)))
    zero = 0.0 if minimum <= 0.0 <= maximum else minimum
    if maximum <= zero:
        return minimum + (zero - minimum) * fraction
    zero_fraction = 0.78 if zero > minimum else 0.0
    if fraction <= zero_fraction and zero > minimum:
        return minimum + (zero - minimum) * fraction / zero_fraction
    return zero + (maximum - zero) * (fraction - zero_fraction) / (1.0 - zero_fraction)


class _SeekSlider(QSlider):
    """An absolute timeline: left-click locates, dragging keeps its grab offset."""

    seekCancelled = pyqtSignal()

    def _cancel_gesture(self):
        if self.isSliderDown():
            blocked = self.blockSignals(True)
            try:
                self.setSliderDown(False)
            finally:
                self.blockSignals(blocked)
            self.seekCancelled.emit()

    def event(self, event):
        if event.type() in (QEvent.UngrabMouse, QEvent.WindowDeactivate, QEvent.Hide):
            self._cancel_gesture()
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isSliderDown():
            self._cancel_gesture()
            event.accept()
            return
        super().keyPressEvent(event)

    def _mouse_position(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        pixel = event.pos().x() - getattr(self, '_grab_offset', 0) - groove.left() - handle.width() // 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), max(0, min(span, pixel)), span, option.upsideDown)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.minimum() == self.maximum():
            return super().mousePressEvent(event)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        self._grab_offset = event.pos().x() - handle.center().x() if handle.contains(event.pos()) else 0
        self.setFocus(Qt.MouseFocusReason)
        self.setSliderDown(True)
        self.setSliderPosition(self._mouse_position(event))
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown() and event.buttons() & Qt.LeftButton:
            self.setSliderPosition(self._mouse_position(event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.setSliderPosition(self._mouse_position(event))
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _DbFader(QWidget):
    """A compact vertical fader with snap, fine drag and keyboard control."""

    valueChanged = pyqtSignal(int)
    _SNAP_TENTHS = 5

    def __init__(self, minimum=-240, maximum=120, value=0, parent=None):
        super().__init__(parent)
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self._value = max(self._minimum, min(self._maximum, int(value)))
        self._dragging = False
        self._drag_origin_y = 0
        self._drag_origin_value = self._value
        self._drag_fine = False
        self.setMinimumSize(54, 146)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleDescription(_tr(
            '双击回零；Shift+拖动或 Shift+方向键微调 0.1 dB；普通滚轮用于滚动页面。'))
        self._update_tooltip()

    def sizeHint(self):
        return QSize(60, 166)

    def minimumSizeHint(self):
        return QSize(54, 132)

    def value(self):
        return self._value

    def _update_tooltip(self):
        self.setToolTip(
            self._db_text() + '  ·  '
            + _tr('双击回零；Shift+拖动或方向键微调 0.1 dB'))

    def setValue(self, value):
        value = max(self._minimum, min(self._maximum, int(round(value))))
        if value == self._value:
            return
        self._value = value
        self._update_tooltip()
        self.update()
        self.valueChanged.emit(value)

    def setRange(self, minimum, maximum):
        self._minimum, self._maximum = int(minimum), int(maximum)
        self.setValue(self._value)
        self._update_tooltip()

    def setMinimum(self, minimum):
        self.setRange(minimum, self._maximum)

    def setMaximum(self, maximum):
        self.setRange(self._minimum, maximum)

    def _db_text(self):
        value = self._value / 10.0
        return ('+' if value > 0 else '') + f'{value:.1f} dB'

    def _track_rect(self):
        return QRectF(max(24.0, self.width() - 27.0), 12.0, 7.0,
                      max(30.0, self.height() - 24.0))

    def _handle_y(self):
        track = self._track_rect()
        fraction = _db_to_fraction(
            self._value / 10.0, self._minimum / 10.0, self._maximum / 10.0)
        return track.bottom() - fraction * track.height()

    def _set_from_y(self, y, *, snap=True):
        track = self._track_rect()
        fraction = 1.0 - (float(y) - track.top()) / max(1.0, track.height())
        db = _fraction_to_db(
            fraction, self._minimum / 10.0, self._maximum / 10.0)
        value = int(round(db * 10.0))
        if snap and abs(value) <= self._SNAP_TENTHS:
            value = 0
        self.setValue(value)

    def _drag_to(self, y, modifiers):
        fine = bool(modifiers & Qt.ShiftModifier)
        if fine != self._drag_fine:
            self._drag_fine = fine
            self._drag_origin_y = int(y)
            self._drag_origin_value = self._value
        if fine:
            # One pixel is one internal 0.1 dB step. The anchor avoids a jump
            # when Shift is pressed after an ordinary drag has started.
            self.setValue(self._drag_origin_value + self._drag_origin_y - int(y))
        else:
            self._set_from_y(y, snap=True)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = self.palette()
        text_color = palette.color(palette.Text)
        muted = QColor(text_color)
        muted.setAlpha(155)
        groove = QColor(text_color)
        groove.setAlpha(30)
        accent = palette.color(palette.Highlight)
        if not accent.isValid():
            accent = QColor('#705af8')
        paint_accent = accent if self.isEnabled() else palette.color(palette.Mid)
        track = self._track_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(groove)
        painter.drawRoundedRect(track, 3.5, 3.5)

        handle_y = self._handle_y()
        fill = QRectF(track.left(), handle_y, track.width(), track.bottom() - handle_y)
        gradient = QLinearGradient(0, track.bottom(), 0, track.top())
        gradient.setColorAt(0.0, QColor(paint_accent).darker(125))
        gradient.setColorAt(1.0, paint_accent)
        painter.setBrush(gradient)
        painter.drawRoundedRect(fill, 3.5, 3.5)

        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() * .72))
        painter.setFont(font)
        for db in (12, 0, -6, -12, -24):
            y = track.bottom() - _db_to_fraction(
                db, self._minimum / 10.0, self._maximum / 10.0) * track.height()
            zero = db == 0
            pen = QPen(paint_accent if zero else muted)
            pen.setWidthF(1.35 if zero else 1.0)
            painter.setPen(pen)
            painter.drawLine(int(track.left() - (7 if zero else 4)), int(y),
                             int(track.left() + (6 if zero else 3)), int(y))
            painter.setPen(paint_accent if zero else muted)
            label = ('+' if db > 0 else '') + str(db)
            painter.drawText(QRectF(0, y - 8, track.left() - 8, 16),
                             Qt.AlignRight | Qt.AlignVCenter, label)

        handle = QRectF(track.center().x() - 17, handle_y - 7, 34, 14)
        edge = QColor(paint_accent).darker(125)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(paint_accent)
        painter.drawRoundedRect(handle, 5, 5)
        painter.setPen(QPen(QColor(255, 255, 255, 160), 1.0))
        painter.drawLine(int(handle.left() + 8), int(handle.center().y()),
                         int(handle.right() - 8), int(handle.center().y()))
        if self.hasFocus():
            focus_accent = QColor(paint_accent)
            focus_accent.setAlpha(225)
            painter.setPen(QPen(focus_accent, 2.0))
            painter.setBrush(Qt.NoBrush)
            focus_rect = QRectF(self.rect()).adjusted(1.5, 1.5, -2.5, -2.5)
            painter.drawRoundedRect(focus_rect, 8.0, 8.0)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._dragging = True
            self._drag_fine = bool(event.modifiers() & Qt.ShiftModifier)
            self._drag_origin_y = event.pos().y()
            self._drag_origin_value = self._value
            self.setFocus(Qt.MouseFocusReason)
            if not self._drag_fine:
                self._set_from_y(event.pos().y(), snap=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.isEnabled():
            self._drag_to(event.pos().y(), event.modifiers())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._drag_to(event.pos().y(), event.modifiers())
            self._dragging = False
            self._drag_fine = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setValue(0)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        # A console fader must never steal an ordinary page scroll. Use drag or
        # the documented keyboard shortcuts when the control has focus.
        event.ignore()

    def keyPressEvent(self, event):
        step = 1 if event.modifiers() & Qt.ShiftModifier else 5
        if event.key() in (Qt.Key_Up, Qt.Key_Right):
            self.setValue(self._value + step)
        elif event.key() in (Qt.Key_Down, Qt.Key_Left):
            self.setValue(self._value - step)
        elif event.key() == Qt.Key_PageUp:
            self.setValue(self._value + 20)
        elif event.key() == Qt.Key_PageDown:
            self.setValue(self._value - 20)
        elif event.key() == Qt.Key_Home:
            self.setValue(self._maximum)
        elif event.key() == Qt.Key_End:
            self.setValue(self._minimum)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class _PanDial(QDial):
    """Compact pan knob: -100 left, 0 center, +100 right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(-100, 100)
        self.setSingleStep(5)
        self.setPageStep(10)
        self.setNotchesVisible(True)
        self.setWrapping(False)
        self.setFixedSize(40, 40)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False
        super().setValue(0)
        self.setAccessibleDescription(_tr(
            '双击回中；方向键调 5%，Shift+方向键调 1%；普通滚轮用于滚动页面。'))
        self._update_tooltip()

    @staticmethod
    def _pan_text(value):
        if value == 0:
            return 'C'
        return ('L' if value < 0 else 'R') + str(abs(value))

    def _update_tooltip(self):
        self.setToolTip(
            self._pan_text(super().value()) + '  ·  '
            + _tr('双击回中；方向键调 5%，Shift 调 1%'))

    def setValue(self, value):
        value = max(-100, min(100, int(round(value))))
        super().setValue(value)
        self._update_tooltip()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = self.palette()
        text_color = palette.color(palette.Text)
        muted = QColor(text_color)
        muted.setAlpha(130)
        accent = palette.color(palette.Highlight)
        if not accent.isValid():
            accent = QColor('#705af8')
        paint_accent = accent if self.isEnabled() else muted
        rect = QRectF(self.rect()).adjusted(4.0, 4.0, -4.0, -4.0)
        painter.setPen(QPen(muted, 1.4))
        painter.setBrush(QColor(text_color.red(), text_color.green(), text_color.blue(), 14))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(text_color.red(), text_color.green(), text_color.blue(), 80),
                            1.0, Qt.DotLine))
        center = rect.center()
        painter.drawLine(int(center.x()), int(rect.top()), int(center.x()), int(rect.top() + 5))
        angle = math.radians(135.0 - ((super().value() + 100) / 200.0) * 270.0)
        radius = rect.width() * 0.34
        pointer_end = QRectF(
            center.x() + math.cos(angle) * radius,
            center.y() - math.sin(angle) * radius,
            0.0, 0.0,
        ).topLeft()
        painter.setPen(QPen(paint_accent, 2.2))
        painter.drawLine(center, pointer_end)
        painter.setPen(Qt.NoPen)
        painter.setBrush(paint_accent if super().value() != 0 else muted)
        painter.drawEllipse(QRectF(center.x() - 2.5, center.y() - 2.5, 5.0, 5.0))
        if self.hasFocus():
            focus_accent = QColor(paint_accent)
            focus_accent.setAlpha(225)
            painter.setPen(QPen(focus_accent, 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -2.5, -2.5))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._dragging = True
            self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._dragging and abs(super().value()) <= 2:
            self.setValue(0)

    def mouseReleaseEvent(self, event):
        if self._dragging and abs(super().value()) <= 2:
            self.setValue(0)
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setValue(0)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        # Let the enclosing scroll area receive an ordinary wheel gesture.
        event.ignore()

    def keyPressEvent(self, event):
        step = 1 if event.modifiers() & Qt.ShiftModifier else 5
        if event.key() in (Qt.Key_Up, Qt.Key_Right):
            self.setValue(super().value() + step)
        elif event.key() in (Qt.Key_Down, Qt.Key_Left):
            self.setValue(super().value() - step)
        elif event.key() == Qt.Key_PageUp:
            self.setValue(super().value() + 10)
        elif event.key() == Qt.Key_PageDown:
            self.setValue(super().value() - 10)
        elif event.key() == Qt.Key_Home:
            self.setValue(-100)
        elif event.key() == Qt.Key_End:
            self.setValue(100)
        elif event.key() == Qt.Key_0:
            self.setValue(0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class _LevelMeter(QWidget):
    """Small peak meter with a held peak line and a latchable clip lamp."""

    def __init__(self, orientation=Qt.Vertical, parent=None):
        super().__init__(parent)
        self.orientation = orientation
        if orientation == Qt.Vertical:
            self.setMinimumSize(18, 132)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            self.setMinimumSize(240, 20)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._level_db = -60.0
        self._peak_db = -60.0
        self._clip = False

    def sizeHint(self):
        return QSize(24, 166) if self.orientation == Qt.Vertical else QSize(420, 24)

    def reset(self):
        self._level_db = self._peak_db = -60.0
        self._clip = False
        self.update()

    def set_levels(self, level_db, peak_db=None, clip=None):
        try:
            level = float(level_db)
        except (TypeError, ValueError):
            level = -60.0
        if not math.isfinite(level) or level <= -143.0:
            level = -60.0
        try:
            peak = float(peak_db) if peak_db is not None else level
        except (TypeError, ValueError):
            peak = level
        if not math.isfinite(peak) or peak <= -143.0:
            peak = -60.0
        self._level_db = max(-60.0, min(0.0, level))
        self._peak_db = max(self._level_db, max(-60.0, min(0.0, peak)))
        if clip is None:
            clip = self._peak_db >= -1.0
        self._clip = bool(clip)
        self.setToolTip(f'RMS {self._level_db:.1f} dBFS · ' + _tr('峰值') + f' {self._peak_db:.1f} dBFS')
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = self.palette()
        text_color = palette.color(palette.Text)
        base = QColor(text_color)
        base.setAlpha(18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        if self.orientation == Qt.Vertical:
            rect = self.rect().adjusted(2, 4, -3, -3)
            painter.drawRoundedRect(QRectF(rect), 4, 4)
            count = 24
            gap = 1.5
            cell = max(2.0, (rect.height() - gap * (count - 1)) / count)
            channel_width = max(3.0, (rect.width() - 3.0) / 2.0)
            lit_limit = int(round(_db_to_fraction(self._level_db, -60.0, 0.0) * count))
            for channel in range(2):
                x = rect.left() + 1 + channel * (channel_width + 1.5)
                for index in range(count):
                    y = rect.bottom() - (index + 1) * cell - index * gap
                    color = self._segment_color(index / max(1, count - 1), index < lit_limit)
                    painter.setBrush(color)
                    painter.drawRoundedRect(QRectF(x, y, channel_width, cell), 1.2, 1.2)
            peak_y = rect.bottom() - _db_to_fraction(self._peak_db, -60.0, 0.0) * rect.height()
            painter.setPen(QPen(QColor('#ffffff'), 1.2))
            painter.drawLine(QRectF(rect.left(), peak_y, rect.width(), 0).topLeft(),
                             QRectF(rect.right(), peak_y, 0, 0).topLeft())
            painter.setPen(QPen(QColor(text_color.red(), text_color.green(), text_color.blue(), 90), 1.0))
            painter.drawLine(QRectF(rect.left() - 1, rect.bottom() - .78 * rect.height(), rect.width() + 2, 0).topLeft(),
                             QRectF(rect.right() + 1, rect.bottom() - .78 * rect.height(), 0, 0).topLeft())
            lamp = QRectF(rect.left() + 1, 0, rect.width() - 2, 3)
        else:
            rect = self.rect().adjusted(3, 3, -3, -4)
            painter.drawRoundedRect(QRectF(rect), 6, 6)
            count = 40
            gap = 1.5
            cell = max(2.0, (rect.width() - gap * (count - 1)) / count)
            lit_limit = int(round(_db_to_fraction(self._level_db, -60.0, 0.0) * count))
            for index in range(count):
                x = rect.left() + index * (cell + gap)
                color = self._segment_color(index / max(1, count - 1), index < lit_limit)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, rect.top() + 1, cell, rect.height() - 2), 1.5, 1.5)
            peak_x = rect.left() + _db_to_fraction(self._peak_db, -60.0, 0.0) * rect.width()
            painter.setPen(QPen(QColor('#ffffff'), 1.2))
            painter.drawLine(QRectF(peak_x, rect.top(), 0, rect.height()).topLeft(),
                             QRectF(peak_x, rect.bottom(), 0, 0).topLeft())
            lamp = QRectF(rect.right() - 3, 0, 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#ff6577') if self._clip else QColor(text_color.red(), text_color.green(), text_color.blue(), 40))
        painter.drawRoundedRect(lamp, 1.5, 1.5)

    @staticmethod
    def _segment_color(position, lit):
        if position >= .88:
            color = QColor('#ff6577')
        elif position >= .67:
            color = QColor('#f1c75b')
        else:
            color = QColor('#35d0a5')
        color.setAlpha(255 if lit else 28)
        return color


class _ResponsiveMixerConsole(QWidget):
    """Centered channel rack that only lays out active strips."""

    MAX_STRIP_WIDTH = 190
    GAP = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('mixer-console')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer = QHBoxLayout(self)
        self._outer = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._rack = QWidget()
        self._rack.setObjectName('mixer-rack')
        self._rack.setMinimumWidth(0)
        self._rack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._grid = QGridLayout(self._rack)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setHorizontalSpacing(self.GAP)
        self._grid.setVerticalSpacing(self.GAP)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        outer.addWidget(self._rack)
        self._strips = []
        self._active_vocal_count = 1
        self._columns = 0
        self._wide_rack_hint = None

    def add_strip(self, strip):
        self._strips.append(strip)
        self.reflow(force=True)

    def set_active_vocal_count(self, count):
        self._active_vocal_count = max(1, min(_MAX_VOCAL_TRACKS, int(count)))
        self.reflow(force=True)

    def active_strips(self):
        vocals = [strip for strip in self._strips
                  if strip.property('channelKind') == 'vocal']
        backing = [strip for strip in self._strips
                   if strip.property('channelKind') == 'backing']
        return vocals[:self._active_vocal_count] + backing

    def set_wide_rack(self, enabled):
        """Let the card workspace choose 1-row/2x2 independently of width."""
        self._wide_rack_hint = bool(enabled)
        self._set_compact_density(bool(enabled))
        self.reflow(force=True)

    def _set_compact_density(self, compact):
        margin = 6 if compact else 10
        self._grid.setContentsMargins(margin, margin, margin, margin)
        self._grid.setVerticalSpacing(6 if compact else 8)
        for panel in self._strips:
            layout = panel.layout()
            layout.setContentsMargins(*(7, 5, 7, 7) if compact else (9, 8, 9, 10))
            layout.setSpacing(2 if compact else 5)
            for fader in panel.findChildren(_DbFader):
                fader.setMinimumHeight(124 if compact else 146)
            for meter in panel.findChildren(_LevelMeter):
                if meter.orientation == Qt.Vertical:
                    meter.setMinimumHeight(124 if compact else 132)
            for role in panel.findChildren(QLabel, 'mixer-role'):
                role.setVisible(not compact)
        self.updateGeometry()

    def column_count(self):
        return self._columns

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def _size_rack(self, active, columns):
        available = max(0, self.width() - 20)
        inner = max(1, available - 20 - max(0, columns - 1) * self.GAP)
        strip_width = max(112, min(self.MAX_STRIP_WIDTH,
                                   inner // max(1, columns)))
        target = (columns * strip_width
                  + max(0, columns - 1) * self.GAP + 20)
        for strip in active:
            strip.setFixedWidth(strip_width)
            for title in strip.findChildren(QLabel, 'mixer-channel-title'):
                title.setVisible(strip_width >= 138)
        self._rack.setMinimumWidth(target)
        self._rack.setMaximumWidth(target)
        # Center via alignment. Padding based on the previous width becomes
        # part of minimumSizeHint and prevents a scroll pane from shrinking.
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.invalidate()

    def reflow(self, force=False):
        active = self.active_strips()
        count = max(1, len(active))
        if count == 1:
            columns = 1
        elif self._wide_rack_hint is True and self.width() >= 480:
            columns = min(4, count)
        elif self._wide_rack_hint is False:
            columns = 2
        elif self.width() < 600:
            columns = 2
        else:
            columns = min(4, count)
        if not force and columns == self._columns and self._grid.count() == count:
            self._size_rack(active, columns)
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, strip in enumerate(active):
            row, column = divmod(index, columns)
            self._grid.addWidget(strip, row, column,
                                 Qt.AlignTop | Qt.AlignHCenter)
        self._size_rack(active, columns)
        for column in range(4):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._grid.invalidate()
        self._rack.updateGeometry()
        self.updateGeometry()
        self.update()


class _ResponsiveEQCurveView(EQCurveView):
    """EQ editor that fits the full-width focus view and narrow windows."""

    COMPACT_MINIMUM_WIDTH = 264

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(self.COMPACT_MINIMUM_WIDTH, self.MINIMUM_HEIGHT)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self.COMPACT_MINIMUM_WIDTH), hint.height())

    def bypass_rect(self):
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        width = min(104.0, max(94.0, outer.width() * 0.30))
        return QRectF(outer.right() - width - 5.0, outer.top() + 3.0,
                      width, 22.0)


class _ResponsiveEffectGrid(QWidget):
    """Keep effect toggles readable as the processing card changes width."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._cells = []
        for control, text in items:
            cell = QWidget()
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            layout.addWidget(control)
            hint = label(text, 'muted')
            hint.setStyleSheet('font-size: 13px;')
            layout.addWidget(hint)
            self._cells.append(cell)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        self._columns = 0
        self.reflow(force=True)

    def column_count(self):
        return self._columns

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self, force=False):
        columns = 3 if self.width() >= 650 else (2 if self.width() >= 400 else 1)
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for index, cell in enumerate(self._cells):
            row, column = divmod(index, columns)
            self.grid.addWidget(cell, row, column)
        for column in range(3):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self.grid.invalidate()
        self.updateGeometry()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())


class _ElidedLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text or '')
        self.setToolTip(self._full_text)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full_text, Qt.ElideMiddle,
                                   max(20, self.contentsRect().width()))
        QLabel.setText(self, shown)


def _application_root():
    """Locate the app tree containing component manifest and packaged installer."""
    try:
        from common import ROOT
        candidates = (Path(ROOT), Path(ROOT).parent)
    except Exception:
        candidates = (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1])
    for candidate in candidates:
        if ((candidate / 'components/pedalboard.json').is_file()
                and (candidate / 'tools/install_pedalboard_component.py').is_file()):
            return candidate.resolve()
    return None


def _component_manifest():
    root = _application_root()
    if root is None:
        return root, None, None
    manifest = root / 'components/pedalboard.json'
    try:
        value = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return root, None, None
    return root, value, root / 'tools/install_pedalboard_component.py'


def _optional_packages(root, spec, packages=None):
    if packages:
        path = Path(packages).resolve()
    elif spec:
        try:
            path = (root / spec['install']['root']
                    / spec['install']['packages']).resolve()
            if not (root / spec['install']['root']).resolve().is_relative_to(root.resolve()):
                return None
        except (KeyError, TypeError, OSError, ValueError):
            return None
    else:
        path = None
    return path if path and path.is_dir() else None


def _child_python():
    """Use a console Python for clean subprocesses, with an explicit fallback."""
    executable = Path(sys.executable).resolve()
    sibling = executable.with_name('python.exe')
    if sibling.is_file():
        return sibling
    return executable


def _worker_environment(packages=None):
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    paths = []
    if packages:
        paths.append(str(Path(packages).resolve()))
    app_root = _application_root()
    if app_root:
        paths.append(str(app_root))
        source = app_root / 'src'
        if source.is_dir():
            paths.append(str(source))
    paths.append(str(Path(__file__).resolve().parent))
    paths.extend(str(path) for path in sys.path if path)
    previous = env.get('PYTHONPATH')
    if previous:
        paths.extend(part for part in previous.split(os.pathsep) if part)
    ordered = list(dict.fromkeys(paths))
    # Embedded Python ignores PYTHONPATH because python312._pth is present.
    # The child bootstrap explicitly inserts this custom list before imports.
    env['VWA_MIXDOWN_EXTRA_PATHS'] = os.pathsep.join(ordered)
    env['PYTHONPATH'] = os.pathsep.join(ordered)
    return env


def _activate_pedalboard(packages=None, check=None):
    """Verify Pedalboard in a clean Python process and remember its optional path."""
    global _OPTIONAL_PACKAGES
    if packages:
        path = Path(packages).resolve()
        if not path.is_dir():
            return False, '已安装的 Pedalboard 组件目录不存在。'
        _OPTIONAL_PACKAGES = str(path)
    _check_cancel(check)
    fake = sys.modules.get('pedalboard')
    if fake is not None and not getattr(fake, '__file__', None):
        version = str(getattr(fake, '__version__', ''))
        if version != '0.9.26':
            return False, 'Pedalboard 版本不符：需要 0.9.26，当前为 ' + (version or '未知') + '。'
        return True, version
    code = (
        "import os,sys; "
        "[sys.path.insert(0,p) for p in reversed(os.environ.get('VWA_MIXDOWN_EXTRA_PATHS','').split(os.pathsep)) if p]; "
        "import pedalboard; "
        "v=str(getattr(pedalboard,'__version__','')); "
        "assert v=='0.9.26', '版本不符: '+v; "
        "assert all(hasattr(pedalboard,n) for n in ('Pedalboard','HighpassFilter','PeakFilter','Compressor','Reverb')), '组件不完整'; "
        "print(v)"
    )
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(
            [str(_child_python()), '-B', '-c', code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=_worker_environment(_OPTIONAL_PACKAGES),
            creationflags=creationflags,
        )
    except OSError as error:
        return False, 'Pedalboard 组件检查失败：' + str(error)
    deadline = time.monotonic() + 20.0
    stdout = stderr = ''
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.10)
                break
            except subprocess.TimeoutExpired:
                _check_cancel(check)
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    return False, 'Pedalboard 组件检查失败：检查超时。'
    except BaseException:
        _terminate_process(process)
        raise
    if process.returncode:
        detail = (stderr or '').strip().splitlines()
        return False, 'Pedalboard 组件检查失败：' + (detail[-1] if detail else '未知错误')
    return True, (stdout or '').strip() or '0.9.26'


def _run_component_installer(action, root, installer, check, force=False):
    """Run the packaged component installer without a shell or PowerShell."""
    if not installer or not Path(installer).is_file():
        raise FileNotFoundError('混音组件安装器未随当前版本提供。')
    argv = [str(_child_python()), '-B', str(installer), action, '--app-root', str(root)]
    if action == 'install':
        argv.extend(['--python', str(_child_python())])
    if action == 'install' and force:
        argv.append('--force')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        while process.poll() is None:
            _check_cancel(check)
            time.sleep(0.10)
        stdout, stderr = process.communicate()
    finally:
        if process.poll() is None:
            try:
                from process_control import terminate_tree
                terminate_tree(process)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait(timeout=5)
    if process.returncode:
        detail = (stderr or stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else '混音组件安装器返回失败。')
    try:
        value = json.loads(stdout)
    except ValueError:
        raise ValueError('混音组件安装器没有返回可读状态。')
    if not isinstance(value, dict):
        raise ValueError('混音组件安装器返回状态无效。')
    return value


class MixdownTask(QThread):
    """One owned render/register operation; the worker owns all cleanup."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    progressChanged = pyqtSignal(object)

    def __init__(self, function, parent=None, with_progress=False):
        super().__init__(parent)
        self.function = function
        self.with_progress = bool(with_progress)
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def _emit_progress(self, event):
        if isinstance(event, dict) and event.get('event', 'progress') == 'progress':
            self.progressChanged.emit(dict(event))

    def run(self):
        try:
            if self.with_progress:
                result = self.function(self.cancel_event.is_set, self._emit_progress)
            else:
                result = self.function(self.cancel_event.is_set)
        except _Cancelled:
            self.cancelled.emit()
        except Exception as error:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


def _check_cancel(check):
    if check and check():
        raise _Cancelled('混音操作已取消。')


_RENDER_WORKER = r"""
import inspect
import json
import os
from pathlib import Path
import sys

for extra in reversed(os.environ.get('VWA_MIXDOWN_EXTRA_PATHS', '').split(os.pathsep)):
    if extra:
        sys.path.insert(0, extra)

request_path = Path(sys.argv[1])
cancel_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding='utf-8'))

def cancel():
    return cancel_path.exists()

def emit(event):
    value = dict(event)
    value.setdefault('event', 'progress')
    print(json.dumps(value, ensure_ascii=False), flush=True)

try:
    from audio_mixdown import render
    parameters = inspect.signature(render).parameters
    if 'progress' in parameters:
        result = render(request, cancel=cancel, progress=emit)
    else:
        result = render(request, cancel=cancel)
    if not isinstance(result, dict):
        raise TypeError('render did not return a dictionary')
    print(json.dumps({'event': 'done', 'fraction': 1.0, 'report': result},
                     ensure_ascii=False), flush=True)
except BaseException as error:
    print(type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
    raise SystemExit(1)
"""


def _injected_backend(name):
    """Only explicit module doubles may bypass the isolated native worker."""
    module = sys.modules.get(name)
    return module if module is not None and not getattr(module, '__file__', None) else None


def _render_once(request, check, progress=None):
    """Run the shared renderer and forward progress without loading native Qt."""
    _check_cancel(check)
    injected = _injected_backend('audio_mixdown')
    if injected is not None:
        import inspect
        from audio_mixdown import render
        kwargs = {'cancel': check}
        if progress is not None and 'progress' in inspect.signature(render).parameters:
            kwargs['progress'] = progress
        report = render(dict(request), **kwargs)
        _check_cancel(check)
        if not isinstance(report, dict):
            raise ValueError('混音后端没有返回结果字典。')
        return dict(report)

    root = Path(tempfile.mkdtemp(prefix='vwa-audio-render-'))
    request_path = root / 'request.json'
    cancel_path = root / 'cancel.flag'
    stderr_path = root / 'stderr.txt'
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    process = None
    reader = None
    output_lines = queue.Queue()
    try:
        with stderr_path.open('wb') as stderr:
            process = subprocess.Popen(
                [str(_child_python()), '-B', '-u', '-c', _RENDER_WORKER,
                 str(request_path), str(cancel_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
                env=_worker_environment(_OPTIONAL_PACKAGES),
                cwd=str(_application_root() or Path(__file__).resolve().parents[1]),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                text=True, encoding='utf-8', errors='replace', bufsize=1)

            def read_output():
                try:
                    for line in process.stdout:
                        output_lines.put(line)
                finally:
                    try:
                        process.stdout.close()
                    except Exception:
                        pass

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            final_report = None
            while True:
                if check and check():
                    cancel_path.touch()
                    grace = time.monotonic() + 2.0
                    while process.poll() is None and time.monotonic() < grace:
                        time.sleep(0.05)
                    if process.poll() is None:
                        try:
                            from process_control import terminate_tree
                            terminate_tree(process)
                        except Exception:
                            process.kill()
                    raise _Cancelled('混音操作已取消。')
                try:
                    line = output_lines.get(timeout=0.05)
                except queue.Empty:
                    line = None
                if line:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        if event.get('event') == 'progress':
                            if progress is not None:
                                try:
                                    progress(event)
                                except Exception:
                                    pass
                        elif event.get('event') == 'done':
                            payload = event.get('report')
                            if isinstance(payload, dict):
                                final_report = payload
                        elif 'event' not in event:
                            final_report = event
                if (process.poll() is not None and reader is not None
                        and not reader.is_alive() and output_lines.empty()):
                    break
        stderr_text = stderr_path.read_text(encoding='utf-8', errors='replace').strip()
        if check and check():
            raise _Cancelled('混音操作已取消。')
        if process.returncode:
            detail = stderr_text.splitlines()[-1] if stderr_text else '混音渲染子进程失败。'
            raise RuntimeError(detail)
        if final_report is None:
            raise ValueError('混音后端没有返回可读结果。')
        return dict(final_report)
    finally:
        if process is not None and process.poll() is None:
            try:
                from process_control import terminate_tree
                terminate_tree(process)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
        if reader is not None:
            reader.join(timeout=1.0)
        shutil.rmtree(root, ignore_errors=True)


def _source_signature(path):
    info = Path(path).stat()
    return info.st_size, info.st_mtime_ns


def _file_sha256(path, check=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            _check_cancel(check)
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _normal_path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _validate_render_source_hashes(report, sources):
    """Require the backend to identify every input it actually consumed."""
    hashes = report.get('source_sha256')
    if not isinstance(hashes, dict):
        raise ValueError('混音后端没有返回源音频 SHA-256 指纹。')
    normalized = {_normal_path_key(path): value for path, value in hashes.items()}
    for source in sources:
        value = normalized.get(_normal_path_key(source))
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError('混音后端没有返回完整源音频指纹：' + Path(source).name)
        try:
            int(value, 16)
        except ValueError:
            raise ValueError('混音后端返回的源音频指纹无效：' + Path(source).name) from None
    return normalized


def _validate_render(report, output):
    output = Path(output).resolve()
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError('混音后端没有生成有效的 WAV 文件。')
    returned = report.get('output_path')
    if returned and Path(returned).resolve() != output:
        raise ValueError('混音后端返回了预期之外的文件路径。')
    report['output_path'] = str(output)
    return report


def _publish_without_overwrite(temporary, destination):
    """Atomically give the finished WAV its requested name; never replace a file."""
    temporary, destination = Path(temporary), Path(destination)
    if destination.exists():
        raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    except OSError as error:
        # Windows os.rename does not replace an existing destination. Hard links
        # can be unavailable on a mounted filesystem, so this is a narrow fallback.
        if os.name != 'nt':
            raise OSError('无法以不覆盖方式发布混音文件：' + str(error))
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    else:
        try:
            temporary.unlink()
        except OSError:
            # The destination is already durable; a hidden staging link left on
            # an unusual filesystem must not turn a successful publish into failure.
            pass


def _same_path(left, right):
    if left is None or right is None:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _discard_preview_directory(path):
    if not path:
        return
    try:
        candidate = Path(path).absolute()
        root = Path(tempfile.gettempdir()).resolve()
        if (candidate.parent.resolve() != root
                or not (candidate.name.startswith(_PREVIEW_PREFIX)
                        or candidate.name.startswith('vwa-audio-mixdown-'))):
            return
        if candidate.is_symlink() or candidate.is_junction():
            return
        shutil.rmtree(candidate, ignore_errors=True)
    except (OSError, ValueError):
        pass


def _task_source(job, names):
    if not job:
        return None
    job = Path(job)
    for name in names:
        path = job / name
        if path.is_file():
            return path.resolve()
    return None


def _suggested_output(job):
    directory = (Path(job) / 'rendered') if job else Path.cwd()
    stem = 'simple-mixdown-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    path = directory / (stem + '.wav')
    index = 2
    while path.exists():
        path = directory / (stem + '-' + str(index) + '.wav')
        index += 1
    return path


def _report_detail(report):
    parts = []
    try:
        parts.append(_tr('时长：{0:.3f} 秒').format(float(report['duration_seconds'])))
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('时长：后端未报告'))
    try:
        peak = float(report['peak_dbfs'])
        value = _tr('静音') if not math.isfinite(peak) or peak <= -143.0 else '{:.2f} dBFS'.format(peak)
        parts.append(_tr('峰值：') + value)
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('峰值：后端未报告'))
    return '；'.join(parts)


class AudioMixdownDialog(QDialog):
    """Mix up to three dry vocal WAVs and one backing without replacing sources."""

    def __init__(self, owner, job, parent=None, embedded=False, *, preset_library=None):
        if embedded:
            super().__init__(parent, Qt.Widget)
            self.setWindowFlags(Qt.Widget)
        else:
            super().__init__(parent)
        self.owner = owner
        self._mix_preset_library = preset_library
        self._mix_preset_undo = None
        self.embedded = bool(embedded)
        self._job = Path(job).resolve() if job else None
        self._ui_ready = False
        self._auto_instrumental = None
        self.worker = None
        self._busy_kind = None
        self._close_pending = False
        self._completion_action = None
        self._pending_navigation = False
        self._syncing = False
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._player = None
        self._player_key = 'after'
        self._player_seeking = False
        self._player_seek_serial = 0
        self._pending_player_seek = None
        self._last_player_seek = None
        self._player_seek_timer = QTimer(self)
        self._player_seek_timer.setSingleShot(True)
        self._player_seek_timer.setInterval(_PLAYER_SEEK_COALESCE_MS)
        self._player_seek_timer.timeout.connect(self._commit_queued_player_seek)
        self._player_duration_ms = 0
        self.navigate_requested = False
        self.video_requested = False
        self.registered = False
        self._support_ready = False
        self._support_error = ''
        self._component_packages = None
        self._component_spec = None
        self._component_root = None
        self._component_installer = None
        self._support_worker = None
        self._support_checking = False
        self._audition_service = None
        self._audition_closing = False
        self._audition_close_thread = None
        self._audition_cleanup_complete = False
        self._close_finalize_pending = False
        self._pending_done_result = None
        self._eq_sample_rate_probe = None
        # Retain reports by actual media path while a replacement is loading;
        # the displayed A/B analysis must follow the source being heard.
        self._native_preview_player = None
        self._live_measurement_frame = None
        self._native_dll_path = None
        self._native_source_key = None
        self._native_requested_key = 'after'
        self._edc_playback_position_ms = 0.0
        self._preview_dirty = True
        self._preview_play_intent = False
        self._preview_cache_directory = None
        self._preview_resume_seconds = None
        self._preview_segment_start_seconds = 0.0
        self._pending_player_position_ms = 0
        self._player_loop = False
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(90)
        self._preview_debounce.timeout.connect(self._start_live_preview)
        self._close_wait_timer = QTimer(self)
        self._close_wait_timer.setSingleShot(True)
        self._close_wait_timer.setInterval(25)
        self._close_wait_timer.timeout.connect(self._try_finish_pending_close)
        self._audition_poll_timer = QTimer(self)
        self._audition_poll_timer.setInterval(33)
        self._audition_poll_timer.timeout.connect(self._poll_audition_service)
        self._audition_close_watch = QTimer(self)
        self._audition_close_watch.setInterval(33)
        self._audition_close_watch.timeout.connect(self._finish_pending_dialog_close)
        self._eq_bands = default_bands()
        for band in self._eq_bands:
            if band['type'] == 'highpass':
                band['freq_hz'] = 80.0
            elif band['type'] == 'bell':
                band['gain_db'] = 1.0
                band['q'] = 0.75
        self._active_preset_key = None

        self.setObjectName(CONTROL_NAMES['dialog'])
        self.setWindowTitle(_tr('制作简易混音'))
        self.setModal(not self.embedded)
        self.setMinimumSize(640, 560)
        self.resize(920, 840)
        self.setSizeGripEnabled(not self.embedded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        self._main_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(26, 22, 26, 24)
        body.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._heading = label(_tr('简易混音'), 'heading')
        body.addWidget(self._heading)
        self._intro = label(
            _tr('先选声音，再调平衡；基础流程不用懂效果参数。需要细调时再展开高级处理。源文件始终只读。'),
            'muted')
        body.addWidget(self._intro)

        steps_widget = QWidget()
        self._steps = steps_widget
        steps = FlowLayout(steps_widget, spacing=8)
        for index, title in enumerate((
                _tr('选择 1–3 路歌声'), _tr('逐路平衡'), _tr('人声处理'),
                _tr('试听'), _tr('导出登记'))):
            if index:
                steps.addWidget(label('›', 'muted'))
            step = label(_tr('{0}  {1}').format(index + 1, title), 'muted')
            step.setStyleSheet('font-weight: 600;')
            steps.addWidget(step)
        body.addWidget(steps_widget)

        self.support_panel = QWidget()
        self.support_panel.setObjectName(CONTROL_NAMES['support_panel'])
        support_outer = QVBoxLayout(self.support_panel)
        support_outer.setContentsMargins(0, 0, 0, 0)
        support_surface = QWidget()
        support_surface.setObjectName('step-surface')
        support_outer.addWidget(support_surface)
        support_layout = QVBoxLayout(support_surface)
        support_layout.setContentsMargins(16, 13, 16, 13)
        support_layout.setSpacing(8)
        support_title = label(_tr('混音组件'), 'muted')
        support_title.setStyleSheet('font-weight: 600;')
        self.support_state = label(_tr('正在检查混音组件…'), 'muted')
        self.support_state.setObjectName(CONTROL_NAMES['support_state'])
        self.install_dependency_button = button(
            _tr('安装混音组件（约 3.5 MiB）'), self.install_dependency, 'primary')
        self.install_dependency_button.setObjectName(CONTROL_NAMES['install_dependency'])
        self.install_dependency_button.setAccessibleName(_tr('一键安装混音组件'))
        self.install_dependency_button.setMaximumWidth(420)
        support_layout.addWidget(support_title)
        support_layout.addWidget(self.support_state)
        support_layout.addWidget(self.install_dependency_button, 0, Qt.AlignLeft)
        body.addWidget(self.support_panel)

        mixer_card, mixer_body = card(
            _tr('混音台'),
            _tr('声音像实体调音台一样排开：每路一条通道、一个 dB 推子和一块峰值表。先调平衡，再决定要不要加处理。'),
            '1')
        self.fields = {}
        self._choose_buttons = {}
        self.source_states = {}
        self.vocal_tracks = []
        self._channel_meters = {}

        console = _ResponsiveMixerConsole()
        self.mixer_console = console
        preset_items = [('custom', _tr('自定义'))] + [
            (item['key'], _tr(item['label'])) for item in preset_catalog()
        ]
        self.preset_bar = PresetBar(preset_items, current_key='custom', lock_mix=True)
        self.preset_bar.presetRequested.connect(self.apply_mix_preset)
        self.preset_bar.setToolTip(_tr('起点预设，需按素材微调。'))
        mixer_body.addWidget(self.preset_bar)

        preset_actions = QWidget()
        preset_actions.setObjectName('mixdownLibraryActions')
        preset_actions_layout = FlowLayout(preset_actions, spacing=8)
        self.save_mix_preset_button = button(_tr('保存当前为预设…'), self.save_mix_preset)
        self.save_mix_preset_button.setObjectName('mixdownSavePresetButton')
        self.choose_mix_preset_button = button(_tr('从本机库选择…'), self.choose_mix_preset)
        self.choose_mix_preset_button.setObjectName('mixdownChoosePresetButton')
        self.undo_mix_preset_button = button(_tr('撤销预设应用'), self.undo_mix_preset)
        self.undo_mix_preset_button.setObjectName('mixdownUndoPresetButton')
        for control in (self.save_mix_preset_button, self.choose_mix_preset_button,
                        self.undo_mix_preset_button):
            preset_actions_layout.addWidget(control)
        mixer_body.addWidget(preset_actions)

        def compact_spin(spin, width=None):
            if width is None:
                spin.setMinimumWidth(0)
                spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            else:
                spin.setFixedWidth(width)
            spin.setAlignment(Qt.AlignCenter)
            spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
            spin.setKeyboardTracking(False)
            return spin

        def channel_header(panel, badge_text, title_text, accent, remove=None):
            stripe = QWidget()
            stripe.setFixedHeight(4)
            stripe.setStyleSheet(
                'background: ' + accent + '; border: 0; border-radius: 2px;')
            panel_layout = panel.layout()
            panel_layout.addWidget(stripe)
            heading = QHBoxLayout()
            heading.setSpacing(4)
            badge = QLabel(badge_text)
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedSize(28, 24)
            badge.setStyleSheet(
                'color: #ffffff; background: ' + accent + '; border: 0; '
                'border-radius: 7px; font-size: 12px; font-weight: 700;')
            title = label(title_text, 'muted')
            title.setObjectName('mixer-channel-title')
            title.setStyleSheet('font-weight: 650; font-size: 15px;')
            heading.addWidget(badge)
            heading.addWidget(title, 1)
            if remove is not None:
                heading.addWidget(remove)
            panel_layout.addLayout(heading)
            return badge

        def make_channel_shell(kind, number):
            panel = QFrame()
            panel.setObjectName('mixer-channel')
            panel.setProperty('channelKind', kind)
            panel.setMinimumWidth(112)
            panel.setMaximumWidth(210)
            panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            box = QVBoxLayout(panel)
            box.setContentsMargins(9, 8, 9, 10)
            box.setSpacing(5)
            return panel

        def mix_controls(kind, number):
            prefix = ('mixdownVocal' + str(number)
                      if kind == 'vocal' else 'mixdownInstrumental')
            channel_name = (_tr('第 {0} 路歌声').format(number)
                            if kind == 'vocal' else _tr('伴奏'))
            row = QHBoxLayout()
            row.setContentsMargins(0, 1, 0, 1)
            row.setSpacing(3)
            mute = QPushButton('M')
            mute.setObjectName(prefix + 'Mute')
            mute.setAccessibleName(_tr('{0}，静音').format(channel_name))
            mute.setAccessibleDescription(_tr('切换静音；静音优先于独奏。'))
            mute.setToolTip(_tr('静音'))
            mute.setCheckable(True)
            mute.setProperty('mixerToggle', 'mute')
            mute.setFixedSize(26, 24)
            mute.setFocusPolicy(Qt.StrongFocus)
            mute.setAutoDefault(False)
            solo = QPushButton('S')
            solo.setObjectName(prefix + 'Solo')
            solo.setAccessibleName(_tr('{0}，独奏').format(channel_name))
            solo.setAccessibleDescription(_tr('切换独奏；存在独奏时其他轨会被过滤。'))
            solo.setToolTip(_tr('独奏'))
            solo.setCheckable(True)
            solo.setProperty('mixerToggle', 'solo')
            solo.setFixedSize(26, 24)
            solo.setFocusPolicy(Qt.StrongFocus)
            solo.setAutoDefault(False)
            pan = _PanDial()
            pan.setObjectName(prefix + 'Pan')
            pan.setAccessibleName(_tr('{0}，声像').format(channel_name))
            pan.setAccessibleDescription(_tr('双击回中；Shift 再按方向键或滚轮可微调。'))
            pan_value = QLabel('C')
            pan_value.setObjectName('mixer-pan-value')
            pan_value.setAccessibleName(_tr('{0}，声像值').format(channel_name))
            pan_value.setAlignment(Qt.AlignCenter)
            pan_value.setFixedWidth(28)
            pan.valueChanged.connect(
                lambda value, shown=pan_value: shown.setText(_PanDial._pan_text(value)))
            mute.toggled.connect(self.parameters_changed)
            solo.toggled.connect(self.parameters_changed)
            pan.valueChanged.connect(self.parameters_changed)
            row.addWidget(mute)
            row.addWidget(solo)
            row.addStretch(1)
            row.addWidget(pan)
            row.addWidget(pan_value)
            return row, mute, solo, pan, pan_value

        for index in range(_MAX_VOCAL_TRACKS):
            number = index + 1
            suffix = '' if index == 0 else '_' + str(number)
            field_key = 'vocal' if index == 0 else 'vocal_' + str(number)
            panel = make_channel_shell('vocal', number)
            remove = button(_tr('移除'), lambda checked=False, index=index:
                            self.remove_vocal_track(index))
            remove.setObjectName('mixdownRemoveVocal' + str(number))
            remove.setAccessibleName(_tr('移除第 {0} 路歌声').format(number))
            remove.setProperty('mixerAction', 'remove')
            remove.setText('×')
            remove.setToolTip(_tr('移除第 {0} 路歌声').format(number))
            remove.setFixedSize(26, 26)
            remove.hide()
            channel_header(panel, 'V' + str(number), _tr('歌声 {0}').format(number),
                           ('#705af8', '#ef6f9a', '#38bfa1')[index], remove)

            role = _ElidedLabel('LEAD' if index == 0 else 'HARMONY')
            role.setObjectName('mixer-role')
            role.setStyleSheet('font-size: 10px; font-weight: 650; letter-spacing: 1px;')
            panel.layout().addWidget(role)
            controls, mute, solo, pan, pan_value = mix_controls('vocal', number)
            panel.layout().addLayout(controls)

            field = QLineEdit()
            field.setObjectName(CONTROL_NAMES.get(
                'vocal_path' + suffix, 'mixdownVocal' + str(number) + 'Path'))
            field.setAccessibleName(_tr('第 {0} 路歌声干声').format(number))
            field.setPlaceholderText(
                _tr('声库或编辑器导出的 WAV') if index == 0
                else _tr('可选：另一路歌声 WAV'))
            field.setClearButtonEnabled(True)
            field.hide()

            file_label = _ElidedLabel(_tr('尚未选择'))
            file_label.setObjectName('mixer-filename')
            file_label.setAlignment(Qt.AlignCenter)
            panel.layout().addWidget(file_label)

            choose = button(_tr('选择…'),
                            lambda checked=False, key=field_key: self._choose_path(key))
            choose.setObjectName(CONTROL_NAMES.get(
                'browse_vocal' + suffix, 'mixdownBrowseVocal' + str(number)))
            choose.setAccessibleName(_tr('选择第 {0} 路歌声').format(number))
            choose.setProperty('mixerAction', 'choose')
            panel.layout().addWidget(choose)

            visual = QHBoxLayout()
            visual.setContentsMargins(0, 2, 0, 0)
            visual.setSpacing(2)
            visual.addStretch(1)
            fader = _DbFader(-240, 120, 0)
            fader.setObjectName(CONTROL_NAMES.get(
                'vocal_slider' + suffix, 'mixdownVocal' + str(number) + 'LevelSlider'))
            fader.setAccessibleName(_tr('第 {0} 路歌声音量推子').format(number))
            meter = _LevelMeter(Qt.Vertical)
            meter.setObjectName('mixdownVocal{0}Meter'.format(number))
            meter.setAccessibleName(_tr('第 {0} 路歌声峰值表').format(number))
            visual.addWidget(fader, 0, Qt.AlignHCenter)
            visual.addWidget(meter, 0, Qt.AlignHCenter)
            visual.addStretch(1)
            panel.layout().addLayout(visual)

            gain = compact_spin(QDoubleSpinBox(), 90)
            gain.setObjectName(CONTROL_NAMES.get(
                'vocal_db' + suffix, 'mixdownVocal' + str(number) + 'Db'))
            gain.setAccessibleName(_tr('第 {0} 路歌声干声音量').format(number))
            gain.setRange(-24.0, 12.0)
            gain.setDecimals(1)
            gain.setSingleStep(0.5)
            gain.setSuffix(' dB')
            gain.setValue(0.0)
            panel.layout().addWidget(gain, 0, Qt.AlignHCenter)
            fader.valueChanged.connect(lambda value, spin=gain: spin.setValue(value / 10.0))
            gain.valueChanged.connect(lambda value, control=fader: control.setValue(int(round(value * 10.0))))

            offset = compact_spin(QDoubleSpinBox())
            offset.setObjectName(CONTROL_NAMES.get(
                'offset' + suffix, 'mixdownOffset' + str(number)))
            offset.setAccessibleName(_tr('第 {0} 路歌声位置偏移秒数').format(number))
            offset.setRange(-30.0, 30.0)
            offset.setDecimals(3)
            offset.setSingleStep(0.01)
            offset.setSuffix(' 秒')
            offset.setValue(0.0)
            offset_row = QHBoxLayout()
            offset_row.setSpacing(4)
            offset_row.addWidget(label(_tr('位置'), 'muted'))
            offset_row.addWidget(offset, 1)
            panel.layout().addLayout(offset_row)

            state = label(_tr('可选 · 未填写时不参与混音') if index else _tr('尚未选择'), 'muted')
            state.setObjectName(CONTROL_NAMES.get(
                'vocal_state' + suffix, 'mixdownVocal' + str(number) + 'State'))
            state.setAlignment(Qt.AlignCenter)
            state.setStyleSheet('font-size: 11px;')
            panel.layout().addWidget(state)

            console.add_strip(panel)
            self.fields[field_key] = field
            self._choose_buttons[field_key] = choose
            self.source_states[field_key] = state
            self._channel_meters[field_key] = meter
            self.vocal_tracks.append({
                'index': index, 'field_key': field_key, 'panel': panel,
                'path': field, 'choose': choose, 'state': state,
                'file_label': file_label,
                'remove': remove, 'gain_db': gain, 'slider': fader,
                'offset': offset, 'level_panel': panel, 'meter': meter,
                'mute': mute, 'solo': solo, 'pan': pan, 'pan_value': pan_value,
                'reverb_send': 1.0,
            })
            if index == 0:
                self.vocal_db = gain
                self.vocal_slider = fader
                self.offset = offset

        backing_panel = make_channel_shell('backing', 0)
        channel_header(backing_panel, 'B', _tr('伴奏'), '#e6a33f')
        backing_role = _ElidedLabel('BACKING')
        backing_role.setObjectName('mixer-role')
        backing_role.setStyleSheet('font-size: 10px; font-weight: 650; letter-spacing: 1px;')
        backing_panel.layout().addWidget(backing_role)
        backing_controls, self.instrumental_mute, self.instrumental_solo, self.instrumental_pan, self.instrumental_pan_value = mix_controls('instrumental', 0)
        backing_panel.layout().addLayout(backing_controls)

        backing_field = QLineEdit()
        backing_field.setObjectName(CONTROL_NAMES['instrumental_path'])
        backing_field.setAccessibleName('伴奏')
        backing_field.setPlaceholderText(_tr('WAV、FLAC、MP3、M4A 等常见音频'))
        backing_field.setClearButtonEnabled(True)
        backing_field.hide()
        backing_file_label = _ElidedLabel(_tr('尚未选择'))
        backing_file_label.setObjectName('mixer-filename')
        backing_file_label.setAlignment(Qt.AlignCenter)
        backing_panel.layout().addWidget(backing_file_label)
        backing_choose = button(_tr('选择…'),
                                lambda checked=False: self._choose_path('instrumental'))
        backing_choose.setObjectName(CONTROL_NAMES['browse_instrumental'])
        backing_choose.setAccessibleName(_tr('选择伴奏'))
        backing_choose.setProperty('mixerAction', 'choose')
        backing_panel.layout().addWidget(backing_choose)

        backing_visual = QHBoxLayout()
        backing_visual.setContentsMargins(0, 2, 0, 0)
        backing_visual.setSpacing(2)
        backing_visual.addStretch(1)
        self.instrumental_slider = _DbFader(-240, 120, 0)
        self.instrumental_slider.setObjectName(CONTROL_NAMES['instrumental_slider'])
        self.instrumental_slider.setAccessibleName(_tr('伴奏音量推子'))
        self.instrumental_meter = _LevelMeter(Qt.Vertical)
        self.instrumental_meter.setObjectName('mixdownInstrumentalMeter')
        self.instrumental_meter.setAccessibleName(_tr('伴奏峰值表'))
        backing_visual.addWidget(self.instrumental_slider, 0, Qt.AlignHCenter)
        backing_visual.addWidget(self.instrumental_meter, 0, Qt.AlignHCenter)
        backing_visual.addStretch(1)
        backing_panel.layout().addLayout(backing_visual)

        self.instrumental_db = compact_spin(QDoubleSpinBox(), 90)
        self.instrumental_db.setObjectName(CONTROL_NAMES['instrumental_db'])
        self.instrumental_db.setAccessibleName(_tr('伴奏音量'))
        self.instrumental_db.setRange(-24.0, 12.0)
        self.instrumental_db.setDecimals(1)
        self.instrumental_db.setSingleStep(0.5)
        self.instrumental_db.setSuffix(' dB')
        self.instrumental_db.setValue(0.0)
        backing_panel.layout().addWidget(self.instrumental_db, 0, Qt.AlignHCenter)
        self.instrumental_slider.valueChanged.connect(
            lambda value: self.instrumental_db.setValue(value / 10.0))
        self.instrumental_db.valueChanged.connect(
            lambda value: self.instrumental_slider.setValue(int(round(value * 10.0))))

        backing_timing = label(_tr('随歌曲起点'), 'muted')
        backing_timing.setAlignment(Qt.AlignCenter)
        backing_timing.setStyleSheet('font-size: 11px;')
        backing_panel.layout().addWidget(backing_timing)
        backing_state = label(_tr('尚未选择'), 'muted')
        backing_state.setObjectName(CONTROL_NAMES['instrumental_state'])
        backing_state.setAlignment(Qt.AlignCenter)
        backing_state.setStyleSheet('font-size: 11px;')
        backing_panel.layout().addWidget(backing_state)
        console.add_strip(backing_panel)
        self.backing_panel = backing_panel

        self.fields['instrumental'] = backing_field
        self._choose_buttons['instrumental'] = backing_choose
        self.source_states['instrumental'] = backing_state
        self._channel_meters['instrumental'] = self.instrumental_meter
        self.instrumental_file_label = backing_file_label

        mixer_body.addWidget(console)
        console_footer = QHBoxLayout()
        console_footer.setSpacing(10)
        self.add_vocal_button = button(_tr('添加第 2 路歌声'), self.add_vocal_track)
        self.add_vocal_button.setObjectName(CONTROL_NAMES['add_vocal_track'])
        self.add_vocal_button.setAccessibleName(_tr('添加另一路歌声'))
        console_footer.addWidget(self.add_vocal_button)
        console_footer.addStretch(1)
        self._mix_summary = label(_tr('1 路歌声 · 1 路伴奏 · 共享人声处理'), 'muted')
        console_footer.addWidget(self._mix_summary)
        mixer_body.addLayout(console_footer)
        self._loudness_summary = label(_tr('峰值对比：等待音频数据'), 'muted')
        self._loudness_summary.setObjectName('mixdownLoudnessSummary')
        self._loudness_summary.setAccessibleName(_tr('各轨峰值电平对比'))
        self._loudness_summary.setStyleSheet('font-size: 12px; font-weight: 600;')
        mixer_body.addWidget(self._loudness_summary)
        self.send_controls = PerTrackSendControls([
            {'label': 'V1', 'reverb_send': 1.0},
        ])
        self.send_controls.sendChanged.connect(self._reverb_send_changed)
        self.send_controls_fold = fold(
            _tr('每轨混响发送'), self.send_controls, mixer_body, expanded=False)
        self.send_controls_fold.setAccessibleName(_tr('展开每轨混响发送'))
        self._mixer_help = label(
            _tr('双击推子回零，双击声像回中；按住 Shift 拖动或按方向键可微调。滚轮只在控件聚焦时调值。'),
            'muted')
        self._mixer_help.setToolTip(self._mixer_help.text())
        mixer_body.addWidget(self._mixer_help)

        bus = QWidget()
        bus.setObjectName('mixer-bus')
        bus_layout = QHBoxLayout(bus)
        bus_layout.setContentsMargins(12, 7, 12, 7)
        bus_layout.setSpacing(10)
        bus_title = QLabel('MIX BUS')
        bus_title.setStyleSheet('font-size: 11px; font-weight: 700; letter-spacing: 1px;')
        bus_layout.addWidget(bus_title)
        self.bus_meter = _LevelMeter(Qt.Horizontal)
        self.bus_meter.setMinimumWidth(96)
        self.bus_meter.setObjectName('mixdownBusMeter')
        self.bus_meter.setAccessibleName(_tr('混音总线峰值表'))
        bus_layout.addWidget(self.bus_meter, 1)
        self.bus_peak_value = QLabel('—  dBFS')
        self.bus_peak_value.setMinimumWidth(86)
        self.bus_peak_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bus_layout.addWidget(self.bus_peak_value)
        mixer_body.addWidget(bus)
        self.width_control = WidthControl(1.0)
        self.width_control.setToolTip(_tr('立体声宽度：0 全单声道，1 原宽，2 最大侧向展开。'))
        self.width_control.widthChanged.connect(self.parameters_changed)
        mixer_body.addWidget(self.width_control)

        processing_card, processing_body = card(
            _tr('人声处理与空间'),
            _tr('三项默认关闭。建议先试听干声与伴奏的平衡，再一项一项打开，避免一次改变太多。'),
            '2')
        self.eq_enabled = QCheckBox(_tr('启用 EQ'))
        self.eq_enabled.setObjectName(CONTROL_NAMES['eq_enabled'])
        self.eq_enabled.setAccessibleName(_tr('启用均衡器'))
        self.compression_enabled = QCheckBox(_tr('启用压缩'))
        self.compression_enabled.setObjectName(CONTROL_NAMES['compression_enabled'])
        self.compression_enabled.setAccessibleName(_tr('启用压缩'))
        self.reverb_enabled = QCheckBox(_tr('启用混响'))
        self.reverb_enabled.setObjectName(CONTROL_NAMES['reverb_enabled'])
        self.reverb_enabled.setAccessibleName(_tr('启用混响'))
        self.reverb_profile = QComboBox()
        self.reverb_profile.setObjectName('mixdownReverbProfile')
        self.reverb_profile.setAccessibleName(_tr('混响空间'))
        for title, key in ((_tr('短空间'), 'small'), (_tr('自然空间'), 'natural'),
                           (_tr('宽阔空间'), 'wide')):
            self.reverb_profile.addItem(title, key)
        self.reverb_profile.setCurrentIndex(1)
        self.reverb_predelay = QSpinBox()
        self.reverb_predelay.setObjectName('mixdownReverbPredelay')
        self.reverb_predelay.setAccessibleName(_tr('混响预延迟毫秒'))
        self.reverb_predelay.setRange(0, 120)
        self.reverb_predelay.setSingleStep(5)
        self.reverb_predelay.setSuffix(' ms')
        self.reverb_predelay.setValue(0)
        self.eq_curve = _ResponsiveEQCurveView()
        self.eq_curve.setMinimumHeight(320)
        self.eq_curve.setMaximumHeight(16777215)
        self.eq_curve.set_bands(self._eq_bands)
        self.eq_curve.setAccessibleName(_tr('实时四段 EQ 曲线'))
        self.processing_visual = self.eq_curve

        self.low_cut = QDoubleSpinBox()
        self.low_cut.setObjectName(CONTROL_NAMES['low_cut'])
        self.low_cut.setAccessibleName(_tr('低频切除频率'))
        self.low_cut.setRange(20.0, 500.0)
        self.low_cut.setDecimals(0)
        self.low_cut.setSingleStep(5.0)
        self.low_cut.setSuffix(' Hz')
        self.low_cut.setValue(80.0)
        self.presence = QDoubleSpinBox()
        self.presence.setObjectName(CONTROL_NAMES['presence'])
        self.presence.setAccessibleName(_tr('咬字存在感增益'))
        self.presence.setRange(-6.0, 6.0)
        self.presence.setDecimals(1)
        self.presence.setSingleStep(0.5)
        self.presence.setSuffix(' dB')
        self.presence.setValue(1.0)
        self.compression = QDoubleSpinBox()
        self.compression.setObjectName(CONTROL_NAMES['compression'])
        self.compression.setAccessibleName(_tr('压缩强度百分比'))
        self.compression.setRange(0.0, 100.0)
        self.compression.setDecimals(0)
        self.compression.setSingleStep(5.0)
        self.compression.setSuffix(' %')
        self.compression.setValue(25.0)
        self.reverb = QDoubleSpinBox()
        self.reverb.setObjectName(CONTROL_NAMES['reverb'])
        self.reverb.setAccessibleName(_tr('混响混合百分比'))
        self.reverb.setRange(0.0, 100.0)
        self.reverb.setDecimals(0)
        self.reverb.setSingleStep(5.0)
        self.reverb.setSuffix(' %')
        self.reverb.setValue(10.0)

        self.eq_band_controls = EqBandStripControls(
            self._eq_bands, curve_view=self.eq_curve)
        self.eq_band_controls.bandsChanged.connect(self._eq_bands_changed)
        self.eq_band_controls.setEnabled(self.eq_enabled.isChecked())
        self.eq_page = QWidget()
        self.eq_page.setObjectName("mixdownAnalysisEqPage")
        self.eq_page.setAutoFillBackground(False)
        self.eq_page_layout = QVBoxLayout(self.eq_page)
        self.eq_page_layout.setContentsMargins(4, 4, 4, 4)
        self.eq_page_layout.setSpacing(2)
        self.eq_header = QHBoxLayout()
        self.eq_header.addWidget(self.eq_enabled)
        self.eq_header.addStretch(1)
        self.eq_spectrum_label = label(_tr('人声频谱'), 'muted')
        self.eq_header.addWidget(self.eq_spectrum_label)
        self.eq_page_layout.addLayout(self.eq_header)
        self.eq_page_layout.addWidget(self.eq_curve, 1)
        self.gr_page = QWidget()
        self.gr_page.setObjectName("mixdownAnalysisGrPage")
        self.gr_page.setAutoFillBackground(False)
        self.gr_layout = QVBoxLayout(self.gr_page)
        self.gr_layout.setContentsMargins(4, 4, 4, 4)
        self.gr_layout.setSpacing(2)
        self.gr_head = QHBoxLayout()
        self.gr_head.addWidget(self.compression_enabled)
        self.gr_summary = QLabel("GR p95 —")
        self.gr_head.addStretch(1)
        self.gr_head.addWidget(self.gr_summary)
        self.gr_layout.addLayout(self.gr_head)
        self.gr_controls = QHBoxLayout()
        self.gr_controls.addWidget(label(_tr('压缩强度')))
        self.gr_controls.addWidget(self.compression, 1)
        self.gr_layout.addLayout(self.gr_controls)
        self.edc_page = QWidget()
        self.edc_page.setObjectName("mixdownAnalysisEdcPage")
        self.edc_page.setAutoFillBackground(False)
        self.edc_layout = QVBoxLayout(self.edc_page)
        self.edc_layout.setContentsMargins(4, 4, 4, 4)
        self.edc_layout.setSpacing(2)
        self.edc_head = QHBoxLayout()
        self.edc_head.addWidget(self.reverb_enabled)
        self.edc_head.addWidget(label(_tr('混合')))
        self.edc_head.addWidget(self.reverb)
        self.edc_head.addStretch(1)
        self.edc_space = QHBoxLayout()
        self.reverb_profile.setMinimumWidth(72)
        self.reverb_profile.setMaximumWidth(130)
        self.reverb_predelay.setFixedWidth(74)
        self.edc_space.addWidget(label(_tr('空间类型')))
        self.edc_space.addWidget(self.reverb_profile, 1)
        self.edc_space.addWidget(label(_tr('预延迟')))
        self.edc_space.addWidget(self.reverb_predelay)
        self.edc_layout.addLayout(self.edc_head)
        self.edc_layout.addLayout(self.edc_space)
        self.reverb_live_values = label(_tr('发送 / 返回电平：等待播放'), 'muted')
        self.reverb_live_values.setObjectName('mixdownReverbLiveValues')
        self.reverb_live_values.setWordWrap(True)
        self.edc_layout.addWidget(self.reverb_live_values)
        self.meter_feed = MeterEnvelope(self)
        self.meter_feed.changed.connect(self._apply_live_meter_snapshot)
        self.gr_history = GrHistoryView(feed=self.meter_feed)
        self.gr_history.setMinimumSize(0, 180)
        self.gr_history.setMaximumHeight(16777215)
        self.gr_layout.addWidget(self.gr_history, 1)
        self.reverb_decay = ReverbDecayView(feed=self.meter_feed)
        self.reverb_decay.setMinimumSize(0, 180)
        self.reverb_decay.setMaximumHeight(16777215)
        self.reverb_decay_toggle = fold(
            _tr('查看混响衰减（静态）'), self.reverb_decay, self.edc_layout,
            expanded=False)
        self.reverb_decay_help = label(
            _tr('脉冲衰减尚未测量；播放时显示真实发送与返回电平。'), 'muted')
        self.reverb_decay_help.setWordWrap(True)
        self.edc_layout.addWidget(self.reverb_decay_help)
        self.edc_layout.addStretch(1)
        self.analysis_tabs = QTabWidget()
        self.analysis_tabs.setObjectName("mixdownAnalysisTabs")
        self.analysis_tabs.setDocumentMode(True)
        self.analysis_tabs.setAutoFillBackground(False)
        self.analysis_tabs.setMinimumHeight(190)
        self.analysis_tabs.setMaximumHeight(16777215)
        self.analysis_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.analysis_tabs.addTab(self.eq_page, _tr("EQ"))
        self.analysis_tabs.addTab(self.gr_page, _tr("GR"))
        self.analysis_tabs.addTab(self.edc_page, _tr("混响空间"))
        self.analysis_tabs.currentChanged.connect(self._analysis_page_changed)
        self._analysis_page_changed(0)
        processing_body.addWidget(self.analysis_tabs, 1)
        self.expand_curve_button = QToolButton()
        self.expand_curve_button.setText(_tr('放大曲线'))
        self.expand_curve_button.setObjectName('mixdownExpandCurve')
        self.expand_curve_button.setAccessibleName(_tr('放大曲线编辑区域'))
        self.expand_curve_button.setCheckable(True)
        self.expand_curve_button.setMinimumHeight(36)
        self.expand_curve_button.toggled.connect(self._expand_curve_workspace)
        processing_body.itemAt(0).layout().addWidget(self.expand_curve_button)
        self.eq_precision_toggle = fold(
            _tr('四段精确参数'), self.eq_band_controls, self.eq_page_layout, expanded=False)
        self.eq_precision_toggle.setAccessibleName(_tr('展开四段精确参数'))

        advanced_content = QWidget()
        advanced_content.setObjectName('mixdownAdvancedPanel')
        advanced_grid = QGridLayout(advanced_content)
        advanced_grid.setContentsMargins(0, 8, 0, 0)
        advanced_grid.setHorizontalSpacing(12)
        advanced_grid.setVerticalSpacing(8)
        advanced_grid.setColumnStretch(1, 1)
        advanced_grid.setColumnStretch(3, 1)
        advanced_grid.addWidget(label(_tr('低频切除')), 0, 0)
        advanced_grid.addWidget(self.low_cut, 0, 1)
        advanced_grid.addWidget(label(_tr('咬字存在感')), 0, 2)
        advanced_grid.addWidget(self.presence, 0, 3)
        advanced_grid.addWidget(label(
            _tr('经典兼容值会同步到四段 EQ 曲线。'), 'muted'),
            1, 0, 1, 4)
        self.advanced_toggle = fold(_tr('经典双段兼容值'), advanced_content, self.eq_page_layout, expanded=False)
        self.advanced_toggle.setObjectName(CONTROL_NAMES['advanced_toggle'])
        self.advanced_toggle.setAccessibleName(_tr('展开高级处理值'))
        self.advanced_toggle.setProperty('role', 'muted')
        eq_tools = FlowLayout(spacing=12)
        for toggle in (self.eq_precision_toggle, self.advanced_toggle):
            self.eq_page_layout.removeWidget(toggle)
            eq_tools.addWidget(toggle)
        self.eq_page_layout.addLayout(eq_tools)
        self.eq_precision_toggle.toggled.connect(self._analysis_fold_changed)
        self.advanced_toggle.toggled.connect(self._analysis_fold_changed)

        preview_card = QWidget()
        preview_card.setObjectName('card')
        preview_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        preview_body = QVBoxLayout(preview_card)
        preview_body.setContentsMargins(14, 8, 14, 8)
        preview_body.setSpacing(4)
        preview_range_content = QWidget()
        preview_range_content.setObjectName('mixdownPreviewRangePanel')
        preview_range = QGridLayout(preview_range_content)
        preview_range.setContentsMargins(0, 8, 0, 0)
        preview_range.setHorizontalSpacing(12)
        preview_range.setVerticalSpacing(8)
        self.preview_start = QDoubleSpinBox()
        self.preview_start.setObjectName(CONTROL_NAMES['preview_start'])
        self.preview_start.setAccessibleName(_tr('试听起点秒数'))
        self.preview_start.setRange(0.0, 86400.0)
        self.preview_start.setDecimals(3)
        self.preview_start.setSingleStep(0.5)
        self.preview_start.setSuffix(' 秒')
        self.preview_start.setValue(0.0)
        self.preview_duration = QDoubleSpinBox()
        self.preview_duration.setObjectName(CONTROL_NAMES['preview_duration'])
        self.preview_duration.setAccessibleName(_tr('试听范围时长秒数'))
        self.preview_duration.setRange(2.0, 30.0)
        self.preview_duration.setDecimals(1)
        self.preview_duration.setSingleStep(1.0)
        self.preview_duration.setSuffix(' 秒')
        self.preview_duration.setValue(12.0)
        preview_range.addWidget(label(_tr('试听起点')), 0, 0)
        preview_range.addWidget(self.preview_start, 0, 1)
        preview_range.addWidget(label(_tr('范围时长')), 0, 2)
        preview_range.addWidget(self.preview_duration, 0, 3)
        preview_range.addWidget(label(
            _tr('打开循环后重复此范围；顺序播放时可直接在进度条定位。'), 'muted'), 1, 0, 1, 4)
        self.preview_range_toggle = fold(
            _tr('试听范围'), preview_range_content, preview_body, expanded=False)
        self.preview_range_toggle.setObjectName(CONTROL_NAMES['preview_range_toggle'])
        self.preview_range_toggle.setAccessibleName(_tr('展开试听范围'))
        self.preview_range_toggle.setProperty('role', 'muted')

        live_head = QHBoxLayout()
        live_head.setSpacing(8)
        self.preview_live_badge = label(_tr('实时试听'), 'success')
        self.preview_live_badge.setStyleSheet('font-weight: 650;')
        live_head.addWidget(self.preview_live_badge)
        self.audition_diagnostics = _ElidedLabel(_tr('实时试听 · 等待组件'))
        self.audition_diagnostics.setObjectName('mixdownAuditionDiagnostics')
        self.audition_diagnostics.setAccessibleName(_tr('实时试听诊断'))
        live_head.addWidget(self.audition_diagnostics, 1)
        live_head.addStretch(1)
        preview_body.addLayout(live_head)
        self.preview_report = label(_tr('选择声音后自动准备实时试听。'), 'muted')
        self.preview_report.setObjectName(CONTROL_NAMES['preview_status'])
        self.preview_report.setAccessibleName(_tr('实时试听状态'))
        preview_body.addWidget(self.preview_report)
        self.preview_report.hide()

        player_panel = QWidget()
        player_panel.setObjectName('mixer-player')
        player_layout = QVBoxLayout(player_panel)
        player_layout.setContentsMargins(12, 10, 12, 10)
        player_layout.setSpacing(8)
        self.preview_before_button = button(
            _tr('A  处理前'), lambda: self.play_preview('before'))
        self.preview_before_button.setObjectName(CONTROL_NAMES['preview_before'])
        self.preview_before_button.setAccessibleName(_tr('切换到处理前试听'))
        self.preview_before_button.setCheckable(True)
        self.preview_after_button = button(
            _tr('B  处理后'), lambda: self.play_preview('after'))
        self.preview_after_button.setObjectName(CONTROL_NAMES['preview_after'])
        self.preview_after_button.setAccessibleName(_tr('切换到处理后试听'))
        self.preview_after_button.setCheckable(True)
        for control in (self.preview_before_button, self.preview_after_button):
            control.setProperty('role', 'step-nav')
            control.setMinimumWidth(108)
        transport = FlowLayout(spacing=8)
        self.player_toggle_button = QPushButton(_tr('播放'))
        self.player_toggle_button.setObjectName('mixdownPlayerToggle')
        self.player_toggle_button.setAccessibleName(_tr('播放试听'))
        self.player_toggle_button.setMinimumSize(64, 40)
        self.player_toggle_button.clicked.connect(self.toggle_preview_playback)
        self.player_loop_button = QPushButton(_tr('循环'))
        self.player_loop_button.setObjectName(CONTROL_NAMES['player_loop'])
        self.player_loop_button.setAccessibleName(_tr('循环当前试听范围'))
        self.player_loop_button.setToolTip(_tr('循环当前试听范围'))
        self.player_loop_button.setCheckable(True)
        self.player_loop_button.setMinimumSize(64, 40)
        self.player_loop_button.toggled.connect(self._set_preview_loop)
        self.player_position = _SeekSlider(Qt.Horizontal)
        self.player_position.setObjectName('mixdownPlayerPosition')
        self.player_position.setAccessibleName(_tr('试听播放位置'))
        self.player_position.setRange(0, 0)
        self.player_position.setMinimumHeight(44)
        self.player_position.setStyleSheet(
            'QSlider#mixdownPlayerPosition:horizontal { min-height: 48px; }'
            'QSlider#mixdownPlayerPosition::groove:horizontal { height: 8px; }'
            'QSlider#mixdownPlayerPosition::handle:horizontal { width: 22px; }')
        self.player_position.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.player_position.sliderPressed.connect(self._begin_player_seek)
        self.player_position.sliderMoved.connect(self._seek_player_preview)
        self.player_position.sliderReleased.connect(self._finish_player_seek)
        self.player_position.seekCancelled.connect(self._cancel_player_seek_gesture)
        self.player_position.valueChanged.connect(self._player_position_value_changed)
        self.player_time = QLabel('00:00 / 00:00')
        self.player_time.setObjectName('mixer-time')
        self.player_time.setMinimumWidth(104)
        self.player_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        transport.addWidget(self.player_toggle_button)
        transport.addWidget(self.player_loop_button)
        transport.addWidget(self.preview_before_button)
        transport.addWidget(self.preview_after_button)
        transport.addWidget(self.player_time)
        preview_body.removeWidget(self.preview_range_toggle)
        transport.addWidget(self.preview_range_toggle)
        player_layout.addLayout(transport)
        player_layout.addWidget(self.player_position)

        state_row = QHBoxLayout()
        state_row.setSpacing(10)
        self.preview_before_state = label(_tr('自动待命'), 'muted')
        self.preview_before_state.setObjectName(CONTROL_NAMES['preview_before_state'])
        self.preview_after_state = label(_tr('自动待命'), 'muted')
        self.preview_after_state.setObjectName(CONTROL_NAMES['preview_after_state'])
        self.preview_before_state.hide()
        self.preview_after_state.hide()
        state_row.addWidget(self.preview_before_state, 1)
        state_row.addWidget(self.preview_after_state, 1)
        player_layout.addLayout(state_row)
        preview_body.addWidget(player_panel)

        export_card, export_outer = card(_tr('导出与登记'))
        export_details = QWidget(export_card)
        export_details.setObjectName('mixdownExportContent')
        export_body = QVBoxLayout(export_details)
        export_body.setContentsMargins(0, 0, 0, 0)
        export_body.setSpacing(10)
        export_outer.addWidget(export_details)
        self.export_details = export_details
        output_grid = QGridLayout()
        output_grid.setContentsMargins(0, 0, 0, 0)
        output_grid.setHorizontalSpacing(10)
        output_grid.setVerticalSpacing(8)
        output_grid.setColumnStretch(0, 1)
        output = QLineEdit()
        output.setObjectName(CONTROL_NAMES['output_path'])
        output.setAccessibleName(_tr('新的混音 WAV 文件名'))
        output.setPlaceholderText(_tr('选择新的 WAV 文件名'))
        output.setClearButtonEnabled(True)
        output_choose = button(_tr('选择…'), lambda checked=False: self._choose_path('output'))
        output_choose.setObjectName(CONTROL_NAMES['browse_output'])
        output_choose.setAccessibleName(_tr('选择新的混音输出文件'))
        self.fields['output'] = output
        self._choose_buttons['output'] = output_choose
        self.output_state = label(_tr('尚未指定输出文件'), 'muted')
        self.output_state.setObjectName(CONTROL_NAMES['output_state'])
        # Give the filename its own row so the chosen path remains readable
        # in the narrow export column of the three-column mixer.
        output_grid.addWidget(label(_tr('新的混音 WAV')), 0, 0, 1, 2)
        output_grid.addWidget(output, 1, 0)
        output_grid.addWidget(output_choose, 1, 1)
        output_grid.addWidget(self.output_state, 2, 0, 1, 2)
        export_body.addLayout(output_grid)

        export_actions = FlowLayout(spacing=10)
        self.export_button = button(_tr('导出 WAV'), self.export, 'primary')
        self.export_button.setObjectName(CONTROL_NAMES['export'])
        self.export_button.setAccessibleName(_tr('导出新的混音 WAV'))
        self.cancel_button = button(_tr('取消当前处理'), self.cancel, 'danger')
        self.cancel_button.setObjectName(CONTROL_NAMES['cancel'])
        self.cancel_button.setEnabled(False)
        export_actions.addWidget(self.export_button)
        export_body.addLayout(export_actions)

        result_panel = QWidget()
        result_panel.setObjectName('step-surface')
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(16, 14, 16, 14)
        result_layout.setSpacing(7)
        self.result_title = label(_tr('尚无导出结果'), 'muted')
        self.result_title.setStyleSheet('font-weight: 600;')
        self.result_title.setObjectName(CONTROL_NAMES['result_title'])
        self.result_text = label(_tr('选择歌声与伴奏，并指定新输出文件名后导出。'), 'muted')
        self.result_text.setObjectName(CONTROL_NAMES['result_status'])
        self.result_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_text)
        export_body.addWidget(result_panel)

        self.export_note = label(
            _tr('登记是单独的一步：只登记本次导出的文件，不改动源 WAV；实际选择的源路径、指纹与处理参数会另存为回执。'),
            'muted')
        export_body.addWidget(self.export_note)
        register_actions = FlowLayout(spacing=10)
        self.register_button = button(_tr('只登记为任务 Mixdown'), lambda: self.register(False))
        self.register_button.setObjectName(CONTROL_NAMES['register'])
        self.register_button.setAccessibleName(_tr('登记本次导出为任务混音文件'))
        self.to_video_button = button(_tr('登记并进入视频合成'), self.continue_to_video, 'primary')
        self.to_video_button.setObjectName(CONTROL_NAMES['register_video'])
        self.to_video_button.setAccessibleName(_tr('登记本次导出并进入视频合成'))
        self.register_button.setEnabled(False)
        self.to_video_button.setEnabled(False)
        register_actions.addWidget(self.register_button)
        register_actions.addWidget(self.to_video_button)
        export_body.addLayout(register_actions)
        export_outer.setContentsMargins(14, 8, 14, 8)
        export_details.hide()
        self.export_details_toggle = QToolButton()
        self.export_details_toggle.setText(_tr('展开导出设置'))
        self.export_details_toggle.setObjectName('mixdownExportDetails')
        self.export_details_toggle.setCheckable(True)
        self.export_details_toggle.setArrowType(Qt.RightArrow)
        self.export_details_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.export_details_toggle.toggled.connect(self._set_export_expanded)
        self.export_details_toggle.toggled.connect(lambda checked: (
            self.export_details_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow),
            self.export_details_toggle.setText(_tr('收起导出设置') if checked else _tr('展开导出设置'))))
        export_outer.itemAt(0).layout().addWidget(self.export_details_toggle)
        self.mixer_card = mixer_card
        self.processing_card = processing_card
        self.preview_card = preview_card
        self.export_card = export_card
        self.workspace = _MixerWorkspace(
            mixer_card, processing_card, preview_card, export_card,
            chrome=(self._heading, self._intro, self._steps, self._mixer_help,
                    self.export_note))
        self.workspace._body_layout = body
        self.workspace.set_rack_console(console)
        body.addWidget(self.workspace, 1)
        outer.addWidget(preview_card)

        footer = QWidget()
        footer.setObjectName('footer')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 10, 18, 10)
        footer_layout.setSpacing(7)
        self.status = label(_tr('从选择素材开始；每一步都可以返回检查。'), 'muted')
        self.status.setObjectName(CONTROL_NAMES['status'])
        footer_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName(CONTROL_NAMES['progress'])
        self.progress.setRange(0, 0)
        self.progress.hide()
        footer_layout.addWidget(self.progress)
        footer_actions = FlowLayout(spacing=10)
        footer_actions.addWidget(self.cancel_button)
        self.close_button = button(_tr('关闭'), self.accept)
        self.close_button.setObjectName(CONTROL_NAMES['close'])
        self.close_button.setVisible(not self.embedded)
        footer_actions.addWidget(self.close_button)
        footer_layout.addLayout(footer_actions)
        outer.addWidget(footer)

        track_value_controls = [
            control for track in self.vocal_tracks
            for control in (track['gain_db'], track['slider'], track['offset'])
        ]
        track_mix_controls = [
            control for track in self.vocal_tracks
            for control in (track['mute'], track['solo'], track['pan'])
        ]
        self._editable_widgets = list(self.fields.values()) + list(self._choose_buttons.values()) + [
            *track_value_controls, *track_mix_controls,
            self.instrumental_db, self.instrumental_slider,
            self.instrumental_mute, self.instrumental_solo, self.instrumental_pan,
            self.eq_enabled, self.low_cut, self.presence, self.eq_curve,
            self.eq_band_controls, self.eq_precision_toggle,
            self.compression_enabled, self.compression, self.reverb_enabled, self.reverb,
            self.reverb_profile, self.reverb_predelay,
            self.advanced_toggle, self.preview_start, self.preview_duration,
            self.preview_range_toggle, self.preset_bar, self.width_control,
            self.send_controls_fold, self.save_mix_preset_button,
            self.choose_mix_preset_button, self.undo_mix_preset_button]

        self._syncing = True
        try:
            # Never infer an external voicebank render from the task's original
            # source separation. The user must choose the dry vocal explicitly.
            instrumental = _task_source(self.job, ('audio/instrumental.wav',))
            if instrumental:
                self.fields['instrumental'].setText(str(instrumental))
                self._auto_instrumental = instrumental
            self._auto_output = str(_suggested_output(self.job))
            self.fields['output'].setText(self._auto_output)
            self.fields['output'].setToolTip(_tr('导出前会再次检查；任何已存在文件都不会被覆盖。'))
        finally:
            self._syncing = False

        for key, field in self.fields.items():
            field.textChanged.connect(self.output_changed if key == 'output' else self.parameters_changed)
        for control in (*track_value_controls, self.instrumental_db,
                        self.compression, self.reverb, self.reverb_predelay):
            control.valueChanged.connect(self.parameters_changed)
        self.low_cut.valueChanged.connect(self._legacy_eq_changed)
        self.presence.valueChanged.connect(self._legacy_eq_changed)
        for control in (self.preview_start, self.preview_duration):
            control.valueChanged.connect(self.preview_parameters_changed)
        for control in (self.eq_enabled, self.compression_enabled, self.reverb_enabled):
            control.toggled.connect(self.parameters_changed)
        self.reverb_profile.currentIndexChanged.connect(self.parameters_changed)
        self._set_vocal_track_count(1, announce=False)
        self._update_source_states()
        self._update_output_state()
        self._init_preview_player()
        self._install_mixdown_shortcuts()
        self._prepare_component_support()
        self._sync_processing_controls()
        self._refresh_analysis_palette()
        self._ui_ready = True
        self._sync_controls()
        self._start_component_support_check()

    def _set_export_expanded(self, expanded):
        self.export_details.setVisible(bool(expanded))
        self.export_details.updateGeometry()
        self.export_card.updateGeometry()
        self.workspace.grid.invalidate()
        self.workspace.updateGeometry()
        if expanded:
            QTimer.singleShot(0, lambda: self._main_scroll.ensureWidgetVisible(
                self.export_details, 0, 12))

    @property
    def job(self):
        return self._job

    @job.setter
    def job(self, value):
        self._job = Path(value).resolve() if value else None
        if self._ui_ready:
            self._job_context_changed()

    def set_job(self, job):
        """Associate or clear a task without changing editable mix inputs."""
        if self.busy():
            return False
        self.job = job
        return True

    def _job_context_changed(self):
        if not self._ui_ready:
            return
        input_changed = False
        instrumental = _task_source(self.job, ('audio/instrumental.wav',))
        current = self._path('instrumental')
        auto_selected = _same_path(current, self._auto_instrumental)
        if instrumental:
            if current is None or auto_selected:
                self._syncing = True
                try:
                    input_changed = not _same_path(current, instrumental)
                    self.fields['instrumental'].setText(str(instrumental))
                    self._auto_instrumental = instrumental
                finally:
                    self._syncing = False
        elif auto_selected:
            self._syncing = True
            try:
                self.fields['instrumental'].clear()
                self._auto_instrumental = None
                input_changed = True
            finally:
                self._syncing = False
        origin = (self._export_context or {}).get('source_job')
        if self._result and origin and not _same_path(origin, self.job):
            # An explicitly task-bound export cannot silently become another
            # task's result. The WAV remains on disk for deliberate reuse.
            self._result = None
            self._export_context = None
            self._export_receipt = None
            self.result_title.setText(_tr('任务已变化'))
            self.result_text.setText(_tr('原混音文件仍在原位置；请确认素材后重新导出。'))
        if input_changed:
            self.parameters_changed()
        if self._result is None and self.fields['output'].text() == self._auto_output:
            replacement = _suggested_output(self.job)
            self._syncing = True
            try:
                self.fields['output'].setText(str(replacement))
                self._auto_output = str(replacement)
            finally:
                self._syncing = False
        self.status.setText(
            _tr('已关联任务；导出后可按需登记。') if self.job
            else _tr('独立文件模式；无需任务也可导出并交给视频合成。'))
        _set_role(self.status, 'muted')
        self._update_source_states()
        self._update_output_state()
        self._sync_controls()

    def active_vocal_tracks(self):
        count = getattr(self, '_vocal_track_count', 1)
        return list(self.vocal_tracks[:count])

    def _set_vocal_track_count(self, count, announce=True):
        count = max(1, min(_MAX_VOCAL_TRACKS, int(count)))
        self._vocal_track_count = count
        self._syncing = True
        try:
            for track in self.vocal_tracks:
                active = track['index'] < count
                track['panel'].setVisible(active)
                track['level_panel'].setVisible(active)
                track['remove'].setVisible(active and track['index'] > 0
                                            and track['index'] == count - 1)
                if not active:
                    track['path'].clear()
                    track['gain_db'].setValue(0.0)
                    track['offset'].setValue(0.0)
                    track['mute'].setChecked(False)
                    track['solo'].setChecked(False)
                    track['pan'].setValue(0)
                    track['pan_value'].setText('C')
                    track['reverb_send'] = 1.0
                    track['file_label'].setText(_tr('尚未选择'))
                    track['meter'].reset()
            next_count = count + 1
            if hasattr(self, '_mix_summary'):
                self._mix_summary.setText(
                    _tr('{0} 路歌声 · 1 路伴奏 · 共享人声处理').format(count))
            if hasattr(self, 'mixer_console'):
                self.mixer_console.set_active_vocal_count(count)
            if hasattr(self, 'send_controls'):
                self._sync_send_controls()
            self.add_vocal_button.setVisible(next_count <= _MAX_VOCAL_TRACKS)
            self.add_vocal_button.setText(
                _tr('添加第 {0} 路歌声').format(next_count)
                if next_count <= _MAX_VOCAL_TRACKS else _tr('最多 3 路歌声'))
        finally:
            self._syncing = False
        if announce and self._ui_ready:
            self.parameters_changed()

    def add_vocal_track(self):
        if self.busy():
            return
        count = getattr(self, '_vocal_track_count', 1)
        if count >= _MAX_VOCAL_TRACKS:
            return
        self._set_vocal_track_count(count + 1)
        self.status.setText(_tr('已增加一路可选歌声；未填写路径时不会参与混音。'))
        _set_role(self.status, 'muted')

    def remove_vocal_track(self, index=None):
        if self.busy():
            return
        count = getattr(self, '_vocal_track_count', 1)
        if index is None:
            index = count - 1
        if index <= 0 or index >= count:
            return
        if index != count - 1:
            self.status.setText(_tr('请从最后一路歌声开始移除。'))
            _set_role(self.status, 'error')
            return
        self._set_vocal_track_count(count - 1)
        self.status.setText(_tr('已移除最后一路歌声；其路径、音量和偏移已清空。'))
        _set_role(self.status, 'muted')

    def busy(self):
        return self.worker is not None

    def support_checking(self):
        return self._support_worker is not None

    def _prepare_component_support(self):
        root, spec, installer = _component_manifest()
        self._component_root, self._component_spec = root, spec
        self._component_installer = installer
        self._component_packages = _optional_packages(root, spec) if root and spec else None
        self._support_ready = False
        self._support_error = ''
        self.support_panel.show()
        self.support_state.setText(_tr('正在后台检查混音组件…'))
        _set_role(self.support_state, 'muted')
        self.install_dependency_button.hide()
        self.install_dependency_button.setEnabled(False)

    def _start_component_support_check(self):
        if not self._ui_ready or self._support_ready or self._support_worker is not None:
            return False
        packages = self._component_packages
        worker = MixdownTask(
            lambda check: _activate_pedalboard(packages, check=check), self)
        self._support_worker = worker
        self._support_checking = True
        worker.succeeded.connect(self._support_probe_succeeded)
        worker.failed.connect(self._support_probe_failed)
        worker.cancelled.connect(self._support_probe_cancelled)
        worker.finished.connect(self._support_probe_finished)
        self.status.setText(_tr('正在后台检查混音组件；界面仍可查看和选择素材。'))
        _set_role(self.status, 'muted')
        worker.start()
        self._sync_controls()
        return True

    def _support_probe_succeeded(self, result):
        try:
            ready, detail = result
        except (TypeError, ValueError):
            ready, detail = False, _tr('混音组件检查返回了无效结果。')
        self._apply_component_support(ready, detail, self._component_packages)
        if ready:
            self.status.setText(_tr('混音组件已就绪，可以渲染试听或导出。'))
            _set_role(self.status, 'success')
        else:
            self.status.setText(_tr('混音组件尚未就绪；安装后即可试听和导出。'))
            _set_role(self.status, 'muted')

    def _support_probe_failed(self, text):
        self._apply_component_support(False, text, self._component_packages)
        self.support_state.setText(_tr('混音组件检查失败：') + text)
        _set_role(self.support_state, 'error')
        self.status.setText(_tr('混音组件检查失败：') + text)
        _set_role(self.status, 'error')

    def _support_probe_cancelled(self):
        self._support_checking = False

    def _support_probe_finished(self):
        worker = self.sender()
        if worker is not None and worker is not self._support_worker:
            return
        self._support_worker = None
        self._support_checking = False
        if worker is not None:
            worker.deleteLater()
        if self._try_finish_pending_close():
            return
        self._sync_controls()

    def _refresh_component_support(self):
        self._prepare_component_support()
        if self._ui_ready:
            self._start_component_support_check()

    def _apply_component_support(self, ready, detail, packages=None):
        self._support_ready = bool(ready)
        self._support_error = '' if ready else str(detail or '')
        if packages:
            candidate = Path(packages).resolve()
            self._component_packages = candidate if candidate.is_dir() else None
        self.support_panel.setVisible(not self._support_ready)
        if self._support_ready:
            self.support_state.setText(_tr('混音组件已就绪'))
            _set_role(self.support_state, 'success')
            self.support_state.setToolTip('Pedalboard ' + str(detail))
            self.install_dependency_button.hide()
        else:
            size = ''
            try:
                size = _tr('（下载约 {0:.1f} MiB）').format(
                    float(self._component_spec['wheel']['bytes']) / 1024 / 1024)
            except (KeyError, TypeError, ValueError):
                size = ''
            self.support_state.setText(
                _tr('首次使用请安装混音组件') + size + _tr('，安装后即可试听和导出。'))
            _set_role(self.support_state, 'muted')
            self.support_state.setToolTip(self._support_error)
            available = bool(self._component_installer and self._component_installer.is_file())
            self.install_dependency_button.setVisible(available)
            self.install_dependency_button.setEnabled(available and not self.busy())
            self.install_dependency_button.setText(
                _tr('修复混音组件') if self._component_packages
                else _tr('安装混音组件（约 3.5 MiB）'))
        self._sync_controls()
        if (self._support_ready and self._ui_ready and self.isVisible()
                and not self._close_pending):
            if not self._audition_closing:
                # Start the resident service when the mixdown page is opened,
                # before source selection, so the first preview does not pay
                # the Python/Pedalboard startup cost.
                self._ensure_audition_service_started()
            if self._sources_ready():
                self._schedule_preview_refresh()

    def _init_preview_player(self):
        from mixdown_realtime_player import native_output_path
        self._native_dll_path = native_output_path()
        self._player = self._create_native_preview_player()
        self._player.available = self._native_dll_path is not None
        self._connect_preview_player(self._player)
        if not self._player.available:
            self.preview_report.setText(_tr('连续试听组件缺失或校验失败，请修复组件后重新打开混音页。'))

    def _create_native_preview_player(self):
        from mixdown_realtime_player import RealtimePreviewPlayer
        packages = self._component_packages or _OPTIONAL_PACKAGES
        if not packages:
            root, spec, _ = _component_manifest()
            packages = _optional_packages(root, spec) if root and spec else None
        player = RealtimePreviewPlayer(
            self._native_dll_path, cache_dir=self._ensure_preview_cache_directory(), parent=self,
            service_options=dict(python_executable=_child_python(),
                                 extra_paths=[packages] if packages else [],
                                 environment=_worker_environment(packages),
                                 startup_timeout=8.0, shutdown_timeout=2.0))
        self._native_preview_player = player
        self.eq_spectrum_label.setText(_tr('实测频谱 · EQ 前 / 后'))
        self._native_requested_key = self._player_key
        self._audition_service = player
        self._audition_service_ready = self._audition_start_attempted = True
        player.prepared.connect(self._native_preview_prepared)
        player.parametersApplied.connect(self._native_parameters_applied)
        player.telemetryChanged.connect(self._native_preview_telemetry)
        return player


    def _start_native_preview(self):
        if self._audition_closing or self.busy() or not self._player.available:
            return False
        player = self._native_preview_player
        if player.failed and not self._preview_play_intent:
            return False
        source_key = self._current_preview_source_key()
        if not source_key or not self._support_ready:
            player.release()
            self._native_source_key = None
            return False
        key = self._native_requested_key
        try:
            # This path is only a request field; live playback never writes it.
            request = self._request(self._ensure_preview_cache_directory() / 'live-unused.wav',
                                    bypass_processing=key == 'before')
            position = int(round((self._preview_resume_seconds or 0.0) * 1000))
            if source_key != self._native_source_key:
                position = int(round(self.preview_start.value() * 1000))
            player.set_loop_range(int(self.preview_start.value() * 1000),
                                  int((self.preview_start.value() + self.preview_duration.value()) * 1000))
            if source_key != self._native_source_key or not player.ready:
                if player.loading and source_key == self._native_source_key:
                    player.parameters(request, key=key)
                else:
                    player.open(request, position_ms=position,
                                autoplay=self._preview_play_intent, key=key)
                    self._native_source_key = source_key
            else:
                player.parameters(request, key=key)
            self._preview_dirty = False
            self._preview_pending = False
            self._set_audition_diagnostic('updating', _tr('正在更新连续试听…'))
            self._sync_controls()
            return True
        except Exception as error:
            self._native_preview_error(str(error))
            return False

    def _native_preview_prepared(self):
        player = self._native_preview_player
        self._preview_segment_start_seconds = 0.0
        self._preview_segment_duration_seconds = player.duration() / 1000.0
        self._player_duration_changed(player.duration())
        self._native_parameters_applied(player.selected_key)
        self.preview_report.setText(_tr('连续试听已就绪，可拖动进度条定位整曲。'))
        self.status.setText(_tr('连续试听已就绪；点击播放可试听整曲。'))
        _set_role(self.status, 'success')
        self._sync_controls()

    def _native_parameters_applied(self, key):
        self._set_preview_selection(key)
        self._preview_actual_key = key
        self._preview_selection_pending_key = self._preview_selection_restore_key = None
        self.preview_before_state.setText(_tr('已就绪'))
        self.preview_after_state.setText(_tr('已就绪'))
        self._set_audition_diagnostic('updated', _tr('连续试听 · 已更新'))
        if self._preview_play_intent:
            self.status.setText(_tr('正在试听：') + (_tr('处理前') if key == 'before' else _tr('处理后')))
            _set_role(self.status, 'success')

    def _native_preview_telemetry(self, snapshot):
        data = snapshot.get('telemetry')
        if not isinstance(data, dict) or data.get('schema') != 'vwa.audio_mixdown.realtime-telemetry/v1':
            return
        if data.get('status') != 'ready':
            if self._live_measurement_frame is not None:
                self._live_measurement_frame = None
                self._clear_render_meters()
                self.eq_curve.clear_live_spectrum()
            return
        identity = (self._player._generation, data.get('history_epoch'), data.get('media_frame'),
                    data.get('parameter_revision'))
        if identity != self._live_measurement_frame:
            self._live_measurement_frame = identity
            self._sync_eq_sample_rate(data)
            self.meter_feed.set_live_snapshot(data)
            spectrum = data.get('spectrum')
            if spectrum and spectrum.get('media_frame') is not None:
                self.eq_curve.set_live_spectrum(spectrum)
            else:
                self.eq_curve.clear_live_spectrum()
        # Background IR measurements may arrive while paused at the same
        # media frame. They still own a fresh display update.
        compressor = (data.get('dynamics') or {}).get('compressor') or {}
        limiter = (data.get('dynamics') or {}).get('limiter') or {}
        def db(value):
            return '—' if value is None else f'{float(value):.1f}'
        self.gr_summary.setText(_tr('压缩 GR') + ' ' + db(compressor.get('gr_db')) +
                               ' dB · ' + _tr('限制 GR') + ' ' + db(limiter.get('gr_db')) + ' dB')
        self.gr_summary.setToolTip(_tr('由对应处理前后音频块的 RMS 差估计；歌声共享同一个压缩总线。'))
        reverb = data.get('reverb') or {}
        fraction = reverb.get('measured_wet_fraction')
        wet = '—' if fraction is None else f'{float(fraction)*100:.1f}%'
        self.reverb_live_values.setText(
            _tr('发送 RMS') + ' ' + db(reverb.get('send_rms_dbfs')) + ' dBFS · ' +
            _tr('返回 RMS') + ' ' + db(reverb.get('return_rms_dbfs')) + ' dBFS\n' +
            _tr('湿声分量能量占比') + ' ' + wet)
        edc = reverb.get('kernel_edc') or {}
        if edc.get('available') and edc.get('measured'):
            edc_key = (edc.get('profile'), edc.get('predelay_ms'), edc.get('sample_rate'),
                       edc.get('signature'), self._player._generation)
            if edc_key != getattr(self, '_shown_edc_key', None):
                self.reverb_decay.set_report({'reverb_edc': edc}, merge=False)
                self._shown_edc_key = edc_key
            self.reverb_decay_help.setText(_tr('当前空间的真实单位脉冲衰减；发送与返回电平随歌曲变化。'))
        else:
            self._shown_edc_key = None
            self.reverb_decay.set_report(None)
            self.reverb_decay_help.setText(_tr('脉冲衰减未测量；发送与返回电平来自当前音频。'))
        native = snapshot.get('native') or {}
        self.audition_diagnostics.setText(
            _tr('连续试听') + f" · {float(data['sample_rate'])/1000:g} kHz · " +
            _tr('欠载') + f" {int(native.get('underrun_frames', 0))}")

    def _native_preview_error(self, message):
        self._preview_play_intent = self._preview_autoplay = False
        self._preview_dirty = True
        if self._pending_player_seek:
            self._fail_player_seek()
        self._set_audition_diagnostic('failed', _tr('连续试听已停止'))
        self.eq_curve.clear_live_spectrum()
        self._clear_render_meters()
        self.status.setText(_tr('连续试听失败：') + str(message) + _tr('；点击播放重试。'))
        _set_role(self.status, 'error')
        self._sync_controls()

    def _connect_preview_player(self, player, *, connect=True):
        bindings = (('positionChanged', self._player_position_changed),
                    ('durationChanged', self._player_duration_changed),
                    ('stateChanged', self._player_state_changed),
                    ('mediaStatusChanged', self._player_media_status_changed),
                    ('sourceChanged', self._player_source_changed),
                    ('errorOccurred', self._player_error))
        for name, callback in bindings:
            signal = getattr(player, name)
            (signal.connect if connect else signal.disconnect)(callback)

    def _player_source_changed(self, _generation):
        self._live_measurement_frame = None
        self.eq_curve.clear_spectrum_envelope()
        self.meter_feed.clear()


    def _install_mixdown_shortcuts(self):
        """Install discoverable transport shortcuts without stealing typing."""
        self._mixdown_shortcuts = []
        bindings = (
            ('Alt+1', lambda: self._shortcut_select_analysis_tab(0)),
            ('Alt+2', lambda: self._shortcut_select_analysis_tab(1)),
            ('Alt+3', lambda: self._shortcut_select_analysis_tab(2)),
            ('Ctrl+Shift+A', lambda: self._shortcut_preview('before')),
            ('Ctrl+Shift+B', lambda: self._shortcut_preview('after')),
            ('Ctrl+Shift+L', self._shortcut_toggle_loop),
        )
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(callback)
            self._mixdown_shortcuts.append(shortcut)
        self.analysis_tabs.setTabToolTip(0, _tr('快捷键 Alt+1：EQ 曲线'))
        self.analysis_tabs.setTabToolTip(1, _tr('快捷键 Alt+2：压缩 GR'))
        self.analysis_tabs.setTabToolTip(2, _tr('快捷键 Alt+3：混响空间'))
        self.preview_before_button.setToolTip(_tr('快捷键 Ctrl+Shift+A：切换到处理前'))
        self.preview_after_button.setToolTip(_tr('快捷键 Ctrl+Shift+B：切换到处理后'))
        self.player_loop_button.setToolTip(_tr('快捷键 Ctrl+Shift+L：切换循环'))
    def _shortcut_select_analysis_tab(self, index):
        if self.analysis_tabs.isVisible():
            self.analysis_tabs.setCurrentIndex(index)

    def _shortcut_preview(self, key):
        if self._support_ready and self._sources_ready():
            self.play_preview(key)

    def _shortcut_toggle_loop(self):
        if self.player_loop_button.isEnabled():
            self.player_loop_button.setChecked(not self.player_loop_button.isChecked())

    def _set_preview_loop(self, checked):
        self._player_loop = bool(checked)
        if self._player is not None:
            self._player.set_loop(self._player_loop)

    def _player_has_media(self):
        return bool(self._player is not None and self._player.ready)

    def _player_is_playing(self):
        if self._player is None:
            return False
        try:
            return int(self._player.state()) == PlaybackState.PlayingState
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    def _set_preview_selection(self, key):
        if key not in ('before', 'after'):
            return False
        self._player_key = key
        self.preview_before_button.setChecked(key == 'before')
        self.preview_after_button.setChecked(key == 'after')
        return True


    @staticmethod
    def _format_player_time(milliseconds):
        seconds = max(0, int(milliseconds or 0) // 1000)
        return '{:02d}:{:02d}'.format(seconds // 60, seconds % 60)

    def _absolute_player_seconds(self):
        pending = getattr(self, '_pending_player_seek', None)
        if pending:
            return pending['target_ms'] / 1000.0
        if self._player is not None and self._player_has_media():
            return (self._preview_segment_start_seconds
                    + max(0, int(self._player.position())) / 1000.0)
        if self._preview_resume_seconds is not None:
            return float(self._preview_resume_seconds)
        return float(self.preview_start.value())

    def _preview_is_playing(self):
        return self._player_is_playing()


    def _update_player_time(self):
        base_ms = int(round(self._preview_segment_start_seconds * 1000))
        position = (self.player_position.value() if (
                    self._player_seeking or getattr(self, '_pending_player_seek', None)) else
                    base_ms + (self._player.position() if self._player is not None else 0))
        duration_ms = self._preview_total_milliseconds()
        self.player_time.setText(
            self._format_player_time(max(0, int(position)))
            + ' / ' + self._format_player_time(duration_ms))

    def _preview_total_milliseconds(self):
        return self._player.duration() if self._player is not None else 0

    def _player_position_changed(self, position):
        if self._player_seeking or self._player._pending_seek:
            return
        position = max(0, int(self._player.position()))
        seeking = self._pending_player_seek
        if seeking:
            if seeking['phase'] not in ('seeking', 'loading'):
                return
            seeking.update(phase='following', confirmed_at=time.monotonic(),
                           clock_quality='native_callback_submitted_media_not_acoustic')
            seeking['elapsed_ms'] = round((seeking['confirmed_at']-seeking['requested_at'])*1000, 3)
            self._last_player_seek = dict(seeking)
            self._pending_player_seek = None
        blocked = self.player_position.blockSignals(True)
        self.player_position.setRange(0, self._preview_total_milliseconds())
        self.player_position.setValue(position)
        self.player_position.blockSignals(blocked)
        self._preview_resume_seconds = position / 1000.0
        self._update_player_time()


    def _player_duration_changed(self, duration):
        if self._player_seeking or getattr(self, '_pending_player_seek', None):
            # A retiring segment must not shrink the user's global timeline.
            return
        if duration > 0:
            self._preview_segment_duration_seconds = int(duration) / 1000.0
        self._player_duration_ms = max(0, int(duration))
        blocked = self.player_position.blockSignals(True)
        try:
            self.player_position.setRange(
                0, self._preview_total_milliseconds())
        finally:
            self.player_position.blockSignals(blocked)
        self._update_player_time()

    def _player_state_changed(self, state):
        playing = self._player is not None and int(state) == PlaybackState.PlayingState
        if getattr(self, '_pending_player_seek', None):
            playing = bool(self._preview_play_intent)
        self.player_toggle_button.setText(_tr('暂停') if playing else _tr('播放'))
        self.player_toggle_button.setAccessibleName(
            _tr('暂停试听') if playing else _tr('播放试听'))
        if not self._audition_closing:
            if playing:
                key = self._native_preview_player.selected_key
                self.status.setText(_tr('正在试听：') + (_tr('处理前') if key == 'before' else _tr('处理后')))
                _set_role(self.status, 'success')
            elif int(state) == PlaybackState.PausedState and not self._preview_play_intent:
                self.status.setText(_tr('试听已暂停；可拖动进度条定位。'))
                _set_role(self.status, 'muted')

    def _player_media_status_changed(self, status):
        if status == MediaStatus.EndOfMedia and not self._player_loop:
            self._preview_play_intent = self._preview_autoplay = False
            self.player_toggle_button.setText(_tr('播放'))
            self.status.setText(_tr('已播放到曲尾。'))
        self._sync_controls()

    def _player_error(self, message):
        self._native_preview_error(message)


    def _seek_player(self, position):
        return self._player.seek(max(0, int(position))) if self._player else False

    def _begin_player_seek(self):
        self._player_seek_timer.stop()
        self._player_seeking = True
        self._player_seek_serial += 1

    def _player_position_value_changed(self, position):
        # Mouse drags are handled by sliderMoved; keyboard and wheel changes
        # only emit valueChanged. Programmatic playback updates are blocked.
        if not self._player_seeking and self._player_has_media():
            self._stage_player_seek(position, phase='queued')
            self._player_seek_timer.start()

    def _seek_player_preview(self, position):
        # The gesture owns the displayed cursor. Never reset/start a device
        # from sliderMoved; the release commits exactly one final location.
        self._update_player_time()

    def _stage_player_seek(self, position, *, phase):
        previous = self._pending_player_seek
        autoplay = bool(self._preview_play_intent)
        if not previous:
            self._preview_play_intent = autoplay
        self._player_seek_serial += 1
        target = max(0, min(int(position), self._preview_total_milliseconds()))
        self._pending_player_seek = {
            'seek_id': self._player_seek_serial, 'target_ms': target,
            'phase': phase, 'autoplay': autoplay, 'requested_at': time.monotonic(),
        }
        blocked = self.player_position.blockSignals(True)
        try:
            self.player_position.setValue(target)
        finally:
            self.player_position.blockSignals(blocked)
        self._preview_resume_seconds = target / 1000.0
        self._update_player_time()
        return self._pending_player_seek

    def _commit_queued_player_seek(self):
        pending = self._pending_player_seek
        if pending and pending['phase'] == 'queued' and not self._player_seeking:
            self._seek_to_song_position(pending['target_ms'], commit=True)

    def _cancel_player_seek_gesture(self):
        self._player_seeking = False
        if self._pending_player_seek:
            self._seek_to_song_position(self._pending_player_seek['target_ms'], commit=True)
        elif self._player is not None:
            self._player_position_changed(self._player.position())
            if self._preview_dirty:
                self._schedule_preview_refresh(autoplay=self._preview_play_intent)

    def _fail_player_seek(self):
        pending = getattr(self, '_pending_player_seek', None)
        if pending:
            pending.update(phase='failed', failed_at=time.monotonic(), autoplay=False)
            self._last_player_seek = dict(pending)
            self._preview_play_intent = self._preview_autoplay = False
            self._player_state_changed(PlaybackState.PausedState)

    def _seek_to_song_position(self, position, *, commit):
        if self._player is None or not commit or not self._player.ready:
            return
        self._player_seek_timer.stop()
        pending = self._pending_player_seek
        if not pending or pending['target_ms'] != int(position):
            pending = self._stage_player_seek(position, phase='seeking')
        pending.update(phase='seeking', relative_ms=pending['target_ms'], submitted_at=time.monotonic())
        if not self._player.seek(pending['target_ms'], autoplay=pending['autoplay']):
            self._fail_player_seek()
        self._update_player_time()

    def _finish_player_seek(self):
        position = max(0, int(self.player_position.value()))
        self._player_seeking = False
        self._seek_to_song_position(position, commit=True)

    def _stop_preview_player(self):
        """Release a session only for source retirement, cancellation or shutdown."""
        self._player_seek_timer.stop()
        self._pending_player_seek = None
        self._player_seeking = False
        self._preview_play_intent = self._preview_autoplay = False
        if self._player is not None:
            self._player.release()
        self._native_source_key = None
        self._preview_resume_seconds = None
        self._player_duration_ms = 0
        self._preview_dirty = True
        self._live_measurement_frame = None
        self.eq_curve.clear_spectrum_envelope()
        self._clear_render_meters()
        blocked = self.player_position.blockSignals(True)
        self.player_position.setSliderDown(False)
        self.player_position.setRange(0, 0)
        self.player_position.blockSignals(blocked)
        self.player_toggle_button.setText(_tr('播放'))
        self._update_player_time()


    def toggle_preview_playback(self):
        if not self._sources_ready() or not self._support_ready or not self._player.available:
            self.status.setText(_tr('选择声音并等待连续试听组件就绪后即可播放。'))
            return
        self._preview_play_intent = not self._preview_play_intent
        self._preview_autoplay = self._preview_play_intent
        if self._pending_player_seek:
            self._pending_player_seek['autoplay'] = self._preview_play_intent
        if not self._preview_play_intent:
            self._player.pause()
        elif self._player.ready and not self._preview_dirty:
            self._player.play()
        else:
            self._start_native_preview()
        self._player_state_changed(self._player.state())

    def _request_preview_playback(self):
        self._preview_play_intent = self._preview_autoplay = True
        return self._start_native_preview()

    def play_preview(self, key):
        if key not in ('before', 'after'):
            return False
        self._native_requested_key = key
        self._preview_play_intent = self._preview_autoplay = True
        if self._pending_player_seek and self._pending_player_seek['phase'] == 'queued':
            self._commit_queued_player_seek()
        result = self._start_native_preview()
        if self._player.ready:
            self._player.play()
        self._sync_controls()
        return result

    def preview_busy(self):
        return bool(self._native_preview_player is not None and self._native_preview_player.loading)

    def _current_preview_source_key(self):
        try:
            vocals, instrumental = self._validated_sources()
            paths = [Path(row['path']).resolve() for row in vocals] + [instrumental]
            return tuple(
                (_normal_path_key(path), path.stat().st_size, path.stat().st_mtime_ns)
                for path in paths
            )
        except (OSError, ValueError):
            return None


    def _ensure_preview_cache_directory(self):
        if self._preview_cache_directory is None:
            self._preview_cache_directory = Path(tempfile.mkdtemp(prefix=_PREVIEW_CACHE_PREFIX))
        return self._preview_cache_directory

    def _clear_preview_cache(self):
        if not self._audition_cleanup_complete:
            return
        directory, self._preview_cache_directory = self._preview_cache_directory, None
        _discard_preview_directory(directory)

    def _schedule_preview_refresh(self, *, autoplay=False, immediate=False, message=None):
        if not self._ui_ready or self._audition_closing:
            return False
        self._preview_dirty = True
        if not self._sources_ready() or not self._support_ready:
            if self._native_source_key is not None:
                self._stop_preview_player()
            return False
        if autoplay:
            self._preview_play_intent = self._preview_autoplay = True
        if not self._preview_debounce.isActive() or immediate:
            self._preview_debounce.start(0 if immediate else 60)
        return True


    def cancel_preview(self):
        self._preview_debounce.stop()
        self._preview_pending = False


    def _set_audition_diagnostic(self, state, text, latency_ms=None):
        self._audition_diagnostic_state = str(state)
        suffix = "" if latency_ms is None else " · %d ms" % int(round(float(latency_ms)))
        self.audition_diagnostics.setText(str(text) + suffix)
        self.audition_diagnostics.setToolTip(self.audition_diagnostics.text())
        self.audition_diagnostics.setProperty("auditionState", state)
        self.audition_diagnostics.style().unpolish(self.audition_diagnostics)
        self.audition_diagnostics.style().polish(self.audition_diagnostics)
        _set_role(self.audition_diagnostics, "error" if state == "failed" else ("success" if state == "updated" else "muted"))

    def _ensure_audition_service_started(self):
        if self._audition_closing or self._player is None:
            return False
        if not self._audition_poll_timer.isActive():
            self._audition_poll_timer.start()
        return True


    def _poll_audition_service(self):
        if self._player is not None and not self._audition_closing:
            self._player.poll()


    def _start_live_preview(self):
        return self._start_native_preview()


    def _shutdown_audition_service(self):
        if self._audition_cleanup_complete or self._audition_close_thread is not None:
            return
        self._audition_closing = True
        self._audition_poll_timer.stop()
        self._preview_debounce.stop()
        service = self._audition_service
        if service is None:
            self._audition_cleanup_complete = True
            self._finish_pending_dialog_close()
            return
        self._audition_close_error = None
        def close_service():
            try:
                service.close(timeout=3.0)
            except Exception as error:
                self._audition_close_error = str(error)
        self._audition_close_thread = threading.Thread(
            target=close_service, name='mixdown-session-close', daemon=True)
        self._audition_close_thread.start()
        self._audition_close_watch.start()

    def _finish_pending_dialog_close(self):
        thread = self._audition_close_thread
        if thread is not None and thread.is_alive():
            return
        self._audition_cleanup_complete = True
        self._audition_close_watch.stop()
        QTimer.singleShot(0, self._complete_pending_dialog_close)

    def _complete_pending_dialog_close(self):
        pending_result, self._pending_done_result = self._pending_done_result, None
        if pending_result is not None:
            self._retire_preview()
            super().done(pending_result)
            return
        if self._close_finalize_pending:
            self._close_finalize_pending = False
            self.close()

    def install_dependency(self):
        if self.busy():
            return
        if not self._component_installer or not self._component_installer.is_file():
            self.support_state.setText(_tr('当前版本没有可用的混音组件安装器；请稍后重新检查。'))
            _set_role(self.support_state, 'error')
            return
        root, installer = self._component_root, self._component_installer
        force = bool(self._component_packages)

        def run(check):
            return _run_component_installer('install', root, installer, check, force=force)

        self.support_panel.show()
        self.support_state.setText(_tr('正在下载、校验并安装混音组件…'))
        _set_role(self.support_state, 'muted')
        self.status.setText(_tr('正在后台安装混音组件；可在底栏取消。'))
        _set_role(self.status, 'muted')
        self._launch('component', run)

    def _legacy_eq_changed(self, *args):
        if self._syncing or not hasattr(self, '_eq_bands'):
            return
        bands = [dict(band) for band in self._eq_bands]
        for band in bands:
            if band['type'] == 'highpass':
                band['freq_hz'] = float(self.low_cut.value())
            elif band['type'] == 'bell':
                band['gain_db'] = float(self.presence.value())
        self._apply_eq_bands(bands, trigger=True)
        self.parameters_changed()

    def _eq_bands_changed(self, bands):
        if self._syncing:
            return
        self._apply_eq_bands(bands, trigger=True)
        self.parameters_changed()

    def _apply_eq_bands(self, bands, *, trigger=False):
        try:
            normalized = normalize_eq_bands(bands)
            sample_rate = (self.eq_curve.sample_rate()
                           if hasattr(self, 'eq_curve') else None)
            if normalized:
                normalized = [
                    project_eq_band(band, sample_rate=sample_rate)
                    for band in normalized
                ]
        except (TypeError, ValueError) as error:
            self.status.setText(_tr('EQ 参数无效：') + str(error))
            _set_role(self.status, 'error')
            return False
        if not normalized:
            normalized = default_bands()
        self._eq_bands = normalized
        previous = self._syncing
        self._syncing = True
        try:
            highpass = next((band for band in normalized
                             if band['type'] == 'highpass'), None)
            bell = next((band for band in normalized
                         if band['type'] == 'bell'), None)
            if highpass is not None:
                self.low_cut.setValue(float(highpass['freq_hz']))
            if bell is not None:
                self.presence.setValue(float(bell['gain_db']))
            if hasattr(self, 'eq_curve'):
                self.eq_curve.set_bands(normalized)
            if hasattr(self, 'eq_band_controls'):
                self.eq_band_controls.set_bands(normalized, emit=False)
        finally:
            self._syncing = previous
        if trigger:
            self._active_preset_key = None
            if hasattr(self, 'preset_bar'):
                self.preset_bar.set_current_key('custom')
        return True

    def _analysis_fold_changed(self, checked=False):
        expanded = bool(self.eq_precision_toggle.isChecked() or self.advanced_toggle.isChecked())
        self.analysis_tabs.setMinimumHeight(460 if expanded else 390)
        self.analysis_tabs.setMaximumHeight(16777215)

    def _analysis_page_changed(self, index):
        # Hidden analysis pages must not reserve their controls' height in EQ.
        for page_index in range(self.analysis_tabs.count()):
            page = self.analysis_tabs.widget(page_index)
            policy = QSizePolicy.Expanding if page_index == index else QSizePolicy.Ignored
            page.setSizePolicy(policy, policy)
        self.analysis_tabs.updateGeometry()

    def _expand_curve_workspace(self, checked):
        self.workspace.set_curve_expanded(checked)
        self.expand_curve_button.setText(_tr('返回混音台') if checked else _tr('放大曲线'))
        if checked:
            QTimer.singleShot(0, lambda: self._main_scroll.ensureWidgetVisible(self.eq_curve, 0, 0))

    def _refresh_analysis_palette(self):
        apply_palette = getattr(
            _desktop_theme, 'apply_mixdown_analysis_palette', None)
        if not callable(apply_palette):
            return False
        app = QApplication.instance()
        mode = app.property('vwa-theme') if app is not None else 'light'
        material = app.property('vwa-material') if app is not None else 'solid'
        apply_palette(self, mode or 'light', material or 'solid')
        return True

    def _sync_processing_visual(self):
        if hasattr(self, 'eq_curve'):
            self.eq_curve.setEnabled(self.eq_enabled.isChecked() and not self.busy())

    def _sync_send_controls(self):
        if not hasattr(self, 'send_controls'):
            return
        tracks = []
        for track in self.active_vocal_tracks():
            number = track['index'] + 1
            path = self._path(track['field_key'])
            tracks.append({
                'label': 'V%d' % number,
                'display_name': ('V%d · %s' % (number, path.stem)) if path else 'V%d' % number,
                'reverb_send': max(0.0, min(1.0, float(track.get('reverb_send', 1.0)))),
                'enabled': path is not None and not self.busy(),
            })
        self.send_controls.update_tracks(tracks or [{'label': 'V1', 'reverb_send': 1.0}])

    def _reverb_send_changed(self, index, percent):
        tracks = self.active_vocal_tracks()
        if not 0 <= int(index) < len(tracks):
            return
        tracks[int(index)]['reverb_send'] = max(0.0, min(1.0, float(percent) / 100.0))
        if not self._syncing:
            self.parameters_changed()

    def _library_for_mixing(self):
        if self._mix_preset_library is None:
            from preset_library import PresetLibrary
            self._mix_preset_library = PresetLibrary()
        return self._mix_preset_library

    def _mix_preset_state(self):
        return {'processing': {
            'apply_scope': 'vocal_processing_and_output_width',
            'eq_enabled': self.eq_enabled.isChecked(),
            'eq_bands': copy.deepcopy(self._eq_bands),
            'compression_enabled': self.compression_enabled.isChecked(),
            'compression_strength': float(self.compression.value()) / 100,
            'reverb_enabled': self.reverb_enabled.isChecked(),
            'reverb_profile': self.reverb_profile.currentData(),
            'reverb_mix': float(self.reverb.value()) / 100,
            'reverb_predelay_ms': self.reverb_predelay.value(),
            'width': self.width_control.value(),
        }, 'tracks': [{'pan': track['pan'].value() / 100,
                       'reverb_send': float(track.get('reverb_send', 1.0))}
                      for track in self.active_vocal_tracks()]}

    def _mix_preset_stamp(self):
        # The protected context never goes into a portable document.
        return {'state': self._mix_preset_state(),
                'paths': {k: v.text() for k, v in self.fields.items()},
                'balance': [(t['gain_db'].value(), t['offset'].value(),
                             t['mute'].isChecked(), t['solo'].isChecked())
                            for t in self.active_vocal_tracks()],
                'backing': (self.instrumental_db.value(), self.instrumental_pan.value(),
                            self.instrumental_mute.isChecked(), self.instrumental_solo.isChecked()),
                'sample_rate': self.eq_curve.sample_rate(), 'job': str(self.job)}

    def _write_mix_preset_state(self, state):
        payload = state['processing']
        previous = self._syncing
        self._syncing = True
        try:
            if not self._apply_eq_bands(payload['eq_bands']):
                raise ValueError('EQ parameters could not be applied')
            self.eq_enabled.setChecked(payload['eq_enabled'])
            self.compression.setValue(payload['compression_strength'] * 100)
            self.compression_enabled.setChecked(payload['compression_enabled'])
            self.reverb.setValue(payload['reverb_mix'] * 100)
            self.reverb_enabled.setChecked(payload['reverb_enabled'])
            self.reverb_profile.setCurrentIndex(self.reverb_profile.findData(payload['reverb_profile']))
            self.reverb_predelay.setValue(int(payload['reverb_predelay_ms']))
            self.width_control.set_value(payload['width'], emit=False)
            for track, values in zip(self.active_vocal_tracks(), state['tracks'], strict=True):
                track['pan'].setValue(int(round(values['pan'] * 100)))
                track['reverb_send'] = values['reverb_send']
            self._sync_send_controls()
        finally:
            self._syncing = previous

    def _commit_mix_preset(self, plan, expected_stamp, *, key=None):
        from mixdown_preset_documents import plan_application
        from preset_documents import PresetError
        if self.busy() or self._mix_preset_stamp() != expected_stamp:
            raise PresetError('target_changed', '混音参数或输入已变化，请重新预览。')
        # Rebuild from the validated source and scope, not an editable UI table.
        plan = plan_application(plan['document'], expected_stamp['state'],
                                sample_rate=expected_stamp['sample_rate'],
                                include_width=plan['include_width'],
                                include_tracks=plan['include_tracks'])
        before = self._mix_preset_state()
        old_key = self._active_preset_key
        try:
            self._write_mix_preset_state(plan['after'])
            if self._mix_preset_state() != plan['after']:
                raise PresetError('precision_mismatch', '界面实际参数与预览不一致，本次应用已取消。')
        except Exception:
            self._write_mix_preset_state(before)
            raise
        self._active_preset_key = key
        self.preset_bar.set_current_key(key or 'custom')
        # Existing parameter notification invalidates result/prefetch caches
        # once, using the same audition and export request path as manual edits.
        self.parameters_changed()
        self._mix_preset_undo = {'before': before, 'after': self._mix_preset_stamp(),
                                 'key': old_key}
        self._sync_controls()
        return True

    def _preset_error(self, error):
        self.status.setText(_tr('预设操作未完成：') + _tr(str(error)))
        _set_role(self.status, 'error')
        return False

    def save_mix_preset(self):
        if self.busy():
            return False
        from mixdown_preset_ui import MixPresetSaveDialog
        from preset_documents import PresetError
        try:
            stamp = self._mix_preset_stamp()
            library = self._library_for_mixing()
            revision = library.snapshot()['revision']
            dialog = MixPresetSaveDialog(stamp['state']['processing'], self)
            if dialog.exec_() != QDialog.Accepted:
                return False
            if self._mix_preset_stamp() != stamp or self.busy():
                raise PresetError('target_changed', '混音参数或输入已变化，请重新预览。')
            result = library.put_document(dialog.document, expected_revision=revision)
            self.status.setText(_tr('已附加保存预设“{0}”。', result['entry']['name']))
            _set_role(self.status, 'success')
            return True
        except (ValueError, OSError) as error:
            return self._preset_error(error)

    def choose_mix_preset(self):
        if self.busy():
            return False
        from mixdown_preset_ui import MixPresetLibraryDialog
        from preset_documents import PresetError
        try:
            stamp = self._mix_preset_stamp()
            library = self._library_for_mixing()
            def manage(parent=None):
                from preset_library_ui import PresetLibraryManagerDialog
                PresetLibraryManagerDialog(parent or self, library=library).exec_()
            dialog = MixPresetLibraryDialog(library, stamp['state'],
                                           sample_rate=stamp['sample_rate'], parent=self,
                                           manage=manage)
            if dialog.exec_() != QDialog.Accepted:
                return False
            selected = dialog.selected_entry
            current = next((entry for entry in library.snapshot()['entries']
                            if (entry['id'], entry['version']) ==
                               (selected['id'], selected['version'])), None)
            if current is None or current['sha256'] != selected['sha256']:
                raise PresetError('preset_changed', '所选预设已变化，请刷新后重新预览。')
            self._commit_mix_preset(dialog.plan, stamp)
            self.status.setText(_tr('已应用库预设“{0}”；可撤销本次应用。', selected['name']))
            _set_role(self.status, 'success')
            return True
        except (ValueError, OSError) as error:
            return self._preset_error(error)

    def undo_mix_preset(self):
        undo = self._mix_preset_undo
        if not undo or self.busy():
            return False
        if self._mix_preset_stamp() != undo['after']:
            self._mix_preset_undo = None
            self._sync_controls()
            return self._preset_error(ValueError(_tr('混音参数或输入已变化，请重新预览。')))
        self._write_mix_preset_state(undo['before'])
        self._active_preset_key = undo['key']
        self.preset_bar.set_current_key(undo['key'] or 'custom')
        self.parameters_changed()
        self.status.setText(_tr('已撤销本次预设应用。'))
        _set_role(self.status, 'success')
        return True

    def apply_mix_preset(self, key, lock_mix=True):
        # Retain the programmatic legacy entry while sharing actual application.
        if key == 'custom':
            self.status.setText(_tr('当前使用自定义参数；请选择一套起点预设。'))
            _set_role(self.status, 'muted')
            return False
        from mixdown_preset_documents import plan_application
        from preset_builtin_documents import builtin_documents
        try:
            preset = resolve_preset(key)
            document = next(d for d in builtin_documents()
                            if d['id'] == 'builtin.mixdown.' + preset['key'])
            stamp = self._mix_preset_stamp()
            plan = plan_application(document, stamp['state'], sample_rate=stamp['sample_rate'],
                                    include_width=True, include_tracks=not lock_mix)
            self._commit_mix_preset(plan, stamp, key=preset['key'])
        except (TypeError, ValueError, OSError) as error:
            return self._preset_error(error)
        self.status.setText(
            _tr('已应用预设“{0}”').format(_tr(preset['label'])) +
            (_tr('；已锁定当前音量、声像和发送。') if lock_mix else _tr('；已同时应用声像与发送建议。')))
        _set_role(self.status, 'success')
        return True

    def _sync_processing_controls(self):
        active = self.busy()
        self.low_cut.setEnabled(not active and self.eq_enabled.isChecked())
        self.presence.setEnabled(not active and self.eq_enabled.isChecked())
        self.compression.setEnabled(not active and self.compression_enabled.isChecked())
        self.reverb.setEnabled(not active and self.reverb_enabled.isChecked())
        self.reverb_profile.setEnabled(not active and self.reverb_enabled.isChecked())
        self.reverb_predelay.setEnabled(not active and self.reverb_enabled.isChecked())
        self.eq_band_controls.setEnabled(not active and self.eq_enabled.isChecked())
        self._sync_processing_visual()

    def _source_ready(self, key):
        path = self._path(key)
        optional_vocal = key.startswith('vocal_')
        if path is None:
            return optional_vocal
        if not path.is_file():
            return False
        return key == 'instrumental' or path.suffix.lower() == '.wav'

    def _sources_ready(self):
        try:
            self._validated_sources()
        except (OSError, ValueError):
            return False
        return True

    def _active_source_paths(self):
        paths = [self._path(track['field_key']) for track in self.active_vocal_tracks()]
        paths.append(self._path('instrumental'))
        return [path for path in paths if path is not None]

    def _output_ready(self):
        output = self._path('output')
        if not output or output.suffix.lower() != '.wav' or output.exists():
            return False
        return not any(_same_path(output, path) for path in self._active_source_paths())

    def _set_channel_flags(self, panel, *, muted=False, solo=False, filtered=False):
        changed = False
        for name, value in (('channelMuted', bool(muted)),
                            ('channelSolo', bool(solo)),
                            ('channelFiltered', bool(filtered))):
            if panel.property(name) != value:
                panel.setProperty(name, value)
                changed = True
        if changed:
            panel.style().unpolish(panel)
            panel.style().polish(panel)
            panel.update()

    def _reset_route_controls(self, track=None):
        previous = self._syncing
        self._syncing = True
        try:
            if track is None:
                controls = (self.instrumental_mute, self.instrumental_solo,
                            self.instrumental_pan)
            else:
                controls = (track['mute'], track['solo'], track['pan'])
            controls[0].setChecked(False)
            controls[1].setChecked(False)
            controls[2].setValue(0)
        finally:
            self._syncing = previous

    def _update_source_states(self):
        filled = self._filled_vocal_tracks()
        backing_path = self._path('instrumental')
        valid_filled = [
            track for track in filled
            if self._path(track['field_key']).is_file()
            and self._path(track['field_key']).suffix.lower() == '.wav'
        ]
        backing_valid = bool(
            backing_path and backing_path.is_file())
        any_solo = (
            any(track['solo'].isChecked() and not track['mute'].isChecked()
                for track in valid_filled)
            or bool(backing_valid
                    and self.instrumental_solo.isChecked()
                    and not self.instrumental_mute.isChecked())
        )
        for track in self.active_vocal_tracks():
            number = track['index'] + 1
            path = self._path(track['field_key'])
            state = track['state']
            if not path:
                self._reset_route_controls(track)
                track['file_label'].setText(
                    _tr('尚未选择') if number == 1 else _tr('未填写（可选）'))
                state.setText('WAV')
                _set_role(state, 'muted')
                self._set_channel_flags(track['panel'])
                continue
            track['file_label'].setText(path.name)
            valid_wav = path.is_file() and path.suffix.lower() == '.wav'
            muted = track['mute'].isChecked()
            solo = track['solo'].isChecked()
            filtered = valid_wav and not muted and any_solo and not solo
            if not path.is_file():
                state.setText(_tr('文件缺失'))
                _set_role(state, 'error')
            elif not valid_wav:
                state.setText(_tr('需要 WAV'))
                _set_role(state, 'error')
            elif muted:
                state.setText(_tr('静音'))
                _set_role(state, 'muted')
            elif solo:
                state.setText(_tr('独奏'))
                _set_role(state, 'success')
            elif filtered:
                state.setText(_tr('其他轨独奏中'))
                _set_role(state, 'muted')
            else:
                state.setText(_tr('已就绪'))
                _set_role(state, 'success')
            self._set_channel_flags(
                track['panel'], muted=valid_wav and muted,
                solo=valid_wav and solo, filtered=filtered)

        state = self.source_states['instrumental']
        if not backing_path:
            self._reset_route_controls()
            self.instrumental_file_label.setText(_tr('尚未选择'))
            state.setText(_tr('常见音频格式'))
            _set_role(state, 'muted')
            self._set_channel_flags(self.backing_panel)
            self._sync_eq_sample_rate_from_instrumental()
            return
        self.instrumental_file_label.setText(backing_path.name)
        if not backing_path.is_file():
            state.setText(_tr('文件缺失'))
            _set_role(state, 'error')
            self._set_channel_flags(self.backing_panel)
            return
        muted = self.instrumental_mute.isChecked()
        solo = self.instrumental_solo.isChecked()
        filtered = not muted and any_solo and not solo
        if muted:
            state.setText(_tr('静音'))
            _set_role(state, 'muted')
        elif solo:
            state.setText(_tr('独奏'))
            _set_role(state, 'success')
        elif filtered:
            state.setText(_tr('其他轨独奏中'))
            _set_role(state, 'muted')
        else:
            state.setText(_tr('已就绪'))
            _set_role(state, 'success')
        self._set_channel_flags(
            self.backing_panel,
            muted=muted, solo=solo, filtered=filtered)
        self._sync_eq_sample_rate_from_instrumental()

    def _update_output_state(self):
        output = self._path('output')
        if not output:
            message, role = _tr('尚未指定输出文件'), 'muted'
        elif output.suffix.lower() != '.wav':
            message, role = _tr('请使用 .wav 扩展名'), 'error'
        elif output.exists():
            message, role = _tr('文件已存在；导出会拒绝覆盖'), 'error'
        elif any(_same_path(output, path) for path in self._active_source_paths()):
            message, role = _tr('不能与源文件相同'), 'error'
        else:
            message, role = _tr('将创建新文件，不会覆盖已有输出'), 'success'
        self.output_state.setText(message)
        _set_role(self.output_state, role)

    def _sync_controls(self):
        active = self.busy()
        for widget in self._editable_widgets:
            widget.setEnabled(not active)
        if hasattr(self, 'undo_mix_preset_button'):
            self.undo_mix_preset_button.setEnabled(not active and self._mix_preset_undo is not None)
        self._sync_processing_controls()
        sources_ready = self._sources_ready()
        preview_ready = bool(not active and sources_ready and self._support_ready)
        has_segment = bool(preview_ready and self._player.ready)
        self.preview_before_button.setEnabled(preview_ready)
        self.preview_after_button.setEnabled(preview_ready)
        player_available = bool(
            self._player is not None
            and getattr(self._player, 'available', True))
        self.player_toggle_button.setEnabled(preview_ready and player_available)
        self.player_loop_button.setEnabled(has_segment and player_available)
        self.player_position.setEnabled(has_segment and player_available)
        self.export_button.setEnabled(not active and not self.preview_busy()
                                  and sources_ready and self._support_ready
                                  and self._output_ready())
        self.cancel_button.setVisible(active and self._busy_kind in ('export', 'component'))
        self.cancel_button.setEnabled(active and self._busy_kind in ('export', 'component')
                                       and not self.worker.cancel_event.is_set())
        if hasattr(self, 'install_dependency_button'):
            self.install_dependency_button.setEnabled(
                not active and not self._support_ready and bool(self._component_installer))
        self.progress.setVisible(active)
        self.progress.setRange(0, 0)
        exported = bool(self._result and not active)
        can_register = bool(exported and self.job and (self.job / 'request.json').is_file())
        self.register_button.setVisible(bool(self.job))
        self.register_button.setEnabled(can_register)
        self.to_video_button.setEnabled(exported)
        self.to_video_button.setText(
            _tr('登记并进入视频合成') if self.job else _tr('进入视频合成'))
        self.add_vocal_button.setEnabled(not active and self._vocal_track_count < _MAX_VOCAL_TRACKS)
        for track in self.active_vocal_tracks():
            track['remove'].setEnabled(not active)
            has_source = self._path(track['field_key']) is not None
            for control in (track['mute'], track['solo'], track['pan']):
                control.setEnabled(not active and has_source)
        backing_available = self._path('instrumental') is not None
        for control in (self.instrumental_mute, self.instrumental_solo,
                        self.instrumental_pan):
            control.setEnabled(not active and backing_available)
        # The close action stays available while busy so it can request cooperative
        # cancellation and wait for the owned thread to finish.
        self.close_button.setEnabled(True)

    def parameters_changed(self, *args):
        if self._syncing or self.busy():
            return
        self._mix_preset_undo = None
        had_result = self._result is not None
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._update_source_states()
        self._update_output_state()
        self._sync_send_controls()
        self._set_audition_diagnostic('updating', _tr('实时更新中'))
        if had_result:
            self.result_title.setText(_tr('参数已变化'))
            self.result_text.setText(_tr('请重新导出；磁盘上此前的成品不会被删除。'))
            _set_role(self.result_title, 'muted')
        self._schedule_preview_refresh(autoplay=self._preview_play_intent)
        self._sync_controls()

    def output_changed(self, *args):
        if self._syncing or self.busy():
            return
        had_result = self._result is not None
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._update_output_state()
        if had_result:
            self.result_title.setText(_tr('输出位置已变化'))
            self.result_text.setText(_tr('请重新导出；此前文件仍保留在原位置。'))
            _set_role(self.result_title, 'muted')
        self._sync_controls()

    def preview_parameters_changed(self, *args):
        if self._syncing or self.busy():
            return
        self._player.set_loop_range(int(self.preview_start.value()*1000),
                                   int((self.preview_start.value()+self.preview_duration.value())*1000))
        if self._player.ready:
            self._seek_to_song_position(int(self.preview_start.value()*1000), commit=True)

    def _choose_path(self, key):
        if self.busy():
            return
        current = self.fields[key].text().strip()
        if key == 'output':
            path, _ = QFileDialog.getSaveFileName(
                self, _tr('选择新 WAV 文件名'), current, _WAV_FILTER)
            if path and not path.lower().endswith('.wav'):
                path += '.wav'
        else:
            is_vocal = key == 'vocal' or key.startswith('vocal_')
            path, _ = QFileDialog.getOpenFileName(
                self, _tr('选择') + self.fields[key].accessibleName(), current,
                _VOCAL_FILTER if is_vocal else _INSTRUMENTAL_FILTER)
        if path:
            self.fields[key].setText(str(Path(path).expanduser().resolve()))

    def _path(self, key):
        text = self.fields[key].text().strip().strip('"')
        return Path(text).expanduser().resolve() if text else None

    def _filled_vocal_tracks(self):
        return [track for track in self.active_vocal_tracks()
                if self._path(track['field_key']) is not None]

    def _vocal_specs(self):
        specs = []
        for track in self._filled_vocal_tracks():
            path = self._path(track['field_key'])
            specs.append({
                'path': str(path),
                'gain_db': float(track['gain_db'].value()),
                'offset_seconds': float(track['offset'].value()),
                'mute': bool(track['mute'].isChecked()),
                'solo': bool(track['solo'].isChecked()),
                'pan': float(track['pan'].value()) / 100.0,
                'reverb_send': max(0.0, min(1.0, float(track.get('reverb_send', 1.0)))),
            })
        return specs

    def _validated_sources(self):
        vocals = self._vocal_specs()
        instrumental = self._path('instrumental')
        if not vocals:
            raise ValueError('请至少选择一路歌声干声 WAV。')
        if len(vocals) > _MAX_VOCAL_TRACKS:
            raise ValueError('最多只能混入 3 路歌声。')
        if not instrumental:
            raise ValueError('请选择伴奏 WAV 或常见音频文件。')
        for number, vocal in enumerate(vocals, 1):
            path = Path(vocal['path'])
            if not path.is_file():
                raise ValueError('第 {0} 路歌声文件不存在或不可读取。'.format(number))
            if path.suffix.lower() != '.wav':
                raise ValueError('第 {0} 路歌声必须选择 WAV 文件。'.format(number))
        if not instrumental.is_file():
            raise ValueError('伴奏文件不存在或不可读取。')
        vocal_keys = [_normal_path_key(item['path']) for item in vocals]
        if len(set(vocal_keys)) != len(vocal_keys):
            raise ValueError('同一歌声文件不能重复添加。')
        backing_key = _normal_path_key(instrumental)
        if backing_key in vocal_keys:
            raise ValueError('歌声与伴奏必须选择不同文件。')
        return vocals, instrumental

    def _validated_paths(self):
        vocals, instrumental = self._validated_sources()
        output = self._path('output')
        if not output:
            raise ValueError('请指定新的导出 WAV 文件名。')
        if output.suffix.lower() != '.wav':
            raise ValueError('导出文件必须使用 .wav 扩展名。')
        if output.exists():
            raise FileExistsError('导出目标已存在；请换一个文件名，现有文件不会被覆盖。')
        if any(_same_path(output, path) for path in
               [item['path'] for item in vocals] + [instrumental]):
            raise ValueError('导出文件不能与任一源文件相同。')
        return vocals, instrumental, output

    def _request(self, output, *, preview=False, bypass_processing=False):
        self._sync_eq_sample_rate_from_instrumental()
        use_eq = self.eq_enabled.isChecked() and not bypass_processing
        use_compression = self.compression_enabled.isChecked() and not bypass_processing
        use_reverb = self.reverb_enabled.isChecked() and not bypass_processing
        vocals = self._vocal_specs()
        eq_bands = normalize_eq_bands(self._eq_bands) or []
        sample_rate = (self.eq_curve.sample_rate()
                       if hasattr(self, 'eq_curve') else None)
        eq_bands = [
            project_eq_band(band, sample_rate=sample_rate)
            for band in eq_bands
        ]
        if not use_eq:
            eq_bands = [dict(band, enabled=False) for band in eq_bands]
        request = {
            'vocals': vocals,
            'instrumental_path': str(self._path('instrumental')),
            'output_path': str(Path(output).resolve()),
            'instrumental_db': float(self.instrumental_db.value()),
            'instrumental_mute': bool(self.instrumental_mute.isChecked()),
            'instrumental_solo': bool(self.instrumental_solo.isChecked()),
            'instrumental_pan': float(self.instrumental_pan.value()) / 100.0,
            'eq_low_cut_hz': float(self.low_cut.value()) if use_eq else 0.0,
            'eq_presence_db': float(self.presence.value()) if use_eq else 0.0,
            'eq_bands': eq_bands,
            'compression_strength': float(self.compression.value()) / 100.0 if use_compression else 0.0,
            'reverb_mix': float(self.reverb.value()) / 100.0 if use_reverb else 0.0,
            'reverb_profile': self.reverb_profile.currentData(),
            'reverb_predelay_ms': (float(self.reverb_predelay.value())
                                   if use_reverb else 0.0),
            'width': float(self.width_control.value()),
        }
        if preview:
            request['preview_start_seconds'] = float(self.preview_start.value())
            request['preview_duration_seconds'] = float(self.preview_duration.value())
        return request

    def _launch(self, kind, function, with_progress=False):
        if self.busy():
            self.status.setText(_tr('已有混音任务正在运行；请等待完成或先取消。'))
            _set_role(self.status, 'error')
            return False
        worker = MixdownTask(function, self, with_progress=with_progress)
        self.worker = worker
        self._busy_kind = kind
        worker.succeeded.connect(lambda result, kind=kind: self.succeeded(kind, result))
        worker.failed.connect(lambda text, kind=kind: self.failed(kind, text))
        worker.cancelled.connect(lambda kind=kind: self.cancelled(kind))
        worker.finished.connect(self.finished)
        if with_progress:
            worker.progressChanged.connect(self._apply_progress_event)
        self._sync_controls()
        worker.start()
        return True

    def _signatures_unchanged(self, expected):
        return all(Path(path).is_file() and _source_signature(path) == signature
                   for path, signature in expected.items())

    def export(self):
        if self.busy():
            return
        try:
            vocals, instrumental, output = self._validated_paths()
            source_paths = [Path(item['path']).resolve() for item in vocals] + [instrumental]
            temporary = output.with_name(
                '.' + output.stem + '.' + uuid.uuid4().hex + '.part.wav')
            if any(_same_path(temporary, path) for path in (*source_paths, output)):
                raise ValueError('临时导出路径与输入或目标冲突，已拒绝开始。')
            request = self._request(temporary)
            final_request = self._request(output)
            expected = {path: _source_signature(path) for path in source_paths}
            vocal_inputs = []
            for vocal in vocals:
                path = Path(vocal['path']).resolve()
                vocal_inputs.append({
                    'path': str(path), 'bytes': expected[path][0],
                    'mtime_ns': expected[path][1],
                })
            self._export_context = {
                'parameters': final_request,
                'source_job': str(self.job) if self.job else None,
                'inputs': {
                    'vocals': vocal_inputs,
                    'instrumental_path': {'path': str(instrumental),
                                          'bytes': expected[instrumental][0],
                                          'mtime_ns': expected[instrumental][1]},
                },
            }
            self._export_receipt = None
        except (OSError, ValueError) as error:
            self.status.setText(_tr('无法开始导出：') + str(error))
            _set_role(self.status, 'error')
            return

        def run(check, progress=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                report = _render_once(request, check, progress=progress)
                _validate_render(report, temporary)
                _validate_render_source_hashes(report, source_paths)
                _check_cancel(check)
                if not self._signatures_unchanged(expected):
                    raise ValueError('处理期间源 WAV 发生了变化；没有发布成品，请重新检查输入。')
                if output.exists():
                    raise FileExistsError('导出目标在渲染期间出现；没有覆盖该文件。')
                _publish_without_overwrite(temporary, output)
                report.update(output_path=str(output), source_files_unchanged=True)
                return report
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass

        self.result_title.setText(_tr('正在导出'))
        self.result_text.setText(_tr('正在后台处理；关闭窗口会先请求停止。'))
        _set_role(self.result_title, 'muted')
        self.status.setText(_tr('正在导出新的 WAV，源文件不会被修改…'))
        _set_role(self.status, 'muted')
        self._launch('export', run, with_progress=True)

    def preview(self):
        """Compatibility entry for callers that explicitly request a fresh segment."""
        if self.busy():
            return False
        self._preview_resume_seconds = self._absolute_player_seconds()
        return self._schedule_preview_refresh(immediate=True, autoplay=False)

    def _apply_progress_event(self, event):
        if not isinstance(event, dict):
            return
        try:
            fraction = max(0.0, min(1.0, float(event.get('fraction', 0.0))))
        except (TypeError, ValueError):
            fraction = 0.0
        phase_labels = {
            'decode': _tr('解码'),
            'prepare': _tr('准备'),
            'mix': _tr('混音'),
            'encode': _tr('编码'),
            'loudness': _tr('响度分析'),
            'done': _tr('完成'),
        }
        phase = phase_labels.get(str(event.get('phase', 'mix')), _tr('混音'))
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(round(fraction * 1000)))
        self.progress.setFormat(phase + '  ' + str(int(round(fraction * 100))) + '%')

    def _apply_live_meter_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        vocal_map = {index: track for index, track in enumerate(self._filled_vocal_tracks())}
        values = []
        for row in snapshot.get('tracks') or []:
            if not isinstance(row, dict):
                continue
            role = row.get('role')
            try:
                index = int(row.get('index', 0))
                peak = float(row.get('peak_dbfs'))
            except (TypeError, ValueError):
                continue
            if role == 'vocal' and index in vocal_map:
                rms = row.get('rms_dbfs')
                vocal_map[index]['meter'].set_levels(rms, peak)
                values.append('V%d %.1f / %.1f' % (index + 1, float(rms if rms is not None else -144), peak))
            elif role == 'backing':
                rms = row.get('rms_dbfs')
                self.instrumental_meter.set_levels(rms, peak)
                values.append(_tr('伴奏') + ' %.1f / %.1f' % (float(rms if rms is not None else -144), peak))
        bus = snapshot.get('bus') or {}
        try:
            bus_value = float(bus.get('peak_dbfs'))
        except (TypeError, ValueError):
            bus_value = None
        if bus_value is not None:
            self.bus_meter.set_levels(bus.get('rms_dbfs'), bus_value)
            self.bus_peak_value.setText('%.1f dBFS' % bus_value)
        if values and hasattr(self, '_loudness_summary'):
            self._loudness_summary.setText(_tr('实测 RMS / 峰值（dBFS）：') + ' · '.join(values))

    def _clear_render_meters(self):
        self._shown_edc_key = None
        self._preview_meter_report = {}
        self._preview_meter_scope = {}
        for meter in self._channel_meters.values():
            meter.reset()
        self.bus_meter.reset()
        self.bus_peak_value.setText('—  dBFS')
        if hasattr(self, 'meter_feed'):
            self.meter_feed.clear()
        if hasattr(self, 'reverb_decay'):
            self.reverb_decay.set_report(None)
        self.gr_summary.setText(_tr('压缩 GR — · 限制 GR —'))
        self.reverb_live_values.setText(_tr('发送 / 返回电平：等待播放'))
        if hasattr(self, '_loudness_summary'):
            self._loudness_summary.setText(_tr('实测电平：等待播放'))


    def _sync_eq_sample_rate_from_instrumental(self):
        """Use the renderer's backing-track probe before the first preview."""
        path = self._path('instrumental')
        if path is None:
            self._eq_sample_rate_probe = None
            self._sync_eq_sample_rate({'sample_rate': DEFAULT_SAMPLE_RATE})
            return False
        if not path.is_file():
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        identity = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
        cached = self._eq_sample_rate_probe
        if cached is not None and cached[0] == identity:
            if cached[1] is not None:
                self._sync_eq_sample_rate({'sample_rate': cached[1]})
            return cached[1] is not None
        log_path = Path(tempfile.gettempdir()) / (
            'vwa-mix-metadata-' + uuid.uuid4().hex + '.log')
        try:
            info = _mixdown_backend._probe_audio(path, None, log_path, None)
            rate = valid_sample_rate(info.sample_rate)
        except (OSError, TypeError, ValueError, RuntimeError):
            self._eq_sample_rate_probe = (identity, None)
            return False
        finally:
            try:
                log_path.unlink()
            except OSError:
                pass
        self._eq_sample_rate_probe = (identity, rate)
        self._sync_eq_sample_rate({'sample_rate': rate})
        return True

    def _sync_eq_sample_rate(self, report):
        """Apply the real render rate to both EQ views and the request state."""
        raw_rate = report.get('sample_rate')
        if raw_rate is None:
            return
        try:
            rate = valid_sample_rate(raw_rate)
            eq_frequency_limit_hz(rate)
        except ValueError:
            return
        if hasattr(self, 'eq_curve'):
            self.eq_curve.set_sample_rate(rate)
        if hasattr(self, 'eq_band_controls'):
            self.eq_band_controls.set_sample_rate(rate)
        projected = [
            project_eq_band(band, sample_rate=rate)
            for band in (self._eq_bands or [])
        ]
        if projected and projected != self._eq_bands:
            self._eq_bands = projected
            previous = self._syncing
            self._syncing = True
            try:
                if hasattr(self, 'eq_curve'):
                    self.eq_curve.set_bands(projected)
                if hasattr(self, 'eq_band_controls'):
                    self.eq_band_controls.set_bands(
                        projected, emit=False, sync_curve=False)
            finally:
                self._syncing = previous


    def _build_export_receipt(self, report):
        context = dict(self._export_context or {})
        output = Path(report.get('output_path', '')).resolve()
        info = output.stat()
        parameters = dict(context.get('parameters') or {})
        parameters['output_path'] = str(output)
        inputs = dict(context.get('inputs') or {})
        hashes = report.get('source_sha256') or {}
        hashes = {_normal_path_key(path): value for path, value in hashes.items()}

        def with_hash(entry):
            saved = dict(entry)
            saved['sha256'] = hashes.get(_normal_path_key(saved['path']))
            return saved

        vocals = [with_hash(entry) for entry in inputs.get('vocals', [])]
        backing = with_hash(inputs['instrumental_path'])
        saved_inputs = {'vocals': vocals, 'instrumental_path': backing}
        return {
            'schema': 'vwa.audio_mixdown.receipt/v2',
            'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'backend': 'audio_mixdown.render',
            'registration': 'explicit_user_action',
            'source_job': context.get('source_job'),
            'dependencies': [],
            'inputs': saved_inputs,
            'parameters': parameters,
            'output': {'path': str(output), 'bytes': info.st_size,
                       'mtime_ns': info.st_mtime_ns, 'sha256': report.get('output_sha256')},
            'render_result': {
                'duration_seconds': report.get('duration_seconds'),
                'peak_dbfs': report.get('peak_dbfs'),
                'warnings': list(report.get('warnings') or []),
            },
        }

    def succeeded(self, kind, result):
        if kind == 'export':
            self._result = result
            self._export_receipt = self._build_export_receipt(result)
            self.result_title.setText(
                _tr('导出完成；尚未登记到任务') if self.job
                else _tr('导出完成；可直接进入视频合成'))
            self.result_text.setText(
                _report_detail(result) + '\n' + _tr('文件：') + str(result.get('output_path', ''))
                + (('\n' + _tr('导出提示：') + str(result['warnings'][0]))
                   if result.get('warnings') else ''))
            self.result_text.setToolTip('\n'.join(str(item) for item in result.get('warnings', ())))
            _set_role(self.result_title, 'success')
            self.status.setText(
                _tr('WAV 已安全导出。检查试听后，可显式登记为任务 Mixdown。')
                if self.job else _tr('WAV 已安全导出；无需任务即可进入视频合成。'))
            _set_role(self.status, 'success')
            return
        if kind == 'component':
            packages = result.get('packages')
            if packages:
                candidate = Path(packages).resolve()
                self._component_packages = candidate if candidate.is_dir() else None
            self._support_ready = False
            self.support_panel.show()
            self.support_state.setText(_tr('混音组件安装完成，正在后台复查…'))
            _set_role(self.support_state, 'muted')
            self.status.setText(_tr('混音组件安装完成；正在后台复查导入状态。'))
            _set_role(self.status, 'muted')
            self._start_component_support_check()
            return
        if kind == 'register':
            self.registered = True
            destination = Path(result).resolve()
            if self._pending_navigation:
                try:
                    handed_off = self._handoff_to_video(destination)
                except (OSError, ValueError, TypeError) as error:
                    self.status.setText(_tr('已登记 Mixdown，但进入视频合成失败：') + str(error))
                    return
                self.navigate_requested = True
                if not handed_off:
                    self.status.setText(
                        _tr('已登记 Mixdown，但视频页已有其他 Mixdown；未覆盖当前编辑输入。'))
                    _set_role(self.status, 'error')
                    return
            self.status.setText(_tr('已显式登记任务 Mixdown；真实输入与参数回执已保存，未添加伪造上游。'))
            _set_role(self.status, 'success')
            if not self.embedded:
                self._completion_action = 'accept'

    def _handoff_to_video(self, mixdown):
        video = getattr(self.owner, 'video_mixdown', None)
        navigate = getattr(self.owner, 'navigate', None)
        if video is None:
            raise ValueError('当前窗口没有可用的视频合成页。')
        busy = getattr(video, 'busy', None)
        preview_busy = getattr(video, 'preview_busy', None)
        if ((callable(busy) and busy())
                or (callable(preview_busy) and preview_busy())):
            raise ValueError('视频合成页正在处理或预览，请完成或取消后再传入 Mixdown。')
        loaded = None
        if self.job and callable(getattr(video, 'use_source_job', None)):
            # Loading the task can fill its source video. If the task lacks a
            # video, preserve any existing editable value and only seed an empty
            # field so the user can still choose the video manually.
            loaded = video.use_source_job(self.job, mixdown)
        fields = getattr(video, 'fields', None)
        mixdown_field = fields.get('mixdown') if isinstance(fields, dict) else None
        if mixdown_field is None:
            raise ValueError('视频合成页没有可写入的 Mixdown 字段。')
        destination = str(Path(mixdown).resolve())
        if loaded is not False:
            mixdown_field.setText(destination)
        elif not mixdown_field.text().strip():
            mixdown_field.setText(destination)
        elif not _same_path(mixdown_field.text(), destination):
            return False
        if not self.job or loaded is False:
            # Existing video files may remain useful, but a previous task must
            # not silently claim an independently exported mix as its own.
            if hasattr(video, 'source_job'):
                video.source_job = None
            refresh_context = getattr(video, '_set_context', None)
            if callable(refresh_context):
                refresh_context()
        if not callable(navigate):
            raise ValueError('当前窗口没有可用的页面导航。')
        navigate('video_mixdown')
        self.video_requested = True
        return True

    def open_video(self):
        """Send the current export to video composition without a task."""
        if self.busy():
            return False
        if not self._result or not Path(self._result.get('output_path', '')).is_file():
            self.status.setText(_tr('请先安全导出 WAV，再进入视频合成。'))
            _set_role(self.status, 'error')
            return False
        try:
            handed_off = self._handoff_to_video(self._result['output_path'])
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(_tr('进入视频合成失败：') + str(error))
            _set_role(self.status, 'error')
            return False
        self.navigate_requested = True
        if not handed_off:
            self.status.setText(_tr('已进入视频合成，但保留页面原有的 Mixdown；未覆盖正在编辑的输入。'))
            _set_role(self.status, 'error')
            return False
        self.status.setText(_tr('已将本次 WAV 传给视频合成；任务关联是可选的。'))
        _set_role(self.status, 'success')
        return True

    def continue_to_video(self):
        if self.job:
            self.register(True)
            return self.busy()
        else:
            return self.open_video()

    def failed(self, kind, text):
        if kind == 'export':
            self.result_title.setText(_tr('导出失败'))
            self.result_text.setText(text)
            _set_role(self.result_title, 'error')
        elif kind == 'preview':
            self.preview_report.setText(_tr('试听片段生成失败：') + text)
            self.preview_before_state.setText(_tr('生成失败'))
            self.preview_after_state.setText(_tr('生成失败'))
            _set_role(self.preview_report, 'error')
            _set_role(self.preview_before_state, 'error')
            _set_role(self.preview_after_state, 'error')
        elif kind in ('component', 'support_check'):
            self._apply_component_support(False, text)
            self.support_panel.show()
            self.support_state.setText(
                (_tr('混音组件安装未完成：') if kind == 'component'
                 else _tr('混音组件检查失败：')) + text)
            _set_role(self.support_state, 'error')
        self.status.setText(_tr('混音操作失败：') + text)
        _set_role(self.status, 'error')

    def cancelled(self, kind):
        if kind == 'export':
            self.result_title.setText(_tr('导出已取消'))
            self.result_text.setText(_tr('未发布新的导出文件；源文件与已有文件保持不变。'))
            _set_role(self.result_title, 'muted')
        elif kind == 'preview':
            self.preview_report.setText(_tr('试听片段生成已取消。'))
            self.preview_before_state.setText(_tr('已取消'))
            self.preview_after_state.setText(_tr('已取消'))
            _set_role(self.preview_report, 'muted')
        elif kind == 'component':
            self.support_panel.show()
            self.support_state.setText(_tr('混音组件安装已取消；已下载内容可由安装器继续校验。'))
            _set_role(self.support_state, 'muted')
        self.status.setText(_tr('混音操作已取消。'))
        _set_role(self.status, 'muted')

    def finished(self):
        worker = self.sender()
        if worker is not None and worker is not self.worker:
            return
        self.worker = None
        self._busy_kind = None
        if worker is not None:
            worker.deleteLater()
        self._sync_controls()
        if self._completion_action == 'accept' and not self.embedded:
            self._completion_action = None
            self.accept()
        elif self._try_finish_pending_close():
            return

    def cancel(self):
        if not self.busy():
            return
        if self._busy_kind == 'register':
            self.status.setText(_tr('正在写入任务登记，请等待当前文件检查完成。'))
            _set_role(self.status, 'muted')
            return
        self.worker.cancel()
        self.cancel_button.setEnabled(False)
        if self._busy_kind == 'component':
            self.status.setText(_tr('正在取消组件安装…'))
        else:
            self.status.setText(_tr('已请求取消；会等待混音后端返回并清理本次临时 WAV。'))
        _set_role(self.status, 'muted')

    def register(self, navigate=False):
        if self.busy():
            return
        if not self._result or not Path(self._result.get('output_path', '')).is_file():
            self.status.setText(_tr('请先安全导出 WAV，再显式登记。'))
            return
        if not self.job or not (self.job / 'request.json').is_file():
            self.status.setText(_tr('当前混音没有可登记的现有任务。'))
            return
        try:
            from task_store import locked
            storage = getattr(self.owner, 'storage', None)
            publishing = getattr(self.owner, 'publishing', None)
            storage_busy = callable(getattr(storage, 'busy', None)) and storage.busy()
            publishing_busy = callable(getattr(publishing, 'busy', None)) and publishing.busy()
            occupied = (locked(self.job) or getattr(self.owner, 'processing_active', False)
                        or storage_busy or publishing_busy)
            if not occupied:
                from results_ui import active_task_operations
                operations = active_task_operations(self.owner, self.job)
                occupied = bool(operations)
            if occupied:
                raise ValueError('该任务仍被处理、页面预览、缓存管理或发布流程占用，请结束后再登记。')
        except (OSError, ValueError) as error:
            self.status.setText(_tr('暂时不能登记：') + str(error))
            _set_role(self.status, 'error')
            return
        destination = Path(self._result['output_path']).resolve()
        self._pending_navigation = bool(navigate)
        job = self.job
        receipt = dict(self._export_receipt or {})

        def run(check):
            _check_cancel(check)
            from task_store import locked
            if locked(job):
                raise ValueError('任务在登记开始前进入运行状态；本次没有写入任务登记。')
            expected_sha256 = (receipt.get('output') or {}).get('sha256')
            if expected_sha256 and _file_sha256(destination, check) != expected_sha256:
                raise ValueError('导出 WAV 在登记前发生变化；请重新导出后再登记。')
            from task_contract import bind, update_metadata
            # The external dry-vocal mixdown is not an automatic child of the
            # task's separated audio, MIDI or score. Keep provenance explicit.
            artifact_id = bind(job, 'mixdown', destination, dependencies=[])
            _check_cancel(check)
            saved = dict(receipt)
            saved['artifact_id'] = artifact_id
            saved['registered_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
            update_metadata(job, audio_mixdown=saved)
            _check_cancel(check)
            return str(destination)

        self.status.setText(_tr('正在显式登记到任务并校验 WAV…'))
        self._launch('register', run)

    def _retire_preview(self):
        self.cancel_preview()
        self._stop_preview_player()
        self._clear_preview_cache()

    def _defer_pending_close(self):
        """Remember one close request until all owned background work stops."""
        self._close_pending = True
        if not self._close_wait_timer.isActive():
            self._close_wait_timer.start()
        return self._try_finish_pending_close()

    def _try_finish_pending_close(self):
        if not self._close_pending:
            self._close_wait_timer.stop()
            return False
        if self.busy() or self.support_checking() or self.preview_busy():
            if not self._close_wait_timer.isActive():
                self._close_wait_timer.start()
            return False
        self._close_wait_timer.stop()
        self._close_pending = False
        self.reject()
        return True

    def done(self, result):
        if self.busy() or self.support_checking():
            self._defer_pending_close()
            if self.busy():
                self.cancel()
            elif self._support_worker is not None:
                self._support_worker.cancel()
            return
        if self._audition_service is None:
            self._audition_cleanup_complete = True
            self._audition_closing = True
            self._audition_poll_timer.stop()
            self._preview_debounce.stop()
            self._retire_preview()
            super().done(result)
            return
        if not self._audition_cleanup_complete:
            self._pending_done_result = result
            self._close_pending = False
            self.hide()
            self._shutdown_audition_service()
            return
        self._retire_preview()
        super().done(result)

    def showEvent(self, event):
        super().showEvent(event)
        if self._ui_ready and not self._audition_closing:
            self._ensure_audition_service_started()
            if self._support_ready and self._sources_ready() and self._native_source_key is None:
                self._schedule_preview_refresh()
            elif self._player is not None:
                self._player_position_changed(self._player.position())
            self._sync_controls()

    def hideEvent(self, event):
        # Navigation/occlusion does not own the session. Keep transport, metering
        # and parameter acknowledgements alive until explicit shutdown.
        super().hideEvent(event)

    def closeEvent(self, event):
        if self.busy() or self.support_checking():
            event.ignore()
            self._defer_pending_close()
            if self.busy():
                self.cancel()
            elif self._support_worker is not None:
                self._support_worker.cancel()
            return
        if self._audition_service is None:
            self._audition_cleanup_complete = True
            self._audition_closing = True
            self._audition_poll_timer.stop()
            self._preview_debounce.stop()
            self._retire_preview()
            super().closeEvent(event)
            return
        if not self._audition_cleanup_complete:
            event.ignore()
            self._close_finalize_pending = True
            self.hide()
            self._shutdown_audition_service()
            return
        self._retire_preview()
        super().closeEvent(event)


MixdownDialog = AudioMixdownDialog

__all__ = ['AudioMixdownDialog', 'CONTROL_NAMES', 'MixdownDialog', 'MixdownTask']
