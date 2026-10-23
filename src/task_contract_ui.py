"""Inspect task evidence and explicitly accept user-completed files off the UI thread."""
from i18n import tr as _tr
import os
from pathlib import Path
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHeaderView, QLayout,
                             QMessageBox, QScrollArea, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)
from common import load_config
from ui_components import FlowLayout
from desktop_theme import COLORS, button, label
import task_contract as contract

STATES = {'verified': '校验通过', 'unchecked': '尚未校验', 'modified': '文件已修改',
          'missing': '文件缺失', 'unreadable': '无法读取', 'stale': '上游文件已变化',
          'missing_dependency': '上游文件缺失', 'invalid_dependency': '文件关联无效'}
ROLES = {'midi': 'MIDI', 'editor_project': '编辑器工程', 'absolute_pitch': '原始音高线',
         'audio': '分轨音频', 'source_video': '原视频', 'cover': '视频截图',
         'original_cover': '本家封面', 'subtitles': '字幕', 'report': '处理记录',
         'source_metadata': '来源资料', 'description_draft': '简介草稿',
         'tuned_project': '人工调音工程', 'mixdown': 'Mixdown', 'video': '成品视频',
         'publication': '发布资料', 'publication_asset': '发布附件',
         'calibration_profile': '声库校准配置', 'calibration_audio': '校准试音',
         'retained_expression': '保留的演唱参数'}
ROLES.update(calibration_session='校准导出任务', calibration_input='校准输入', calibrated_project='校准后的工程', publication_source='发布资料来源')
ROLES['project_audio'] = '工程音频附件'
PRODUCERS = {'metadata':'来源资料', 'download':'下载 / 导入', 'audio':'音频解码', 'separation':'分离声部',
             'midi':'提取 MIDI', 'subtitles':'导出字幕', 'exports':'导出文件', 'human':'人工登记',
             'calibration':'声库校准', 'calibration_session':'校准试音', 'calibration_result':'校准结果',
             'publication':'发布准备', 'project_reference':'工程内音频'}


def _state_color(state, palette=None):
    """Use the active theme semantics without baking light-theme text colors in."""
    if palette is None:
        mode = load_config().get('theme', 'light')
        palette = COLORS.get(mode, COLORS['light'])
    if state == 'verified':
        return QColor(palette['selected_text'])
    if state in ('modified', 'missing', 'unreadable', 'missing_dependency', 'invalid_dependency'):
        return QColor(palette['danger'])
    if state == 'stale':
        return QColor(palette['accent'])
    return QColor(palette['muted'])




