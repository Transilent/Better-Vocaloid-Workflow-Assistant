"""Moonlight surfaces and purple accents over a cached in-app background."""
import os
import re
from i18n import tr as _tr
from pathlib import Path
from string import Template
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor,QPalette,QFont,QFontDatabase,QIcon
from PyQt5.QtWidgets import QApplication,QFrame,QHBoxLayout,QLabel,QPushButton,QScrollArea,QToolButton,QVBoxLayout,QWidget


COLORS={
    'light':{
        'workspace':'#eef1ff','sidebar':'#e8ecfc','surface':'#fafbff','input':'#ffffff',
        'text':'#202546','muted':'#596485','border':'#d4d8ee','border_hover':'#a598e0',
        'accent':'#705af8','accent_hover':'#654ce8','accent_pressed':'#5b46d8','accent_text':'#ffffff',
        'selected':'#e9e3ff','selected_text':'#4635a6','button':'#f8f8ff','button_hover':'#efebff','button_pressed':'#e2dafc',
        'disabled':'#e9ebf4','disabled_text':'#747b93','track':'#dce0ef','scroll':'#aeb5d2',
        'danger_bg':'#fff1f3','danger':'#a33350','danger_border':'#e9bcc9','check':'#705af8','preview':'#e8ecf8'},
    'dark':{
        'workspace':'#171b2e','sidebar':'#20243a','surface':'#252a42','input':'#1d2237',
        'text':'#eff0ff','muted':'#b3bbd6','border':'#464d70','border_hover':'#9a8dcb',
        'accent':'#ae98ff','accent_hover':'#c1afff','accent_pressed':'#9780f0','accent_text':'#211844',
        'selected':'#443864','selected_text':'#e0d5ff','button':'#30354f','button_hover':'#3c4160','button_pressed':'#4d4472',
        'disabled':'#292d43','disabled_text':'#9097b3','track':'#383f5d','scroll':'#60698f',
        'danger_bg':'#452a3b','danger':'#ffb1c6','danger_border':'#82566e','check':'#705af8','preview':'#20263b'}
}

STYLE=Template("""
QWidget { background: transparent; color: $text; font-family: '$font'; font-size: 18px; }
QWidget#workspace, QDialog { background: $workspace; }
QWidget#sidebar { background: $sidebar; border-right: 1px solid $border; }
QFrame#card, QWidget#step-surface { background: $surface; border: 1px solid $border; border-radius: 22px; }
QWidget#footer { background: $surface; border: 1px solid $border; border-radius: 18px; }
QLabel { background: transparent; }
QLabel[role="heading"] { font-size: 32px; font-weight: 600; }
QLabel[role="card-title"] { font-size: 22px; font-weight: 600; }
QLabel[role="muted"] { color: $muted; font-size: 16px; }
QLabel[role="success"] { color: $selected_text; }
QLabel[role="error"] { color: $danger; }
QLabel#cover-preview { background: $preview; border: 1px solid $border; border-radius: 12px; }
QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox, QSpinBox { background: $input; border: 1px solid $border; border-radius: 15px; padding: 10px; selection-background-color: $selected; selection-color: $text; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { min-height: 28px; }
QLineEdit:hover, QTextEdit:hover, QComboBox:hover, QDoubleSpinBox:hover { border-color: $border_hover; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { color: $disabled_text; background: $disabled; }
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox QAbstractItemView { background: $input; color: $text; selection-background-color: $selected; selection-color: $selected_text; border: 1px solid $border; padding: 6px; }
QPushButton, QToolButton { background: $button; border: 1px solid $border; border-radius: 17px; padding: 11px 18px; min-height: 23px; }
QPushButton:focus, QToolButton:focus { border: 2px solid $accent; }
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
QCheckBox { spacing: 10px; background: transparent; min-height: 32px; padding: 3px; }
QCheckBox:disabled { color: $disabled_text; }
QCheckBox::indicator { width: 26px; height: 26px; border-radius: 13px; border: 1px solid $border_hover; background: $input; }
QCheckBox::indicator:checked { background: $check; border-color: $check; }
QCheckBox::indicator:disabled { background: $disabled; border-color: $border; }
QCheckBox:focus { color: $selected_text; }
QRadioButton[role="choice"], QRadioButton[role="choice-compact"] { background: $button; border: 1px solid $border; border-radius: 16px; padding: 12px; spacing: 10px; }
QRadioButton[role="choice-compact"] { padding: 8px; border-radius: 14px; }
QRadioButton:hover { border-color: $border_hover; background: $button_hover; }
QRadioButton:checked { background: $selected; color: $selected_text; border-color: $border_hover; }
QRadioButton:focus { border: 2px solid $accent; }
QRadioButton:disabled { background: $disabled; color: $disabled_text; border-color: $border; }
QRadioButton::indicator { width: 24px; height: 24px; border: 1px solid $border_hover; border-radius: 12px; background: $input; }
QRadioButton::indicator:checked { border: 7px solid $accent; width: 12px; height: 12px; border-radius: 13px; background: $input; }
QRadioButton::indicator:disabled { border-color: $border; background: $disabled; }
QProgressBar { background: $track; border: 0; border-radius: 4px; height: 12px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QSlider:horizontal { min-height: 36px; }
QSlider::groove:horizontal { background: $track; height: 6px; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal { background: $input; border: 2px solid $accent; width: 18px; margin: -8px 0; border-radius: 11px; }
QSlider::handle:horizontal:disabled { border-color: $disabled_text; background: $disabled; }
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
QMenu { background: $input; border: 1px solid $border; padding: 7px; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: $selected; color: $selected_text; }
QToolTip { background: $input; color: $text; border: 1px solid $border; padding: 6px; }
""")


