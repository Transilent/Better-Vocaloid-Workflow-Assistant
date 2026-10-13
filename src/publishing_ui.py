"""Publication tab: prepare assets, then hand the creator page to the user."""
import json
import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton,
                            QDoubleSpinBox, QTextEdit, QCheckBox, QFileDialog, QMessageBox, QTabWidget,
                            QScrollArea, QComboBox)
from common import ROOT, atomic_json, load_config, saved_path
from publication import SourceInfoError, read_source_info, source_draft, clean_path, extract_cover, prepare


class Task(QThread):
    finished_value = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.finished_value.emit(self.function())
        except Exception as exc:
            self.failed.emit(str(exc))


def song_from_title(title):
    return re_strip(title).split("丨", 1)[0].split("|", 1)[0].strip().strip("《》")


def re_strip(title):
    import re
    return re.sub(r"^(?:\s*【[^】]+】\s*)+", "", title)


class PublishingTab(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.task = None
        self.bundle = None
        self.bundle_inputs = None
        self.processes = {}
        self.log_streams = {}
        self.fields = {}
        self.cover_stamp=None
        from publishing_layout import build
        build(self)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll_browser)
        self.timer.start()
        settings_path = ROOT / "publish-ui.json"
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError("发布设置格式无效。")
                self.video.setText(str(clean_path(settings["video"])) if settings.get("video") else "")
                if settings.get("job"):
                    self.load_job(settings["job"])
                if settings.get("bundle"):
                    self.restore_bundle(settings["bundle"], settings.get("inputs"))
            except (OSError, ValueError, KeyError) as exc:
                self.status.setText("上次的发布资料未能恢复，请重新选择来源任务或发布包。")
                self.log_message(str(exc))
        self.count_text()
        self.refresh_browser_controls()

    def row(self, layout, label, field, button_text, function):
        from desktop_theme import button as action
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(field, 1)
        row.addWidget(action(button_text,function))
        layout.addLayout(row)

    def refresh_action(self):
        if hasattr(self, 'prepare_button'):
            ready = self.bundle is not None and self.bundle_inputs == self.input_snapshot()
            self.prepare_button.setText('上传所选平台' if ready else '生成发布资料')
            self.refresh_review()

    def refresh_review(self):
        if not hasattr(self,'review'): return
        platforms=['B站' if name=='bilibili' else '小红书' for name in self.content()]
        self.review.setText('成品：'+(Path(self.video.text()).name or '未选择')+'\n封面：'+self.cover_mode.currentText()+
                            '\n平台：'+('、'.join(platforms) or '未选择')+'\n资料：'+
                            ('已生成，可上传' if self.bundle is not None and self.bundle_inputs==self.input_snapshot() else '需要生成发布资料'))

    def select_step(self,index):
        self.workspace.select(index)

    def step_changed(self,index):
        self.workspace.next.setVisible(index<3)
        self.prepare_button.setVisible(index==3)
        if index==1: self.cached_cover_preview()
        if index==3: self.refresh_review()

    def next_step(self):
        index=self.workspace.stack.currentIndex()
        if index not in (0, 1, 2) or self.busy():
            return
        try:
            self.validate_inputs(index)
            self.workspace.select(index+1)
            if self.workspace.stack.currentIndex()==index+1:
                self.status.setText('检查'+self.workspace.titles[index+1]+'，完成后继续下一步。' if index<2 else '资料检查完成，可生成发布资料并上传。')
        except (OSError,ValueError) as exc:
            self.status.setText(str(exc))

    def selected_source(self):
        data = read_source_info(self.job.text())
        source_draft(self.job.text())
        return clean_path(self.job.text()), data

    def validate_inputs(self, through):
        """Recheck prerequisites even when the user navigates by step number."""
        try:
            if not self.video.text().strip() or not clean_path(self.video.text()).is_file():
                self.video.setFocus()
                raise ValueError("请先选择存在的成品视频。")
            source = self.selected_source()
        except (OSError, ValueError):
            self.workspace.select(0)
            raise
        if through >= 1:
            try:
                self.validate_cover_choice(source)
            except (OSError, ValueError):
                self.workspace.select(1)
                raise
        if through >= 2:
            from publication import validate_content
            try:
                if not self.content():
                    raise ValueError("请至少选择一个发布平台。")
                for platform, content in self.content().items():
                    validate_content(content, platform)
            except ValueError:
                self.workspace.select(2)
                raise

    def validate_cover_choice(self, source=None):
        mode=self.cover_mode.currentData()
        if mode=='custom':
            if not self.cover_file.text().strip() or not clean_path(self.cover_file.text()).is_file():
                raise ValueError('请先选择封面图片。')
            if QPixmap(str(clean_path(self.cover_file.text()))).isNull(): raise ValueError('封面图片无法读取，请选择其他图片。')
        else:
            job, data = source if source is not None else self.selected_source()
            if mode=='frame' and not (job/'video/source.mp4').is_file():
                if not data.get('bvid'): raise ValueError('来源任务没有原视频，请选择自选图片或本家封面。')
            if mode=='original':
                if not data.get('bvid') and not (job/'original-cover.json').is_file():
                    raise ValueError('本地任务没有本家封面，请读取本家资料或选择自选图片。')

    def cover_key(self):
        return (self.job.text(),self.cover_mode.currentData(),self.cover_file.text(),self.cover_seconds.value())

    def invalidate_cover_preview(self,*args):
        if not hasattr(self,'cover_preview'): return
        self.cover_stamp=None
        self.cover_preview.clear()
        self.cover_preview.setText('封面设置已改变，点击获取 / 刷新预览。')
        self.cover_detail.setText('预览按原图比例显示。')

    def update_cover_options(self):
        mode=self.cover_mode.currentData()
        self.frame_panel.setVisible(mode=='frame')
        self.cover_seconds.setEnabled(mode=='frame')
        self.custom_cover_panel.setVisible(mode=='custom')
        self.invalidate_cover_preview()
        if hasattr(self,'workspace') and self.workspace.stack.currentIndex()==1: self.cached_cover_preview()

    def cached_cover_preview(self):
        if self.cover_stamp==self.cover_key(): return
        try:
            if self.cover_mode.currentData()=='custom' and self.cover_file.text().strip():
                self.show_cover(clean_path(self.cover_file.text()))
            elif self.cover_mode.currentData()=='original' and self.job.text().strip():
                job=clean_path(self.job.text())
                info=json.loads((job/'original-cover.json').read_text(encoding='utf-8'))
                self.show_cover(job/Path(info['file']).name)
        except (OSError,ValueError,KeyError): pass

    def primary_action(self):
        if self.busy():
            return
        if self.bundle is not None and self.bundle_inputs == self.input_snapshot():
            self.upload_selected()
        else:
            self.prepare_bundle()

    def load_job(self, job):
        data = read_source_info(job)
        path = clean_path(job)
        draft = source_draft(path)
        self.job.setText(str(path))
        self.bv.setText(data.get("bvid") or "")
        self.song.setText(song_from_title(data["title"]))
        self.source_info.setText(f"本地音乐：{data['title']} · 本家、歌姬和 STAFF 请手填" if data.get("kind") == "local" else
                                f"本家：{data.get('bvid') or '未提供'} / @{data.get('owner', {}).get('name', '')} / {data['title']}（约 {data.get('duration', '?')} 秒）")
        self.voice.clear()
        self.credit.clear()
        for fields in self.fields.values():
            fields["title"].setText("【翻唱】" + self.song.text())
            fields["description"].setPlainText(draft)
        self.bundle = None
        self.bundle_inputs = None
        self.cover_file.clear()
        self.cover_mode.setCurrentIndex(0)
        if data.get("kind") == "local":
            self.cover_mode.setCurrentIndex(max(0, self.cover_mode.findData("custom")))
        self.cover_preview.clear()
        self.cover_preview.setText("请选择本地封面图片；本家资料可通过 BV 另行读取。" if data.get("kind") == "local" else "点击获取本家原始封面")
        try:
            cover_data = json.loads((path / "original-cover.json").read_text(encoding="utf-8"))
            self.show_cover(path / Path(cover_data["file"]).name)
        except (OSError, ValueError, KeyError):
            pass

    def choose_job(self):
        path = QFileDialog.getExistingDirectory(self, "选择来源任务", str(self.owner.output_parent()))
        if path:
            self.try_load(path)

    def current_job(self):
        path = self.owner.job
        if path is None:
            try:
                path = (ROOT / "last_job.txt").read_text(encoding="utf-8").strip()
            except OSError:
                path = ""
        self.try_load(path)

    def try_load(self, path):
        try:
            self.load_job(path)
        except Exception as exc:
            QMessageBox.warning(self, "来源资料错误", str(exc))

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择成品视频", "", "视频 (*.mp4 *.mov *.mkv)")
        if path:
            self.video.setText(path)

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择封面图片", str(ROOT), "图片 (*.jpg *.jpeg *.png *.webp)")
        if path:
            self.cover_file.setText(path)
            self.show_cover(path)

    def count_text(self):
        for platform, fields in self.fields.items():
            a, b = (80, 2000) if platform == "bilibili" else (20, 1000)
            fields["counter"].setText(f"标题 {len(fields['title'].text())}/{a} 字 · 简介 {len(fields['description'].toPlainText())}/{b} 字；超长时请编辑，助手不会自动删去 STAFF。")
            role='error' if len(fields['title'].text())>a or len(fields['description'].toPlainText())>b else 'muted'
            fields['counter'].setProperty('role',role)
            fields['counter'].style().unpolish(fields['counter']);fields['counter'].style().polish(fields['counter'])

    def apply_title(self):
        name = self.voice.text().strip()
        for fields in self.fields.values():
            fields["title"].setText(f"【{name}翻唱】{self.song.text().strip()}")

    def append_credit(self):
        lines = []
        if self.voice.text().strip():
            lines.append("本次翻唱歌姬：" + self.voice.text().strip())
        if self.credit.text().strip():
            lines.append("本次调音：" + self.credit.text().strip())
        if lines:
            for fields in self.fields.values():
                text = fields["description"].toPlainText()
                if not all(line in text.splitlines() for line in lines):
                    fields["description"].setPlainText("\n".join(lines) + "\n\n" + text)

    def start_task(self, function, success):
        if self.task is not None and self.task.isRunning():
            return
        if hasattr(self.owner, 'storage') and self.owner.storage.busy():
            self.status.setText('请等待缓存管理操作结束后再准备发布资料。')
            return
        self.set_busy(True)
        self.status.setText("正在处理发布资料…")
        self.task = Task(function)
        self.task.finished_value.connect(success)
        self.task.failed.connect(self.task_error)
        self.task.finished.connect(lambda: self.set_busy(False))
        self.task.start()

    def set_busy(self, active):
        self.form_panel.setEnabled(not active)
        self.workspace.set_locked(active)
        for button in (self.fetch, self.prepare_button, self.cover_button):
            button.setEnabled(not active)
        if not active: self.refresh_browser_controls()

    def task_error(self, text):
        self.status.setText(text)
        self.log_message(text)
        self.log_toggle.setChecked(True)

    def log_message(self, text):
        from PyQt5.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text + "\n")

    def fetch_source(self):
        source = self.bv.text()
        output_parent = self.owner.output_parent()
        def fetch():
            from pipeline import create_job, run
            job = create_job(source, parent=output_parent, remember=False)
            run(job, until="metadata")
            from cover_art import job_cover
            job_cover(job)
            return job
        def ready(path):
            from common import save_output_directory
            save_output_directory(path.parent)
            self.load_job(path)
            self.status.setText("本家简介与原封面已准备。选择视频截帧时会另行下载原视频。")
        self.start_task(fetch, ready)

    def preview_cover(self):
        try:
            self.validate_cover_choice()
        except SourceInfoError as exc:
            self.workspace.select(0)
            self.task_error(str(exc))
            return
        except (OSError, ValueError) as exc:
            self.workspace.select(1)
            self.task_error(str(exc))
            return
        seconds = self.cover_seconds.value()
        mode = self.cover_mode.currentData()
        job = None if mode == "custom" else clean_path(self.job.text())
        destination = ROOT / "cache/publish-preview.jpg"
        def render():
            if mode == "custom":
                image = clean_path(self.cover_file.text()) if self.cover_file.text().strip() else None
                if image is None or not image.is_file():
                    raise ValueError("请先选择封面图片。")
                return image
            if mode == "original":
                from cover_art import job_cover
                return job_cover(job)[0]
            from pipeline import ensure_source_video
            extract_cover(ensure_source_video(job), destination, seconds)
            return destination
        def ready(path):
            self.show_cover(path)
            self.status.setText("已预览手选封面图片。" if mode == "custom" else
                               "已获取本家在 B站展示的原始封面。" if mode == "original" else f"视频画面截图：原视频 {seconds:.2f} 秒。")
        self.start_task(render, ready)

    def show_cover(self, path):
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self.cover_preview.setPixmap(pixmap)
            self.cover_stamp=self.cover_key()
            self.cover_detail.setText(f'原图 {pixmap.width()} × {pixmap.height()} · 完整预览，保留原比例')
        else:
            self.cover_preview.clear();self.cover_preview.setText('封面图片无法读取。')
            self.cover_stamp=None

    def content(self):
        return {platform: {"title": fields["title"].text().strip(), "description": fields["description"].toPlainText()}
                for platform, fields in self.fields.items() if fields["enabled"].isChecked()}

    def prepare_bundle(self):
        try:
            self.validate_inputs(2)
        except (OSError, ValueError) as exc:
            self.task_error(str(exc))
            return
        content = self.content()
        if any("【XX翻唱】" in value["title"] for value in content.values()):
            QMessageBox.warning(self, "请填写歌姬", "请将标题里的 XX 替换为这次使用的歌姬，或直接填写发布标题。")
            return
        job, video = self.job.text(), self.video.text()
        seconds, image = self.cover_seconds.value(), self.cover_file.text().strip() or None
        mode = self.cover_mode.currentData()
        inputs = self.input_snapshot()
        def make():
            if mode == 'frame':
                from pipeline import ensure_source_video
                ensure_source_video(job)
            return prepare(job, video, content, seconds, image, cover_mode=mode)
        def ready(bundle):
            self.bundle = bundle
            self.bundle_inputs = inputs
            self.show_cover(bundle / "cover.jpg")
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.status.setText(f"发布包已生成 · 成品 {manifest['video']['duration']:.2f} 秒 · 可分别开始平台上传。")
            self.log_message(str(bundle))
            self.save_settings()
            self.refresh_action()
        self.start_task(make, ready)

    def input_snapshot(self):
        return {"job": self.job.text(), "video": self.video.text(), "content": self.content(),
                "cover_seconds": self.cover_seconds.value(), "cover_file": self.cover_file.text(),
                "cover_mode": self.cover_mode.currentData()}

    def save_settings(self):
        inputs = self.input_snapshot()
        for key in ("job", "video", "cover_file"):
            if inputs[key]:
                inputs[key] = saved_path(inputs[key])
        atomic_json(ROOT / "publish-ui.json", {"job": inputs["job"], "video": inputs["video"],
                    "bundle": saved_path(self.bundle) if self.bundle else None,
                    "inputs": {**inputs, "song": self.song.text(),
                               "voice": self.voice.text(), "credit": self.credit.text()}})

    def restore_bundle(self, path, inputs=None):
        bundle = clean_path(path)
        data = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        self.load_job(data["source_job"])
        self.video.setText(str(clean_path(data["video"]["path"])))
        for platform, fields in self.fields.items():
            content = data["content"].get(platform)
            fields["enabled"].setChecked(content is not None)
            if content:
                fields["title"].setText(content["title"])
                fields["description"].setPlainText(content["description"])
        self.cover_seconds.setValue(data["cover"].get("seconds") or 0)
        kind = data["cover"].get("kind", "frame" if data["cover"].get("seconds") is not None else "custom")
        self.cover_mode.setCurrentIndex(max(0, self.cover_mode.findData(kind)))
        self.cover_file.setText(str(clean_path(data["cover"]["source"])) if kind == "custom" else "")
        if inputs:
            for key in ("song", "voice", "credit"):
                getattr(self, key).setText(inputs.get(key, ""))
        self.bundle = bundle
        self.bundle_inputs = self.input_snapshot()
        self.show_cover(bundle / "cover.jpg")
        self.status.setText("已载入发布包。若网页任务仍打开，可继续填写；发布由你在网页确认。")
        self.refresh_action()

    def choose_bundle(self):
        path = QFileDialog.getExistingDirectory(self, "选择发布包", str(ROOT / "publish-packages"))
        if path:
            try:
                self.restore_bundle(path)
                self.save_settings()
            except Exception as exc:
                QMessageBox.warning(self, "发布包无法载入", str(exc))

    def browser_location(self, platform):
        process = self.processes.get(platform)
        if process is not None and process.poll() is None:
            return process.vf_location
        if self.bundle is not None:
            try:
                profile = ROOT / "cache/publish-browser" / platform
                active = json.loads((profile / "active.json").read_text(encoding="utf-8"))
                active_bundle = clean_path(active["bundle"])
                control = clean_path(active.get("control_bundle", active["bundle"]))
                if active_bundle != self.bundle:
                    old = json.loads((active_bundle / "manifest.json").read_text(encoding="utf-8"))
                    new = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
                    if old["video"]["sha256"] != new["video"]["sha256"] or clean_path(old["video"]["path"]) != clean_path(new["video"]["path"]):
                        return None
                from pipeline import job_lock
                try:
                    with job_lock(profile):
                        return None
                except RuntimeError:
                    return control
            except (OSError, ValueError, KeyError):
                pass
        return None

    def resume_browser(self, platform):
        if self.bundle is None or self.bundle_inputs != self.input_snapshot():
            QMessageBox.warning(self, "发布资料已变化", "请载入原发布包，或重新生成发布包后开始新的上传。")
            return
        location = self.browser_location(platform)
        if location is None:
            QMessageBox.warning(self, "没有打开的上传任务", "继续填写需要原上传浏览器仍然打开。浏览器已关闭时请重新开始上传。")
            return
        try:
            from publication import load_bundle
            load_bundle(self.bundle, platform)
            state = json.loads((location / (platform + "_上传状态.json")).read_text(encoding="utf-8"))
            if not state.get("checks", {}).get("video_selected"):
                raise ValueError("尚未选择视频，不能继续填写。")
            atomic_json(location / (platform + ".replacement.json"), {"bundle": str(self.bundle)})
            (location / (platform + ".repair")).touch()
            self.status.setText("继续填写当前发布包的标题、简介与封面；视频不会重传。")
        except Exception as exc:
            QMessageBox.warning(self, "无法继续填写", str(exc))

    def launch_browser(self, platform, login_only=False):
        if self.browser_location(platform) is not None:
            QMessageBox.warning(self, "浏览器任务已打开", "请使用已打开的浏览器，或先关闭它再继续。")
            return
        if not login_only and self.bundle is None:
            QMessageBox.warning(self, "还没有发布包", "请先生成发布包。修改成品或草稿后，请重新生成。")
            return
        if not login_only:
            if self.bundle_inputs != self.input_snapshot():
                QMessageBox.warning(self, "发布资料已变化", "视频、来源、封面或草稿与发布包不同，请重新生成发布包。")
                return
        argv = [load_config()["python"], "-B", "-u", str(ROOT / "publish_browser.py"), "--platform", platform]
        if login_only:
            argv.append("--login")
            location = ROOT / "cache/publish-browser" / platform
        else:
            argv.extend(["--bundle", str(self.bundle)])
            location = self.bundle
        location.mkdir(parents=True, exist_ok=True)
        log = (location / (platform + "_浏览器.log")).open("a", encoding="utf-8")
        self.log_streams[platform] = log
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        self.processes[platform] = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env,
                                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.processes[platform].vf_location = location
        self.status.setText("浏览器已打开。登录、验证码和最终发布由你在网页完成。")
        self.account_status[platform].setText('登录窗口已打开，完成后请关闭。' if login_only else '正在启动上传任务…')
        self.refresh_browser_controls()

    def upload_selected(self):
        platforms = list(self.content())
        if not platforms:
            QMessageBox.warning(self, "没有选择平台", "请勾选需要上传的平台。")
            return
        if self.bundle is None or self.bundle_inputs != self.input_snapshot():
            QMessageBox.warning(self, "请先生成发布包", "请先生成与当前视频、封面和文稿一致的发布包。")
            return
        for platform in platforms:
            self.launch_browser(platform)

    def poll_browser(self):
        messages = []
        for platform, process in list(self.processes.items()):
            path = process.vf_location / (platform + "_上传状态.json")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                message=data.get('message') or data.get('state','正在等待平台响应。')
                self.account_status[platform].setText(message)
                messages.append(('B站' if platform=='bilibili' else '小红书')+'：'+message)
            except (OSError, ValueError):
                pass
            if process.poll() is not None:
                stream=self.log_streams.pop(platform,None)
                if stream: stream.close()
                self.log_message(platform + " 浏览器任务已结束；详情见 " + str(process.vf_location))
                if process.returncode:
                    messages.append(platform + "：启动或上传失败，请查看浏览器日志。")
                    self.account_status[platform].setText('任务已结束，启动或上传失败。请查看发布日志。')
                else:
                    self.account_status[platform].setText('浏览器任务已结束；可重新登录或上传。')
                self.processes.pop(platform, None)
        if messages:
            self.status.setText("\n".join(messages))
        elif self.bundle:
            for platform in self.fields:
                try:
                    if self.browser_location(platform):
                        location = self.browser_location(platform)
                        data = json.loads((location / (platform + "_上传状态.json")).read_text(encoding="utf-8"))
                        message=data.get('message') or data.get('state','正在等待平台响应。')
                        self.account_status[platform].setText(message)
                        messages.append(('B站' if platform=='bilibili' else '小红书')+'：'+message)
                except (OSError, ValueError):
                    pass
            if messages:
                self.status.setText("\n".join(messages))
        self.refresh_browser_controls()

    def refresh_browser_controls(self):
        for platform,actions in self.account_actions.items():
            location=self.browser_location(platform)
            actions['login'].setEnabled(location is None)
            actions['stop'].setEnabled(location is not None)
            can_resume=False
            if location is not None and self.bundle is not None and self.bundle_inputs==self.input_snapshot():
                try:
                    state=json.loads((location/(platform+'_上传状态.json')).read_text(encoding='utf-8'))
                    can_resume=bool(state.get('checks',{}).get('video_selected'))
                except (OSError,ValueError): pass
            actions['resume'].setEnabled(can_resume)

    def stop_browser(self, platform):
        location = self.browser_location(platform)
        if location:
            (location / (platform + ".stop")).touch()
            self.account_status[platform].setText('正在关闭平台任务…')

    def open_bundle(self):
        location = self.bundle or ROOT / "publish-packages"
        location.mkdir(parents=True, exist_ok=True)
        os.startfile(str(location))

    def busy(self):
        return self.task is not None and self.task.isRunning()
