"""UI languages preserve operational keys, private lyrics and saved values."""
import ast
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', 'Use tests/run_tests.py isolation'

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QAbstractButton, QComboBox, QScrollArea, QWidget
from app import Window
import i18n
from mixdown_eq_curve import EQCurveView
from mixdown_live_meters import (GrHistoryView, MeterBankWidget, MeterEnvelope,
                                 ReverbDecayView)

_EQ_HELP_SOURCE = (
    '拖动节点：频率/增益；滚轮：Q（高通固定，Shift 精调）；'
    'Alt+点击节点或“旁路”按钮：启停；双击/Enter：恢复默认；'
    '方向键：调频/增益，Shift 更细；Ctrl+↑↓ 或 PageUp/Down：调 Q；'
    'Space：旁路。'
)


def _assert_mixdown_custom_widget_localization(mixer, language):
    """Inspect the painted widgets' visible text and accessibility surface."""
    assert mixer.eq_curve.accessibleName() == i18n.tr('实时四段 EQ 曲线')
    assert mixer.gr_history.accessibleName() == i18n.tr('GR 历史曲线')
    assert mixer.reverb_decay.accessibleName() == i18n.tr('实测混响能量衰减曲线')

    eq = EQCurveView()
    gr = GrHistoryView(auto_start=False)
    edc = ReverbDecayView(auto_start=False)
    feed = MeterEnvelope(auto_start=False)
    feed.set_report({
        'duration_seconds': 2.0,
        'peak_envelope': {
            'tracks': [
                {'role': 'vocal', 'index': 0, 'label': '歌声 1',
                 'db': [-24.0, -12.0], 'hz': 10.0},
                {'role': 'backing', 'index': 0,
                 'db': [-20.0, -16.0], 'hz': 10.0},
            ],
            'bus': {'db': [-6.0, -3.0], 'hz': 10.0},
        },
    })
    meters = MeterBankWidget(feed=feed)
    try:
        assert eq.accessibleName() == i18n.tr('四段交互均衡器')
        assert eq.toolTip() == i18n.tr(_EQ_HELP_SOURCE)
        assert eq.accessibleDescription() == i18n.tr(
            '第 {0} 段：{1}，{2}，频率 {3:.2f} Hz，{4}，{5}。',
            1, i18n.tr('高通'), i18n.tr('启用'), 90.0,
            i18n.tr('增益固定'), i18n.tr('Q 固定') + ' 0.707',
        ) + i18n.tr(_EQ_HELP_SOURCE)
        assert eq.bubble_text(1) == (
            i18n.tr('低架') + ' 260.0 Hz  +0.00 dB  Q 0.707 · '
            + i18n.tr('旁路'))
        assert gr.caption == i18n.tr('GR 为分块RMS估计（非采样精确）')
        assert edc.measurement_status == i18n.tr('未实测')
        assert edc.placeholder_text == i18n.tr('暂无实测 EDC · 等待脉冲测量')
        assert meters.accessibleName() == i18n.tr('实时混音电平表')
        assert meters.position_text == i18n.tr('位置') + ' 0:00.000 / 0:02.000'
        labels = {row['label'] for row in feed.snapshot(0)['lanes']}
        assert i18n.tr('歌声 %d') % 1 in labels
        assert i18n.tr('伴奏') in labels

        edc.set_report({
            'reverb_edc': {
                'db': [0.0, -12.0, -60.0],
                'time_s': [0.0, 0.2, 1.0],
                'cache_state': 'hit',
                'measured': True,
            },
        })
        assert edc.cache_status == i18n.tr('缓存命中')
        assert edc.measurement_status == i18n.tr('实测')
        assert not eq.grab().isNull()
        assert not gr.grab().isNull()
        assert not edc.grab().isNull()
        assert not meters.grab().isNull()

        if language == 'en':
            for text in (eq.accessibleDescription(), eq.bubble_text(1),
                         gr.caption, edc.placeholder_text,
                         edc.cache_status, meters.position_text):
                assert not _CJK.search(text), text
    finally:
        feed.deleteLater()
        for widget in (meters, edc, gr, eq):
            widget.close()
            widget.deleteLater()

# P1 mixdown catalog contracts. The project translates Chinese display source
# text directly, so these checks deliberately exercise that same contract.
def _unique_json_object(pairs):
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, 'Duplicate JSON keys: ' + ', '.join(duplicates)
    return dict(pairs)