def apply_theme(window, mode=None):
    if mode is None:
        from common import load_config
        mode=load_config().get('theme','light')
    mode=mode if mode in COLORS else 'light'
    colors=dict(COLORS[mode])
    from ui_preferences import load_appearance
    preferences=getattr(window,'_vwa_appearance',None) or load_appearance()
    window._vwa_appearance=preferences
    if preferences.get('material')=='glass':
        colors['surface']='rgba(250, 251, 255, 144)' if mode=='light' else 'rgba(37, 42, 66, 168)'
        colors['sidebar']='rgba(232, 236, 252, 120)' if mode=='light' else 'rgba(32, 36, 58, 120)'
    app=QApplication.instance()
    if not getattr(app,'_vwa_fonts_loaded',False):
        for filename in ('msyh.ttc','segoeui.ttf'):
            font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
            if font.is_file():
                families=QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
                if families:
                    app.setFont(QFont(families[0],12))
                    break
        app._vwa_fonts_loaded=True
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
    stylesheet=STYLE.substitute(colors, font=app.font().family().replace("'", ''))+icons
    factor=preferences.get('ui_scale',100)/100.
    if factor!=1:
        stylesheet=re.sub(r'(\d+)px',lambda match:str(round(int(match.group(1))*factor))+'px',stylesheet)
    window.setStyleSheet(stylesheet)
    from ui_components import ChoiceGroup
    for view in window.findChildren(ChoiceGroup):view.reflow()
    window.setWindowIcon(QIcon(str(assets/'app.ico')))
    from ui_materials import apply_material
    apply_material(window,mode,preferences)
    from ui_materials import navigation_icon
    for index in (0,9,8,3,1,2,4):
        controls=getattr(window,'nav_buttons',[])
        if index<len(controls) and controls[index] is not None:
            controls[index].setIcon(navigation_icon(index,COLORS[mode]['selected_text']))
            controls[index].setIconSize(QSize(22,22))
    chrome=getattr(window,'_workspace_chrome',None)
    if chrome is not None:chrome.refresh(force=True)
    tuning=getattr(window,'tuning',None)
    if tuning is not None:tuning.refresh_track_geometry()


class MotionButton(QPushButton):
    """Shared action button with stable layout and a standard hover state."""
    def __init__(self,text):
        super().__init__(_tr(text))
        self.setCursor(Qt.PointingHandCursor)

    def setText(self, text):
        super().setText(_tr(text))


class DisplayLabel(QLabel):
    """Translate dynamic worker/status text at the display boundary only."""
    def __init__(self, text=''):
        super().__init__(_tr(text))

    def setText(self, text):
        super().setText(_tr(text))


def label(text='', role=None):
    value = DisplayLabel(text)
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


class ScrollContent(QWidget):
    def scroll_to(self, widget):
        self._scroll.ensureWidgetVisible(widget, 0, 0)


def scroll_content(outer):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = ScrollContent()
    content._scroll = scroll
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
