"""Offscreen theme and three-column mixdown workspace smoke test."""
import math
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path.insert(0, str(SRC))

import common
common.ROOT = APP

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QImage, QPalette
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from audio_mixdown_ui import _LevelMeter, _MixerWorkspace, _PanDial
from desktop_theme import (
    COLORS, apply_theme, card, label, mixdown_analysis_qss,
    mixdown_analysis_tokens, theme_colors,
)
from mixdown_control_panel import PresetBar
from mixdown_eq_curve import EQCurveView
from mixdown_live_meters import GrHistoryView, ReverbDecayView


SCREEN_SIZES = ((1600, 1000), (1920, 1080), (2512, 1352))
MIXDOWN_THEMES = (
    ('light', 'glass'), ('light', 'solid'),
    ('dark', 'glass'), ('dark', 'solid'),
)
REQUIRED_ANALYSIS_SELECTORS = (
    'QTabWidget#mixdownAnalysisTabs::pane',
    'QTabWidget#mixdownAnalysisTabs QTabBar',
    'QTabWidget#mixdownAnalysisTabs QTabBar::tab:hover',
    'QTabWidget#mixdownAnalysisTabs QTabBar::tab:selected',
    'QTabWidget#mixdownAnalysisTabs QTabBar::tab:focus',
    'QTabWidget#mixdownAnalysisTabs QTabBar::tab:disabled',
    'QTabWidget#mixdownAnalysisTabs QStackedWidget',
    'QTabWidget#mixdownAnalysisTabs QStackedWidget > QWidget',
    'QTabWidget#mixdownAnalysisTabs QAbstractScrollArea::viewport',
    'QTabWidget#mixdownAnalysisTabs QTabBar::scroller',
    'QTabWidget#mixdownAnalysisTabs QTabBar QToolButton',
    'QWidget#mixdownEqCurve',
    'QWidget#mixdownGrHistory',
    'QWidget#mixdownReverbDecay',
    'QWidget#mixdownPresetBar',
    'QLabel#mixdownLoudnessSummary',
    'QLabel#mixdownPreviewStatus',
    'QFrame#mixer-channel',
)


def settle(app):
    for _ in range(5):
        app.processEvents()


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        channels = []
        for component in (color.red(), color.green(), color.blue()):
            channel = component / 255.0
            channels.append(channel / 12.92 if channel <= 0.04045
                            else ((channel + 0.055) / 1.055) ** 2.4)
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + .05) / (low + .05)


def channel_strip(kind, state):
    strip = QFrame()
    strip.setObjectName('mixer-channel')
    strip.setProperty('channelKind', kind)
    strip.setProperty('channelMuted', state == 'muted')
    strip.setProperty('channelSolo', state == 'solo')
    strip.setProperty('channelFiltered', state == 'filtered')
    strip.setMinimumWidth(112)
    strip.setMaximumWidth(210)
    body = QVBoxLayout(strip)
    body.setContentsMargins(9, 8, 9, 10)
    body.setSpacing(5)

    role = label('LEAD' if kind == 'vocal' else 'BACKING', 'muted')
    role.setObjectName('mixer-role')
    body.addWidget(role)
    controls = QHBoxLayout()
    controls.setSpacing(3)
    mute = QPushButton('M')
    mute.setProperty('mixerToggle', 'mute')
    mute.setCheckable(True)
    mute.setChecked(state == 'muted')
    mute.setFixedSize(26, 24)
    solo = QPushButton('S')
    solo.setProperty('mixerToggle', 'solo')
    solo.setCheckable(True)
    solo.setChecked(state == 'solo')
    solo.setFixedSize(26, 24)
    pan = _PanDial()
    pan.setValue(-22 if kind == 'vocal' else 12)
    pan_value = QLabel('L22' if kind == 'vocal' else 'R12')
    pan_value.setObjectName('mixer-pan-value')
    pan_value.setAlignment(Qt.AlignCenter)
    controls.addWidget(mute)
    controls.addWidget(solo)
    controls.addStretch(1)
    controls.addWidget(pan)
    controls.addWidget(pan_value)
    body.addLayout(controls)

    filename = QLabel('vocal-main.wav' if kind == 'vocal' else 'backing.flac')
    filename.setObjectName('mixer-filename')
    filename.setAlignment(Qt.AlignCenter)
    body.addWidget(filename)
    choose = QPushButton('选择…')
    choose.setProperty('mixerAction', 'choose')
    body.addWidget(choose)

    visual = QHBoxLayout()
    visual.addStretch(1)
    meter = _LevelMeter(Qt.Vertical)
    meter.set_levels(-18.0 if state != 'muted' else -60.0,
                     -9.0 if state != 'muted' else -60.0)
    visual.addWidget(meter)
    visual.addStretch(1)
    body.addLayout(visual)
    status = label({'muted': '静音', 'solo': '独奏', 'filtered': '其他轨独奏中'}.get(state, '已就绪'),
                   'muted' if state in ('muted', 'filtered') else 'success')
    status.setAlignment(Qt.AlignCenter)
    body.addWidget(status)
    return strip


