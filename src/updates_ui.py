"""Update preferences, release notes and verified download progress."""

from common import environment_value
from i18n import tr as _tr
import json
import threading
from PyQt5.QtCore import QEvent, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QTextEdit, QProgressBar
from common import ROOT, load_config, atomic_json
from desktop_theme import button, card, label, page, scroll_content
from ui_components import choice
import updater
from task_contract_ui import active_ui_operations
from support_info import QQ_GROUP, FEEDBACK_URL
import startup_preferences


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
        self.install_requested = False
        self._downloading = False
        self._notice_dialog = None
        self._queued_notice = None
        self._notice_shown = set()
        self._pending_release = None
        self._notice_force = False
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.maybe_show_notice)
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

        automatic, body = card(_tr('更新提醒'), _tr('启动时检查新版本，先展示更新内容；确认“立即更新”后才下载或安装。'))
        self.auto_check = QCheckBox(_tr('启动时检查更新'))
        self.auto_check.setChecked(config.get('check_for_updates', True))
        self.auto_restart = QCheckBox(_tr('确认立即更新后，下载完成时倒计时重启安装'))
        self.auto_restart.setChecked(config.get('auto_restart_updates', True))
        body.addWidget(self.auto_check)
        body.addWidget(self.auto_restart)
        body.addWidget(label(_tr('音乐处理、组件安装、缓存管理、发布浏览器、视频或校准进行中时，暂不安装更新。'), 'muted'))
        self.auto_check.toggled.connect(self.save_settings)
        self.auto_restart.toggled.connect(self.save_settings)
        body.addWidget(label(_tr('延后提醒只对所选版本生效；新版本仍会提示。手动检查不受延后时间限制。'), 'muted'))
        self.reset_reminder_button=button(_tr('恢复更新提醒'),self.reset_update_reminder)
        body.addWidget(self.reset_reminder_button)
        body_layout.addWidget(automatic)

        self.community_card,community=card(_tr('帮助与用户社区'),_tr('交流使用问题、查看公告，也欢迎反馈建议。'))
        self.community_card.setObjectName('communitySupportCard')
        self.group_label=label(_tr('用户QQ群：{0}').format(QQ_GROUP))
        self.group_label.setObjectName('communityQQGroup')
        self.group_label.setTextFormat(Qt.PlainText)
        self.group_label.setTextInteractionFlags(Qt.TextSelectableByMouse|Qt.TextSelectableByKeyboard)
        community.addWidget(self.group_label)
        community_actions=QHBoxLayout()
        self.copy_group_button=button(_tr('复制QQ群号'),self.copy_qq_group)
        self.replay_tour_button=button(_tr('重新查看入门教程'),self.replay_tutorial)
        community_actions.addWidget(self.copy_group_button);community_actions.addWidget(self.replay_tour_button)
        community.addLayout(community_actions)
        self.feedback_button=button(_tr('GitHub 反馈与建议'),self.open_feedback)
        self.feedback_button.setObjectName('githubFeedbackButton')
        community.addWidget(self.feedback_button)
        body_layout.addWidget(self.community_card)

        library_card, library_body = card(
            _tr('本机预设库'),
            _tr('集中管理本机的准备、混音、调音与风格配方预设；只读写本地 JSON，不上传或自动应用。'))
        library_card.setObjectName('presetLibraryCard')
        library_body.addWidget(label(_tr('支持导入预览、复制内置预设、收藏和导出；混音与调音页可保存、预览并应用方案。'), 'muted'))
        library_row = QHBoxLayout()
        library_row.setSpacing(10)
        self.open_preset_button = button(_tr('打开本机预设库'), self.open_preset_library, 'primary')
        self.open_preset_button.setObjectName('openPresetLibraryButton')
        self.open_preset_button.setAccessibleName(_tr('打开本机预设库管理器'))
        library_row.addWidget(self.open_preset_button)
        library_row.addStretch()
        library_body.addLayout(library_row)
        body_layout.addWidget(library_card)

        updates, body = card(_tr('程序版本'), _tr('当前版本 · ') + updater.current_version())
        self.message = label(_tr('点击“检查更新”获取最新版本。'), 'muted')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
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
        body.addWidget(label(_tr('确认立即更新后，自动安装前倒计时 10 秒。开始其他操作会暂缓安装，也可手动选择“暂缓安装”。'), 'muted'))
        body_layout.addWidget(updates)
        body_layout.addStretch()

        self.application = QApplication.instance()
        self.application.aboutToQuit.connect(self.stop_watching)
        self.application.installEventFilter(self)
        pending = updater.pending_update()
        if pending:
            self.plan = pending['plan']
            self._pending_release={'version':pending['version'],'available':True,'update':None,
                'notes':self.saved_release_notes(pending['version']),
                'url':updater.RELEASES+'/tag/v'+pending['version'],'pending_plan':self.plan}
            self.action.setText(_tr('重启并安装更新'))
            self.message.setText(_tr('更新 ') + pending['version'] + _tr(' 已下载，重启后安装。'))
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
        if self.auto_check.isChecked():
            import os
            if not environment_value('VWA_TESTING'):
                QTimer.singleShot(1800, lambda: self.check(startup=True))

    def saved_release_notes(self,version):
        path=ROOT/'cache/updates/last-notice.json'
        try:
            if path.stat().st_size<=256*1024:
                data=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data,dict) and data.get('version')==version and isinstance(data.get('notes'),str):
                    return data['notes']
        except (OSError,ValueError):pass
        return _tr('更新文件已下载并通过校验；联网检查后可查看完整更新说明。')

    def copy_qq_group(self):
        clipboard=QApplication.clipboard()
        clipboard.setText(QQ_GROUP)
        if clipboard.text()==QQ_GROUP:
            self.message.setText(_tr('QQ群号已复制：{0}').format(QQ_GROUP))
        else:
            self.message.setText(_tr('无法写入剪贴板，请选择上方群号手动复制。'))

    def replay_tutorial(self):
        start=getattr(self.owner,'start_tutorial',None)
        if callable(start):start(force=True)

    def open_feedback(self):
        import webbrowser
        webbrowser.open(FEEDBACK_URL)

    def reset_update_reminder(self):
        try:startup_preferences.reset_reminder(ROOT)
        except (OSError,ValueError) as error:self.message.setText(str(error));return
        self._notice_shown.clear()
        self.message.setText(_tr('更新提醒已恢复。'))
        if self.release and self.release.get('available'):self.queue_notice(self.release,force=True)

    def queue_notice(self,release,*,force=False):
        if not isinstance(release,dict) or not release.get('available') or not release.get('version'):return
        version=release['version']
        updater.version_key(version)
        if not force and (version in self._notice_shown or not startup_preferences.reminder_due(
                version,startup_preferences.load_preferences(ROOT))):return
        self._queued_notice=dict(release);self._notice_force=force
        self.notice_timer.start(100)

    def maybe_show_notice(self):
        release=self._queued_notice
        if release is None or self._notice_dialog is not None:return
        if getattr(self.owner,'_startup_closing',False):
            self._queued_notice=None;self.notice_timer.stop();return
        tour=getattr(self.owner,'onboarding',None)
        owner=self.owner if isinstance(self.owner,QWidget) else None
        if (self.busy() or (tour is not None and tour.active) or QApplication.activeModalWidget() is not None
                or (owner is not None and (not owner.isVisible() or owner.isMinimized() or not owner.isActiveWindow()))):
            self.notice_timer.start(400);return
        if not self._notice_force and not startup_preferences.reminder_due(
                release['version'],startup_preferences.load_preferences(ROOT)):
            self._queued_notice=None;return
        from update_notice_ui import UpdateNoticeDialog
        self._queued_notice=None
        dialog=UpdateNoticeDialog(release,updater.current_version(),owner,downloaded=bool(release.get('pending_plan')))
        self._notice_dialog=dialog;self._notice_shown.add(release['version'])
        dialog.finished.connect(lambda result:self.notice_finished(dialog,release,result))
        dialog.open()

    def notice_finished(self,dialog,release,result):
        self._notice_dialog=None
        if getattr(self.owner,'_startup_closing',False):dialog.deleteLater();return
        if result==QDialog.Accepted:
            self.release=dict(release)
            self.install_requested=True;self.restart_deferred=False;self.startup_interrupted=False
            if hasattr(self.owner,'navigate'):self.owner.navigate('updates')
            if release.get('pending_plan'):
                self.plan=release['pending_plan'];self.queue_auto_restart()
                if not self.auto_restart.isChecked():self.primary_action()
            elif release.get('update'):
                self.plan=None;self.download()
            else:
                self.install_requested=False;self.open_release()
        else:
            self.install_requested=False;self.restart_deferred=True
            self.restart_timer.stop();self.restart_remaining=None
            try:startup_preferences.defer_reminder(ROOT,release['version'],dialog.defer_seconds)
            except (OSError,ValueError) as error:self.message.setText(_tr('更新提醒设置未保存：')+str(error))
        dialog.deleteLater()

    def dismiss_startup_notice(self):
        self.notice_timer.stop();self._queued_notice=None
        if self._notice_dialog is not None:self._notice_dialog.reject()

    def eventFilter(self, watched, event):
        if self.install_requested and event.type() in (QEvent.MouseButtonPress,QEvent.KeyPress,QEvent.Wheel):
            if isinstance(self.owner,QWidget) and isinstance(watched,QWidget):
                widget=watched
                while widget is not None and widget not in (self,self._notice_dialog,self.owner):widget=widget.parentWidget()
                if widget is self.owner:
                    self.startup_interrupted=True;self.restart_deferred=True;self.install_requested=False
                    self.restart_timer.stop();self.restart_remaining=None
                    if self.plan:
                        self.cancel.hide();self.message.setText(_tr('已开始其他操作，自动安装已暂缓；更新文件会保留。'))
        return False

    def stop_watching(self):
        self.application.removeEventFilter(self)
        self.notice_timer.stop()

    def closeEvent(self, event):
        self.dismiss_startup_notice()
        self.stop_watching()
        super().closeEvent(event)

    def save_settings(self):
        config = load_config(resolve_paths=False)
        config.update(download_source=self.source.currentData(), check_for_updates=self.auto_check.isChecked(),
                      download_workers=self.download_workers.currentData(),
                      auto_restart_updates=self.auto_restart.isChecked())
        config.pop('startup_auto_update',None)
        atomic_json(ROOT / 'config.json', config)
        self.owner.components.source.setCurrentIndex(self.owner.components.source.findData(self.source.currentData()))
        if not self.auto_restart.isChecked():
            self.restart_timer.stop()
            self.restart_remaining = None
            if self.plan:
                self.cancel.hide()
                self.message.setText(_tr('更新已校验，可点击“重启并安装更新”应用。'))

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def check(self, startup=False):
        if getattr(self.owner,'_startup_closing',False):return
        if self.busy():
            return
        if hasattr(self.owner,'storage') and self.owner.storage.busy():
            if startup:QTimer.singleShot(500,lambda:self.check(startup=True))
            else:self.message.setText(_tr('请等待缓存管理操作结束后再检查或下载更新。'))
            return
        self.startup_update = bool(startup)
        self.worker = UpdateWorker(self.source.currentData())
        self.worker.value.connect(self.checked)
        self.start_worker('正在检查更新…', downloading=False)

    def start_worker(self, message, downloading):
        if hasattr(self.owner, 'storage') and self.owner.storage.busy():
            self.message.setText(_tr('请等待缓存管理操作结束后再检查或下载更新。'))
            self.install_requested=False
            return False
        self._downloading=bool(downloading)
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
        return True

    def checked(self, release):
        self.release = dict(release)
        self.notes.setPlainText(str(release.get('notes','')))
        if release.get('version'):
            updater.version_key(release['version'])
            try:atomic_json(ROOT/'cache/updates/last-notice.json',{
                'version':release['version'],'notes':str(release.get('notes',''))[:60000]})
            except OSError:pass
        if self._pending_release:
            if release.get('version')==self._pending_release['version']:
                self.release['pending_plan']=self._pending_release['pending_plan']
                self.plan=self._pending_release['pending_plan']
            elif release.get('available'):
                self.plan=None
            else:
                self.release=dict(self._pending_release)
                self.plan=self.release['pending_plan']
                release=self.release;self.notes.setPlainText(release['notes'])
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
        if self.release.get('pending_plan'):
            self.action.setText(_tr('重启并安装更新'))
            self.message.setText(_tr('更新 {0} 已下载，确认后安装。').format(self.release['version']))
        if self.release.get('available'):
            self.queue_notice(self.release,force=not self.startup_update)

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
        self.restart_deferred = self.restart_deferred or self.startup_interrupted
        self.restart_remaining = None
        self.action.setText(_tr('重启并安装更新'))
        self.message.setText(_tr('下载完成，全部文件已校验。重启后安装更新。'))

    def failed(self, message):
        self.message.setText(message)
        if self._downloading:
            self.install_requested=False;self.restart_deferred=True
        if not self._downloading and self._pending_release:
            self.release=dict(self._pending_release);self.plan=self.release['pending_plan']
            self.queue_notice(self.release,force=not self.startup_update)

    def finished(self):
        self.action.setEnabled(True)
        self.source.setEnabled(True)
        self.cancel.hide()
        self._downloading=False
        self.queue_auto_restart()

    def queue_auto_restart(self):
        if (self.plan and self.install_requested and self.auto_restart.isChecked() and not self.restart_deferred):
            if hasattr(self.owner, 'navigate'):
                self.owner.navigate('updates')
            self.cancel.setText(_tr('暂缓安装'))
            self.cancel.setEnabled(True)
            self.cancel.show()
            self.restart_timer.start()

    def active_operations(self):
        return bool(self.busy() or active_ui_operations(self.owner))

    def maybe_auto_restart(self):
        if (not self.plan or not self.install_requested or not self.auto_restart.isChecked() or self.restart_deferred):
            self.restart_timer.stop()
            return
        if QApplication.activeModalWidget() is not None:
            self.restart_remaining=None;return
        if self.active_operations():
            self.restart_remaining = None
            if self.busy():
                return
            self.restart_deferred = True
            self.restart_timer.stop()
            self.message.setText(_tr('当前操作已开始，自动安装已暂缓。更新文件会保留，下次启动仍需确认安装。'))
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
        self.install_requested=False
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
            self.install_requested=False;self.restart_deferred=False;self.startup_interrupted=False
            self.download()
        else:
            self.check()

    def open_preset_library(self):
        from preset_library_ui import open_preset_library_manager
        return open_preset_library_manager(self)

    def open_release(self):
        import webbrowser
        webbrowser.open(self.release['url'] if self.release else updater.RELEASES + '/latest')
