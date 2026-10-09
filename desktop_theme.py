"""Original native-Qt dark workspace styling; no additional GUI dependency."""
from PyQt5.QtCore import Qt
import os
from pathlib import Path
from PyQt5.QtGui import QColor, QPalette, QFont, QFontDatabase
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                            QScrollArea, QToolButton, QVBoxLayout, QWidget)

STYLE = """
QWidget { background: transparent; color: #e3e7e9; font-family: 'Microsoft YaHei'; font-size: 13px; }
QWidget#workspace { background: #17191c; }
QWidget#sidebar { background: #121416; border-right: 1px solid #2c3034; }
QFrame#card { background: #1e2125; border: 1px solid #30353a; border-radius: 12px; }
QLabel { background: transparent; }
QLabel[role="heading"] { font-size: 23px; font-weight: 600; }
QLabel[role="card-title"] { font-size: 15px; font-weight: 600; }
QLabel[role="muted"] { color: #a4adb5; }
QLabel[role="success"] { color: #8fd6bd; }
QLabel[role="error"] { color: #ed9b91; }
QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox { background: #25292e; border: 1px solid #3b4249; border-radius: 7px; padding: 8px; selection-background-color: #386859; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #82cbb1; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled { color: #69737c; background: #202328; }
QComboBox::drop-down { border: 0; width: 25px; }
QComboBox QAbstractItemView { background: #25292e; color: #e3e7e9; selection-background-color: #386859; }
QPushButton, QToolButton { background: #2a3036; border: 1px solid #3b4249; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover, QToolButton:hover { background: #354049; border-color: #61716e; }
QPushButton[role="primary"] { background: #8fd6bd; border-color: #8fd6bd; color: #122b23; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #a5e4cc; }
QPushButton[role="danger"] { background: #3c2929; color: #ed9b91; border-color: #62413d; }
QPushButton[role="nav"] { background: transparent; color: #a4adb5; border: 0; text-align: left; padding: 12px 15px; }
QPushButton[role="nav"]:checked { background: #273c35; color: #a5e4cc; }
QPushButton[role="nav"]:hover { background: #252b2c; }
QPushButton:disabled, QToolButton:disabled { background: #25292e; color: #67727a; border-color: #30363b; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid #59646a; background: #25292e; }
QCheckBox::indicator:checked { background: #8fd6bd; border-color: #8fd6bd; }
QCheckBox::indicator:disabled { background: #30363b; border-color: #424a50; }
QProgressBar { background: #282e33; border: 0; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: #8fd6bd; border-radius: 4px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: #1c2024; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #454f58; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #25292e; color: #a4adb5; padding: 8px 20px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #a5e4cc; border-bottom-color: #8fd6bd; }
QTreeWidget { background: #1e2125; border: 1px solid #30353a; border-radius: 8px; outline: 0; }
QTreeWidget::item { padding: 10px; }
QTreeWidget::item:selected { background: #2d4d40; }
QHeaderView::section { background: #25292e; color: #a4adb5; padding: 9px; border: 0; }
QMenu { background: #25292e; border: 1px solid #454f58; padding: 5px; }
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
                    app.setFont(QFont(families[0], 10))
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


def label(text='', role=None):
    value = QLabel(text)
    value.setTextFormat(Qt.PlainText)
    value.setWordWrap(True)
    if role:
        value.setProperty('role', role)
    return value


def button(text, callback=None, role=None):
    value = QPushButton(text)
    if role:
        value.setProperty('role', role)
    if callback:
        value.clicked.connect(callback)
    return value


def card(title, subtitle='', number=''):
    frame = QFrame()
    frame.setObjectName('card')
    body = QVBoxLayout(frame)
    body.setContentsMargins(20, 18, 20, 18)
    body.setSpacing(12)
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
    outer.setContentsMargins(26, 22, 26, 20)
    outer.setSpacing(16)
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
