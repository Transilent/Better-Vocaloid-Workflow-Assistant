"""Moonlight surfaces and purple accents over a cached in-app background."""
import os
import re
from i18n import tr as _tr
from pathlib import Path
from string import Template
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor,QPalette,QFont,QFontDatabase,QIcon
from PyQt5.QtWidgets import QApplication,QAbstractScrollArea,QFrame,QHBoxLayout,QLabel,QPushButton,QScrollArea,QStackedWidget,QTabWidget,QToolButton,QVBoxLayout,QWidget


COLORS={
    'light':{
        'workspace':'#eef1ff','sidebar':'#e8ecfc','surface':'#fafbff','input':'#ffffff',
        'text':'#202546','muted':'#596485','border':'#d4d8ee','border_hover':'#a598e0',
        'accent':'#705af8','accent_hover':'#654ce8','accent_pressed':'#5b46d8','accent_text':'#ffffff',
        'selected':'#e9e3ff','selected_text':'#4635a6','button':'#f8f8ff','button_hover':'#efebff','button_pressed':'#e2dafc',
        'disabled':'#e9ebf4','disabled_text':'#747b93','track':'#dce0ef','scroll':'#aeb5d2',
        'danger_bg':'#fff1f3','danger':'#a33350','danger_border':'#e9bcc9','check':'#705af8','preview':'#e8ecf8',
        'mixer_surface':'#f7f8ff','mixer_console':'#f3f5ff','mixer_well':'#ffffff',
        'mixer_border':'#c6cbe5','mixer_well_border':'#d4d8ee',
        'mixer_toggle':'#f4f5ff','mixer_toggle_border':'#c9cee5',
        'mixer_muted':'#fff1f3','mixer_muted_border':'#e9bcc9',
        'mixer_solo':'#e9e3ff','mixer_solo_border':'#705af8',
        'mixer_filtered':'#e9ebf4','mixer_filtered_border':'#c5cadd',
        'danger_on':'#ffffff','backing_accent':'#c47a16'},
    'dark':{
        'workspace':'#171b2e','sidebar':'#20243a','surface':'#252a42','input':'#1d2237',
        'text':'#eff0ff','muted':'#b3bbd6','border':'#464d70','border_hover':'#9a8dcb',
        'accent':'#ae98ff','accent_hover':'#c1afff','accent_pressed':'#9780f0','accent_text':'#211844',
        'selected':'#443864','selected_text':'#e0d5ff','button':'#30354f','button_hover':'#3c4160','button_pressed':'#4d4472',
        'disabled':'#292d43','disabled_text':'#9097b3','track':'#383f5d','scroll':'#60698f',
        'danger_bg':'#452a3b','danger':'#ffb1c6','danger_border':'#82566e','check':'#705af8','preview':'#20263b',
        'mixer_surface':'#272d48','mixer_console':'#21263d','mixer_well':'#1d2237',
        'mixer_border':'#505878','mixer_well_border':'#464d70',
        'mixer_toggle':'#30354f','mixer_toggle_border':'#545c80',
        'mixer_muted':'#452a3b','mixer_muted_border':'#b06d88',
        'mixer_solo':'#443864','mixer_solo_border':'#ae98ff',
        'mixer_filtered':'#292d43','mixer_filtered_border':'#555b76',
        'danger_on':'#2b1822','backing_accent':'#f0b75a'}
}


def _rgba(value, alpha):
    """Return a theme color with explicit glass opacity."""
    color = QColor(value)
    if not color.isValid():
        return value
    return 'rgba({0}, {1}, {2}, {3})'.format(
        color.red(), color.green(), color.blue(), max(0, min(255, int(alpha))))


def _qcolor(value):
    """Parse a theme token, including CSS rgba(), into an alpha-aware QColor."""
    color = QColor(value)
    if color.isValid():
        return color
    match = re.fullmatch(
        r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',
        str(value),
    )
    if match:
        return QColor(*(int(component) for component in match.groups()))
    return color


