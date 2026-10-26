"""Accessible presentation adapters; existing Qt controls remain value owners."""
import math

from PyQt5.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QSignalBlocker, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                            QLabel, QRadioButton, QLayout, QScrollArea,
                            QSizePolicy, QTextEdit, QToolButton, QVBoxLayout,
                            QWidget)
from i18n import tr as _tr


class ChoiceRadioButton(QRadioButton):
    def hitButton(self, position):
        # Native radio hit-testing covers only its indicator/text, not card whitespace.
        return self.rect().contains(position)


class ChoiceGroup(QFrame):
    """Render a combo's current model as radio cards, without copying its value."""
    def __init__(self, combo, columns=2, compact=False):
        super().__init__()
        self.combo = combo
        self.columns = max(1, int(columns))
        self.compact = bool(compact)
        self.setObjectName('choice-group')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        combo.setParent(self)
        combo.hide()
        combo.setFocusPolicy(Qt.NoFocus)
        combo.installEventFilter(self)
        combo.currentIndexChanged.connect(self.sync)
        model = combo.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset,
                       model.layoutChanged, model.dataChanged):
            signal.connect(self.rebuild)
        self.rebuild()

    def rebuild(self, *args):
        while self.grid.count():
            item = self.grid.takeAt(0)
            old = item.widget()
            if old:
                self.group.removeButton(old)
                old.hide()
                old.deleteLater()
        self.buttons = []
        for index in range(self.combo.count()):
            text = self.combo.itemText(index)
            radio = ChoiceRadioButton(text, self)
            radio.setProperty('role', 'choice-compact' if self.compact else 'choice')
            radio.setAccessibleName(text)
            radio.setToolTip(str(self.combo.itemData(index, Qt.ToolTipRole) or text))
            radio.setMinimumWidth(0)
            radio.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            radio.setMinimumHeight(42 if self.compact else 54)
            radio.clicked.connect(lambda checked=False, i=index: self.choose(i))
            self.group.addButton(radio, index)
            self.buttons.append(radio)
        self.reflow()
        self.sync()

    def choose(self, index):
        if self.combo.isEnabled() and self._item_enabled(index):
            self.combo.setCurrentIndex(index)
        self.sync()

    def _item_enabled(self, index):
        model_index = self.combo.model().index(index, self.combo.modelColumn(), self.combo.rootModelIndex())
        return bool(model_index.flags() & Qt.ItemIsEnabled)

    def sync(self, *args):
        # Temporarily nonexclusive also permits the combo's valid unset (-1) state.
        self.group.setExclusive(False)
        for index, radio in enumerate(self.buttons):
            blocker = QSignalBlocker(radio)
            radio.setChecked(index == self.combo.currentIndex())
            radio.setEnabled(self.combo.isEnabled() and self._item_enabled(index))
            del blocker
        self.group.setExclusive(True)

    def reflow(self):
        factor = max(1., self.font().pixelSize()/18.)
        minimum = round((118 if self.compact else 210)*factor)
        cols = min(self.columns, max(1, len(self.buttons)), max(1, self.width() // minimum))
        cell_width = max(60, (self.width() - (cols - 1) * 8) // cols - round((52 if self.compact else 60)*factor))
        for index, radio in enumerate(self.buttons):
            radio.ensurePolished()
            full = self.combo.itemText(index)
            metrics = radio.fontMetrics()
            lines, line = [], ''
            for char in full:
                if char == '\n' or (line and metrics.horizontalAdvance(line + char) > cell_width):
                    lines.append(line)
                    line = '' if char == '\n' else char
                else:
                    line += char
            lines.append(line)
            radio.setText('\n'.join(lines))
            # Wrapped choices must publish their real minimum height. A fixed
            # one-row hint let parent columns collapse after project import.
            height=max(round((42 if self.compact else 54)*factor),
                       len(lines)*metrics.height()+round((18 if self.compact else 26)*factor))
            radio.setMinimumHeight(height)
            self.grid.addWidget(radio, index // cols, index % cols)
        for col in range(self.columns):
            self.grid.setColumnStretch(col, 1 if col < cols else 0)
        self.grid.invalidate()
        self.setMinimumHeight(self.grid.minimumSize().height())
        self.updateGeometry()

    def eventFilter(self, watched, event):
        if watched is self.combo and event.type() == QEvent.EnabledChange:
            self.sync()
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())


def choice(combo, columns=2, compact=False):
    """Wrap the original combo, retaining all its data/signals for legacy callers."""
    existing = getattr(combo, '_vwa_choice', None)
    if existing is not None:
        return existing
    view = ChoiceGroup(combo, columns=columns, compact=compact)
    combo._vwa_choice = view
    return view


class AdaptiveColumns(QWidget):
    """Use two columns only when the actual available logical width permits it."""
    def __init__(self, left, right, breakpoint=980, ratio=(1, 1)):
        super().__init__()
        self.left, self.right = left, right
        self.breakpoint, self.ratio = breakpoint, ratio
        self._stacked = None
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(20)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        for child in (left, right):
            child.setMinimumWidth(0)
        self.reflow()

    def reflow(self):
        stacked = self.width() < self.breakpoint
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self.grid.removeWidget(self.left)
        self.grid.removeWidget(self.right)
        self.grid.addWidget(self.left, 0, 0)
        self.grid.addWidget(self.right, 1 if stacked else 0, 0 if stacked else 1)
        self.grid.setColumnStretch(0, 1 if stacked else self.ratio[0])
        self.grid.setColumnStretch(1, 0 if stacked else self.ratio[1])
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())


class LyricsEdit(QTextEdit):
    """Multiline plain lyrics with QLineEdit's legacy text/setText API."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setMinimumHeight(150)

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)


class FlowLayout(QLayout):
    """Wrap long action groups before they can overflow a 1000px viewport."""
    def __init__(self, parent=None, spacing=10):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = area.x(), area.y()
        line_height = 0
        horizontal = max(0, self.spacing())
        vertical = max(0, self.spacing())
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            if line_height and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + vertical
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + horizontal
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()

# Timeline colors are presentation-only. They never feed back into a project or
# imply a rendered voicebank pitch.
_TRACK_COLORS = tuple(QColor(value) for value in (
    '#4e9af1', '#e0715b', '#51ad72', '#b179d6', '#d2a347', '#42aeb3'))
_REGION_COLORS = tuple(QColor(value) for value in (
    '#6d5bb5', '#ad6a3d', '#347d8a', '#9c596f'))
_STYLE_REGION_COLORS = {'sad': QColor('#8a5ba8')}


def _finite_number(value):
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        return None
    return float(value)


def _nice_interval(target):
    if not math.isfinite(target) or target <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(target))
    scaled = target / power
    factor = next((candidate for candidate in (1., 2., 5., 10.)
                   if scaled <= candidate), 10.)
    return factor * power


def _pitch_name(pitch):
    names = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
    value = min(127, max(0, int(round(pitch))))
    return names[value % 12] + str(value // 12 - 1)


class _TimelineCanvas(QWidget):
    """Paint a scrollable piano-roll canvas; all input data remains external."""
    selectionChanged = pyqtSignal(object)
    zoomRequested = pyqtSignal(float)

    RULER_HEIGHT = 44
    PITCH_HEIGHT = 14
    PIANO_WIDTH = 72
    RIGHT_PADDING = 16
    BASE_PIXELS_PER_SECOND = 80.0
    MIN_ZOOM = .125
    MAX_ZOOM = 256.0
    MAX_CANVAS_WIDTH = 4_000_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('style-timeline-canvas')
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self._zoom = 1.0
        self._notes = []
        self._regions = []
        self._selection = None
        self._drag_anchor = None
        self._drag_pixels = 0
        self._time_start = 0.0
        self._time_end = 10.0
        self.setMinimumWidth(180)
        self._refresh_geometry()

    def zoom_factor(self):
        return self._zoom

    def set_zoom(self, factor):
        try:
            value = float(factor)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(value):
            return False
        value = min(self.MAX_ZOOM, max(self.MIN_ZOOM, value))
        if value == self._zoom:
            return True
        self._zoom = value
        self._refresh_geometry()
        return True

    @property
    def pixels_per_second(self):
        duration = max(1e-9, self._time_end - self._time_start)
        requested = self.BASE_PIXELS_PER_SECOND * self._zoom
        maximum = max(1.0, self.MAX_CANVAS_WIDTH - self.PIANO_WIDTH - self.RIGHT_PADDING) / duration
        return max(1e-300, min(requested, maximum))

    def canvas_width(self):
        duration = max(0.0, self._time_end - self._time_start)
        result = self.PIANO_WIDTH + duration * self.pixels_per_second + self.RIGHT_PADDING
        if not math.isfinite(result):
            return self.MAX_CANVAS_WIDTH
        return max(180, min(self.MAX_CANVAS_WIDTH, int(round(result)) + 1))

    def canvas_height(self):
        return self.RULER_HEIGHT + 128 * self.PITCH_HEIGHT + 4

    def sizeHint(self):
        return QSize(self.canvas_width(), self.canvas_height())

    def minimumSizeHint(self):
        return QSize(180, self.canvas_height())

    def _refresh_geometry(self):
        self.setMinimumSize(180, self.canvas_height())
        self.resize(self.canvas_width(), self.canvas_height())
        self.updateGeometry()
        self.update()

    def _refresh_bounds(self):
        starts = [0.0]
        ends = [10.0]
        starts.extend(note['start'] for note in self._notes)
        ends.extend(note['end'] for note in self._notes)
        starts.extend(region['start'] for region in self._regions)
        ends.extend(region['end'] for region in self._regions)
        earliest = min(starts)
        latest = max(ends)
        left_margin = max(.25, min(1.0, max(0.0, latest - earliest) * .02))
        right_margin = left_margin
        self._time_start = min(0.0, earliest) - left_margin
        self._time_end = max(10.0, latest) + right_margin
        if not (math.isfinite(self._time_start) and math.isfinite(self._time_end)
                and self._time_end > self._time_start):
            self._time_start, self._time_end = 0.0, 10.0
        self._refresh_geometry()

    def scene_bounds(self):
        return self._time_start, self._time_end

    def time_to_x(self, seconds):
        value = _finite_number(seconds)
        if value is None:
            value = self._time_start
        return self.PIANO_WIDTH + (value - self._time_start) * self.pixels_per_second

    def x_to_time(self, x):
        position = self._time_start if not math.isfinite(float(x)) else float(x)
        seconds = self._time_start + (position - self.PIANO_WIDTH) / self.pixels_per_second
        seconds = min(self._time_end, max(self._time_start, seconds))
        return seconds if math.isfinite(seconds) else self._time_start

    def pitch_to_y(self, pitch):
        value = _finite_number(pitch)
        if value is None:
            value = 60.0
        return self.RULER_HEIGHT + (127.0 - min(127.0, max(0.0, value))) * self.PITCH_HEIGHT

    def set_project(self, project, track_indices):
        """Rebuild display notes from only the selected mainGroups."""
        from vocal_expression import TempoMap

        if not isinstance(project, dict):
            raise TypeError(_tr('时间轴工程数据必须为字典。'))
        tracks = project.get('tracks')
        if not isinstance(tracks, list):
            tracks = []
        tempo_rows = (project.get('time') or {}).get('tempo') if isinstance(project.get('time'), dict) else None
        if not isinstance(tempo_rows, list) or not tempo_rows:
            tempo_rows = [{'position': 0, 'bpm': 120}]
        clock = TempoMap(tempo_rows)
        indices = track_indices if isinstance(track_indices, (list, tuple)) else []
        notes = []
        for order, track_index in enumerate(indices):
            if type(track_index) is not int or not 0 <= track_index < len(tracks):
                continue
            track = tracks[track_index]
            if not isinstance(track, dict):
                continue
            group = track.get('mainGroup')
            if not isinstance(group, dict) or not isinstance(group.get('notes'), list):
                continue
            reference = track.get('mainRef') if isinstance(track.get('mainRef'), dict) else {}
            offset = _finite_number(reference.get('blickOffset', 0))
            if offset is None:
                offset = 0.0
            color = _TRACK_COLORS[order % len(_TRACK_COLORS)]
            for note in group['notes']:
                if not isinstance(note, dict):
                    continue
                onset = _finite_number(note.get('onset'))
                duration = _finite_number(note.get('duration'))
                pitch = _finite_number(note.get('pitch'))
                if onset is None or duration is None or pitch is None or duration <= 0 or not 0 <= pitch <= 127:
                    continue
                try:
                    start = clock.to_seconds(onset + offset)
                    end = clock.to_seconds(onset + offset + duration)
                except (TypeError, ValueError, OverflowError):
                    continue
                if not (math.isfinite(start) and math.isfinite(end) and end > start):
                    continue
                notes.append({'track_index': track_index, 'start': float(start),
                              'end': float(end), 'pitch': float(pitch), 'color': color,
                              'lyric': str(note.get('lyrics') or note.get('lyric') or '')})
        self._notes = notes
        self._regions = []
        self._drag_anchor = None
        self._drag_pixels = 0
        self._set_selection(None, emit=True)
        self._refresh_bounds()

    def set_regions(self, regions):
        """Display region backgrounds without retaining or changing caller data."""
        rows = regions if isinstance(regions, (list, tuple)) else []
        display = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            start = _finite_number(row.get('start'))
            end = _finite_number(row.get('end'))
            if start is None or end is None or end <= start:
                continue
            preset = row.get('preset')
            label = row.get('label')
            if not isinstance(label, str) or not label.strip():
                if preset == 'gentle':
                    label = _tr('柔和')
                elif preset == 'sad':
                    label = _tr('悲伤')
                elif preset == 'bold':
                    label = _tr('明亮欢快')
                else:
                    label = _tr('风格区域')
            else:
                label = _tr(label)
            color = _STYLE_REGION_COLORS.get(preset)
            if color is None:
                color = _REGION_COLORS[index % len(_REGION_COLORS)]
            display.append({'start': start, 'end': end, 'label': label,
                            'color': QColor(color)})
        self._regions = display
        self._refresh_bounds()
        if self._selection is not None:
            self.set_selection(*self._selection)

    def selection(self):
        return self._selection

    def _set_selection(self, value, emit=True):
        if value == self._selection:
            return
        self._selection = value
        self.update()
        if emit:
            self.selectionChanged.emit(value)

    def set_selection(self, start, end):
        first, second = _finite_number(start), _finite_number(end)
        if first is None or second is None:
            raise ValueError(_tr('时间轴选区端点必须是有限数值。'))
        low, high = sorted((max(self._time_start, min(self._time_end, first)),
                            max(self._time_start, min(self._time_end, second))))
        if high - low <= 1e-9:
            self.clear_selection()
            return None
        value = (float(low), float(high))
        self._set_selection(value, emit=True)
        return value

    def clear_selection(self):
        self._set_selection(None, emit=True)

    def note_color_count(self):
        return len({note['color'].name() for note in self._notes})

    def _time_at(self, x):
        return self.x_to_time(x)

    def mousePressEvent(self, event):
        if not self.isEnabled() or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_anchor = self._time_at(event.pos().x())
        self._drag_pixels = 0
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.isEnabled() or self._drag_anchor is None:
            super().mouseMoveEvent(event)
            return
        self._drag_pixels = max(self._drag_pixels, abs(event.pos().x() - self.time_to_x(self._drag_anchor)))
        if self._drag_pixels >= 2:
            self.set_selection(self._drag_anchor, self._time_at(event.pos().x()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self.isEnabled() or self._drag_anchor is None or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._drag_pixels >= 2:
            self.set_selection(self._drag_anchor, self._time_at(event.pos().x()))
        self._drag_anchor = None
        self._drag_pixels = 0
        event.accept()

    def wheelEvent(self, event):
        if self.isEnabled() and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoomRequested.emit(1.2 if delta > 0 else 1 / 1.2)
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _visible_time_span(self, left, right):
        return self.x_to_time(max(self.PIANO_WIDTH, left)), self.x_to_time(right)

    def _major_interval(self):
        return _nice_interval(88.0 / self.pixels_per_second)

    def _major_ticks(self, start, end):
        step = self._major_interval()
        if not (math.isfinite(start) and math.isfinite(end) and end >= start
                and math.isfinite(step) and step > 0):
            return ()
        first = math.ceil(start / step) * step
        count = int(math.floor((end - first) / step)) + 1
        count = max(0, min(count, 40000))
        return tuple(first + index * step for index in range(count)
                     if math.isfinite(first + index * step))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        # QPainter.clipBoundingRect() may be empty until an explicit clip is set.
        # event.rect() is the exposed canvas strip, so keep it as a real fallback
        # for ticks and text while drawing under the device's own clipping.
        visible = QRectF(event.rect()).intersected(QRectF(self.rect()))
        if visible.isEmpty():
            visible = QRectF(self.visibleRegion().boundingRect()).intersected(QRectF(self.rect()))
        if visible.isEmpty():
            visible = QRectF(self.rect())
        painter.setClipRect(visible)
        painter.fillRect(self.rect(), QColor('#121820'))
        panel = QRectF(self.PIANO_WIDTH, 0, max(0, self.width() - self.PIANO_WIDTH), self.height())
        painter.fillRect(panel, QColor('#f4f6f8'))
        self._paint_rows(painter)
        self._paint_ruler(painter, visible)
        self._paint_regions(painter)
        self._paint_grid(painter, visible)
        self._paint_notes(painter)
        self._paint_selection(painter)
        self._paint_empty_message(painter)
        painter.end()

    def _paint_rows(self, painter):
        for row in range(128):
            pitch = 127 - row
            y = self.RULER_HEIGHT + row * self.PITCH_HEIGHT
            black = pitch % 12 in (1, 3, 6, 8, 10)
            key_color = QColor('#171c22') if black else QColor('#f8f7f1')
            painter.fillRect(0, y, self.PIANO_WIDTH, self.PITCH_HEIGHT, key_color)
            if black:
                painter.fillRect(self.PIANO_WIDTH - 12, y, 12, self.PITCH_HEIGHT, QColor('#0d1116'))
            row_color = QColor('#f1f4f7') if black else QColor('#fbfcfd')
            painter.fillRect(self.PIANO_WIDTH, y, self.width() - self.PIANO_WIDTH,
                             self.PITCH_HEIGHT, row_color)
            painter.setPen(QPen(QColor('#c8d0d8'), 0))
            painter.drawLine(0, y, self.width(), y)
            painter.setPen(QColor('#fffdf4') if black else QColor('#222830'))
            font = painter.font()
            font.setPointSizeF(6.8)
            painter.setFont(font)
            painter.drawText(QRectF(4, y, self.PIANO_WIDTH - 9, self.PITCH_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, _pitch_name(pitch))

    def _paint_grid(self, painter, visible):
        left = self.PIANO_WIDTH
        visible = visible.intersected(QRectF(self.rect()))
        major = self._major_interval()
        minor = major / 5.0
        start, end = self._visible_time_span(max(left, visible.left()), max(left + 1, visible.right()))
        first = math.floor(start / minor) * minor
        count = int(math.floor((end - first) / minor)) + 2
        count = max(0, min(count, 120000))
        for index in range(count):
            seconds = first + index * minor
            if not math.isfinite(seconds):
                continue
            x = self.time_to_x(seconds)
            if x < left - 1 or x > self.width() + 1:
                continue
            is_major = abs(seconds / major - round(seconds / major)) < 1e-6
            painter.setPen(QPen(QColor('#aeb8c2' if is_major else '#e0e5ea'), 1 if is_major else 0))
            painter.drawLine(int(round(x)), self.RULER_HEIGHT, int(round(x)), self.height())
        painter.fillRect(left, self.RULER_HEIGHT - 1, self.width() - left, 1, QColor('#87939f'))

    def _paint_ruler(self, painter, visible):
        left = self.PIANO_WIDTH
        painter.fillRect(left, 0, self.width() - left, self.RULER_HEIGHT, QColor('#202b36'))
        painter.fillRect(0, 0, left, self.RULER_HEIGHT, QColor('#0d1217'))
        painter.setPen(QColor('#dce7ef'))
        font = painter.font()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.drawText(QRectF(4, 0, left - 8, self.RULER_HEIGHT),
                         Qt.AlignCenter, _tr('音高'))
        visible = visible.intersected(QRectF(self.rect()))
        start, end = self._visible_time_span(max(left, visible.left()), max(left + 1, visible.right()))
        major = self._major_interval()
        decimals = 0 if major >= 1 else (1 if major >= .1 else 2)
        ticks = self._major_ticks(start, end)
        if not ticks:
            ticks = (min(end, max(start, (start + end) / 2)),)
        for seconds in ticks:
            x = self.time_to_x(seconds)
            if x < left - 1 or x > self.width() + 1:
                continue
            painter.setPen(QColor('#8eb7d4'))
            painter.drawLine(int(round(x)), self.RULER_HEIGHT - 7,
                             int(round(x)), self.RULER_HEIGHT)
            painter.setPen(QColor('#e8f2f8'))
            label = f'{seconds:.{decimals}f} s'
            painter.drawText(QRectF(x + 4, 3, 82, 17), Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.setPen(QColor('#8eb7d4'))
        painter.drawLine(left, 0, left, self.height())

    def _paint_regions(self, painter):
        painter.save()
        painter.setClipRect(QRectF(self.PIANO_WIDTH, 0,
                                   self.width() - self.PIANO_WIDTH, self.height()))
        for region in self._regions:
            left = self.time_to_x(region['start'])
            right = self.time_to_x(region['end'])
            if right <= self.PIANO_WIDTH or left >= self.width():
                continue
            color = QColor(region['color'])
            background = QColor(color)
            background.setAlpha(38)
            painter.fillRect(QRectF(left, self.RULER_HEIGHT, max(1, right - left),
                                    self.height() - self.RULER_HEIGHT), background)
            ruler = QColor(color)
            ruler.setAlpha(82)
            painter.fillRect(QRectF(left, 22, max(1, right - left), self.RULER_HEIGHT - 22), ruler)
            available = right - left - 8
            if available >= 36:
                painter.setPen(QColor('#eef6fc'))
                metrics = painter.fontMetrics()
                label = metrics.elidedText(region['label'], Qt.ElideRight, int(available))
                painter.drawText(QRectF(left + 4, 22, available, self.RULER_HEIGHT - 22),
                                 Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.setPen(QPen(QColor(color), 1))
            painter.drawLine(int(round(left)), self.RULER_HEIGHT,
                             int(round(left)), self.height())
        painter.restore()

    def _paint_notes(self, painter):
        painter.save()
        painter.setClipRect(QRectF(self.PIANO_WIDTH, self.RULER_HEIGHT,
                                   self.width() - self.PIANO_WIDTH,
                                   self.height() - self.RULER_HEIGHT))
        for note in self._notes:
            left = self.time_to_x(note['start'])
            right = self.time_to_x(note['end'])
            y = self.pitch_to_y(note['pitch'])
            width = max(2.0, right - left)
            color = QColor(note['color'])
            painter.setPen(QPen(color.darker(155), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(left, y + 2, width, self.PITCH_HEIGHT - 4), 2, 2)
            lyric = note.get('lyric', '')
            if lyric and width >= 18:
                painter.setClipRect(QRectF(left + 2, y + 2, max(0, width - 4),
                                           self.PITCH_HEIGHT - 4))
                painter.setPen(QColor('#ffffff'))
                metrics = painter.fontMetrics()
                painter.drawText(QRectF(left + 4, y + 1, max(0, width - 7),
                                        self.PITCH_HEIGHT - 2), Qt.AlignVCenter | Qt.AlignLeft,
                                 metrics.elidedText(lyric, Qt.ElideRight, max(0, int(width - 8))))
                painter.setClipRect(QRectF(self.PIANO_WIDTH, self.RULER_HEIGHT,
                                           self.width() - self.PIANO_WIDTH,
                                           self.height() - self.RULER_HEIGHT))
        painter.restore()

    def _paint_selection(self, painter):
        if self._selection is None:
            return
        left = self.time_to_x(self._selection[0])
        right = self.time_to_x(self._selection[1])
        color = QColor('#2f86d8')
        fill = QColor(color)
        fill.setAlpha(48)
        painter.fillRect(QRectF(left, self.RULER_HEIGHT, max(1, right - left),
                                self.height() - self.RULER_HEIGHT), fill)
        painter.setPen(QPen(color, 2))
        painter.drawLine(int(round(left)), self.RULER_HEIGHT, int(round(left)), self.height())
        painter.drawLine(int(round(right)), self.RULER_HEIGHT, int(round(right)), self.height())

    def _paint_empty_message(self, painter):
        if self._notes:
            return
        painter.setPen(QColor('#65717d'))
        font = painter.font()
        font.setPointSizeF(9)
        painter.setFont(font)
        rect = QRectF(self.PIANO_WIDTH + 20, self.RULER_HEIGHT + 24,
                      max(80, self.width() - self.PIANO_WIDTH - 40), 28)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, _tr('暂无可显示的音符'))


class _TimelinePianoHeader(QWidget):
    """Sticky piano keys drawn over the scroll viewport's left edge."""
    def __init__(self, canvas, vertical_scroll, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.vertical_scroll = vertical_scroll
        self.setObjectName('style-timeline-piano-header')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor('#0d1217'))
        font = painter.font()
        offset = self.vertical_scroll.value()
        for row in range(128):
            pitch = 127 - row
            y = self.canvas.RULER_HEIGHT + row * self.canvas.PITCH_HEIGHT - offset
            if y + self.canvas.PITCH_HEIGHT <= self.canvas.RULER_HEIGHT or y >= self.height():
                continue
            black = pitch % 12 in (1, 3, 6, 8, 10)
            painter.fillRect(0, y, self.width(), self.canvas.PITCH_HEIGHT,
                             QColor('#171c22') if black else QColor('#f8f7f1'))
            if black:
                painter.fillRect(max(0, self.width() - 12), y, 12,
                                 self.canvas.PITCH_HEIGHT, QColor('#0d1116'))
            painter.setPen(QPen(QColor('#c8d0d8'), 0))
            painter.drawLine(0, y, self.width(), y)
            painter.setPen(QColor('#fffdf4') if black else QColor('#222830'))
            font.setPointSizeF(6.8)
            painter.setFont(font)
            painter.drawText(QRectF(4, y, self.width() - 9, self.canvas.PITCH_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, _pitch_name(pitch))
        # Draw the corner after scrolling rows so a partially visible row cannot
        # cover the fixed pitch heading.
        painter.fillRect(0, 0, self.width(), self.canvas.RULER_HEIGHT, QColor('#0d1217'))
        painter.setPen(QColor('#dce7ef'))
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.drawText(QRectF(4, 0, self.width() - 8, self.canvas.RULER_HEIGHT),
                         Qt.AlignCenter, _tr('音高'))
        painter.setPen(QColor('#8eb7d4'))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        painter.end()


class _TimelineRulerHeader(QWidget):
    """Sticky second ruler drawn at the scroll viewport's top edge."""
    def __init__(self, canvas, horizontal_scroll, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.horizontal_scroll = horizontal_scroll
        self.visible_ticks = ()
        self.setObjectName('style-timeline-ruler-header')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _visible_span(self):
        left, right = self.canvas.PIANO_WIDTH, self.canvas.PIANO_WIDTH + self.width()
        offset = self.horizontal_scroll.value()
        return self.canvas.x_to_time(offset + left), self.canvas.x_to_time(offset + right)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor('#202b36'))
        start, end = self._visible_span()
        offset = self.horizontal_scroll.value() + self.canvas.PIANO_WIDTH
        for region in self.canvas._regions:
            left = self.canvas.time_to_x(region['start']) - offset
            right = self.canvas.time_to_x(region['end']) - offset
            if right <= 0 or left >= self.width():
                continue
            color = QColor(region['color'])
            band = QColor(color)
            band.setAlpha(82)
            painter.fillRect(QRectF(left, 22, max(1, right - left), self.height() - 22), band)
            available = right - left - 8
            if available >= 36:
                painter.setPen(QColor('#eef6fc'))
                label = painter.fontMetrics().elidedText(region['label'], Qt.ElideRight,
                                                        int(available))
                painter.drawText(QRectF(left + 4, 22, available, self.height() - 22),
                                 Qt.AlignLeft | Qt.AlignVCenter, label)
        major = self.canvas._major_interval()
        decimals = 0 if major >= 1 else (1 if major >= .1 else 2)
        ticks = self.canvas._major_ticks(start, end)
        if not ticks:
            ticks = (min(end, max(start, (start + end) / 2)),)
        self.visible_ticks = ticks
        for seconds in ticks:
            x = self.canvas.time_to_x(seconds) - offset
            if x < -1 or x > self.width() + 1:
                continue
            painter.setPen(QColor('#8eb7d4'))
            painter.drawLine(int(round(x)), self.height() - 7,
                             int(round(x)), self.height())
            painter.setPen(QColor('#e8f2f8'))
            painter.drawText(QRectF(x + 4, 3, 82, 17),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             f'{seconds:.{decimals}f} s')
        painter.setPen(QColor('#87939f'))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class StyleTimeline(QWidget):
    """Selectable, zoomable piano-roll overview for an existing SVP project."""
    selectionChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('style-timeline')
        self.setMinimumSize(0, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(_tr('钢琴卷帘时间轴'))
        self.setAccessibleDescription(_tr('拖动选择时间；按 Esc 清除；按住 Ctrl 滚轮缩放。'))
        self._center_on_show = True
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        self.hint = QLabel(_tr('拖动选择时间；按 Esc 清除；按住 Ctrl 滚轮缩放。'), self)
        self.hint.setProperty('role', 'muted')
        self.hint.setWordWrap(True)
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.zoom_out = QToolButton(self)
        self.zoom_out.setText('−')
        self.zoom_out.setToolTip(_tr('缩小时间轴'))
        self.zoom_out.setAccessibleName(_tr('缩小时间轴'))
        self.zoom_percent = QLabel('100%', self)
        self.zoom_percent.setMinimumWidth(38)
        self.zoom_percent.setAlignment(Qt.AlignCenter)
        self.zoom_percent.setAccessibleName(_tr('时间轴缩放'))
        self.zoom_in = QToolButton(self)
        self.zoom_in.setText('+')
        self.zoom_in.setToolTip(_tr('放大时间轴'))
        self.zoom_in.setAccessibleName(_tr('放大时间轴'))
        toolbar.addWidget(self.hint, 1)
        toolbar.addWidget(self.zoom_out)
        toolbar.addWidget(self.zoom_percent)
        toolbar.addWidget(self.zoom_in)
        self._layout.addLayout(toolbar)
        self._canvas = _TimelineCanvas(self)
        self._view = QScrollArea(self)
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setWidgetResizable(False)
        self._view.setMinimumSize(0, 176)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setWidget(self._canvas)
        self._layout.addWidget(self._view, 1)
        viewport = self._view.viewport()
        self._piano_header = _TimelinePianoHeader(
            self._canvas, self._view.verticalScrollBar(), viewport)
        self._ruler_header = _TimelineRulerHeader(
            self._canvas, self._view.horizontalScrollBar(), viewport)
        self._piano_header.show()
        self._ruler_header.show()
        viewport.installEventFilter(self)
        self._view.horizontalScrollBar().valueChanged.connect(self._sync_headers)
        self._view.verticalScrollBar().valueChanged.connect(self._sync_headers)
        self._canvas.selectionChanged.connect(self.selectionChanged)
        self._canvas.zoomRequested.connect(self.zoom_by)
        self.zoom_out.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        self.zoom_in.clicked.connect(lambda: self.zoom_by(1.2))
        self._canvas.installEventFilter(self)
        self._sync_headers()

    def _sync_headers(self, *args):
        viewport = self._view.viewport()
        piano_width = self._canvas.PIANO_WIDTH
        ruler_height = self._canvas.RULER_HEIGHT
        self._piano_header.setGeometry(0, 0, piano_width, max(ruler_height, viewport.height()))
        self._ruler_header.setGeometry(piano_width, 0, max(0, viewport.width() - piano_width),
                                       ruler_height)
        self._piano_header.raise_()
        self._ruler_header.raise_()
        self._piano_header.update()
        self._ruler_header.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_headers()
        if self._center_on_show:
            self._center_vertical_on_notes()
            self._center_on_show = False

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.hint.setEnabled(enabled)
        self.zoom_out.setEnabled(enabled)
        self.zoom_in.setEnabled(enabled)
        self.zoom_percent.setEnabled(enabled)
        self._view.setEnabled(enabled)
        self._canvas.setEnabled(enabled)
        if not enabled:
            self._canvas._drag_anchor = None
            self._canvas._drag_pixels = 0

    def selection(self):
        return self._canvas.selection()

    def set_selection(self, start, end):
        return self._canvas.set_selection(start, end)

    def clear_selection(self):
        self._canvas.clear_selection()

    def set_project(self, project, track_indices):
        self._canvas.set_project(project, track_indices)
        bar = self._view.horizontalScrollBar()
        bar.setValue(bar.minimum())
        self._center_on_show = not self.isVisible()
        if not self._center_on_show:
            self._center_vertical_on_notes()
        self._update_zoom_label()
        self._sync_headers()

    def set_regions(self, regions):
        self._canvas.set_regions(regions)
        self._sync_headers()

    def zoom_factor(self):
        return self._canvas.zoom_factor()

    def set_zoom(self, factor):
        center_x = max(0, self._view.viewport().width() // 2)
        center_time = self.time_at_view_x(center_x)
        if not self._canvas.set_zoom(factor):
            return False
        self._restore_view_time(center_time, center_x)
        self._update_zoom_label()
        self._sync_headers()
        return True

    def zoom_by(self, factor):
        try:
            value = float(factor)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(value) or value <= 0:
            return False
        return self.set_zoom(self.zoom_factor() * value)

    def time_at_view_x(self, x):
        value = _finite_number(x)
        if value is None:
            value = 0.0
        bar = self._view.horizontalScrollBar()
        return self._canvas.x_to_time(bar.value() + value)

    def view_x_for_time(self, seconds):
        return self._canvas.time_to_x(seconds) - self._view.horizontalScrollBar().value()

    def _restore_view_time(self, seconds, viewport_x):
        self._view.updateGeometry()
        target = self._canvas.time_to_x(seconds) - viewport_x
        bar = self._view.horizontalScrollBar()
        bar.setValue(int(round(max(bar.minimum(), min(bar.maximum(), target)))))
        self._sync_headers()

    def _center_vertical_on_notes(self):
        pitches = [note['pitch'] for note in self._canvas._notes]
        target = (min(pitches) + max(pitches)) / 2 if pitches else 60
        y = self._canvas.pitch_to_y(target) - self._view.viewport().height() / 2
        bar = self._view.verticalScrollBar()
        bar.setValue(int(round(max(bar.minimum(), min(bar.maximum(), y)))))
        self._sync_headers()

    def _update_zoom_label(self):
        self.zoom_percent.setText(f'{round(self.zoom_factor() * 100)}%')

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self._view.viewport() and event.type() in (QEvent.Resize, QEvent.Show):
            if event.type() == QEvent.Resize and event.oldSize().height() > 0 and self._canvas._notes:
                # Folding controls or wrapping translated hints changes the
                # viewport height. Keep the same pitch at its centre rather
                # than letting the displayed melody drift out of view.
                if self._center_on_show:
                    self._center_vertical_on_notes()
                else:
                    bar = self._view.verticalScrollBar()
                    shift = (event.oldSize().height() - event.size().height()) / 2.
                    bar.setValue(int(round(bar.value() + shift)))
            self._sync_headers()
        if watched is self._canvas and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            return True
        return super().eventFilter(watched, event)
