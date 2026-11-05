"""Accessible presentation adapters; existing Qt controls remain value owners."""
import math
import bisect

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QSignalBlocker, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPalette, QPen, QMouseEvent, QPolygonF
from PyQt5.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                            QLabel, QRadioButton, QLayout, QScrollArea,
                            QSizePolicy, QTextEdit, QToolButton, QVBoxLayout,
                            QWidget, QComboBox, QApplication)
from i18n import tr as _tr
import style_catalog


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
            if self.combo.property('stylePresetChoices'):
                radio.setProperty('stylePreset',str(self.combo.itemData(index)))
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
STYLE_PRESET_COLORS = {
    key: style_catalog.style_color(key) for key in style_catalog.VISIBLE_STYLES
}
STYLE_PRESET_COLORS.update({key: style_catalog.technique_color(key)
                            for key in style_catalog.TECHNIQUE_IDS
                            if style_catalog.technique_color(key)})
_STYLE_REGION_COLORS = {key:QColor(value) for key,value in STYLE_PRESET_COLORS.items()}


def style_preset_color(preset):
    return QColor(STYLE_PRESET_COLORS.get(preset,STYLE_PRESET_COLORS['none']))


def _timeline_theme(widget):
    # Use the same palette as the surrounding workspace, including fullscreen
    # children. Project/track colours are display metadata, never style inputs.
    from desktop_theme import COLORS
    application = QApplication.instance()
    mode = application.property('vwa-theme') if application is not None else None
    if mode not in COLORS:
        mode = 'dark' if widget.palette().color(QPalette.Window).lightness() < 128 else 'light'
    return COLORS[mode]


