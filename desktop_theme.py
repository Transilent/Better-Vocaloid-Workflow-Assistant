"""Original native-Qt dark workspace styling; no additional GUI dependency."""
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
import os
from pathlib import Path
from PyQt5.QtGui import QColor, QPalette, QFont, QFontDatabase, QPainter, QPen, QIcon
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                            QScrollArea, QToolButton, QVBoxLayout, QWidget)

STYLE = """
QWidget { background: transparent; color: #edf4fa; font-family: 'Microsoft YaHei'; font-size: 16px; }
QWidget#workspace { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #172838, stop:0.5 #18252e, stop:1 #172e31); }
QWidget#sidebar { background: rgba(12, 24, 34, 210); border-right: 1px solid rgba(173, 219, 235, 35); }
QFrame#card { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(53, 76, 90, 155), stop:1 rgba(30, 48, 58, 180)); border: 1px solid rgba(178, 220, 236, 48); border-radius: 18px; }
QLabel { background: transparent; }
QLabel[role="heading"] { font-size: 30px; font-weight: 600; }
QLabel[role="card-title"] { font-size: 19px; font-weight: 600; }
QLabel[role="muted"] { color: #b1c3d1; font-size: 15px; }
QLabel[role="success"] { color: #8fd6bd; }
QLabel[role="error"] { color: #ed9b91; }
QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox { background: rgba(13, 28, 40, 165); border: 1px solid rgba(175, 207, 229, 65); border-radius: 10px; padding: 10px; selection-background-color: #396e73; }
QLineEdit:hover, QTextEdit:hover, QComboBox:hover { border-color: #61868c; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #8ce3d5; background: #1c343e; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled { color: #69737c; background: #202328; }
QComboBox::drop-down { border: 0; width: 25px; }
QComboBox QAbstractItemView { background: #203844; color: #edf4fa; selection-background-color: #396e73; padding: 4px; }
QPushButton, QToolButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(79, 112, 127, 140), stop:1 rgba(46, 69, 83, 150)); border: 1px solid rgba(175, 214, 230, 65); border-radius: 11px; padding: 11px 18px; }
QPushButton:hover, QToolButton:hover { background: rgba(80, 126, 139, 180); border-color: #89c5c9; }
QPushButton:pressed, QToolButton:pressed { background: #284b59; }
QPushButton[role="primary"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a7edd6, stop:1 #80d8df); border-color: #b7f3e2; color: #122f37; font-weight: 600; }
QPushButton[role="primary"]:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #c2f6e5, stop:1 #a0e9ed); }
QPushButton[role="primary"]:pressed { background: #78cbd1; }
QPushButton[role="danger"] { background: #3c2929; color: #ed9b91; border-color: #62413d; }
QPushButton[role="nav"] { background: transparent; color: #a4adb5; border: 0; text-align: left; padding: 12px 15px; }
QPushButton[role="nav"]:checked { background: rgba(94, 183, 177, 42); color: #b7f3e2; border: 1px solid rgba(164, 235, 221, 65); }
QPushButton[role="nav"]:hover { background: rgba(91, 139, 154, 45); }
QPushButton:disabled, QToolButton:disabled { background: #25292e; color: #67727a; border-color: #30363b; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 19px; height: 19px; border-radius: 5px; border: 1px solid #74929e; background: #1c303d; }
QCheckBox::indicator:checked { background: #8fd6bd; border-color: #8fd6bd; }
QCheckBox::indicator:disabled { background: #30363b; border-color: #424a50; }
QProgressBar { background: #243f4a; border: 0; border-radius: 5px; height: 10px; text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9ee9c9, stop:1 #79d4e5); border-radius: 5px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: #1c2024; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #454f58; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #25292e; color: #a4adb5; padding: 8px 20px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #a5e4cc; border-bottom-color: #8fd6bd; }
QTreeWidget, QTableWidget { background: rgba(22, 42, 54, 180); border: 1px solid #42616f; border-radius: 12px; outline: 0; gridline-color: #42616f; }
QTableWidget::item { padding: 9px; }
QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { background: rgba(53, 76, 90, 155); border: 1px solid #42616f; border-radius: 10px; padding: 14px 9px; margin-bottom: 9px; }
QListWidget::item:selected { background: #396e73; border-color: #8ce3d5; }
QDoubleSpinBox { min-width: 90px; }
QTreeWidget::item { padding: 10px; }
QTreeWidget::item:selected { background: #2d4d40; }
QHeaderView::section { background: #25292e; color: #a4adb5; padding: 9px; border: 0; }
QMenu { background: #233b49; border: 1px solid #597f8e; padding: 7px; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: #386859; }
QToolTip { background: #30363b; color: #e3e7e9; border: 1px solid #59646a; padding: 5px; }
"""


