"""Independent video assembly page with real still previews and external open."""
from i18n import tr as _tr
import json
import uuid
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from common import ROOT, app_path
from desktop_theme import button, card, label
from media_preview import PreviewTask, discard_preview_job
from ui_components import AdaptiveColumns


class RenderTask(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.succeeded.emit(self.function())
        except Exception as error:
            self.failed.emit(str(error))


class StillView(QLabel):
    """Real frame display with an intentionally empty, non-media empty state."""

    def __init__(self):
        super().__init__(_tr('选择原视频后生成首帧预览。'))
        self.setObjectName('video-still')
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._source = QPixmap()

    def show_frame(self, path):
        source = QPixmap(str(path))
        if source.isNull():
            self.clear_frame('首帧图片无法读取，请重新生成预览。')
            return False
        self._source = source
        self._render()
        return True

    def clear_frame(self, message):
        self._source = QPixmap()
        super().clear()
        self.setText(_tr(message))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    def _render(self):
        if self._source.isNull():
            return
        super().setPixmap(self._source.scaled(self.contentsRect().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class VideoMixdownPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.task, self.job, self.result = owner, None, None, None
        self.source_job = None
        self.active_inputs = {}
        self.formal_inputs = {}

        self.preview_task = None
        self.preview_inputs = {}
        self.preview_result = None
        self.preview_generation = 0
        self.preview_error = ''
        self._preview_cancel_message = '首帧预览已取消。'
        self.alignment_candidate = None
        self.alignment_candidate_key = None
        self._reset_pending = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self._page = QVBoxLayout(content)
        self._page.setContentsMargins(30, 28, 30, 24)
        self._page.setSpacing(20)
        self._page.addWidget(label(_tr('合成视频'), 'heading'))
        self._page.addWidget(label(_tr('将 Mixdown 配回原视频。检查首帧与文件后开始合成。'), 'muted'))
        self._context = label('', 'muted')
        self._page.addWidget(self._context)

        preview_card, preview_body = card(_tr('媒体检查'), _tr('预览仅显示首帧，音视频可分别打开检查。'))
        self.preview = StillView()
        preview_body.addWidget(self.preview, 1)
        self.preview_state = label(_tr('尚未生成预览。'), 'muted')
        preview_body.addWidget(self.preview_state)
        preview_actions = QHBoxLayout()
        self.preview_button = button(_tr('生成首帧预览'), self.preview_still, 'primary')
        self.preview_stop = button(_tr('取消预览'), self.cancel_preview, 'danger')
        self.preview_stop.hide()
        preview_actions.addWidget(self.preview_button)
        preview_actions.addWidget(self.preview_stop)
        preview_actions.addStretch()
        preview_body.addLayout(preview_actions)

        self.summary = label('', 'muted')
        preview_body.addWidget(self.summary)
        self.open_video = button(_tr('打开原视频'), lambda: self.open_media('video'))
        self.open_mixdown = button(_tr('打开 Mixdown'), lambda: self.open_media('mixdown'))
        self.open_output = button(_tr('打开成品'), lambda: self.open_media('output'))
        opens = QHBoxLayout()
        for action in (self.open_video, self.open_mixdown, self.open_output):
            action.setEnabled(False)
            opens.addWidget(action)
        opens.addStretch()
        preview_body.addLayout(opens)
        self.alignment = label(_tr('自动对齐：合成后显示对齐结果。'), 'muted')
        preview_body.addWidget(self.alignment)

        form_card, body = card(_tr('文件与合成参数'), _tr('保留原视频画面，成品保存为 MP4；合成期间不能修改当前设置。'))
        self.fields = {}
        for key, title, pattern in (('video', '原视频', '视频 (*.mp4 *.mkv *.mov)'),
                                    ('mixdown', 'Mixdown', '音频 (*.wav *.flac *.mp3 *.m4a)'),
                                    ('output', '成品位置', 'MP4 视频 (*.mp4)')):
            field = QLineEdit()
            field.setPlaceholderText(_tr('选择') + title)
            field.setAccessibleName(title)
            self.fields[key] = field
            row = QHBoxLayout()
            row.addWidget(label(title), 1)
            row.addWidget(field, 3)
            def choose(checked=False, key=key, title=title, pattern=pattern):
                picker = QFileDialog.getSaveFileName if key == 'output' else QFileDialog.getOpenFileName
                value, _ = picker(self, '选择' + title, self.fields[key].text(), pattern)
                if value:
                    self.fields[key].setText(value)
            row.addWidget(button(_tr('选择'), choose))
            body.addLayout(row)
            field.textChanged.connect(self.inputs_changed)

        self.choose_task_button=button(_tr('从任务与结果选择原视频'),self.choose_job)
        body.addWidget(self.choose_task_button)
        self.auto = QCheckBox(_tr('自动对齐音频'))
        self.auto.setChecked(True)
        body.addWidget(self.auto)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3600, 3600)
        self.offset.setDecimals(3)
        self.offset.setSuffix(' 秒')
        self.offset.setEnabled(False)
        self.offset.setAccessibleName(_tr('手动偏移秒数'))
        offset_row = QHBoxLayout()
        offset_row.addWidget(label(_tr('手动偏移')), 1)
        offset_row.addWidget(self.offset, 2)
        body.addLayout(offset_row)
        body.addWidget(label(_tr('正数延后 Mixdown，负数提前，支持三位小数；启用自动对齐时不能手动输入偏移。'), 'muted'))
        self.normalize = QCheckBox(_tr('统一响度至 -14 LUFS（双遍测量）'))
        body.addWidget(self.normalize)

        self.report = QFrame()
        self.report.setObjectName('video-report')
        report_body = QVBoxLayout(self.report)
        report_body.setContentsMargins(16, 14, 16, 14)
        report_body.setSpacing(8)
        self.report_title = label(_tr('尚无合成结果'), 'card-title')
        self.report_text = label(_tr('合成完成后显示对齐、响度与成品检查。'), 'muted')
        self.report_file = label('', 'muted')
        self.report_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        report_body.addWidget(self.report_title)
        report_body.addWidget(self.report_text)
        report_body.addWidget(self.report_file)
        body.addWidget(self.report)
        self.report.hide()

        footer = QWidget()
        footer.setObjectName('footer')
        footer_body = QVBoxLayout(footer)
        footer_body.setContentsMargins(16,10,16,10)
        self.progress = QProgressBar()
        self.progress.hide()
        footer_body.addWidget(self.progress)
        self.status = label(_tr('选择原视频、Mixdown 与成品位置即可合成，无需先创建任务。'), 'muted')
        footer_body.addWidget(self.status)
        from ui_components import FlowLayout
        actions = FlowLayout()
        self.start = button(_tr('合成视频'), self.render, 'primary')
        self.stop = button(_tr('取消'), self.cancel, 'danger')
        self.stop.hide()
        self.to_publish = button(_tr('将成品带入发布'), self.use_result)
        self.to_publish.setEnabled(False)
        actions.addWidget(self.start)
        actions.addWidget(self.stop)
        actions.addWidget(self.to_publish)
        footer_body.addLayout(actions)

        for child in (preview_card, form_card):
            child.setMinimumWidth(0)
        self._page.addWidget(AdaptiveColumns(preview_card, form_card, breakpoint=980, ratio=(1, 1)), 1)
        self._page.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        outer.addWidget(footer)
        self.form = form_card

        for signal in (self.auto.toggled, self.offset.valueChanged, self.normalize.toggled):
            signal.connect(self.inputs_changed)
        self.refresh_paths()

    def busy(self):
        """Formal render only; preview has an independent lifetime."""
        return self.task is not None and self.task.isRunning()

    def preview_busy(self):
        return ((self.preview_task is not None and self.preview_task.isRunning())
                or bool(self.preview_inputs))

    def _set_context(self):
        job = self.source_job
        from results_ui import task_caption
        self._context.setText(_tr('当前来源任务：') + (task_caption(self.owner, job) if job else _tr('独立文件（未关联任务）')))
        self._context.setToolTip(str(job) if job else '')

    def _path(self, key):
        value = self.fields[key].text().strip()
        return app_path(value) if value else None

    def refresh_paths(self, *args):
        for key, action in (('video', self.open_video), ('mixdown', self.open_mixdown),
                            ('output', self.open_output)):
            path = self._path(key)
            action.setEnabled(bool(path and path.is_file()))
        video, mixdown = self._path('video'), self._path('mixdown')
        text = []
        for title, path in (('原视频', video), ('Mixdown', mixdown)):
            if path:
                text.append(title + '：' + self.short_path(path))
        self.summary.setText('\n'.join(text) if text else _tr('尚未选择原视频与 Mixdown。'))
        self._set_context()
        self._refresh_candidate()

    def short_path(self, path, width=72):
        value = str(path)
        return value if len(value) <= width else value[:30] + ' … ' + value[-38:]

    def inputs_changed(self, *args):
        self.offset.setEnabled(not self.busy() and not self.auto.isChecked())
        self.invalidate_result()
        if not self.busy():
            self.invalidate_preview('原视频或参数已变化，请重新生成首帧预览。')

    def invalidate_result(self, *args):
        self.result = None
        self.formal_inputs = {}
        self.alignment_candidate = None
        self.alignment_candidate_key = None
        if hasattr(self, 'to_publish'):
            self.to_publish.setEnabled(False)
        if hasattr(self, 'report'):
            self.report_title.setText(_tr('尚无合成结果'))
            self.report_text.setText(_tr('合成完成后显示对齐、响度与成品检查。'))
            self.report_file.clear()
            self.report.hide()
        if hasattr(self, 'alignment'):
            if self.auto.isChecked():
                self.alignment.setText(_tr('自动对齐：合成后显示对齐结果。'))
            else:
                self.alignment.setText(_tr('手动偏移：{:+.3f} 秒（合成后请试听确认）。').format(self.offset.value()))
        if hasattr(self, 'open_video'):
            self.refresh_paths()

    def invalidate_preview(self, message='尚未生成预览。'):
        self.preview_generation += 1
        self._preview_cancel_message = message
        previous = self.preview_result
        self.preview_result = None
        self.preview_error = ''
        if previous:
            self._discard_preview_result(previous)
        if hasattr(self, 'preview'):
            self.preview.clear_frame(message)
        if hasattr(self, 'preview_state'):
            self.preview_state.setText(message)
        if self.preview_busy():
            self.cancel_preview()

    def use_current_job(self):
        job = getattr(self.owner, 'job', None)
        self.use_source_job(job)

    def choose_job(self):
        if self.busy() or self.preview_busy():
            self.status.setText(_tr('视频正在处理，请完成或取消后再更换任务。'));return
        from results_ui import TaskPickerDialog
        from PyQt5.QtWidgets import QDialog
        def choices(row):
            job=Path(row['path'])
            if not (job/'video/source.mp4').is_file():raise ValueError('此任务没有原视频；请直接选择本地视频。')
            return [{'label':'载入任务原视频','value':job}]
        dialog=TaskPickerDialog(self.owner,self,title='从任务与结果选择视频来源',selected=self.source_job,choices=choices)
        self.task_picker_dialog=dialog
        try:
            if dialog.exec_()==QDialog.Accepted:
                selected=dialog.selection()
                if selected:self.use_source_job(selected['value'])
        finally:dialog.deleteLater();self.task_picker_dialog=None

    def use_source_job(self, job, mixdown=None):
        if self.busy() or self.preview_busy():
            self.status.setText(_tr('视频正在处理，请完成或取消后再更换任务。'))
            return
        if job and (Path(job) / 'video/source.mp4').is_file():
            self.source_job = Path(job).resolve()
            self.fields['video'].setText(str(Path(job) / 'video/source.mp4'))
            self.fields['output'].setText(str(Path(job) / 'rendered/cover-mixdown.mp4'))
            self.fields['mixdown'].setText(str(mixdown) if mixdown else '')
            self.invalidate_preview('当前任务已载入；可生成首帧预览检查画面。')
        else:
            self.status.setText(_tr('此任务没有原视频；请直接选择本地视频。'))
            return False
        self.refresh_paths()
        return True

    def preview_still(self):
        if self.busy() or self.preview_busy():
            return
        if getattr(self.owner.storage, 'busy', lambda: False)() or getattr(self.owner.updates, 'busy', lambda: False)():
            self.preview_state.setText(_tr('请等待缓存管理或更新操作结束。'))
            return
        video = self._path('video')
        if video is None or not video.is_file():
            self.preview_state.setText(_tr('请选择可用的原视频后再生成首帧预览。'))
            return
        linked = self.source_job if (self.source_job and video == (self.source_job / 'video/source.mp4').resolve()) else None
        self.invalidate_preview('正在生成首帧预览…')
        generation = self.preview_generation
        try:
            task = PreviewTask(video, source_job=linked, parent=self)
        except (OSError, ValueError) as error:
            self.preview_state.setText(_tr('预览无法开始：') + str(error))
            return
        self._preview_cancel_message = '首帧预览已取消。'
        self.preview_task = task
        self.preview_inputs = dict(task.preview_inputs)
        self.preview_inputs['generation'] = generation
        self.preview_button.setEnabled(False)
        self.preview_stop.show()
        self.preview_stop.setEnabled(True)
        self.preview_state.setText(_tr('正在生成首帧预览…'))

        def stale():
            return task is not self.preview_task or generation != self.preview_generation

        def accepted(result):
            if stale() or result.get('token') != task.token or task.cancel_event.is_set():
                self._discard_preview_result(result)
                return
            if not self.preview.show_frame(result.get('output')):
                self.preview_result = None
                self.preview_state.setText(_tr('首帧图片无法读取，请重新生成预览。'))
                self._discard_preview_result(result)
                return
            self.preview_result = dict(result)
            self.preview_error = ''
            self.preview_state.setText(_tr('首帧预览 · 0.000 秒。确认画面后再合成。'))

        def rejected(message):
            if stale():
                self._discard_preview_result(task)
                return
            self.preview_error = str(message)
            self.preview_state.setText(_tr('预览失败：') + str(message))

        def cancelled():
            if not stale():
                self.preview_state.setText(_tr('首帧预览已取消。'))

        def finished():
            if task is self.preview_task:
                self.preview_inputs = {}
                # Clear the live Qt ownership before queued deletion runs. A late
                # signal is rejected by the generation/token guards above.
                self.preview_task = None
                self.preview_stop.hide()
                self.preview_button.setEnabled(not self.busy())
                if self.preview_result is None and self.preview_error == '' and task.cancel_event.is_set():
                    self.preview_state.setText(self._preview_cancel_message)
                if self._reset_pending and not self.preview_busy():
                    self._reset_pending = False
                    QTimer.singleShot(0, self.render)
            task.deleteLater()
            # Stale and cancelled tasks must clean their own UUID directory now
            # that the owned FFmpeg process has exited.
            self._drop_stale_preview_dirs(task)

        task.succeeded.connect(accepted)
        task.failed.connect(rejected)
        task.cancelled.connect(cancelled)
        task.finished.connect(finished)
        task.start()

    def _discard_preview_result(self, result_or_task):
        job = getattr(result_or_task, 'job', None)
        if isinstance(result_or_task, dict):
            job = result_or_task.get('preview_job')
        if not job:
            return
        current = self.preview_result and self.preview_result.get('preview_job')
        if str(job) != str(current):
            try:
                discard_preview_job(job)
            except (OSError, ValueError):
                pass

    def _drop_stale_preview_dirs(self, task):
        current = self.preview_result and self.preview_result.get('preview_job')
        if str(task.job) != str(current):
            try:
                discard_preview_job(task.job)
            except (OSError, ValueError):
                pass

    def cancel_preview(self):
        if self.preview_busy():
            self.preview_generation += 1
            self.preview_result = None
            self.preview_state.setText(_tr('正在取消首帧预览…'))
            self.preview_stop.setEnabled(False)
            if self.preview_task is not None:
                # Keep preview_inputs until the owned process has actually exited;
                # app close, storage and results guards must keep seeing the source.
                self.preview_task.cancel()

    def open_media(self, key):
        path = self._path(key)
        if path is None or not path.is_file():
            self.status.setText(_tr('文件尚不存在；请先选择或生成。'))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.status.setText(_tr('无法调用系统播放器；请检查默认程序和文件权限。'))

    def render(self):
        if self.busy():
            return
        if self.preview_busy():
            self.cancel_preview()
            self._reset_pending = True
            self.status.setText(_tr('正在停止首帧预览；结束后可再次开始合成。'))
            return
        if getattr(self.owner.storage, 'busy', lambda: False)() or getattr(self.owner.updates, 'busy', lambda: False)():
            self.status.setText(_tr('请等待缓存管理或更新操作结束。'))
            return
        try:
            values = {key: app_path(field.text()) for key, field in self.fields.items() if field.text().strip()}
            if len(values) != 3 or not all(values[key].is_file() for key in ('video', 'mixdown')):
                raise ValueError('请选择可用的原视频和 Mixdown，并指定成品保存位置。')
            if values['output'].suffix.lower() != '.mp4':
                raise ValueError('成品必须使用 .mp4 文件名。')
            if values['output'].exists():
                raise ValueError('成品已存在，请选择其他文件名。')
            options = {'offset': None if self.auto.isChecked() else self.offset.value(),
                       'normalize': self.normalize.isChecked()}
            self.invalidate_result()
            self.report.hide()
            self.job = ROOT / 'cache/video-mixdown' / ('task-' + uuid.uuid4().hex)
            self.job.mkdir(parents=True)
            from video_mixdown import render
            source_job = self.source_job
            self.active_inputs = {'source_job': source_job, **values}
            self.formal_inputs = dict(self.active_inputs)
            render_job = self.job

            def make():
                from task_contract import bind, reconcile, selected
                linked = source_job if (source_job and values['video'] == (source_job / 'video/source.mp4').resolve()) else None
                if linked:
                    reconcile(linked)
                    current = selected(linked, 'mixdown')
                    if current != values['mixdown']:
                        bind(linked, 'mixdown', values['mixdown'])
                report = render(render_job, **values, reset_cancel=False, **options)
                if linked:
                    bind(linked, 'video', Path(report['output']), validate=False)
                    report['source_job'] = str(linked)
                return report

            self.task = RenderTask(make, self)
            self.task.succeeded.connect(self.completed)
            self.task.failed.connect(self.failed)
            self.task.finished.connect(self.finished)
            self.set_locked(True)
            self.status.setText(_tr('正在对齐并合成视频…'))
            self.task.start()
        except (OSError, ValueError) as error:
            if not self.busy():
                self.active_inputs = {}
            self.formal_inputs = {}
            self.status.setText(str(error))

    def set_locked(self, active):
        for field in self.fields.values():
            field.setEnabled(not active)
        self.auto.setEnabled(not active)
        self.normalize.setEnabled(not active)
        self.offset.setEnabled(not active and not self.auto.isChecked())
        for action in self.findChildren(QPushButton):
            action.setEnabled(not active)
        self.stop.setEnabled(active)
        self.stop.setVisible(active)
        self.progress.setVisible(active)
        self.progress.setRange(0, 0 if active else 1)
        self.to_publish.setEnabled(not active and self.result is not None)
        if active:
            self.preview_button.setEnabled(False)
        elif not self.preview_busy():
            self.preview_button.setEnabled(True)
        self.refresh_paths()

    def _alignment_text(self, alignment):
        if not alignment:
            return '自动对齐：未获得对齐结果。'
        method = alignment.get('method')
        if method == 'manual':
            return '手动偏移：{:+.3f} 秒。'.format(float(alignment.get('offset_seconds', 0) or 0))
        values = []
        if alignment.get('accepted') is False:
            values.append('结果不确定')
        for key, title, fmt in (('offset_seconds', '偏移', '{:+.3f} 秒'),
                                ('correlation', '相关度', '{:.3f}'),
                                ('margin', '差距', '{:.3f}'),
                                ('overlap_seconds', '重叠', '{:.2f} 秒')):
            if alignment.get(key) is not None:
                try:
                    values.append(title + ' ' + fmt.format(float(alignment[key])))
                except (TypeError, ValueError):
                    pass
        return '自动对齐：' + (' / '.join(values) if values else method or '分析完成')

    def completed(self, report):
        if self.sender() is not None and self.sender() is not self.task:
            return
        self.result = report
        alignment = report.get('alignment') or {}
        self.alignment.setText(self._alignment_text(alignment))
        self.status.setText(_tr('成品已生成。播放检查对齐与音量后，再决定是否带入发布。'))
        loudness = report.get('loudness_measurement')
        detail = ['响度处理：已应用' if report.get('normalized') else '响度处理：未启用']
        if loudness:
            for key, title in (('input_i', '输入 LUFS'), ('output_i', '输出 LUFS'),
                               ('input_tp', '峰值 dBTP')):
                if loudness.get(key) is not None:
                    detail.append('{} {:.2f}'.format(title, float(loudness[key])))
        self.report_title.setText(_tr('合成完成'))
        self.report_text.setText('\n'.join(detail))
        sha = report.get('sha256', '')
        self.report_file.setText(_tr('成品：') + str(report.get('output', '')) + ('\nSHA-256：' + sha if sha else ''))
        self.report.show()
        self._alignment_candidate()
        self.refresh_paths()

    def _candidate_path(self):
        video, mixdown = self._path('video'), self._path('mixdown')
        active = self.formal_inputs
        if not self.job or not video or not mixdown or not active:
            return None, None
        current = {'video': video.resolve(), 'mixdown': mixdown.resolve()}
        for key, value in current.items():
            if active.get(key) is None or Path(active[key]).resolve() != value:
                return None, None
        key = (str(current['video']), str(current['mixdown']))
        return self.job / 'video-alignment.json', key

    def _refresh_candidate(self):
        path, key = self._candidate_path()
        if self.alignment_candidate_key and key != self.alignment_candidate_key:
            self.alignment_candidate = None
            self.alignment_candidate_key = None
        if path is None or not path.is_file():
            return None
        return self._alignment_candidate()

    def _alignment_candidate(self):
        path, key = self._candidate_path()
        if path is None or not path.is_file():
            self.alignment_candidate = None
            self.alignment_candidate_key = None
            return None
        if key == self.alignment_candidate_key and self.alignment_candidate is not None:
            return self.alignment_candidate
        try:
            candidate = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            candidate = None
        self.alignment_candidate = candidate
        self.alignment_candidate_key = key
        if candidate and candidate.get('accepted') is False:
            self.alignment.setText(self._alignment_text(candidate) + _tr('。请试听后手动填写偏移。'))
        return candidate

    def failed(self, error):
        if self.sender() is not None and self.sender() is not self.task:
            return
        if self.job and (self.job / 'cancel.flag').exists():
            self.status.setText(_tr('已取消合成。'))
            self.report_title.setText(_tr('合成已取消'))
            self.report_text.setText(_tr('原视频与 Mixdown 保持不变；可检查参数后重新开始。'))
        else:
            self.status.setText(_tr('合成失败：') + str(error))
            self.report_title.setText(_tr('合成失败'))
            self.report_text.setText(str(error))
        self.report.show()
        candidate = self._alignment_candidate()
        if candidate:
            self.alignment.setText(self._alignment_text(candidate) + _tr('。请试听后手动填写偏移。'))
        self.refresh_paths()

    def finished(self):
        task = self.sender()
        if task is not None and task is not self.task:
            return
        self.active_inputs = {}
        self.set_locked(False)

    def cancel(self):
        if self.busy():
            (self.job / 'cancel.flag').touch()
            self.stop.setEnabled(False)
            self.status.setText(_tr('正在停止合成，原视频与 Mixdown 保持不变…'))

    def use_result(self):
        if self.result and Path(self.result['output']).is_file():
            if self.result.get('source_job'):
                self.owner.publishing.load_job(Path(self.result['source_job']))
            self.owner.publishing.video.setText(self.result['output'])
            self.owner.navigate(3)