def theme_colors(mode='light', material='solid'):
    """Resolve semantic theme colors without changing widget geometry."""
    mode = mode if mode in COLORS else 'light'
    raw = COLORS[mode]
    colors = dict(raw)
    if material == 'glass':
        light = mode == 'light'
        # Keep the artwork visible while giving cards and controls a stable
        # reading plane. Values are derived from the active palette rather
        # than fixed glass colors, so both themes retain their own hue.
        colors['surface'] = _rgba(raw['surface'], 188 if light else 204)
        colors['sidebar'] = _rgba(raw['sidebar'], 120)
        colors['mixer_surface'] = _rgba(raw['surface'], 226 if light else 234)
        colors['mixer_console'] = _rgba(raw['input'], 190 if light else 208)
        colors['mixer_well'] = _rgba(raw['input'], 224 if light else 230)
        colors['mixer_toggle'] = _rgba(raw['button'], 210 if light else 216)
        colors['mixer_muted'] = _rgba(raw['danger_bg'], 212 if light else 224)
        colors['mixer_solo'] = _rgba(raw['selected'], 214 if light else 226)
        colors['mixer_filtered'] = _rgba(raw['disabled'], 194 if light else 214)
    return colors


def _opaque_blend(first, second, first_weight):
    """Blend two palette colors without introducing a fixed black/white."""
    left, right = QColor(first), QColor(second)
    if not left.isValid():
        return second
    if not right.isValid():
        return first
    weight = max(0.0, min(1.0, float(first_weight)))
    values = []
    for left_value, right_value in ((left.red(), right.red()),
                                    (left.green(), right.green()),
                                    (left.blue(), right.blue())):
        values.append(int(round(left_value * weight + right_value * (1.0 - weight))))
    return '#{0:02x}{1:02x}{2:02x}'.format(*values)


def mixdown_analysis_tokens(mode='light', material='solid', ui_scale=100):
    """Semantic colors and density tokens for the scoped mixdown console."""
    mode = mode if mode in COLORS else 'light'
    material = 'glass' if material == 'glass' else 'solid'
    colors = theme_colors(mode, material)
    raw = COLORS[mode]
    glass = material == 'glass'
    try:
        factor = float(ui_scale) / 100.0
    except (TypeError, ValueError):
        factor = 1.0
    factor = max(0.8, min(2.0, factor))

    def scaled(value):
        return max(1, int(round(value * factor)))

    border_width = 1 if factor < 1.2 else 2
    warning = (_opaque_blend(raw['backing_accent'], raw['text'], .55)
               if mode == 'light'
               else _opaque_blend(raw['backing_accent'], raw['text'], .85))
    return {
        'card_fill': colors['surface'],
        'panel_fill': colors['mixer_console'],
        'preset_fill': _rgba(raw['surface'], 226) if glass else raw['surface'],
        'diagnostic_fill': _rgba(raw['preview'], 228) if glass else raw['preview'],
        'chart_surface': raw['input'],
        'chart_alpha': 238 if glass else 255,
        'chart_fill': _rgba(raw['input'], 238) if glass else raw['input'],
        'border': colors['mixer_border'],
        'border_hover': colors['border_hover'],
        'text': colors['text'],
        'muted': colors['muted'],
        'disabled': colors['disabled'],
        'disabled_text': colors['disabled_text'],
        'accent': colors['accent'],
        'accent_hover': colors['accent_hover'],
        'accent_text': colors['accent_text'],
        'selected_text': colors['selected_text'],
        'success': colors['selected_text'],
        'warning': warning,
        'error': colors['danger'],
        'tab_base': colors['mixer_toggle'],
        'tab_hover': colors['button_hover'],
        'tab_selected': colors['mixer_solo'],
        'tab_disabled': colors['mixer_filtered'],
        'track_fill': colors['mixer_surface'],
        'track_muted': colors['mixer_muted'],
        'track_muted_border': colors['mixer_muted_border'],
        'track_solo': colors['mixer_solo'],
        'track_solo_border': colors['mixer_solo_border'],
        'track_filtered': colors['mixer_filtered'],
        'track_filtered_border': colors['mixer_filtered_border'],
        'backing_accent': raw['backing_accent'],
        'border_width': border_width,
        'accent_border_width': border_width + 1,
        'radius_card': scaled(18),
        'radius_panel': scaled(11),
        'radius_tab': scaled(7),
        'radius_track': scaled(14),
        'tab_pad_y': scaled(4),
        'tab_pad_x': scaled(10),
        'tab_gap': scaled(4),
        'tab_min_height': scaled(19),
        'scroller_width': scaled(42),
        'scroller_button': scaled(18),
        'status_pad_y': scaled(5),
        'status_pad_x': scaled(9),
        'status_min_height': scaled(22),
    }