def build_host(app):
    host = QWidget()
    outer = QVBoxLayout(host)
    outer.setContentsMargins(14, 14, 14, 14)
    cards = []
    for index, title in enumerate(('混音通道', '人声处理与空间', '试听与播放', '导出')):
        frame, body = card(title, '主题对比样本', str(index + 1))
        if index == 0:
            rack = QHBoxLayout()
            rack.setSpacing(7)
            for number, (kind, state) in enumerate((
                    ('vocal', 'normal'), ('vocal', 'muted'),
                    ('vocal', 'solo'), ('backing', 'filtered'))):
                rack.addWidget(channel_strip(kind, state))
            body.addLayout(rack)
        else:
            body.addWidget(label('可读的文字、状态与控制保持同一层级。', 'muted'))
            body.addStretch(1)
        cards.append(frame)
    workspace = _MixerWorkspace(*cards)
    outer.addWidget(workspace, 1)
    host.workspace = workspace
    host.resize(1600, 1000)
    host.show()
    settle(app)
    return host


def build_analysis_host(app):
    host = QWidget()
    host.setObjectName('mixdownDialog')
    outer = QVBoxLayout(host)
    outer.setContentsMargins(20, 18, 20, 18)
    outer.setSpacing(12)

    surface, body = card(
        '分析卡片', '四主题 tab/page/viewport 与曲线卡片像素核验。', '1')
    preset = PresetBar(
        (('custom', '自定义'), ('clear_lead', '清透主唱')),
        current_key='custom',
    )
    body.addWidget(preset)

    eq_curve = EQCurveView()
    eq_curve.setMinimumHeight(230)
    body.addWidget(eq_curve)

    tabs = QTabWidget()
    tabs.setObjectName('mixdownAnalysisTabs')
    gr_history = GrHistoryView(auto_start=False)
    gr_history.setMinimumSize(0, 150)
    gr_history.setMaximumHeight(170)
    reverb_decay = ReverbDecayView()
    reverb_decay.setMinimumSize(0, 150)
    reverb_decay.setMaximumHeight(170)
    tabs.addTab(gr_history, '压缩 GR')
    tabs.addTab(reverb_decay, '实测 EDC')
    tabs.setMinimumHeight(185)
    tabs.setMaximumHeight(205)
    body.addWidget(tabs)

    diagnostic = label('实时诊断：等待试听包络', 'warning')
    diagnostic.setObjectName('mixdownPreviewStatus')
    body.addWidget(diagnostic)
    summary = label('成功：最近一次分析已更新', 'success')
    summary.setObjectName('mixdownLoudnessSummary')
    body.addWidget(summary)

    track = QFrame()
    track.setObjectName('mixer-channel')
    track.setProperty('channelKind', 'vocal')
    track.setProperty('channelMuted', False)
    track.setProperty('channelSolo', False)
    track.setProperty('channelFiltered', False)
    track_layout = QHBoxLayout(track)
    track_layout.setContentsMargins(10, 8, 10, 8)
    track_layout.addWidget(label('V1 · LEAD', 'muted'))
    track_layout.addStretch(1)
    track_status = QPushButton('M')
    track_status.setProperty('mixerToggle', 'mute')
    track_status.setFixedSize(26, 24)
    track_layout.addWidget(track_status)
    body.addWidget(track)

    outer.addWidget(surface)
    host.preset_bar = preset
    host.analysis_tabs = tabs
    host.eq_curve = eq_curve
    host.gr_history = gr_history
    host.reverb_decay = reverb_decay
    host.track_card = track
    host.resize(*SCREEN_SIZES[0])
    host.show()
    settle(app)
    return host


