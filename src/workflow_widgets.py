"""Reusable workflow navigation and real, aspect-preserving cover previews."""
from i18n import tr as _tr
from collections import deque

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                             QScrollArea, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

from desktop_theme import button, label


class BufferedLog(QObject):
    """Batch GUI text updates and bound pending memory during verbose inference."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.pending = deque(maxlen=1800)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)

    def append(self, text):
        # The full original log remains in the task's log file.
        self.pending.extend(str(text)[-32768:].splitlines() or [''])
        if not self.timer.isActive():
            self.timer.start()

    def flush(self):
        if not self.pending:
            return
        from PyQt5.QtGui import QTextCursor
        scroll = self.editor.verticalScrollBar()
        follow = scroll.value() >= scroll.maximum() - 4
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText('\n'.join(self.pending) + '\n')
        self.pending.clear()
        if follow:
            scroll.setValue(scroll.maximum())


class CoverPreview(QLabel):
    """Show one supplied image without inventing or stretching a replacement."""

    def __init__(self):
        super().__init__(_tr('选择封面后在这里预览'))
        self.original = QPixmap()
        self.setObjectName('cover-preview')
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(300, 250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setWordWrap(True)

    def setPixmap(self, pixmap):
        self.original = QPixmap(pixmap)
        self.setText('')
        self.render_image()

    def render_image(self):
        if self.original.isNull():
            return
        size = self.contentsRect().size()
        if size.width() <= 0 or size.height() <= 0:
            return
        super().setPixmap(
            self.original.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def show_placeholder(self, text):
        self.original = QPixmap()
        super().clear()
        self.setText(_tr(str(text)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_image()

    def clear(self):
        self.original = QPixmap()
        super().clear()


class StepWorkspace(QWidget):
    """A large four-step surface that keeps index, locking, and signal semantics."""

    changed = pyqtSignal(int)

    def __init__(self, titles):
        super().__init__()
        self.titles = tuple(_tr(title) for title in titles)
        self.locked = False
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.position = label('', 'muted')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.step_rail = QWidget()
        self.step_rail.setObjectName('footer')
        self.step_rail.setAccessibleName(_tr('工作流步骤'))
        rail = QHBoxLayout(self.step_rail)
        rail.setContentsMargins(8, 8, 8, 8)
        rail.setSpacing(8)
        self.buttons = []
        for index, title in enumerate(self.titles):
            item = button(
                f'{index + 1:02d}  {title}',
                lambda checked=False, i=index: self.select(i),
                'step-nav',
            )
            item.setCheckable(True)
            item.setMinimumHeight(54)
            item.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            item.setAccessibleName(_tr('步骤 {0}：{1}', f'{index + 1}', f'{title}'))
            self.buttons.append(item)
            rail.addWidget(item, 1)
        layout.addWidget(self.step_rail)

        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(self.titles)))
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setAccessibleName(_tr('发布步骤进度'))
        layout.addWidget(self.progress)

        layout.addWidget(self.stack, 1)

        navigation = QHBoxLayout()
        navigation.setSpacing(12)
        self.navigation = navigation
        self.previous = button(
            _tr('‹ 上一步'), lambda: self.select(self.stack.currentIndex() - 1)
        )
        self.previous.setMinimumHeight(48)
        navigation.addWidget(self.previous)
        navigation.addStretch()
        navigation.addWidget(self.position)
        navigation.addStretch()
        self.next = button(
            _tr('下一步 ›'), lambda: self.select(self.stack.currentIndex() + 1), 'primary'
        )
        self.next.setMinimumHeight(48)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)

        self.action_bar = QWidget()
        self.action_bar.setObjectName('footer')
        self.action_bar.setAccessibleName(_tr('当前步骤操作'))
        self.action_bar.hide()
        self.actions = QHBoxLayout(self.action_bar)
        self.actions.setContentsMargins(12, 8, 12, 8)
        self.actions.setSpacing(12)
        layout.addWidget(self.action_bar)

    @property
    def index(self):
        return self.stack.currentIndex()

    def add_step(self, title, subtitle):
        panel = QWidget()
        panel.setObjectName('step-surface')
        body = QVBoxLayout(panel)
        body.setContentsMargins(28, 24, 28, 24)
        body.setSpacing(18)
        body.addWidget(label(title, 'card-title'))
        if subtitle:
            body.addWidget(label(subtitle, 'muted'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        self.stack.addWidget(scroll)
        self.refresh()
        return panel, body

    def select(self, index):
        if self.locked:
            return False
        if not 0 <= index < self.stack.count():
            return False
        self.stack.setCurrentIndex(index)
        self.refresh()
        self.changed.emit(index)
        return True

    def refresh(self):
        index = self.stack.currentIndex()
        count = self.stack.count()
        self.previous.setEnabled(index > 0 and not self.locked)
        self.next.setEnabled(index + 1 < count and not self.locked)
        for i, item in enumerate(self.buttons):
            item.setChecked(i == index)
            item.setEnabled(not self.locked)
            item.setAccessibleDescription(
                _tr('当前步骤') if i == index else _tr('第 {0} 步', f'{i + 1}')
            )
        self.progress.setRange(0, max(1, count))
        self.progress.setValue(index + 1 if index >= 0 else 0)
        if index >= 0 and index < len(self.titles):
            self.position.setText(
                _tr('第 {0} / {1} 步 · {2}', f'{index + 1}', f'{len(self.titles)}', f'{self.titles[index]}')
            )

    def set_locked(self, value):
        self.locked = bool(value)
        self.refresh()
