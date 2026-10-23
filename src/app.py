"""Native Windows front end. Processing runs in an isolated child process."""

from common import environment_value
from i18n import tr as _tr
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                            QLineEdit, QTextEdit, QPushButton, QComboBox, QCheckBox, QMessageBox, QFileDialog, QProgressBar, QTabWidget)
from common import ROOT, load_config, app_path, output_directory, save_output_directory
from ui_input import install_input_wheel_guard

sys.dont_write_bytecode = True


class ReferenceSelectionError(ValueError):
    """A recoverable input error that belongs to the reference-lyrics card."""


class Runner(QThread):
    line = pyqtSignal(str)
    result = pyqtSignal(int)

    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self):
        try:
            python = load_config()["python"]
            env = os.environ.copy()
            env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            with (self.job / "pipeline.log").open("a", encoding="utf-8") as log:
                process = subprocess.Popen([python, "-B", "-u", str(ROOT / "pipeline.py"), "--job", str(self.job)],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           encoding="utf-8", errors="replace", env=env,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    self.line.emit(line.rstrip())
                self.result.emit(process.wait())
        except Exception:
            self.line.emit(traceback.format_exc())
            self.result.emit(1)


class Window(QWidget):
    @property
    def processing_active(self):
        tuning=getattr(self,'tuning',None)
        return getattr(self,'_processing_active',False) or bool(tuning and tuning.busy())

    @processing_active.setter
    def processing_active(self,value):self._processing_active=bool(value)

    def __init__(self):
        super().__init__()
        from i18n import set_language
        from ui_preferences import load_appearance
        set_language(load_appearance()['ui_language'])
        application = QApplication.instance()
        if isinstance(application, QApplication):
            install_input_wheel_guard(application)
        self.runner = None
        self.processing_active = False
        self.job = None
        self.stem_fields = {}
        self.reference_metadata = {'local':None, 'netease':None}
        self.setObjectName('workspace')
        self.setWindowTitle(_tr('术力口工作流助手 · VWA'))
        from workspace_ui import build_window
        build_window(self)
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.refresh_progress)

    def navigate(self, index):
        if index == 5: index = 0
        if index == 6: index = 4
        if index == 7:
            index=0
            self.focus_step('subtitles')
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if index == 2 and hasattr(self, 'results'):
            self.results.reload()
        elif index == 1 and hasattr(self, 'components'):
            self.components.refresh()
        elif index == 9:
            self.tuning.reload_jobs()

    def focus_step(self, name):
        card = self.step_cards[name]
        card.toggle.setChecked(True)
        if name=='subtitles' and hasattr(self,'subtitle_dialog'):
            self.subtitle_dialog.show()
            self.subtitle_dialog.raise_()
            return
        if name=='reference' and hasattr(self,'lyric_tab_buttons'):
            self.lyric_tab_buttons['lead'].click()
        QTimer.singleShot(0, lambda: self.step_area.scroll_to(card))

    def update_component_state(self):
        from optional_components import status
        new_model = self.voice_mode.currentData() == 'dual' and self.separator_model.currentData() == 'roformer'
        installed = status()['ready']
        busy = bool(getattr(self, 'components', None) and self.components.busy())
        active = self.processing_active
        connected = True
        if hasattr(self, 'workflow'):
            from workflow import validate_graph
            try:
                validate_graph(self.workflow.graph)
            except ValueError:
                connected = False
        self.component_status.setText((_tr('BS-RoFormer 已安装，可离线使用') if installed else _tr('BS-RoFormer 尚未安装，需先下载组件')) if new_model else _tr('已内置 · 无需额外下载'))
        self.component_link.setVisible(new_model and not installed)
        self.start.setEnabled(not active and connected and (not new_model or (installed and not busy)))
        self.start.setToolTip('' if connected else _tr('请检查处理步骤和模型设置。'))
        self.roformer_device.setEnabled(new_model and not active)
        self.separation_flow.setEnabled(new_model and not active)

    def start_new(self):
        if self.processing_active or self.storage.busy() or self.tuning.busy():
            return
        try:
            from pipeline import create_job
            self.job = create_job(self.source.text() if self.input_mode.currentData() == "bilibili" else "",
                                  local_music=self.local_music.text() if self.input_mode.currentData() == "local" else None,
                                  **self.options())
            save_output_directory(self.job.parent)
            self.launch()
        except ReferenceSelectionError as exc:
            self.focus_step('reference')
            self.reference_status.setText(str(exc))
            self.status.setText(str(exc))
        except Exception as exc:
            QMessageBox.warning(self, _tr('输入或配置错误'), _tr(str(exc)))

    def choose_stem(self, key):
        path, _ = QFileDialog.getOpenFileName(self, _tr('选择音频图层'), str(ROOT), _tr('WAV 音频 (*.wav)'))
        if path:
            self.stem_fields[key].setText(path)

    def choose_music(self):
        path, _ = QFileDialog.getOpenFileName(self, _tr('选择本地音乐'), str(ROOT),
                    _tr('音频文件 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma *.aif *.aiff *.ape *.wv);;所有文件 (*)'))
        if path:
            self.local_music.setText(path)

    def output_parent(self):
        return output_directory(self.output_dir.text())

    def choose_output_directory(self):
        path = QFileDialog.getExistingDirectory(self, _tr('选择保存目录'), str(self.output_parent()))
        if path:
            self.output_dir.setText(path)
            save_output_directory(path)

    def update_source_panel(self):
        local = self.input_mode.currentData() == "local"
        self.bv_panel.setVisible(not local)
        self.music_panel.setVisible(local)

    def update_import_panel(self):
        self.import_panel.setVisible(self.voice_mode.currentData() == "import")
        self.automatic_panel.setVisible(self.voice_mode.currentData() == 'dual')
        self.output_summary.setText(_tr('单轨 MIDI · 人声 WAV · 伴奏 WAV') if self.voice_mode.currentData() == 'single' else _tr('双轨 MIDI · 主唱 WAV · 和声 WAV · 伴奏 WAV'))
        self.update_component_state()

    def options(self):
        import copy
        import workflow
        graph = copy.deepcopy(self.workflow.graph)
        workflow.validate_graph(graph)
        values = self.workflow.options()
        mode=self.reference_mode.currentData()
        editor=self.netease_lyrics if mode=='netease' else self.lyrics
        reference=self.reference_metadata[mode]
        if mode=='netease' and self.midi_enabled.isChecked() and self.recognize.isChecked():
            if not reference or not editor.toPlainText().strip():
                raise ReferenceSelectionError('请搜索并选择网易云歌曲，或切换为本地匹配后开始处理。')
        return {**values, "lyrics": editor.toPlainText(), "parent": self.output_parent(),
                'export_preferences': workflow.export_preferences(self.workflow.preferences()),
                "imported_stems": {key: field.text() for key, field in self.stem_fields.items()},
                "workflow_graph": graph, "backing_lyrics": self.backing_lyrics.text(),
                'reference_calibration':None,
                "lyric_reference": reference}

    def update_reference_options(self):
        netease=self.reference_mode.currentData()=='netease'
        self.netease_reference_panel.setVisible(netease)
        self.local_reference_panel.setVisible(not netease)

    def import_reference(self,voice):
        path,_=QFileDialog.getOpenFileName(self,_tr('导入主唱歌词') if voice == 'lead' else _tr('导入和声歌词'),
                                         str(self.output_parent()),_tr('歌词文件 (*.lrc *.yrc *.txt)'))
        if path:
            try:
                from lyrics_sources import from_file
                reference=from_file(path)
                if voice=='lead':
                    self.reference_mode.setCurrentIndex(self.reference_mode.findData('local'))
                    self.lyrics.setPlainText(reference.text)
                    self.reference_metadata['local']={'origin':reference.origin}
                    self.local_reference_status.setText(_tr('主唱歌词已载入 · ') + Path(path).name)
                else:
                    self.backing_lyrics.setText(reference.text)
                    self.local_reference_status.setText(_tr('和声歌词已载入 · ') + Path(path).name)
                self.step_cards['reference'].toggle.setChecked(True)
                tabs=getattr(self,'lyric_tab_buttons',{})
                if voice in tabs:tabs[voice].click()
            except Exception as exc:
                QMessageBox.warning(self,_tr('无法导入参考歌词'),_tr(str(exc)))

    def load_reference(self, subtitles=False):
        from lyrics_dialog import LyricsDialog
        dialog = LyricsDialog(self,purpose='subtitles' if subtitles else 'reference')
        if dialog.exec_() == dialog.Accepted:
            if subtitles: self.subtitles.load_reference(dialog.reference)
            else:
                self.reference_mode.setCurrentIndex(self.reference_mode.findData('netease'))
                self.netease_lyrics.setPlainText(dialog.reference.text)
                self.reference_metadata['netease'] = dialog.serialized()
                self.reference_status.setText(_tr('主唱歌词已载入 · 网易云歌曲 ') + str(dialog.reference.origin.get('song_id', '')))
                self.step_cards['reference'].toggle.setChecked(True)

    def change_theme(self):
        from desktop_theme import apply_theme
        mode = self.theme_picker.currentData()
        apply_theme(self, mode)
        if environment_value('VWA_TESTING') != '1':
            from common import atomic_json
            config = load_config(resolve_paths=False)
            config['theme'] = mode
            atomic_json(ROOT / 'config.json', config)

    def reprocess_last(self):
        if self.processing_active or self.storage.busy():
            return
        try:
            from pipeline import reuse_download
            previous = self.results.selected_job or app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            self.job = reuse_download(previous, **self.options())
            save_output_directory(self.job.parent)
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法重做'), _tr(str(exc)))

    def resume_last(self):
        if self.processing_active or self.storage.busy():
            return
        try:
            self.job = self.results.selected_job or app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            if not (self.job / "request.json").exists():
                raise FileNotFoundError("没有找到上次任务。请先新建一个任务。")
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法继续'), _tr(str(exc)))

    def launch(self):
        self.log.clear()
        self.error_panel.hide()
        self.workflow.loading = True
        request = json.loads((self.job / "request.json").read_text(encoding="utf-8"))
        self.output_dir.setText('jobs' if self.job.parent == ROOT / 'jobs' else str(self.job.parent))
        local = request.get("source_kind") == "local"
        self.input_mode.setCurrentIndex(self.input_mode.findData("local" if local else "bilibili"))
        if local:
            self.local_music.setText(str(app_path(request["source_audio"])))
        else:
            self.source.setText(request["bv"])
        requested_mode = request.get('voice_mode', 'single')
        self.voice_mode.setCurrentIndex(max(0, self.voice_mode.findData('dual' if requested_mode == 'roformer' else requested_mode)))
        self.separator_model.setCurrentIndex(self.separator_model.findData('roformer' if requested_mode == 'roformer' else 'dual'))
        self.roformer_device.setCurrentIndex(self.roformer_device.findData(request.get('roformer_device', 'auto')))
        self.separation_flow.setCurrentIndex(self.separation_flow.findData(request.get('separation_flow','cascade')))
        # The request owns preparation/resume behavior.  Calibration controls
        # belong to the independent tuning page, including legacy aliases.
        self.language.setCurrentIndex(max(0, self.language.findData(request["language"])))
        self.recognize.setChecked(request.get("recognize_lyrics", True))
        self.zh_lyric_mode.setCurrentIndex(max(0, self.zh_lyric_mode.findData(request.get('zh_lyric_mode', 'hanzi'))))
        self.precision.setChecked(request.get("midi_steps", 8) >= 16)
        from workflow import default_graph, request_options
        self.workflow.graph = request.get('workflow_graph', default_graph())
        values = request_options(request)
        if 'reference_mode' not in request:
            provider=((request.get('lyric_reference') or {}).get('origin') or {}).get('provider')
            values['reference_mode']='netease' if provider=='netease' else 'local'
        self.workflow.apply_options(values)
        self.workflow.loading = False
        self.workflow.reload_presets(self.workflow.matching_preset())
        self.refresh_workflow_summary()
        self.reference_metadata = {'local':None, 'netease':None}
        mode=values['reference_mode']
        self.reference_metadata[mode]=request.get('lyric_reference')
        self.lyrics.clear()
        self.netease_lyrics.clear()
        (self.netease_lyrics if mode=='netease' else self.lyrics).setPlainText(request.get('lyrics',''))
        self.reference_status.setText(_tr('已载入任务的网易云主唱歌词') if mode == 'netease' else _tr('尚未选择网易云歌曲。'))
        self.local_reference_status.setText(_tr('已载入任务主唱歌词') if mode == 'local' and request.get('lyrics') else _tr('导入歌词或直接填写；留空时从对应人声音频识别歌词。'))
        self.backing_lyrics.setText(request.get("backing_lyrics", ""))
        for key, field in self.stem_fields.items():
            value = request.get("imported_stems", {}).get(key)
            field.setText(str(app_path(value)) if value else "")
        self.progress.setRange(0, 5 if request.get("voice_mode", "single") == "single" else 6)
        self.progress.setValue(0)
        from results_ui import task_caption
        self.status.setText(_tr('正在处理 · ') + task_caption(self, self.job))
        self.status.setToolTip(str(self.job))
        self.set_running(True)
        self.runner = Runner(self.job)
        self.runner.line.connect(self.append_line)
        self.runner.result.connect(self.on_result)
        self.runner.start()
        self.timer.start()

    def refresh_progress(self):
        if not self.job:
            return
        try:
            from progress_state import snapshot, TITLES
            data = snapshot(self.job)
            self.progress.setRange(0, len(data['stages']))
            self.progress.setValue(data['completed'])
            event = data['event']
            detail = _tr(event.get('detail') or TITLES.get(data['current'], '等待处理'))
            terminal = data['status'].get('state')
            if terminal in ('done', 'partial', 'cancelled', 'interrupted'):
                detail = _tr({'done': '处理完成', 'partial': '本轮阶段完成', 'cancelled': '已停止', 'interrupted': '处理已中断，可继续'}[terminal])
            seconds = data['elapsed']
            clock_label = _tr('最后记录' if terminal == 'interrupted' else '已用')
            text = f'已完成 {data["completed"]}/{len(data["stages"])} 阶段 · {detail} · {clock_label} {seconds//60:02}:{seconds%60:02}'
            if event.get('total'):
                text += f' · {event["done"]}/{event["total"]}' if data['current'] != 'download' else f' · {event["done"]/1024**2:.1f}/{event["total"]/1024**2:.1f} MiB'
            if event.get('speed'):
                text += f' · {event["speed"]/1024**2:.1f} MiB/s'
            self.progress_detail.setText(text)
            if self.processing_active:
                from results_ui import task_record
                title = task_record(self,self.job)['title']
                self.status.setText(_tr('正在处理 · ') + title)
            for key, value in self.stage_labels.items():
                value.setVisible(key in data['stages'])
                state = data['status'].get('steps', {}).get(key, {}).get('state')
                prefix, color = ('✓ ', '#8fd6bd') if state == 'done' else ('● ', '#e3c386') if key == data['current'] else ('○ ', '#8aa0af')
                if key == data['current'] and terminal == 'failed':
                    prefix, color = '× ', '#ed9b91'
                value.setText(prefix+_tr(TITLES[key]))
                value.setStyleSheet('color: '+color+'; font-size: 14px;')
        except (OSError, ValueError):
            pass

    def append_line(self, text):
        if not hasattr(self, '_log_buffer'):
            from workflow_widgets import BufferedLog
            self._log_buffer = BufferedLog(self.log)
        self._log_buffer.append(text)

    def set_running(self, active):
        self.processing_active = active
        self.progress.show()
        self.progress_detail.show()
        self.stage_strip.setVisible(active or self.job is not None)
        for control in (self.start, self.resume, self.reprocess, self.source, self.input_mode, self.music_panel, self.language,
                        self.recognize, self.voice_mode, self.separator_model, self.precision, self.import_panel, self.output_panel,
                        self.workflow_preset):
            control.setEnabled(not active)
        self.lyrics.setEnabled(not active and self.recognize.isChecked())
        self.netease_lyrics.setEnabled(not active and self.recognize.isChecked())
        self.backing_lyrics.setEnabled(not active and self.recognize.isChecked())
        self.update_lyric_options()
        self.stop.setEnabled(active)
        self.stop.setVisible(active)
        self.update_component_state()
        self.step_area.setEnabled(not active)
        self.subtitles.setEnabled(not active)
        self.device_button.setEnabled(not active)
        self.device_panel.setEnabled(not active)
        self.save_preset_button.setEnabled(not active)
        self.delete_preset_button.setEnabled(not active)
        self.workflow.setEnabled(not active)
        self.results.select()

    def update_lyric_options(self):
        chinese = self.language.currentData() == 'zh'
        self.zh_lyric_panel.setVisible(chinese)
        self.zh_lyric_mode.setEnabled(chinese and self.recognize.isChecked() and not self.processing_active)
        editable=self.recognize.isChecked() and not self.processing_active
        for field in (self.lyrics,self.netease_lyrics,self.backing_lyrics): field.setEnabled(editable)

    def on_result(self, code):
        self.timer.stop()
        self.refresh_progress()
        self.set_running(False)
        if code == 0:
            self.progress.setValue(self.progress.maximum())
            filename = "voices.mid（主唱 / 和声双轨）" if (self.job / "midi/voices.mid").exists() else "vocals.mid"
            self.status.setText(_tr('处理完成 · ') + filename + _tr(' 和伴奏已准备好。'))
            from workflow import validate_graph
            request = json.loads((self.job/'request.json').read_text(encoding='utf-8'))
            if request.get('workflow_graph') and validate_graph(request['workflow_graph']) == 'separation':
                self.status.setText(_tr('分离完成 · 音频已保存到任务目录。'))
                self.results.reload()
                return
            if (self.job/'subtitles/report.json').exists():
                self.append_line('字幕已导出到 subtitles；分句由歌词停顿推断，使用前请核对。')
            try:
                report = json.loads((self.job / "midi/report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {}
                self.append_line("未能读取 MIDI 报告，请检查任务目录中的日志。")
            if report.get("voices", {}).get("backing", {}).get("notes") == 0:
                self.append_line("提示：本次未检出和声音符，双轨 MIDI 的和声轨为空，请试听和声 WAV 后核对。")
            voice_reports = report.get("voices") or {"vocals": report}
            partial = sum((details.get("alignment") or {}).get("pitch_only_chunks", 0)
                          for details in voice_reports.values()
                          if (details.get("alignment") or {}).get("requested_lyrics"))
            if partial:
                self.append_line(f"提示：{partial} 个片段保留了音符但未可靠对齐歌词，详见声部目录的 *_alignment.json。")
            self.append_line("VOCALOID 6：请用“文件 → 导入”导入 MIDI；带歌词的文件请选择 UTF-8 编码。")
        else:
            self.log_toggle.setChecked(True)
            try:
                status = json.loads((self.job / "status.json").read_text(encoding="utf-8"))
                self.status.setText(status.get('error', '处理未完成，查看日志后可继续上次任务。'))
            except Exception:
                self.status.setText(_tr('处理未完成，查看日志后可继续上次任务。'))
            self.show_failure()
        self.results.reload()

    def quick_options_changed(self, *args):
        if hasattr(self, 'workflow') and not self.workflow.loading and not self.processing_active:
            self.workflow.sync_from_owner()
            self.workflow.persist()

    def choose_processing_preset(self, *args):
        if hasattr(self, 'workflow') and not self.processing_active:
            self.workflow.choose_preset(self.workflow_preset.currentData())

    def refresh_workflow_summary(self):
        if not hasattr(self, 'workflow'):
            return
        import workflow
        self.update_step_dependencies()
        self.workflow.sync_preset_picker()
        self.update_component_state()
        target = workflow.validate_graph(self.workflow.graph)
        values = self.workflow.options()
        outputs = [_tr('分离音频')]
        if target != 'separation': outputs.append(_tr('单轨 MIDI' if self.voice_mode.currentData() == 'single' else '双轨 MIDI'))
        outputs.extend(fmt.upper() for fmt in values['project_formats'])
        if target == 'subtitles': outputs.append(_tr('SRT / WebVTT 字幕'))
        self.workflow_summary.setText(self.workflow.preset.currentText() + ' · ' + ' · '.join(outputs))
        self.output_summary.setText(' · '.join(outputs))
        self.step_cards['source'].summary.setText(_tr('导入本地音频') if self.input_mode.currentData() == 'local' else _tr('下载原视频与音频，保留本家资料和封面。'))
        self.step_cards['separation'].summary.setText(self.voice_mode.currentText() + (' · ' + self.separator_model.currentText() if self.voice_mode.currentData() == 'dual' else ''))
        self.step_cards['midi'].summary.setText((_tr('已跳过') if target == 'separation' else self.language.currentText() + ' · ' + self.midi_steps.currentText()))
        self.step_cards['audio'].summary.setText(' · '.join(map(str.upper, values['audio_formats'])))
        for key,value in self.stage_labels.items():
            value.setVisible(key != 'subtitles' or target == 'subtitles')
            if key == 'midi': value.setVisible(target != 'separation')
            if key == 'exports': value.setVisible(bool(values['project_formats']) or any(fmt != 'wav' for fmt in values['audio_formats']))

    def update_step_dependencies(self):
        enabled = self.midi_enabled.isChecked()
        subtitles = enabled and self.recognize.isChecked() and self.subtitles.match_mode.currentData()=='midi'
        self.subtitles_enabled.setEnabled(subtitles)
        if not subtitles:
            self.subtitles_enabled.blockSignals(True)
            self.subtitles_enabled.setChecked(False)
            self.subtitles_enabled.blockSignals(False)
        self.exports_enabled.setEnabled(enabled)
        self.project_options.setEnabled(enabled and self.exports_enabled.isChecked())
        for check in self.audio_checks.values():check.setEnabled(self.audio_enabled.isChecked())
        self.export_pitch.setEnabled(enabled and self.exports_enabled.isChecked())
        self.include_instrumental.setEnabled(enabled and self.exports_enabled.isChecked())
        note_settings=getattr(self,'note_settings',None)
        if note_settings is not None:note_settings.setEnabled(enabled)
        self.alignment_engine.setEnabled(enabled)
        self.manual_bpm.setEnabled(enabled)
        self.bpm.setEnabled(enabled and self.manual_bpm.isChecked())
        self.delete_preset_button.setEnabled(not self.processing_active and self.workflow.preset.currentData() in self.workflow.presets)
        self.update_lyric_options()

    def show_failure(self):
        from error_recovery import failure
        self.recovery = failure(self.job)
        raw = self.recovery['raw']
        self.error_message.setText(self.recovery['title'] + '：' + self.recovery['message'] + _tr('\n详细原因：') + raw[:600])
        self.error_message.setToolTip(raw)
        self.cpu_retry.setVisible(self.recovery['action'] == 'cpu')
        self.recovery_action.setText({'storage': '查看磁盘', 'subtitles': '打开字幕工具', 'components': '检查模型与组件'}.get(self.recovery['action'], '检查处理设置'))
        self.error_panel.show()

    def retry_job(self):
        if self.job and not self.processing_active and not self.storage.busy():
            self.launch()

    def retry_cpu(self):
        if self.job and not self.processing_active and not self.storage.busy():
            from error_recovery import retry_with_cpu
            from task_store import known_job
            try:
                known_job(self.job)
                retry_with_cpu(self.job)
                self.launch()
            except Exception as exc:
                QMessageBox.warning(self, _tr('无法继续任务'), _tr(str(exc)))

    def open_recovery(self):
        self.navigate({'storage': 6, 'subtitles': 7, 'components':1}.get(getattr(self, 'recovery', {}).get('action'), 5))

    def show_job(self, job):
        self.job = job
        for progress_control in (self.progress,self.progress_detail,self.stage_strip):
            progress_control.show()
        self.refresh_progress()
        from results_ui import task_caption
        self.status.setText(_tr('查看任务 · ') + task_caption(self, job));self.status.setToolTip(str(job))
        self.log.clear()
        path = job/'pipeline.log'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size-60000))
                self.append_line(stream.read().decode('utf-8', errors='replace'))
        try:
            from progress_state import snapshot
            state = snapshot(job)['status'].get('state')
        except (OSError, ValueError):
            state = None
        self.error_panel.hide()
        if state in ('failed', 'cancelled', 'interrupted'):
            self.show_failure()
        elif state == 'running':
            self.status.setText(_tr('任务正在运行 · ') + task_caption(self, job) + _tr('。请在启动任务的窗口查看或停止处理。'))

    def cancel(self):
        if self.job:
            (self.job / "cancel.flag").touch()
            self.status.setText(_tr('已请求停止，正在结束当前计算；已完成的输出会保留。'))
            self.stop.setEnabled(False)

    def open_folder(self):
        destination = self.results.selected_job or self.job or self.output_parent()
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(str(destination))

    def closeEvent(self, event):
        if self.video_mixdown.preview_busy():
            event.ignore()
            self.video_mixdown.cancel_preview()
            return
        if self.tuning.busy():
            event.ignore();self.tuning.cancel();return
        calibration=getattr(self,'calibration_dialog',None)
        if calibration and calibration.busy():
            event.ignore();calibration.stop()
            return
        if self.video_mixdown.busy():
            event.ignore()
            self.video_mixdown.cancel()
            return
        if self.storage.busy():
            event.ignore()
            self.storage.summary.setText(_tr('正在扫描或清理缓存，完成后即可关闭窗口。'))
            return
        if hasattr(self, 'updates') and self.updates.busy():
            event.ignore()
            self.updates.cancel_download()
            self.updates.message.setText(_tr('正在取消更新下载，下载停止后即可关闭窗口。'))
            return
        if self.components.busy():
            event.ignore()
            self.components.request_cancel()
        elif self.publishing.busy():
            event.ignore()
            self.publishing.status.setText(_tr('正在准备发布资料，请完成后再关闭窗口。'))
        elif self.runner is not None and self.runner.isRunning():
            event.ignore()
            self.cancel()
            self.status.setText(_tr('已请求停止；处理停止后即可关闭窗口。'))
        else:
            if not self.results.stop_scan():
                event.ignore()
                return
            self.workflow.persist(dirty=False)
            visual_timer=getattr(self.tuning,'_progress_timer',None)
            if visual_timer is not None:visual_timer.stop()
            visual_clock=getattr(self.tuning,'_progress_clock',None)
            if visual_clock is not None:visual_clock.pause()
            event.accept()


def main(smoke=False):
    app = QApplication.instance() or QApplication(sys.argv)
    install_input_wheel_guard(app)
    from process_control import application_lock
    guard = application_lock(ROOT)
    if guard is None:
        print('助手已在运行，请使用已打开的窗口。', flush=True)
        return 2 if smoke else 0
    from PyQt5.QtGui import QFontDatabase, QFont
    font_id = QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0], 10))
    window = Window()
    window.show()
    if smoke:
        app.processEvents()
        if not window.isVisible():
            raise RuntimeError('Application window did not initialize')
        QTimer.singleShot(1000, app.quit)
    print("GUI ready", flush=True)
    try:
        return app.exec_()
    finally:
        guard.unlock()


if __name__ == "__main__":
    sys.exit(main())
