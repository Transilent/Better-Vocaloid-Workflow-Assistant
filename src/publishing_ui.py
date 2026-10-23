"""Publication tab: prepare assets, then hand the creator page to the user."""
from i18n import tr as _tr
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
        self._browser_live = {}
        self.fields = {}
        self.cover_stamp=None
        self._cover_asset_bundle=None
        self._cover_asset_manifest_mtime=None
        self._cover_asset_manifest_data={}
        self._cover_asset_manifest_ready=False
        self._cover_asset_paths={}
        self._cover_asset_item_state={}
        self.upload_preflight_error=None
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
                    raise ValueError("发布设置格式无效；请重新选择成品和来源任务。")
                self.video.setText(str(clean_path(settings["video"])) if settings.get("video") else "")
                if settings.get("job"):
                    self.load_job(settings["job"])
                if settings.get("bundle"):
                    self.restore_bundle(settings["bundle"], settings.get("inputs"))
            except (OSError, ValueError, KeyError) as exc:
                self.status.setText(_tr('上次的发布资料未能恢复，请重新选择来源任务或发布资料。'))
                self.log_message(str(exc))
        self.count_text()
        self.refresh_browser_controls()
        self.refresh_review()

    def row(self, layout, label, field, button_text, function):
        from desktop_theme import button as action, label as caption
        row = QHBoxLayout()
        row.addWidget(caption(label, 'muted'))
        row.addWidget(field, 1)
        row.addWidget(action(button_text,function))
        layout.addLayout(row)

    def bundle_inputs_current(self):
        return self.bundle is not None and self.bundle_inputs == self.input_snapshot()

    def storage_is_busy(self):
        storage = getattr(self.owner, "storage", None)
        storage_busy = getattr(storage, "busy", None)
        return bool(callable(storage_busy) and storage_busy())

    def upload_ready(self):
        if self.busy() or self.storage_is_busy():
            return False
        if not self.bundle_inputs_current() or not self.content() or self.upload_preflight_error:
            return False
        return (Path(self.bundle) / 'manifest.json').is_file()

    def refresh_upload_button(self):
        if not hasattr(self, 'upload_button'):
            return
        enabled = self.upload_ready()
        self.upload_button.setEnabled(enabled)
        if self.busy():
            reason = '正在更新发布资料，请稍候。'
        elif self.storage_is_busy():
            reason = '缓存管理进行中，完成后可打开平台填写。'
        elif self.bundle is None:
            reason = '请先生成发布资料。'
        elif self.bundle_inputs != self.input_snapshot():
            reason = '成品、来源、封面或平台草稿已变化，请重新生成发布资料。'
        elif not self.content():
            reason = '请至少启用一个平台草稿。'
        elif self.upload_preflight_error:
            reason = '上次发布资料检查未通过；请重新生成后再试。'
        elif enabled:
            reason = '打开所选平台网页供你填写；最终发布需你在网页确认。'
        else:
            reason = '发布资料缺少必要文件，请重新生成。'
        self.upload_button.setToolTip(reason)

    def refresh_action(self):
        if hasattr(self, 'prepare_button'):
            self.prepare_button.setEnabled(not self.busy())
        self.refresh_upload_button()
        self.refresh_cover_assets()
        self.refresh_review()

    def set_review_state(self, key, text, role='muted'):
        check = getattr(self, 'review_checks', {}).get(key)
        if check is None:
            return
        if check.text()==str(text) and check.property('role')==role:
            return
        check.setText(str(text))
        check.setProperty('role', role)
        check.style().unpolish(check)
        check.style().polish(check)

    def refresh_review(self):
        if not hasattr(self,'review'): return
        platform_names = {'bilibili': 'B站', 'xiaohongshu': '小红书'}
        selected = self.content()
        platforms = [platform_names[name] for name in selected]
        video_name = Path(self.video.text()).name or '未选择'
        from results_ui import task_caption
        job_name = task_caption(self.owner,self.job.text())
        self.job_display.setText(job_name);self.job_display.setToolTip(self.job.text())
        current = self.bundle_inputs_current()
        if self.upload_preflight_error:
            bundle_text = '发布资料需重新生成'
            bundle_role = 'error'
        elif current:
            bundle_text = '已生成，内容一致'
            bundle_role = 'success'
        elif self.bundle is not None:
            bundle_text = '内容已变化，请重新生成'
            bundle_role = 'error'
        else:
            bundle_text = '尚未生成'
            bundle_role = 'muted'

        self.review.setText(
            _tr('成品：{0}\n来源任务：{1}\n封面：{2}\n平台：{3}\n资料：{4}', f'{video_name}', f'{job_name}', f'{self.cover_mode.currentText()}', f"{'、'.join(platforms) or '未选择'}", f'{bundle_text}')
        )
        self.page_context.setText(
            _tr('当前成品：{0} · 来源任务：{1}', f'{video_name}', f'{job_name}') + (f' · {self.bv.text().strip()}' if self.bv.text().strip() else '')
        )

        video = clean_path(self.video.text()) if self.video.text().strip() else None
        self.set_review_state(
            'video',
            '成品可用' if video is not None and video.is_file() else '尚未选择成品',
            'success' if video is not None and video.is_file() else 'error',
        )
        source = clean_path(self.job.text()) if self.job.text().strip() else None
        self.set_review_state(
            'source',
            '来源任务可用' if source is not None and source.is_dir() else '尚未选择可用的来源任务',
            'success' if source is not None and source.is_dir() else 'error',
        )
        variants = [name for name in ('4:3', '16:9') if name in self._cover_asset_paths]
        if variants:
            cover_text = (
                '原图 + B站 ' + ' / '.join(variants) + ' 已生成'
                + ('' if current else ' · 封面需重新生成')
            )
        elif self.bundle is not None and not self._cover_asset_manifest_ready:
            cover_text = self.cover_mode.currentText() + ' · 发布资料需重新读取'
        elif self.bundle is not None and not self._cover_asset_manifest_data.get('content', {}).get('bilibili'):
            cover_text = self.cover_mode.currentText() + ' · 未启用 B站平台'
        elif self.bundle is not None:
            cover_text = self.cover_mode.currentText() + ' · B站补边封面待生成'
        else:
            cover_text = self.cover_mode.currentText() + ' · 生成发布资料后显示补边封面'
        self.set_review_state(
            'cover',
            cover_text,
            'success' if variants and current else ('error' if variants else 'muted'),
        )

        if selected:
            drafts = []
            drafts_valid = True
            for platform, values in selected.items():
                title_length = len(values['title'])
                description_length = len(values['description'])
                limits = (80, 2000) if platform == 'bilibili' else (20, 1000)
                drafts_valid = drafts_valid and bool(values['title'].strip())
                drafts_valid = drafts_valid and bool(values['description'].strip())
                drafts_valid = drafts_valid and title_length <= limits[0]
                drafts_valid = drafts_valid and description_length <= limits[1]
                drafts.append(
                    f'{platform_names[platform]} {title_length}/{description_length} 字'
                )
            self.set_review_state(
                'drafts',
                '；'.join(drafts) + ('' if drafts_valid else ' · 待修正'),
                'success' if drafts_valid else 'error',
            )
        else:
            self.set_review_state('drafts', '未选择平台草稿', 'error')
        self.set_review_state('bundle', bundle_text, bundle_role)

        active = []
        for platform, status in self.account_status.items():
            process = self.processes.get(platform)
            running = bool(self._browser_live.get(platform))
            if process is not None:
                try:
                    running = running or process.poll() is None
                except OSError:
                    running = True
            if running:
                active.append(platform_names[platform] + '：' + status.text())
        self.set_review_state(
            'browser',
            '；'.join(active) if active else '尚未打开；发布资料就绪后可打开平台填写',
            'muted',
        )

    def select_step(self,index):
        self.workspace.select(index)

    def step_changed(self,index):
        self.workspace.next.setVisible(index<3)
        self.workspace.action_bar.setVisible(index==3)
        self.prepare_button.setVisible(index==3)
        self.upload_button.setVisible(index==3)
        if index==1: self.cached_cover_preview()
        if index==3: self.refresh_review()
        self.refresh_upload_button()


    def next_step(self):
        index=self.workspace.stack.currentIndex()
        if index not in (0, 1, 2) or self.busy():
            return
        try:
            self.validate_inputs(index)
            self.workspace.select(index+1)
            if self.workspace.stack.currentIndex()==index+1:
                self.status.setText(
                    _tr('检查') + self.workspace.titles[index + 1] + _tr('，完成后继续下一步。') if index < 2 else _tr('资料检查完成，可生成发布资料；生成并核对后再打开平台填写。')
                )
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
                raise ValueError("请先选择可用的成品视频。")
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
                    raise ValueError("请至少启用一个平台草稿。")
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
                if not data.get('bvid'): raise ValueError('来源任务没有原视频，请选择“自选图片”或“B站原封面”。')
            if mode=='original':
                if not data.get('bvid') and not (job/'original-cover.json').is_file():
                    raise ValueError('本地任务没有 B站原封面；请读取本家资料或选择自选图片。')

    def cover_key(self):
        return (self.job.text(),self.cover_mode.currentData(),self.cover_file.text(),self.cover_seconds.value())

    def invalidate_cover_preview(self,*args):
        if not hasattr(self,'cover_preview'): return
        self.cover_stamp=None
        if hasattr(self, 'cover_asset_mode'):
            previous = self.cover_asset_mode.blockSignals(True)
            self.cover_asset_mode.setCurrentIndex(0)
            self.cover_asset_mode.blockSignals(previous)
        self.cover_preview.show_placeholder('封面设置已改变；请点击“获取 / 刷新来源预览”更新。')
        self.cover_detail.setText(_tr('预览按原图比例显示。'))
        self.refresh_cover_assets()

    def update_cover_options(self):
        mode=self.cover_mode.currentData()
        self.frame_panel.setVisible(mode=='frame')
        self.cover_seconds.setEnabled(mode=='frame')
        self.custom_cover_panel.setVisible(mode=='custom')
        self.invalidate_cover_preview()
        if hasattr(self,'workspace') and self.workspace.stack.currentIndex()==1: self.cached_cover_preview()

    def cached_cover_preview(self):
        if self.cover_stamp==self.cover_key(): return
        if hasattr(self, 'cover_asset_mode') and self.cover_asset_mode.currentData() != 'original':
            self.show_cover_asset()
            return
        try:
            if self.cover_mode.currentData()=='custom' and self.cover_file.text().strip():
                self.show_cover(clean_path(self.cover_file.text()))
            elif self.cover_mode.currentData()=='original' and self.job.text().strip():
                job=clean_path(self.job.text())
                info=json.loads((job/'original-cover.json').read_text(encoding='utf-8'))
                self.show_cover(job/Path(info['file']).name)
        except (OSError,ValueError,KeyError): pass

    def primary_action(self):
        """Compatibility entry point for the generate action only."""
        self.prepare_bundle()

    def refresh_cover_assets(self, force=False):
        if not hasattr(self, 'cover_asset_mode'):
            return
        manifest = {}
        manifest_ready = False
        if self.bundle is not None:
            try:
                manifest_path = Path(self.bundle) / 'manifest.json'
                mtime = manifest_path.stat().st_mtime_ns
                cache_key = (str(manifest_path), mtime)
                if force or cache_key != self._cover_asset_manifest_mtime:
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    manifest_ready = True
                    self._cover_asset_manifest_mtime = cache_key
                    self._cover_asset_bundle = self.bundle
                else:
                    manifest = getattr(self, '_cover_asset_manifest_data', {})
                    manifest_ready = bool(manifest)
            except (OSError, ValueError, TypeError):
                self._cover_asset_bundle = None
                self._cover_asset_manifest_mtime = None
                self._cover_asset_paths = {}
        else:
            self._cover_asset_bundle = None
            self._cover_asset_manifest_mtime = None
            self._cover_asset_paths = {}
        if self.bundle is not None and manifest_ready:
            variant_data = manifest.get('platform_covers', {}).get('bilibili', {})
            candidate = {'original': Path(self.bundle) / 'cover.jpg'}
            for ratio in ('4:3', '16:9'):
                asset = variant_data.get(ratio) or {}
                name = asset.get('file')
                if name and Path(name).name == name:
                    candidate[ratio] = Path(self.bundle) / name
            self._cover_asset_paths = {
                name: path for name, path in candidate.items() if path.is_file()
            }
        else:
            self._cover_asset_paths = {}
        self._cover_asset_manifest_data = manifest
        self._cover_asset_manifest_ready = manifest_ready

        for index in range(self.cover_asset_mode.count()):
            name = self.cover_asset_mode.itemData(index)
            ready = name == 'original' or name in self._cover_asset_paths
            tooltip = (
                '查看封面预览' if ready else
                f'{self.cover_asset_mode.itemText(index)} 尚未生成'
            )
            state = (ready, tooltip)
            if self._cover_asset_item_state.get(index) != state:
                item = self.cover_asset_mode.model().item(index)
                if item is not None:
                    item.setEnabled(ready)
                    item.setToolTip(tooltip)
                    self._cover_asset_item_state[index] = state

        if self.bundle is None:
            hint = 'B站 4:3 与 16:9 补边封面尚未生成；生成发布资料后可在此查看。'
        elif self.bundle_inputs != self.input_snapshot():
            hint = '内容已变化；这里显示的是上次生成的封面。请重新生成发布资料后再打开平台填写。'
        elif not manifest_ready:
            hint = '发布资料无法读取，请重新载入或生成。'
        elif not manifest.get('content', {}).get('bilibili'):
            hint = '本次没有启用 B站，因此不会生成 4:3 或 16:9 封面。'
        elif all(name in self._cover_asset_paths for name in ('4:3', '16:9')):
            hint = '补边封面已生成，完整保留原图。'
        else:
            hint = 'B站补边封面缺失，请重新生成发布资料。'
        self.cover_asset_hint.setText(hint)

        current = self.cover_asset_mode.currentData()
        if current != 'original' and current not in self._cover_asset_paths:
            asset_label = self.cover_asset_mode.itemText(self.cover_asset_mode.currentIndex())
            self.cover_preview.show_placeholder(
                f'{asset_label} 尚未生成；请重新生成发布资料。'
            )
            self.cover_detail.setText(_tr('尚未生成；请重新生成发布资料。'))
            self.cover_stamp = None
            previous = self.cover_asset_mode.blockSignals(True)
            self.cover_asset_mode.setCurrentIndex(0)
            self.cover_asset_mode.blockSignals(previous)

    def show_cover_asset(self, index=None):
        if not hasattr(self, 'cover_asset_mode'):
            return False
        name = self.cover_asset_mode.currentData()
        if name == 'original':
            bundle_cover = self._cover_asset_paths.get('original')
            if self.bundle is not None and bundle_cover is not None:
                return self.show_cover(
                    bundle_cover,
                    f'发布资料中的原图 · 完整预览',
                )
            self.cached_cover_preview()
            return not self.cover_preview.original.isNull()

        path = self._cover_asset_paths.get(name)
        if path is None or not path.is_file():
            asset_label = self.cover_asset_mode.itemText(self.cover_asset_mode.currentIndex())
            self.cover_preview.show_placeholder(
                f'{asset_label} 尚未生成；生成发布资料后可在此查看补边封面。'
            )
            self.cover_detail.setText(_tr('尚未生成，请先生成发布资料。'))
            self.cover_stamp = None
            return False
        return self.display_cover(path, f'{name} 补边封面预览')

    def load_job(self, job):
        data = read_source_info(job)
        path = clean_path(job)
        draft = source_draft(path)
        self.job.setText(str(path))
        self.bv.setText(data.get("bvid") or "")
        self.song.setText(song_from_title(data["title"]))
        self.source_info.setText(_tr('本地音乐：{0} · 本家、歌姬和 STAFF 请手填', f"{data['title']}") if data.get('kind') == 'local' else _tr('本家：{0} / @{1} / {2}（约 {3} 秒）', f"{data.get('bvid') or '未提供'}", f"{data.get('owner', {}).get('name', '')}", f"{data['title']}", f"{data.get('duration', '?')}"))
        self.voice.clear()
        self.credit.clear()
        for fields in self.fields.values():
            fields["title"].setText('【翻唱】' + self.song.text())
            fields["description"].setPlainText(draft)
        self.bundle = None
        self.bundle_inputs = None
        self.upload_preflight_error = None
        self.refresh_cover_assets(force=True)
        if hasattr(self, 'cover_asset_mode'):
            previous = self.cover_asset_mode.blockSignals(True)
            self.cover_asset_mode.setCurrentIndex(0)
            self.cover_asset_mode.blockSignals(previous)
        self.cover_file.clear()
        self.cover_mode.setCurrentIndex(0)
        if data.get("kind") == "local":
            self.cover_mode.setCurrentIndex(max(0, self.cover_mode.findData("custom")))
        self.cover_preview.show_placeholder(
            "请选择自选封面；也可按 BV 读取本家资料。"
            if data.get("kind") == "local" else
            "点击“获取 / 刷新来源预览”读取 B站原封面。"
        )
        try:
            cover_data = json.loads((path / "original-cover.json").read_text(encoding="utf-8"))
            self.show_cover(path / Path(cover_data["file"]).name)
        except (OSError, ValueError, KeyError):
            pass
        self.refresh_action()

    def choose_job(self):
        if self.busy():
            self.status.setText(_tr('请等待发布资料生成完毕，再更换来源任务。'));return
        from results_ui import TaskPickerDialog
        from PyQt5.QtWidgets import QDialog
        def choices(row):
            read_source_info(row['path']);source_draft(row['path'])
            return [{'label':'使用此来源任务','value':Path(row['path'])}]
        dialog=TaskPickerDialog(self.owner,self,title='从任务与结果选择发布来源',selected=self.job.text() or None,choices=choices)
        self.task_picker_dialog=dialog
        try:
            if dialog.exec_()==QDialog.Accepted:
                selected=dialog.selection()
                if selected:self.try_load(selected['value'])
        finally:dialog.deleteLater();self.task_picker_dialog=None

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
            QMessageBox.warning(self, _tr('无法读取来源资料'), _tr(str(exc)))

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, _tr('选择成品视频'), "", _tr('视频 (*.mp4 *.mov *.mkv)'))
        if path:
            self.video.setText(path)

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, _tr('选择封面图片'), str(ROOT), _tr('图片 (*.jpg *.jpeg *.png *.webp)'))
        if path:
            self.cover_file.setText(path)
            self.show_cover(path)

    def count_text(self):
        for platform, fields in self.fields.items():
            a, b = (80, 2000) if platform == "bilibili" else (20, 1000)
            fields["counter"].setText(_tr('标题 {0}/{1} 字 · 简介 {2}/{3} 字；超长时请编辑，助手不会自动删去 STAFF。', f"{len(fields['title'].text())}", f'{a}', f"{len(fields['description'].toPlainText())}", f'{b}'))
            role='error' if len(fields['title'].text())>a or len(fields['description'].toPlainText())>b else 'muted'
            fields['counter'].setProperty('role',role)
            fields['counter'].style().unpolish(fields['counter']);fields['counter'].style().polish(fields['counter'])
            if 'draft_state' in fields:
                enabled = fields['enabled'].isChecked()
                fields['draft_state'].setText(
                    (_tr('已启用 · ') if enabled else _tr('未纳入发布资料 · ')) + (_tr('标题或简介待补充') if not fields['title'].text().strip() or not fields['description'].toPlainText().strip() else _tr('草稿已填写'))
                )
                fields['draft_state'].setProperty('role', 'success' if enabled else 'muted')
                fields['draft_state'].style().unpolish(fields['draft_state'])
                fields['draft_state'].style().polish(fields['draft_state'])

    def apply_title(self):
        name = self.voice.text().strip()
        for fields in self.fields.values():
            fields["title"].setText(f'【{name}翻唱】{self.song.text().strip()}')

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

    def start_task(self, function, success, status_text="正在生成发布资料…", failure=None):
        if self.task is not None and self.task.isRunning():
            return
        if hasattr(self.owner, 'storage') and self.owner.storage.busy():
            self.status.setText(_tr('请等待缓存管理结束后再生成发布资料。'))
            return
        self.set_busy(True)
        self.status.setText(status_text)
        self.task = Task(function)
        self.task.finished_value.connect(success)
        self.task.failed.connect(failure or self.task_error)
        self.task.finished.connect(lambda: self.set_busy(False))
        self.task.start()

    def set_busy(self, active):
        self.form_panel.setEnabled(not active)
        self.workspace.set_locked(active)
        for button in (self.fetch, self.prepare_button, self.upload_button, self.cover_button):
            button.setEnabled(not active)
        if not active:
            self.refresh_action()
            self.refresh_browser_controls()

    def task_error(self, text):
        self.status.setText(text)
        self.log_message(text)
        self.log_toggle.setChecked(True)

    def log_message(self, text):
        if not hasattr(self, '_log_buffer'):
            from workflow_widgets import BufferedLog
            self._log_buffer = BufferedLog(self.log)
        self._log_buffer.append(text)

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
            self.load_job(path)
            self.status.setText(_tr('本家简介与 B站原封面已准备好。选择“来源原视频截图”时会另行下载原视频。'))
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
                    raise ValueError("请先选择自选封面。")
                return image
            if mode == "original":
                from cover_art import job_cover
                return job_cover(job)[0]
            from pipeline import ensure_source_video
            extract_cover(ensure_source_video(job), destination, seconds)
            return destination
        def ready(path):
            self.show_cover(path)
            self.status.setText(_tr('已选择自选封面。') if mode == 'custom' else _tr('已获取 B站原封面。') if mode == 'original' else _tr('来源原视频截图：原视频 {0} 秒。', f'{seconds:.2f}'))
        self.start_task(render, ready)

    def display_cover(self, path, detail=None):
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self.cover_preview.setPixmap(pixmap)
            self.cover_stamp=self.cover_key()
            prefix = detail or '原图预览'
            self.cover_detail.setText(
                _tr('{0} · {1} × {2} · 完整比例', f'{prefix}', f'{pixmap.width()}', f'{pixmap.height()}')
            )
            return True
        else:
            self.cover_preview.show_placeholder('封面预览无法读取；请重新选择或生成。')
            self.cover_detail.setText(_tr('请重新选择或生成封面；这里不会显示其他图片。'))
            self.cover_stamp=None
            return False

    def show_cover(self, path, detail=None):
        """Display a source/original cover and keep the asset selector honest."""
        if hasattr(self, 'cover_asset_mode'):
            previous = self.cover_asset_mode.blockSignals(True)
            self.cover_asset_mode.setCurrentIndex(0)
            self.cover_asset_mode.blockSignals(previous)
        return self.display_cover(path, detail)

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
            QMessageBox.warning(self, _tr('请填写歌姬'), _tr('请将标题里的 XX 替换为这次使用的歌姬，或直接填写发布标题。'))
            return
        job, video = self.job.text(), self.video.text()
        mode = self.cover_mode.currentData()
        seconds = self.cover_seconds.value()
        image = self.cover_file.text().strip() if mode == 'custom' else None
        inputs = self.input_snapshot()
        def make():
            if mode == 'frame':
                from pipeline import ensure_source_video
                ensure_source_video(job)
            return prepare(job, video, content, seconds, image, cover_mode=mode)
        def ready(bundle):
            self.bundle = bundle
            self.bundle_inputs = inputs
            self.upload_preflight_error = None
            previous = self.cover_asset_mode.blockSignals(True)
            self.cover_asset_mode.setCurrentIndex(0)
            self.cover_asset_mode.blockSignals(previous)
            self.refresh_cover_assets(force=True)
            self.show_cover(bundle / "cover.jpg", '发布资料中的原图')
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.status.setText(
                _tr('发布资料已生成 · 成品 {0} 秒 · 核对后可打开平台填写。', f"{manifest['video']['duration']:.2f}")
            )
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
        if not isinstance(data.get("content"), dict) or not data["content"]:
            raise ValueError("发布资料没有可恢复的平台文稿，请重新生成。")
        from publication import load_bundle
        # Check structure and file presence before replacing the controls. Large
        # video/cover hashes are deferred to the asynchronous open-platform preflight.
        for platform in data.get("content", {}):
            load_bundle(bundle, platform, verify_hash=False)
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
        self.upload_preflight_error = None
        previous = self.cover_asset_mode.blockSignals(True)
        self.cover_asset_mode.setCurrentIndex(0)
        self.cover_asset_mode.blockSignals(previous)
        self.refresh_cover_assets(force=True)
        self.show_cover(bundle / "cover.jpg", '发布资料中的原图')
        self.status.setText(_tr('已载入发布资料并完成检查。打开平台填写前会再次核对文件。'))
        self.refresh_action()
        self.refresh_browser_controls()
        self.refresh_review()

    def choose_bundle(self):
        path = QFileDialog.getExistingDirectory(self, _tr('选择发布资料文件夹'), str(ROOT / "publish-packages"))
        if path:
            try:
                self.restore_bundle(path)
                self.save_settings()
            except Exception as exc:
                QMessageBox.warning(self, _tr('发布资料无法载入'), _tr(str(exc)))

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

    def live_browser_platforms(self):
        """Return platforms with a live helper or a held browser-profile lease."""
        live = set()
        for platform, process in list(self.processes.items()):
            try:
                if process.poll() is None:
                    live.add(platform)
            except OSError:
                # An indeterminate process is treated as live so cleanup stays conservative.
                live.add(platform)
        from task_store import locked
        profiles = ROOT / "cache/publish-browser"
        for platform in ("bilibili", "xiaohongshu"):
            if locked(profiles / platform):
                live.add(platform)
        return tuple(sorted(live))

    def live_browser_active(self):
        return bool(self.live_browser_platforms())

    def resume_browser(self, platform):
        if self.bundle is None or self.bundle_inputs != self.input_snapshot():
            QMessageBox.warning(self, _tr('发布资料已变化'), _tr('请载入原发布资料，或按当前内容重新生成后再填写。'))
            return
        location = self.browser_location(platform)
        if location is None:
            QMessageBox.warning(self, _tr('平台页面已关闭'), _tr('继续填写需要原平台页面保持打开。请重新打开平台填写。'))
            return
        try:
            from publication import load_bundle
            load_bundle(self.bundle, platform)
            state = json.loads((location / (platform + "_上传状态.json")).read_text(encoding="utf-8"))
            if not state.get("checks", {}).get("video_selected"):
                raise ValueError("尚未选择视频；请先在平台选择视频后再继续。")
            atomic_json(location / (platform + ".replacement.json"), {"bundle": str(self.bundle)})
            (location / (platform + ".repair")).touch()
            self.status.setText(_tr('继续填写当前发布资料的标题、简介与封面；不会重新上传视频。'))
            self.refresh_review()
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法继续填写'), _tr(str(exc)))

    def launch_browser(self, platform, login_only=False):
        storage = getattr(self.owner, "storage", None)
        storage_busy = getattr(storage, "busy", None)
        if callable(storage_busy) and storage_busy():
            self.status.setText(_tr('请等待缓存管理结束后再打开登录或平台填写。'))
            QMessageBox.warning(self, _tr('缓存管理正在进行'), _tr('请等待缓存扫描或清理完成，再打开登录或平台填写。'))
            return
        if self.browser_location(platform) is not None:
            QMessageBox.warning(self, _tr('平台页面已打开'), _tr('请使用已打开的平台页面，或先关闭它再继续。'))
            return
        if not login_only and self.bundle is None:
            QMessageBox.warning(self, _tr('还没有发布资料'), _tr('请先生成发布资料。成品或文稿变化后，请重新生成。'))
            return
        if not login_only:
            if self.bundle_inputs != self.input_snapshot():
                QMessageBox.warning(self, _tr('发布资料已变化'), _tr('成品、来源、封面或文稿已变化，请重新生成发布资料。'))
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
        self.status.setText(_tr('平台页面已打开。登录、验证码和最终发布由你在网页完成。'))
        self.account_status[platform].setText(_tr('登录窗口已打开，完成后请关闭。') if login_only else _tr('正在打开平台填写…'))
        self.refresh_browser_controls()
        self.refresh_review()

    def upload_selected(self):
        if self.busy():
            return
        platforms = list(self.content())
        if not platforms:
            QMessageBox.warning(self, _tr('没有选择平台'), _tr('请选择需要填写的平台。'))
            return
        if self.bundle is None or self.bundle_inputs != self.input_snapshot():
            QMessageBox.warning(self, _tr('请先生成发布资料'), _tr('请先生成与当前成品、封面和文稿一致的发布资料。'))
            return
        storage = getattr(self.owner, "storage", None)
        storage_busy = getattr(storage, "busy", None)
        if callable(storage_busy) and storage_busy():
            self.status.setText(_tr('请等待缓存管理结束后再打开平台填写。'))
            QMessageBox.warning(self, _tr('缓存管理正在进行'), _tr('请等待缓存扫描或清理完成，再打开平台填写。'))
            return

        bundle = Path(self.bundle)
        frozen_inputs = self.input_snapshot()
        frozen_binding = json.loads(json.dumps(self.bundle_inputs))
        platforms = tuple(platforms)
        manifest = bundle / 'manifest.json'

        def manifest_stamp():
            stat = manifest.stat()
            return stat.st_mtime_ns, stat.st_size

        try:
            frozen_manifest = manifest_stamp()
        except OSError as exc:
            self.status.setText(_tr('发布资料无法读取，请重新生成后再打开平台填写。'))
            QMessageBox.warning(self, _tr('发布资料检查未通过'), _tr(str(exc)))
            self.refresh_action()
            return

        def validate_bundle():
            # This method has no GUI access. Large video/cover hashes stay off the
            # event loop; repeating load_bundle per platform also checks platform variants.
            from publication import load_bundle
            for platform in platforms:
                load_bundle(bundle, platform)
            return bundle, frozen_inputs, frozen_binding, frozen_manifest, platforms

        self.start_task(
            validate_bundle,
            self.finish_upload_preflight,
            status_text="正在检查发布资料…",
            failure=self.upload_preflight_failed,
        )

    def upload_preflight_failed(self, text):
        self.upload_preflight_error = str(text)
        self.status.setText(_tr('发布资料检查未通过；请检查文件或重新生成后再试。'))
        self.log_message("发布资料检查未通过：" + str(text))
        self.log_toggle.setChecked(True)
        QMessageBox.warning(
            self,
            _tr('发布资料检查未通过'),
            _tr('{0}\n\n可以重新生成发布资料后再打开平台填写。', f'{text}'),
        )

    def finish_upload_preflight(self, result):
        bundle, frozen_inputs, frozen_binding, frozen_manifest, platforms = result
        manifest = Path(bundle) / 'manifest.json'
        try:
            stat = manifest.stat()
            current_manifest = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            current_manifest = None
        stale = (
            self.bundle != bundle
            or self.bundle_inputs != frozen_binding
            or self.input_snapshot() != frozen_inputs
            or current_manifest != frozen_manifest
        )
        if stale:
            self.status.setText(
                _tr('发布资料或当前内容已变化，未打开平台填写。')
            )
            self.log_message("发布资料检查结果已作废：当前内容或资料文件已变化。")
            QMessageBox.warning(
                self,
                _tr('检查结果已过期'),
                _tr('发布资料或当前内容已变化。请重新生成发布资料后再打开平台填写。'),
            )
            self.refresh_action()
            return
        self.status.setText(_tr('发布资料检查通过，正在打开所选平台填写…'))
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
                self.log_message("平台填写已结束；详情见 " + str(process.vf_location))
                if process.returncode:
                    messages.append("平台填写未完成，请查看发布日志。")
                    self.account_status[platform].setText(_tr('平台填写未完成，请查看发布日志。'))
                else:
                    self.account_status[platform].setText(_tr('平台填写已结束；可重新登录或再次填写。'))
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
        self.refresh_review()

    def refresh_browser_controls(self):
        storage = getattr(self.owner, "storage", None)
        storage_busy = getattr(storage, "busy", None)
        storage_busy = bool(callable(storage_busy) and storage_busy())
        self._browser_live = {}
        for platform,actions in self.account_actions.items():
            location=self.browser_location(platform)
            self._browser_live[platform] = location is not None
            actions['login'].setEnabled(location is None and not storage_busy)
            actions['stop'].setEnabled(location is not None)
            can_resume=False
            if location is not None and self.bundle is not None and self.bundle_inputs==self.input_snapshot():
                try:
                    state=json.loads((location/(platform+'_上传状态.json')).read_text(encoding='utf-8'))
                    can_resume=bool(state.get('checks',{}).get('video_selected'))
                except (OSError,ValueError): pass
            actions['resume'].setEnabled(can_resume)
        self.refresh_upload_button()

    def stop_browser(self, platform):
        location = self.browser_location(platform)
        if location:
            (location / (platform + ".stop")).touch()
            self.account_status[platform].setText(_tr('正在关闭平台任务…'))
            self.refresh_review()

    def open_bundle(self):
        location = self.bundle or ROOT / "publish-packages"
        location.mkdir(parents=True, exist_ok=True)
        os.startfile(str(location))

    def busy(self):
        return self.task is not None and self.task.isRunning()