def _set_role(widget, role):
    value = role or ''
    if widget.property('role') == value:
        return
    widget.setProperty('role', value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class FileCheck(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function

    def run(self):
        try: self.ready.emit(self.function())
        except Exception as error: self.failed.emit(str(error))


class TaskFilesDialog(QDialog):
    def __init__(self, owner, job):
        super().__init__(owner)
        self.owner, self.job, self.worker, self.rows = owner, Path(job).resolve(), None, []
        self.setWindowTitle(_tr('文件与下一步'))
        self.setMinimumSize(620, 520)
        self.resize(1080, 760)
        self.setSizeGripEnabled(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(26, 24, 26, 24)
        body.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        body.addWidget(label(_tr('文件与下一步'), 'heading'))
        from results_ui import task_caption
        self.task_context=label(task_caption(owner,self.job),'muted');self.task_context.setToolTip(str(self.job));body.addWidget(self.task_context)
        body.addWidget(label(_tr('检查已生成文件，登记调音完成后的工程、Mixdown 或成品视频。原文件会保留在原位置。'), 'muted'))
        self.status = label(_tr('正在检查…'), 'muted')
        self.status.setWordWrap(True)
        body.addWidget(self.status)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.setHeaderLabels([_tr('文件'), _tr('用途'), _tr('状态'), _tr('生成步骤 / 上游文件数')])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.tree.setMinimumHeight(270)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_file())
        body.addWidget(self.tree, 1)

        first_actions = FlowLayout(spacing=10)
        self.details = QCheckBox(_tr('显示处理记录与发布附件'))
        self.details.toggled.connect(self.render)
        first_actions.addWidget(self.details)
        first_actions.addWidget(button(_tr('打开选中文件'), self.open_file))
        first_actions.addWidget(button(_tr('重新检查'), self.refresh))
        first_actions.addWidget(button(_tr('重建文件清单'), self.rebuild))
        body.addLayout(first_actions)

        bind_actions = FlowLayout(spacing=10)
        self.kind = QComboBox()
        self.kind.setMinimumWidth(250)
        for text, kind in (('调音完成的工程', 'project'), ('导出的 Mixdown', 'mixdown'),
                           ('最终成品视频', 'video')):
            self.kind.addItem(text, kind)
        bind_actions.addWidget(self.kind)
        bind_actions.addWidget(button(_tr('登记文件…'), self.choose, 'primary'))
        bind_actions.addWidget(button(_tr('取消此项登记'), self.remove_binding))
        body.addLayout(bind_actions)

        next_actions = FlowLayout(spacing=10)
        next_actions.addWidget(button(_tr('进入视频合成'), self.to_video))
        next_actions.addWidget(button(_tr('进入发布工作流'), self.to_publication))
        next_actions.addWidget(button(_tr('关闭'), self.accept))
        body.addLayout(next_actions)
        body.addWidget(label(_tr('双击文件可打开。登记或取消登记、重建清单前，会检查文件是否正被处理、缓存管理或发布流程使用。'), 'muted'))
        self.refresh()

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def start(self, action=None, callback=None):
        if self.busy(): return
        from task_store import locked
        if locked(self.job) or self.owner.processing_active or self.owner.storage.busy() or self.owner.publishing.busy():
            self.status.setText(_tr('请等待任务处理、缓存管理或发布准备结束后再检查文件。')); return
        self.status.setText(_tr('正在检查文件，较大的音视频可能需要几秒钟…'))
        def run():
            result = action() if action else None
            contract.reconcile(self.job)
            rows = contract.inspect(self.job)
            return {'result': result, 'rows': rows, 'action': contract.handoff(self.job, rows=rows)}
        self.worker = FileCheck(run, self)
        self.worker.ready.connect(lambda data: self.ready(data, callback))
        self.worker.failed.connect(lambda text: self.status.setText(_tr('文件检查未完成：') + text))
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        worker = self.sender()
        if self.worker is worker: self.worker = None
        worker.deleteLater()
        if hasattr(self, '_pending_result'):
            result = self._pending_result
            del self._pending_result
            super().done(result)

    def ready(self, data, callback=None):
        self.rows = data['rows']; self.render()
        issues = sum(row['state'] != 'verified' for row in self.rows)
        if self.rows:
            _set_role(self.status, 'success' if issues == 0 else '')
            self.status.setText(_tr('{0} 个文件，{1} 项需要检查。下一步：{2}。', f'{len(self.rows)}', f'{issues}', _tr(contract.NEXT[data['action']])))
        else:
            _set_role(self.status, 'muted')
            self.status.setText(_tr('还没有可检查的文件。可重建文件清单，或登记已有的工程、Mixdown 或成品视频。'))
        self.owner.results.reload()
        if callback: callback(data['result'])

    def render(self):
        self.tree.clear()
        paths = {row['id']: row['path'] for row in self.rows}
        palette = COLORS.get(load_config().get('theme', 'light'), COLORS['light'])
        for row in self.rows:
            if not self.details.isChecked() and row['role'] in ('report', 'publication_asset'):
                continue
            item = QTreeWidgetItem([Path(row['path']).name, _tr(ROLES.get(row['role'], row['role'])),
                                    _tr(STATES.get(row['state'], row['state'])),
                                    f'{_tr(PRODUCERS.get(row["producer"], row["producer"]))} · {len(row["dependencies"])}'])
            item.setData(0, Qt.UserRole, row)
            item.setToolTip(0, row['path'])
            item.setToolTip(3, '\n'.join(paths.get(dep['id'], _tr('上游文件未登记'))
                                        for dep in row['dependencies']) or _tr('没有上游文件'))
            item.setForeground(2, _state_color(row['state'], palette))
            self.tree.addTopLevelItem(item)
    def refresh(self):
        self.start()

    def open_file(self):
        item = self.tree.currentItem()
        if not item: return
        path = Path(item.data(0, Qt.UserRole)['path'])
        try:
            if not path.is_file(): raise ValueError('文件不存在，请重新检查或重新生成。')
            os.startfile(str(path))
        except (OSError, ValueError) as error: self.status.setText(str(error))

    def choose(self):
        if self.busy(): return
        kind = self.kind.currentData()
        filters = {'project': '编辑器工程 (*.svp *.ustx *.vsqx)', 'mixdown': '音频 (*.wav *.flac *.mp3 *.ogg)',
                   'video': '视频 (*.mp4 *.mkv *.mov)'}
        path, _ = QFileDialog.getOpenFileName(self, _tr('登记') + self.kind.currentText(), str(self.job), filters[kind])
        if path: self.start(lambda: contract.bind(self.job, kind, Path(path)))

    def remove_binding(self):
        kind = self.kind.currentData()
        self.start(lambda: contract.unbind(self.job, kind))

    def rebuild(self):
        if self.busy(): return
        if QMessageBox.question(self, _tr('重建文件清单'), _tr('根据现有处理记录重建文件清单？\n原清单会另存备份；音视频和工程不会被修改。旧任务缺少校验记录的文件会按当前内容登记。')) == QMessageBox.Yes:
            self.start(lambda: contract.rebuild(self.job))

    def to_video(self):
        def ready(path):
            self.owner.video_mixdown.use_source_job(self.job, path)
            self.owner.navigate(8); self.accept()
        self.start(lambda: contract.selected(self.job, 'mixdown'), ready)

    def to_publication(self):
        def choose():
            path = contract.selected(self.job, 'video')
            if not path: raise ValueError('请先登记最终成品视频，或通过视频合成生成成品。')
            return path
        def ready(path):
            try:
                self.owner.publishing.load_job(self.job)
                self.owner.publishing.video.setText(str(path))
                self.owner.navigate(3); self.accept()
            except (OSError, ValueError) as error: self.status.setText(str(error))
        self.start(choose, ready)

    def done(self, result):
        if self.busy(): self._pending_result = result
        else: super().done(result)

    def closeEvent(self, event):
        if self.busy(): event.ignore()
        else: super().closeEvent(event)
