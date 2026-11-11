"""Qt adapter for the shared workspace state and real component services."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QTimer, Qt, QUrl, Signal, Slot

import component_operations as components
from i18n import tr
from workspace_controller import WorkspaceController


class ComponentRows(QAbstractListModel):
    _roles = ('componentId', 'title', 'summary', 'status', 'statusText')
    _keys = ('id', 'title', 'summary', 'status', 'status_text')
    _first = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = ()

    def replace_rows(self, rows):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def roleNames(self):
        return {self._first + index: name.encode() for index, name in enumerate(self._roles)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row['title']
        if self._first <= role < self._first + len(self._keys):
            return row.get(self._keys[role - self._first])
        return None


def _operation_result(request, stop, progress):
    try:
        if request.action == 'install':
            components.install(request.component_id, runtime=request.runtime, source=request.source,
                               force=request.force, progress=progress, check=stop.is_set)
            return 'succeeded', '安装完成。'
        report = components.verify(request.component_id, progress=progress, check=stop.is_set)
    except InterruptedError:
        return 'cancelled', '操作已取消。已安装组件和可续传的数据按原规则保留。'
    except Exception as exc:
        return 'failed', str(exc)[:1600] or exc.__class__.__name__
    if report.get('verified') is True:
        count = report.get('progress', {}).get('sha256_checked')
        return 'succeeded', f'完整校验通过，已核对 {count} 个文件。' if count else '组件完整校验通过。'
    if report.get('state') == 'cancelled':
        return 'cancelled', '校验已取消。'
    problems = report.get('corrupted') or report.get('errors') or report.get('unverified') or []
    detail = next((row.get('message') or row.get('path') for row in problems if isinstance(row, dict)), '')
    return 'failed', f"未通过完整校验：{report.get('message') or detail or report.get('state', '未知状态')}"


class QuickBridge(QObject):
    navigationChanged = Signal()
    draftChanged = Signal()
    selectionChanged = Signal()
    queryChanged = Signal()
    optionsChanged = Signal()
    activityChanged = Signal()
    closeReady = Signal()

    def __init__(self):
        super().__init__()
        self.state = WorkspaceController(components.download_source_options())
        self._rows = ComponentRows(self)
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vwa-quick-components')
        self._future = None
        self._kind = None
        self._stop = None
        self._latest = None
        self._progress_lock = Lock()
        self._exit_when_idle = False
        self._closed = False
        self._notice = ''
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    @Property(str, notify=navigationChanged)
    def route(self):
        return self.state.route

    @Property('QVariantList', constant=True)
    def routes(self):
        return [{'pageId': spec.page_id, 'title': tr(spec.title)} for spec in self.state.routes]

    @Property('QVariantMap', notify=draftChanged)
    def preparation(self):
        return self.state.draft_view

    @Property(QObject, constant=True)
    def componentModel(self):
        return self._rows

    @Property('QVariantMap', notify=selectionChanged)
    def selected(self):
        return dict(self.state.selected)

    @Property(str, notify=selectionChanged)
    def selectedId(self):
        return self.state.selected_id

    @Property(str, notify=queryChanged)
    def query(self):
        return self.state.query

    @Property('QVariantList', constant=True)
    def downloadSources(self):
        return [{'label': label, 'value': value} for label, value in self.state.source_options]

    @Property(str, notify=optionsChanged)
    def downloadSource(self):
        return self.state.source

    @Property(str, notify=optionsChanged)
    def runtime(self):
        return self.state.runtime

    @Property(bool, notify=activityChanged)
    def busy(self):
        return self.state.active is not None

    @Property(bool, notify=activityChanged)
    def loading(self):
        return self._kind == 'scan'

    @Property('QVariantMap', notify=activityChanged)
    def operation(self):
        return dict(self.state.operation)

    @Property(str, notify=activityChanged)
    def notice(self):
        return tr(self._notice)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def _update_rows(self):
        self._rows.replace_rows(self.state.visible_rows)
        self.selectionChanged.emit()

    @Slot(str)
    def navigate(self, destination):
        if self.state.navigate(destination):
            self.navigationChanged.emit()

    @Slot()
    def returnToPreparation(self):
        if self.state.return_to_preparation():
            self.navigationChanged.emit()

    @Slot(QUrl)
    def chooseMusic(self, url):
        try:
            if url.isEmpty() or url.isLocalFile():
                if self.state.set_music(url.toLocalFile()):
                    self.draftChanged.emit()
        except (OSError, ValueError) as exc:
            self._notice = str(exc)
            self.activityChanged.emit()

    @Slot()
    def clearMusic(self):
        self.chooseMusic(QUrl())

    @Slot(str)
    def setQuery(self, query):
        if self.state.set_query(query):
            self._update_rows()
            self.queryChanged.emit()

    @Slot(str)
    def selectComponent(self, component_id):
        if self.state.select(component_id):
            self.selectionChanged.emit()

    @Slot(str)
    def setRuntime(self, runtime):
        if not self.loading and self.state.set_runtime(runtime):
            self.optionsChanged.emit()
            self.refresh()

    @Slot(str)
    def setDownloadSource(self, source):
        if self.state.set_download_source(source):
            self.optionsChanged.emit()

    @Slot()
    def useComponent(self):
        if not self.loading and self.state.use_component():
            self.draftChanged.emit()
            self.navigationChanged.emit()

    @Slot()
    def refresh(self):
        if self._future is not None or self._closed:
            return
        runtime = self.state.runtime
        self._notice = ''
        self._kind = 'scan'
        self._future = self._workers.submit(components.catalog, runtime)
        self._scan_runtime = runtime
        self._timer.start()
        self.activityChanged.emit()

    @Slot()
    def installSelected(self):
        self._start('install', force=bool(self.state.selected.get('can_use')))

    @Slot()
    def verifySelected(self):
        self._start('verify')

    def _start(self, action, *, force=False):
        if self._future is not None or self._closed:
            return
        request = self.state.begin(action, force=force)
        if request is None:
            return
        self._stop = Event()
        self._notice = ''
        self._kind = 'operation'

        def progress(done, total, message):
            with self._progress_lock:
                self._latest = request.operation_id, done, total, message

        self._future = self._workers.submit(_operation_result, request, self._stop, progress)
        self._timer.start()
        self.activityChanged.emit()

    @Slot()
    def cancelOperation(self):
        if self.state.cancel():
            self._stop.set()
            self.activityChanged.emit()

    @Slot()
    def cancelAndClose(self):
        self._exit_when_idle = True
        if self.busy:
            self.cancelOperation()
        else:
            self.closeReady.emit()

    @Slot()
    def _tick(self):
        with self._progress_lock:
            progress = self._latest
            self._latest = None
        if progress and self.state.progress(*progress):
            self.activityChanged.emit()
        if self._future is None or not self._future.done():
            return
        future, kind = self._future, self._kind
        self._future = self._kind = None
        self._timer.stop()
        if kind == 'scan':
            try:
                if self.state.set_catalog(self._scan_runtime, future.result()):
                    self._update_rows()
            except Exception as exc:
                self.state.set_catalog(self._scan_runtime, ())
                self.state.rows_runtime = None
                self._notice = f'无法读取组件状态：{exc}'
                self._update_rows()
            self.activityChanged.emit()
            return
        request = self.state.active
        try:
            status, message = future.result()
        except Exception as exc:
            status, message = 'failed', str(exc)
        self.state.complete(request.operation_id, status, message)
        self._stop = None
        if self._exit_when_idle:
            self.activityChanged.emit()
            self.closeReady.emit()
        else:
            self.refresh()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if self._stop:
            self._stop.set()
        self._workers.shutdown(wait=True, cancel_futures=True)
