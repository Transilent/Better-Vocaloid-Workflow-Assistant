"""Native Windows front end. Processing runs in an isolated child process."""
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

sys.dont_write_bytecode = True


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
    def __init__(self):
        super().__init__()
        self.runner = None
        self.processing_active = False
        self.job = None
        self.stem_fields = {}
        self.setObjectName('workspace')
        self.setWindowTitle('术力口工作流助手 · BVWA')
        from workspace_ui import build_window
        build_window(self)
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.refresh_progress)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if index == 2 and hasattr(self, 'results'):
            self.results.reload()
        elif index == 1 and hasattr(self, 'components'):
            self.components.refresh()

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
        self.component_status.setText(('BS-RoFormer 已安装，可离线使用' if installed else 'BS-RoFormer 尚未安装，需先下载组件') if new_model else '已内置 · 无需额外下载')
        self.component_link.setVisible(new_model and not installed)
        self.start.setEnabled(not active and connected and (not new_model or (installed and not busy)))
        self.start.setToolTip('' if connected else '请先在工作流页面连接音乐输入和处理节点。')
        self.roformer_device.setEnabled(new_model and not active)

    def start_new(self):
        if self.processing_active or self.storage.busy():
            return
        try:
            from pipeline import create_job
            self.job = create_job(self.source.text() if self.input_mode.currentData() == "bilibili" else "",
                                  local_music=self.local_music.text() if self.input_mode.currentData() == "local" else None,
                                  **self.options())
            save_output_directory(self.job.parent)
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, "输入或配置错误", str(exc))

    def choose_stem(self, key):
        path, _ = QFileDialog.getOpenFileName(self, "选择音频图层", str(ROOT), "WAV 音频 (*.wav)")
        if path:
            self.stem_fields[key].setText(path)

    def choose_music(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择本地音乐", str(ROOT),
                    "音频文件 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma *.aif *.aiff *.ape *.wv);;所有文件 (*)")
        if path:
            self.local_music.setText(path)

    def output_parent(self):
        return output_directory(self.output_dir.text())

    def choose_output_directory(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存目录', str(self.output_parent()))
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
        self.output_summary.setText('单轨 MIDI · 人声 WAV · 伴奏 WAV' if self.voice_mode.currentData() == 'single' else
                                    '双轨 MIDI · 主唱 WAV · 和声 WAV · 伴奏 WAV')
        self.update_component_state()

    def options(self):
        import copy
        import workflow
        graph = copy.deepcopy(self.workflow.graph)
        workflow.validate_graph(graph)
        return {"lyrics": self.lyrics.toPlainText(), "language": self.language.currentData(),
                "recognize_lyrics": self.recognize.isChecked(),
                "zh_lyric_mode": self.zh_lyric_mode.currentData(),
                "parent": self.output_parent(),
                "voice_mode": self.separator_model.currentData() if self.voice_mode.currentData() == 'dual' else self.voice_mode.currentData(),
                "roformer_device": self.roformer_device.currentData(),
                "imported_stems": {key: field.text() for key, field in self.stem_fields.items()},
                'workflow_graph': graph,
                'separation_device': self.workflow.fields['separation_device'].currentData(),
                'midi_device': self.workflow.fields['midi_device'].currentData(),
                "midi_steps": self.workflow.fields['midi_steps'].currentData(),
                "backing_lyrics": self.backing_lyrics.text()}

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
            QMessageBox.warning(self, "无法重做", str(exc))

    def resume_last(self):
        if self.processing_active or self.storage.busy():
            return
        try:
            self.job = self.results.selected_job or app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            if not (self.job / "request.json").exists():
                raise FileNotFoundError("没有找到上次任务。请先新建一个任务。")
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, "无法继续", str(exc))

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
        self.language.setCurrentIndex(max(0, self.language.findData(request["language"])))
        self.recognize.setChecked(request.get("recognize_lyrics", True))
        self.zh_lyric_mode.setCurrentIndex(max(0, self.zh_lyric_mode.findData(request.get('zh_lyric_mode', 'hanzi'))))
        self.precision.setChecked(request.get("midi_steps", 8) >= 16)
        from workflow import default_graph, DEFAULT_OPTIONS
        self.workflow.graph = request.get('workflow_graph', default_graph())
        values = {**DEFAULT_OPTIONS, **{k: request[k] for k in DEFAULT_OPTIONS if k in request},
                  **{k: request['tools'][k] for k in ('separation_device', 'midi_device') if k in request['tools']}}
        self.workflow.apply_options(values)
        self.workflow.draw_graph()
        self.workflow.loading = False
        self.workflow.reload_presets(self.workflow.matching_preset())
        self.workflow.update_message()
        self.refresh_workflow_summary()
        self.lyrics.setPlainText(request.get("lyrics", ""))
        self.backing_lyrics.setText(request.get("backing_lyrics", ""))
        for key, field in self.stem_fields.items():
            value = request.get("imported_stems", {}).get(key)
            field.setText(str(app_path(value)) if value else "")
        self.progress.setRange(0, 5 if request.get("voice_mode", "single") == "single" else 6)
        self.progress.setValue(0)
        self.status.setText("正在处理 · " + self.job.name)
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
            detail = event.get('detail') or TITLES.get(data['current'], '等待处理')
            terminal = data['status'].get('state')
            if terminal in ('done', 'partial', 'cancelled', 'interrupted'):
                detail = {'done': '处理完成', 'partial': '本轮阶段完成', 'cancelled': '已停止', 'interrupted': '处理已中断，可继续'}[terminal]
            seconds = data['elapsed']
            text = f'已完成 {data["completed"]}/{len(data["stages"])} 阶段 · {detail} · 已用 {seconds//60:02}:{seconds%60:02}'
            if event.get('total'):
                text += f' · {event["done"]}/{event["total"]}' if data['current'] != 'download' else f' · {event["done"]/1024**2:.1f}/{event["total"]/1024**2:.1f} MiB'
            if event.get('speed'):
                text += f' · {event["speed"]/1024**2:.1f} MiB/s'
            self.progress_detail.setText(text)
            if self.processing_active:
                from task_store import read_json
                title = read_json(self.job/'task.json').get('name') or read_json(self.job/'source-info.json').get('title') or self.job.name
                self.status.setText('正在处理 · '+title)
            for key, value in self.stage_labels.items():
                value.setVisible(key in data['stages'])
                state = data['status'].get('steps', {}).get(key, {}).get('state')
                prefix, color = ('✓ ', '#8fd6bd') if state == 'done' else ('● ', '#e3c386') if key == data['current'] else ('○ ', '#8aa0af')
                if key == data['current'] and terminal == 'failed':
                    prefix, color = '× ', '#ed9b91'
                value.setText(prefix+TITLES[key])
                value.setStyleSheet('color: '+color+'; font-size: 14px;')
        except (OSError, ValueError):
            pass

    def append_line(self, text):
        # Plain text prevents source descriptions being rendered as HTML.
        self.log.moveCursor(__import__("PyQt5.QtGui", fromlist=["QTextCursor"]).QTextCursor.End)
        self.log.insertPlainText(text + "\n")
        self.log.ensureCursorVisible()

    def set_running(self, active):
        self.processing_active = active
        for control in (self.start, self.resume, self.reprocess, self.source, self.input_mode, self.music_panel, self.language,
                        self.recognize, self.voice_mode, self.separator_model, self.precision, self.import_panel, self.output_panel,
                        self.workflow_preset):
            control.setEnabled(not active)
        self.lyrics.setEnabled(not active and self.recognize.isChecked())
        self.backing_lyrics.setEnabled(not active and self.recognize.isChecked())
        self.update_lyric_options()
        self.stop.setEnabled(active)
        self.stop.setVisible(active)
        self.update_component_state()
        self.workflow.setEnabled(not active)
        self.results.select()

    def update_lyric_options(self):
        chinese = self.language.currentData() == 'zh'
        self.zh_lyric_panel.setVisible(chinese)
        self.zh_lyric_mode.setEnabled(chinese and self.recognize.isChecked() and not self.processing_active)

    def on_result(self, code):
        self.timer.stop()
        self.refresh_progress()
        self.set_running(False)
        if code == 0:
            self.progress.setValue(self.progress.maximum())
            filename = "voices.mid（主唱 / 和声双轨）" if (self.job / "midi/voices.mid").exists() else "vocals.mid"
            self.status.setText("处理完成 · " + filename + " 和伴奏已准备好。")
            from workflow import validate_graph
            request = json.loads((self.job/'request.json').read_text(encoding='utf-8'))
            if request.get('workflow_graph') and validate_graph(request['workflow_graph']) == 'separation':
                self.status.setText('分离完成 · 音频已保存到任务目录。')
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
                self.status.setText(status.get("error", "处理未完成，查看日志后可继续上次任务。"))
            except Exception:
                self.status.setText("处理未完成，查看日志后可继续上次任务。")
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
        self.workflow.sync_preset_picker()
        self.update_component_state()
        try:
            target = workflow.validate_graph(self.workflow.graph)
        except ValueError:
            self.workflow_summary.setText('工作流尚未连接完整，请打开工作流检查。')
            self.output_summary.setText('请完成工作流连接后开始处理')
            return
        name = self.workflow.preset.currentText()
        output = '分离音频' if target == 'separation' else 'MIDI 与音频' if target == 'midi' else 'MIDI、音频与字幕'
        self.workflow_summary.setText(name+' · 输出 '+output)
        if target == 'separation':
            self.output_summary.setText('分离音频 WAV · 保留原始时间线')
        elif target == 'subtitles':
            self.output_summary.setText(('单轨' if self.voice_mode.currentData() == 'single' else '双轨')+' MIDI · 分离音频 · SRT / WebVTT 字幕')
        else:
            self.output_summary.setText('单轨 MIDI · 人声 WAV · 伴奏 WAV' if self.voice_mode.currentData() == 'single' else
                                        '双轨 MIDI · 主唱 WAV · 和声 WAV · 伴奏 WAV')
        for key, value in self.stage_labels.items():
            value.setVisible(key != 'subtitles' or target == 'subtitles')
            if key == 'midi':
                value.setVisible(target != 'separation')

    def show_failure(self):
        from error_recovery import failure
        self.recovery = failure(self.job)
        raw = self.recovery['raw']
        self.error_message.setText(self.recovery['title']+'：'+self.recovery['message']+'\n详细原因：'+raw[:600])
        self.error_message.setToolTip(raw)
        self.cpu_retry.setVisible(self.recovery['action'] == 'cpu')
        self.recovery_action.setText({'storage': '查看磁盘', 'subtitles': '打开字幕工具'}.get(self.recovery['action'], '检查工作流'))
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
                QMessageBox.warning(self, '无法继续任务', str(exc))

    def open_recovery(self):
        self.navigate({'storage': 6, 'subtitles': 7}.get(getattr(self, 'recovery', {}).get('action'), 5))

    def show_job(self, job):
        self.job = job
        self.refresh_progress()
        self.status.setText('查看任务 · '+job.name)
        self.log.clear()
        path = job/'pipeline.log'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size-60000))
                self.append_line(stream.read().decode('utf-8', errors='replace'))
        try:
            state = json.loads((job/'status.json').read_text(encoding='utf-8')).get('state')
        except (OSError, ValueError):
            state = None
        self.error_panel.hide()
        if state in ('failed', 'cancelled', 'running'):
            self.show_failure()

    def cancel(self):
        if self.job:
            (self.job / "cancel.flag").touch()
            self.status.setText("已请求停止，正在结束当前计算；已完成的输出会保留。")
            self.stop.setEnabled(False)

    def open_folder(self):
        destination = self.results.selected_job or self.job or self.output_parent()
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(str(destination))

    def closeEvent(self, event):
        if self.storage.busy():
            event.ignore()
            self.storage.summary.setText('正在扫描或清理缓存，完成后即可关闭窗口。')
            return
        if hasattr(self, 'updates') and self.updates.busy():
            event.ignore()
            self.updates.cancel_download()
            return
        if self.components.busy():
            event.ignore()
            self.components.request_cancel()
        elif self.publishing.busy():
            event.ignore()
            self.publishing.status.setText("正在准备发布资料，请完成后再关闭窗口。")
        elif self.runner is not None and self.runner.isRunning():
            event.ignore()
            self.cancel()
            self.status.setText("已请求停止；处理停止后即可关闭窗口。")
        else:
            self.workflow.persist(dirty=False)
            event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    from PyQt5.QtGui import QFontDatabase, QFont
    font_id = QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0], 10))
    window = Window()
    window.show()
    print("GUI ready", flush=True)
    sys.exit(app.exec_())
