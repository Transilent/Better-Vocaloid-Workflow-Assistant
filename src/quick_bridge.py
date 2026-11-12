"""Qt projections of the shared workspace, task library and worker commands."""

from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QUrl, Signal, Slot, QTimer
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


def _run_preparation(draft, existing, retry_cpu, stop, send, *, reuse_job='', reprocess=''):
    from task_store import known_job

    if stop.is_set():
        raise InterruptedError('已取消任务。')
    job = (known_job(existing) if existing else preparation.reprocess_task(draft, reprocess) if reprocess else
           preparation.create_from_draft(draft, reuse_job=reuse_job) if reuse_job else preparation.create_from_draft(draft))
    send({'state': 'running', 'job': str(job), 'message': '正在启动准备任务…'})
    try:
        code = preparation.run_process(job, stop=stop, on_progress=send, retry_cpu=retry_cpu)
        view = preparation.progress_view(job)
        if code and view['state'] not in ('cancelled', 'failed'):
            view.update(state='failed', message=f'准备进程退出（{code}）。请查看任务日志。')
        if view['state'] == 'failed':
            recovery = preparation.recovery_view(job)
            if code and recovery['message'] == '处理进程提前结束，请查看 pipeline.log。':
                recovery['message'] = view['message']
            view.update(recovery)
        elif view['state'] == 'cancelled':
            view['message'] = '准备已取消，可以继续此任务。'
        elif view['state'] in ('done', 'partial'):
            view['message'] = '准备完成。' if view['state'] == 'done' else '本轮阶段完成，可以继续。'
        return view
    except Exception as exc:
        return {'job': str(job), 'state': 'failed', 'message': str(exc)[:1600]}


def _controller_view(controller):
    if controller is None:
        return {}
    value = getattr(controller, 'view', {})
    if not isinstance(value, dict):
        raise TypeError('task controller view is not a mapping')
    return dict(value)


def _reference_paths(values):
    """Extract only local path-shaped values from a selected controller view."""
    result = []
    for value in values:
        if isinstance(value, QUrl):
            if value.isLocalFile():
                result.append(value.toLocalFile())
            continue
        if not isinstance(value, (str, os.PathLike)):
            continue
        text = os.fspath(value).strip().strip('"').strip()
        if not text or ('://' in text and not text.casefold().startswith('file:')):
            continue
        if text.casefold().startswith('file:'):
            url = QUrl(text)
            text = url.toLocalFile() if url.isLocalFile() else ''
        if text:
            result.append(text)
    return list(dict.fromkeys(result))


def _task_reference_groups(*, mixdown=None, mixdown_tracks=(), tuning=None, video=None,
                           publication=None, calibration=None, subtitles=None, subtitle_drafts=None,
                           preparation=None, operation=None, operations=None):
    """Build task-reference groups without coupling task safety to any one page."""
    groups = []

    def add(label, *values):
        paths = _reference_paths(values)
        if paths:
            groups.append({'label': label, 'paths': paths})

    mix = _controller_view(mixdown)
    track_rows = mixdown_tracks if isinstance(mixdown_tracks, (list, tuple)) else ()
    mix_label = ('简易混音 · 正在播放' if mix.get('playing') else
                 '简易混音 · 后台处理中' if mix.get('busy') else '简易混音 · 已载入')
    add(mix_label, mix.get('job'), mix.get('output'), mix.get('result'),
        *(row.get('path') for row in track_rows if isinstance(row, dict)))

    tune = _controller_view(tuning)
    add('调音 · ' + ('正在处理' if tune.get('busy') else '已载入'),
        tune.get('job'), tune.get('source'), tune.get('candidate'), tune.get('savedCopy'))

    vid = _controller_view(video)
    add('视频 · ' + ('正在处理' if vid.get('busy') else '已载入'),
        vid.get('job'), vid.get('sourceVideo'), vid.get('mixdown'), vid.get('output'),
        vid.get('preview'), vid.get('result'))

    publish = _controller_view(publication)
    add('发布 · ' + ('正在处理' if publish.get('busy') else '已载入'),
        publish.get('job'), publish.get('video'), publish.get('bundle'), publish.get('coverFile'))

    calibrate = _controller_view(calibration)
    add('声库校准 · ' + ('正在处理' if calibrate.get('busy') else '已载入'),
        calibrate.get('job'), calibrate.get('project'), calibrate.get('reference'),
        calibrate.get('folder'), calibrate.get('renders'), calibrate.get('session'),
        calibrate.get('result'), calibrate.get('report'))

    subtitle_view = _controller_view(subtitles)
    add('字幕工具 · 已载入来源', subtitle_view.get('taskJobId'), subtitle_view.get('sourcePath'),
        subtitle_view.get('exportedPath'))
    drafts = subtitle_drafts if isinstance(subtitle_drafts, dict) else {}
    for kind in sorted(drafts):
        row = drafts.get(kind) if isinstance(drafts.get(kind), dict) else {}
        add('字幕工具 · ' + str(kind).upper() + ' 草稿', row.get('_task_job_id'),
            row.get('_source_path'), row.get('_exported_path'))

    prep = preparation if isinstance(preparation, dict) else {}
    if prep.get('job'):
        add('准备任务 · 正在运行', prep.get('job'))

    active_rows = []
    if isinstance(operation, dict):
        active_rows.append(operation)
    if isinstance(operations, (list, tuple)):
        active_rows.extend(row for row in operations if isinstance(row, dict))
    for active in active_rows:
        if active.get('paths'):
            add(str(active.get('label') or '任务操作 · 进行中'), *active.get('paths', ()))
    return groups

