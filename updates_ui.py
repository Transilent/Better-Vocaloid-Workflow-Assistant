"""Update preferences, release notes and verified download progress."""
import json
import threading
from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QTextEdit, QProgressBar
from common import ROOT, load_config, atomic_json
from desktop_theme import button, card, label, page, scroll_content
import updater


class UpdateWorker(QThread):
    value = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object, object, str)

    def __init__(self, source, release=None):
        super().__init__()
        self.source, self.release = source, release
        self.stop = threading.Event()

    def run(self):
        try:
            if self.release:
                self.value.emit(updater.prepare_update(self.release, self.source, self.progress.emit, self.stop.is_set))
            else:
                self.value.emit(updater.check_release(self.source))
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdatesPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.worker, self.release = owner, None, None
        self.plan = None
        container, layout = page('设置与更新', '检查新版本，选择适合当前网络的下载方式。')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        content = scroll_content(layout)
        body_layout = QVBoxLayout(content)
        body_layout.setContentsMargins(0, 0, 0, 0)
        settings, body = card('下载与更新设置')
        config = load_config()
        row = QHBoxLayout()
        row.addWidget(label('默认下载方式'))
        self.source = QComboBox()
        for title, value in (('自动选择', 'auto'), ('官方源直连', 'direct'), ('优先加速源', 'mirror')):
            self.source.addItem(title, value)
        self.source.setCurrentIndex(max(0, self.source.findData(config.get('download_source', 'auto'))))
        row.addWidget(self.source, 1)
        body.addLayout(row)
        body.addWidget(label('GitHub 下载支持 GitProxy，Hugging Face 模型支持 HF-Mirror。所有更新文件均校验 SHA-256。', 'muted'))
        self.auto_check = QCheckBox('启动时检查更新')
        self.auto_check.setChecked(config.get('check_for_updates', True))
        self.auto_download = QCheckBox('自动下载可用的程序更新，完成后提示重启')
        self.auto_download.setChecked(config.get('auto_download_updates', False))
        body.addWidget(self.auto_check)
        body.addWidget(self.auto_download)
        self.source.currentIndexChanged.connect(self.save_settings)
        self.auto_check.toggled.connect(self.save_settings)
        self.auto_download.toggled.connect(self.save_settings)
        body_layout.addWidget(settings)
        updates, body = card('程序版本', '当前版本 · ' + updater.current_version())
        self.message = label('点击“检查更新”获取最新版本。', 'muted')
        body.addWidget(self.message)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAcceptRichText(False)
        self.notes.setPlaceholderText('新版本的变更说明会显示在这里。')
        self.notes.setMinimumHeight(170)
        body.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        body.addWidget(self.progress)
        row = QHBoxLayout()
        self.action = button('检查更新', self.primary_action, 'primary')
        self.cancel = button('取消下载', self.cancel_download, 'danger')
        self.cancel.hide()
        row.addWidget(self.action)
        row.addWidget(self.cancel)
        row.addStretch()
        self.release_link = button('发布页面', self.open_release)
        row.addWidget(self.release_link)
        body.addLayout(row)
        body.addWidget(label('程序更新会保留任务、配置、平台登录和已安装模型。安装前需结束正在运行的任务。', 'muted'))
        body_layout.addWidget(updates)
        body_layout.addStretch()
        pending = updater.pending_update()
        if pending:
            self.plan = pending['plan']
            self.action.setText('重启并安装更新')
            self.message.setText('更新 ' + pending['version'] + ' 已下载，重启后安装。')
        receipt = ROOT / 'cache/updates/last-result.json'
        if receipt.is_file() and not pending:
            try:
                result = json.loads(receipt.read_text(encoding='utf-8'))
                if result['state'] == 'failed':
                    self.message.setText('上次更新未完成：' + result['error'])
            except (OSError, ValueError, KeyError):
                pass
        if self.auto_check.isChecked() and not pending:
            import os
            if not os.environ.get('BVWA_TESTING'):
                QTimer.singleShot(1800, self.check)

    def save_settings(self):
        config = load_config(resolve_paths=False)
        config.update(download_source=self.source.currentData(), check_for_updates=self.auto_check.isChecked(),
                      auto_download_updates=self.auto_download.isChecked())
        atomic_json(ROOT / 'config.json', config)
        self.owner.components.source.setCurrentIndex(self.owner.components.source.findData(self.source.currentData()))

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def check(self):
        if self.busy() or self.plan:
            return
        self.worker = UpdateWorker(self.source.currentData())
        self.worker.value.connect(self.checked)
        self.start_worker('正在检查更新…', downloading=False)

    def start_worker(self, message, downloading):
        if hasattr(self.owner, 'storage') and self.owner.storage.busy():
            self.message.setText('请等待缓存管理操作结束后再检查或下载更新。')
            return
        self.message.setText(message)
        self.action.setEnabled(False)
        self.source.setEnabled(False)
        self.cancel.setVisible(downloading)
        self.cancel.setEnabled(True)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.progress.connect(self.update_progress)
        self.worker.start()

    def checked(self, release):
        self.release = release
        self.notes.setPlainText(release['notes'])
        if release.get('legacy'):
            self.message.setText('当前发布版尚未提供自动更新信息，请前往“发布页面”查看。')
            self.action.setText('重新检查')
        elif not release['available']:
            self.message.setText('暂无可用更新 · 最新正式版 ' + release['version'])
            self.action.setText('重新检查')
        elif not release.get('update'):
            self.message.setText('新版本 ' + release['version'] + ' 需要下载完整便携包。')
            self.action.setText('检查更新')
        else:
            self.message.setText('新版本 ' + release['version'] + ' · 下载约 ' + f"{release['update']['bytes'] / 1024**2:.1f} MB")
            self.action.setText('下载程序更新')
            if self.auto_download.isChecked():
                QTimer.singleShot(100, self.download)

    def download(self):
        if self.busy() or not self.release or not self.release.get('update'):
            return
        self.worker = UpdateWorker(self.source.currentData(), self.release)
        self.worker.value.connect(self.downloaded)
        self.progress.setValue(0)
        self.progress.show()
        self.start_worker('正在下载程序更新…', downloading=True)

    def downloaded(self, plan):
        self.plan = str(plan)
        self.action.setText('重启并安装更新')
        self.message.setText('下载完成，全部文件已校验。重启后安装更新。')

    def failed(self, message):
        self.message.setText(message)

    def finished(self):
        self.action.setEnabled(True)
        self.source.setEnabled(True)
        self.cancel.hide()

    def update_progress(self, done, total, message):
        self.progress.setValue(round(done * 100 / max(total, 1)))
        self.message.setText(message + f' · {done / 1024**2:.1f} / {total / 1024**2:.1f} MB')

    def cancel_download(self):
        if self.worker:
            self.worker.stop.set()
            self.message.setText('正在取消下载，已下载的数据会保留。')
            self.cancel.setEnabled(False)

    def primary_action(self):
        if self.plan:
            owner = self.owner
            if owner.processing_active or owner.components.busy() or owner.publishing.busy() or owner.storage.busy():
                self.message.setText('请等待当前任务结束后再重启安装。')
                return
            try:
                updater.restart_to_apply(self.plan)
                QApplication.instance().quit()
            except Exception as exc:
                self.message.setText(str(exc))
        elif self.release and self.release['available'] and self.release.get('update'):
            self.download()
        else:
            self.check()

    def open_release(self):
        import webbrowser
        webbrowser.open(self.release['url'] if self.release else updater.RELEASES + '/latest')
