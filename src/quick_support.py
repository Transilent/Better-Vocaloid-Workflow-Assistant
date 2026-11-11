"""Qt projection for settings, updates and the first-use tour."""

from pathlib import Path
import platform
import shutil

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication

import startup_preferences
import storage
import updater
from common import ROOT, job_directories, output_directory
from download_sources import read_small
from i18n import tr
from quick_work import WorkerLane
from support_info import FEEDBACK_URL, QQ_GROUP
from task_store import format_size, size
from workspace_settings import SettingsState


def _existing_directory(value):
    """Find a usable directory for disk-space reporting without touching files."""
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    for candidate in (path, *path.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _new_preset_library():
    """Construct the shared library lazily; construction alone writes nothing."""
    from preset_library import PresetLibrary

    return PresetLibrary()


def _preset_preview(path):
    """Validate one selected preset without writing to the user library."""
    from preset_documents import decode_document, document_digest, read_regular_bytes

    source = Path(path)
    raw = read_regular_bytes(source)
    document = decode_document(raw, allow_builtin=True)
    metadata = document.get('metadata') if isinstance(document.get('metadata'), dict) else {}
    name = metadata.get('name')
    if not isinstance(name, str) or not name.strip():
        name = document['id']
    return {
        'path': str(source),
        'source_name': source.name,
        'sha256': document_digest(raw),
        'id': document['id'],
        'version': document['version'],
        'type': document['type'],
        'name': name,
        'builtin': document['id'].startswith('builtin.'),
        'copy_required': document['id'].startswith('builtin.'),
        'bytes': len(raw),
    }


def _scan_storage(output_path=None):
    """Return the Widgets storage service's protected, disposable cache view."""
    rows = storage.candidates(include_protected=True)
    directory = _existing_directory(output_path or output_directory())
    free = shutil.disk_usage(directory).free if directory is not None else None
    jobs = job_directories()
    return {
        'rows': [{
            'kind': str(row['kind']),
            'title': str(row['title']),
            'path': str(row['path']),
            'bytes': int(row['bytes']),
            'size': format_size(row['bytes']),
            'protected': str(row.get('protected', '')),
        } for row in rows],
        'free': free,
        'free_label': format_size(free) if free is not None else tr('输出盘不可用'),
        'tasks': sum(size(job) for job in jobs),
        'publication': size(ROOT / 'publish-packages'),
        'dependencies': size(ROOT / 'dependencies') + size(ROOT / 'models'),
    }


class QuickSupport(QObject):
    settingsChanged = Signal()
    startupChanged = Signal()
    workChanged = Signal()
    diagnosticsChanged = Signal()
    storageChanged = Signal()
    presetLibraryChanged = Signal()

    def __init__(self, owner, worker, sources):
        super().__init__(owner)
        self.owner = owner
        self.worker = worker
        self.discovery = WorkerLane(self, 'vwa-update-discovery')
        self.storage_lane = WorkerLane(self, 'vwa-storage-management')
        self.preset_lane = WorkerLane(self, 'vwa-preset-library')
        self.state = SettingsState(sources)
        self.sources = sources
        self.version = updater.current_version()
        self._can_apply_updates = self.state.installed_quick_frontend()
        self.worker.changed.connect(self.workChanged)
        self.discovery.changed.connect(self.workChanged)
        self._auto_check = False
        self._diagnostics = {'open': False, 'text': ''}
        self._storage_view = {
            'busy': False,
            'scanned': False,
            'rows': [],
            'free': None,
            'free_label': '',
            'tasks_label': '0 B',
            'publication_label': '0 B',
            'dependencies_label': '0 B',
            'message': '点击“扫描占用”查看磁盘和可清理缓存。',
            'error': '',
            'freed': 0,
            'freed_label': '',
            'generation': 0,
        }
        self._preset_library = None
        self._preset_pending_import = None
        self._preset_request = None
        self._preset_view = {
            'busy': False,
            'loaded': False,
            'revision': None,
            'entries': [],
            'message': '正在读取本机预设库…',
            'error': '',
            'error_code': '',
            'conflict': False,
            'import_preview': {},
            'last_entry_key': '',
        }

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

    @Property('QVariantMap', notify=storageChanged)
    def storage(self):
        return dict(self._storage_view)

    @Property('QVariantMap', notify=presetLibraryChanged)
    def presetLibrary(self):
        return dict(self._preset_view)

    @Property('QVariantList', constant=True)
    def downloadSources(self):
        return [{'label': tr(label), 'value': value} for label, value in self.sources]

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def _error(self, error):
        self.state.update.update(update_state='failed', message=str(error)[:1600])
        self.startupChanged.emit()

    def _storage_failed(self, error, prefix):
        self._storage_view.update(
            busy=False,
            message='',
            error=prefix + str(error)[:1600],
        )
        self.storageChanged.emit()

    @Slot()
    def refreshStorage(self):
        if self.storage_lane.kind:
            return
        self._storage_view.update(
            busy=True,
            message='正在扫描磁盘和可清理缓存…',
            error='',
            freed=0,
            freed_label='',
        )
        self.storageChanged.emit()
        output_path = str(self.state.values.get('output_directory', '') or '')

        def work(stop, _send):
            value = _scan_storage(output_path)
            if stop.is_set():
                raise InterruptedError
            return value

        if not self.storage_lane.start('storage-scan', work, self._storage_scanned):
            self._storage_failed(ValueError('缓存扫描已在运行。'), '扫描未开始：')

    def _storage_scanned(self, value, error):
        if isinstance(error, InterruptedError):
            self._storage_view.update(busy=False, message='扫描已取消。')
            self.storageChanged.emit()
            return
        if error is not None:
            self._storage_failed(error, '扫描未完成：')
            return
        try:
            rows = [dict(row) for row in value['rows']]
            generation = int(self._storage_view.get('generation', 0)) + 1
            self._storage_view.update(
                busy=False,
                scanned=True,
                rows=rows,
                free=value.get('free'),
                free_label=str(value.get('free_label', '')),
                tasks_label=format_size(value.get('tasks', 0)),
                publication_label=format_size(value.get('publication', 0)),
                dependencies_label=format_size(value.get('dependencies', 0)),
                message=f'扫描完成：{len(rows)} 项；其中 {sum(bool(row.get("protected")) for row in rows)} 项正在使用，不能清理。',
                error='',
                generation=generation,
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._storage_failed(exc, '缓存列表无效：')
            return
        self.storageChanged.emit()

    @Slot('QVariantList')
    def cleanStorage(self, paths):
        if self.storage_lane.kind:
            return
        calibration = getattr(self.owner, 'calibration', None)
        if self.worker.kind or (calibration is not None and calibration.view['busy']):
            self._storage_failed(ValueError('请等待当前工作区任务完成。'), '清理未开始：')
            return
        selected = []
        seen = set()
        for value in list(paths or []):
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                selected.append(text)
        if not selected:
            self._storage_view.update(error='请先选择可清理缓存。')
            self.storageChanged.emit()
            return
        self._storage_view.update(
            busy=True,
            message=f'正在清理 {len(selected)} 项；会再次核验引用与运行状态…',
            error='',
            freed=0,
            freed_label='',
        )
        self.storageChanged.emit()

        def work(stop, _send):
            if stop.is_set():
                raise InterruptedError
            return storage.clean(selected)

        if not self.worker.start('storage-clean', work, self._storage_cleaned):
            self._storage_failed(ValueError('缓存清理已在运行。'), '清理未开始：')

    def _storage_cleaned(self, value, error):
        if isinstance(error, InterruptedError):
            self._storage_view.update(busy=False, message='清理已取消。')
            self.storageChanged.emit()
            return
        if error is not None:
            self._storage_failed(error, '清理未完成：')
            return
        freed = int(value or 0)
        generation = int(self._storage_view.get('generation', 0)) + 1
        self._storage_view.update(
            busy=False,
            scanned=False,
            rows=[],
            free=None,
            free_label='',
            freed=freed,
            freed_label=format_size(freed),
            message=f'清理完成，释放 {format_size(freed)}。可再次扫描核对剩余占用。',
            error='',
            generation=generation,
        )
        self.storageChanged.emit()

    def _library(self):
        if self._preset_library is None:
            self._preset_library = _new_preset_library()
        return self._preset_library

    @staticmethod
    def _entry_key(entry):
        if not isinstance(entry, dict):
            return ''
        return f"{entry.get('id', '')}|{entry.get('version', '')}"

    @staticmethod
    def _error_code(error):
        return str(getattr(error, 'code', '') or '')

    def _preset_error_parts(self, error):
        code = self._error_code(error)
        text = str(error)[:1600] or '预设库操作未完成。'
        if code:
            text += f' · {code}'
        if code == 'preset_version_conflict':
            text += '\n同一 ID 与版本已有不同内容；本机已有预设和导入文件均未覆盖。可关闭，或明确创建独立副本后重试。'
        elif code in ('library_stale', 'library_busy'):
            text += '\n预设库状态已变化或正被占用；列表需要刷新后再决定。'
        elif code == 'file_changed':
            text += '\n预览后文件内容发生变化；请重新选择并预览。'
        return code, text

    def _set_preset_error(self, error):
        code, text = self._preset_error_parts(error)
        self._preset_view.update(
            busy=False,
            error=text,
            error_code=code,
            conflict=code == 'preset_version_conflict',
        )
        self.presetLibraryChanged.emit()

    @Slot()
    def refreshPresetLibrary(self):
        if self.preset_lane.kind:
            return
        try:
            library = self._library()
        except Exception as exc:
            self._set_preset_error(exc)
            return
        self._preset_view.update(
            busy=True,
            message='正在读取本机预设库…',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()
        if not self.preset_lane.start(
                'preset-refresh', lambda _stop, _send: library.snapshot(), self._preset_refreshed):
            self._set_preset_error(ValueError('预设库正在执行其他操作。'))

    def _preset_refreshed(self, snapshot, error):
        if error is not None:
            self._set_preset_error(error)
            return
        try:
            entries = [dict(entry) for entry in snapshot.get('entries', [])]
            revision = snapshot.get('revision')
        except (AttributeError, TypeError, ValueError) as exc:
            self._set_preset_error(exc)
            return
        self._preset_view.update(
            busy=False,
            loaded=True,
            revision=revision,
            entries=entries,
            message=f'已载入 {len(entries)} 个预设；四类预设共享同一本机库。',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()

    def _start_preset(self, kind, operation, success):
        if self.preset_lane.kind:
            return False
        if not self._preset_view.get('loaded'):
            self._set_preset_error(ValueError('请先载入预设库。'))
            return False
        revision = self._preset_view.get('revision')
        try:
            library = self._library()
        except Exception as exc:
            self._set_preset_error(exc)
            return False
        self._preset_request = {'kind': kind, 'success': success}
        self._preset_view.update(
            busy=True,
            message='正在执行预设库操作…',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()

        def work(_stop, _send):
            result = operation(library, revision)
            return {'result': result, 'snapshot': library.snapshot()}

        if not self.preset_lane.start(kind, work, self._preset_finished):
            self._preset_request = None
            self._set_preset_error(ValueError('预设库正在执行其他操作。'))
            return False
        return True

    def _preset_finished(self, value, error):
        request = self._preset_request or {}
        self._preset_request = None
        if error is not None:
            code = self._error_code(error)
            keep_import = (
                request.get('kind') == 'import'
                and code in ('preset_version_conflict', 'library_stale', 'library_busy')
            )
            if not keep_import:
                self._preset_pending_import = None
                self._preset_view['import_preview'] = {}
            self._set_preset_error(error)
            return
        try:
            result = value.get('result') if isinstance(value, dict) else {}
            snapshot = value['snapshot']
            entries = [dict(entry) for entry in snapshot.get('entries', [])]
            revision = snapshot.get('revision')
            result = result if isinstance(result, dict) else {}
            entry = result.get('entry') if isinstance(result.get('entry'), dict) else {}
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_preset_error(exc)
            return
        kind = request.get('kind', '')
        success = str(request.get('success', '预设库已更新。'))
        name = str(entry.get('name', '') or '')
        if kind == 'import':
            if result.get('copied'):
                success = f'已作为独立本机副本附加：{name}'
            elif result.get('already_present'):
                success = f'同一预设版本已存在，未重复写入：{name}'
            else:
                success = f'已附加到本机预设库：{name}'
        elif kind == 'copy':
            success = f'已复制为本机预设：{name}'
        elif kind == 'rename':
            success = f'本机别名已更新：{name}'
        elif kind == 'favorite':
            success = ('已收藏：' if entry.get('favorite') else '已取消收藏：') + name
        self._preset_pending_import = None
        self._preset_view.update(
            busy=False,
            loaded=True,
            revision=revision,
            entries=entries,
            message=success,
            error='',
            error_code='',
            conflict=False,
            import_preview={},
            last_entry_key=self._entry_key(entry),
        )
        self.presetLibraryChanged.emit()

    @Slot(QUrl, result=bool)
    def previewPresetImport(self, url):
        if self.preset_lane.kind:
            self._set_preset_error(ValueError('请等待当前预设库操作结束。'))
            return False
        if not url.isLocalFile():
            self._set_preset_error(ValueError('请选择本机预设文件。'))
            return False
        try:
            preview = _preset_preview(url.toLocalFile())
        except Exception as exc:
            self._preset_pending_import = None
            self._preset_view['import_preview'] = {}
            self._set_preset_error(exc)
            return False
        self._preset_pending_import = {
            'path': preview['path'],
            'sha256': preview['sha256'],
        }
        self._preset_view.update(
            import_preview=preview,
            message='导入预览已通过校验；尚未写入本机库。',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()
        return True

    @Slot()
    def cancelPresetImport(self):
        self._preset_pending_import = None
        self._preset_view['import_preview'] = {}
        self.presetLibraryChanged.emit()

    @Slot(bool, result=bool)
    def importPreset(self, asCopy=False):
        pending = self._preset_pending_import
        if not isinstance(pending, dict):
            self._set_preset_error(ValueError('没有待导入的预设预览。'))
            return False

        def operation(library, revision):
            return library.import_file(
                pending['path'],
                expected_revision=revision,
                expected_sha256=pending['sha256'],
                as_copy=bool(asCopy),
            )

        return self._start_preset('import', operation, '已附加到本机预设库。')

    @Slot(str, str, str, result=bool)
    def copyPreset(self, identifier, version, name):
        label = str(name).strip()
        if not label:
            self._set_preset_error(ValueError('副本名称不能为空。'))
            return False
        return self._start_preset(
            'copy',
            lambda library, revision: library.copy(
                identifier, version, name=label, expected_revision=revision),
            '已复制内置预设。',
        )

    @Slot(str, str, str, result=bool)
    def renamePreset(self, identifier, version, alias):
        label = str(alias).strip()
        return self._start_preset(
            'rename',
            lambda library, revision: library.rename(
                identifier, version, alias=label, expected_revision=revision),
            '本机别名已更新。',
        )

    @Slot(str, str, bool, result=bool)
    def setPresetFavorite(self, identifier, version, favorite):
        return self._start_preset(
            'favorite',
            lambda library, revision: library.set_favorite(
                identifier, version, favorite=bool(favorite), expected_revision=revision),
            '收藏状态已更新。',
        )

    @Slot(str, str, result=bool)
    def removePreset(self, identifier, version):
        return self._start_preset(
            'remove',
            lambda library, revision: library.delete(
                identifier, version, expected_revision=revision),
            '已从本机活动列表移除；不可变文件与上一索引仍保留。',
        )

    @Slot(str, str, QUrl, result=bool)
    def exportPreset(self, identifier, version, url):
        if self.preset_lane.kind:
            return False
        if not url.isLocalFile():
            self._set_preset_error(ValueError('请选择本机导出位置。'))
            return False
        try:
            library = self._library()
        except Exception as exc:
            self._set_preset_error(exc)
            return False
        destination = url.toLocalFile()
        self._preset_request = {'kind': 'export', 'success': '预设已导出。'}
        self._preset_view.update(
            busy=True,
            message='正在导出原始预设文件；不会覆盖已有文件…',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()
        if not self.preset_lane.start(
                'preset-export',
                lambda _stop, _send: library.export_file(identifier, version, destination),
                self._preset_exported):
            self._preset_request = None
            self._set_preset_error(ValueError('预设库正在执行其他操作。'))
            return False
        return True

    def _preset_exported(self, destination, error):
        self._preset_request = None
        if error is not None:
            self._set_preset_error(error)
            return
        self._preset_view.update(
            busy=False,
            message=f'已导出原始预设文件：{destination}',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()

    @Slot(result=bool)
    def recoverPresetLibrary(self):
        def operation(library, _revision):
            return library.recover_previous()

        # Recovery deliberately ignores the visible revision and requires an
        # explicit UI confirmation before reaching this slot.
        if self.preset_lane.kind:
            return False
        try:
            library = self._library()
        except Exception as exc:
            self._set_preset_error(exc)
            return False
        self._preset_request = {
            'kind': 'recover',
            'success': '已恢复上一份有效索引；预设未自动应用。',
        }
        self._preset_view.update(
            busy=True,
            message='正在恢复并重新核验上一索引…',
            error='',
            error_code='',
            conflict=False,
        )
        self.presetLibraryChanged.emit()

        def work(_stop, _send):
            result = operation(library, None)
            return {'result': result, 'snapshot': library.snapshot()}

        if not self.preset_lane.start('preset-recover', work, self._preset_finished):
            self._preset_request = None
            self._set_preset_error(ValueError('预设库正在执行其他操作。'))
            return False
        return True

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
        self.storage_lane.close()
        self.preset_lane.close()

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