def _blend_color(first, second, amount):
    a, b = QColor(first), QColor(second)
    return QColor(*(round(x + (y - x) * amount) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


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
    selectionCommitted = pyqtSignal(object)
    zoomRequested = pyqtSignal(float)

    RULER_HEIGHT = 64
    REGION_LANE_TOP = 26
    REGION_HANDLE_WIDTH = 14
    PITCH_HEIGHT = 40
    PIANO_WIDTH = 72
    RIGHT_PADDING = 16
    BASE_PIXELS_PER_SECOND = 80.0
    DEFAULT_ZOOM = 3.25
    MIN_ZOOM = .001
    MAX_ZOOM = 256.0
    MAX_CANVAS_WIDTH = 4_000_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('style-timeline-canvas')
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self._zoom = self.DEFAULT_ZOOM
        self._notes = []
        self._regions = []
        self._active_region = -1
        self._selection_preset = style_catalog.DEFAULT_STYLE_ID
        self._region_preview = None
        self._snap_enabled = False
        self._note_edges = ()
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
        self._note_edges = tuple(sorted({t for note in notes for t in (note['start'], note['end'])}))
        self._regions = []
        self._active_region = -1
        self._region_preview = None
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
                label = _tr(style_catalog.style_region_label(preset))
            else:
                label = _tr(label)
            technique=row.get('technique')
            technique_applied=(style_catalog.is_technique(technique)
                               and technique!=style_catalog.DEFAULT_TECHNIQUE_ID)
            if technique_applied:
                label=(label+' · ' if preset!=style_catalog.DEFAULT_STYLE_ID else '')+_tr(style_catalog.technique_region_label(technique))
            color = _STYLE_REGION_COLORS.get(technique if technique_applied else preset)
            if color is None:
                color = _REGION_COLORS[index % len(_REGION_COLORS)]
            display.append({'index': index, 'preset':preset, 'start': start, 'end': end, 'label': label,
                            'color': QColor(color)})
        self._regions = display
        self._region_preview = None
        if not any(row['index'] == self._active_region for row in display):
            self._active_region = -1
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

    def snap_time(self, seconds, bypass=False):
        if bypass or not self._snap_enabled or not self._note_edges:
            return seconds
        index = bisect.bisect_left(self._note_edges, seconds)
        candidates = self._note_edges[max(0, index - 1):index + 1]
        nearest = min(candidates, key=lambda value: abs(value - seconds))
        return nearest if abs(nearest - seconds) * self.pixels_per_second <= 9 else seconds

    def display_regions(self):
        if self._region_preview is None:
            return self._regions
        index, start, end = self._region_preview
        return [{**row, 'start': start, 'end': end} if row['index'] == index else row
                for row in self._regions]

    def selection_color(self):
        active=next((row for row in self._regions if row['index']==self._active_region),None)
        return QColor(active['color']) if active is not None else style_preset_color(self._selection_preset)

    def mousePressEvent(self, event):
        if not self.isEnabled() or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_anchor = self.snap_time(self._time_at(event.pos().x()), bool(event.modifiers() & Qt.AltModifier))
        self._drag_pixels = 0
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.isEnabled() or self._drag_anchor is None:
            super().mouseMoveEvent(event)
            return
        self._drag_pixels = max(self._drag_pixels, abs(event.pos().x() - self.time_to_x(self._drag_anchor)))
        if self._drag_pixels >= 2:
            self.set_selection(self._drag_anchor, self.snap_time(self._time_at(event.pos().x()), bool(event.modifiers() & Qt.AltModifier)))
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self.isEnabled() or self._drag_anchor is None or event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._drag_pixels >= 2:
            self.set_selection(self._drag_anchor, self.snap_time(self._time_at(event.pos().x()), bool(event.modifiers() & Qt.AltModifier)))
        committed=self.selection() if self._drag_pixels>=2 else None
        self._drag_anchor = None
        self._drag_pixels = 0
        if committed is not None:self.selectionCommitted.emit(committed)
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
        colors = _timeline_theme(self)
        painter.fillRect(self.rect(), QColor(colors['input']))
        panel = QRectF(self.PIANO_WIDTH, 0, max(0, self.width() - self.PIANO_WIDTH), self.height())
        painter.fillRect(panel, QColor(colors['input']))
        self._paint_rows(painter)
        self._paint_ruler(painter, visible)
        self._paint_regions(painter)
        self._paint_grid(painter, visible)
        self._paint_notes(painter)
        self._paint_selection(painter)
        self._paint_empty_message(painter)
        painter.end()

    def _paint_rows(self, painter):
        colors = _timeline_theme(self)
        for row in range(128):
            pitch = 127 - row
            y = self.RULER_HEIGHT + row * self.PITCH_HEIGHT
            black = pitch % 12 in (1, 3, 6, 8, 10)
            key_color = QColor('#171c22') if black else QColor('#f8f7f1')
            painter.fillRect(0, y, self.PIANO_WIDTH, self.PITCH_HEIGHT, key_color)
            if black:
                painter.fillRect(self.PIANO_WIDTH - 12, y, 12, self.PITCH_HEIGHT, QColor('#0d1116'))
            row_color = _blend_color(colors['input'], colors['surface'], .6) if black else QColor(colors['input'])
            painter.fillRect(self.PIANO_WIDTH, y, self.width() - self.PIANO_WIDTH,
                             self.PITCH_HEIGHT, row_color)
            line = _blend_color(colors['input'], colors['border'], .85 if pitch % 12 == 0 else .3)
            painter.setPen(QPen(line, 0))
            painter.drawLine(0, y, self.width(), y)
            painter.setPen(QColor('#fffdf4') if black else QColor('#222830'))
            font = painter.font()
            font.setPointSizeF(11.5)
            painter.setFont(font)
            painter.drawText(QRectF(4, y, self.PIANO_WIDTH - 9, self.PITCH_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, _pitch_name(pitch))

    def _paint_grid(self, painter, visible):
        colors = _timeline_theme(self)
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
            line = QColor(colors['border']) if is_major else _blend_color(colors['input'], colors['border'], .35)
            painter.setPen(QPen(line, 1 if is_major else 0))
            painter.drawLine(int(round(x)), self.RULER_HEIGHT, int(round(x)), self.height())
        painter.fillRect(left, self.RULER_HEIGHT - 1, self.width() - left, 1, QColor(colors['border']))

    def _paint_ruler(self, painter, visible):
        colors = _timeline_theme(self)
        left = self.PIANO_WIDTH
        painter.fillRect(left, 0, self.width() - left, self.RULER_HEIGHT, QColor(colors['surface']))
        painter.fillRect(0, 0, left, self.RULER_HEIGHT, QColor(colors['sidebar']))
        painter.setPen(QColor(colors['text']))
        font = painter.font()
        font.setPointSizeF(11)
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
            painter.setPen(QColor(colors['border_hover']))
            painter.drawLine(int(round(x)), self.RULER_HEIGHT - 7,
                             int(round(x)), self.RULER_HEIGHT)
            painter.setPen(QColor(colors['text']))
            label = f'{seconds:.{decimals}f} s'
            painter.drawText(QRectF(x + 4, 3, 82, 17), Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.setPen(QColor(colors['border']))
        painter.drawLine(left, 0, left, self.height())

    def _paint_regions(self, painter):
        painter.save()
        painter.setClipRect(QRectF(self.PIANO_WIDTH, 0,
                                   self.width() - self.PIANO_WIDTH, self.height()))
        for region in self.display_regions():
            left = self.time_to_x(region['start'])
            right = self.time_to_x(region['end'])
            if right <= self.PIANO_WIDTH or left >= self.width():
                continue
            color = QColor(region['color'])
            background = QColor(color)
            active = region['index'] == self._active_region
            background.setAlpha(76 if active else 44)
            painter.fillRect(QRectF(left, self.RULER_HEIGHT, max(1, right - left),
                                    self.height() - self.RULER_HEIGHT), background)
            ruler = QColor(color)
            ruler.setAlpha(82)
            top=self.REGION_LANE_TOP
            painter.fillRect(QRectF(left, top, max(1, right - left), self.RULER_HEIGHT - top), ruler)
            available = right - left - 8
            if available >= 36:
                painter.setPen(QColor('#eef6fc'))
                metrics = painter.fontMetrics()
                label = metrics.elidedText(region['label'], Qt.ElideRight, int(available))
                painter.drawText(QRectF(left + 4, top, available, self.RULER_HEIGHT - top),
                                 Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.setPen(QPen(QColor(color), 2 if active else 1))
            painter.drawLine(int(round(left)), self.RULER_HEIGHT,
                             int(round(left)), self.height())
            painter.drawLine(int(round(right)), self.RULER_HEIGHT,
                             int(round(right)), self.height())
        painter.restore()

    def _paint_notes(self, painter):
        painter.save()
        colors = _timeline_theme(self)
        font = painter.font()
        font.setPointSizeF(11.5)
        painter.setFont(font)
        painter.setClipRect(QRectF(self.PIANO_WIDTH, self.RULER_HEIGHT,
                                   self.width() - self.PIANO_WIDTH,
                                   self.height() - self.RULER_HEIGHT))
        for note in self._notes:
            left = self.time_to_x(note['start'])
            right = self.time_to_x(note['end'])
            y = self.pitch_to_y(note['pitch'])
            width = max(2.0, right - left)
            color = QColor(colors['accent']) if note['color'] == _TRACK_COLORS[0] else QColor(note['color'])
            painter.setPen(QPen(color.darker(155), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(left, y + 2, width, self.PITCH_HEIGHT - 4), 4, 4)
            lyric = note.get('lyric', '')
            if lyric and width >= 18:
                painter.setClipRect(QRectF(left + 2, y + 2, max(0, width - 4),
                                           self.PITCH_HEIGHT - 4))
                painter.setPen(QColor(colors['accent_text']) if note['color'] == _TRACK_COLORS[0] else QColor('#101526'))
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
        if self._region_preview is not None:
            _,start,end=self._region_preview
            left,right=self.time_to_x(start),self.time_to_x(end)
        color = self.selection_color()
        fill = QColor(color)
        fill.setAlpha(32)
        painter.fillRect(QRectF(left, self.RULER_HEIGHT, max(1, right - left),
                                self.height() - self.RULER_HEIGHT), fill)
        painter.setPen(QPen(color, 2))
        painter.drawLine(int(round(left)), self.RULER_HEIGHT, int(round(left)), self.height())
        painter.drawLine(int(round(right)), self.RULER_HEIGHT, int(round(right)), self.height())

    def _paint_empty_message(self, painter):
        if self._notes:
            return
        painter.setPen(QColor(_timeline_theme(self)['muted']))
        font = painter.font()
        font.setPointSizeF(11.5)
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
        colors = _timeline_theme(self)
        painter.fillRect(self.rect(), QColor(colors['sidebar']))
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
            font.setPointSizeF(11.5)
            painter.setFont(font)
            painter.drawText(QRectF(4, y, self.width() - 9, self.canvas.PITCH_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, _pitch_name(pitch))
        # Draw the corner after scrolling rows so a partially visible row cannot
        # cover the fixed pitch heading.
        painter.fillRect(0, 0, self.width(), self.canvas.RULER_HEIGHT, QColor(colors['sidebar']))
        painter.setPen(QColor(colors['text']))
        font.setPointSizeF(11)
        painter.setFont(font)
        painter.drawText(QRectF(4, 0, self.width() - 8, self.canvas.RULER_HEIGHT),
                         Qt.AlignCenter, _tr('音高'))
        painter.setPen(QColor(colors['border']))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        painter.end()


class _TimelineRulerHeader(QWidget):
    """Sticky seconds and editable style lane; project notes stay read-only."""
    def __init__(self, canvas, horizontal_scroll, parent=None, timeline=None):
        super().__init__(parent)
        self.canvas = canvas
        self.horizontal_scroll = horizontal_scroll
        self.timeline = timeline
        self.visible_ticks = ()
        self.setObjectName('style-timeline-ruler-header')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        if self.timeline is not None and self.isEnabled() and event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self.timeline._ruler_press(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.timeline is not None and self.isEnabled():
            self.timeline._ruler_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.timeline is not None and self.isEnabled() and event.button() == Qt.LeftButton:
            self.timeline._ruler_release(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _visible_span(self):
        left, right = self.canvas.PIANO_WIDTH, self.canvas.PIANO_WIDTH + self.width()
        offset = self.horizontal_scroll.value()
        return self.canvas.x_to_time(offset + left), self.canvas.x_to_time(offset + right)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        colors = _timeline_theme(self)
        painter.fillRect(self.rect(), QColor(colors['surface']))
        start, end = self._visible_span()
        offset = self.horizontal_scroll.value() + self.canvas.PIANO_WIDTH
        top=self.canvas.REGION_LANE_TOP
        handle=self.canvas.REGION_HANDLE_WIDTH
        font=painter.font();font.setPointSizeF(11);painter.setFont(font)
        for region in self.canvas.display_regions():
            left = self.canvas.time_to_x(region['start']) - offset
            right = self.canvas.time_to_x(region['end']) - offset
            if right <= 0 or left >= self.width():
                continue
            color = QColor(region['color'])
            band = QColor(color)
            active = region['index'] == self.canvas._active_region
            band.setAlpha(255 if active else 220)
            painter.fillRect(QRectF(left, top, max(1, right - left), self.height() - top), band)
            if active:
                painter.setPen(QPen(QColor('#ffffff'), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(left+1, top+1, max(1, right-left-2), self.height()-top-2))
            if right-left>=handle*2:
                grip=QColor('#ffffff');grip.setAlpha(65 if active else 35)
                for edge,direction in ((left,-1),(right-handle,1)):
                    painter.fillRect(QRectF(edge+1,top+3,handle-2,self.height()-top-6),grip)
                    cx=edge+handle/2;cy=(top+self.height())/2
                    painter.setPen(Qt.NoPen);painter.setBrush(QColor('#ffffff'))
                    painter.drawPolygon(QPolygonF([QPointF(cx+direction*3,cy),
                        QPointF(cx-direction*3,cy-5),QPointF(cx-direction*3,cy+5)]))
                painter.setBrush(Qt.NoBrush)
            label_left=max(0,left+handle+5)
            label_right=min(self.width(),right-handle-5)
            available = label_right-label_left
            if available >= 36:
                painter.setPen(QColor('#ffffff'))
                label = painter.fontMetrics().elidedText(region['label']+' · '+_tr('拖动移动'), Qt.ElideRight,
                                                        int(available))
                painter.drawText(QRectF(label_left, top, available, self.height() - top),
                                 Qt.AlignLeft | Qt.AlignVCenter, label)
        major = self.canvas._major_interval()
        decimals = 0 if major >= 1 else (1 if major >= .1 else 2)
        ticks = self.canvas._major_ticks(start, end)
        if not ticks:
            ticks = (min(end, max(start, (start + end) / 2)),)
        self.visible_ticks = ticks
        font = painter.font()
        font.setPointSizeF(11)
        painter.setFont(font)
        for seconds in ticks:
            x = self.canvas.time_to_x(seconds) - offset
            if x < -1 or x > self.width() + 1:
                continue
            painter.setPen(QColor(colors['border_hover']))
            painter.drawLine(int(round(x)), 19,int(round(x)),top)
            painter.setPen(QColor(colors['text']))
            painter.drawText(QRectF(x + 4, 3, 82, 17),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             f'{seconds:.{decimals}f} s')
        painter.setPen(QColor(colors['border']))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class StyleTimeline(QWidget):
    """Edit style regions over a read-only piano roll of an existing SVP."""
    selectionChanged = pyqtSignal(object)
    selectionCommitted = pyqtSignal(object)
    regionSelected = pyqtSignal(int)
    regionEditRequested = pyqtSignal(int, float, float)
    regionDeleteRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('style-timeline')
        self.setMinimumSize(0, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(_tr('钢琴卷帘时间轴'))
        self.setAccessibleDescription(_tr('拖选时间；上方片段可拖移和拉伸，Delete 删除，Esc 取消。'))
        self._center_on_show = True
        self._region_gesture = None
        self._range_gesture = None
        self._pan_origin = None
        self._pan_button = None;self._pan_enabled=False;self._initial_note_view_pending=True
        self._drag_pointer = None
        self._edge_pan_timer=QTimer(self);self._edge_pan_timer.setInterval(30)
        self._edge_pan_timer.timeout.connect(self._pan_drag_tick)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        self.draw_mode_button=QToolButton(self);self.draw_mode_button.setText(_tr('绘制片段'))
        self.hand_mode_button=QToolButton(self);self.hand_mode_button.setText(_tr('移动视野'))
        self._mode_group=QButtonGroup(self);self._mode_group.setExclusive(True)
        for control in (self.draw_mode_button,self.hand_mode_button):
            control.setCheckable(True);self._mode_group.addButton(control);toolbar.addWidget(control)
        self.draw_mode_button.setChecked(True)
        self.draw_mode_button.clicked.connect(lambda:self.set_pan_mode(False))
        self.hand_mode_button.clicked.connect(lambda:self.set_pan_mode(True))
        self.hint = QLabel(_tr('拖选时间；拖动上方片段或边缘可编辑。'), self)
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
        self.readable_button = QToolButton(self)
        self.readable_button.setText(_tr('编辑视野'))
        self.readable_button.setToolTip(_tr('恢复清晰的音符大小和短句视野'))
        toolbar.addWidget(self.readable_button)
        self.fit_button = QToolButton(self)
        self.fit_button.setText(_tr('全曲'))
        self.fit_button.setToolTip(_tr('缩放至完整工程'))
        self.note_start_button=QToolButton(self);self.note_start_button.setText(_tr('音符起点'))
        self.note_start_button.setToolTip(_tr('音符起点'))
        toolbar.addWidget(self.note_start_button)
        self.zoom_selection_button = QToolButton(self)
        self.zoom_selection_button.setText(_tr('选区'))
        self.zoom_selection_button.setToolTip(_tr('放大当前时间选区'))
        self.snap_button = QToolButton(self)
        self.snap_button.setText(_tr('吸附'))
        self.snap_button.setCheckable(True)
        self.snap_button.setToolTip(_tr('吸附到附近音符边界；按 Alt 临时关闭'))
        for tool in (self.fit_button, self.zoom_selection_button, self.snap_button):
            tool.setAccessibleName(tool.toolTip())
            toolbar.addWidget(tool)
        # Flowing tools keep the viewport usable at narrow window widths.
        tools_widget = QWidget(self)
        tools_flow = FlowLayout(tools_widget, spacing=5)
        while toolbar.count():
            item = toolbar.takeAt(0)
            if item.widget() is not None:
                tools_flow.addWidget(item.widget())
        self._layout.addWidget(tools_widget)
        size_widget = QWidget(self)
        size_row = QHBoxLayout(size_widget)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(5)
        size_row.addWidget(QLabel(_tr('行高'), self))
        self.row_height_choice = QComboBox(self)
        for text, height in (('80%', 32), ('100%', 40), ('125%', 50), ('150%', 60)):
            self.row_height_choice.addItem(text, height)
        self.row_height_choice.setCurrentIndex(1)
        self.row_height_choice.setAccessibleName(_tr('音符行高'))
        self.row_height_choice.setMaximumWidth(116)
        size_row.addWidget(self.row_height_choice)
        # Row height lives alongside horizontal zoom, rather than changing data.
        tools_flow.addWidget(size_widget)
        self._canvas = _TimelineCanvas(self)
        self._view = QScrollArea(self)
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setWidgetResizable(False)
        self._view.setMinimumSize(0, 176)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setWidget(self._canvas)
        self._layout.addWidget(self._view, 1)
        nav_widget=QWidget(self);nav_widget.setObjectName('timeline-navigation')
        navigation=QHBoxLayout(nav_widget);navigation.setContentsMargins(8,5,8,5);navigation.setSpacing(8)
        self.pan_left_button=QToolButton(self);self.pan_left_button.setArrowType(Qt.LeftArrow)
        self.pan_right_button=QToolButton(self);self.pan_right_button.setArrowType(Qt.RightArrow)
        for control,text,direction in ((self.pan_left_button,'向左移动视野',-1),
                                       (self.pan_right_button,'向右移动视野',1)):
            control.setToolTip(_tr(text));control.setAccessibleName(_tr(text))
            control.setText(_tr('向左看') if direction<0 else _tr('向右看'))
            control.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            control.setMinimumWidth(100);control.setIconSize(QSize(18,18))
            control.setAutoRepeat(True);control.setAutoRepeatDelay(300);control.setAutoRepeatInterval(80)
            control.clicked.connect(lambda checked=False,d=direction:self.pan_view(d))
        navigation.addWidget(self.pan_left_button)
        self.pan_hint=QLabel(self);self.pan_hint.setAlignment(Qt.AlignCenter)
        self.pan_hint.setProperty('role','muted');self.pan_hint.setMinimumWidth(0)
        self.pan_hint.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.pan_hint.setToolTip(_tr('拖动滚动条浏览时间；也可切换“移动视野”后左键拖动画面。'))
        navigation.addWidget(self.pan_hint,1);navigation.addWidget(self.pan_right_button)
        self._layout.addWidget(nav_widget)
        viewport = self._view.viewport()
        self._piano_header = _TimelinePianoHeader(
            self._canvas, self._view.verticalScrollBar(), viewport)
        self._ruler_header = _TimelineRulerHeader(
            self._canvas, self._view.horizontalScrollBar(), viewport, timeline=self)
        self._ruler_header.setToolTip(_tr('点击片段选中，拖动中部移动，拖动两侧调整范围。'))
        self._piano_header.show()
        self._ruler_header.show()
        viewport.installEventFilter(self)
        self._view.horizontalScrollBar().valueChanged.connect(self._sync_headers)
        self._view.horizontalScrollBar().rangeChanged.connect(self._sync_headers)
        self._view.verticalScrollBar().valueChanged.connect(self._sync_headers)
        self._canvas.selectionChanged.connect(self._selection_changed)
        self._canvas.selectionCommitted.connect(self.selectionCommitted)
        self._canvas.zoomRequested.connect(self.zoom_by)
        self.zoom_out.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        self.zoom_in.clicked.connect(lambda: self.zoom_by(1.2))
        self.fit_button.clicked.connect(self.fit_project)
        self.note_start_button.clicked.connect(self.focus_note_start)
        self.readable_button.clicked.connect(self.readable_zoom)
        self.row_height_choice.currentIndexChanged.connect(
            lambda: self.set_pitch_height(self.row_height_choice.currentData()))
        self.zoom_selection_button.clicked.connect(self.zoom_to_selection)
        self.snap_button.toggled.connect(self.set_snap_enabled)
        self._canvas.installEventFilter(self)
        self._ruler_header.installEventFilter(self)
        self.zoom_selection_button.setEnabled(False)
        self._sync_headers()

    def _selection_changed(self, selection):
        self.zoom_selection_button.setEnabled(self.isEnabled() and selection is not None)
        self.selectionChanged.emit(selection)

    def set_active_region(self, index):
        self._canvas._active_region = index if type(index) is int and any(
            row['index'] == index for row in self._canvas._regions) else -1
        self._canvas.update()
        self._ruler_header.update()

    def set_selection_style(self,preset):
        self._canvas._selection_preset=preset if preset in STYLE_PRESET_COLORS else style_catalog.DEFAULT_STYLE_ID
        self._canvas.update()

    def complete_note_range(self,start,end):
        first,last=_finite_number(start),_finite_number(end)
        if first is None or last is None or last<=first:
            raise ValueError(_tr('时间轴选区端点必须是有限数值。'))
        # A boundary is safe only if it cuts no selected-track note. Merge
        # overlapping notes first, including overlaps across different tracks.
        components=[];epsilon=1e-9
        for note in sorted(self._canvas._notes,key=lambda n:n['start']):
            a,b=note['start'],note['end']
            if components and a<components[-1][1]-epsilon:
                components[-1]=(components[-1][0],max(components[-1][1],b))
            else:components.append((a,b))
        touched=[(a,b) for a,b in components if a<last-epsilon and b>first+epsilon]
        if not touched:raise ValueError(_tr('选区内没有音符，请选择有音符的范围。'))
        return (min(first,touched[0][0]),max(last,touched[-1][1]))

    def pan_mode(self):return self._pan_enabled

    def set_pan_mode(self,enabled):
        self._cancel_gesture();self._pan_origin=None;self._pan_button=None
        self._pan_enabled=bool(enabled)
        with QSignalBlocker(self.draw_mode_button),QSignalBlocker(self.hand_mode_button):
            self.draw_mode_button.setChecked(not enabled);self.hand_mode_button.setChecked(bool(enabled))
        cursor=Qt.OpenHandCursor if enabled else Qt.CrossCursor
        self._canvas.setCursor(cursor);self._ruler_header.setCursor(cursor)

    def focus_note_start(self):
        notes=self._canvas._notes
        if not notes:return False
        first=min(notes,key=lambda note:note['start'])
        self.set_zoom(self._canvas.DEFAULT_ZOOM)
        self._restore_view_time(first['start'],self._canvas.PIANO_WIDTH+24)
        y=self._canvas.pitch_to_y(first['pitch'])+self._canvas.PITCH_HEIGHT/2-self._view.viewport().height()/2
        self._view.verticalScrollBar().setValue(round(y));self._sync_headers()
        self._initial_note_view_pending=False
        return True

    def _focus_initial_notes(self):
        if self._initial_note_view_pending and self.isVisible():self.focus_note_start()

    def pan_view(self,direction):
        if not self.isEnabled():return
        bar=self._view.horizontalScrollBar()
        distance=max(80,round((self._view.viewport().width()-self._canvas.PIANO_WIDTH)*.65))
        bar.setValue(bar.value()+(-distance if direction<0 else distance))

    def _update_pan_controls(self):
        if not hasattr(self,'pan_left_button'):return
        bar=self._view.horizontalScrollBar();enabled=self.isEnabled()
        self.pan_left_button.setEnabled(enabled and bar.value()>bar.minimum())
        self.pan_right_button.setEnabled(enabled and bar.value()<bar.maximum())
        start=self.time_at_view_x(self._canvas.PIANO_WIDTH)
        end=self.time_at_view_x(self._view.viewport().width())
        self.pan_hint.setText(f'{start:.2f} – {end:.2f} s')

    def _pan_drag_tick(self):
        if self._drag_pointer is None or not self.isEnabled() or (
                self._region_gesture is None and self._range_gesture is None):
            self._edge_pan_timer.stop();return
        point,modifiers=self._drag_pointer
        width=self._ruler_header.width();edge=32
        if point.x()<edge:step=-max(4,min(32,edge-point.x()))
        elif point.x()>width-edge:step=max(4,min(32,point.x()-(width-edge)))
        else:self._edge_pan_timer.stop();return
        bar=self._view.horizontalScrollBar();previous=bar.value()
        bar.setValue(previous+step)
        if bar.value()==previous:self._edge_pan_timer.stop();return
        self._ruler_move(QMouseEvent(QEvent.MouseMove,QPointF(point),Qt.NoButton,Qt.LeftButton,modifiers))

    def set_snap_enabled(self, enabled):
        self._canvas._snap_enabled = bool(enabled)
        if self.snap_button.isChecked() != bool(enabled):
            self.snap_button.setChecked(bool(enabled))

    def snap_enabled(self):
        return self._canvas._snap_enabled

    def pitch_height(self):
        return self._canvas.PITCH_HEIGHT

    def set_pitch_height(self, pixels):
        try:
            value = float(pixels)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(value):
            return False
        value = max(32, min(64, int(round(value))))
        canvas, bar = self._canvas, self._view.verticalScrollBar()
        center = bar.value() + self._view.viewport().height() / 2.
        pitch_row = (center - canvas.RULER_HEIGHT) / canvas.PITCH_HEIGHT
        self._cancel_gesture()
        canvas.PITCH_HEIGHT = value
        canvas._refresh_geometry()
        bar.setValue(round(canvas.RULER_HEIGHT + pitch_row * value - self._view.viewport().height() / 2.))
        with QSignalBlocker(self.row_height_choice):
            self.row_height_choice.setCurrentIndex(self.row_height_choice.findData(value))
        self._sync_headers()
        return True

    def readable_zoom(self):
        bounds = self.selection()
        center_x = (self._canvas.PIANO_WIDTH + self._view.viewport().width()) / 2.
        seconds = sum(bounds) / 2. if bounds else self.time_at_view_x(center_x)
        self.set_zoom(self._canvas.DEFAULT_ZOOM)
        self._restore_view_time(seconds, center_x)
        self._center_vertical_on_notes(visible_only=True)
        return True

    def fit_project(self):
        start, end = self._canvas.scene_bounds()
        available = max(1, self._view.viewport().width() - self._canvas.PIANO_WIDTH - 18)
        self.set_zoom(available / ((end - start) * self._canvas.BASE_PIXELS_PER_SECOND))
        self._view.horizontalScrollBar().setValue(0)
        self._center_vertical_on_notes()
        return True

    def zoom_to_selection(self):
        bounds = self.selection()
        if bounds is None:
            return False
        available = max(1, self._view.viewport().width() - self._canvas.PIANO_WIDTH - 18)
        self.set_zoom(available / ((bounds[1] - bounds[0]) * 1.15 * self._canvas.BASE_PIXELS_PER_SECOND))
        self._restore_view_time(sum(bounds) / 2, (self._canvas.PIANO_WIDTH + self._view.viewport().width()) / 2)
        return True

    def _ruler_x(self, event):
        return event.pos().x() + self._view.horizontalScrollBar().value() + self._canvas.PIANO_WIDTH

    def _region_hit(self, x):
        for row in reversed(self._canvas._regions):
            left, right = self._canvas.time_to_x(row['start']), self._canvas.time_to_x(row['end'])
            if left - 12 <= x <= right + 12:
                mode = 'move'
                if min(abs(x - left), abs(x - right)) <= 12:
                    mode = 'start' if abs(x - left) <= abs(x - right) else 'end'
                return row, mode
        return None, None

    def _ruler_press(self, event):
        x = self._ruler_x(event)
        row, mode = self._region_hit(x) if event.pos().y() >= self._canvas.REGION_LANE_TOP else (None, None)
        if row is not None:
            self.set_active_region(row['index'])
            self.regionSelected.emit(row['index'])
            self._region_gesture = {**row, 'mode': mode, 'anchor': self._canvas.x_to_time(x), 'anchor_x': x}
            self._ruler_header.setCursor(Qt.ClosedHandCursor if mode=='move' else Qt.SizeHorCursor)
        else:
            self.set_active_region(-1)
            self.regionSelected.emit(-1)
            self._range_gesture = (x, self._canvas.snap_time(self._canvas.x_to_time(x), bool(event.modifiers() & Qt.AltModifier)))

    def _ruler_move(self, event):
        x = self._ruler_x(event)
        gesture=self._region_gesture
        anchor_x=gesture['anchor_x'] if gesture is not None else self._range_gesture[0] if self._range_gesture else x
        if (gesture is not None or self._range_gesture is not None) and abs(x-anchor_x)>=2:
            self._drag_pointer=(QPoint(event.pos()),event.modifiers())
            if event.pos().x()<32 or event.pos().x()>self._ruler_header.width()-32:
                if not self._edge_pan_timer.isActive():self._edge_pan_timer.start()
            else:self._edge_pan_timer.stop()
        if self._range_gesture is not None:
            anchor_x, anchor_time = self._range_gesture
            if abs(x - anchor_x) >= 2:
                self.set_selection(anchor_time, self._canvas.snap_time(self._canvas.x_to_time(x), bool(event.modifiers() & Qt.AltModifier)))
            return
        if gesture is None:
            row, mode = self._region_hit(x) if event.pos().y() >= self._canvas.REGION_LANE_TOP else (None, None)
            self._ruler_header.setCursor(Qt.SizeHorCursor if row and mode != 'move' else Qt.OpenHandCursor if row else Qt.CrossCursor)
            text=('拖动左边界调整开始时间' if mode=='start' else '拖动右边界调整结束时间' if mode=='end' else
                  '拖动片段移动；靠近边缘自动滚动' if row else '点击片段选中，拖动中部移动，拖动两侧调整范围。')
            self._ruler_header.setToolTip(_tr(text))
            return
        if self._canvas._region_preview is None and abs(x - gesture['anchor_x']) < 2:
            return
        start, end = gesture['start'], gesture['end']
        minimum, maximum = self._canvas.scene_bounds()
        for other in self._canvas._regions:
            if other['index'] == gesture['index']:
                continue
            if other['end'] <= gesture['start']:
                minimum = max(minimum, other['end'])
            if other['start'] >= gesture['end']:
                maximum = min(maximum, other['start'])
        seconds = self._canvas.x_to_time(x)
        bypass = bool(event.modifiers() & Qt.AltModifier)
        if gesture['mode'] == 'move':
            duration = end - start
            start += seconds - gesture['anchor']
            end = start + duration
            shifts = (self._canvas.snap_time(start, bypass) - start,
                      self._canvas.snap_time(end, bypass) - end)
            nonzero = [shift for shift in shifts if shift]
            if nonzero:
                start += min(nonzero, key=abs)
            start = max(minimum, min(maximum - duration, start))
            end = start + duration
        elif gesture['mode'] == 'start':
            start = max(minimum, min(end - .025, self._canvas.snap_time(seconds, bypass)))
        else:
            end = min(maximum, max(start + .025, self._canvas.snap_time(seconds, bypass)))
        self._canvas._region_preview = (gesture['index'], start, end)
        self._canvas.update()
        self._ruler_header.update()

    def _ruler_release(self, event):
        if self._region_gesture is not None or self._range_gesture is not None:
            self._ruler_move(event)
        gesture, preview = self._region_gesture, self._canvas._region_preview
        committed=(self.selection() if self._range_gesture is not None and
                   abs(self._ruler_x(event)-self._range_gesture[0])>=2 else None)
        self._cancel_gesture()
        if gesture is not None and preview is not None and (
                abs(preview[1] - gesture['start']) > 1e-9 or abs(preview[2] - gesture['end']) > 1e-9):
            self.regionEditRequested.emit(*preview)
        elif committed is not None:self.selectionCommitted.emit(committed)

    def _cancel_gesture(self):
        self._edge_pan_timer.stop();self._drag_pointer=None
        self._region_gesture = None
        self._range_gesture = None
        self._canvas._region_preview = None
        self._canvas._drag_anchor = None
        self._canvas._drag_pixels = 0
        self._canvas.update()
        self._ruler_header.update()
        self._update_pan_controls()

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
        self._update_pan_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_headers()
        if self._center_on_show:
            self._center_vertical_on_notes()
            self._center_on_show = False
        QTimer.singleShot(0,self._focus_initial_notes)

    def hideEvent(self,event):
        self._cancel_gesture()
        super().hideEvent(event)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.hint.setEnabled(enabled)
        self.zoom_out.setEnabled(enabled)
        self.zoom_in.setEnabled(enabled)
        self.zoom_percent.setEnabled(enabled)
        self._view.setEnabled(enabled)
        self._canvas.setEnabled(enabled)
        self.zoom_selection_button.setEnabled(enabled and self.selection() is not None)
        self._update_pan_controls()
        if not enabled:
            self._cancel_gesture()
            self._pan_origin = None
            self._canvas._drag_anchor = None
            self._canvas._drag_pixels = 0

    def selection(self):
        return self._canvas.selection()

    def set_selection(self, start, end):
        return self._canvas.set_selection(start, end)

    def clear_selection(self):
        self._canvas.clear_selection()

    def set_project(self, project, track_indices):
        self._cancel_gesture()
        self._canvas.set_project(project, track_indices)
        self._initial_note_view_pending=True
        bar = self._view.horizontalScrollBar()
        bar.setValue(bar.minimum())
        self._center_on_show = not self.isVisible()
        if not self._center_on_show:
            self._center_vertical_on_notes()
        self._update_zoom_label()
        self._sync_headers()
        if self.isVisible():QTimer.singleShot(0,self._focus_initial_notes)

    def set_regions(self, regions):
        self._cancel_gesture()
        self._canvas.set_regions(regions)
        self._sync_headers()

    def zoom_factor(self):
        return self._canvas.zoom_factor()

    def set_zoom(self, factor):
        center_x = max(0, self._view.viewport().width() // 2)
        return self._set_zoom_at(factor, center_x)

    def _set_zoom_at(self, factor, center_x):
        self._initial_note_view_pending=False
        self._cancel_gesture()
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

    def _center_vertical_on_notes(self, visible_only=False):
        notes = self._canvas._notes
        if visible_only:
            start = self.time_at_view_x(self._canvas.PIANO_WIDTH)
            end = self.time_at_view_x(self._view.viewport().width())
            visible = [note for note in notes if note['start'] < end and note['end'] > start]
            notes = visible or notes
        pitches = [note['pitch'] for note in notes]
        target = (min(pitches) + max(pitches)) / 2 if pitches else 60
        y = self._canvas.pitch_to_y(target) - self._view.viewport().height() / 2
        bar = self._view.verticalScrollBar()
        bar.setValue(int(round(max(bar.minimum(), min(bar.maximum(), y)))))
        self._sync_headers()

    def _update_zoom_label(self):
        self.zoom_percent.setText(f'{round(self.zoom_factor() * 100)}%')

    def keyPressEvent(self, event):
        if self.isEnabled() and event.key() in (Qt.Key_Left,Qt.Key_Right) and event.modifiers()==Qt.NoModifier:
            self.pan_view(-1 if event.key()==Qt.Key_Left else 1);event.accept();return
        if self.isEnabled() and event.key() == Qt.Key_Delete and self._canvas._active_region >= 0:
            self.regionDeleteRequested.emit(self._canvas._active_region)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._cancel_gesture()
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
        if watched in (self._canvas, self._ruler_header) and self.isEnabled():
            if self._pan_enabled and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
                self._pan_origin=(event.globalPos(),self._view.horizontalScrollBar().value(),self._view.verticalScrollBar().value())
                self._pan_button=Qt.LeftButton;watched.setCursor(Qt.ClosedHandCursor);event.accept();return True
            if watched is self._canvas and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
                self.set_active_region(-1);self.regionSelected.emit(-1)
            if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Escape, Qt.Key_Delete,Qt.Key_Left,Qt.Key_Right):
                self.keyPressEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta and event.modifiers() & Qt.ControlModifier:
                    x = event.pos().x() - self._view.horizontalScrollBar().value() if watched is self._canvas else event.pos().x() + self._canvas.PIANO_WIDTH
                    self._set_zoom_at(self.zoom_factor() * (1.2 if delta > 0 else 1 / 1.2), x)
                    event.accept()
                    return True
                if delta and event.modifiers() & Qt.ShiftModifier:
                    bar = self._view.horizontalScrollBar()
                    bar.setValue(bar.value() - delta)
                    event.accept()
                    return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                self._pan_button=Qt.MiddleButton
                self._pan_origin = (event.globalPos(), self._view.horizontalScrollBar().value(), self._view.verticalScrollBar().value())
                watched.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return True
            if event.type() == QEvent.MouseMove and self._pan_origin is not None:
                origin, x, y = self._pan_origin
                delta = event.globalPos() - origin
                self._view.horizontalScrollBar().setValue(x - delta.x())
                self._view.verticalScrollBar().setValue(y - delta.y())
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == self._pan_button:
                self._pan_origin = None;self._pan_button=None
                watched.setCursor(Qt.OpenHandCursor if self._pan_enabled else Qt.CrossCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)
