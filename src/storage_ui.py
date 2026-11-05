"""Asynchronous disk scan and explicit cache cleanup selection."""
from i18n import tr as _tr
import shutil
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (QHBoxLayout, QHeaderView, QMessageBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)
from common import ROOT, output_directory, job_directories
from desktop_theme import page, button, label
from task_store import format_size, size
from task_contract_ui import active_ui_operations
import storage


def _set_role(widget, role):
    value = role or ''
    if widget.property('role') == value:
        return
    widget.setProperty('role', value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _existing_directory(value):
    """Return the nearest usable directory without relying on a self-parent loop."""
    path = Path(value)
    for candidate in (path, *path.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


class StorageWorker(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, paths=None):
        super().__init__()
        self.paths = paths

    def run(self):
        try:
            if self.paths is not None:
                self.result.emit({'freed': storage.clean(self.paths)})
            else:
                directory = _existing_directory(output_directory())
                self.result.emit({'rows': storage.candidates(include_protected=True),
                                  'free': shutil.disk_usage(directory).free if directory is not None else None,
                                  'tasks': sum(size(job) for job in job_directories()),
                                  'publication': size(ROOT/'publish-packages'),
                                  'dependencies': size(ROOT/'dependencies') + size(ROOT/'models')})
        except Exception as exc:
            self.error.emit(str(exc))


class StoragePage(QWidget):
    def __init__(self, owner, compact=False):
        super().__init__()
        self.owner, self.worker = owner, None
        if compact:
            from desktop_theme import card
            panel, body = card(_tr('缓存与磁盘'), _tr('只清理可再生成的缓存；任务或浏览器正在使用的内容会保留。'))
        else:
            panel, body = page(_tr('缓存与磁盘'), _tr('检查磁盘占用，选择并清理可再生成的缓存和任务临时文件。'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)

        self.summary = label(_tr('点击“扫描占用”查看磁盘和缓存大小。'), 'muted')
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName(_tr('磁盘占用概览'))
        body.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr('缓存内容'), _tr('大小'), _tr('目录')])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 110)
        self.tree.setMinimumHeight(230 if compact else 320)
        if compact:
            self.tree.setMaximumHeight(300)
        self.tree.setAccessibleName(_tr('可清理缓存列表'))
        self.tree.itemChanged.connect(self.selection_changed)
        body.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.scan = button(_tr('扫描占用'), self.refresh, 'primary')
        self.remove = button(_tr('清理选中缓存'), self.clean, 'danger')
        row.addWidget(self.scan)
        row.addWidget(self.remove)
        row.addStretch()
        body.addLayout(row)

        self.selection = label(_tr('尚未选择缓存项。正在使用的内容不可勾选。'), 'muted')
        self.selection.setWordWrap(True)
        self.selection.setAccessibleName(_tr('缓存选择摘要'))
        body.addWidget(self.selection)
        body.addWidget(label(_tr('仅列出可再生成的缓存和未使用的发布资料。成品视频、任务结果、已修改的发布资料、模型和登录信息会保留。'), 'muted'))

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def other_operations(self):
        """Name live work that shares cache or task files with this page."""
        return active_ui_operations(self.owner)

    def start(self, paths=None):
        if self.busy():
            return
        active = self.other_operations()
        if active:
            self.summary.setText(_tr('请等待') + '、'.join(active) + _tr('结束后再管理缓存。'))
            return
        self.scan.setEnabled(False)
        self.remove.setEnabled(False)
        self.summary.setText(_tr('正在清理…') if paths is not None else _tr('正在扫描…'))
        self.worker = StorageWorker(paths)
        self.worker.result.connect(self.ready)
        self.worker.error.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        worker = self.sender() or self.worker
        if self.worker is worker:
            self.worker = None
        self.scan.setEnabled(True)
        self.remove.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def refresh(self):
        self.start()

    def ready(self, data):
        if 'freed' in data:
            self.tree.clear()
            _set_role(self.summary, 'success')
            self.summary.setText(_tr('清理完成，释放 ') + format_size(data['freed']) + _tr('。可再次扫描查看占用。'))
            self.selection.setText(_tr('尚未选择缓存项。可再次扫描后选择其他可清理内容。'))
            return
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for row in data['rows']:
                item = QTreeWidgetItem([row['title'], format_size(row['bytes']), str(row['path'])])
                item.setData(0, Qt.UserRole, row)
                item.setToolTip(2, str(row['path']))
                if row.get('protected'):
                    item.setText(0, row['title'] + ' · 使用中，暂不可清理')
                    item.setToolTip(0, row['protected'])
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                else:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Unchecked)
                self.tree.addTopLevelItem(item)
        finally:
            self.tree.blockSignals(False)
        _set_role(self.summary, '')
        free = data.get('free')
        free_text = format_size(free) if free is not None else _tr('输出盘不可用')
        self.summary.setText(
            _tr('磁盘可用：{0}  ·  任务：{1}  ·  发布资料：{2}  ·  运行库与模型：{3}', f'{free_text}', f"{format_size(data['tasks'])}", f"{format_size(data.get('publication', 0))}", f"{format_size(data['dependencies'])}"))
        self.selection_changed()

    def selection_changed(self, *_):
        rows = [self.tree.topLevelItem(i).data(0, Qt.UserRole)
                for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if rows:
            self.selection.setText(_tr('已选择 {0} 项，可释放 {1}。清理前会再次确认范围。', f'{len(rows)}', f"{format_size(sum((row['bytes'] for row in rows)))}"))
        else:
            self.selection.setText(_tr('尚未选择缓存项。正在使用的内容不可勾选。'))

    def failed(self, text):
        _set_role(self.summary, 'error')
        self.summary.setText(_tr('操作未完成：') + text)

    def clean(self):
        rows = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if rows and QMessageBox.question(self, _tr('清理缓存'), _tr('清理 {0} 项，共 {1}？\n已完成的任务结果会保留。', f'{len(rows)}', f"{format_size(sum((r['bytes'] for r in rows)))}")) == QMessageBox.Yes:
            self.start([r['path'] for r in rows])