ADDITIONS = json.loads(
    (APP / 'assets/i18n/additions.json').read_text(encoding='utf-8'),
    object_pairs_hook=_unique_json_object,
)
assert set(ADDITIONS) == {'en', 'ja'}
assert set(ADDITIONS['en']) == set(ADDITIONS['ja'])
_CJK = re.compile(r'[\u3400-\u9fff]')
_FORMAT_PLACEHOLDER = re.compile(r'\{(\d+)(?::[^}]*|![^}]*)?\}')
_PRINTF_PLACEHOLDER = re.compile(r'%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?[sdgfr]')

def _placeholder_shape(text):
    return (sorted(_FORMAT_PLACEHOLDER.findall(text)),
            sorted(_PRINTF_PLACEHOLDER.findall(text)))

for language in ('en', 'ja'):
    for source, target in ADDITIONS[language].items():
        assert isinstance(target, str) and target.strip(), (language, source)
        assert _placeholder_shape(source) == _placeholder_shape(target), (language, source, target)
    merged = i18n.catalog(language)
    for source, target in ADDITIONS[language].items():
        assert merged.get(source) == target, (language, source)

MIXDOWN_SOURCES = (
    APP / 'src/audio_mixdown_ui.py',
    *sorted((APP / 'src').glob('mixdown_*.py')),
)
MIXDOWN_DIRECT_KEYS = set()
for source_path in MIXDOWN_SOURCES:
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        name = (function.id if isinstance(function, ast.Name)
                else function.attr if isinstance(function, ast.Attribute) else '')
        argument = node.args[0]
        if name in {'_tr', 'tr'} and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            MIXDOWN_DIRECT_KEYS.add(argument.value)

# These currently visible labels are painted or assigned inside the EQ/meter
# widgets rather than passed through the host at this commit. Keep their
# Chinese-source keys reserved so wiring them later does not fork terminology.
MIXDOWN_RAW_UI_KEYS = {
    '锁定轨道设置', '压缩 GR', '实测 EDC', '高通', '低架', '峰值', '钟形', '高架',
    '旁路', '启用', '禁用', '频率', '增益', '品质因数 Q', '四段交互均衡器',
    '拖动节点调整频率和增益；滚轮调整 Q；双击启停；方向键微调。',
    '当前没有可编辑的均衡器频段。', '实时混音电平表', '实时电平 · dBFS',
    '30 Hz 合并 · 峰值保持 %g s', '暂无实时电平 · 等待试听包络', '峰值电平',
    '混音总线', '播放头', '缓存', '实测', '总线', '歌声 %d', '计算中', '缓存命中',
    '缓存未命中', '已生成', '计算失败', '无数据', '缓存键 ', 'GR 历史曲线',
    'GR 为分块RMS估计（非采样精确）', 'GR 历史 · 最近 %g s',
    '暂无 GR 数据 · 等待试听包络', '当前区间没有 GR 采样', '实测混响能量衰减曲线',
    '实测 EDC 不可用 · %s', '正在测量 EDC · 等待脉冲结果',
    '暂无实测 EDC · 等待脉冲测量', '实测 EDC · 能量衰减', '计算中…',
    '混响预延迟毫秒', '峰值对比：',
}
for key in MIXDOWN_RAW_UI_KEYS:
    assert key in ADDITIONS['en'] and key in ADDITIONS['ja'], key
MIXDOWN_REQUIRED_KEYS = MIXDOWN_DIRECT_KEYS | MIXDOWN_RAW_UI_KEYS
_JA_IDENTICAL_ALLOWED = {'位置', '伴奏', '伴奏音量'}

for language in ('en', 'ja'):
    merged = i18n.catalog(language)
    for source in MIXDOWN_REQUIRED_KEYS:
        target = merged.get(source)
        assert isinstance(target, str) and target.strip(), (language, source)
        assert _placeholder_shape(source) == _placeholder_shape(target), (language, source, target)
        if language == 'en':
            assert not _CJK.search(target), (source, target)
        elif _CJK.search(source) and source not in _JA_IDENTICAL_ALLOWED:
            assert target != source, (source, target)
from ui_preferences import DEFAULTS, merge_appearance, normalize, save_appearance
from common import ROOT, atomic_json, workspace_state