def build_transparency_probe(app):
    """Build a plain page and scroll viewport over a uniquely colored parent."""
    host = QWidget()
    host.setObjectName('mixdownDialog')
    outer = QVBoxLayout(host)
    outer.setContentsMargins(18, 18, 18, 18)
    surface = QFrame()
    surface.setObjectName('analysisTransparencySurface')
    surface.setStyleSheet(
        'QFrame#analysisTransparencySurface { background: #407090; border: 0; }')
    surface_layout = QVBoxLayout(surface)
    surface_layout.setContentsMargins(7, 7, 7, 7)
    tabs = QTabWidget()
    tabs.setObjectName('mixdownAnalysisTabs')
    tabs.setUsesScrollButtons(True)
    tabs.setFixedSize(310, 150)
    for index in range(6):
        page = QWidget()
        page.setObjectName('mixdownTransparencyPage{0}'.format(index))
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        if index == 0:
            scroll = QScrollArea()
            scroll.setObjectName('mixdownTransparencyScroll')
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            content = QWidget()
            content.setMinimumSize(700, 500)
            scroll.setWidget(content)
            page_layout.addWidget(scroll)
        tabs.addTab(page, 'Scrolling analysis tab {0}'.format(index + 1))
    surface_layout.addWidget(tabs)
    outer.addWidget(surface)
    host.transparency_surface = surface
    host.transparency_tabs = tabs
    host.resize(360, 220)
    host.show()
    settle(app)
    return host


def screenshot_rect(host, widget):
    point = widget.mapTo(host, QPoint(0, 0))
    return QRect(point, widget.size())


def image_rect(image, host, widget):
    rect = screenshot_rect(host, widget)
    scale_x = image.width() / max(1, host.width())
    scale_y = image.height() / max(1, host.height())
    return QRect(
        round(rect.left() * scale_x), round(rect.top() * scale_y),
        max(1, round(rect.width() * scale_x)),
        max(1, round(rect.height() * scale_y)),
    )


def color_alpha(value):
    match = re.fullmatch(
        r'rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*\)', value)
    if match:
        return int(match.group(1))
    return QColor(value).alpha()