def mixdown_analysis_qss(mode='light', material='solid', ui_scale=100):
    """Return only mixdown-scoped QSS with stable object-name selectors."""
    tokens = mixdown_analysis_tokens(mode, material, ui_scale)
    return Template("""
QWidget#mixdownDialog QFrame#card {
    background: $card_fill;
    border: ${border_width}px solid $border;
    border-radius: ${radius_card}px;
}
QWidget#mixdownDialog QWidget#mixdownPresetBar {
    background: $preset_fill;
    border: ${border_width}px solid $border;
    border-radius: ${radius_panel}px;
    padding: ${status_pad_y}px ${status_pad_x}px;
}
QWidget#mixdownDialog QWidget#mixdownPresetBar:disabled {
    background: $disabled;
    border-color: $border;
}
QWidget#mixdownDialog QPushButton#mixdownPresetApply {
    background: $accent;
    color: $accent_text;
    border-color: $accent;
    border-radius: ${radius_tab}px;
}
QWidget#mixdownDialog QPushButton#mixdownPresetApply:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QWidget#mixdownDialog QPushButton#mixdownPresetApply:focus {
    border: ${accent_border_width}px solid $selected_text;
}
QWidget#mixdownDialog QPushButton#mixdownPresetApply:disabled {
    background: $disabled;
    color: $disabled_text;
    border-color: $border;
}
QWidget#mixdownDialog QLabel#mixdownLoudnessSummary,
QWidget#mixdownDialog QLabel#mixdownPreviewStatus {
    background: $diagnostic_fill;
    color: $text;
    border: ${border_width}px solid $border;
    border-left: ${accent_border_width}px solid $accent;
    border-radius: ${radius_panel}px;
    padding: ${status_pad_y}px ${status_pad_x}px;
    min-height: ${status_min_height}px;
}
QWidget#mixdownDialog QLabel#mixdownLoudnessSummary[role="success"],
QWidget#mixdownDialog QLabel#mixdownPreviewStatus[role="success"] {
    color: $success;
    border-left-color: $success;
}
QWidget#mixdownDialog QLabel#mixdownLoudnessSummary[role="warning"],
QWidget#mixdownDialog QLabel#mixdownPreviewStatus[role="warning"] {
    color: $warning;
    border-left-color: $warning;
}
QWidget#mixdownDialog QLabel#mixdownLoudnessSummary[role="error"],
QWidget#mixdownDialog QLabel#mixdownPreviewStatus[role="error"] {
    color: $error;
    border-left-color: $error;
}
QWidget#mixdownDialog QLabel[role="success"] { color: $success; }
QWidget#mixdownDialog QLabel[role="warning"] { color: $warning; }
QWidget#mixdownDialog QLabel[role="error"] { color: $error; }
QWidget#mixdownDialog QLabel:disabled { color: $disabled_text; }
QFrame#mixer-channel {
    background: $track_fill;
    border: ${border_width}px solid $border;
    border-left: ${accent_border_width}px solid $accent;
    border-radius: ${radius_track}px;
}
QFrame#mixer-channel[channelKind="backing"] {
    border-left-color: $backing_accent;
}
QFrame#mixer-channel:hover {
    border-color: $border_hover;
}
QFrame#mixer-channel:focus {
    border-color: $accent;
}
QFrame#mixer-channel[channelMuted="true"] {
    background: $track_muted;
    border-color: $track_muted_border;
    border-left-color: $error;
}
QFrame#mixer-channel[channelSolo="true"] {
    background: $track_solo;
    border-color: $track_solo_border;
    border-left-color: $accent;
}
QFrame#mixer-channel[channelFiltered="true"] {
    background: $track_filtered;
    border-color: $track_filtered_border;
    border-left-color: $muted;
}
QFrame#mixer-channel:disabled {
    background: $track_filtered;
    border-color: $track_filtered_border;
}
QTabWidget#mixdownAnalysisTabs {
    background: transparent;
    border: 0;
    color: $text;
}
QTabWidget#mixdownAnalysisTabs::pane {
    background: transparent;
    border: ${border_width}px solid $border;
    border-radius: ${radius_panel}px;
}
QTabWidget#mixdownAnalysisTabs QTabBar {
    background: transparent;
    border: 0;
    color: $muted;
    spacing: ${tab_gap}px;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab {
    background: $tab_base;
    color: $muted;
    border: ${border_width}px solid $border;
    border-bottom: 0;
    border-top-left-radius: ${radius_tab}px;
    border-top-right-radius: ${radius_tab}px;
    min-height: ${tab_min_height}px;
    padding: ${tab_pad_y}px ${tab_pad_x}px;
    margin-right: ${tab_gap}px;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab:hover {
    background: $tab_hover;
    color: $text;
    border-color: $border_hover;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab:selected,
QTabWidget#mixdownAnalysisTabs QTabBar::tab:selected:hover {
    background: $tab_selected;
    color: $selected_text;
    border-color: $accent;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab:focus,
QTabWidget#mixdownAnalysisTabs QTabBar::tab:selected:focus {
    color: $selected_text;
    border-color: $accent;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab:disabled {
    background: $tab_disabled;
    color: $disabled_text;
    border-color: $border;
}
QTabWidget#mixdownAnalysisTabs QTabBar::tab:selected:disabled {
    background: $disabled;
    color: $disabled_text;
    border-color: $disabled_text;
}
QTabWidget#mixdownAnalysisTabs QStackedWidget,
QTabWidget#mixdownAnalysisTabs QStackedWidget QWidget,
QTabWidget#mixdownAnalysisTabs QStackedWidget > QWidget,
QTabWidget#mixdownAnalysisTabs QAbstractScrollArea,
QTabWidget#mixdownAnalysisTabs QAbstractScrollArea::viewport,
QTabWidget#mixdownAnalysisTabs QAbstractItemView,
QTabWidget#mixdownAnalysisTabs QAbstractItemView::viewport,
QTabWidget#mixdownAnalysisTabs QScrollArea,
QTabWidget#mixdownAnalysisTabs QScrollArea > QWidget,
QTabWidget#mixdownAnalysisTabs QScrollArea > QWidget > QWidget {
    background: transparent;
    color: $text;
    border: 0;
}
QTabWidget#mixdownAnalysisTabs QTabBar::scroller {
    background: transparent;
    border: 0;
    width: ${scroller_width}px;
}
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton {
    background: $tab_base;
    color: $text;
    border: ${border_width}px solid $border;
    border-radius: ${radius_tab}px;
    margin: 1px;
    padding: 0;
    min-width: ${scroller_button}px;
    min-height: ${scroller_button}px;
}
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton:hover {
    background: $tab_hover;
    border-color: $border_hover;
}
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton:focus {
    border-color: $accent;
}
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton:disabled {
    background: $tab_disabled;
    color: $disabled_text;
    border-color: $border;
}
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton::left-arrow,
QTabWidget#mixdownAnalysisTabs QTabBar QToolButton::right-arrow {
    width: ${scroller_button}px;
    height: ${scroller_button}px;
    background: transparent;
    border: 0;
}
QWidget#mixdownEqCurve,
QWidget#mixdownGrHistory,
QWidget#mixdownReverbDecay,
QWidget#mixdownMeterBank {
    background: $chart_fill;
    color: $text;
    border: ${border_width}px solid $border;
    border-radius: ${radius_panel}px;
}
QWidget#mixdownEqCurve:focus,
QWidget#mixdownGrHistory:focus,
QWidget#mixdownReverbDecay:focus,
QWidget#mixdownMeterBank:focus {
    border-color: $accent;
}
QWidget#mixdownEqCurve:disabled,
QWidget#mixdownGrHistory:disabled,
QWidget#mixdownReverbDecay:disabled,
QWidget#mixdownMeterBank:disabled {
    background: $disabled;
    color: $disabled_text;
    border-color: $border;
}
QWidget#mixdownEqBandStripControls,
QWidget#mixdownProcessingPresetControls,
QWidget#mixdownPerTrackSendControls {
    background: $panel_fill;
    border: ${border_width}px solid $border;
    border-radius: ${radius_panel}px;
}
""").substitute(tokens) + "\n"


