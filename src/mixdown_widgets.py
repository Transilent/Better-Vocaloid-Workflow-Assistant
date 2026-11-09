"""Reusable Qt controls for the simple mixdown console."""
import math

from PyQt5.QtCore import QEvent, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import (QDial, QGridLayout, QHBoxLayout, QLabel,
                             QSizePolicy, QSlider, QStyle, QStyleOptionSlider,
                             QVBoxLayout, QWidget)

from desktop_theme import label
from mixdown_eq_curve import EQCurveView
from mixdown_contract import MAX_VOCALS as _MAX_VOCAL_TRACKS
from i18n import tr as _tr


def _set_role(widget, role):
    role = role or ''
    if widget.property('role') == role:
        return
    widget.setProperty('role', role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


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
