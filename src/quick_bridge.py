"""Qt projections of the shared workspace, task library and worker commands."""

import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QUrl, Signal, Slot, QTimer
from PySide6.QtGui import QDesktopServices

import component_operations as components
from common import ROOT, load_config, output_directory, remember_job, save_output_directory
from download_sources import update_source_options
from i18n import tr
from operation_diagnostics import clock_text, describe, error_text, journal_for, operation_event
import preparation_commands as preparation
from preparation_languages import PROFILES
import quick_results
from quick_media_handoff import (artifact_identity, verify_preparation_vocal, verify_publication_bundle,
                                  verify_task_artifact)
from quick_publication import QuickPublication
from quick_subtitles import QuickSubtitles, read_task_midi
from quick_support import QuickSupport
from quick_calibration import QuickCalibration
from quick_preparation_sources import QuickPreparationSources
from quick_tuning import QuickTuning
from quick_workshop import QuickWorkshop
from quick_mixdown import QuickMixdown
from quick_chorus import QuickChorus
from quick_convert import QuickConvert
from quick_video import QuickVideo
from quick_work import WorkerLane
import task_actions_service as _task_actions
from workspace_controller import WorkspaceController
from workspace_settings import component_source_preferences, save_component_source_preferences
import workflow


_CANCELLABLE_TUNING = _task_actions._CANCELLABLE_TUNING


def _can_cancel(kind):
    return _task_actions.can_cancel(kind)


_TASK_STATE_FILTERS = frozenset(('all', 'done', 'pending', 'failed', 'running'))
_TASK_SORTS = frozenset(('recent', 'title'))
_PENDING_TASK_STATES = frozenset(('ready', 'partial', 'cancelled', 'interrupted'))


class RowsModel(QAbstractListModel):
    _first = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent, roles, *, key_field=None):
        super().__init__(parent)
        self.roles = tuple(roles)
        self.rows = ()
        self._key_field = key_field or (roles[0][1] if roles else None)

    def _row_key(self, row):
        if isinstance(row, dict):
            for candidate in (self._key_field, 'jobId', 'id', 'path', 'key', 'title'):
                if candidate and candidate in row and row[candidate] not in (None, ''):
                    return row[candidate]
        return None

    def replace_rows(self, rows):
        from list_diff import compute_list_diff

        incoming = tuple(rows)
        if incoming == self.rows:
            return
        ops = compute_list_diff(self.rows, incoming, self._row_key)
        if not ops:
            return
        if len(ops) == 1 and ops[0].kind == 'reset':
            self.beginResetModel()
            self.rows = incoming
            self.endResetModel()
            return
        working = list(self.rows)
        for op in ops:
            if op.kind == 'remove':
                self.beginRemoveRows(QModelIndex(), op.first, op.last)
                del working[op.first : op.last + 1]
                self.rows = tuple(working)
                self.endRemoveRows()
            elif op.kind == 'insert':
                self.beginInsertRows(QModelIndex(), op.first, op.last)
                for idx in range(op.first, op.last + 1):
                    working.insert(idx, incoming[idx])
                self.rows = tuple(working)
                self.endInsertRows()
            elif op.kind == 'update':
                for idx in range(op.first, op.last + 1):
                    working[idx] = incoming[idx]
                self.rows = tuple(working)
                self.dataChanged.emit(self.index(op.first, 0), self.index(op.last, 0))

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


def _qurl_to_local(value):
    if isinstance(value, QUrl):
        return value.toLocalFile() if value.isLocalFile() else ''
    return None


def _component_result(request, stop, send): return _task_actions.component_result(request, stop, send)
def _run_preparation(draft, existing, retry_cpu, stop, send, *, reuse_job='', reprocess=''): return _task_actions.run_preparation(draft, existing, retry_cpu, stop, send, reuse_job=reuse_job, reprocess=reprocess)
def _controller_view(controller): return _task_actions.controller_view(controller)
def _reference_paths(values): return _task_actions.reference_paths(values, url_to_local_fn=_qurl_to_local)
def _task_reference_groups(**kwargs): return _task_actions.task_reference_groups(**kwargs, url_to_local_fn=_qurl_to_local)


