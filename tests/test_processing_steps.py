"""Preset reuse, export dependencies, themes and lyric dialog threading."""
import os,sys,tempfile,contextlib,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt5.QtCore import Qt,QPoint,QPointF
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
import common,workflow,lyrics_sources
from lyrics_dialog import LyricsDialog
from app import Window
app=QApplication([])
def wheel(target):
    event=QWheelEvent(QPointF(5,5),QPointF(target.mapToGlobal(QPoint(5,5))),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    QApplication.sendEvent(target,event);app.processEvents()
with tempfile.TemporaryDirectory(dir=ROOT/'work') as tmp:
    private=Path(tmp); common.atomic_json(private/'config.json',common.load_config(False))
    def private_workspace():
        path=private/'cache/workspace.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    # State reads and writes use the same private directory; component manifests
    # continue to resolve against the isolated application's installed runtime.
    with patch.object(workflow,'ROOT',private), patch.object(workflow,'workspace_state',side_effect=private_workspace):
        w=Window();w.show();app.processEvents()
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.options()['midi_backend']=='v2' and w.options()['project_formats']==['ustx','vsqx','svp']
        assert w.expression_density.isEnabled() and w.options()['expression_density']=='original'
        assert w.options()['prepare_only'] and w.options()['reference_calibration'] is None
        w.performance_style.setCurrentIndex(w.performance_style.findData('soft_ballad'))
        w.style_strength.setCurrentIndex(w.style_strength.findData(.3))
        w.style_pitch.setChecked(True)
        w.reference_tension.setChecked(True)
        saved_tuning=w.tuning.preferences()
        for control in (w.performance_style,w.style_strength,w.style_scope,w.language):
            before=control.currentData();control.setFocus();wheel(control)
            assert control.currentData()==before,'Focused option changed via wheel'
        w.exports_enabled.setChecked(False)
        assert w.expression_density.isEnabled() and w.performance_style.isEnabled()
        assert w.style_strength.isEnabled() and w.style_pitch.isEnabled()
        assert w.workflow.preferences()['performance_style']=='soft_ballad'
        w.exports_enabled.setChecked(True)
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.tuning.preferences()==saved_tuning,'Preparation preset changed independent tuning'
        assert w.options()['performance_style']=='none' and not w.options()['style_pitch']
        assert not w.options()['reference_tension']
        w.project_checks['svp'].setChecked(False)
        assert w.expression_density.isEnabled() and w.performance_style.isEnabled()
        w.project_checks['svp'].setChecked(True)
        w.workflow._persist_test=True;w.workflow.persist(dirty=False);w.close()
        w=Window();w.show();app.processEvents()
        assert w.performance_style.currentData()=='soft_ballad' and w.style_strength.currentData()==.3
        assert w.style_pitch.isChecked() and not w.options()['style_pitch']
        assert w.options()['bpm'] is None and not w.bpm.isEnabled()
        w.manual_bpm.setChecked(True);w.bpm.setValue(137.5)
        assert w.options()['bpm']==137.5 and w.bpm.isEnabled()
        w.bpm.setFocus();wheel(w.bpm);wheel(w.bpm.lineEdit())
        assert w.options()['bpm']==137.5,'Manual BPM changed via wheel'
        tempo_preset=w.workflow.options()
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.options()['bpm'] is None and not w.bpm.isEnabled()
        w.workflow.apply_options(tempo_preset);w.refresh_workflow_summary()
        assert w.options()['bpm']==137.5
        w.exports_enabled.setChecked(False)
        assert w.bpm.isEnabled() and w.options()['bpm']==137.5
        w.midi_enabled.setChecked(False)
        assert not w.manual_bpm.isEnabled() and not w.bpm.isEnabled()
        assert w.expression_density.isEnabled() and w.performance_style.isEnabled()
        w.midi_enabled.setChecked(True);w.exports_enabled.setChecked(True)
        assert w.manual_bpm.isEnabled() and w.bpm.isEnabled()
        w.step_cards['midi'].toggle.setChecked(True);app.processEvents()
        assert w.step_cards['midi'].grab().save(str(ROOT/'work/test-results/manual-bpm-ui.png'))
        w.workflow.choose_preset('内置 · 中文双轨')
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
        without_projects=w.workflow.preferences();without_projects['project_export_enabled']=False
        w.workflow.apply_options(without_projects);w.refresh_workflow_summary()
        assert not w.exports_enabled.isChecked() and not w.export_pitch.isEnabled()
        assert not any(check.isEnabled() for check in w.project_checks.values())
        w.workflow.choose_preset('内置 · 中文双轨')
        assert w.exports_enabled.isChecked() and all(check.isEnabled() for check in w.project_checks.values())
        w.theme_picker.setCurrentIndex(w.theme_picker.findData('light'))
        from desktop_theme import COLORS
        assert w.palette().color(w.backgroundRole()).name()==COLORS['light']['workspace']
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