def apply_theme(window):
    app = QApplication.instance()
    if not getattr(app, '_bvwa_fonts_loaded', False):
        for filename in ('msyh.ttc', 'segoeui.ttf'):
            font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / filename
            if font.is_file():
                font_id = QFontDatabase.addApplicationFont(str(font))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    app.setFont(QFont(families[0], 12))
        app._bvwa_fonts_loaded = True
    app.setStyle('Fusion')
    palette = QPalette()
    for role, value in ((QPalette.Window, '#17191c'), (QPalette.Base, '#25292e'),
                        (QPalette.Text, '#e3e7e9'), (QPalette.WindowText, '#e3e7e9'),
                        (QPalette.Button, '#2a3036'), (QPalette.ButtonText, '#e3e7e9'),
                        (QPalette.Highlight, '#386859'), (QPalette.HighlightedText, '#ffffff')):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    assets = Path(__file__).resolve().parent / 'assets'
    icons = ('QComboBox::down-arrow { image: url("' + (assets / 'chevron-down.svg').as_posix() + '"); width: 12px; height: 12px; }'
             'QCheckBox::indicator:checked { image: url("' + (assets / 'check.svg').as_posix() + '"); }')
    window.setStyleSheet(STYLE + icons)
    window.setWindowIcon(QIcon(str(assets / 'app.ico')))


class MotionButton(QPushButton):
    """A subtle animated highlight without moving controls or changing layout."""
    def __init__(self, text):
        super().__init__(text)
        self._highlight = 0.0
        self.motion = QPropertyAnimation(self, b'highlight', self)
        self.motion.setDuration(150)
        self.motion.setEasingCurve(QEasingCurve.OutCubic)
        self.setCursor(Qt.PointingHandCursor)

    @pyqtProperty(float)
    def highlight(self):
        return self._highlight

    @highlight.setter
    def highlight(self, value):
        self._highlight = value
        self.update()

    def animate(self, end):
        self.motion.stop()
        self.motion.setStartValue(self._highlight)
        self.motion.setEndValue(end)
        self.motion.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self.animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._highlight and self.isEnabled():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(175, 242, 233, round(120 * self._highlight)), 1.4))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 10, 10)


def label(text='', role=None):
    value = QLabel(text)
    value.setTextFormat(Qt.PlainText)
    value.setWordWrap(True)
    if role:
        value.setProperty('role', role)
    return value


def button(text, callback=None, role=None):
    value = MotionButton(text)
    if role:
        value.setProperty('role', role)
    if callback:
        value.clicked.connect(callback)
    return value


def card(title, subtitle='', number=''):
    frame = QFrame()
    frame.setObjectName('card')
    body = QVBoxLayout(frame)
    body.setContentsMargins(24, 22, 24, 22)
    body.setSpacing(15)
    row = QHBoxLayout()
    if number:
        badge = label(number, 'success')
        badge.setFixedWidth(28)
        row.addWidget(badge)
    row.addWidget(label(title, 'card-title'), 1)
    body.addLayout(row)
    if subtitle:
        body.addWidget(label(subtitle, 'muted'))
    return frame, body


def page(title, subtitle):
    container = QWidget()
    outer = QVBoxLayout(container)
    outer.setContentsMargins(30, 28, 30, 24)
    outer.setSpacing(20)
    outer.addWidget(label(title, 'heading'))
    outer.addWidget(label(subtitle, 'muted'))
    return container, outer


def scroll_content(outer):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    scroll.setWidget(content)
    outer.addWidget(scroll, 1)
    return content


def fold(title, content, parent_layout, expanded=False):
    toggle = QToolButton()
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setChecked(expanded)
    toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
    toggle.setStyleSheet('QToolButton { border: 0; background: transparent; text-align: left; padding: 5px 0; color: #a4adb5; }')
    content.setVisible(expanded)
    def change(checked):
        content.setVisible(checked)
        toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
    toggle.toggled.connect(change)
    parent_layout.addWidget(toggle)
    parent_layout.addWidget(content)
    return toggle
