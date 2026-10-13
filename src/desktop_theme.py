"""Opaque light/dark Qt themes with a restrained green accent."""
import os
from pathlib import Path
from string import Template
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor,QPalette,QFont,QFontDatabase,QIcon
from PyQt5.QtWidgets import QApplication,QFrame,QHBoxLayout,QLabel,QPushButton,QScrollArea,QToolButton,QVBoxLayout,QWidget


COLORS={
    'light':{
        'workspace':'#f3f6f3','sidebar':'#eaf0eb','surface':'#ffffff','input':'#fafcfb',
        'text':'#263b32','muted':'#617268','border':'#d6e0d9','border_hover':'#a8bdb0',
        'accent':'#2c8066','accent_hover':'#256e58','accent_pressed':'#1d5b49','accent_text':'#ffffff',
        'selected':'#deeee4','selected_text':'#255c46','button':'#f8faf8','button_hover':'#edf4ef','button_pressed':'#dceae1',
        'disabled':'#edf1ee','disabled_text':'#909b94','track':'#e2e9e4','scroll':'#b8c7bd',
        'danger_bg':'#fff4f2','danger':'#ad483e','danger_border':'#e6c5c0','check':'#91cfae','preview':'#eaf0eb'},
    'dark':{
        'workspace':'#18201d','sidebar':'#1c2822','surface':'#24322a','input':'#1b2821',
        'text':'#edf3ef','muted':'#a8bbae','border':'#405247','border_hover':'#6e8e7a',
        'accent':'#92cfb0','accent_hover':'#a9dcbf','accent_pressed':'#78b995','accent_text':'#173625',
        'selected':'#2f4d3d','selected_text':'#c7ead4','button':'#2a3b31','button_hover':'#344d3d','button_pressed':'#3d5948',
        'disabled':'#27312b','disabled_text':'#75867b','track':'#34463b','scroll':'#577161',
        'danger_bg':'#422e2c','danger':'#f0a79c','danger_border':'#725048','check':'#91cfae','preview':'#1f2c24'}
}

