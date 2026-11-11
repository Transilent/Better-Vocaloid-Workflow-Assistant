"""Qt projection for settings, updates and the first-use tour."""

from pathlib import Path
import platform

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication

import startup_preferences
import updater
from common import ROOT
from download_sources import read_small
from i18n import tr
from quick_work import WorkerLane
from support_info import FEEDBACK_URL, QQ_GROUP
from workspace_settings import SettingsState


class QuickSupport(QObject):
    settingsChanged = Signal()
    startupChanged = Signal()
    workChanged = Signal()
    diagnosticsChanged = Signal()

    def __init__(self, owner, worker, sources):
        super().__init__(owner)
        self.owner = owner
        self.worker = worker
        self.discovery = WorkerLane(self, 'vwa-update-discovery')
        self.state = SettingsState(sources)
        self.sources = sources
        self.version = updater.current_version()
        self._can_apply_updates = self.state.installed_quick_frontend()
        self.worker.changed.connect(self.workChanged)
        self.discovery.changed.connect(self.workChanged)
        self._auto_check = False
        self._diagnostics = {'open': False, 'text': ''}

    @Property('QVariantMap', notify=settingsChanged)
    def settings(self):
        return dict(self.state.values)

    @Property('QVariantMap', notify=startupChanged)
    def startup(self):
        return self.state.startup_view

    @Property(str, constant=True)
    def qqGroup(self):
        return QQ_GROUP

    @Property(str, constant=True)
    def currentVersion(self):
        return self.version

    @Property(bool, constant=True)
    def canApplyProgramUpdates(self):
        return self._can_apply_updates

    @Property(bool, notify=workChanged)
    def workBusy(self):
        return bool(self.worker.kind or self.discovery.kind)

    @Property('QVariantMap', notify=diagnosticsChanged)
    def diagnostics(self):
        return dict(self._diagnostics)

    @Property('QVariantList', constant=True)
    def downloadSources(self):
        return [{'label': tr(label), 'value': value} for label, value in self.sources]

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def _error(self, error):
        self.state.update.update(update_state='failed', message=str(error)[:1600])
        self.startupChanged.emit()

    @Slot(str, 'QVariant')
    def setSetting(self, name, value):
        if self.worker.kind in ('component-install', 'update-download') and name in ('download_source', 'download_workers'):
            return
        try:
            if self.state.save(name, value):
                if name == 'download_source':
                    self.owner.set_component_source(value)
                self.settingsChanged.emit()
        except (OSError, ValueError, TypeError) as exc:
            self._error(exc)

    @Slot(QUrl)
    def chooseOutput(self, url):
        self.owner.set_default_output_directory(url)

    def set_output(self, path):
        self.state.values['output_directory'] = path
        self.settingsChanged.emit()

    def begin_startup(self):
        if startup_preferences.tour_due(self.state.preferences):
            self.showTutorial()
        self._auto_check = self.state.values['check_for_updates']
        if self._auto_check:
            QTimer.singleShot(800, self._startup_check)

    def _startup_check(self):
        if not self._auto_check or not self.state.values['check_for_updates']:
            return
        self._auto_check = False
        self._check(startup=True)

    @Slot()
    def checkForUpdates(self):
        self._auto_check = False
        self._check(startup=False)

    def _check(self, *, startup):
        if self.discovery.kind or self.worker.kind == 'update-download':
            return
        self.state.update.update(update_state='checking', message='正在检查更新…', done=0, total=0)
        self.startupChanged.emit()
        source = self.state.values['download_source']

        def complete(release, error):
            if error is not None:
                self._error(error)
                return
            try:
                self.state.set_release(release, startup=startup)
            except (OSError, ValueError, TypeError) as exc:
                self._error(exc)
                return
            self.startupChanged.emit()

        self.discovery.start('update-check', lambda stop, _send: updater.check_release(
            source, reader=lambda url, route: read_small(url, route, timeout=5, check=stop.is_set)), complete)

    def close(self):
        self._auto_check = False
        self.discovery.close()

    @Slot(int)
    def deferUpdate(self, seconds):
        try:
            self.state.defer(seconds)
            self.startupChanged.emit()
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot()
    def dismissUpdate(self):
        self.deferUpdate(0)

    @Slot()
    def resetUpdateReminder(self):
        try:
            self.state.preferences = startup_preferences.reset_reminder(ROOT)
            self.state.update.update(message='已恢复版本提醒。')
            self.startupChanged.emit()
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot()
    def downloadUpdate(self):
        if self.workBusy or not self.state.release.get('update') or not self.state.installed_quick_frontend():
            return
        release = dict(self.state.release)
        source = self.state.values['download_source']
        self.state.update.update(update_open=False, update_state='downloading', done=0, total=0,
                                 message='正在下载程序更新…')
        self.startupChanged.emit()

        def work(stop, send):
            return updater.prepare_update(release, source, lambda done, total, message: send((done, total, message)),
                                          check=stop.is_set)

        def progress(value):
            if self.state.update['update_state'] == 'downloading':
                done, total, message = value
                self.state.update.update(done=done, total=total, message=message)
                self.startupChanged.emit()

        def complete(plan, error):
            if isinstance(error, InterruptedError):
                self.state.update.update(update_state='cancelled', message='已取消更新下载。')
            elif error is not None:
                self._error(error)
                return
            else:
                self.state.update_plan = Path(plan)
                self.state.update.update(update_state='downloaded', message='下载完成，可重启安装更新。')
            self.startupChanged.emit()
            if error is None and self.state.values['auto_restart_updates']:
                self.applyUpdate()

        self.worker.start('update-download', work, complete, progress)

    @Slot()
    def cancelUpdate(self):
        if self.worker.kind == 'update-download':
            self.state.update.update(update_state='cancelling', message='正在取消更新下载…')
            self.worker.cancel()
            self.startupChanged.emit()

    @Slot()
    def applyUpdate(self):
        if self.workBusy or self.state.update_plan is None or not self.state.installed_quick_frontend():
            return
        try:
            updater.restart_to_apply(self.state.update_plan)
            self.owner.closeReady.emit()
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot()
    def openRelease(self):
        version = self.state.release.get('version')
        url = updater.RELEASES + (f'/tag/v{version.removeprefix("v")}' if version else '/latest')
        QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def openFeedback(self):
        QDesktopServices.openUrl(QUrl(FEEDBACK_URL))

    @Slot()
    def copyCommunity(self):
        QGuiApplication.clipboard().setText(QQ_GROUP)

    @Slot()
    def showDiagnostics(self):
        lines = (
            f'VWA: {self.version}', f'Qt Quick: {qVersion()}',
            f'{tr("系统")}: {platform.system()} {platform.release()}',
            f'{tr("界面语言")}: {self.state.values["ui_language"]}',
            f'{tr("界面缩放")}: {self.state.values["ui_scale"]}%',
            f'{tr("准备任务状态")}: {self.owner.preparationTask["state"]}',
            f'{tr("更新状态")}: {self.state.update["update_state"]}',
        )
        self._diagnostics = {'open': True, 'text': '\n'.join(lines)}
        self.diagnosticsChanged.emit()

    @Slot()
    def closeDiagnostics(self):
        self._diagnostics['open'] = False
        self.diagnosticsChanged.emit()

    @Slot()
    def copyDiagnostics(self):
        if self._diagnostics['open']:
            QGuiApplication.clipboard().setText(self._diagnostics['text'])

    @Slot()
    def showTutorial(self):
        self.state.show_tour()
        self.startupChanged.emit()

    def _finish_tutorial(self, outcome):
        try:
            self.state.finish_tour(outcome)
        except (OSError, ValueError) as exc:
            self.state.tour_open = False
            self._error(exc)
        self.startupChanged.emit()

    @Slot()
    def advanceTutorial(self):
        if not self.state.tour_open:
            return
        if self.state.tour_step >= 3:
            self._finish_tutorial('completed')
        else:
            self.state.tour_step += 1
            self.startupChanged.emit()

    @Slot()
    def skipTutorial(self):
        if self.state.tour_open:
            self._finish_tutorial('skipped')