class QuickBridge(QObject):
    verify_task_artifact = staticmethod(verify_task_artifact)
    verify_publication_bundle = staticmethod(verify_publication_bundle)

    navigationChanged = Signal()
    draftChanged = Signal()
    selectionChanged = Signal()
    componentTaskPlanChanged = Signal()
    queryChanged = Signal()
    optionsChanged = Signal()
    componentSourceChanged = Signal()
    activityChanged = Signal()
    operationActivityChanged = Signal()
    preparationTaskChanged = Signal()
    preparationPresetsChanged = Signal()
    taskChanged = Signal()
    taskSafetyChanged = Signal()
    taskArtifactChanged = Signal()
    taskPrimaryActionChanged = Signal()
    subtitlesRequested = Signal()
    toastRequested = Signal(str, str)
    closeReady = Signal()

    def __init__(self):
        super().__init__()
        self.journal = journal_for()
        self.state = WorkspaceController(component_source_preferences(load_config(False)))
        self.selectionChanged.connect(self.componentSourceChanged)
        self._work = WorkerLane(self, 'vwa-workspace', journal=self.journal)
        self._catalog = WorkerLane(self, 'vwa-components', journal=self.journal)
        self._read = WorkerLane(self, 'vwa-task-library', journal=self.journal)
        self._details = WorkerLane(self, 'vwa-task-detail', journal=self.journal)
        self._task_work = WorkerLane(self, 'vwa-task-mutations', journal=self.journal)
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
        self._support = QuickSupport(self, self._work, update_source_options())
        self._tuning = QuickTuning(self, self._work)
        self._workshop = QuickWorkshop(self, self._tuning, self._support, self._work)
        self._workshop_vocal_pick = False
        self._calibration = QuickCalibration(self, self._tuning)
        self._calibration.presetSaved.connect(self._workshop.calibrationSaved)
        self._calibration.worker.changed.connect(self._work_changed)
        self._mixdown = QuickMixdown(self, self._work)
        self._chorus = QuickChorus(self)
        self._chorus.worker.changed.connect(self._work_changed)
        self._chorus.mixdownHandoffReady.connect(self._open_chorus_mixdown)
        from quick_recovery import QuickRecoveryBridge
        self._recovery = QuickRecoveryBridge(self, self._chorus, self._mixdown)
        self._recovery.toastRequested.connect(self.toastRequested)
        from quick_ux_hub import QuickUxHub
        self._ux = QuickUxHub(self)
        self._converter = QuickConvert(self)
        self._converter.worker.changed.connect(self._work_changed)
        self._converter.tuningHandoffRequested.connect(self._open_converted_in_tuning)
        self._converter.chorusScoreHandoffRequested.connect(self._open_converted_in_chorus)
        self._video = QuickVideo(self, self._work)
        self._publication = QuickPublication(self, self._work)
        self._subtitles = QuickSubtitles(self)
        self._sources = QuickPreparationSources(self, self._work)
        self._video.publishRequested.connect(self._open_verified_video)
        self._tuning.presetLibraryChanged.connect(self._mixdown.refresh_presets)
        for signal in (self._mixdown.viewChanged, self._mixdown.tracksChanged, self._tuning.changed,
                       self._video.changed, self._publication.viewChanged, self._calibration.changed,
                       self._subtitles.changed, self.preparationTaskChanged, self._chorus.tracksChanged):
            signal.connect(self._schedule_task_safety)
        # Settings observes the actual cross-page operation owners used by the
        # deferred update restart, not just the preparation/component lane.
        for signal in (self._tuning.changed, self._video.changed,
                       self._publication.viewChanged, self._calibration.changed,
                       self._mixdown.viewChanged, self._chorus.worker.changed,
                       self._converter.worker.changed,
                       self._catalog.changed, self._read.changed, self._details.changed,
                       self._task_work.changed, self.preparationTaskChanged):
            signal.connect(self._support.refreshActivity)
        saved_workflow = workflow.read_state()
        self._legacy_workflow = saved_workflow
        self._legacy_workflow_presets = dict(saved_workflow['presets'])
        from preset_library import PresetLibrary
        self._preparation_preset_library = PresetLibrary(ROOT / 'user-presets')
        self._preparation_presets = []
        self._preparation_preset_rows = {}
        self._preparation_preset_failure = ''
        QTimer.singleShot(0, self._refresh_preparation_presets) if os.environ.get('VWA_TESTING') != '1' else self._refresh_preparation_presets()
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
        self._dismissed_issue = ''
        self._activities = (self._work, self._task_work, self._calibration.worker, self._catalog,
                            self._read, self._details, self._support.storage_lane,
                            self._support.preset_lane, self._support.inspect_lane, self._support.discovery,
                            self._mixdown.support_lane, self._chorus.worker, self._converter.worker)
        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(1000)
        self._operation_timer.timeout.connect(self.operationActivityChanged)
        for lane in self._activities:
            lane.activityChanged.connect(self._refresh_activity)

    @Property(bool, notify=operationActivityChanged)
    def hasActiveOperations(self):
        # Quiet catalog reads and the automatic startup check may finish while
        # closing. Only operations initiated by the user need the exit choice.
        return any(lane.kind for lane in (self._work, self._task_work, self._calibration.worker,
                                          self._support.storage_lane, self._support.preset_lane,
                                          self._chorus.worker, self._converter.worker))

    def _refresh_activity(self):
        busy = any(lane.kind for lane in self._activities)
        if busy and not self._operation_timer.isActive():
            self._operation_timer.start()
        elif not busy:
            self._operation_timer.stop()
        self.operationActivityChanged.emit()
        if not self.hasActiveOperations and self._exit_when_idle and not self._closed:
            self.closeReady.emit()

    def report_problem(self, kind, error, *, route=''):
        self.journal.problem(kind, error, route=route)
        self.operationActivityChanged.emit()

    @Slot(str)
    def cancelActiveOperation(self, identifier):
        for lane in self._activities:
            row = lane.activity
            if row.get('id') != identifier:
                continue
            kind = row['kind']
            if not _can_cancel(kind) or row['cancelling']:
                return
            if kind == 'component':
                self.cancelOperation()
            elif kind == 'preparation':
                self.cancelPreparation()
            elif kind == 'update-download':
                self._support.cancelUpdate()
            elif kind.startswith('video-'):
                self._video.cancel()
            elif kind.startswith('calibration-'):
                self._calibration.cancel()
            elif kind.startswith('workshop-'):
                self._workshop.cancel()
            elif kind.startswith('chorus-'):
                self._chorus.cancel()
            elif kind.startswith('tuning-'):
                self._tuning.cancel()
            elif kind in ('mixdown-export', 'mixdown-install', 'mixdown-support-probe'):
                self._mixdown.cancel()
            elif kind.startswith('publication-'):
                self._publication.cancel()
            return

    @Property('QVariantList', notify=operationActivityChanged)
    def activeOperations(self):
        from task_store import format_size
        active = []
        for lane in self._activities:
            row = lane.activity
            if not row:
                continue
            detail = row['detail']
            row['label'] = tr(row['label'])
            row['detail'] = tr(detail) if detail else row['label']
            kind = row['kind']
            row['canCancel'] = _can_cancel(kind)
            row['timing'] = tr('已等待 {0}', clock_text(row['elapsed']))
            if row['cancelling']:
                row['detail'] = tr('正在取消，等待安全收尾…')
            elif row['quiet'] >= 15 and row['phase'] == 'waiting':
                row['timing'] = tr('正在等外部编辑器导出 · 已等待 {0}', clock_text(row['elapsed']))
            elif row['quiet'] >= 15:
                row['timing'] = tr('等待下一次进度 · 已等待 {0}', clock_text(row['elapsed']))
            if row.get('total'):
                row['fraction'] = row['done'] / row['total']
                unit = row['unit']
                row['count'] = (f'{format_size(row["done"])} / {format_size(row["total"])}' if unit == 'bytes'
                                else f'{row["done"]:g} / {row["total"]:g}' if unit == 'items'
                                else f'{row["fraction"]:.0%}')
            else:
                row['count'] = ''
            active.append(row)
        return active

    @Property('QVariantMap', notify=operationActivityChanged)
    def latestOperationIssue(self):
        issue = self.journal.latest_failure(self._dismissed_issue)
        if not issue:
            return {}
        label, _ = describe(issue.get('kind', ''))
        title, hint = error_text(issue.get('code'))
        details = self._notice if issue.get('kind') == 'workspace' else ''
        return {**issue, 'title': f'{tr(label)} · {tr(title)}', 'hint': tr(hint), 'details': details}

    @Slot(str)
    def dismissOperationIssue(self, identifier):
        if identifier:
            self._dismissed_issue = identifier
        self._notice = ''
        self.operationActivityChanged.emit()
        self.activityChanged.emit()

    def _error(self, error):
        self._notice = str(error)[:1600] or error.__class__.__name__
        if not any(lane.completing_failure for lane in self._activities):
            self.report_problem('workspace', error, route=self.state.route)
        self.activityChanged.emit()

    def _work_changed(self):
        self.activityChanged.emit()

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
            chorus_paths=self._chorus.source_paths,
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
    def workshop(self):
        return self._workshop

    @Property(QObject, constant=True)
    def preparationSources(self):
        return self._sources

    @Property(QObject, constant=True)
    def mixdown(self):
        return self._mixdown

    @Property(QObject, constant=True)
    def chorus(self):
        return self._chorus

    @Property(QObject, constant=True)
    def video(self):
        return self._video

    @Property(QObject, constant=True)
    def publication(self):
        return self._publication

    @Property(QObject, constant=True)
    def subtitles(self):
        return self._subtitles

    @Property(QObject, constant=True)
    def converter(self): return self._converter

    @Property(QObject, constant=True)
    def recovery(self): return self._recovery

    @Property(QObject, constant=True)
    def ux(self): return self._ux

    @Slot(str)
    @Slot(str, str)
    def pushToast(self, message, kind='info'):
        text = str(message or '').strip()
        if text:
            self.toastRequested.emit(tr(text), str(kind or 'info'))

    @Property(str, notify=activityChanged)
    def operationKind(self):
        return self._task_work.kind or self._work.kind

    @Property(str, notify=navigationChanged)
    def route(self):
        return self.state.route

    @Property('QVariantList', constant=True)
    def routes(self):
        icons = {
            'processing': 'nav_processing',
            'tuning': 'nav_tuning',
            'tuning_workshop': 'nav_workshop',
            'chorus': 'nav_chorus',
            'audio_mixdown': 'nav_mixdown',
            'video_mixdown': 'nav_video',
            'publishing': 'nav_publishing',
            'project_convert': 'nav_convert',
            'components': 'nav_components',
            'results': 'nav_results',
            'updates': 'nav_settings',
        }
        return [{'pageId': spec.page_id, 'title': tr('设置与支持' if spec.page_id == 'updates' else spec.title),
                 'short': tr({'processing': '准备', 'tuning': '调音', 'tuning_workshop': '工坊', 'chorus': '修音',
                              'audio_mixdown': '混音',
                              'video_mixdown': '视频', 'publishing': '发布', 'project_convert': '互转',
                              'components': '组件', 'results': '任务', 'updates': '设置'}[spec.page_id]),
                 'icon': icons.get(spec.page_id, '')}
                for spec in self.state.routes]

    @Property('QVariantMap', notify=draftChanged)
    def preparation(self):
        return self.state.draft_view

    @Property('QVariantList', constant=True)
    def preparationLanguages(self):
        return [{'label': tr(profile.label), 'value': code} for code, profile in PROFILES.items()]

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
                 'BS-RoFormer · ' + {'cpu': 'CPU', 'webgpu': 'WebGPU', 'cuda': 'WebGPU', 'auto': tr('自动选择运行库')}[draft['roformer_device']]
                 if separator == 'roformer' else tr('内置分离')]
        if draft['target'] != 'separation':
            english = draft['language'] == 'en'
            lines.append('Vocal2Midi ' + ('2.0' if english or draft['midi_backend'] == 'v2' else '1.0'))
            if draft['recognize_lyrics']:
                lines.append(tr(PROFILES[draft['language']].recognition_label))
                lines.append('TiFA' if not english and draft['alignment_engine'] == 'tifa' else 'HubertFA')
        return {**plan, 'summary': ' · '.join(lines)}

    @Property(str, notify=selectionChanged)
    def selectedId(self):
        return self.state.selected_id

    @Property(str, notify=queryChanged)
    def query(self):
        return self.state.query

    @Property('QVariantList', notify=componentSourceChanged)
    def downloadSources(self):
        return [{'label': tr(label), 'value': value} for label, value in self.state.source_options]

    @Property(str, notify=componentSourceChanged)
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
        if self.state.route != 'results':
            self._workshop_vocal_pick = False
        self.navigationChanged.emit()
        if self.state.route == 'results':
            self._invalidate_task_safety(emit=False)
            self._schedule_task_safety()
            self.refreshTasks()
        elif self.state.route == 'components' and self.state.rows_runtime != self.state.runtime:
            self.refresh()
        elif self.state.route == 'tuning':
            self._tuning.refresh_presets()
        elif self.state.route == 'tuning_workshop':
            self._workshop.enter()
        elif self.state.route == 'audio_mixdown':
            self._mixdown.enter()
            self._mixdown.refresh_presets()
        elif self.state.route == 'processing':
            QTimer.singleShot(0, self._refresh_preparation_presets) if os.environ.get('VWA_TESTING') != '1' else self._refresh_preparation_presets()
        elif self.state.route == 'updates' and self._support.presetLibrary['loaded']:
            self._support.refreshPresetLibrary()

    @Slot()
    def returnToPreparation(self):
        self.navigate(self.state.return_route)

    @Slot(str)
    def openTuningWorkshop(self, pane):
        self._workshop.setPane(pane)
        self.navigate('tuning_workshop')

    @Property(bool, notify=taskChanged)
    def workshopVocalPick(self):
        return self._workshop_vocal_pick

    @Slot()
    def openWorkshopSeparation(self):
        if self.workBusy or self._closed:
            return
        draft = self.state.draft.edit('target', 'separation')
        if draft.voice_mode == 'import':
            draft = draft.edit('voice_mode', 'dual')
        self._update_draft(lambda: self.state.set_draft(draft))
        self.navigate('processing')

    @Slot(bool)
    def chooseWorkshopVocal(self, from_preparation):
        self._workshop_vocal_pick = True
        self.state.task_query = ''
        self.setTaskStateFilter('all')
        if from_preparation and self.state.preparation_task['job']:
            self.viewPreparationResult()
        else:
            self.navigate('results')
        self.taskChanged.emit()

    @Slot(str)
    def attachWorkshopVocal(self, artifact_id):
        job = self.state.selected_task_id
        if self.workBusy or not job or self.route != 'results' or self._closed:
            return
        detail = self.state.task_detail
        if artifact_id not in detail.get('referenceVocalRoles', {}):
            return
        row = next((item for item in detail.get('artifacts', ()) if item['id'] == artifact_id), None)
        if row is None:
            return

        def work(stop, _send):
            return verify_preparation_vocal(job, row, check=stop.is_set)

        def finish(value, error):
            self._clear_work_task_lock()
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            if self.route == 'results' and self.state.selected_task_id == job:
                actual, role = value
                self._workshop.acceptPreparedVocal(job, detail.get('title', ''), actual, role)
                self.openTuningWorkshop('style')

        self._set_work_task_lock('工坊干声 · 正在核验', job)
        if not self._work.start('workshop-vocal-handoff', work, finish, route='results',
                                label='正在核对准备任务的人声…'):
            self._clear_work_task_lock()

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

    @Slot(str, str, bool)
    def setPreparationExportFormat(self, field, format_id, selected):
        self._update_draft(lambda: self.state.set_draft(
            self.state.draft.with_export_format(field, format_id, selected)), persist=True)

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
            QTimer.singleShot(0, self._refresh_preparation_presets) if os.environ.get('VWA_TESTING') != '1' else self._refresh_preparation_presets()
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
            QTimer.singleShot(0, self._refresh_preparation_presets) if os.environ.get('VWA_TESTING') != '1' else self._refresh_preparation_presets()
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
                    stages=[], stage_progress={}, log_tail='', recovery_title='', recovery_action='', recovery_message='')
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

        def outcome(value):
            state = value.get('state')
            return ('succeeded' if state == 'done' else state or 'failed', value.get('message', ''))

        started = self._work.start('preparation', lambda stop, send: _run_preparation(
            draft, existing, retry_cpu, stop, send, reuse_job=reuse_job, reprocess=reprocess), finish, progress,
            label='正在准备任务…', outcome=outcome)
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
        if self.workBusy or self.loading:
            return
        try:
            if self.state.set_download_source(source, save=save_component_source_preferences):
                self.componentSourceChanged.emit()
        except OSError as exc:
            self._error(exc)
            self.componentSourceChanged.emit()

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
            if self.state.progress(value['operation_id'], value['done'], value['total'], value['message']):
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
                self._sources.invalidate_component_check()
            self.state.complete(request.operation_id, status, message)
            self.selectionChanged.emit()
            if not self._exit_when_idle:
                self.refresh()

        started = self._work.start('component', lambda stop, send: _component_result(request, stop, send), finish, progress,
                         label='正在准备组件…', facts={'component': request.component_id, 'runtime': request.runtime
                             if request.component_id == 'roformer' else '', 'action': request.action,
                             'source': request.source if request.action == 'install' else ''},
                         outcome=lambda value: value[:2])
        if not started:
            self.state.complete(request.operation_id, 'failed', '已有正在执行的组件操作，请稍候。')
            self.activityChanged.emit()

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
            return _task_actions.inspect_task_progress_work(expected_job, stop)

        def finish(view, error):
            self._clear_work_task_lock()
            if self.state.selected_task_id != expected_job:
                return
            if error is not None:
                self._error(error)
                return
            self.state.preparation_task.update(id='', recovery_action='', recovery_title='', recovery_message='', log_tail='')
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
    def _open_chorus_mixdown(self, payload):
        """Receive same-origin stems from the Pitch & Timing workbench and open Simple Mixdown."""
        self.navigate('audio_mixdown')
        self._mixdown.accept_chorus_handoff(payload)

    @Slot(str, str)
    def open_independent_mix(self, path, sha256):
        """Send one verified standalone WAV to Video without claiming a task."""
        if self.workBusy:
            return
        try:
            candidate, expected = _task_actions.validate_independent_mix_candidate(
                path, sha256, self._video.view,
            )
        except (OSError, TypeError, ValueError) as error:
            self._error(error)
            return

        def work(stop, _send):
            return _task_actions.verify_independent_mix_work(candidate, expected, stop)

        def finish(verified, error):
            if self._exit_when_idle or isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            try:
                target = _task_actions.validate_independent_mix_commit(verified, self._video.view)
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
        wanted = _task_actions.wanted_artifact_ids(shown, artifact_id, route)
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
            try:
                plan = _task_actions.resolve_checked_artifact_plan(
                    shown, detail, job, artifact_id, route, expected, expected_action,
                )
            except (OSError, ValueError) as failure:
                self._clear_work_task_lock()
                self._error(failure)
                return
            kind = plan['action']
            if kind == 'tuning':
                row = plan['row']
                if not quick_results.is_tuning_project(row):
                    self._clear_work_task_lock()
                    self._error(ValueError('请选择调音工作台支持且已核验的工程。'))
                    return
                self.navigate('tuning')
                self._tuning.accept_task(job, row)
                self._hold_tuning_handoff_lock(job)
            elif kind == 'open_local':
                self._open_local(plan['path'])
            elif kind == 'accept_media':
                self._accept_media(plan['route'], job, plan['rows'])
            elif kind == 'audio_mixdown':
                self.navigate('audio_mixdown')
                self._mixdown.accept_task(job, plan['rows'])
            elif kind == 'dispatch':
                row = plan['row']
                self.state.handoff = plan['handoff']
                self.taskChanged.emit()
                if plan['handoff_kind'] == 'awaiting_tuning' and quick_results.is_tuning_project(row):
                    self._set_work_task_lock('调音工程 · 正在载入', job)
                    self.navigate('tuning')
                    self._tuning.accept_task(job, row)
                    self._hold_tuning_handoff_lock(job)
                elif plan['handoff_kind'] == 'audio_ready':
                    self.navigate('audio_mixdown')
                    self._mixdown.accept_task(job, plan['rows'])
                else:
                    self._open_local(plan['destination'])

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

    def _open_converted_in_tuning(self, path_str):
        if self._closed or not path_str:
            return
        self.navigate('tuning')
        self._tuning.chooseProject(QUrl.fromLocalFile(str(path_str)))

    def _open_converted_in_chorus(self, path_str):
        if self._closed or not path_str:
            return
        self.navigate('chorus')
        self._chorus.importScore(QUrl.fromLocalFile(str(path_str)))

    @Slot()
    def cancelAndClose(self):
        self._exit_when_idle = True
        self.cancelOperation()
        self.cancelPreparation()
        self._support.cancelUpdate()
        self._tuning.cancel()
        self._workshop.cancel()
        self._mixdown.cancel()
        self._chorus.cancel()
        self._converter.cancel()
        self._video.cancel()
        self._publication.cancel()
        self._calibration.cancel()
        for lane in self._activities:
            lane.cancel()
        if not self.hasActiveOperations:
            self.closeReady.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._task_safety_timer.stop()
        self._operation_timer.stop()
        self._support.close()
        self._video.cancel()
        self._publication.cancel()
        self._calibration.close()
        for lane in (self._work, self._task_work, self._catalog, self._read, self._details):
            lane.close()
        self._tuning.close()
        self._mixdown.close()
        self._chorus.close()
        self._recovery.close()
        self._converter.close()
        self._video.close()
        self._publication.close()