STYLE=Template("""
QWidget { background: transparent; color: $text; font-family: 'Microsoft YaHei'; font-size: 18px; }
QWidget#workspace, QDialog { background: $workspace; }
QWidget#sidebar { background: $sidebar; border-right: 1px solid $border; }
QFrame#card, QWidget#step-surface { background: $surface; border: 1px solid $border; border-radius: 16px; }
QLabel { background: transparent; }
QLabel[role="heading"] { font-size: 32px; font-weight: 600; }
QLabel[role="card-title"] { font-size: 22px; font-weight: 600; }
QLabel[role="muted"] { color: $muted; font-size: 16px; }
QLabel[role="success"] { color: $selected_text; }
QLabel[role="error"] { color: $danger; }
QLabel#cover-preview { background: $preview; border: 1px solid $border; border-radius: 12px; }
QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox { background: $input; border: 1px solid $border; border-radius: 10px; padding: 13px; selection-background-color: $selected; selection-color: $text; }
QLineEdit, QComboBox, QDoubleSpinBox { min-height: 25px; }
QLineEdit:hover, QTextEdit:hover, QComboBox:hover, QDoubleSpinBox:hover { border-color: $border_hover; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { color: $disabled_text; background: $disabled; }
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox QAbstractItemView { background: $surface; color: $text; selection-background-color: $selected; selection-color: $selected_text; border: 1px solid $border; padding: 6px; }
QPushButton, QToolButton { background: $button; border: 1px solid $border; border-radius: 10px; padding: 13px 20px; min-height: 23px; }
QPushButton:hover, QToolButton:hover { background: $button_hover; border-color: $border_hover; }
QPushButton:pressed, QToolButton:pressed { background: $button_pressed; }
QPushButton[role="primary"] { background: $accent; color: $accent_text; border-color: $accent; font-weight: 600; }
QPushButton[role="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[role="primary"]:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton[role="danger"] { background: $danger_bg; color: $danger; border-color: $danger_border; }
QPushButton[role="nav"] { background: transparent; color: $muted; border: 0; text-align: left; padding: 12px 15px; }
QPushButton[role="nav"]:checked { background: $selected; color: $selected_text; border: 1px solid $border; }
QPushButton[role="nav"]:hover { background: $button_hover; }
QPushButton[role="step-nav"] { padding: 11px 14px; }
QPushButton[role="step-nav"]:checked { background: $selected; color: $selected_text; border-color: $border_hover; font-weight: 600; }
QPushButton:disabled, QToolButton:disabled, QPushButton[role="primary"]:disabled { background: $disabled; color: $disabled_text; border-color: $border; }
QCheckBox { spacing: 10px; background: transparent; }
QCheckBox:disabled { color: $disabled_text; }
QCheckBox::indicator { width: 23px; height: 23px; border-radius: 5px; border: 1px solid $border_hover; background: $input; }
QCheckBox::indicator:checked { background: $check; border-color: $check; }
QCheckBox::indicator:disabled { background: $disabled; border-color: $border; }
QProgressBar { background: $track; border: 0; border-radius: 4px; height: 10px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: $track; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: $button; color: $muted; padding: 12px 20px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $selected_text; border-bottom-color: $accent; }
QTreeWidget, QTableWidget { background: $surface; border: 1px solid $border; border-radius: 10px; outline: 0; gridline-color: $border; }
QTableWidget::item { padding: 9px; }
QTreeWidget::item { padding: 10px; }
QTreeWidget::item:selected { background: $selected; color: $selected_text; }
QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { background: $surface; border: 1px solid $border; border-radius: 10px; padding: 14px 9px; margin-bottom: 9px; }
QListWidget::item:selected { background: $selected; border-color: $accent; }
QDoubleSpinBox { min-width: 90px; }
QHeaderView::section { background: $button; color: $muted; padding: 9px; border: 0; }
QMenu { background: $surface; border: 1px solid $border; padding: 7px; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: $selected; color: $selected_text; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 6px; }
""")


def apply_theme(window, mode=None):
    if mode is None:
        from common import load_config
        mode=load_config().get('theme','light')
    mode=mode if mode in COLORS else 'light'
    colors=COLORS[mode]
    app=QApplication.instance()
    if not getattr(app,'_bvwa_fonts_loaded',False):
        for filename in ('msyh.ttc','segoeui.ttf'):
            font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
            if font.is_file():
                families=QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
                if families: app.setFont(QFont(families[0],12))
        app._bvwa_fonts_loaded=True
    app.setStyle('Fusion')
    palette=QPalette()
    for role,key in ((QPalette.Window,'workspace'),(QPalette.Base,'input'),(QPalette.Text,'text'),
                     (QPalette.WindowText,'text'),(QPalette.Button,'button'),(QPalette.ButtonText,'text'),
                     (QPalette.Highlight,'selected'),(QPalette.HighlightedText,'selected_text')):
        palette.setColor(role,QColor(colors[key]))
    app.setPalette(palette)
    assets=Path(__file__).resolve().parent/'assets'
    icons=('QComboBox::down-arrow { image: url("'+(assets/'chevron-down.svg').as_posix()+'"); width: 12px; height: 12px; }'
           'QCheckBox::indicator:checked { image: url("'+(assets/'check.svg').as_posix()+'"); }')
    window.setStyleSheet(STYLE.substitute(colors)+icons)
    window.setWindowIcon(QIcon(str(assets/'app.ico')))


class MotionButton(QPushButton):
    """Shared action button with stable layout and a standard hover state."""
    def __init__(self,text):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)


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
    toggle.setStyleSheet('QToolButton { border: 0; background: transparent; text-align: left; padding: 5px 0; }')
    content.setVisible(expanded)
    def change(checked):
        content.setVisible(checked)
        toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
    toggle.toggled.connect(change)
    parent_layout.addWidget(toggle)
    parent_layout.addWidget(content)
    return toggle
