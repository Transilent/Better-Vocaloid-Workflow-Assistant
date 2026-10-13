"""Preset reuse, export dependencies, themes and lyric dialog threading."""
import os,sys,tempfile,contextlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
import common,workflow,lyrics_sources
from lyrics_dialog import LyricsDialog
from app import Window
app=QApplication([])
with tempfile.TemporaryDirectory(dir=ROOT/'work') as tmp:
    private=Path(tmp); common.atomic_json(private/'config.json',common.load_config(False))
    with patch.object(workflow,'ROOT',private),patch.object(workflow,'workspace_state',return_value={}):
        w=Window();w.show();app.processEvents()
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.options()['midi_backend']=='v2' and w.options()['project_formats']==['ustx','vsqx','svp']
        w.midi_steps.setCurrentIndex(w.midi_steps.findData(32))
        assert w.options()['midi_steps']==32
        w.midi_enabled.setChecked(False)
        assert w.options()['project_formats']==[] and not w.subtitles_enabled.isEnabled()
        w.midi_enabled.setChecked(True)
        assert len(w.options()['project_formats'])==3
        w.step_cards['midi'].toggle.setChecked(True);app.processEvents()
        assert w.step_cards['midi'].grab().save(str(ROOT/'work/test-results/project-options-enabled.png'))
        choices={fmt:check.isChecked() for fmt,check in w.project_checks.items()}
        w.exports_enabled.setChecked(False)
        assert w.options()['project_formats']==[] and w.options()['audio_formats']==['wav']
        assert not any(check.isEnabled() for check in w.project_checks.values())
        assert not w.export_pitch.isEnabled() and not w.include_instrumental.isEnabled()
        assert w.step_cards['midi'].grab().save(str(ROOT/'work/test-results/project-options-disabled.png'))
        for check in w.project_checks.values(): QTest.mouseClick(check,Qt.LeftButton)
        assert choices=={fmt:check.isChecked() for fmt,check in w.project_checks.items()}
        w.exports_enabled.setChecked(True)
        assert all(check.isEnabled() for check in w.project_checks.values())
        assert w.export_pitch.isEnabled() and w.include_instrumental.isEnabled()
        assert choices=={fmt:check.isChecked() for fmt,check in w.project_checks.items()}
        assert w.options()['project_formats']==list(choices)
        w.midi_enabled.setChecked(False)
        assert not w.exports_enabled.isEnabled()
        assert not any(check.isEnabled() for check in w.project_checks.values())
        w.midi_enabled.setChecked(True)
        assert w.exports_enabled.isEnabled() and all(check.isEnabled() for check in w.project_checks.values())
        without_projects=w.options();without_projects['project_formats']=[]
        w.workflow.apply_options(without_projects);w.refresh_workflow_summary()
        assert not w.exports_enabled.isChecked() and not w.export_pitch.isEnabled()
        assert not any(check.isEnabled() for check in w.project_checks.values())
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.exports_enabled.isChecked() and all(check.isEnabled() for check in w.project_checks.values())
        w.theme_picker.setCurrentIndex(w.theme_picker.findData('light'))
        assert w.palette().color(w.backgroundRole()).name()=='#f3f6f3'
        w.set_running(True)
        assert not w.step_area.isEnabled() and not w.save_preset_button.isEnabled()
        w.set_running(False)
        assert w.step_area.isEnabled()
        ref=lyrics_sources.LyricReference('你好',[],{'provider':'file','name':'test.lrc'})
        with patch.object(lyrics_sources,'fetch',return_value=ref):
            d=LyricsDialog(w);d.keyword.setText('123');d.submit()
            assert d.worker.wait(1000);app.processEvents()
            assert d.reference.text=='你好' and d.apply_button.isEnabled()
            d.apply();assert d.result()==d.Accepted
        w.close()
print('PASS: processing steps, export options, 32-step precision, theme and lyric search worker')