def apply_mixdown_analysis_palette(window, mode='light', material='solid', ui_scale=100):
    """Apply chart roles to the mixdown widgets without changing other pages."""
    tokens = mixdown_analysis_tokens(mode, material, ui_scale)
    names = ('mixdownAnalysisTabs', 'mixdownEqCurve', 'mixdownGrHistory',
             'mixdownReverbDecay', 'mixdownMeterBank')
    targets = []
    for name in names:
        if window.objectName() == name:
            targets.append(window)
        targets.extend(window.findChildren(QWidget, name))
    tabs_targets = [target for target in targets if isinstance(target, QTabWidget)]
    for tabs in tabs_targets:
        stack = tabs.findChild(QStackedWidget)
        if stack is not None:
            targets.append(stack)
        for index in range(tabs.count()):
            page = tabs.widget(index)
            if page is not None:
                targets.append(page)
                for scroll in page.findChildren(QAbstractScrollArea):
                    targets.append(scroll.viewport())
    chart = QColor(tokens['chart_surface'])
    chart.setAlpha(tokens['chart_alpha'])
    panel = QColor(tokens['chart_surface'])
    text = QColor(tokens['text'])
    disabled = QColor(tokens['disabled_text'])
    seen = set()
    for target in targets:
        key = id(target)
        if key in seen:
            continue
        seen.add(key)
        palette = QPalette(target.palette())
        palette.setColor(QPalette.Base, chart)
        palette.setColor(QPalette.Window, panel)
        palette.setColor(QPalette.Text, text)
        palette.setColor(QPalette.WindowText, text)
        palette.setColor(QPalette.Button, QColor(tokens['border']))
        palette.setColor(QPalette.ButtonText, text)
        palette.setColor(QPalette.Highlight, QColor(tokens['accent']))
        palette.setColor(QPalette.HighlightedText, QColor(tokens['accent_text']))
        palette.setColor(QPalette.Mid, QColor(tokens['border']))
        palette.setColor(QPalette.Midlight, QColor(tokens['border_hover']))
        palette.setColor(QPalette.Dark, disabled)
        palette.setColor(QPalette.Light, chart)
        palette.setColor(QPalette.Shadow, QColor(tokens['border']))
        palette.setColor(QPalette.Disabled, QPalette.Text, disabled)
        palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled)
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
        target.setPalette(palette)
        target.update()


