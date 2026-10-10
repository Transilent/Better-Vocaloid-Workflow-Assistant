"""Asynchronous disk scan and explicit cache cleanup selection."""
import shutil
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QMessageBox
from common import ROOT, output_directory, job_directories
from desktop_theme import page, button, label
from task_store import format_size, size
import storage


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
                directory = output_directory()
                while not directory.exists():
                    directory = directory.parent
                self.result.emit({'rows': storage.candidates(), 'free': shutil.disk_usage(directory).free,
                                  'tasks': sum(size(job) for job in job_directories()),
                                  'dependencies': size(ROOT/'dependencies') + size(ROOT/'models')})
        except Exception as exc:
            self.error.emit(str(exc))


class StoragePage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.worker = owner, None
        panel, body = page('缓存与磁盘', '检查磁盘占用，选择并清理可再生成的缓存和任务临时文件。')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        self.summary = label('点击“扫描占用”查看磁盘和缓存大小。', 'muted')
        body.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['可清理内容', '大小', '目录'])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 120)
        body.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.scan = button('扫描占用', self.refresh, 'primary')
        self.remove = button('清理选中缓存', self.clean, 'danger')
        row.addWidget(self.scan)
        row.addWidget(self.remove)
        row.addStretch()
        body.addLayout(row)
        body.addWidget(label('清理不会删除任务结果、模型、登录信息或工作流预设。任务临时文件清除后，重试时会重新生成。', 'muted'))

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def start(self, paths=None):
        if self.busy():
            return
        if self.owner.processing_active or self.owner.components.busy() or self.owner.publishing.busy() or self.owner.updates.busy():
            self.summary.setText('请等待正在运行的处理、下载或上传任务结束后再管理缓存。')
            return
        self.scan.setEnabled(False)
        self.remove.setEnabled(False)
        self.summary.setText('正在清理…' if paths is not None else '正在扫描…')
        self.worker = StorageWorker(paths)
        self.worker.result.connect(self.ready)
        self.worker.error.connect(self.failed)
        self.worker.finished.connect(lambda: (self.scan.setEnabled(True), self.remove.setEnabled(True)))
        self.worker.start()

    def refresh(self):
        self.start()

    def ready(self, data):
        if 'freed' in data:
            self.tree.clear()
            self.summary.setText('清理完成，释放 '+format_size(data['freed'])+'。可再次扫描查看占用。')
            return
        self.tree.clear()
        for row in data['rows']:
            item = QTreeWidgetItem([row['title'], format_size(row['bytes']), str(row['path'])])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, row)
            self.tree.addTopLevelItem(item)
        self.summary.setText(f'保存磁盘可用：{format_size(data["free"])}  ·  任务：{format_size(data["tasks"])}  ·  运行库与模型：{format_size(data["dependencies"])}')

    def failed(self, text):
        self.summary.setText('操作未完成：'+text)

    def clean(self):
        rows = [self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if rows and QMessageBox.question(self, '清理缓存', f'清理 {len(rows)} 项，共 {format_size(sum(r["bytes"] for r in rows))}？\n已完成的任务结果会保留。') == QMessageBox.Yes:
            self.start([r['path'] for r in rows])
