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
        self.job = None
        self.setWindowTitle("Better Vocaloid Workflow Assistant")
        self.resize(920, 940)
        self.stem_fields = {}
        self.setStyleSheet("""
            QWidget { font-family: 'Microsoft YaHei'; font-size: 14px; color: #233242; background: #f5f7fa; }
            QLineEdit, QTextEdit, QComboBox { background: white; border: 1px solid #cad4de; border-radius: 6px; padding: 8px; }
            QPushButton { background: #e5ebf2; border: 0; border-radius: 6px; padding: 11px 17px; }
            QPushButton#start { background: #2469ad; color: white; font-weight: 600; }
            QPushButton:disabled { background: #e4e7ec; color: #8b96a1; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        processing = QWidget()
        self.tabs.addTab(processing, "下载 / 分离 / MIDI")
        layout = QVBoxLayout(processing)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(13)
        title = QLabel("从 BV 号或本地音乐到可调音的 MIDI")
        title.setStyleSheet("font-size: 26px; font-weight: 600;")
        layout.addWidget(title)
        subtitle = QLabel("下载原视频 / 导入音乐  →  分离主唱、和声与伴奏  →  生成双轨 MIDI")
        subtitle.setStyleSheet("color: #607184; background: transparent;")
        layout.addWidget(subtitle)
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("音乐来源"))
        self.input_mode = QComboBox()
        self.input_mode.addItem("B站 BV 号 / 链接", "bilibili")
        self.input_mode.addItem("导入本地音乐", "local")
        input_row.addWidget(self.input_mode, 1)
        layout.addLayout(input_row)
        self.bv_panel = QWidget()
        bv_layout = QVBoxLayout(self.bv_panel)
        bv_layout.setContentsMargins(0, 0, 0, 0)
        bv_layout.addWidget(QLabel("BV 号 / B站视频链接"))
        self.source = QLineEdit(load_config().get("default_source", ""))
        self.source.setPlaceholderText("BV1… 或 https://www.bilibili.com/video/BV1…/")
        bv_layout.addWidget(self.source)
        layout.addWidget(self.bv_panel)
        self.music_panel = QWidget()
        music_layout = QHBoxLayout(self.music_panel)
        music_layout.setContentsMargins(0, 0, 0, 0)
        self.local_music = QLineEdit()
        self.local_music.setPlaceholderText("选择或粘贴本地音乐路径，直接分离并转 MIDI")
        music_layout.addWidget(self.local_music, 1)
        self.choose_music_btn = QPushButton("选择音乐…")
        self.choose_music_btn.clicked.connect(self.choose_music)
        music_layout.addWidget(self.choose_music_btn)
        layout.addWidget(self.music_panel)
        self.input_mode.currentIndexChanged.connect(self.update_source_panel)
        self.update_source_panel()
        row = QHBoxLayout()
        row.addWidget(QLabel("歌词语言"))
        self.language = QComboBox()
        self.language.addItem("中文", "zh")
        self.language.addItem("日语", "ja")
        row.addWidget(self.language)
        self.recognize = QCheckBox("识别歌词并写入 MIDI")
        self.recognize.setChecked(True)
        row.addWidget(self.recognize)
        row.addStretch()
        layout.addLayout(row)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("分离方式"))
        self.voice_mode = QComboBox()
        self.voice_mode.addItem("模型全自动：人声 → 主唱 / 和声", "dual")
        self.voice_mode.addItem("软件分离：导入 SpectraLayers 主唱、和声、伴奏", "import")
        self.voice_mode.addItem("原版：合并人声单轨", "single")
        mode_row.addWidget(self.voice_mode, 1)
        self.precision = QCheckBox("精细提取（较慢）")
        self.precision.setToolTip("使用 16 步 GAME 推理；关闭时用 8 步。识别准确率仍取决于音频与模型。")
        self.precision.setChecked(True)
        mode_row.addWidget(self.precision)
        layout.addLayout(mode_row)
        self.import_panel = QWidget()
        import_layout = QVBoxLayout(self.import_panel)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.addWidget(QLabel("在 SpectraLayers 用高质量“音频分离歌曲”后，对人声使用“音频分离合唱”。\n从项目开始导出完整图层，保留开头静音，再选择下面三个文件。"))
        for key, label in (("lead", "主唱"), ("backing", "和声"), ("instrumental", "伴奏")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            field = QLineEdit()
            field.setPlaceholderText("选择完整 WAV 文件")
            self.stem_fields[key] = field
            row.addWidget(field, 1)
            browse = QPushButton("选择…")
            browse.clicked.connect(lambda checked=False, k=key: self.choose_stem(k))
            row.addWidget(browse)
            import_layout.addLayout(row)
        layout.addWidget(self.import_panel)
        self.voice_mode.currentIndexChanged.connect(self.update_import_panel)
        self.update_import_panel()
        layout.addWidget(QLabel("参考歌词（可选，只粘贴歌词；空格、标点和换行会自动去除）"))
        self.lyrics = QTextEdit()
        self.lyrics.setPlaceholderText("推荐填写参考歌词，减少分离伪影导致的错字。留空则本地识别。不要粘贴作者、STAFF 或完整简介。")
        self.lyrics.setMaximumHeight(112)
        layout.addWidget(self.lyrics)
        self.backing_lyrics = QLineEdit()
        self.backing_lyrics.setPlaceholderText("和声参考歌词（可选；留空时独立识别，不套用主唱歌词）")
        layout.addWidget(self.backing_lyrics)
        buttons = QHBoxLayout()
        self.start = QPushButton("开始处理")
        self.start.setObjectName("start")
        self.start.clicked.connect(self.start_new)
        self.resume = QPushButton("继续上次任务")
        self.resume.clicked.connect(self.resume_last)
        self.reprocess = QPushButton("重做上次分离 / MIDI")
        self.reprocess.clicked.connect(self.reprocess_last)
        self.stop = QPushButton("取消")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        self.folder = QPushButton("打开结果目录")
        self.folder.clicked.connect(self.open_folder)
        buttons.addWidget(self.start)
        buttons.addWidget(self.resume)
        buttons.addWidget(self.reprocess)
        buttons.addWidget(self.stop)
        buttons.addStretch()
        buttons.addWidget(self.folder)
        layout.addLayout(buttons)
        self.status = QLabel("准备就绪 · 输出自动保存至本程序的 jobs 文件夹")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 6)
        self.progress.setValue(0)
        self.progress.setFormat("已完成 %v / %m 个处理阶段")
        layout.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(1800)
        self.log.setStyleSheet("font-family: 'Microsoft YaHei'; font-size: 12px;")
        layout.addWidget(self.log, 1)
        foot = QLabel("模型全自动与软件分离都可选。调音、剪辑完成后，在“发布准备 / 上传”页选择成品，最后人工确认发布。")
        foot.setWordWrap(True)
        foot.setStyleSheet("color: #607184;")
        layout.addWidget(foot)
        self.recognize.toggled.connect(self.lyrics.setEnabled)
        self.recognize.toggled.connect(self.backing_lyrics.setEnabled)
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.refresh_progress)
        from publishing_ui import PublishingTab
        self.publishing = PublishingTab(self)
        self.tabs.addTab(self.publishing, "发布准备 / 上传")

    def start_new(self):
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

    def options(self):
        return {"lyrics": self.lyrics.toPlainText(), "language": self.language.currentData(),
                "recognize_lyrics": self.recognize.isChecked(), "voice_mode": self.voice_mode.currentData(),
                "imported_stems": {key: field.text() for key, field in self.stem_fields.items()},
                "midi_steps": 16 if self.precision.isChecked() else 8,
                "backing_lyrics": self.backing_lyrics.text()}

    def reprocess_last(self):
        try:
            from pipeline import reuse_download
            previous = app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
            self.job = reuse_download(previous, **self.options())
            self.launch()
        except Exception as exc:
            QMessageBox.warning(self, "无法重做", str(exc))

    def resume_last(self):
        try:
            self.job = app_path((ROOT / "last_job.txt").read_text(encoding="utf-8").strip())
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
        self.voice_mode.setCurrentIndex(max(0, self.voice_mode.findData(request.get("voice_mode", "single"))))
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
        self.status.setText("正在处理 · " + str(self.job))
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
        for control in (self.start, self.resume, self.reprocess, self.source, self.input_mode, self.music_panel, self.language,
                        self.recognize, self.voice_mode, self.precision, self.import_panel):
            control.setEnabled(not active)
        self.lyrics.setEnabled(not active and self.recognize.isChecked())
        self.backing_lyrics.setEnabled(not active and self.recognize.isChecked())
        self.stop.setEnabled(active)

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
        else:
            try:
                status = json.loads((self.job / "status.json").read_text(encoding="utf-8"))
                self.status.setText(status.get("error", "处理未完成，查看日志后可继续上次任务。"))
            except Exception:
                self.status.setText("处理未完成，查看日志后可继续上次任务。")

    def cancel(self):
        if self.job:
            (self.job / "cancel.flag").touch()
            self.status.setText("已请求停止，正在结束当前计算；已完成的输出会保留。")
            self.stop.setEnabled(False)

    def open_folder(self):
        destination = self.job or ROOT / "jobs"
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(str(destination))

    def closeEvent(self, event):
        if self.publishing.busy():
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
