"""Accessible presentation adapters; existing Qt controls remain value owners."""
from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt, QSignalBlocker
from PyQt5.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QRadioButton,
                            QLayout, QSizePolicy, QTextEdit, QWidget)


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
