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
from common import ROOT, load_config, app_path

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
        self.setWindowTitle('Better Vocaloid Workflow Assistant')
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
        self.component_status.setText(('BS-RoFormer 已安装，可离线使用' if installed else 'BS-RoFormer 尚未安装，需先下载组件') if new_model else '已内置 · 无需额外下载')
        self.component_link.setVisible(new_model and not installed)
        self.start.setEnabled(not active and (not new_model or (installed and not busy)))
        self.roformer_device.setEnabled(new_model and not active)

    def start_new(self):
        if self.processing_active:
            return
        try:
            from pipeline import create_job
            self.job = create_job(self.source.text() if self.input_mode.currentData() == "bilibili" else "",
                                  local_music=self.local_music.text() if self.input_mode.currentData() == "local" else None,
                                  **self.options())
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
        return {"lyrics": self.lyrics.toPlainText(), "language": self.language.currentData(),
                "recognize_lyrics": self.recognize.isChecked(),
                "voice_mode": self.separator_model.currentData() if self.voice_mode.currentData() == 'dual' else self.voice_mode.currentData(),
                "roformer_device": self.roformer_device.currentData(),
                "imported_stems": {key: field.text() for key, field in self.stem_fields.items()},
                "midi_steps": 16 if self.precision.isChecked() else 8,
                "backing_lyrics": self.backing_lyrics.text()}

    def reprocess_last(self):
        try:
            from pipeline import reuse_download
            previous = self.results.selected_job or app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            self.job = reuse_download(previous, **self.options())
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, "无法重做", str(exc))

    def resume_last(self):
        try:
            self.job = self.results.selected_job or app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            if not (self.job / "request.json").exists():
                raise FileNotFoundError("没有找到上次任务。请先新建一个任务。")
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, "无法继续", str(exc))

    def launch(self):
        self.log.clear()
        request = json.loads((self.job / "request.json").read_text(encoding="utf-8"))
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
        self.precision.setChecked(request.get("midi_steps", 8) >= 16)
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
            status = json.loads((self.job / "status.json").read_text(encoding="utf-8"))
            done = sum(status.get("steps", {}).get(key, {}).get("state") == "done"
                       for key in ("metadata", "download", "audio", "separation"))
            if self.progress.maximum() == 5:
                done += status.get("steps", {}).get("midi", {}).get("state") == "done"
            else:
                done += sum((self.job / "midi" / voice / "report.json").exists() for voice in ("lead", "backing"))
            self.progress.setValue(done)
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
                        self.recognize, self.voice_mode, self.separator_model, self.precision, self.import_panel):
            control.setEnabled(not active)
        self.lyrics.setEnabled(not active and self.recognize.isChecked())
        self.backing_lyrics.setEnabled(not active and self.recognize.isChecked())
        self.stop.setEnabled(active)
        self.stop.setVisible(active)
        self.update_component_state()

    def on_result(self, code):
        self.timer.stop()
        self.refresh_progress()
        self.set_running(False)
        if code == 0:
            self.progress.setValue(self.progress.maximum())
            filename = "voices.mid（主唱 / 和声双轨）" if (self.job / "midi/voices.mid").exists() else "vocals.mid"
            self.status.setText("处理完成 · " + filename + " 和伴奏已准备好。")
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
        self.results.reload()

    def cancel(self):
        if self.job:
            (self.job / "cancel.flag").touch()
            self.status.setText("已请求停止，正在结束当前计算；已完成的输出会保留。")
            self.stop.setEnabled(False)

    def open_folder(self):
        destination = self.results.selected_job or self.job or ROOT / "jobs"
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(str(destination))

    def closeEvent(self, event):
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