STYLE=Template("""
QWidget { background: transparent; color: $text; font-family: '$font'; font-size: 18px; }
QWidget#workspace, QDialog { background: $workspace; }
QWidget#sidebar { background: $sidebar; border-right: 1px solid $border; }
QFrame#card, QWidget#step-surface { background: $surface; border: 1px solid $border; border-radius: 22px; }
QWidget#mixer-workspace, QWidget#mixer-right-stack, QWidget#mixer-rack { background: transparent; }
QWidget#mixer-console { background: $mixer_console; border: 1px solid $mixer_border; border-radius: 18px; }
QFrame#mixer-channel { background: $mixer_surface; border: 1px solid $mixer_border; border-left: 3px solid $accent; border-radius: 16px; }
QFrame#mixer-channel[channelKind="backing"] { border-left-color: $backing_accent; }
QLabel#mixer-role { color: $muted; background: transparent; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#mixer-filename { color: $text; background: $mixer_well; border: 1px solid $mixer_well_border; border-radius: 10px; padding: 5px 7px; min-height: 24px; font-size: 14px; font-weight: 600; }
QPushButton[mixerAction="choose"] { padding: 6px 8px; min-height: 18px; border-radius: 12px; font-size: 14px; font-weight: 600; }
QPushButton[mixerToggle] { background: $mixer_toggle; color: $text; border: 1px solid $mixer_toggle_border; padding: 0; min-height: 20px; border-radius: 8px; font-size: 11px; font-weight: 700; }
QPushButton[mixerToggle]:hover { background: $button_hover; border-color: $border_hover; }
QPushButton[mixerToggle]:focus { border: 2px solid $accent; }
QPushButton[mixerToggle="mute"]:checked { background: $danger; color: $danger_on; border-color: $danger; }
QPushButton[mixerToggle="solo"]:checked { background: $accent; color: $accent_text; border-color: $accent; }
QPushButton[mixerToggle="mute"]:checked:hover { background: $danger; border-color: $danger_border; }
QPushButton[mixerToggle="solo"]:checked:hover { background: $accent_hover; border-color: $accent_hover; }
QLabel#mixer-pan-value { color: $text; background: $mixer_toggle; border: 1px solid $mixer_toggle_border; border-radius: 7px; padding: 0 1px; font-size: 11px; font-weight: 700; }
QFrame#mixer-channel[channelMuted="true"] { background: $mixer_muted; border-color: $mixer_muted_border; border-left-color: $danger; }
QFrame#mixer-channel[channelSolo="true"] { background: $mixer_solo; border-color: $mixer_solo_border; border-left-color: $accent; }
QFrame#mixer-channel[channelFiltered="true"] { background: $mixer_filtered; border-color: $mixer_filtered_border; border-left-color: $muted; }
QFrame#mixer-channel[channelMuted="true"][channelSolo="true"] { background: $mixer_muted; border-color: $mixer_muted_border; border-left-color: $danger; }
QPushButton[mixerAction="remove"] { padding: 3px 7px; min-height: 16px; border-radius: 10px; font-size: 12px; color: $muted; }
QWidget#mixer-player { background: $mixer_well; border: 1px solid $mixer_well_border; border-radius: 16px; }
QWidget#mixer-bus { background: $mixer_well; border: 1px solid $mixer_well_border; border-radius: 12px; }
QLabel#mixer-time { color: $muted; font-size: 13px; font-weight: 600; }
QPushButton#mixdownPlayerToggle, QPushButton#mixdownPlayerLoop { border-radius: 19px; padding: 0; min-height: 36px; font-size: 12px; font-weight: 700; }
QPushButton#mixdownPlayerLoop:checked { background: $selected; color: $selected_text; border-color: $border_hover; }
QWidget#footer { background: $surface; border: 1px solid $border; border-radius: 18px; }
QLabel { background: transparent; }
QLabel[role="heading"] { font-size: 32px; font-weight: 600; }
QLabel[role="card-title"] { font-size: 22px; font-weight: 650; }
QLabel[role="muted"] { color: $muted; font-size: 16px; }
QLabel[role="success"] { color: $selected_text; font-weight: 600; }
QLabel[role="error"] { color: $danger; font-weight: 600; }
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
QWidget#style-timeline QToolButton { padding: 6px 10px; min-height: 22px; border-radius: 11px; font-size: 16px; }
QWidget#style-timeline QToolButton:checked { background: $selected; color: $selected_text; border-color: $accent; }
QWidget#style-timeline QComboBox { padding: 5px 8px; min-height: 22px; border-radius: 10px; font-size: 16px; }
QWidget#style-timeline QScrollBar:horizontal { height: 24px; background: $track; margin: 0; border-radius: 9px; }
QWidget#style-timeline QScrollBar::handle:horizontal { background: $accent; min-width: 96px; border-radius: 9px; }
QWidget#timeline-navigation { background: $surface; border: 1px solid $border; border-radius: 12px; }
QWidget#style-timeline QScrollBar::add-line:horizontal, QWidget#style-timeline QScrollBar::sub-line:horizontal { width: 0; }
QPushButton#style-editor-entry { font-size: 22px; font-weight: 600; border-radius: 18px; }
""")


