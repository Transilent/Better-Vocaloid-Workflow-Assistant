"""UI languages preserve operational keys, private lyrics and saved values."""
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
        for index, name in ((0, 'preparation'), (9, 'tuning'), (3, 'publishing'), (1, 'components'), (4, 'settings')):
            window.navigate(index)
            application.processEvents()
            target = out / ('ui-language-' + language + '-' + name + '.png')
            assert window.grab().save(str(target))
            screens.append(str(target))
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
