"""Real preparation-page interaction contracts in the isolated desktop app."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', \
    'Use tests/run_tests.py isolation'

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QSizePolicy
from app import Window
from processing_ui import PreparationGrid
from ui_components import FlowLayout, LyricsEdit

application = QApplication([])
window = Window()
window.show()
QTest.qWait(20)
application.processEvents()


def settle():
    for _ in range(4):
        application.processEvents()


def stacked(columns):
    return bool(columns._stacked)


def origin(widget):
    return widget.mapTo(window.step_area, widget.rect().topLeft())

try:
    # The same four card owners are rearranged at each supported width. The
    # wide arrangement puts output at the far right and keeps every group top
    # aligned instead of stretching short cards to the lyrics height.
    workspace = window.prep_workspace
    assert isinstance(workspace, PreparationGrid)
    assert workspace.WIDE_RATIOS == (3, 8, 3)
    assert window.lyrics_columns.breakpoint == 880
    assert [window.midi_steps.itemData(index) for index in range(window.midi_steps.count())] == [
        8, 16, 32
    ]
    assert [window.zh_lyric_mode.itemData(index) for index in range(window.zh_lyric_mode.count())] == [
        'hanzi', 'pinyin'
    ]
    assert window.device_button.text() == '设备设置'
    assert window.open_subtitles_button.text() == '打开字幕工具'
    assert isinstance(window.subtitle_layout, FlowLayout)

    window.resize(1000, 680)
    settle()
    assert workspace.mode == 'narrow'
    assert workspace.left_group.isHidden() and workspace.left_layout.count() == 0
    assert window.step_cards['source'].parentWidget() is workspace
    assert window.step_cards['separation'].parentWidget() is workspace
    assert stacked(window.lyrics_columns)
    assert origin(window.step_cards['source']).y() < origin(window.step_cards['separation']).y()
    assert origin(window.step_cards['source']).y() < origin(window.step_cards['output']).y()
    assert origin(window.step_cards['output']).y() < origin(window.step_cards['separation']).y()
    assert origin(window.step_cards['separation']).y() < origin(window.step_cards['reference']).y()
    assert window.step_area._scroll.horizontalScrollBar().maximum() == 0

    for size in ((1440, 900), (1600, 1000)):
        window.resize(*size)
        settle()
        assert workspace.mode == 'medium'
        assert workspace.left_group.isHidden() and workspace.left_layout.count() == 0
        assert window.step_cards['source'].parentWidget() is workspace
        assert window.step_cards['separation'].parentWidget() is workspace
        assert not stacked(window.lyrics_columns)
        source_pos = origin(window.step_cards['source'])
        output_pos = origin(window.step_cards['output'])
        separation_pos = origin(window.step_cards['separation'])
        lyrics_pos = origin(window.step_cards['reference'])
        assert abs(source_pos.y() - output_pos.y()) <= 1
        assert output_pos.x() > source_pos.x()
        assert separation_pos.y() > source_pos.y()
        assert lyrics_pos.y() > separation_pos.y()
        assert window.step_cards['separation'].width() > window.step_cards['source'].width() * 1.5
        assert window.step_cards['reference'].width() > window.step_cards['source'].width() * 1.5
        assert window.step_area._scroll.horizontalScrollBar().maximum() == 0
        if size == (1600, 1000):
            viewport = window.step_area._scroll.viewport()
            for control in (window.manual_bpm, window.bpm, window.include_instrumental):
                top_left = control.mapTo(viewport, control.rect().topLeft())
                bottom_right = control.mapTo(viewport, control.rect().bottomRight())
                assert viewport.rect().contains(top_left) and viewport.rect().contains(bottom_right), \
                    (control.text() if hasattr(control, 'text') else control.objectName(), 'below first viewport')

    window.resize(2512, 1352)
    settle()
    assert workspace.mode == 'wide'
    assert workspace.left_group.isVisible()
    assert workspace.left_layout.count() == 2
    assert not stacked(window.lyrics_columns)
    source_card = window.step_cards['source']
    processing_card = window.step_cards['separation']
    lyrics_card = window.step_cards['reference']
    output_card = window.step_cards['output']
    source_pos = origin(source_card)
    separation_pos = origin(processing_card)
    lyrics_pos = origin(lyrics_card)
    output_pos = origin(output_card)
    assert abs(source_pos.x() - separation_pos.x()) <= 1
    assert separation_pos.y() > source_pos.y()
    assert source_card.parentWidget() is workspace.left_group
    assert processing_card.parentWidget() is workspace.left_group
    assert workspace.left_group.geometry().left() == 0
    assert processing_card.geometry().top() - source_card.geometry().bottom() <= 16
    expected_left_height = source_card.height() + processing_card.height() + workspace.left_layout.spacing()
    assert abs(workspace.left_group.height() - expected_left_height) <= 3
    assert workspace.left_group.height() < lyrics_card.height()
    assert source_pos.x() < lyrics_pos.x() < output_pos.x()
    assert abs(source_pos.y() - lyrics_pos.y()) <= 1
    assert abs(source_pos.y() - output_pos.y()) <= 1
    assert lyrics_card.width() > source_card.width() * 2
    assert lyrics_card.width() > output_card.width() * 2
    assert abs(source_card.width() - output_card.width()) <= 4
    assert output_card.height() < lyrics_card.height()
    assert processing_card.height() < lyrics_card.height()
    assert output_card.geometry().right() >= workspace.width() - 2
    assert workspace.sizePolicy().verticalPolicy() == QSizePolicy.Maximum
    assert source_card.sizePolicy().verticalPolicy() == QSizePolicy.Maximum
    assert source_card.height() < 260
    assert window.input_mode._vwa_choice.height() < 100
    assert window.lyric_host.width() > window.note_settings.width()
    assert window.source_mode_row.isVisible()
    assert window.separation_mode_row.isVisible()
    assert window.source_mode_row.isAncestorOf(window.input_mode._vwa_choice)
    assert window.separation_mode_row.isAncestorOf(window.voice_mode._vwa_choice)
    assert not window.step_cards['separation'].details.isVisible()
    assert not window.step_cards['separation'].details.isAncestorOf(window.voice_mode._vwa_choice)
    assert window.step_area._scroll.horizontalScrollBar().maximum() == 0

    # Lyrics remain a large, usable editor without expanding to an empty slab.
    for editor in (window.lyrics, window.netease_lyrics, window.backing_lyrics):
        assert editor.minimumHeight() == 260
        assert editor.maximumHeight() == 340
        assert editor.height() <= 340

    # The compact source/output cards expose no duplicate expansion controls.
    assert window.step_cards['source'].toggle.isHidden()
    assert window.step_cards['output'].toggle.isHidden()
    assert not window.output_summary.isVisible()

    # Main and backing lyrics are real multiline edits, and switching is driven
    # through the visible tab buttons rather than swapping QLineEdit controls.
    assert isinstance(window.lyrics, LyricsEdit)
    assert isinstance(window.netease_lyrics, LyricsEdit)
    assert isinstance(window.backing_lyrics, LyricsEdit)
    window.lyrics.setText('主唱第一行\n主唱第二行')
    assert window.lyrics.text() == '主唱第一行\n主唱第二行'
    QTest.mouseClick(window.lyric_tab_buttons['backing'], Qt.LeftButton)
    application.processEvents()
    assert window.backing_lyrics.isVisible() and not window.lyrics.isVisible()
    window.backing_lyrics.setText('和声第一行\n和声第二行')
    assert window.backing_lyrics.text() == '和声第一行\n和声第二行'
    QTest.mouseClick(window.lyric_tab_buttons['lead'], Qt.LeftButton)
    application.processEvents()
    assert window.lyrics.isVisible() and not window.backing_lyrics.isVisible()

    # Closing an output stage only changes execution enablement; its format
    # preferences remain selected in the UI and in ProcessingWorkflow.preferences.
    project_before = {key: check.isChecked() for key, check in window.project_checks.items()}
    audio_before = {key: check.isChecked() for key, check in window.audio_checks.items()}
    QTest.mouseClick(window.exports_enabled, Qt.LeftButton)
    QTest.mouseClick(window.audio_enabled, Qt.LeftButton)
    application.processEvents()
    preferences = window.workflow.preferences()
    assert not preferences['project_export_enabled']
    assert not preferences['audio_export_enabled']
    assert preferences['project_formats'] == [key for key, value in project_before.items() if value]
    assert preferences['audio_formats'] == [key for key, value in audio_before.items() if value]
    QTest.mouseClick(window.exports_enabled, Qt.LeftButton)
    QTest.mouseClick(window.audio_enabled, Qt.LeftButton)
    application.processEvents()

    # Visible MIDI choices still update the original combo with integer data.
    step_choice = window.midi_steps._vwa_choice
    QTest.mouseClick(step_choice.buttons[2], Qt.LeftButton)
    application.processEvents()
    assert window.midi_steps.currentData() == 32
    assert isinstance(window.midi_steps.currentData(), int)
    assert window.precision.isChecked()
    window.manual_bpm.setChecked(True)
    window.bpm.setValue(137.5)
    assert window.options()['bpm'] == 137.5
    assert window.note_settings.objectName() == 'prep-midi-settings'
    assert window.note_settings.isAncestorOf(window.export_pitch)
    assert window.note_settings.isAncestorOf(window.include_instrumental)
    # Stage dependencies disable execution controls without losing preferences.
    saved_pitch = window.export_pitch.isChecked()
    window.midi_enabled.setChecked(False)
    assert not window.note_settings.isEnabled() and not window.exports_enabled.isEnabled()
    window.midi_enabled.setChecked(True)
    window.exports_enabled.setChecked(False)
    assert not window.export_pitch.isEnabled() and not window.include_instrumental.isEnabled()
    assert window.export_pitch.isChecked() == saved_pitch
    window.exports_enabled.setChecked(True)
    window.audio_enabled.setChecked(False)
    assert all(not check.isEnabled() for check in window.audio_checks.values())
    assert window.output_dir.isEnabled() and window.choose_output_btn.isEnabled()
    window.audio_enabled.setChecked(True)

    # A backing import must not change the independent NetEase lead source.
    imported = APP / 'work' / 'backing-lyrics.txt'
    imported.write_text('和声内容', encoding='utf-8')
    window.reference_mode.setCurrentIndex(window.reference_mode.findData('netease'))
    window.netease_lyrics.setText('原主唱内容')
    with patch('app.QFileDialog.getOpenFileName', return_value=(str(imported), '')):
        window.import_reference('backing')
    assert window.reference_mode.currentData() == 'netease'
    assert window.netease_lyrics.text() == '原主唱内容'
    assert window.backing_lyrics.isVisible() and '和声内容' in window.backing_lyrics.text()
    window.focus_step('reference')
    application.processEvents()
    assert window.lyric_tab_buttons['lead'].isChecked() and window.netease_lyrics.isVisible()

    # The subtitle switch remains the exact SubtitlesPage.auto_generate object.
    assert window.subtitles_enabled is window.subtitles.auto_generate
    window.subtitles_enabled.setChecked(True)
    assert window.subtitles.auto_generate.isChecked()
    window.focus_step('subtitles')
    application.processEvents()
    assert window.subtitle_dialog.isVisible() and window.subtitles.isVisible()

    # Running freeze disables the preparation surface and re-enables it cleanly.
    window.set_running(True)
    application.processEvents()
    assert not window.step_area.isEnabled()
    assert not window.save_preset_button.isEnabled()
    assert not window.lyric_tab_buttons['lead'].isEnabled()
    assert not window.subtitles.isEnabled()
    window.set_running(False)
    application.processEvents()
    assert window.step_area.isEnabled()
    assert window.save_preset_button.isEnabled()
    assert window.subtitles.isEnabled()
    window.subtitle_dialog.close()
    window.resize(1600, 1000)
    settle()
finally:
    window.close()
    application.processEvents()

print('PASS: real preparation window, multiline lead/backing tabs, retained output formats, '
      'integer MIDI steps, decimal BPM, responsive wide layout, same subtitle object and running freeze')
