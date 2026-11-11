"""Qt projections of the shared workspace, task library and worker commands."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import component_operations as components
from common import ROOT, output_directory, remember_job, save_output_directory
from i18n import tr
import preparation_commands as preparation
import quick_results
from quick_media_handoff import artifact_identity, verify_publication_bundle, verify_task_artifact
from quick_publication import QuickPublication
from quick_subtitles import QuickSubtitles, read_task_midi
from quick_support import QuickSupport
from quick_calibration import QuickCalibration
from quick_preparation_sources import QuickPreparationSources
from quick_tuning import QuickTuning
from quick_mixdown import QuickMixdown
from quick_video import QuickVideo
from quick_work import WorkerLane
from workspace_controller import WorkspaceController
import workflow


_TASK_STATE_FILTERS = frozenset(('all', 'done', 'pending', 'failed', 'running'))
_TASK_SORTS = frozenset(('recent', 'title'))
_PENDING_TASK_STATES = frozenset(('ready', 'partial', 'cancelled', 'interrupted'))


class RowsModel(QAbstractListModel):
    _first = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent, roles):
        super().__init__(parent)
        self.roles = tuple(roles)
        self.rows = ()

    def replace_rows(self, rows):
        rows = tuple(rows)
        if rows == self.rows:
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def roleNames(self):
        return {self._first + index: name.encode() for index, (name, _) in enumerate(self.roles)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row['title']
        if self._first <= role < self._first + len(self.roles):
            return row.get(self.roles[role - self._first][1])
        return None


def _component_result(request, stop, send):
    def progress(done, total, message):
        send((request.operation_id, done, total, message))

    if request.action == 'install':
        components.install(request.component_id, runtime=request.runtime, source=request.source,
                           force=request.force, progress=progress, check=stop.is_set)
        return 'succeeded', '安装完成。', None
    report = components.verify(request.component_id, progress=progress, check=stop.is_set)
    if report.get('verified') is True:
        count = report.get('progress', {}).get('sha256_checked')
        return ('succeeded', tr('完整校验通过，已核对 {0} 个文件。', count) if count else '组件完整校验通过。',
                report)
    if report.get('state') == 'cancelled':
        return 'cancelled', '校验已取消。', report
    problems = report.get('corrupted') or report.get('errors') or report.get('unverified') or []
    detail = next((row.get('message') or row.get('path') for row in problems if isinstance(row, dict)), '')
    return 'failed', f"未通过完整校验：{report.get('message') or detail or report.get('state', '未知状态')}", report


def _run_preparation(draft, existing, retry_cpu, stop, send, *, reuse_job=''):
    from task_store import known_job
    from error_recovery import failure

    if stop.is_set():
        raise InterruptedError('已取消任务。')
    job = (known_job(existing) if existing else
           preparation.create_from_draft(draft, reuse_job=reuse_job) if reuse_job else preparation.create_from_draft(draft))
    send({'state': 'running', 'job': str(job), 'message': '正在启动准备任务…'})
    try:
        code = preparation.run_process(job, stop=stop, on_progress=send, retry_cpu=retry_cpu)
        view = preparation.progress_view(job)
        if code and view['state'] not in ('cancelled', 'failed'):
            view.update(state='failed', message=f'准备进程退出（{code}）。请查看任务日志。')
        if view['state'] == 'failed':
            recovery = failure(job)
            view.update(message=(recovery.get('cause') or view['message'] or recovery['raw'])[:1600],
                        recovery_action=recovery.get('action', ''))
        elif view['state'] == 'cancelled':
            view['message'] = '准备已取消，可以继续此任务。'
        elif view['state'] in ('done', 'partial'):
            view['message'] = '准备完成。' if view['state'] == 'done' else '本轮阶段完成，可以继续。'
        return view
    except Exception as exc:
        return {'job': str(job), 'state': 'failed', 'message': str(exc)[:1600]}


class QuickBridge(QObject):
    verify_task_artifact = staticmethod(verify_task_artifact)
    verify_publication_bundle = staticmethod(verify_publication_bundle)

    navigationChanged = Signal()
    draftChanged = Signal()
    selectionChanged = Signal()
    queryChanged = Signal()
    optionsChanged = Signal()
    activityChanged = Signal()
    preparationTaskChanged = Signal()
    taskChanged = Signal()
    subtitlesRequested = Signal()
    closeReady = Signal()

    def __init__(self):
        super().__init__()
        sources = components.download_source_options()
        self.state = WorkspaceController(sources)
        self._work = WorkerLane(self, 'vwa-workspace')
        self._catalog = WorkerLane(self, 'vwa-components')
        self._read = WorkerLane(self, 'vwa-task-library')
        self._details = WorkerLane(self, 'vwa-task-detail')
        self._work.changed.connect(self._work_changed)
        self._catalog.changed.connect(self.activityChanged)
        self._read.changed.connect(self.taskChanged)
        self._details.changed.connect(self.taskChanged)
        self._support = QuickSupport(self, self._work, sources)
        self._tuning = QuickTuning(self, self._work)
        self._calibration = QuickCalibration(self, self._tuning)
        self._calibration.worker.changed.connect(self._work_changed)
        self._mixdown = QuickMixdown(self, self._work)
        self._video = QuickVideo(self, self._work)
        self._publication = QuickPublication(self, self._work)
        self._sources = QuickPreparationSources(self, self._work)
        self._subtitles = QuickSubtitles(self)
        self._video.publishRequested.connect(self._open_verified_video)
        self._tuning.presetLibraryChanged.connect(self._mixdown.refresh_presets)
        self.state.set_download_source(self._support.state.values['download_source'])
        saved_workflow = workflow.read_state()
        self._presets = {**workflow.builtins(), **saved_workflow['presets']}
        draft = self.state.draft.with_options(saved_workflow['options'], saved_workflow['graph'])
        self.state.set_draft(draft)
        self._rows = RowsModel(self, (('componentId', 'id'), ('title', 'title'), ('summary', 'summary'),
                                     ('status', 'status'), ('statusText', 'status_text')))
        self._component_checks = {}
        self._tasks = RowsModel(self, tuple((key, key) for key in ('jobId', 'title', 'statusText', 'summary')))
        self._exit_when_idle = False
        self._closed = False
        self._notice = ''
        self._next_detail = None
        self._rescan = False
        self._task_state_filter = 'all'
        self._task_sort = 'recent'

    def _error(self, error):
        self._notice = str(error)[:1600] or error.__class__.__name__
        self.activityChanged.emit()

    def _work_changed(self):
        self.activityChanged.emit()
        if self._exit_when_idle and not self.workBusy and not self._calibration.worker.kind:
            self.closeReady.emit()

    @Property(QObject, constant=True)
    def support(self):
        return self._support

    @Property(QObject, constant=True)
    def tuning(self):
        return self._tuning

    @Property(QObject, constant=True)
    def calibration(self):
        return self._calibration

    @Property(QObject, constant=True)
    def preparationSources(self):
        return self._sources

    @Property(QObject, constant=True)
    def mixdown(self):
        return self._mixdown

    @Property(QObject, constant=True)
    def video(self):
        return self._video

    @Property(QObject, constant=True)
    def publication(self):
        return self._publication

    @Property(QObject, constant=True)
    def subtitles(self):
        return self._subtitles

    @Property(str, notify=activityChanged)
    def operationKind(self):
        return self._work.kind

    @Property(str, notify=navigationChanged)
    def route(self):
        return self.state.route

    @Property('QVariantList', constant=True)
    def routes(self):
        return [{'pageId': spec.page_id, 'title': tr('设置与支持' if spec.page_id == 'updates' else spec.title),
                 'short': tr({'processing': '准备', 'tuning': '调音', 'audio_mixdown': '混音',
                              'video_mixdown': '视频', 'publishing': '发布',
                              'components': '组件', 'results': '任务', 'updates': '设置'}[spec.page_id])}
                for spec in self.state.routes]

    @Property('QVariantMap', notify=draftChanged)
    def preparation(self):
        return self.state.draft_view

    @Property('QVariantList', constant=True)
    def preparationPresets(self):
        return [{'name': name} for name in self._presets]

    @Property('QVariantMap', notify=preparationTaskChanged)
    def preparationTask(self):
        return dict(self.state.preparation_task)

    @Property(bool, notify=activityChanged)
    def preparationBusy(self):
        return self._work.kind == 'preparation'

    @Property(bool, notify=activityChanged)
    def workBusy(self):
        return bool(self._work.kind)

    @Property(QObject, constant=True)
    def componentModel(self):
        return self._rows

    @Property('QVariantMap', notify=selectionChanged)
    def selected(self):
        row = dict(self.state.selected)
        if row:
            key = self._component_check_key(row['id'], self.state.runtime)
            row['last_check'] = self._component_checks.get(key, {})
        return row

    @staticmethod
    def _component_check_key(component_id, runtime):
        return component_id, runtime if component_id == 'roformer' else ''

    @Property('QVariantMap', notify=draftChanged)
    def componentTaskPlan(self):
        draft = self.state.draft_view
        voice = draft['voice_mode']
        roformer = voice == 'roformer' or (voice == 'dual' and draft['separator_model'] == 'roformer')
        separator = 'roformer' if roformer else 'builtin' if voice != 'import' else ''
        lines = [tr('使用导入的分轨') if not separator else
                 'BS-RoFormer · ' + {'cpu': 'CPU', 'cuda': 'CUDA', 'auto': tr('自动选择运行库')}[draft['roformer_device']]
                 if roformer else tr('内置分离')]
        required = [separator] if separator else []
        if draft['target'] != 'separation':
            lines.append('Vocal2Midi ' + ('2.0' if draft['midi_backend'] == 'v2' else '1.0'))
            if draft['recognize_lyrics']:
                chinese = draft['language'] == 'zh'
                lines.append(tr('中文识别') if chinese else tr('日语识别'))
                lines.append('TiFA' if draft['alignment_engine'] == 'tifa' else 'HubertFA')
                if chinese:
                    required.append('chinese')
        return {'summary': ' · '.join(lines), 'components': required}

    @Property(str, notify=selectionChanged)
    def selectedId(self):
        return self.state.selected_id

    @Property(str, notify=queryChanged)
    def query(self):
        return self.state.query

    @Property('QVariantList', constant=True)
    def downloadSources(self):
        return self._support.downloadSources

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
        return bool(self._catalog.kind)

    @Property('QVariantMap', notify=activityChanged)
    def operation(self):
        return dict(self.state.operation)

    @Property(str, notify=activityChanged)
    def notice(self):
        return tr(self._notice)

    @Property(QObject, constant=True)
    def taskModel(self):
        return self._tasks

    @Property(str, notify=taskChanged)
    def selectedTaskId(self):
        return self.state.selected_task_id

    @Property(str, notify=taskChanged)
    def taskQuery(self):
        return self.state.task_query

    @Property(str, notify=taskChanged)
    def taskStateFilter(self):
        return self._task_state_filter

    @Property(str, notify=taskChanged)
    def taskSort(self):
        return self._task_sort

    @Property('QVariantMap', notify=taskChanged)
    def taskDetail(self):
        return dict(self.state.task_detail)

    @Property('QVariantMap', notify=taskChanged)
    def taskHandoff(self):
        return dict(self.state.handoff)

    @Property(bool, notify=taskChanged)
    def taskLoading(self):
        return bool(self._read.kind or self._details.kind)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    @Slot(str)
    def navigate(self, destination):
        if not self.state.navigate(destination):
            return
        self.navigationChanged.emit()
        if self.state.route == 'results':
            self.refreshTasks()
        elif self.state.route == 'components' and self.state.rows_runtime != self.state.runtime:
            self.refresh()
        elif self.state.route == 'tuning':
            self._tuning.refresh_presets()
        elif self.state.route == 'audio_mixdown':
            self._mixdown.enter()
            self._mixdown.refresh_presets()

    @Slot()
    def returnToPreparation(self):
        self.navigate(self.state.return_route)

    def _update_draft(self, call):
        try:
            if call():
                self.draftChanged.emit()
        except (OSError, ValueError, TypeError) as exc:
            self._error(exc)

    @Slot(QUrl)
    def chooseMusic(self, url):
        if url.isEmpty() or url.isLocalFile():
            self._update_draft(lambda: self.state.set_music(url.toLocalFile()))

    @Slot()
    def clearMusic(self):
        self.chooseMusic(QUrl())

    @Slot(str, QUrl)
    def chooseStem(self, key, url):
        if url.isEmpty() or url.isLocalFile():
            self._update_draft(lambda: self.state.set_draft(self.state.draft.set_stem(key, url.toLocalFile())))

    @Slot(QUrl)
    def chooseOutput(self, url):
        if url.isLocalFile():
            self.setPreparationField('output_directory', str(output_directory(url.toLocalFile())))

    def set_default_output_directory(self, url):
        if not url.isLocalFile():
            return
        try:
            path = output_directory(url.toLocalFile())
            if not path.is_dir():
                raise ValueError('请选择存在的任务目录。')
            save_output_directory(path)
            self._support.set_output(str(path))
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot(str, 'QVariant')
    def setPreparationField(self, name, value):
        self._update_draft(lambda: self.state.set_preparation_field(name, value))

    @Slot(str)
    def applyPreparationPreset(self, name):
        preset = self._presets.get(name)
        if preset:
            self._update_draft(lambda: self.state.set_draft(
                self.state.draft.with_options(preset['options'], preset['graph'])))

    @Slot(str, QUrl)
    def importLyrics(self, voice, url):
        if voice not in ('lead', 'backing') or not url.isLocalFile():
            return
        from lyrics_sources import from_file
        self._fetch_lyrics(voice, lambda: from_file(url.toLocalFile()), 'local' if voice == 'lead' else None)

    @Slot(str)
    def fetchLyrics(self, source):
        from lyrics_sources import fetch
        self._fetch_lyrics('lead', lambda: fetch(source), 'netease')

    def _fetch_lyrics(self, voice, lookup, mode):
        if self.workBusy:
            return
        revision = self.state.draft.revision

        def finish(reference, error):
            if error is not None:
                self._error(error)
                return
            if revision != self.state.draft.revision:
                self._error(ValueError('草稿已修改，请重新获取歌词。'))
                return
            def update():
                draft = self.state.draft.apply_lyrics(voice, reference)
                return self.state.set_draft(draft.edit('reference_mode', mode) if mode else draft)
            self._update_draft(update)

        self._work.start('lyrics', lambda _stop, _send: lookup(), finish)

    @Slot()
    def startPreparation(self):
        self._start_preparation()

    @Slot(bool)
    def retryPreparation(self, use_cpu):
        job = self.state.preparation_task['job']
        if job and self.state.preparation_task.get('can_retry'):
            self._start_preparation(job, use_cpu)

    def _start_preparation(self, existing=None, retry_cpu=False):
        if self.workBusy or self._closed:
            return
        draft = self.state.draft
        reuse_job = '' if existing else self._sources.reusable_job()
        if not draft.view['output_directory'].strip():
            draft = draft.edit('output_directory', self._support.state.values['output_directory'])
        key = uuid4().hex
        task = self.state.preparation_task
        task.update(id=key, state='running', job=existing or '', message='正在准备任务…',
                    completed=0, total=0, current='', elapsed=0, can_retry=False, recovery_action='')
        self.state.handoff = {}
        self._notice = ''
        self.preparationTaskChanged.emit()
        self.navigate('processing')
        registered = False

        def register_job():
            nonlocal registered
            if registered or not task['job']:
                return
            registered = True
            try:
                remember_job(task['job'])
            except (OSError, ValueError) as exc:
                self._error(exc)

        def progress(value):
            if task['id'] != key:
                return
            if task['state'] == 'cancelling':
                value = {name: value[name] for name in ('job', 'completed', 'total', 'elapsed') if name in value}
            else:
                value = {**value, 'state': 'running'}
            task.update(value)
            was_registered = registered
            register_job()
            self.preparationTaskChanged.emit()
            if registered and not was_registered and self.state.route == 'results' and not self._exit_when_idle:
                self.refreshTasks()

        def finish(value, error):
            if task['id'] != key:
                return
            if error is not None:
                state = 'cancelled' if isinstance(error, InterruptedError) else 'failed'
                value = {'state': state, 'message': str(error)[:1600]}
            task.update(value)
            task['can_retry'] = bool(task['job'] and task['state'] in ('failed', 'cancelled', 'partial', 'interrupted'))
            register_job()
            self.preparationTaskChanged.emit()
            if not self._exit_when_idle and self.state.route == 'results':
                self.refreshTasks()

        self._work.start('preparation', lambda stop, send: _run_preparation(draft, existing, retry_cpu, stop, send,
                                                                           reuse_job=reuse_job),
                         finish, progress)

    @Slot()
    def cancelPreparation(self):
        if self.preparationBusy and self.state.preparation_task['state'] == 'running':
            self.state.preparation_task.update(state='cancelling', message='正在取消，已完成的阶段会保留。')
            self._work.cancel()
            self.preparationTaskChanged.emit()

    @Slot()
    def viewPreparationResult(self):
        job = self.state.preparation_task['job']
        if not job:
            return
        self.state.selected_task_id = job
        self.state.task_detail = {}
        self.state.task_query = ''
        if self.state.route == 'results':
            self.refreshTasks()
        else:
            self.navigate('results')

    def _update_rows(self):
        self._rows.replace_rows(self.state.visible_rows)
        self.selectionChanged.emit()

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
        if not self.workBusy and not self.loading and self.state.set_runtime(runtime):
            self.optionsChanged.emit()
            self.refresh()

    @Slot(str)
    def setDownloadSource(self, source):
        self._support.setSetting('download_source', source)

    def set_component_source(self, source):
        if self.state.set_download_source(source):
            self.optionsChanged.emit()

    @Slot()
    def useComponent(self):
        if not self.workBusy and not self.loading and self.state.use_component():
            self.draftChanged.emit()
            self.navigationChanged.emit()

    @Slot()
    def refresh(self):
        if self.loading or self.state.active or self._closed:
            return
        runtime = self.state.runtime
        self._notice = ''

        def finish(rows, error):
            if isinstance(error, InterruptedError):
                return
            if error is None:
                self.state.set_catalog(runtime, rows)
            else:
                self.state.set_catalog(runtime, ())
                self.state.rows_runtime = None
                self._error(error)
            self._update_rows()

        self._catalog.start('component-scan', lambda stop, _send: components.catalog(runtime, check=stop.is_set), finish)

    @Slot()
    def installSelected(self):
        self._start_component('install', force=self.state.selected.get('status') == 'ready')

    @Slot()
    def verifySelected(self):
        self._start_component('verify')

    def _start_component(self, action, *, force=False):
        if self.workBusy or self.loading or self._closed:
            return
        request = self.state.begin(action, force=force)
        if request is None:
            return
        self._notice = ''

        def progress(value):
            if self.state.progress(*value):
                self.activityChanged.emit()

        def finish(value, error):
            if error is not None:
                status = 'cancelled' if isinstance(error, InterruptedError) else 'failed'
                message = str(error)[:1600]
                value = status, message, {'state': status, 'errors': [{'message': message}]} if status == 'failed' else None
            status, message, report = value
            key = self._component_check_key(request.component_id, request.runtime)
            if request.action == 'verify' and report is not None:
                self._component_checks[key] = {**components.verification_summary(report), 'message': message}
            elif request.action == 'install':
                self._component_checks.pop(key, None)
            self.state.complete(request.operation_id, status, message)
            self.selectionChanged.emit()
            if not self._exit_when_idle:
                self.refresh()

        self._work.start('component', lambda stop, send: _component_result(request, stop, send), finish, progress)

    @Slot()
    def cancelOperation(self):
        if self.state.cancel():
            self._work.cancel()
            self.activityChanged.emit()

    def _task_rows(self):
        rows = list(self.state.visible_tasks)
        selected_filter = self._task_state_filter
        if selected_filter == 'done':
            rows = [row for row in rows if row.get('state') in ('done', 'completed')]
        elif selected_filter == 'pending':
            rows = [row for row in rows if row.get('state') in _PENDING_TASK_STATES]
        elif selected_filter in ('failed', 'running'):
            rows = [row for row in rows if row.get('state') == selected_filter]
        rows.sort(key=lambda row: (str(row.get('title') or '').casefold(), row.get('jobId') or ''))
        if self._task_sort == 'recent':
            rows.sort(key=lambda row: float(row.get('modified') or 0), reverse=True)
        return tuple(rows)

    @Slot()
    def refreshTasks(self):
        if self._closed:
            return
        if self._read.kind:
            self._rescan = True
            return

        def finish(rows, error):
            rescan, self._rescan = self._rescan, False
            if error is not None:
                if not isinstance(error, InterruptedError):
                    self._error(error)
                return
            self.state.set_task_rows(rows)
            visible_ids = {row['jobId'] for row in rows}
            if self.state.handoff and self.state.handoff['jobId'] not in visible_ids:
                self.state.handoff = {}
            if self.state.selected_task_id not in visible_ids:
                self.state.selected_task_id = rows[0]['jobId'] if rows else ''
                self.state.task_detail = {}
            self._tasks.replace_rows(self._task_rows())
            self.taskChanged.emit()
            if self.state.selected_task_id:
                self._request_detail(self.state.selected_task_id)
            if rescan:
                self.refreshTasks()

        self._read.start('scan', lambda stop, _send: quick_results.scan(stop.is_set), finish)

    @Slot(str)
    def setTaskQuery(self, query):
        if query != self.state.task_query:
            self.state.task_query = query
            self._tasks.replace_rows(self._task_rows())
            self.taskChanged.emit()

    @Slot(str)
    def setTaskStateFilter(self, value):
        value = str(value or 'all')
        if value not in _TASK_STATE_FILTERS:
            return
        if value != self._task_state_filter:
            self._task_state_filter = value
            self._tasks.replace_rows(self._task_rows())
            self.taskChanged.emit()

    @Slot(str)
    def setTaskSort(self, value):
        value = str(value or 'recent')
        if value not in _TASK_SORTS:
            return
        if value != self._task_sort:
            self._task_sort = value
            self._tasks.replace_rows(self._task_rows())
            self.taskChanged.emit()

    @Slot(str)
    def selectTask(self, job_id):
        if self.state.selected_task_id == job_id or not self.state.select_task(job_id):
            return
        self.taskChanged.emit()
        self._request_detail(job_id)

    def _request_detail(self, job_id):
        if self._details.kind:
            self._next_detail = job_id
            self._details.cancel()
            return

        def finish(value, error):
            if error is None:
                self.state.set_task_detail(job_id, value)
            elif not isinstance(error, InterruptedError) and self.state.selected_task_id == job_id:
                self.state.task_detail = {}
                self._error(error)
            next_job, self._next_detail = self._next_detail, None
            if next_job is not None:
                self._request_detail(next_job)

        self._details.start('detail', lambda stop, _send: quick_results.detail(job_id, stop.is_set), finish)

    @Slot()
    def resumeSelectedTask(self):
        if self.state.task_detail.get('canResume'):
            self._start_preparation(self.state.selected_task_id)

    @Slot()
    def openSelectedTaskFolder(self):
        job = self.state.selected_task_id
        if job and self.state.task_detail.get('jobId') == job and Path(job).is_dir():
            self._open_local(Path(job))

    @Slot()
    def openSelectedTaskLog(self):
        job = self.state.selected_task_id
        if not job or self.state.task_detail.get('jobId') != job:
            return
        try:
            self._open_local(quick_results.task_log(job))
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot(str, QUrl)
    def registerSelectedTaskVideo(self, expected_job, url):
        job = self.state.selected_task_id
        detail = self.state.task_detail
        if (self.workBusy or self.taskLoading or not job or expected_job != job
                or detail.get('jobId') != job or not detail.get('canRegisterVideo')
                or not url.isLocalFile()):
            self._error(ValueError('任务已变化或当前不允许登记已有视频，请重新选择后确认。'))
            return
        source = url.toLocalFile()
        self._notice = '正在核验视频内容并登记到当前任务…'
        self.activityChanged.emit()

        def work(stop, _send):
            return quick_results.register_existing_video(job, source, check=stop.is_set)

        def finish(artifact_id, error):
            if isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            self._notice = (
                f'已登记独立成品视频 {str(artifact_id)[:8]}…；未推断 Mixdown 或其他上游依赖。'
            )
            self.state.handoff = {}
            self.activityChanged.emit()
            self.refreshTasks()

        self._work.start('task-video-register', work, finish)

    @Slot(str, str, QUrl)
    def registerSelectedTaskFile(self, expected_job, kind, url):
        job = self.state.selected_task_id
        detail = self.state.task_detail
        capability = {'project': 'canRegisterProject', 'mixdown': 'canRegisterMixdown'}.get(kind)
        if (self.workBusy or self.taskLoading or not job or expected_job != job
                or detail.get('jobId') != job or not capability or not detail.get(capability)
                or url.isEmpty() or not url.isLocalFile()):
            self._error(ValueError('任务已变化或当前不允许登记工程或 Mixdown，请重新选择后确认。'))
            return
        bindings = detail.get('bindings') if isinstance(detail.get('bindings'), dict) else {}
        binding = bindings.get(kind) if isinstance(bindings.get(kind), dict) else {}
        expected_id = str(binding.get('id') or '')
        expected_revision = str(binding.get('revision') or '')
        source = url.toLocalFile()
        label = 'SVP 工程' if kind == 'project' else 'Mixdown'
        self._notice = f'正在核验 {label} 内容并登记到当前任务…'
        self.activityChanged.emit()

        def work(stop, _send):
            return quick_results.register_external(
                job, kind, source, expected_binding_id=expected_id,
                expected_binding_revision=expected_revision, check=stop.is_set)

        def finish(artifact_id, error):
            if isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            self._notice = (
                f'已登记并核验 {label} {str(artifact_id)[:8]}…；原文件仍保留在原位置，'
                '并已记录当前上游修订。'
            )
            self.state.handoff = {}
            self.activityChanged.emit()
            self.refreshTasks()

        self._work.start('task-artifact-register', work, finish)

    @Slot(str, str, str, str)
    def unlinkSelectedTaskBinding(self, expected_job, kind, expected_binding_id,
                                  expected_binding_revision):
        job = self.state.selected_task_id
        detail = self.state.task_detail
        capability = {
            'project': 'canUnbindProject', 'mixdown': 'canUnbindMixdown',
            'video': 'canUnbindVideo',
        }.get(kind)
        bindings = detail.get('bindings') if isinstance(detail.get('bindings'), dict) else {}
        binding = bindings.get(kind) if isinstance(bindings.get(kind), dict) else {}
        if (self.workBusy or self.taskLoading or not job or expected_job != job
                or detail.get('jobId') != job or not capability or not detail.get(capability)
                or not expected_binding_id or str(binding.get('id') or '') != expected_binding_id
                or str(binding.get('revision') or '') != expected_binding_revision):
            self._error(ValueError('任务的人工登记已变化，请重新核对后再取消。'))
            return
        had_publication = bool(detail.get('hasPublication'))
        self._notice = '正在取消当前任务的人工登记引用…'
        self.activityChanged.emit()

        def work(stop, _send):
            return quick_results.unlink_external(
                job, kind, expected_binding_id=expected_binding_id,
                expected_binding_revision=expected_binding_revision, check=stop.is_set)

        def finish(artifact_id, error):
            if isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            self._notice = (
                f'已取消人工登记 {str(artifact_id)[:8]}…；外部文件未被删除或修改。'
                + (' 成品视频对应的发布关联也已取消。' if kind == 'video' and had_publication else '')
            )
            self.state.handoff = {}
            self.activityChanged.emit()
            self.refreshTasks()

        self._work.start('task-artifact-unlink', work, finish)

    @Slot(str, str)
    def rebuildSelectedTaskArtifacts(self, expected_job, expected_index_revision):
        job = self.state.selected_task_id
        detail = self.state.task_detail
        if (self.workBusy or self.taskLoading or not job or expected_job != job
                or detail.get('jobId') != job or not detail.get('canRebuildArtifacts')
                or str(detail.get('artifactIndexRevision') or '') != expected_index_revision):
            self._error(ValueError('任务文件清单已变化，请重新打开确认后再重建。'))
            return
        self._notice = '正在根据现有处理记录重建文件清单…'
        self.activityChanged.emit()

        def work(stop, _send):
            return quick_results.rebuild_artifacts(
                job, expected_index_revision=expected_index_revision, check=stop.is_set)

        def finish(backup, error):
            if isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            suffix = ('；旧清单已备份为 ' + Path(backup).name) if backup else ''
            self._notice = '已重建文件清单，人工登记已恢复核验' + suffix + '。外部文件未被修改。'
            self.state.handoff = {}
            self.activityChanged.emit()
            self.refreshTasks()

        self._work.start('task-artifact-rebuild', work, finish)

    @Slot(str)
    def openArtifact(self, artifact_id):
        self._checked_artifact(artifact_id)

    @Slot(str)
    def tuneArtifact(self, artifact_id):
        self._checked_artifact(artifact_id, route='tuning')

    @Slot(str)
    def editSubtitleArtifact(self, artifact_id):
        job = self.state.selected_task_id
        if self.workBusy or not job or self.route != 'results':
            return
        row = next((item for item in self.state.task_detail.get('artifacts', ())
                    if item['id'] == artifact_id and item['role'] == 'midi' and item['openable']), None)
        if row is None:
            return

        def finish(data, error):
            if (self._exit_when_idle or self.route != 'results' or self.state.selected_task_id != job
                    or isinstance(error, InterruptedError)):
                return
            if error:
                self._error(error)
                return
            self._subtitles.accept_midi(data, job=job, artifact_id=artifact_id)
            self.subtitlesRequested.emit()

        self._work.start('artifact-check', lambda stop, _send: read_task_midi(job, row, stop.is_set), finish)

    @Slot()
    def mixTaskStems(self):
        self._checked_artifact(None, route='audio_mixdown')

    @Slot()
    def composeTaskVideo(self):
        self._checked_artifact(None, route='video_mixdown')

    @Slot()
    def prepareTaskPublication(self):
        self._checked_artifact(None, route='publishing')

    @Slot()
    def dispatchTaskHandoff(self):
        detail = self.state.task_detail
        if detail.get('canDispatch'):
            route = {'mixdown_ready': 'video_mixdown', 'video_ready': 'publishing',
                     'awaiting_upload': 'publishing', 'awaiting_publish': 'publishing'}.get(detail.get('handoff'))
            self._checked_artifact(None, route=route if route in detail.get('studioTargets', {}) else None)

    def _open_verified_video(self, job, video):
        self._accept_media('publishing', job, [video])

    def _accept_media(self, route, job, rows):
        if not rows:
            return False
        command = self._video.accept_task if route == 'video_mixdown' else self._publication.accept_task
        if command(job, *rows) is False:
            return False
        self.navigate(route)
        return True

    def open_registered_mix(self, job, artifact_id, path, sha256):
        if self.workBusy or not job or not artifact_id:
            return

        def work(stop, _send):
            detail = quick_results.detail(job, stop.is_set)
            by_id = {row['id']: row for row in detail['artifacts'] if row['available']}
            rows = [by_id[key] for key in detail['studioTargets'].get('video_mixdown', ()) if key in by_id]
            if not rows or (rows[0]['id'], rows[0]['path'], rows[0]['sha256']) != (artifact_id, path, sha256):
                raise ValueError('登记的混音已变更，请在任务与结果中重新核对。')
            return rows

        def finish(rows, error):
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error:
                self._error(error)
            else:
                self._accept_media('video_mixdown', job, rows)

        self._work.start('artifact-check', work, finish)

    def _checked_artifact(self, artifact_id, *, route=None):
        job = self.state.selected_task_id
        if self.workBusy or not job:
            return
        shown = self.state.task_detail
        shown_rows = {row['id']: row for row in shown.get('artifacts', ())}
        def wanted_ids(detail):
            if route == 'audio_mixdown':
                return detail.get('mixArtifactIds', ())
            if route in ('video_mixdown', 'publishing'):
                return detail.get('studioTargets', {}).get(route, ())
            if artifact_id is not None:
                return [artifact_id]
            return detail.get('delivery', {}).get('artifactIds', ())

        wanted = wanted_ids(shown)
        expected = [artifact_identity(shown_rows[key]) for key in wanted
                    if key in shown_rows and shown_rows[key]['available']]
        if not expected or len(expected) != len(wanted):
            return

        def finish(detail, error):
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            if self.state.selected_task_id != job:
                return
            self.state.set_task_detail(job, detail)
            self.taskChanged.emit()
            by_id = {row['id']: row for row in detail['artifacts'] if row['available']}
            if artifact_id is not None:
                row = by_id.get(artifact_id)
                if not row or not row['openable'] or [artifact_identity(row)] != expected:
                    self._error(ValueError('该文件已变更、无法核验或不支持直接打开。'))
                    return
                if route == 'tuning':
                    if row['role'] not in ('editor_project', 'tuned_project') or Path(row['path']).suffix.casefold() != '.svp':
                        self._error(ValueError('只能在调音工作台加载已核验的 SVP 工程。'))
                        return
                    self.navigate('tuning')
                    self._tuning.accept_task(job, row)
                    return
                self._open_local(Path(row['path']))
                return
            action = detail.get('handoff')
            delivery = detail['delivery']
            if route in ('video_mixdown', 'publishing'):
                rows = [by_id[key] for key in wanted_ids(detail) if key in by_id]
                if [artifact_identity(row) for row in rows] != expected:
                    self._error(ValueError('文件或上游版本已变化，请重新核对。'))
                    return
                self._accept_media(route, job, rows)
                return
            if route == 'audio_mixdown':
                rows = [by_id[key] for key in wanted_ids(detail) if key in by_id]
                if [artifact_identity(row) for row in rows] != expected:
                    self._error(ValueError('任务分轨或上游版本已变化，请重新核对。'))
                    return
                self.navigate('audio_mixdown')
                self._mixdown.accept_task(job, rows)
                return
            rows = [by_id[key] for key in delivery['artifactIds'] if key in by_id]
            if (not detail['canDispatch'] or action != shown.get('handoff')
                    or [artifact_identity(row) for row in rows] != expected):
                self._error(ValueError('产物或上游版本已变化，请核对结果后再交接。'))
                return
            row = by_id.get(delivery['openArtifactId'])
            directories = {Path(item['path']).parent for item in rows}
            directory = next(iter(directories)) if len(directories) == 1 else Path(job)
            destination = Path(row['path']) if row and row['openable'] else directory
            self.state.handoff = {'jobId': job, 'action': action, 'artifacts': rows, 'destination': str(destination)}
            self.taskChanged.emit()
            if action == 'awaiting_tuning' and row and destination.suffix.casefold() == '.svp':
                self.navigate('tuning')
                self._tuning.accept_task(job, row)
            elif action == 'audio_ready':
                self.navigate('audio_mixdown')
                self._mixdown.accept_task(job, rows)
            else:
                self._open_local(destination)

        self._work.start('artifact-check', lambda stop, _send: quick_results.detail(job, stop.is_set), finish)

    def _open_local(self, path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._error(ValueError('系统无法打开此文件或目录。'))

    @Slot(str)
    @Slot(str, str)
    def renameSelectedTask(self, name, expected_id=''):
        self._mutate_task('canRename', expected_id, lambda job: quick_results.rename(job, name))

    @Slot(str)
    def deleteSelectedTask(self, expected_id):
        if expected_id:
            self._mutate_task('canDelete', expected_id, quick_results.delete)

    def _mutate_task(self, capability, expected_id, command):
        job = self.state.selected_task_id
        if self.workBusy or not job or expected_id and job != expected_id or not self.state.task_detail.get(capability):
            return

        def finish(_value, error):
            if error is not None:
                self._error(error)
            if not self._exit_when_idle:
                self.refreshTasks()

        self._work.start('task-edit', lambda _stop, _send: command(job), finish)

    @Slot()
    def cancelAndClose(self):
        self._exit_when_idle = True
        self.cancelOperation()
        self.cancelPreparation()
        self._support.cancelUpdate()
        self._tuning.cancel()
        self._mixdown.cancel()
        self._video.cancel()
        self._publication.cancel()
        self._calibration.cancel()
        if self.workBusy:
            self._work.cancel()
        elif not self._calibration.worker.kind:
            self.closeReady.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._support.close()
        self._video.cancel()
        self._publication.cancel()
        self._calibration.close()
        for lane in (self._work, self._catalog, self._read, self._details):
            lane.close()
        self._tuning.close()
        self._mixdown.close()
        self._video.close()
        self._publication.close()