for language in ('en', 'ja'):
    raw = json.loads((APP / 'assets/i18n' / (language + '.json')).read_text(encoding='utf-8'))
    assert len(raw) >= 1200
    for source, target in raw.items():
        assert isinstance(target, str) and target.strip(), (language, source)
        assert sorted(re.findall(r'\{\d+\}', source)) == sorted(re.findall(r'\{\d+\}', target)), (language, source)
    assert len(i18n.catalog(language)) >= len(raw)

assert normalize({'ui_language': 'broken'})['ui_language'] == 'zh-CN'
saved = {'tuning': {'strength': .625}, 'output_directory': 'keep', 'appearance': {'future': 'keep'}}
merged = merge_appearance(saved, {'ui_language': 'ja'})
assert merged['tuning'] == saved['tuning'] and merged['output_directory'] == 'keep'
assert merged['appearance']['future'] == 'keep' and merged['appearance']['ui_language'] == 'ja'
atomic_json(ROOT / 'cache/workspace.json', saved)
save_appearance({'ui_language': 'en'})
assert workspace_state()['tuning'] == saved['tuning']
assert workspace_state()['appearance']['ui_language'] == 'en'
original_lyrics = '夏の風 私の歌\n私人歌词，never translate this payload'

application = QApplication.instance() or QApplication([])
screens = []
residuals = {}
out = APP / 'work/test-results'
out.mkdir(parents=True, exist_ok=True)
for language in ('zh-CN', 'ja', 'en'):
    preferences = {**DEFAULTS, 'ui_language': language}
    with patch('ui_preferences.load_appearance', return_value=preferences):
        window = Window()
    try:
        window.show()
        QTest.qWait(60)
        application.processEvents()
        assert i18n.current_language() == language
        assert window.nav_buttons[0].text() == i18n.tr('准备工作流')
        assert window.nav_buttons[9].text() == i18n.tr('调音工作流')
        assert window.nav_buttons[8].text() == i18n.tr('合成视频')
        assert window.nav_buttons[3].text() == i18n.tr('发布')
        if language != 'zh-CN':
            assert window.nav_buttons[0].text() != '准备工作流'
            assert window.language.itemText(0) != '中文'
            assert window.device_button.text() != '设备设置'
        assert window.workflow.preset.itemData(0) == '自定义'
        assert window.appearance_panel.language.currentData() == language
        assert [window.language.itemData(i) for i in range(window.language.count())] == ['zh', 'ja']
        assert window.publishing.cover_mode.itemData(0) == 'original'
        from style_presets import PRESETS, visible_presets
        from tuning_ui import style_card_description, style_card_keys, style_card_label
        combo = window.tuning.performance_style
        assert style_card_keys() == visible_presets() == ('none', 'gentle', 'sad', 'bold')
        assert [combo.itemData(i) for i in range(combo.count())] == list(style_card_keys())
        source_labels={'gentle':'柔和〔实验〕','sad':'悲伤〔实验〕','bold':'明亮欢快〔实验〕'}
        for style in style_card_keys()[1:]:
            combo.setCurrentIndex(combo.findData(style))
            assert combo.currentIndex() >= 0
            assert combo.currentText() == style_card_label(style)
            assert window.tuning.style_hint.text() == style_card_description(style)
            if language != 'zh-CN':
                assert combo.currentText() != source_labels[style]
                assert style_card_label(style) != source_labels[style]
        if language == 'zh-CN':
            assert i18n.tr('明亮欢快〔实验〕') == '明亮欢快〔实验〕'
            assert i18n.tr('悲伤〔实验〕') == '悲伤〔实验〕'
        else:
            assert i18n.tr('明亮欢快〔实验〕') != '明亮欢快〔实验〕'
            assert i18n.tr('悲伤〔实验〕') != '悲伤〔实验〕'
            expected_kind={'en':'Emotion (Sad)','ja':'感情（悲しみ）'}[language]
            assert expected_kind in style_card_description('sad')
        for legacy in ('soft_ballad', 'intimate_narration', 'power_pop',
                       'melancholy', 'bright_joy', 'fragile_tender'):
            legacy_label = i18n.tr(PRESETS[legacy]['label'])
            if language == 'zh-CN':
                assert legacy_label == PRESETS[legacy]['label']
            else:
                assert legacy_label != PRESETS[legacy]['label']
        video_page = window.pages.widget(8)
        assert video_page.alignment_analyze.text() == i18n.tr('分析音频对齐')
        assert video_page.use_alignment_suggestion.text() == i18n.tr('使用建议偏移')
        if language != 'zh-CN':
            assert video_page.alignment_analyze.text() != '分析音频对齐'
        assert i18n.tr('来源任务：{0}', '发布').endswith('发布'), 'Formatted private task titles must remain verbatim'
        from tuning_ui import format_tuning_result_summary
        summary = format_tuning_result_summary({'status': 'applied', 'report': {'track_indices': [0], 'pitch_changed': False}})
        if language != 'zh-CN':
            assert '处理结果' not in summary and '处理轨道' not in summary and '局部音高' not in summary
        window.lyrics.setPlainText(original_lyrics)
        assert window.lyrics.toPlainText() == original_lyrics
        before_options = window.workflow.preferences()
        # Saving a language does not rebuild a running page or change ASR data.
        window.appearance_panel.language.setCurrentIndex(window.appearance_panel.language.findData('en'))
        assert window.workflow.preferences() == before_options
        assert window.lyrics.toPlainText() == original_lyrics
        assert i18n.current_language() == language
        window.appearance_panel.language.setCurrentIndex(window.appearance_panel.language.findData(language))
        window.resize(1360, 900)
        for index, name in ((0, 'preparation'), (10, 'audio-mixdown'), (9, 'tuning'), (3, 'publishing'), (1, 'components'), (4, 'settings')):
            window.navigate(index)
            application.processEvents()
            target = out / ('ui-language-' + language + '-' + name + '.png')
            assert window.grab().save(str(target))
            screens.append(str(target))
            if index == 10:
                mixer = window.audio_mixdown.mixer
                assert [mixer.analysis_tabs.tabText(index) for index in range(3)] == [
                    i18n.tr('EQ'), i18n.tr('GR'), i18n.tr('EDC')]
                assert mixer.preset_bar.lock_mix_checkbox.text() == i18n.tr('锁定轨道设置')
                assert mixer.preset_bar.lock_mix_checkbox.accessibleName() == i18n.tr(
                    '锁定轨道设置，应用预设时保留各轨声像和混响发送')
                assert mixer.width_control.accessibleName() == i18n.tr('立体声宽度')
                assert mixer.send_controls_fold.accessibleName() == i18n.tr('展开每轨混响发送')
                assert mixer.preview_report.accessibleName() == i18n.tr('实时试听状态')
                assert mixer.player_position.accessibleName() == i18n.tr('试听播放位置')
                if language != 'zh-CN':
                    assert mixer.audition_diagnostics.accessibleName() == i18n.tr('实时试听诊断')
                    assert mixer.width_control.accessibleName() != '立体声宽度'
                _assert_mixdown_custom_widget_localization(mixer, language)
            if index == 9:
                from strength_control import set_combo_strength
                window.tuning.mode.setCurrentIndex(window.tuning.mode.findData('reference'))
                set_combo_strength(window.tuning.dynamics_strength, .625)
                scroll = window.tuning.form.parentWidget()
                while scroll is not None and not isinstance(scroll, QScrollArea):
                    scroll = scroll.parentWidget()
                assert scroll is not None, 'The tuning form must belong to a scroll area'
                scroll.ensureWidgetVisible(window.tuning.dynamics_strength_spin, 0, 20)
                application.processEvents()
                assert window.tuning.dynamics_strength.currentData() == .625
                assert window.tuning.dynamics_strength_spin.value() == 62.5
                target = out / ('ui-language-' + language + '-strength.png')
                assert window.grab().save(str(target))
                screens.append(str(target))
        pending = []
        if language != 'zh-CN':
            for widget in window.findChildren(QWidget):
                if not isinstance(widget, (QLabel, QAbstractButton, QComboBox)):
                    continue
                texts = [widget.itemText(i) for i in range(widget.count())] if isinstance(widget, QComboBox) else [widget.text()]
                for text in texts:
                    translated = i18n.tr(text)
                    if translated != text:
                        pending.append({'type': type(widget).__name__, 'source': text, 'translation': translated})
        residuals[language] = pending
        assert not pending, (language, pending)
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()
i18n.set_language('zh-CN')
(out / 'language-residuals.json').write_text(json.dumps(residuals, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'languages': 3, 'private_lyrics_preserved': True, 'semantic_keys_preserved': True, 'screenshots': screens}))
