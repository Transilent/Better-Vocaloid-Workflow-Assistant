"""Update preferences, release notes and verified download progress."""

from common import environment_value
from i18n import tr as _tr
import json
import threading
from PyQt5.QtCore import QEvent, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QTextEdit, QProgressBar
from common import ROOT, load_config, atomic_json
from desktop_theme import button, card, label, page, scroll_content
from ui_components import choice
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
        self.startup_update = False
        self.startup_interrupted = False
        self.restart_deferred = False
        self.restart_remaining = None
        self.restart_timer = QTimer(self)
        self.restart_timer.setInterval(1000)
        self.restart_timer.timeout.connect(self.maybe_auto_restart)
        container, layout = page(_tr('设置与更新'), _tr('管理下载、更新、外观与设备设置。'))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        content = scroll_content(layout)
        body_layout = QVBoxLayout(content)
        self.body_layout = body_layout
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)

        settings, body = card(_tr('下载设置'), _tr('选项同时用于组件和程序更新；切换来源不会改动画质、语言或声学参数。'))
        config = load_config()
        body.addWidget(label(_tr('下载方式'), 'muted'))
        self.source = QComboBox()
        from download_sources import SOURCE_OPTIONS, normalize_source
        for title, value in SOURCE_OPTIONS:
            self.source.addItem(_tr(title), value)
        try: configured_source = normalize_source(config.get('download_source', 'auto'))
        except ValueError: configured_source = 'auto'
        self.source.setCurrentIndex(max(0, self.source.findData(configured_source)))
        # Named download routes fit a dropdown better than a wall of cards.
        self.source_choice = None
        body.addWidget(self.source)

        body.addWidget(label(_tr('并行下载数'), 'muted'))
        self.download_workers = QComboBox()
        for count in (1, 2, 4, 8):
            self.download_workers.addItem(str(count), count)
        self.download_workers.setCurrentIndex(
            max(0, self.download_workers.findData(config.get('download_workers', 4))))
        self.workers_choice = choice(self.download_workers, columns=4, compact=True)
        body.addWidget(self.workers_choice)
        self.download_workers.currentIndexChanged.connect(self.save_settings)
        self.source.currentIndexChanged.connect(self.save_settings)
        body.addWidget(label(_tr('自动模式会尝试可用来源；组件和程序更新在安装前会校验文件完整性。'), 'muted'))
        body.addWidget(label(_tr('指定镜像仅用于对应来源；其他文件直连。优先加速源会按文件来源自动匹配。'), 'muted'))
        body_layout.addWidget(settings)

        automatic, body = card(_tr('自动更新'), _tr('可在启动时检查并下载更新；安装前会检查正在使用的功能，并给出重启倒计时。'))
        self.auto_check = QCheckBox(_tr('启动时检查更新'))
        self.auto_check.setChecked(config.get('check_for_updates', True))
        self.auto_download = QCheckBox(_tr('启动时自动下载可用的程序更新'))
        self.auto_download.setChecked(config.get('startup_auto_update', True))
        self.auto_restart = QCheckBox(_tr('启动更新完成后自动重启安装'))
        self.auto_restart.setChecked(config.get('auto_restart_updates', True))
        body.addWidget(self.auto_check)
        body.addWidget(self.auto_download)
        body.addWidget(self.auto_restart)
        body.addWidget(label(_tr('音乐处理、组件安装、缓存管理、发布浏览器、视频或校准进行中时，暂不安装更新。'), 'muted'))
        self.auto_check.toggled.connect(self.save_settings)
        self.auto_download.toggled.connect(self.save_settings)
        self.auto_restart.toggled.connect(self.save_settings)
        body_layout.addWidget(automatic)

        updates, body = card(_tr('程序版本'), _tr('当前版本 · ') + updater.current_version())
        self.message = label(_tr('点击“检查更新”获取最新版本。'), 'muted')
        self.message.setWordWrap(True)
        self.message.setAccessibleName(_tr('更新状态'))
        body.addWidget(self.message)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAcceptRichText(False)
        self.notes.setPlaceholderText(_tr('新版本的变更说明会显示在这里。'))
        self.notes.setMinimumHeight(190)
        self.notes.setAccessibleName(_tr('更新说明'))
        body.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        body.addWidget(self.progress)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.action = button(_tr('检查更新'), self.primary_action, 'primary')
        self.cancel = button(_tr('取消下载'), self.cancel_download, 'danger')
        self.cancel.hide()
        row.addWidget(self.action, 1)
        row.addWidget(self.cancel)
        self.release_link = button(_tr('发布页面'), self.open_release)
        row.addWidget(self.release_link)
        body.addLayout(row)
        body.addWidget(label(_tr('自动安装前会倒计时 10 秒。选择“暂缓安装”后，可在下次启动时应用，或稍后返回这里手动安装。'), 'muted'))
        body_layout.addWidget(updates)
        body_layout.addStretch()

        self.application = QApplication.instance()
        self.application.aboutToQuit.connect(self.stop_watching)
        self.application.installEventFilter(self)
        pending = updater.pending_update()
        if pending:
            self.plan = pending['plan']
            self.startup_update = True
            self.action.setText(_tr('重启并安装更新'))
            self.message.setText(_tr('更新 ') + pending['version'] + _tr(' 已下载，重启后安装。'))
            QTimer.singleShot(0, self.queue_auto_restart)
        receipt = ROOT / 'cache/updates/last-result.json'
        if receipt.is_file() and not pending:
            try:
                result = json.loads(receipt.read_text(encoding='utf-8'))
                if not isinstance(result, dict):
                    self.message.setText(_tr('上次更新记录无法读取，可重新检查更新。'))
                elif result.get('state') == 'failed':
                    error = result.get('error')
                    self.message.setText(_tr('上次更新未完成：') + (error if isinstance(error, str) else _tr('请重新检查更新。')))
            except (OSError, ValueError, KeyError):
                pass
        if self.auto_check.isChecked() and not pending:
            import os
            if not environment_value('VWA_TESTING'):
                QTimer.singleShot(1800, lambda: self.check(startup=True))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel, QEvent.Close):
            if isinstance(self.owner, QWidget) and isinstance(watched, QWidget):
                widget = watched
                while widget is not None and widget is not self.owner:
                    widget = widget.parentWidget()
                if widget is self.owner:
                    self.startup_interrupted = True
                    self.stop_watching()
                    if self.startup_update:
                        self.restart_deferred = True
                        self.restart_timer.stop()
                        self.restart_remaining = None
                        if self.plan:
                            self.cancel.hide()
                            self.message.setText(_tr('已开始使用助手，自动安装已暂缓。可手动重启安装，或在下次启动时应用更新。'))
        return False

    def stop_watching(self):
        self.application.removeEventFilter(self)

    def closeEvent(self, event):
        self.stop_watching()
        super().closeEvent(event)

    def save_settings(self):
        config = load_config(resolve_paths=False)
        config.update(download_source=self.source.currentData(), check_for_updates=self.auto_check.isChecked(),
                      download_workers=self.download_workers.currentData(),
                      startup_auto_update=self.auto_download.isChecked(),
                      auto_restart_updates=self.auto_restart.isChecked())
        atomic_json(ROOT / 'config.json', config)
        self.owner.components.source.setCurrentIndex(self.owner.components.source.findData(self.source.currentData()))
        if not (self.auto_check.isChecked() and self.auto_download.isChecked() and self.auto_restart.isChecked()):
            self.restart_timer.stop()
            self.restart_remaining = None
            if self.plan:
                self.cancel.hide()
                self.message.setText(_tr('更新已校验，可点击“重启并安装更新”应用。'))

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def check(self, startup=False):
        if self.busy() or self.plan:
            return
        if startup and (self.startup_interrupted or self.active_operations()):
            self.message.setText(_tr('已开始使用助手，本次启动的自动更新已跳过。可稍后手动检查更新。'))
            return
        self.startup_update = bool(startup)
        self.worker = UpdateWorker(self.source.currentData())
        self.worker.value.connect(self.checked)
        self.start_worker('正在检查更新…', downloading=False)

    def start_worker(self, message, downloading):
        if hasattr(self.owner, 'storage') and self.owner.storage.busy():
            self.message.setText(_tr('请等待缓存管理操作结束后再检查或下载更新。'))
            return
        self.message.setText(message)
        self.action.setEnabled(False)
        self.source.setEnabled(False)
        self.cancel.setVisible(downloading)
        self.cancel.setEnabled(True)
        self.cancel.setText(_tr('取消下载'))
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.progress.connect(self.update_progress)
        self.worker.start()

    def checked(self, release):
        self.release = release
        self.notes.setPlainText(release['notes'])
        if release.get('legacy'):
            self.message.setText(_tr('当前发布版尚未提供自动更新信息，请前往“发布页面”查看。'))
            self.action.setText(_tr('重新检查'))
        elif not release['available']:
            self.message.setText(_tr('暂无可用更新 · 最新正式版 ') + release['version'])
            self.action.setText(_tr('重新检查'))
        elif not release.get('update'):
            self.message.setText(_tr('新版本 ') + release['version'] + _tr(' 暂不支持当前运行库，请查看发布说明。'))
            self.action.setText(_tr('检查更新'))
        else:
            size = release['update'].get('download_bytes', release['update']['bytes'])
            self.message.setText(_tr('新版本 ') + release['version'] + _tr(' · 下载约 ') + f'{size / 1024 ** 2:.1f} MB')
            self.action.setText(_tr('下载程序更新'))
            if self.startup_update and self.auto_download.isChecked() and not self.restart_deferred:
                if hasattr(self.owner, 'navigate'):
                    self.owner.navigate(4)
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
        self.restart_deferred = self.startup_update and self.startup_interrupted
        self.restart_remaining = None
        self.action.setText(_tr('重启并安装更新'))
        self.message.setText(_tr('下载完成，全部文件已校验。重启后安装更新。'))

    def failed(self, message):
        self.message.setText(message)

    def finished(self):
        self.action.setEnabled(True)
        self.source.setEnabled(True)
        self.cancel.hide()
        self.queue_auto_restart()

    def queue_auto_restart(self):
        if (self.plan and self.startup_update and self.auto_check.isChecked()
                and self.auto_download.isChecked() and self.auto_restart.isChecked() and not self.restart_deferred):
            if hasattr(self.owner, 'navigate'):
                self.owner.navigate(4)
            self.cancel.setText(_tr('暂缓安装'))
            self.cancel.setEnabled(True)
            self.cancel.show()
            self.restart_timer.start()

    def active_operations(self):
        owner = self.owner
        publishing = getattr(owner, 'publishing', None)
        publishing_busy = getattr(publishing, 'busy', None)
        publishing_busy = bool(callable(publishing_busy) and publishing_busy())
        # A prepared upload is only one publishing activity; its login/upload child can outlive it.
        poll = getattr(publishing, 'poll_browser', None)
        if callable(poll):
            poll()
        live_browser = getattr(publishing, 'live_browser_active', None)
        live_browser = bool(callable(live_browser) and live_browser())
        components = getattr(owner, 'components', None)
        storage = getattr(owner, 'storage', None)
        component_busy = getattr(components, 'busy', None)
        storage_busy = getattr(storage, 'busy', None)
        preview_busy = getattr(getattr(owner, 'video_mixdown', None), 'preview_busy', None)
        return (self.busy() or getattr(owner, 'processing_active', False)
                or bool(callable(component_busy) and component_busy())
                or publishing_busy or live_browser
                or bool(callable(storage_busy) and storage_busy())
                or bool(callable(preview_busy) and preview_busy())
                or bool(getattr(owner, 'video_mixdown', None) and owner.video_mixdown.busy())
                or bool(getattr(owner,'calibration_dialog',None) and owner.calibration_dialog.busy()))

    def maybe_auto_restart(self):
        if (not self.plan or not self.startup_update or not self.auto_check.isChecked()
                or not self.auto_download.isChecked() or not self.auto_restart.isChecked() or self.restart_deferred):
            self.restart_timer.stop()
            return
        if self.active_operations():
            self.restart_remaining = None
            if self.busy():
                return
            self.restart_deferred = True
            self.restart_timer.stop()
            self.message.setText(_tr('当前操作已开始，自动安装已暂缓。更新将在下一次启动时应用，也可手动重启安装。'))
            return
        if self.restart_remaining is None:
            self.restart_remaining = 10
        if self.restart_remaining > 0:
            self.message.setText(_tr('更新已校验，将在 {0} 秒后自动重启安装。', f'{self.restart_remaining}'))
            self.restart_remaining -= 1
            return
        self.restart_timer.stop()
        self.primary_action()

    def update_progress(self, done, total, message):
        self.progress.setValue(round(done * 100 / max(total, 1)))
        self.message.setText(message + f' · {done / 1024**2:.1f} / {total / 1024**2:.1f} MB')

    def cancel_download(self):
        if self.plan and not self.busy():
            self.restart_deferred = True
            self.restart_timer.stop()
            self.restart_remaining = None
            self.message.setText(_tr('已暂缓自动安装，更新文件已保留。可稍后点击“重启并安装更新”。'))
            self.cancel.hide()
            return
        if self.worker:
            self.worker.stop.set()
            self.message.setText(_tr('正在取消下载，已下载的数据会保留。'))
            self.cancel.setEnabled(False)

    def primary_action(self):
        if self.plan:
            owner = self.owner
            if self.active_operations():
                self.message.setText(_tr('请等待当前任务结束，并关闭登录或上传浏览器后再重启安装。'))
                return
            try:
                owner.workflow.persist(dirty=False)
                updater.restart_to_apply(self.plan)
                QApplication.instance().quit()
            except Exception as exc:
                self.message.setText(str(exc))
        elif self.release and self.release['available'] and self.release.get('update'):
            self.startup_update = False
            self.download()
        else:
            self.check()

    def open_release(self):
        import webbrowser
        webbrowser.open(self.release['url'] if self.release else updater.RELEASES + '/latest')