def apply_theme(window, mode=None):
    if mode is None:
        from common import load_config
        mode=load_config().get('theme','light')
    mode=mode if mode in COLORS else 'light'
    from ui_preferences import load_appearance
    preferences=getattr(window,'_vwa_appearance',None) or load_appearance()
    window._vwa_appearance=preferences
    material='glass' if preferences.get('material')=='glass' else 'solid'
    colors=theme_colors(mode,material)
    app=QApplication.instance()
    app.setProperty('vwa-theme',mode)
    app.setProperty('vwa-material',material)
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
                     (QPalette.Highlight,'accent'),(QPalette.HighlightedText,'accent_text'),
                     (QPalette.Link,'accent'),(QPalette.ToolTipBase,'input'),
                     (QPalette.ToolTipText,'text')):
        palette.setColor(role,_qcolor(colors[key]))
    for role,key in ((QPalette.Mid,'border'),(QPalette.Midlight,'border_hover'),
                     (QPalette.Dark,'disabled_text'),(QPalette.Light,'surface'),
                     (QPalette.Shadow,'border')):
        palette.setColor(role,_qcolor(colors[key]))
    if hasattr(QPalette,'PlaceholderText'):
        palette.setColor(QPalette.PlaceholderText,QColor(colors['muted']))
    palette.setColor(QPalette.Disabled,QPalette.Text,_qcolor(colors['disabled_text']))
    palette.setColor(QPalette.Disabled,QPalette.WindowText,_qcolor(colors['disabled_text']))
    palette.setColor(QPalette.Disabled,QPalette.ButtonText,_qcolor(colors['disabled_text']))
    app.setPalette(palette)
    assets=Path(__file__).resolve().parent/'assets'
    icons=('QComboBox::down-arrow { image: url("'+(assets/'chevron-down.svg').as_posix()+'"); width: 12px; height: 12px; }'
           'QCheckBox::indicator:checked { image: url("'+(assets/'check.svg').as_posix()+'"); }')
    stylesheet=STYLE.substitute(colors, font=app.font().family().replace("'", ''))+icons
    from ui_components import STYLE_PRESET_COLORS
    for preset,value in STYLE_PRESET_COLORS.items():
        tint=QColor(value);r,g,b,_=tint.getRgb()
        selector='QRadioButton[stylePreset="'+preset+'"]'
        stylesheet+=f'''
{selector} {{ border-color: {value}; background: rgba({r},{g},{b},22); }}
{selector}:hover {{ border-color: {value}; background: rgba({r},{g},{b},44); }}
{selector}:checked {{ border-color: {value}; background: {value}; color: #ffffff; }}
{selector}::indicator {{ border-color: {value}; }}
{selector}::indicator:checked {{ border-color: #ffffff; background: {value}; }}
{selector}:disabled {{ background: {colors['disabled']}; color: {colors['disabled_text']}; border-color: {colors['border']}; }}
'''
    factor=int(preferences.get('ui_scale',100))/100.
    if factor!=1:
        stylesheet=re.sub(r'(\d+)px',lambda match:str(round(int(match.group(1))*factor))+'px',stylesheet)
    stylesheet+=mixdown_analysis_qss(mode,material,factor*100)
    window.setStyleSheet(stylesheet)
    from ui_components import ChoiceGroup,StyleTimeline
    for view in window.findChildren(ChoiceGroup):view.reflow()
    for view in window.findChildren(StyleTimeline):
        view._canvas.update();view._sync_headers()
    window.setWindowIcon(QIcon(str(assets/'app.ico')))
    from ui_materials import apply_material
    apply_material(window,mode,preferences)
    apply_mixdown_analysis_palette(window,mode,material,factor*100)
    from ui_materials import navigation_icon
    for index in (0,9,10,8,3,1,2,4):
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