class QuickBridge(QObject):
    verify_task_artifact = staticmethod(verify_task_artifact)
    verify_publication_bundle = staticmethod(verify_publication_bundle)

    navigationChanged = Signal()
    draftChanged = Signal()
    selectionChanged = Signal()
    componentTaskPlanChanged = Signal()
    queryChanged = Signal()
    optionsChanged = Signal()
    activityChanged = Signal()
    preparationTaskChanged = Signal()
    preparationPresetsChanged = Signal()
    taskChanged = Signal()
    taskSafetyChanged = Signal()
    taskArtifactChanged = Signal()
    taskPrimaryActionChanged = Signal()
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
        self._task_work = WorkerLane(self, 'vwa-task-mutations')
        self._work.changed.connect(self._work_changed)
        self._task_work.changed.connect(self._work_changed)
        self._catalog.changed.connect(self.activityChanged)
        self._catalog.changed.connect(self.componentTaskPlanChanged)
        self.draftChanged.connect(self._component_draft_changed)
        self._read.changed.connect(self.taskChanged)
        self._details.changed.connect(self.taskChanged)
        self._task_work.changed.connect(self.taskChanged)
        self.taskChanged.connect(self._task_changed_safety)
        self.taskSafetyChanged.connect(self.taskArtifactChanged)
        self.taskSafetyChanged.connect(self.taskPrimaryActionChanged)
        self.activityChanged.connect(self.taskPrimaryActionChanged)
        self.preparationTaskChanged.connect(self.taskPrimaryActionChanged)
        self._support = QuickSupport(self, self._work, sources)
        self._tuning = QuickTuning(self, self._work)
        self._calibration = QuickCalibration(self, self._tuning)
        self._calibration.worker.changed.connect(self._work_changed)
        self._mixdown = QuickMixdown(self, self._work)
        self._video = QuickVideo(self, self._work)
        self._publication = QuickPublication(self, self._work)
        self._subtitles = QuickSubtitles(self)
        self._sources = QuickPreparationSources(self, self._work)
        self._video.publishRequested.connect(self._open_verified_video)
        self._tuning.presetLibraryChanged.connect(self._mixdown.refresh_presets)
        for signal in (self._mixdown.viewChanged, self._mixdown.tracksChanged, self._tuning.changed,
                       self._video.changed, self._publication.viewChanged, self._calibration.changed,
                       self._subtitles.changed, self.preparationTaskChanged):
            signal.connect(self._schedule_task_safety)
        # Settings observes the actual cross-page operation owners used by the
        # deferred update restart, not just the preparation/component lane.
        for signal in (self._tuning.changed, self._video.changed,
                       self._publication.viewChanged, self._calibration.changed,
                       self._catalog.changed, self._read.changed, self._details.changed,
                       self._task_work.changed, self.preparationTaskChanged):
            signal.connect(self._support.refreshActivity)
        self.state.set_download_source(self._support.state.values['download_source'])
        saved_workflow = workflow.read_state()
        self._legacy_workflow = saved_workflow
        self._legacy_workflow_presets = dict(saved_workflow['presets'])
        from preset_library import PresetLibrary
        self._preparation_preset_library = PresetLibrary(ROOT / 'user-presets')
        self._preparation_presets = []
        self._preparation_preset_rows = {}
        self._preparation_preset_failure = ''
        self._refresh_preparation_presets()
        draft = self.state.draft.with_options(saved_workflow['options'], saved_workflow['graph'])
        draft = preparation.restore_draft_settings(draft)
        self.state.set_draft(draft)
        self._rows = RowsModel(self, (('componentId', 'id'), ('title', 'title'), ('summary', 'summary'),
                                     ('status', 'status'), ('statusText', 'status_text'),
                                     ('componentGroup', 'component_group')))
        self._component_checks = {}
        self._tasks = RowsModel(self, tuple((key, key) for key in ('jobId', 'title', 'statusText', 'summary', 'sourceText')))
        self._exit_when_idle = False
        self._closed = False
        self._notice = ''
        self._work_task_lock = None
        self._task_mutation_job = ''
        self._detail_job_id = ''
        self._next_detail = None
        self._rescan = False
        self._task_state_filter = 'all'
        self._task_sort = 'recent'
        self._task_safety_timer = QTimer(self)
        self._task_safety_timer.setSingleShot(True)
        self._task_safety_timer.setInterval(140)
        self._task_safety_timer.timeout.connect(self._refresh_task_safety)
        self._task_safety_cache = {'job': '', 'groups': (), 'blockers': [], 'signature': ()}

    def _error(self, error):
        self._notice = str(error)[:1600] or error.__class__.__name__
        self.activityChanged.emit()

    def _work_changed(self):
        self.activityChanged.emit()
        if self._exit_when_idle and not self.workBusy and not self._calibration.worker.kind:
            self.closeReady.emit()

    def _task_reference_groups(self):
        tracks = self._mixdown.tracks
        preparation = self.state.preparation_task if self.preparationBusy else {}
        operations = []
        if self._work_task_lock:
            operations.append(self._work_task_lock)
        if self._detail_job_id:
            operations.append({'label': '任务文件 · 正在核验', 'paths': (self._detail_job_id,)})
        if self._task_mutation_job:
            operations.append({'label': '任务记录操作 · 进行中', 'paths': (self._task_mutation_job,)})
        tuning_kind = self._tuning.worker.kind
        tuning_job = str(getattr(self._tuning, '_selected_tuning_task', '') or '')
        if tuning_job and tuning_kind in ('task-picker-detail', 'task-picker-load'):
            operations.append({'label': '调音 · 正在核验所选任务', 'paths': (tuning_job,)})
        drafts = self._subtitles._drafts
        return _task_reference_groups(
            mixdown=self._mixdown, mixdown_tracks=tracks, tuning=self._tuning, video=self._video,
            publication=self._publication, calibration=self._calibration, subtitles=self._subtitles,
            subtitle_drafts=drafts, preparation=preparation, operations=operations,
        )

    def _task_blockers(self, job, *, canonical=False):
        try:
            return quick_results.task_reference_blockers(
                job, self._task_reference_groups(), canonical=canonical)
        except Exception as exc:
            return quick_results.task_projection_failure(job, exc)

    @staticmethod
    def _task_safety_signature(job, groups, blockers):
        return (str(job or ''), tuple((row.get('label', ''), tuple(row.get('paths', ())))
                                     for row in groups), tuple((row.get('label', ''),
                                                               tuple(row.get('paths', ()))) for row in blockers))

    def _invalidate_task_safety(self, *, emit=True):
        self._task_safety_cache.update(job='', groups=(), blockers=[], signature=None)
        if emit and self.state.route == 'results':
            self.taskSafetyChanged.emit()

    def _task_changed_safety(self):
        self._invalidate_task_safety()

    def _task_blockers_cached(self, job):
        cache = self._task_safety_cache
        if cache.get('job') == job and cache.get('signature') is not None:
            return [dict(row) for row in cache.get('blockers', ())]
        groups = self._task_reference_groups()
        blockers = quick_results.task_reference_blockers(job, groups)
        cache.update(job=job, groups=groups, blockers=blockers,
                     signature=self._task_safety_signature(job, groups, blockers))
        return [dict(row) for row in blockers]

    def _schedule_task_safety(self, *_args):
        if self._closed or self.state.route != 'results':
            return
        if not self._task_safety_timer.isActive():
            self._task_safety_timer.start()

    def _refresh_task_safety(self):
        if self._closed or self.state.route != 'results':
            return
        job = self.state.selected_task_id
        groups = self._task_reference_groups()
        blockers = quick_results.task_reference_blockers(job, groups)
        signature = self._task_safety_signature(job, groups, blockers)
        cache = self._task_safety_cache
        if cache.get('signature') == signature and cache.get('job') == job:
            return
        cache.update(job=job, groups=groups, blockers=blockers, signature=signature)
        self.taskSafetyChanged.emit()

    def _set_work_task_lock(self, label, *paths):
        values = tuple(str(path) for path in paths if path)
        self._work_task_lock = {'label': label, 'paths': values} if values else None
        self._invalidate_task_safety()

    def _clear_work_task_lock(self):
        if self._work_task_lock is not None:
            self._work_task_lock = None
            self._invalidate_task_safety()

    def _mutation_job(self, expected_job, capability, action):
        job = self.state.selected_task_id
        detail = self.state.task_detail
        if self._closed or not job or expected_job != job or detail.get('jobId') != job:
            self._error(ValueError('任务已变化，请重新选择后确认。'))
            return None
        blockers = self._task_blockers(job, canonical=True)
        if blockers:
            self._error(ValueError(quick_results.task_mutation_error(action, blockers)))
            self._invalidate_task_safety()
            return None
        if self._detail_job_id == job:
            self._error(ValueError('任务文件正在核验，请等待完成后再修改。'))
            return None
        if self._task_work.kind:
            self._error(ValueError('另一项任务记录操作仍在进行，请等待完成。'))
            return None
        if not detail.get(capability):
            self._error(ValueError('当前任务状态不允许' + action + '。'))
            return None
        return job

    def _start_task_mutation(self, kind, job, message, work, finish):
        self._task_mutation_job = job
        self._notice = message
        self.activityChanged.emit()
        self._invalidate_task_safety()

        def complete(value, error):
            self._task_mutation_job = ''
            self._invalidate_task_safety()
            finish(value, error)

        if not self._task_work.start(kind, work, complete):
            self._task_mutation_job = ''
            self._invalidate_task_safety()
            self._error(ValueError('任务记录操作未能启动，请稍后重试。'))
            return False
        return True

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
        return self._task_work.kind or self._work.kind

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

    @Property('QVariantList', notify=preparationPresetsChanged)
    def preparationPresets(self):
        return [dict(row) for row in self._preparation_presets]

    def _refresh_preparation_presets(self):
        try:
            snapshot = self._preparation_preset_library.snapshot()
            entries = preparation.preset_catalog(snapshot, self._legacy_workflow_presets)
            self._preparation_preset_failure = ''
            loaded = True
        except Exception as exc:
            entries = preparation.fallback_preset_catalog(self._legacy_workflow_presets)
            self._preparation_preset_failure = str(exc)[:1600] or exc.__class__.__name__
            loaded = False
        self._preparation_presets = entries
        self._preparation_preset_rows = {row['key']: dict(row) for row in entries}
        self.preparationPresetsChanged.emit()
        return loaded

    @Property('QVariantMap', notify=preparationTaskChanged)
    def preparationTask(self):
        return dict(self.state.preparation_task)

    @Property(bool, notify=activityChanged)
    def preparationBusy(self):
        return self._work.kind == 'preparation'

    @Property(bool, notify=activityChanged)
    def workBusy(self):
        return bool(self._work.kind or self._task_work.kind)

    @Property(bool, notify=taskChanged)
    def taskMutationBusy(self):
        return bool(self._task_work.kind)

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

    @Property('QVariantMap', notify=componentTaskPlanChanged)
    def componentTaskPlan(self):
        draft = self.state.draft_view
        plan = self.state.component_plan
        required = plan['components']
        separator = next((key for key in required if key in ('builtin', 'roformer')), '')
        lines = [tr('使用导入的分轨') if not separator else
                 'BS-RoFormer · ' + {'cpu': 'CPU', 'cuda': 'CUDA', 'auto': tr('自动选择运行库')}[draft['roformer_device']]
                 if separator == 'roformer' else tr('内置分离')]
        if draft['target'] != 'separation':
            lines.append('Vocal2Midi ' + ('2.0' if draft['midi_backend'] == 'v2' else '1.0'))
            if draft['recognize_lyrics']:
                chinese = draft['language'] == 'zh'
                lines.append(tr('中文识别') if chinese else tr('日语识别'))
                lines.append('TiFA' if draft['alignment_engine'] == 'tifa' else 'HubertFA')
        return {**plan, 'summary': ' · '.join(lines)}

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

    @Property('QVariantMap', notify=taskSafetyChanged)
    def taskDetail(self):
        detail = dict(self.state.task_detail)
        job = detail.get('jobId') or self.state.selected_task_id
        return quick_results.protect_task_detail(detail, self._task_blockers_cached(job))

    @Property(str, notify=taskArtifactChanged)
    def selectedTaskArtifactId(self):
        return self.state.selected_task_artifact_id

    @Property('QVariantMap', notify=taskArtifactChanged)
    def selectedTaskArtifact(self):
        return dict(self.state.selected_task_artifact)

    @Property('QVariantMap', notify=taskPrimaryActionChanged)
    def taskPrimaryAction(self):
        job = self.state.selected_task_id
        running_here = bool(job and self.preparationBusy and self.state.preparation_task.get('job') == job)
        return quick_results.task_primary_action(self.state.task_detail, running_here=running_here,
                                                 work_busy=self.workBusy or self.taskLoading)

    @Property('QVariantMap', notify=taskChanged)
    def taskHandoff(self):
        return dict(self.state.handoff)

    @Property(bool, notify=taskChanged)
    def taskLoading(self):
        return bool(self._read.kind or self._details.kind or self._task_work.kind)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    @Slot(str)
    def navigate(self, destination):
        if not self.state.navigate(destination):
            return
        self.navigationChanged.emit()
        if self.state.route == 'results':
            self._invalidate_task_safety(emit=False)
            self._schedule_task_safety()
            self.refreshTasks()
        elif self.state.route == 'components' and self.state.rows_runtime != self.state.runtime:
            self.refresh()
        elif self.state.route == 'tuning':
            self._tuning.refresh_presets()
        elif self.state.route == 'audio_mixdown':
            self._mixdown.enter()
            self._mixdown.refresh_presets()
        elif self.state.route == 'processing':
            self._refresh_preparation_presets()
        elif self.state.route == 'updates' and self._support.presetLibrary['loaded']:
            self._support.refreshPresetLibrary()

    @Slot()
    def returnToPreparation(self):
        self.navigate(self.state.return_route)

    def _update_draft(self, call, *, persist=False):
        try:
            if call():
                self.draftChanged.emit()
                if persist:
                    preparation.save_draft_settings(self.state.draft)
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
        self._update_draft(lambda: self.state.set_preparation_field(name, value), persist=True)

    @Slot(str)
    def setPreparationMusicPath(self, path):
        try:
            value = str(path).strip().strip('"')
            if self.state.set_music(value):
                self.draftChanged.emit()
        except (OSError, ValueError, TypeError) as exc:
            self._error(exc)

    @Slot(str)
    def applyPreparationPreset(self, key):
        row = self._preparation_preset_rows.get(str(key))
        if not row:
            return
        try:
            if row['source'] == 'library':
                document = self._preparation_preset_library.get_document(row['id'], row['version'])
                payload = document['payload']
            elif row['source'] == 'legacy':
                payload = self._legacy_workflow_presets[row['preset_name']]
            else:
                payload = workflow.builtins()[row['preset_name']]
            draft = preparation.apply_preset(self.state.draft, payload)
        except Exception as exc:
            self._error(exc)
            return
        self._update_draft(lambda: self.state.set_draft(draft), persist=True)

    @Slot(str, result=bool)
    def savePreparationPreset(self, name):
        if self.workBusy:
            return False
        try:
            preparation.append_preset(self._preparation_preset_library, self.state.draft, name)
            self._refresh_preparation_presets()
        except Exception as exc:
            self._error(exc)
            return False
        self._notice = '准备预设已追加到本机预设库。'
        self.activityChanged.emit()
        return True

    @Slot(str, result=bool)
    def removePreparationPreset(self, key):
        row = self._preparation_preset_rows.get(str(key))
        if not row:
            return False
        if not row.get('removable'):
            self._error(ValueError('内置预设不可删除；可以另存为自定义预设。'))
            return False
        try:
            if row['source'] == 'library':
                preparation.remove_preset(
                    self._preparation_preset_library, row['id'], row['version'])
            elif row['source'] == 'legacy':
                presets = dict(self._legacy_workflow_presets)
                presets.pop(row['preset_name'], None)
                selected = self._legacy_workflow.get('preset', '自定义')
                if selected == row['preset_name']:
                    selected = '自定义'
                workflow.save_state(
                    self._legacy_workflow['graph'], self._legacy_workflow['options'],
                    presets, selected)
                self._legacy_workflow_presets = presets
                self._legacy_workflow = {
                    **self._legacy_workflow, 'presets': presets, 'preset': selected}
            else:
                raise ValueError('该预设不可删除。')
            self._refresh_preparation_presets()
        except Exception as exc:
            self._error(exc)
            return False
        self._notice = '自定义准备预设已移除；本机历史文件仍保留。'
        self.activityChanged.emit()
        return True

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

    def _start_preparation(self, existing=None, retry_cpu=False, *, reprocess=''):
        if self.workBusy or self._closed:
            return
        draft = self.state.draft
        reuse_job = '' if existing or reprocess else self._sources.reusable_job()
        if not draft.view['output_directory'].strip():
            draft = draft.edit('output_directory', self._support.state.values['output_directory'])
        key = uuid4().hex
        task = self.state.preparation_task
        task.update(id=key, state='running', job=existing or '', message='正在准备任务…',
                    completed=0, total=0, current='', elapsed=0, can_retry=False,
                    stages=[], log_tail='', recovery_action='', recovery_message='')
        self.state.handoff = {}
        self._notice = ''
        if existing or reprocess:
            self._set_work_task_lock('准备任务 · 正在运行', existing or reprocess)
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
            if value.get('job'):
                self._set_work_task_lock('准备任务 · 正在运行', value['job'])
            was_registered = registered
            register_job()
            self.preparationTaskChanged.emit()
            if registered and not was_registered and self.state.route == 'results' and not self._exit_when_idle:
                self.refreshTasks()

        def finish(value, error):
            self._clear_work_task_lock()
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

        started = self._work.start('preparation', lambda stop, send: _run_preparation(
            draft, existing, retry_cpu, stop, send, reuse_job=reuse_job, reprocess=reprocess), finish, progress)
        if not started:
            self._clear_work_task_lock()

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
        self.state.focus_task(job)
        self.state.task_query = ''
        if self.state.route == 'results':
            self._invalidate_task_safety(emit=False)
            self._schedule_task_safety()
            self.refreshTasks()
        else:
            self.navigate('results')

    def _update_rows(self):
        self._rows.replace_rows(self.state.component_browser_rows)
        self.selectionChanged.emit()

    def _component_draft_changed(self):
        if self.state.rows:
            self._update_rows()
        self.componentTaskPlanChanged.emit()

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
    def useBuiltinComponent(self):
        if not self.workBusy and not self.loading and self.state.use_builtin_component():
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
                first_catalog = not self.state.rows
                self.state.set_catalog(runtime, rows)
                missing = self.state.component_plan['missingIds']
                if first_catalog and missing:
                    self.state.select(missing[0])
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
                self.state.focus_task(rows[0]['jobId'] if rows else '')
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
            self._detail_job_id = ''
            if error is None:
                self.state.set_task_detail(job_id, value)
            elif not isinstance(error, InterruptedError) and self.state.selected_task_id == job_id:
                self.state.set_task_detail(job_id, {})
                self._error(error)
            next_job, self._next_detail = self._next_detail, None
            if next_job is not None:
                self._request_detail(next_job)

        self._detail_job_id = job_id
        def work(stop, _send):
            return quick_results.detail(job_id, stop.is_set, include_history=True)

        if not self._details.start('detail', work, finish):
            self._detail_job_id = ''

    @Slot(str)
    def selectTaskArtifact(self, artifact_id):
        if self.route == 'results' and self.state.select_task_artifact(artifact_id):
            self.taskArtifactChanged.emit()

    @Slot()
    def runTaskPrimaryAction(self):
        if self._closed or self.route != 'results':
            return
        action = self.taskPrimaryAction
        if not action['enabled']:
            return
        kind = action['kind']
        if kind == 'progress':
            self.inspectTaskProgress(self.state.selected_task_id)
        elif kind == 'retry':
            self.resumeSelectedTask()
        elif kind == 'tuning':
            self._checked_artifact(action['artifactId'], route=kind, expected_action=action)
        elif kind in ('audio_mixdown', 'video_mixdown', 'publishing'):
            self._checked_artifact(None, route=kind, expected_action=action)
        elif kind == 'delivery':
            self._checked_artifact(None, expected_action=action)

    @Slot()
    def resumeSelectedTask(self):
        if self.state.task_detail.get('canResume'):
            self._start_preparation(self.state.selected_task_id)

    @Slot(str)
    def reprocessSelectedTask(self, expected_job):
        if (expected_job and expected_job == self.state.selected_task_id == self.state.task_detail.get('jobId')
                and self.state.task_detail.get('canReprocess')):
            self._start_preparation(reprocess=expected_job)

    @Slot(str)
    def inspectTaskProgress(self, expected_job):
        if not expected_job or expected_job != self.state.selected_task_id:
            return
        if self.preparationBusy and self.state.preparation_task['job'] == expected_job:
            self.navigate('processing')
            return
        if self.workBusy:
            return

        def work(stop, _send):
            from task_store import known_job
            if stop.is_set():
                raise InterruptedError('已停止读取任务进度。')
            job = known_job(expected_job)
            view = preparation.progress_view(job)
            if view['state'] in ('failed', 'cancelled', 'interrupted'):
                view.update(preparation.recovery_view(job))
            try:
                view['log_tail'] = preparation.read_log_tail(quick_results.task_log(job))
            except (OSError, ValueError):
                pass
            return view

        def finish(view, error):
            self._clear_work_task_lock()
            if self.state.selected_task_id != expected_job:
                return
            if error is not None:
                self._error(error)
                return
            self.state.preparation_task.update(id='', recovery_action='', recovery_message='', log_tail='')
            self.state.preparation_task.update(view)
            self.state.preparation_task['can_retry'] = view['state'] in ('failed', 'cancelled', 'interrupted', 'partial')
            self.preparationTaskChanged.emit()
            self.navigate('processing')

        self._set_work_task_lock('任务进度 · 正在读取', expected_job)
        if not self._work.start('preparation-inspect', work, finish):
            self._clear_work_task_lock()

    @Slot()
    def refreshPreparationLog(self):
        task = self.state.preparation_task
        job, revision = task.get('job'), task.get('id')
        if not job or self._read.kind:
            return

        def finish(text, error):
            if (task.get('id'), task.get('job')) != (revision, job):
                return
            task['log_tail'] = str(error) if error else text
            self.preparationTaskChanged.emit()

        self._read.start('preparation-log', lambda _stop, _send: preparation.read_log_tail(quick_results.task_log(job)),
                         finish)

    @Slot()
    def openSubtitleTool(self):
        self.subtitlesRequested.emit()

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
        job = self._mutation_job(expected_job, 'canRegisterVideo', '登记已有成品视频')
        if job is None:
            return
        if not url.isLocalFile():
            self._error(ValueError('只能登记本机视频文件。'))
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

        self._start_task_mutation('task-video-register', job, '正在核验视频内容并登记到当前任务…', work, finish)

    @Slot(str, str, QUrl)
    def registerSelectedTaskFile(self, expected_job, kind, url):
        capability = {'project': 'canRegisterProject', 'mixdown': 'canRegisterMixdown'}.get(kind)
        if not capability:
            self._error(ValueError('未知的工程登记类型。'))
            return
        job = self._mutation_job(expected_job, capability, '登记工程' if kind == 'project' else '登记 Mixdown')
        if job is None:
            return
        if url.isEmpty() or not url.isLocalFile():
            self._error(ValueError('只能登记本机工程或 Mixdown 文件。'))
            return
        detail = self.state.task_detail
        bindings = detail.get('bindings') if isinstance(detail.get('bindings'), dict) else {}
        binding = bindings.get(kind) if isinstance(bindings.get(kind), dict) else {}
        expected_id = str(binding.get('id') or '')
        expected_revision = str(binding.get('revision') or '')
        source = url.toLocalFile()
        label = '工程' if kind == 'project' else 'Mixdown'
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

        self._start_task_mutation('task-artifact-register', job, f'正在核验 {label} 内容并登记到当前任务…', work, finish)

    @Slot(str, str, str, str)
    def unlinkSelectedTaskBinding(self, expected_job, kind, expected_binding_id,
                                  expected_binding_revision):
        capability = {
            'project': 'canUnbindProject', 'mixdown': 'canUnbindMixdown',
            'video': 'canUnbindVideo',
        }.get(kind)
        if not capability:
            self._error(ValueError('未知的人工登记类型。'))
            return
        job = self._mutation_job(expected_job, capability, '取消人工登记')
        if job is None:
            return
        detail = self.state.task_detail
        bindings = detail.get('bindings') if isinstance(detail.get('bindings'), dict) else {}
        binding = bindings.get(kind) if isinstance(bindings.get(kind), dict) else {}
        if (not expected_binding_id or str(binding.get('id') or '') != expected_binding_id
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

        self._start_task_mutation('task-artifact-unlink', job, '正在取消当前任务的人工登记引用…', work, finish)

    @Slot(str, str)
    def rebuildSelectedTaskArtifacts(self, expected_job, expected_index_revision):
        job = self._mutation_job(expected_job, 'canRebuildArtifacts', '重建文件清单')
        if job is None:
            return
        detail = self.state.task_detail
        if str(detail.get('artifactIndexRevision') or '') != expected_index_revision:
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

        self._start_task_mutation('task-artifact-rebuild', job, '正在根据现有处理记录重建文件清单…', work, finish)

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
            self._clear_work_task_lock()
            if (self._exit_when_idle or self.route != 'results' or self.state.selected_task_id != job
                    or isinstance(error, InterruptedError)):
                return
            if error:
                self._error(error)
                return
            self._subtitles.accept_midi(data, job=job, artifact_id=artifact_id)
            self.subtitlesRequested.emit()

        self._set_work_task_lock('字幕工具 · 正在读取任务 MIDI', job)
        if not self._work.start('artifact-check', lambda stop, _send: read_task_midi(job, row, stop.is_set), finish):
            self._clear_work_task_lock()

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
            self._clear_work_task_lock()
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error:
                self._error(error)
            else:
                self._accept_media('video_mixdown', job, rows)

        self._set_work_task_lock('任务产物 · 正在核验', job)
        if not self._work.start('artifact-check', work, finish):
            self._clear_work_task_lock()

    def _hold_tuning_handoff_lock(self, job):
        """Keep a handoff lock until Tuning exposes the same loaded task."""
        if not self._work_task_lock or self._closed:
            self._clear_work_task_lock()
            return
        try:
            loaded = str(getattr(self._tuning, 'job', '') or '') == str(job)
        except (RuntimeError, TypeError, ValueError):
            loaded = False
        if loaded or self._work.kind not in ('tuning-loading', 'tuning-load'):
            self._clear_work_task_lock()
            return
        QTimer.singleShot(100, lambda: self._hold_tuning_handoff_lock(job))
    @Slot(str, str)
    def open_independent_mix(self, path, sha256):
        """Send one verified standalone WAV to Video without claiming a task."""
        if self.workBusy:
            return
        try:
            candidate = Path(path).expanduser().resolve()
            expected = str(sha256 or '').strip().lower()
            if not candidate.is_file() or not expected:
                raise ValueError('独立 Mixdown 文件或摘要无效。')
            view = self._video.view
            current = str(view.get('mixdown') or '')
            current_path = Path(current).expanduser().resolve() if current else None
            if view.get('job'):
                raise ValueError('视频页仍关联任务；请先回到任务与结果切换输入，未覆盖现有 Mixdown。')
            if current_path is not None and current_path != candidate:
                raise ValueError('视频页已选择另一份 Mixdown；为避免覆盖，请先在视频页处理现有输入。')
        except (OSError, TypeError, ValueError) as error:
            self._error(error)
            return

        def work(stop, _send):
            from common import file_hash

            if not candidate.is_file():
                raise ValueError('独立 Mixdown 已不存在，请从简易混音重新导出。')
            if file_hash(candidate, check=stop.is_set) != expected:
                raise ValueError('独立 Mixdown 在交接前发生变化，请重新导出。')
            return str(candidate)

        def finish(verified, error):
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            try:
                view = self._video.view
                current = str(view.get('mixdown') or '')
                current_path = Path(current).expanduser().resolve() if current else None
                target = Path(verified).resolve()
                if view.get('job'):
                    raise ValueError('视频页在交接期间关联了任务；本次未覆盖 Mixdown。')
                if current_path is not None and current_path != target:
                    raise ValueError('视频页在交接期间选择了另一份 Mixdown；本次未覆盖。')
                self._video.chooseFile('mixdown', QUrl.fromLocalFile(str(target)))
                selected = str(self._video.view.get('mixdown') or '')
                if not selected or Path(selected).expanduser().resolve() != target:
                    raise ValueError('视频页未能接受独立 Mixdown。')
            except (OSError, TypeError, ValueError) as failure:
                self._error(failure)
                return
            self.navigate('video_mixdown')

        self._work.start('mixdown-handoff', work, finish)
    def _checked_artifact(self, artifact_id, *, route=None, expected_action=None):
        job = self.state.selected_task_id
        if self.workBusy or not job or self.route != 'results':
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
            if route != 'tuning':
                self._clear_work_task_lock()
            if self._exit_when_idle or isinstance(error, InterruptedError):
                self._clear_work_task_lock()
                return
            if error is not None:
                self._clear_work_task_lock()
                self._error(error)
                return
            if self.state.selected_task_id != job or self.route != 'results':
                self._clear_work_task_lock()
                return
            self.state.set_task_detail(job, detail)
            self.taskChanged.emit()
            if expected_action is not None:
                actual = quick_results.task_primary_action(detail)
                if any(actual[key] != expected_action[key] for key in ('kind', 'artifactId', 'artifactIds')):
                    self._clear_work_task_lock()
                    self._error(ValueError('当前交付已变化，请核对后再继续。'))
                    return
            by_id = {row['id']: row for row in detail['artifacts'] if row['available']}
            if artifact_id is not None:
                row = by_id.get(artifact_id)
                if not row or not row['openable'] or [artifact_identity(row)] != expected:
                    self._clear_work_task_lock()
                    self._error(ValueError('该文件已变更、无法核验或不支持直接打开。'))
                    return
                if route == 'tuning':
                    if not quick_results.is_svp_project(row):
                        self._clear_work_task_lock()
                        self._error(ValueError('只能在调音工作台加载已核验的 SVP 工程。'))
                        return
                    self.navigate('tuning')
                    self._tuning.accept_task(job, row)
                    self._hold_tuning_handoff_lock(job)
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
            if action == 'awaiting_tuning' and quick_results.is_svp_project(row):
                self._set_work_task_lock('调音工程 · 正在载入', job)
                self.navigate('tuning')
                self._tuning.accept_task(job, row)
                self._hold_tuning_handoff_lock(job)
            elif action == 'audio_ready':
                self.navigate('audio_mixdown')
                self._mixdown.accept_task(job, rows)
            else:
                self._open_local(destination)

        label = '调音工程 · 正在核验' if route == 'tuning' else '任务产物 · 正在核验'
        self._set_work_task_lock(label, job)
        def work(stop, _send):
            return quick_results.detail(job, stop.is_set, include_history=True)

        if not self._work.start('artifact-check', work, finish):
            self._clear_work_task_lock()

    def _open_local(self, path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._error(ValueError('系统无法打开此文件或目录。'))

    @Slot(str)
    @Slot(str, str)
    def renameSelectedTask(self, name, expected_id=''):
        self._mutate_task('canRename', expected_id, lambda job: quick_results.rename(job, name), '重命名任务')

    @Slot(str)
    def deleteSelectedTask(self, expected_id):
        if expected_id:
            self._mutate_task('canDelete', expected_id, quick_results.delete, '删除任务')

    def _mutate_task(self, capability, expected_id, command, action):
        job = self._mutation_job(expected_id, capability, action)
        if job is None:
            return

        def finish(_value, error):
            if error is not None:
                self._error(error)
            if not self._exit_when_idle:
                self.refreshTasks()

        self._start_task_mutation('task-edit', job, action + '…', lambda _stop, _send: command(job), finish)

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
        self._task_work.cancel()
        if self._work.kind:
            self._work.cancel()
        if not self.workBusy and not self._calibration.worker.kind:
            self.closeReady.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._task_safety_timer.stop()
        self._support.close()
        self._video.cancel()
        self._publication.cancel()
        self._calibration.close()
        for lane in (self._work, self._task_work, self._catalog, self._read, self._details):
            lane.close()
        self._tuning.close()
        self._mixdown.close()
        self._video.close()
        self._publication.close()