def image_variation(image, rect):
    buckets = set()
    step_x = max(2, rect.width() // 32)
    step_y = max(2, rect.height() // 20)
    for y in range(rect.top() + 2, rect.bottom() - 1, step_y):
        for x in range(rect.left() + 2, rect.right() - 1, step_x):
            color = image.pixelColor(x, y)
            buckets.add((color.red() // 8, color.green() // 8, color.blue() // 8))
    return len(buckets)


def verify_analysis_theme(app, host, mode, material, scale):
    colors = theme_colors(mode, material)
    tokens = mixdown_analysis_tokens(mode, material, scale)
    qss = mixdown_analysis_qss(mode, material, scale)
    compact = ' '.join(qss.split())
    assert '$' not in qss
    assert 'background: #000000' not in compact.lower()
    assert QColor(tokens['chart_surface']).name() != '#000000'
    for selector in REQUIRED_ANALYSIS_SELECTORS:
        assert selector in compact, (mode, material, selector)
    assert qss in host.styleSheet()
    assert tokens['border_width'] == (2 if scale >= 130 else 1)
    assert color_alpha(tokens['chart_fill']) == tokens['chart_alpha']
    if material == 'glass':
        assert tokens['chart_alpha'] < 255
        assert tokens['chart_fill'].startswith('rgba(')
        assert tokens['tab_selected'].startswith('rgba(')
    else:
        assert tokens['chart_alpha'] == 255
        assert not tokens['chart_fill'].startswith('rgba(')
        assert not tokens['tab_selected'].startswith('rgba(')

    chart_background = tokens['chart_surface']
    for key in ('text', 'success', 'warning', 'error'):
        assert contrast(tokens[key], chart_background) >= 4.5, (
            mode, material, key, contrast(tokens[key], chart_background))
    assert contrast(tokens['disabled_text'], tokens['disabled']) >= 3.0

    assert host.eq_curve.objectName() == 'mixdownEqCurve'
    assert host.gr_history.objectName() == 'mixdownGrHistory'
    assert host.reverb_decay.objectName() == 'mixdownReverbDecay'
    assert host.analysis_tabs.objectName() == 'mixdownAnalysisTabs'
    assert host.preset_bar.objectName() == 'mixdownPresetBar'
    assert host.track_card.objectName() == 'mixer-channel'
    for role in (QPalette.Mid, QPalette.Light, QPalette.Shadow):
        color = app.palette().color(role)
        assert color.isValid() and max(color.red(), color.green(), color.blue()) > 0
    for widget in (host.analysis_tabs, host.eq_curve, host.gr_history,
                   host.reverb_decay):
        palette_base = widget.palette().color(QPalette.Base)
        assert palette_base.name().lower() == QColor(chart_background).name().lower()
        assert palette_base.alpha() == tokens['chart_alpha']
    stack = host.analysis_tabs.findChild(QStackedWidget)
    assert stack is not None
    stack_base = stack.palette().color(QPalette.Base)
    assert stack_base.name().lower() == QColor(chart_background).name().lower()
    assert stack_base.alpha() == tokens['chart_alpha']
    page = host.analysis_tabs.currentWidget()
    page_base = page.palette().color(QPalette.Base)
    assert page_base.name().lower() == QColor(chart_background).name().lower()
    assert page_base.alpha() == tokens['chart_alpha']


def verify_transparency_probe(app, host, mode, material):
    del app
    tokens = mixdown_analysis_tokens(mode, material, 130)
    tabs = host.transparency_tabs
    page = tabs.currentWidget()
    stack = tabs.findChild(QStackedWidget)
    scroll = page.findChild(QScrollArea)
    assert stack is not None and scroll is not None
    assert page.objectName() == 'mixdownTransparencyPage0'
    assert scroll.objectName() == 'mixdownTransparencyScroll'
    image = host.grab().toImage()
    expected = QColor('#407090')
    page_color = image.pixelColor(image_rect(image, host, page).center())
    viewport_color = image.pixelColor(
        image_rect(image, host, scroll.viewport()).center())
    for shown in (page_color, viewport_color):
        assert max(abs(shown.red() - expected.red()),
                   abs(shown.green() - expected.green()),
                   abs(shown.blue() - expected.blue())) <= 4, (
                       shown.red(), shown.green(), shown.blue())
    bar = tabs.tabBar()
    buttons = [button for button in bar.findChildren(QToolButton)
               if button.isVisible()]
    assert buttons
    for button in buttons:
        text_color = button.palette().color(QPalette.Text)
        assert max(text_color.red(), text_color.green(), text_color.blue()) > 0
    assert page.palette().color(QPalette.Base).alpha() == tokens['chart_alpha']
    assert scroll.viewport().palette().color(QPalette.Base).alpha() == tokens['chart_alpha']


def main():
    app = QApplication.instance() or QApplication([])
    host = build_host(app)
    output = APP / 'work' / 'theme-smoke'
    output.mkdir(parents=True, exist_ok=True)
    for mode in ('light', 'dark'):
        for material in ('glass', 'solid'):
            for scale in (100, 115, 130):
                host._vwa_appearance = {
                    'ui_scale': scale, 'material': material,
                    'reduce_motion': False, 'ui_language': 'zh-CN',
                }
                apply_theme(host, mode)
                settle(app)
                assert '$' not in host.styleSheet()
                assert app.property('vwa-theme') == mode
                assert app.property('vwa-material') == material
                colors = theme_colors(mode, material)
                if material == 'glass':
                    for key in ('surface', 'mixer_surface', 'mixer_console', 'mixer_well',
                                'mixer_toggle', 'mixer_muted', 'mixer_solo', 'mixer_filtered'):
                        assert colors[key].startswith('rgba('), (mode, key, colors[key])
                else:
                    for key in ('mixer_surface', 'mixer_muted', 'mixer_solo', 'mixer_filtered'):
                        assert not colors[key].startswith('rgba('), (mode, key, colors[key])
                factor = scale / 100.0
                choose_rule = re.search(
                    r'QPushButton\[mixerAction="choose"\] \{[^}]+\}', host.styleSheet()).group(0)
                assert 'font-size: {0}px'.format(round(14 * factor)) in choose_rule
                assert host.workspace.column_count() == 3
                assert not host.workspace._right_stack.isHidden()

    for mode, material, scale in (
            ('light', 'glass', 100), ('light', 'solid', 115),
            ('dark', 'glass', 130), ('dark', 'solid', 130)):
        host._vwa_appearance = {
            'ui_scale': scale, 'material': material,
            'reduce_motion': False, 'ui_language': 'zh-CN',
        }
        apply_theme(host, mode)
        settle(app)
        image = output / 'mixdown-{0}-{1}-{2}.png'.format(mode, material, scale)
        assert host.grab().save(str(image))
        assert image.stat().st_size > 10000

    for mode in ('light', 'dark'):
        colors = COLORS[mode]
        pairs = (
            (colors['muted'], colors['danger_bg']),
            (colors['selected_text'], colors['selected']),
            (colors['muted'], colors['disabled']),
            (colors['text'], colors['input']),
            (colors['text'], colors['mixer_toggle']),
            (colors['danger_on'], colors['danger']),
            (colors['accent_text'], colors['accent']),
        )
        assert all(contrast(*pair) >= 4.5 for pair in pairs), (mode, [contrast(*pair) for pair in pairs])

    analysis_host = build_analysis_host(app)
    transparency_host = build_transparency_probe(app)
    for mode, material in MIXDOWN_THEMES:
        analysis_host._vwa_appearance = {
            'ui_scale': 130, 'material': material,
            'reduce_motion': False, 'ui_language': 'zh-CN',
        }
        apply_theme(analysis_host, mode)
        settle(app)
        verify_analysis_theme(app, analysis_host, mode, material, 130)
        for width, height in SCREEN_SIZES:
            analysis_host.resize(width, height)
            settle(app)
            image = analysis_host.grab().toImage()
            page = analysis_host.analysis_tabs.currentWidget()
            page_rect = image_rect(image, analysis_host, page)
            page_color = image.pixelColor(page_rect.center())
            expected_surface = QColor(
                mixdown_analysis_tokens(mode, material, 130)['chart_surface'])
            assert max(page_color.red(), page_color.green(), page_color.blue()) > 24
            channel_distance = max(
                abs(page_color.red() - expected_surface.red()),
                abs(page_color.green() - expected_surface.green()),
                abs(page_color.blue() - expected_surface.blue()),
            )
            assert channel_distance <= 24, (
                mode, material, page_color.getRgb(), expected_surface.getRgb())
            assert image_variation(image, page_rect) >= 6, (
                mode, material, width, height, image_variation(image, page_rect))
            eq_rect = image_rect(image, analysis_host, analysis_host.eq_curve)
            assert image_variation(image, eq_rect) >= 4, (
                mode, material, width, height, image_variation(image, eq_rect))
            screenshot = output / 'analysis-{0}-{1}-{2}x{3}.png'.format(
                mode, material, width, height)
            assert analysis_host.grab().save(str(screenshot))
            assert screenshot.stat().st_size > 10000
            saved = QImage(str(screenshot))
            saved_rect = image_rect(saved, analysis_host, page)
            saved_color = saved.pixelColor(saved_rect.center())
            image_color = image.pixelColor(page_rect.center())
            assert max(abs(saved_color.red() - image_color.red()),
                       abs(saved_color.green() - image_color.green()),
                       abs(saved_color.blue() - image_color.blue())) <= 2
        transparency_host._vwa_appearance = dict(analysis_host._vwa_appearance)
        apply_theme(transparency_host, mode)
        settle(app)
        verify_transparency_probe(app, transparency_host, mode, material)
        probe_screenshot = output / 'transparency-{0}-{1}-130.png'.format(
            mode, material)
        assert transparency_host.grab().save(str(probe_screenshot))
        assert probe_screenshot.stat().st_size > 4000
    transparency_host.close()
    analysis_host.close()
    settle(app)

    host.resize(1100, 800)
    settle(app)
    assert host.workspace.column_count() == 2
    host.resize(820, 720)
    settle(app)
    assert host.workspace.column_count() == 1
    host.close()
    settle(app)
    print('PASS: 2 themes x 2 materials x 3 scales; 4 themes x 3 full-screen sizes; '
          'analysis pane/page/stack/viewport transparency and palette/render pixels; offscreen')


if __name__ == '__main__':
    main()
