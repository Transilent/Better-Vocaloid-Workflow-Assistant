"""Application-owned Quick tuning workflow. QML observes state and sends commands."""

from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from common import ROOT, atomic_json, file_hash, native_path, output_directory, workspace_state
from i18n import tr
from preset_library import PresetLibrary
import style_catalog
from style_drafts import DraftConflict, DraftStore
from style_presets import DETAIL_DEFAULTS, region_settings, time_range_settings
import tuning_engine as engine
from tuning_execution import EDITOR_OPTION_KEYS, PRESERVE_FOR_EDIT, editor_options
from tuning_note_detail import DETAILS
from tuning_pitch_layer import (EDGE_SECONDS, FORMATS as DRAW_FORMATS, MAX_CENTS, MIN_STROKE_SECONDS,
                                UNSUPPORTED as DRAW_UNSUPPORTED)
from tuning_score_edits import FORMATS as SCORE_EDIT_FORMATS, UNSUPPORTED as SCORE_EDIT_UNSUPPORTED
from tuning_timeline import (ProjectNotes, TimelineDraftStore, batch_lyric_row, draft_options, freeze_plan,
                             normalize_timeline_draft)
from tuning_preset_documents import make_document, plan_application
import tuning_workflow as workflow


_REFERENCE_FLAGS = ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing')
_SOURCES = ('preserve', 'reference', 'calibrated', 'retained')
_BRUSH_KEYS = ('performance_style', 'style_strength', 'style_pitch', 'style_detail',
               'style_technique', 'technique_strength', 'technique_pitch')
_PORTABLE_PREFERENCE_KEYS = frozenset(_BRUSH_KEYS + ('dynamics_strength', 'expression_density'))
_SETTING_KEYS = _REFERENCE_FLAGS + ('dynamics_strength', 'expression_density', 'style_technique',
                                   'technique_strength', 'technique_pitch')
_REGION_UNDO_LIMIT = 32
_REGION_BRUSH_FIELDS = {
    'preset': 'performance_style', 'strength': 'style_strength', 'pitch': 'style_pitch',
    'detail': 'style_detail', 'technique': 'style_technique', 'technique_strength': 'technique_strength',
    'technique_pitch': 'technique_pitch',
}


class SourceChanged(ValueError):
    """The loaded source or its task revision is no longer current."""


def _read_json_file(path, *, limit=2 * 1024 * 1024):
    """Read a small workflow sidecar without trusting its size or schema."""
    path = Path(path)
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _session_candidate(session, reason=''):
    """Return the reverified result, or preserve a safe historical output."""
    try:
        return workflow.verified_result(session), ''
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = str(error)
    root = Path(session).resolve().parent
    state = _read_json_file(root / 'status.json')
    if not state or not isinstance(state.get('output'), str):
        return None, reason
    try:
        declared = Path(state['output'])
        output = workflow._owned(root, declared if declared.is_absolute() else root / declared)
        if not output.is_file():
            return None, reason
        output_sha256 = file_hash(output)
        output_bytes = output.stat().st_size
        workflow.project_format(output)
        receipt_path = workflow._owned(root, workflow.receipt_path_for(output))
    except (OSError, ValueError):
        return None, reason
    receipt = _read_json_file(receipt_path)
    if receipt and (receipt.get('session') not in (None, str(Path(session).resolve()))
                    or receipt.get('output') not in (None, str(output))):
        return None, reason
    report = receipt.get('report', {}) if receipt else {}
    return {
        'output': str(output), 'output_sha256': output_sha256, 'output_bytes': output_bytes,
        'receipt': str(receipt_path) if receipt_path.is_file() else '',
        'report': report if isinstance(report, dict) else {}, 'status': state.get('state', 'historical'),
        'receipt_verified': False, 'historical': True, 'historical_reason': reason,
    }, reason


def _session_intent(session, project):
    """Recover the last execution options and note identity where they are representable."""
    root = Path(session).resolve().parent
    warning = ''
    options = QuickTuning._defaults()
    raw_options = _read_json_file(root / 'last-options.json')
    if raw_options is not None:
        try:
            options = draft_options(raw_options)
            project = _draft_projection(project, {'options': options, 'regions': []})
        except (TypeError, ValueError) as error:
            warning = str(error)
            options = QuickTuning._defaults()
    recipes = options.get('style_regions', []) if isinstance(options, dict) else []
    by_index = {row['index']: key for key, row in project.tracks.items() if key in project.editable}
    regions = []
    for recipe in recipes if isinstance(recipes, list) else []:
        try:
            track = by_index[recipe['track_index']]
            brush = {key: deepcopy(recipe.get(value, engine.DEFAULTS[value]))
                     for key, value in _REGION_BRUSH_FIELDS.items()}
            region = project.region(track, recipe['start'], recipe['end'], brush)
            if region is not None:
                regions.append(region)
        except (KeyError, TypeError, ValueError) as error:
            warning = warning or str(error)
    options = draft_options({**options, 'style_regions': []})
    preset = None
    if not regions:
        try:
            preset = workflow.read_preset_binding(session)
        except (OSError, TypeError, ValueError) as error:
            warning = warning or str(error)
    payload = normalize_timeline_draft(project.sha256, {
        'schema_version': 2, 'input_sha256': project.sha256, 'regions': regions,
        'options': options, 'preset': preset, 'confirmed': False,
    })
    return payload, warning


def _stop_check(stop):
    if stop.is_set():
        raise InterruptedError('调音操作已取消。')


def _verify_input(project, job, artifact, stop):
    _stop_check(stop)
    path = Path(project.project['path'])
    if not path.is_file() or file_hash(path, check=stop.is_set) != project.sha256:
        raise SourceChanged('源工程已改变，请重新载入；现有候选文件会保留。')
    if artifact:
        from quick_results import detail

        inspected = detail(job, stop.is_set)
        identity = ('id', 'revision', 'sha256', 'path')
        if not any(row['available'] and all(row.get(key) == artifact.get(key) for key in identity)
                   for row in inspected['artifacts']):
            raise SourceChanged('任务中的源工程或上游版本已改变，请从任务与结果重新交接。')


def _load_project(path, job, artifact, drafts, legacy, sessions, stop):
    _stop_check(stop)
    project = ProjectNotes.load(path, expected_sha256=artifact.get('sha256') if artifact else None)
    _verify_input(project, job, artifact, stop)
    restored = drafts.read(project.sha256)
    previous = legacy.read(project.sha256) if not restored['exists'] else {'data': None, 'revision': None}
    key = engine.digest([project.project['path'], project.sha256, job])[:24]
    root = sessions / key
    session = root / 'session.json'
    if Path(native_path(session)).is_file():
        data = workflow.read_session(session)
        if (data['input_sha256'], data['source'], data['job']) != (project.sha256, project.project['path'], job or None):
            raise ValueError('已有调音会话的源文件或任务不匹配。')
        result, warning = _session_candidate(session)
    else:
        # An interrupted creation can leave a directory without a valid session.
        if Path(native_path(root)).exists():
            session = root.with_name(key + '-' + uuid4().hex[:8]) / 'session.json'
        result, warning = None, ''
    return project, restored, previous, session, result, warning


def _resume_project(session, drafts, legacy, stop):
    """Load any existing session from its immutable input snapshot."""
    path = Path(session).resolve()
    path = path / 'session.json' if path.is_dir() else path
    spec = workflow.read_session(path)
    # The snapshot is the editable baseline. A changed original is reported
    # by the run/registration checks and never replaces that baseline.
    source_warning = ''
    try:
        workflow.inspect_session_project(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        source_warning = str(error)
    snapshot = path.parent / spec['input_file']
    project = ProjectNotes.load(snapshot, expected_sha256=spec['input_sha256'])
    project.project['candidate_origin'] = deepcopy(spec.get('input_origin'))
    _verify_input(project, '', None, stop)
    restored = drafts.read(project.sha256)
    previous = legacy.read(project.sha256) if not restored['exists'] else {'data': None, 'revision': None}
    if restored['exists']:
        intent, warning = restored, ''
    else:
        payload, warning = _session_intent(path, project)
        intent = {'data': payload, 'revision': None, 'recoveries': []}
    result, result_warning = _session_candidate(path)
    warning = ' '.join(value for value in (source_warning, warning, result_warning if result else '') if value)
    return project, intent, previous, path, result, warning, spec['source'], spec.get('job') or ''


def _preview(project, options, stop, send):
    """Compute parameter differences and the actual pitch layer before manual edits."""
    snapshot = workflow.read_source(project.project['path'])
    if snapshot.sha256 != project.sha256:
        raise SourceChanged('预览期间源工程已改变，请重新载入。')
    format, data = snapshot.format, snapshot.data
    if options['expression_mode'] != 'preserve':
        indices = workflow.check_targets(data, options, format)
        if options['expression_mode'] in ('reference', 'calibrated'):
            engine.sources_for(options, indices)
        else:
            for binding in engine.retained_rows(options, indices):
                path = Path(binding['project'])
                if not path.is_file() or workflow.project_format(path) != format:
                    raise ValueError('沿用原参数需要选择与当前工程同格式的文件。')
        return tr('已核对轨道和参数来源。参考音频、校准或沿用参数将在生成时处理。'), {}
    style = {}
    cancel = lambda: _stop_check(stop)

    def capture(data):
        style.update(project.pitch_layer.curve_sources(data, cancel=cancel))

    _, report = workflow.apply_snapshot(snapshot, options, cancel=cancel, progress=send, observe_style=capture)
    indices = report['track_indices']
    curves = len(report['curve_changes'])
    if options.get('note_edits'):
        label = tr('预览：{tracks} 轨 · {notes} 个音符已修改 · {regions} 个风格片段 · {curves} 条曲线有变化。')
    else:
        label = tr('预览：{tracks} 轨 · {regions} 个逐轨片段 · {curves} 条曲线有变化。音符和时值保留。')
    label = label.format(tracks=len(indices), regions=len(options['style_regions']), curves=curves,
                         notes=len(report.get('note_changes', [])))
    if options.get('pitch_strokes'):
        label += ' ' + tr('手绘 {count} 段；覆盖风格音高偏移。').format(count=report['hand_pitch']['spans'])
    if format in workflow.STYLE_ADAPTERS and (not EDITOR_OPTION_KEYS.intersection(options) or options['style_regions']):
        adapter_note = {'vpr':' VPR 只写 DYN 和 PIT；其他配方参数保留原样。',
                        'ustx':' USTX 只写 DYN 和 PITD；其他配方参数保留原样。'}.get(format)
        label += tr(adapter_note) if adapter_note else ' ' + tr(workflow.style_adapter(format).STYLE_ONLY)
        if report.get('native_curve_skips'):
            label += tr(' 有原生曲线未能安全叠加，请核对回执。')
    elif report['status'] == 'partial':
        label += tr(' 有局部参数无法安全应用，生成后请核对结果。')
    return label, style


def _draft_projection(project, payload):
    options = payload['options'] if payload else {}
    result = project.with_edits(options.get('note_edits', []), options.get('pitch_strokes', []))
    if payload:
        result.verify_regions(payload['regions'])
    return result


class QuickTuning(QObject):
    changed = Signal()
    notesChanged = Signal()
    matchesChanged = Signal()
    tracksChanged = Signal()
    regionsChanged = Signal()
    catalogChanged = Signal()
    presetLibraryChanged = Signal()
    noteLocated = Signal(int, str, str)

    def __init__(self, parent, worker, *, root=ROOT):
        super().__init__(parent)
        self.worker, self.root = worker, Path(root)
        self.project = None
        self.job, self.artifact = '', None
        self.session, self.candidate = None, None
        self._session_parent = (output_directory() / 'tuning-sessions').resolve()
        self._display_source = ''
        self._session_warning = ''
        self._portable_preferences_imported = False
        self._reload_session = None
        self._tuning_tasks = []
        self._tuning_task_projects = []
        self._selected_tuning_task = ''
        self._task_picker_message = ''
        self._saved_copy = ''
        self.state, self.message = 'idle', ''
        self._candidate_stale = False
        self._plan = None
        self._summary = ''
        self._confirmed = False
        self._preset = None
        self._preset_review = None
        self._mode, self._active, self._selected = 'single', '', []
        self._score_tool = 'note'
        self._parameter_style = {}
        self._plot_cache = (None, {})
        self._lyric_batch = None
        self._edit_issue = None
        self._start, self._end, self._left, self._span = 0., 0., 0., 18.
        self._selected_region = ''
        self._selected_notes = ()
        self._query = ''
        self._matches, self._notes, self._regions = [], [], []
        self._lyric_match_index = -1
        self._region_undo, self._region_redo = [], []
        self._view_preferences_file = self.root / 'cache/quick-tuning-view.json'
        self._view_preferences = self._load_view_preferences()
        self._options = self._defaults()
        self._legacy = None
        self._legacy_revision = None
        self._draft_revision = None
        self._dirty = False
        self._draft_conflict = None
        self._draft_review = None
        self._drafts = TimelineDraftStore(self.root / 'cache/style-region-drafts/v2', lock_timeout=.2)
        self._legacy_drafts = DraftStore(self.root / 'cache/style-region-drafts', lock_timeout=.2)
        self._library = PresetLibrary(self.root / 'user-presets')
        self._presets = {}
        self._autosave = QTimer(self)
        self._autosave.setInterval(400)
        self._autosave.setSingleShot(True)
        self._autosave.timeout.connect(self._flush_draft)
        self.worker.changed.connect(self.changed)
        bridge_task_changed = getattr(parent, 'taskChanged', None)
        if bridge_task_changed is not None:
            bridge_task_changed.connect(self._bridge_tasks_changed)

    @staticmethod
    def _defaults():
        return engine.settings({'performance_style': style_catalog.DEFAULT_NEW_STYLE_ID, 'style_pitch': False,
                                'expression_density': 'original'})

    def _new_project_defaults(self):
        """Import only portable legacy style/expression preferences for a new project."""
        self._portable_preferences_imported = False
        try:
            legacy = workspace_state().get('tuning', {}).get('preferences', {})
        except (AttributeError, OSError, TypeError, ValueError):
            legacy = {}
        portable = ({key: deepcopy(legacy[key]) for key in _PORTABLE_PREFERENCE_KEYS
                     if key in legacy} if isinstance(legacy, dict) else {})
        options = self._defaults()
        if self.project and self.project.project['format'] in ('acep', 'ufdata'):
            return {**options, 'style_pitch': True}
        if not portable:
            return options
        try:
            options = engine.settings({**options, **portable})
        except (KeyError, TypeError, ValueError):
            return self._defaults()
        self._portable_preferences_imported = True
        return options

    def _load_view_preferences(self):
        try:
            values = json.loads(self._view_preferences_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            values = {}
        values = values if isinstance(values, dict) else {}
        division = values.get('beat_division', 0)
        return {'snap': values.get('snap', True) is not False,
                'auto_apply_selection': values.get('auto_apply_selection', True) is not False,
                'parameter_open': values.get('parameter_open') is True,
                'beat_division': division if type(division) is int and division in (4, 8, 16) else 0}

    def _set_view_preference(self, key, enabled):
        if self._view_preferences[key] == enabled:
            return
        updated = {**self._view_preferences, key: enabled}
        try:
            atomic_json(self._view_preferences_file, updated)
        except OSError as error:
            self._error(error)
            return
        self._view_preferences = updated
        self.changed.emit()

    @property
    def _origin(self):
        return self.project.project.get('candidate_origin') if self.project else None

    @property
    def _targets(self):
        if not self.project:
            return []
        if self._mode == 'single':
            return [self._active] if self._active in self.project.editable else []
        return [key for key in self._selected if key in self.project.editable]

    def _indices(self, *, segments=False):
        keys = ([row['track_id'] for row in self._regions] if segments and self._regions else self._targets)
        return sorted({self.project.tracks[key]['index'] for key in keys}) if self.project else []

    def _source_mode_reason(self, mode, indices):
        if not self.project or mode == 'preserve':
            return ''
        if self.project.has_score_changes:
            return PRESERVE_FOR_EDIT
        return self.project.source_reason(mode, indices)

    def _execution(self):
        if not self.project:
            raise ValueError('请先载入调音工程。')
        if self._origin and not self._confirmed:
            raise ValueError('请确认是否把已生成的候选当作新基线。')
        if self._legacy:
            raise ValueError('请先处理这个工程的旧版草稿。')
        if self._preset:
            if self._preset['missing']:
                raise ValueError(' '.join(self._preset['missing']))
            self._check_preset_source(self._preset['document'])
            return deepcopy(self._preset['options']), deepcopy(self._preset)
        score_changes = self.project.has_score_changes
        if score_changes:
            indices = self.project.edit_track_indices()
            if self._regions:
                indices.update(self._indices(segments=True))
            indices = sorted(indices)
        else:
            indices = self._indices(segments=True)
        if not indices:
            raise ValueError('请选择至少一条可编辑的演唱轨。')
        options = self._draft_options()
        self.project.validate_source(options, indices)
        options['track_indices'] = indices
        options['style_regions'] = self.project.verify_regions(self._regions)
        if self._regions or score_changes:
            # Brush changes are just preparation for the next region.
            for key in _BRUSH_KEYS:
                options[key] = deepcopy(engine.DEFAULTS[key])
        options['reference_sources'] = [row for row in options['reference_sources'] if row['track_index'] in indices]
        options['retained_sources'] = [row for row in options['retained_sources'] if row['track_index'] in indices]
        return editor_options(options), None

    def _draft_options(self):
        options = deepcopy(self._options)
        if self.project.note_edits:
            options['note_edits'] = list(self.project.note_edits)
        if self.project.pitch_strokes:
            options['pitch_strokes'] = list(self.project.pitch_strokes)
        return options

    def _draft(self):
        options = self._draft_options()
        options['track_indices'] = self._indices() or None
        return {'schema_version': 2, 'input_sha256': self.project.sha256, 'regions': self._regions,
                'options': options, 'preset': self._preset, 'confirmed': self._confirmed}

    def _flush_draft(self):
        if self._draft_conflict:
            return False
        if not self._dirty or not self.project:
            return True
        self._autosave.stop()
        try:
            receipt = self._drafts.write(self.project.sha256, self._draft_revision, self._draft(), writer='vwa-quick')
        except (OSError, ValueError) as error:
            self._error(error)
            return False
        self._draft_revision, self._dirty = receipt['revision'], False
        return True

    def _dirty_draft(self, *, invalidate=True, keep_style=False):
        if self.project and not self._legacy:
            self._draft_review = None
            self._dirty = True
            self._autosave.start()
        if invalidate:
            self._plan, self._summary = None, ''
            if not keep_style:
                self._parameter_style = {}
            self._invalidate_lyric_batch()
            self._candidate_stale = bool(self.candidate)
            if self.state in ('frozen', 'done', 'partial', 'registered'):
                self.state, self.message = 'ready', tr('已修改调音设置，请重新预览。原候选文件保留。')
        self.changed.emit()

    def _error(self, error):
        if isinstance(error, DraftConflict):
            self._draft_conflict = error.details['recovery_path']
            self._draft_review = None
            self._preset_review = None
        if isinstance(error, (SourceChanged, DraftConflict)):
            self._plan, self._summary = None, ''
            self._candidate_stale = bool(self.candidate)
        self.state = 'cancelled' if isinstance(error, InterruptedError) else 'failed'
        self.message = tr(str(error)[:1400])
        self.changed.emit()

    def _command(self, kind, label, work, finish):
        if self.worker.kind:
            return False
        self.state, self.message = kind, tr(label)
        self.changed.emit()

        def progress(text):
            self.message = tr(str(text))
            self.changed.emit()

        result = ['succeeded', '']

        def complete(value, error):
            if error is not None:
                self._error(error)
                return
            try:
                finish(value)
            except (ValueError, OSError, TypeError, KeyError) as failure:
                result[:] = ['cancelled' if isinstance(failure, InterruptedError) else 'failed', failure]
                self._error(failure)
            self.changed.emit()

        return self.worker.start('tuning-' + kind, work, complete, progress, label=label, outcome=lambda _value: result)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    @Property('QVariantMap', notify=changed)
    def view(self):
        busy = bool(self.worker.kind)
        project = self.project.project if self.project else {}
        receipt = self.candidate or {}
        report = receipt.get('report', {})
        missing = ' '.join(self._preset.get('missing', [])) if self._preset else ''
        duration = self.project.maximum - self.project.minimum if self.project else 0.
        note_count = len(self.project.note_edits) if self.project else 0
        pitch_count = len(self.project.pitch_strokes) if self.project else 0
        usable = bool(self._regions or self._targets or note_count or pitch_count) and not self._legacy and not missing and not self._draft_conflict
        review = self._draft_review
        session_exists = bool(self.session and Path(native_path(self.session)).is_file())
        session_root = (Path(self.session).parent if session_exists else
                        ((Path(self.job) / 'tuning-sessions') if self.job else self._session_parent))
        match_scope = self._lyric_match_scope()
        match_position = (match_scope.index(self._lyric_match_index)
                          if self._lyric_match_index in match_scope else -1)
        active = self.project.tracks.get(self._active, {}) if self.project else {}
        preset_options = (self._preset.get('options') or {}) if self._preset else {}
        mode = preset_options.get('expression_mode', self._options['expression_mode'])
        source_indices = preset_options.get('track_indices') or self._indices(segments=True)
        source_reasons = {value: tr(self._source_mode_reason(value, source_indices)) for value in _SOURCES}
        reference_controls = self.project.reference_controls(source_indices) if self.project else ()
        usable = usable and not source_reasons.get(mode)
        format = project.get('format', 'svp')
        score_reason = self._score_block()
        draw_reason = self._pitch_block()
        region = self._region(self._selected_region)
        style_brush = self._brush(region['recipe']) if region else self._brush()
        return {
            'source': self._display_source or project.get('path', ''),
            'title': Path(self._display_source or project.get('path', '')).stem if project else '',
            'sha256': project.get('sha256', ''), 'job': self.job,
            'projectFormat': (format.upper() + ' · ' + str(project['format_version'])) if format in workflow.STYLE_ADAPTERS else
                             ('SV2 · ' + str(project['format_version'])) if project.get('synthesizer_v_2') else '',
            'projectSuffix': format,
            'formatNotice': tr(project['format_notice']) if project.get('format_notice') else '',
            'formatReadOnly': bool(project and format in workflow.STYLE_ADAPTERS and not self.project.editable),
            'trackReadOnlyReason': tr(active['reason']) if active.get('reason') else '',
            'expressionSourceReasons': source_reasons,
            'referenceControls': list(reference_controls),
            'session': str(self.session) if self.session else '',
            'sessionExists': session_exists,
            'sessionLocation': str(session_root),
            'sessionOutput': str(session_root / 'outputs'),
            'sessionFolder': QUrl.fromLocalFile(str(session_root)),
            'canChooseSessionLocation': bool(project and not self.job and not session_exists
                                              and not receipt and not busy),
            'canReload': bool(project and not busy),
            'mode': self._mode, 'activeTrack': self._active, 'selectedTracks': list(self._selected),
            'noteTool': self._score_tool == 'note' and not score_reason, 'scoreEditReason': tr(score_reason),
            'styleTool': self._mode == 'multi' or self._score_tool == 'style',
            'pitchTool': self._score_tool if self._score_tool in ('draw', 'erase') and not draw_reason else '',
            'pitchEditReason': tr(draw_reason), 'pitchStrokeCount': pitch_count,
            'pitchMaxCents': MAX_CENTS, 'pitchEdgeSeconds': EDGE_SECONDS,
            'pitchStrokeMinimum': MIN_STROKE_SECONDS,
            'noteEditCount': note_count,
            'selectedRegion': self._selected_region,
            'styleBrush': style_brush,
            'selectedNote': self._selected_note_view(),
            'selectedNoteIds': list(self._selected_notes),
            'lyricContext': self._lyric_context(),
            'lyricMatchIndex': match_position,
            'lyricMatchCount': len(match_scope),
            'canPreviousLyricMatch': bool(match_scope) and match_position != 0,
            'canNextLyricMatch': bool(match_scope) and match_position != len(match_scope) - 1,
            'query': self._query, 'start': self._start, 'end': self._end,
            'viewportStart': self._left, 'viewportSpan': self._span, 'duration': duration,
            'timelineStart': self.project.minimum if self.project else 0.,
            'style': self._options['performance_style'], 'styleStrength': self._options['style_strength'],
            'styleDetail': dict(self._options['style_detail']),
            'stylePitch': self._options['style_pitch'], 'busy': busy, 'state': self.state, 'message': self.message,
            'canUndoRegions': bool(self._region_undo), 'canRedoRegions': bool(self._region_redo),
            'canReloadDraft': bool(project and not busy and not self._legacy),
            'canSaveDraftRevision': bool(project and not busy and not self._legacy),
            'snapEnabled': self._view_preferences['snap'],
            'autoApplySelection': self._view_preferences['auto_apply_selection'],
            'parameterOpen': self._view_preferences['parameter_open'],
            'beatDivision': self._view_preferences['beat_division'],
            'canUseBeatGrid': bool(self.project and self.project.score_editor.clock),
            'canFreeze': bool(project and not busy and usable and (not self._origin or self._confirmed)),
            'canRun': bool(self._plan and not busy), 'canRegister': bool(self.job and receipt.get('receipt_verified')
                       and format != 'acep' and not self._candidate_stale and not busy and self.state != 'registered'),
            'candidate': receipt.get('output', ''), 'candidateStale': self._candidate_stale,
            'candidateVerified': bool(receipt.get('receipt_verified')),
            'candidateHistorical': bool(receipt.get('historical')),
            'candidateReceipt': receipt.get('receipt', ''),
            'candidateDetails': {
                'sha256': receipt.get('output_sha256', ''), 'bytes': receipt.get('output_bytes', 0),
                'tracks': len(report.get('track_indices', [])), 'status': receipt.get('status', ''),
                'mode': report.get('mode', ''), 'pitchChanged': report.get('pitch_changed') is True,
                'notesPreserved': report.get('notes_and_timing_unchanged') is True,
                'noteChanges': len(report.get('note_changes', [])),
                'pitchSpans': report.get('hand_pitch', {}).get('spans', 0),
            } if receipt else {},
            'savedCopy': self._saved_copy,
            'previewSummary': self._summary,
            'draftConflict': self._draft_conflict or '',
            'draftFolder': QUrl.fromLocalFile(str(self._drafts.root)),
            'draftReview': {
                'path': review['path'],
                'revision': (review['latest']['revision'] or '—')[:12],
                'shared': self._draft_description(review['latest']['data']),
                'local': self._draft_description(review['local']),
            } if review else {},
            'legacyDraft': ({
                'summary': tr('发现 {count} 个旧版全轨片段。转换后会另存逐轨草稿。')
                    .format(count=len(self._legacy['style_regions'])),
                'missingTracks': [] if self._legacy['track_indices'] else ['targets'],
            } if self._legacy else None),
            'candidateNeedsConfirmation': bool(self._origin), 'candidateConfirmed': self._confirmed,
            'presetName': self._preset['preset']['name'] if self._preset else '', 'presetPending': missing,
            'presetReview': self._preset_review['summary'] if self._preset_review else {},
            'expressionMode': self._options['expression_mode'], 'calibration': self._options['reference_calibration'] or '',
            **{key: self._options[key] for key in _SETTING_KEYS},
        }

    @Property('QVariantList', notify=tracksChanged)
    def tracks(self):
        return [{**row, 'reason': tr(row['reason']) if row['reason'] else ''}
                for row in self.project.tracks.values()] if self.project else []

    @Property('QVariantList', constant=True)
    def styles(self):
        return [{'id': key, 'label': tr(style_catalog.style_label(key)),
                 'short': tr('不叠加' if key == 'none' else style_catalog.style_region_label(key)),
                 'color': style_catalog.style_color(key)}
                for key in style_catalog.VISIBLE_STYLES]

    @Property('QVariantList', constant=True)
    def techniques(self):
        return [{'id': key, 'label': tr(style_catalog.technique_label(key)),
                 'short': tr(style_catalog.technique_region_label(key) or style_catalog.technique_label(key)),
                 'color': style_catalog.technique_color(key)}
                for key in style_catalog.TECHNIQUE_IDS]

    @Property('QVariantList', notify=changed)
    def bindings(self):
        if not self.project:
            return []
        reference = {row['track_index']: row for row in self._options['reference_sources']}
        retained = {row['track_index']: row for row in self._options['retained_sources']}
        return [{'trackId': key, 'trackName': self.project.tracks[key]['name'],
                 'reference': reference.get(index, {}).get('path', ''),
                 'referenceVoice': reference.get(index, {}).get('voice', ''),
                 'retained': retained.get(index, {}).get('project', ''),
                 'retainedIndex': retained.get(index, {}).get('source_track_index', 0)}
                for key in self.project.editable if (index := self.project.tracks[key]['index']) in self._indices(segments=True)]

    @Property('QVariantList', notify=changed)
    def tuningTasks(self):
        return list(self._tuning_tasks)

    @Property('QVariantList', notify=changed)
    def tuningTaskProjects(self):
        return list(self._tuning_task_projects)

    @Property(str, notify=changed)
    def tuningTaskMessage(self):
        return self._task_picker_message

    def _bridge_task_projects(self, detail):
        rows = []
        for row in (detail or {}).get('artifacts', []):
            if (row.get('role') in ('editor_project', 'tuned_project') and row.get('available')
                    and row.get('openable') and Path(row.get('path', '')).suffix.casefold() in workflow.TUNING_SUFFIXES):
                rows.append({
                    'artifactId': str(row.get('id') or ''),
                    'label': str(row.get('displayPath') or Path(row['path']).name),
                    'path': str(row.get('path') or ''), 'sha256': str(row.get('sha256') or ''),
                    'role': str(row.get('role') or ''),
                })
        return rows

    def _bridge_tasks_changed(self):
        bridge = self.parent()
        model = getattr(bridge, 'taskModel', None) if bridge is not None else None
        rows = list(getattr(model, 'rows', ()) or ())
        if model is not None:
            self._tuning_tasks = [
                {'jobId': str(row.get('jobId') or ''), 'title': str(row.get('title') or row.get('jobId') or ''),
                 'statusText': str(row.get('statusText') or '')}
                for row in rows if isinstance(row, dict) and row.get('jobId')
            ]
        if bridge is not None:
            selected = str(getattr(bridge, 'selectedTaskId', '') or '')
            detail = getattr(bridge, 'taskDetail', {}) or {}
            if selected and detail.get('jobId') == selected:
                self._selected_tuning_task = selected
                self._tuning_task_projects = self._bridge_task_projects(detail)
                self._task_picker_message = (tr('共 {count} 个已核验调音工程；载入会复用任务与结果页面的核验。')
                                             .format(count=len(self._tuning_task_projects))
                                             if self._tuning_task_projects else
                                             tr('此任务没有可载入的已核验调音工程。'))
        self.changed.emit()

    @Slot()
    def refreshTuningTasks(self):
        if self.worker.kind:
            return
        self._task_picker_message = tr('正在读取任务与结果…')
        bridge = self.parent()
        refresh = getattr(bridge, 'refreshTasks', None) if bridge is not None else None
        if callable(refresh):
            refresh()
            self._bridge_tasks_changed()
            return
        self.changed.emit()

        def work(stop, _send):
            import quick_results

            return quick_results.scan(stop.is_set)

        def finish(rows):
            self._tuning_tasks = [
                {'jobId': str(row.get('jobId') or ''), 'title': str(row.get('title') or row.get('jobId') or ''),
                 'statusText': str(row.get('statusText') or '')}
                for row in rows if isinstance(row, dict) and row.get('jobId')
            ]
            known = {row['jobId'] for row in self._tuning_tasks}
            if self._selected_tuning_task not in known:
                self._selected_tuning_task = self._tuning_tasks[0]['jobId'] if self._tuning_tasks else ''
            self._tuning_task_projects = []
            self._task_picker_message = (tr('共 {count} 个任务；请选择任务后再选择已核验的调音工程。')
                                         .format(count=len(self._tuning_tasks)) if self._tuning_tasks
                                         else tr('没有可读取的任务。'))
            if self._selected_tuning_task:
                QTimer.singleShot(0, lambda: self.selectTuningTask(self._selected_tuning_task))

        self._worker_command('task-picker-scan', '正在读取任务列表…', work, finish)

    @Slot(str)
    def selectTuningTask(self, job):
        job = str(job or '')
        if not job:
            return
        self._selected_tuning_task = job
        self._tuning_task_projects = []
        self._task_picker_message = tr('正在核验任务文件清单…')
        bridge = self.parent()
        select = getattr(bridge, 'selectTask', None) if bridge is not None else None
        if callable(select):
            select(job)
            self._bridge_tasks_changed()
            return
        if self.worker.kind:
            return
        self.changed.emit()

        def work(stop, _send):
            import quick_results

            return quick_results.detail(job, stop.is_set)

        def finish(detail):
            if job != self._selected_tuning_task or detail.get('jobId') != job:
                return
            rows = []
            for row in detail.get('artifacts', []):
                if (row.get('role') in ('editor_project', 'tuned_project') and row.get('available')
                        and row.get('openable') and Path(row.get('path', '')).suffix.casefold() in workflow.TUNING_SUFFIXES):
                    rows.append({
                        'artifactId': str(row.get('id') or ''), 'label': str(row.get('displayPath') or
                        Path(row['path']).name), 'path': str(row.get('path') or ''),
                        'sha256': str(row.get('sha256') or ''), 'role': str(row.get('role') or ''),
                    })
            self._tuning_task_projects = rows
            self._task_picker_message = (tr('共 {count} 个已核验调音工程；载入时会再次核对文件身份。')
                                         .format(count=len(rows)) if rows
                                         else tr('此任务没有可载入的已核验调音工程。'))

        self._worker_command('task-picker-detail', '正在核验任务中的调音工程…', work, finish)

    @Slot(str, str)
    def loadTuningTaskArtifact(self, job, artifact_id):
        job, artifact_id = str(job or ''), str(artifact_id or '')
        if not job or job != self._selected_tuning_task or not artifact_id:
            return
        bridge = self.parent()
        tune_artifact = getattr(bridge, 'tuneArtifact', None) if bridge is not None else None
        selected = str(getattr(bridge, 'selectedTaskId', '') or '') if bridge is not None else ''
        if callable(tune_artifact) and selected == job:
            self._task_picker_message = tr('正在复用任务与结果页面的文件核验…')
            self.changed.emit()
            tune_artifact(artifact_id)
            return
        if self.worker.kind:
            return

        def work(stop, _send):
            import quick_results

            detail = quick_results.detail(job, stop.is_set)
            row = next((item for item in detail.get('artifacts', ())
                        if item.get('id') == artifact_id and item.get('available') and item.get('openable')
                        and item.get('role') in ('editor_project', 'tuned_project')
                        and Path(item.get('path', '')).suffix.casefold() in workflow.TUNING_SUFFIXES), None)
            if detail.get('jobId') != job or row is None:
                raise ValueError('所选任务工程已变更、不可核验或格式不受支持。')
            return row

        def finish(row):
            self._task_picker_message = tr('已核验任务工程，正在读取…')
            QTimer.singleShot(0, lambda: self.accept_task(job, row))

        self._worker_command('task-picker-load', '正在重新核验所选任务工程…', work, finish)

    def _worker_command(self, kind, label, work, finish):
        if self.worker.kind:
            return False
        self.state, self.message = kind, tr(label)
        self.changed.emit()

        def complete(value, error):
            if isinstance(error, InterruptedError):
                return
            if error is not None:
                self._error(error)
                return
            try:
                finish(value)
            except (OSError, TypeError, ValueError, KeyError) as failure:
                self._error(failure)
            self.changed.emit()

        return self.worker.start('tuning-' + kind, work, complete)

    @Property('QVariantList', notify=regionsChanged)
    def regions(self):
        if not self.project:
            return []
        result = []
        for row in self._regions:
            recipe, key = row['recipe'], row['track_id']
            style, technique = recipe['preset'], recipe.get('technique', 'none')
            label = style_catalog.style_region_label(style) if style != 'none' else style_catalog.technique_region_label(technique)
            if style != 'none' and technique != 'none':
                label = tr(label) + ' · ' + tr(style_catalog.technique_region_label(technique))
            else:
                label = tr(label)
            color = style_catalog.style_color(style) if style != 'none' else style_catalog.technique_color(technique)
            result.append({'id': row['id'], 'trackId': key, 'trackName': self.project.tracks[key]['name'],
                           'start': recipe['start'], 'end': recipe['end'], 'style': style,
                           'label': label, 'color': color, 'strength': recipe['strength']})
        return result

    @Property('QVariantList', notify=matchesChanged)
    def matches(self):
        return self._matches

    @Property('QVariantList', notify=notesChanged)
    def notes(self):
        return self._notes

    @Property('QVariantList', notify=notesChanged)
    def pitchCurves(self):
        if not self.project or not self.project.pitch_strokes:
            return []
        visible = {row['id'] for row in self._notes if row['trackId'] == self._active}
        return self.project.pitch_layer.display(self.project.pitch_strokes, self.project.note_patches, visible)

    @Property('QVariantMap', notify=changed)
    def parameterPlot(self):
        if not self.project or self._mode != 'single' or not self._view_preferences['parameter_open']:
            return {}
        key = self.project, self._active, self._left, self._span, self._parameter_style
        if key != self._plot_cache[0]:
            focused = [note for note in self._notes if note['trackId'] == self._active]
            plot = self.project.pitch_layer.plot(focused, self._left, self._left + self._span, self._parameter_style,
                                                 self.project.note_patches)
            self._plot_cache = key, plot
        return self._plot_cache[1]

    @Property('QVariantList', notify=changed)
    def beatMarks(self):
        division = self._view_preferences['beat_division']
        clock = self.project.score_editor.clock if self.project else None
        if not division or clock is None:
            return []
        return clock.marks(clock.to_position(self._left), clock.to_position(self._left + self._span), division)

    @Slot(float, result=float)
    def snapBeat(self, seconds):
        division = self._view_preferences['beat_division']
        clock = self.project.score_editor.clock if self.project else None
        if not division or clock is None or not self._view_preferences['snap'] or not math.isfinite(seconds):
            return seconds
        position = clock.to_position(max(self.project.minimum, min(self.project.maximum, seconds)))
        return clock.to_seconds(clock.snap(position, division))

    @Property('QVariantList', notify=catalogChanged)
    def presets(self):
        return [{'key': key, 'label': row['name']} for key, row in self._presets.items()]

    def _set_catalog(self, snapshot):
        self._presets = {row['id'] + '@' + row['version']: row for row in snapshot['entries']
                         if row['type'] == 'tuning'}
        self.catalogChanged.emit()

    def refresh_presets(self):
        if self.worker.kind:
            return
        try:
            self._set_catalog(self._library.snapshot())
        except (ValueError, OSError) as error:
            self._error(error)

    def _update_notes(self):
        if self.project:
            keys = ([self._active] if self._mode == 'single' else
                    list(dict.fromkeys([self._active, *self.project.tracks])))
            self._notes = self.project.visible(keys, self._left, self._left + self._span)
        else:
            self._notes = []
        self.notesChanged.emit()

    def _selected_note_view(self):
        note = self.project.notes.get(self._selected_notes[-1]) if self._selected_notes else None
        if not note:
            return {}
        editor = self.project.score_editor
        target = editor.targets.get(note['id']) if editor else None
        minimum, maximum = note['start'], note['end']
        if target:
            minimum = editor.clock.to_seconds(target.offset + target.step * target.minimum)
            maximum = editor.clock.to_seconds(target.offset + target.step * target.maximum)
        details, pronunciation = (editor.note_details(note['id'], self.project.note_patches.get(note['id'], {}))
                                  if editor else ([], ''))
        return {**note, 'timingReason': tr(note.get('timingReason', '')), 'pitchReason': tr(note.get('pitchReason', '')),
                'timeMin': minimum, 'timeMax': maximum, 'details': details, 'pronunciation': pronunciation}

    def _lyric_context(self):
        """Project the current search hit or focused-track lyric from visible notes."""
        if not self.project or self._active not in self.project.tracks:
            return {}
        if 0 <= self._lyric_match_index < len(self._matches):
            row = self._matches[self._lyric_match_index]
            if row['trackId'] == self._active:
                return {key: row[key] for key in ('lyric', 'trackId', 'trackName', 'start', 'end')}
        if self._end <= self._start + 1e-9:
            return {}
        # A view pan may hide selected notes; keep reporting the actual selection.
        selected = self.project.visible((self._active,), self._start, self._end, limit=80)
        if not selected:
            return {}
        selected.sort(key=lambda row: (row['start'], row['end'], row['id']))
        return {
            'lyric': ''.join(row['lyric'] for row in selected),
            'trackId': self._active,
            'trackName': self.project.tracks[self._active]['name'],
            'start': selected[0]['start'],
            'end': max(row['end'] for row in selected),
        }

    def _lyric_match_scope(self):
        if self._mode == 'multi':
            return range(len(self._matches))
        focused = [index for index, row in enumerate(self._matches) if row['trackId'] == self._active]
        return focused or range(len(self._matches))

    def _adopt_draft(self, stored):
        payload = stored['data']
        self.project = _draft_projection(self.project, payload)
        self._portable_preferences_imported = False
        self._options = deepcopy(payload['options']) if payload else self._new_project_defaults()
        for key in EDITOR_OPTION_KEYS:
            self._options.pop(key, None)
        self._preset = deepcopy(payload['preset']) if payload else None
        self._confirmed = bool(payload and payload['confirmed'])
        self._regions = deepcopy(payload['regions']) if payload else []
        self._preset_review = self._draft_conflict = self._draft_review = None
        self._region_undo.clear()
        self._region_redo.clear()
        self._draft_revision, self._dirty = stored['revision'], False
        self._plan, self._summary = None, ''
        if self._query:
            self._matches = self.project.matches(self._query)
            self._lyric_match_index = -1
            self.matchesChanged.emit()

    def _select_saved_tracks(self):
        project = self.project
        indices = self._options['track_indices'] or []
        self._selected = [key for key in project.editable if project.tracks[key]['index'] in indices]
        if not self._selected:
            self._selected = list(project.editable[:1])
        self._active = next(iter(self._selected), next(iter(project.tracks)))
        self._mode = 'multi' if len(self._selected) > 1 else 'single'
        self._score_tool = 'note'
        self._selected_region, self._selected_notes = '', ()

    @staticmethod
    def _draft_description(payload):
        if payload is None:
            return tr('没有共享草稿')
        options = payload['options']
        return tr('{count} 个片段 · {style} · 强度 {strength}%{preset}').format(
            count=len(payload['regions']), style=tr(style_catalog.style_label(options['performance_style'])),
            strength=round(options['style_strength'] * 100),
            preset=' · ' + payload['preset']['preset']['name'] if payload['preset'] else '')

    @Slot(QUrl)
    def reviewDraftRecovery(self, url):
        if not self.project or self.worker.kind or not (url.isEmpty() or url.isLocalFile()):
            return
        if not self._flush_draft() and not self._draft_conflict:
            return
        path = self._draft_conflict or url.toLocalFile()
        if not path:
            return
        project = self.project
        self._draft_review = None

        def work(stop, _send):
            _verify_input(project, self.job, self.artifact, stop)
            local = self._drafts.read_recovery(project.sha256, path)
            _draft_projection(project, local)
            latest = self._drafts.read(project.sha256)
            if latest['data']:
                _draft_projection(project, latest['data'])
            return {'path': path, 'latest': latest, 'local': local}

        def finish(review):
            self._draft_review = review
            self.state, self.message = 'ready', tr('已读回草稿的两个版本，请选择要保留为当前草稿的内容。')

        self._command('draft-review', '正在读取共享草稿与本地副本…', work, finish)

    @Slot(bool)
    def resolveDraftRecovery(self, use_local):
        review = self._draft_review
        if not review or self.worker.kind or (self._dirty and not self._draft_conflict):
            return
        project = self.project

        def work(stop, _send):
            _verify_input(project, self.job, self.artifact, stop)
            latest = self._drafts.read(project.sha256)
            if latest['data']:
                _draft_projection(project, latest['data'])
            if latest['revision'] != review['latest']['revision']:
                return latest, False
            if use_local:
                latest = self._drafts.resolve_local(project.sha256, latest['revision'], review['local'],
                                                    writer='vwa-quick', preserve_current=True)
            return latest, True

        def finish(value):
            latest, applied = value
            self.state = 'ready'
            if not applied:
                self._draft_review = {**review, 'latest': latest}
                self.message = tr('共享草稿又有更新。请核对新版本后重新确认。')
                return
            self._adopt_draft(latest)
            self._legacy = self._legacy_revision = None
            self._select_saved_tracks()
            self._candidate_stale = bool(self.candidate)
            self.regionsChanged.emit()
            self._update_notes()
            self.message = tr('已恢复本地编辑。原共享版本也保留为草稿副本。' if use_local else
                              '已载入共享草稿。本地编辑仍保存在冲突副本中。')

        self._command('draft-resolve', '正在核对草稿版本…', work, finish)

    @Slot()
    def dismissDraftReview(self):
        if self._draft_review and not self.worker.kind:
            self._draft_review = None
            self.changed.emit()

    @Slot()
    def reloadLatestDraft(self):
        if not self.project or self.worker.kind:
            return
        local_recovery = ''
        if self._dirty and not self._flush_draft():
            if not self._draft_conflict:
                return
            local_recovery = self._draft_conflict
        project = self.project

        def work(stop, _send):
            _verify_input(project, self.job, self.artifact, stop)
            latest = self._drafts.read(project.sha256)
            if latest['data']:
                _draft_projection(project, latest['data'])
            return latest

        def finish(latest):
            if not latest['data']:
                raise ValueError('当前工程尚无共享草稿可载入。')
            self._adopt_draft(latest)
            self._legacy = self._legacy_revision = None
            self._select_saved_tracks()
            self._candidate_stale = bool(self.candidate)
            self.regionsChanged.emit()
            self._update_notes()
            self.state = 'ready'
            self.message = tr('已载入最新共享草稿。')
            if local_recovery:
                self.message += ' ' + tr('此前本地修改已保留在草稿副本中：') + local_recovery

        self._command('draft-reload', '正在读取最新共享草稿…', work, finish)

    @Slot()
    def saveDraftRevision(self):
        if not self.project or self.worker.kind or not self._flush_draft():
            return
        self._autosave.stop()
        project = self.project
        expected = self._draft_revision
        payload = self._draft()

        def work(stop, _send):
            _verify_input(project, self.job, self.artifact, stop)
            receipt = self._drafts.write(project.sha256, expected, payload,
                                         writer='vwa-quick', preserve_current=True)
            latest = self._drafts.read(project.sha256)
            if latest['data']:
                _draft_projection(project, latest['data'])
            return receipt

        def finish(receipt):
            self._draft_revision, self._dirty = receipt['revision'], False
            self._draft_conflict = self._draft_review = None
            self.state = 'ready'
            self.message = tr('已用当前调音内容保存新的共享草稿修订。')

        self._command('draft-save', '正在保存新的共享草稿修订…', work, finish)

    def _load(self, path, job='', artifact=None):
        if self.worker.kind or not self._flush_draft():
            return
        sessions = (Path(job) / 'tuning-sessions') if job else self._session_parent

        def finish(value):
            self._install_loaded(value, job=job, artifact=artifact, display_source=path, reload_session=None)

        self._command('loading', '正在读取工程、歌词和草稿…',
                      lambda stop, _send: _load_project(path, job, artifact, self._drafts,
                                                        self._legacy_drafts, sessions, stop), finish)

    def _install_loaded(self, value, *, job, artifact, display_source, reload_session):
        project, stored, old_draft, session, receipt, warning = value
        previous = old_draft['data']
        payload = stored['data']
        project = _draft_projection(project, payload)
        self.project, self.job, self.artifact = project, job, deepcopy(artifact)
        self.session, self.candidate = session, receipt
        self._display_source = str(display_source or project.project['path'])
        self._reload_session = Path(reload_session).resolve() if reload_session else None
        self._session_warning = warning
        self._saved_copy = ''
        self._adopt_draft(stored)
        self._legacy = previous if previous and previous.get('mode') == 'regions' and previous['style_regions'] else None
        self._legacy_revision = old_draft['revision'] if self._legacy else None
        if previous and not self._legacy:
            self._options.update(deepcopy(previous['tool']))
            self._options['track_indices'] = previous['track_indices'] or None
        self._select_saved_tracks()
        self._start = self._end = self._left = project.minimum
        self._span = min(18., max(1., project.maximum - project.minimum))
        self._query, self._matches = '', []
        self._lyric_match_index = -1
        self._plan, self._summary = None, ''
        self._parameter_style = {}
        self._plot_cache = (None, {})
        self._lyric_batch = None
        self._edit_issue = None
        self.state = 'ready'
        self._candidate_stale = bool(receipt)
        if receipt:
            try:
                options, preset = self._execution()
                old_preset = receipt.get('preset_binding')
                self._candidate_stale = (receipt.get('historical') is True
                                         or editor_options(receipt.get('options', {})) != options
                                         or old_preset != preset)
            except (ValueError, TypeError, KeyError):
                self._candidate_stale = True
        if warning and receipt and receipt.get('historical'):
            self.message = tr('历史候选已安全保留，但回执或来源未通过当前核验；可打开核对，不能登记。') + ' ' + tr(warning[:900])
        elif warning:
            self.message = tr('已恢复会话；部分编辑意图无法在当前工程中完整表达。') + ' ' + tr(warning[:900])
        elif receipt and self._candidate_stale:
            self.message = tr('已恢复会话；当前编辑意图与原候选不同，原候选文件仍保留。')
        elif self._portable_preferences_imported:
            self.message = tr('已沿用旧版可移植调音偏好；参考音频、校准、路径和轨道选择不会跨工程带入。')
        elif reload_session:
            self.message = tr('已恢复保存的处理记录和可表达的编辑设置，可继续生成工程。')
        elif project.project.get('format_notice'):
            self.message = tr(project.project['format_notice'])
        else:
            self.message = tr('点击歌词或拖出时间范围，再为选中的轨道添加风格。')
        self.tracksChanged.emit()
        self.matchesChanged.emit()
        self.regionsChanged.emit()
        self._update_notes()

    @property
    def _editing_blocked(self):
        return bool(self.worker.kind or self._draft_conflict)

    def _resume(self, session):
        if self.worker.kind or not self._flush_draft():
            return

        def finish(value):
            display_source = value[6]
            session_job = value[7]
            self._install_loaded(value[:6], job=session_job, artifact=None,
                                 display_source=display_source, reload_session=value[3])

        self._command('loading-session', '正在核验调音会话、来源、回执和编辑意图…',
                      lambda stop, _send: _resume_project(session, self._drafts, self._legacy_drafts, stop),
                      finish)

    @Slot(QUrl)
    def resumeSession(self, url):
        if self.worker.kind or not (url.isLocalFile() or url.isEmpty()):
            return
        if url.isEmpty():
            return
        path = Path(url.toLocalFile()).resolve()
        self._resume(path / 'session.json' if path.is_dir() else path)

    @Slot(QUrl)
    def setSessionLocation(self, url):
        if self.worker.kind or not url.isLocalFile():
            return
        try:
            path = Path(url.toLocalFile()).resolve(strict=True)
            if not path.is_dir():
                raise ValueError('调音会话保存位置必须是已有目录。')
            if self.job:
                raise ValueError('关联准备任务的调音会话固定在任务目录中。')
            if self.session and Path(native_path(self.session)).is_file():
                raise ValueError('当前调音会话已创建；请继续该会话，或从其他会话中明确恢复。')
            if self.candidate:
                raise ValueError('当前结果仍保留在原会话中；请明确恢复该会话后再处理。')
            self._session_parent = path
            if self.project:
                key = engine.digest([self.project.project['path'], self.project.sha256, None])[:24]
                self.session = path / key / 'session.json'
            self.state, self.message = 'ready', tr('已设置独立调音会话位置；首次生成时会在该目录下创建唯一子目录。')
            self.changed.emit()
        except (OSError, ValueError) as error:
            self._error(error)

    @Slot()
    def reloadProject(self):
        if not self.project or self.worker.kind or not self._flush_draft():
            return
        if self._reload_session:
            self._resume(self._reload_session)
        else:
            self._load(self.project.project['path'], self.job, self.artifact)

    @Slot(QUrl)
    def chooseProject(self, url):
        if url.isLocalFile():
            self._load(str(Path(url.toLocalFile()).resolve()))

    def accept_task(self, job, artifact):
        if artifact['available'] and Path(artifact['path']).suffix.casefold() in workflow.TUNING_SUFFIXES:
            self._load(artifact['path'], str(Path(job).resolve()), artifact)

    @Slot(str)
    def setMode(self, mode):
        if self.project and not self._editing_blocked and mode in ('single', 'multi') and mode != self._mode:
            old = self._indices()
            self._mode = mode
            self._selected_region, self._selected_notes = '', ()
            self._changed_tracks(old)

    def _changed_tracks(self, old):
        self._invalidate_lyric_batch()
        changed = old != self._indices() and not (self._regions or self.project.has_score_changes)
        if changed:
            self._rebind_preset()
        self._dirty_draft(invalidate=changed)
        self._update_notes()

    @Slot(str)
    def focusTrack(self, track):
        if self.project and track in self.project.tracks and not self._editing_blocked and self._active != track:
            old = self._indices()
            self._active = track
            self._selected_region, self._selected_notes = '', ()
            if not (0 <= self._lyric_match_index < len(self._matches)
                    and self._matches[self._lyric_match_index]['trackId'] == track):
                self._lyric_match_index = -1
            self._changed_tracks(old)

    @Slot(str)
    def toggleTrack(self, track):
        if self.project and track in self.project.editable and not self._editing_blocked:
            old = self._indices()
            if track in self._selected:
                self._selected.remove(track)
            else:
                self._selected.append(track)
            self._changed_tracks(old)

    @Slot(str)
    def setQuery(self, query):
        value = query[:256]
        if value != self._query:
            self._query = value
            self._matches = self.project.matches(value) if self.project else []
            self._lyric_match_index = -1
            self.matchesChanged.emit()
            self.changed.emit()

    @Slot(str, str)
    def selectMatch(self, track, note):
        if not self.project or self.worker.kind or track not in self.project.tracks:
            return
        match_index = next((index for index, item in enumerate(self._matches)
                            if item['trackId'] == track and item['id'] == note), -1)
        row = self._matches[match_index] if match_index >= 0 else None
        row = row or self.project.notes.get(note)
        if row and row['trackId'] == track:
            self._locate_match(row, match_index)

    @Slot(int)
    def stepLyricMatch(self, direction):
        """Move through the current lyric search cursor without another search."""
        if not self.project or self.worker.kind:
            return
        scope = self._lyric_match_scope()
        if not scope:
            return
        step = 1 if direction > 0 else -1
        if self._lyric_match_index in scope:
            position = scope.index(self._lyric_match_index) + step
            if not 0 <= position < len(scope):
                return
        elif step > 0:
            position = next((position for position, index in enumerate(scope)
                             if self._matches[index]['start'] >= self._start - 1e-9), 0)
        else:
            position = next((position for position in range(len(scope) - 1, -1, -1)
                             if self._matches[scope[position]]['start'] <= self._start + 1e-9),
                            len(scope) - 1)
        index = scope[position]
        self._locate_match(self._matches[index], index)

    def _locate_match(self, row, index):
        self.focusTrack(row['trackId'])
        if self._active != row['trackId']:
            return
        self._lyric_match_index = index
        self._set_selection(row['start'], row['end'], keep_match=True)
        self.setViewport(row['start'] - self._span * .22, self._span)
        self.noteLocated.emit(row['pitch'], row['trackId'], row['id'])

    def _set_selection(self, first, last, *, keep_match=False, notes=(), region=''):
        if self.project and math.isfinite(first) and math.isfinite(last):
            a, b = sorted((first, last))
            previous = self._batch_scope()
            self._selected_notes, self._selected_region = notes, region
            if not keep_match:
                self._lyric_match_index = -1
            self._start = min(max(a, self.project.minimum), self.project.maximum)
            self._end = min(max(b, self.project.minimum), self.project.maximum)
            if previous != self._batch_scope():
                self._invalidate_lyric_batch()
            self.changed.emit()

    @Slot(float, float)
    def selectSpan(self, first, last):
        self._set_selection(first, last)

    @Slot(str)
    def selectNote(self, identity):
        note = self.project.notes.get(identity) if self.project else None
        if note and not self.worker.kind:
            self.focusTrack(note['trackId'])
            if self._active == note['trackId']:
                self.selectNotes([identity])

    @staticmethod
    def _note_bounds(project, identities):
        rows = [project.notes[identity] for identity in identities]
        return min(row['start'] for row in rows), max(row['end'] for row in rows)

    @Slot('QVariantList')
    def selectNotes(self, identities):
        if not self.project or self.worker.kind:
            return
        unique = tuple(dict.fromkeys(identities))
        if not unique:
            self.clearSelection()
        elif all(identity in self.project.notes and self.project.notes[identity]['trackId'] == self._active
                 for identity in unique):
            self._set_selection(*self._note_bounds(self.project, unique), notes=unique)

    @Slot(str)
    def extendNoteSelection(self, identity):
        first = self._selected_notes[0] if self._selected_notes else identity
        if self.project and self.project.notes.get(identity, {}).get('trackId') == self._active:
            ordered = self.project.note_range(self._active, first, identity)
            self.selectNotes([first, *[note for note in ordered if note not in (first, identity)], identity])

    @Slot(str, float, int, result='QVariantMap')
    def adjacentNote(self, identity, seconds, direction):
        if not self.project or self.worker.kind:
            return {}
        return self.project.adjacent_note(self._active, identity, seconds, direction) or {}

    @Slot()
    def clearSelection(self):
        if self.project and not self.worker.kind:
            self.selectSpan(self.project.minimum, self.project.minimum)

    @Slot(float, float)
    def finishTimelineSelection(self, first, last):
        if not self.project or self.worker.kind or not (math.isfinite(first) and math.isfinite(last)):
            return
        self.selectSpan(first, last)
        if (self._view_preferences['auto_apply_selection'] and self._targets
                and self._end - self._start >= .025
                and (self._options['performance_style'] != style_catalog.DEFAULT_STYLE_ID
                     or self._options['style_technique'] != style_catalog.DEFAULT_TECHNIQUE_ID)):
            self.addRegion()

    @Slot(float, float)
    def setViewport(self, left, span):
        if self.project and math.isfinite(left) and math.isfinite(span):
            low, high = self.project.minimum, self.project.maximum
            span = min(max(.5, span), max(1., high - low))
            left = max(low, min(left, high - span))
            if (left, span) != (self._left, self._span):
                self._left, self._span = left, span
                self._update_notes()
                self.changed.emit()

    @Slot(bool)
    def setSnapEnabled(self, enabled):
        self._set_view_preference('snap', enabled)

    @Slot(bool)
    def setParameterOpen(self, enabled):
        self._set_view_preference('parameter_open', enabled)

    @Slot(int)
    def setBeatDivision(self, division):
        if division in (0, 4, 8, 16):
            self._set_view_preference('beat_division', division)

    @Slot(bool)
    def setAutoApplySelection(self, enabled):
        self._set_view_preference('auto_apply_selection', enabled)

    def _set_options(self, changes, *, detach=True):
        if self._editing_blocked:
            return
        try:
            if (self.project and self.project.has_score_changes
                    and changes.get('expression_mode', 'preserve') != 'preserve'):
                raise ValueError(PRESERVE_FOR_EDIT)
            options = draft_options({**self._options, **changes})
            if options == self._options:
                return
            self._options = options
            if detach:
                self._preset = None
            else:
                self._rebind_preset()
            staged = self._regions or (self.project and self.project.has_score_changes)
            self._dirty_draft(invalidate=not staged or bool(set(changes) - set(_BRUSH_KEYS)))
        except (OSError, ValueError) as error:
            self._error(error)

    @Slot(str)
    def setStyle(self, name):
        if name == self._options['performance_style']:
            return
        self._set_options({'performance_style': name, 'style_detail': None})

    @Slot(float)
    def setStyleStrength(self, value):
        self._set_options({'style_strength': value})

    @Slot(bool)
    def setStylePitch(self, value):
        self._set_options({'style_pitch': value})

    @Slot(str, float)
    def setStyleDetail(self, phase, value):
        if phase in DETAIL_DEFAULTS:
            self._set_options({'style_detail': {**self._options['style_detail'], phase: value}})

    @Slot(str, 'QVariant')
    def setTuningOption(self, key, value):
        if key in _SETTING_KEYS:
            self._set_options({key: value})

    @Slot(str)
    def setExpressionMode(self, mode):
        if reason := self._source_mode_reason(mode, self._indices(segments=True)):
            self._error(ValueError(reason))
            return
        if mode in _SOURCES and mode != self._options['expression_mode']:
            self._set_options({'expression_mode': mode, 'reference_sources': [], 'retained_sources': [],
                               'reference_calibration': None, 'retained_project': None,
                               **{key: mode == 'reference' and key == 'reference_dynamics' for key in _REFERENCE_FLAGS}})

    @Slot(str, QUrl)
    def bindReference(self, track, url):
        if (self.project and track in self.project.editable and (url.isLocalFile() or url.isEmpty())
                and self._options['expression_mode'] in ('reference', 'calibrated')):
            index = self.project.tracks[track]['index']
            voice = 'vocals' if len(self.project.editable) == 1 else 'lead' if track == self.project.editable[0] else 'backing'
            old = next((row for row in self._options['reference_sources'] if row['track_index'] == index), None)
            if old:
                voice = old['voice']
            rows = [row for row in self._options['reference_sources'] if row['track_index'] != index]
            if not url.isEmpty():
                rows.append({'track_index': index, 'path': url.toLocalFile(), 'voice': voice})
            self._set_options({'reference_sources': rows}, detach=False)

    @Slot(str, str)
    def setReferenceVoice(self, track, voice):
        if (self.project and track in self.project.editable and voice in ('lead', 'backing', 'vocals')
                and self._options['expression_mode'] in ('reference', 'calibrated')):
            index = self.project.tracks[track]['index']
            rows = [{**row, 'voice': voice} if row['track_index'] == index else row
                    for row in self._options['reference_sources']]
            self._set_options({'reference_sources': rows}, detach=False)

    @Slot(QUrl)
    def bindCalibration(self, url):
        if self._options['expression_mode'] == 'calibrated' and (url.isLocalFile() or url.isEmpty()):
            self._set_options({'reference_calibration': url.toLocalFile() or None}, detach=False)

    def calibration_seed(self):
        if not self.project or self._active not in self.project.editable:
            return None
        index = self.project.tracks[self._active]['index']
        source = next((row for row in self._options['reference_sources'] if row['track_index'] == index), {})
        voice = 'vocals' if len(self.project.editable) == 1 else 'lead' if self._active == self.project.editable[0] else 'backing'
        return {'path': self.project.project['path'], 'sha256': self.project.sha256, 'index': index, 'job': self.job,
                'reference': source.get('path', ''), 'voice': source.get('voice', voice)}

    def can_bind_calibration(self, spec):
        seed = self.calibration_seed()
        return bool(spec and seed and not self._editing_blocked and not self._legacy and not self._preset
                    and self._mode == 'single' and self._indices(segments=True) == [seed['index']]
                    and (seed['path'], seed['sha256'], seed['index']) ==
                        (spec['project'], spec['project_sha256'], spec['track_index']))

    def bind_calibration_result(self, session, spec, shown):
        if not self.can_bind_calibration(spec) or not self._flush_draft():
            return
        project, job, artifact = self.project, self.job, self.artifact
        name = Path(shown['output']).name

        def work(stop, _send):
            import calibration_session as calibration

            _verify_input(project, job, artifact, stop)
            current = calibration.read_session(session)
            keys = ('project', 'project_sha256', 'track_index', 'reference', 'reference_sha256', 'voice')
            if any(current[key] != spec[key] for key in keys):
                raise ValueError('校准会话已改变，请重新打开并核对。')
            result = calibration.verified_fit_result(session, output_name=name, cancel=lambda: _stop_check(stop))
            if (not result or not result['calibration_fit'].get('voicebank_calibrated')
                    or any(result[key] != shown[key] for key in ('sha256', 'profile_sha256'))):
                raise ValueError('校准结果或试音已改变，请重新核对。')
            return result

        def finish(result):
            index = spec['track_index']
            rows = [row for row in self._options['reference_sources'] if row['track_index'] != index]
            rows.append({'track_index': index, 'voice': spec['voice'], 'path': spec['reference']})
            self._set_options({'expression_mode': 'calibrated', 'reference_calibration': result['profile'],
                               'reference_sources': rows, 'dynamics_strength': result['calibration_fit']['strength'],
                               'retained_project': None, 'retained_sources': []})
            if self._flush_draft():
                self.state, self.message = 'ready', tr('已把校准和参考演唱绑定到当前调音轨。请重新预览。')

        self._command('checking-calibration', '正在核验校准与当前调音轨…', work, finish)

    @Slot(str, QUrl, int)
    def bindRetained(self, track, url, source_index):
        if (self.project and track in self.project.editable and (url.isLocalFile() or url.isEmpty())
                and source_index >= 0 and self._options['expression_mode'] == 'retained'):
            if not url.isEmpty() and Path(url.toLocalFile()).suffix.casefold() != '.' + self.project.project['format']:
                self._error(ValueError('沿用原参数需要选择与当前工程同格式的文件。'))
                return
            index = self.project.tracks[track]['index']
            rows = [row for row in self._options['retained_sources'] if row['track_index'] != index]
            if not url.isEmpty():
                rows.append({'track_index': index, 'project': url.toLocalFile(), 'source_track_index': source_index})
            self._set_options({'retained_sources': rows}, detach=False)

    @Slot(bool)
    def confirmCandidate(self, value):
        if not self._editing_blocked and self._confirmed != value:
            self._confirmed = value
            self._rebind_preset()
            self._dirty_draft()

    def _brush(self, recipe=None):
        if recipe is None:
            return {key: deepcopy(self._options[value]) for key, value in _REGION_BRUSH_FIELDS.items()}
        return {key: deepcopy(recipe.get(key, engine.DEFAULTS[option]))
                for key, option in _REGION_BRUSH_FIELDS.items()}

    def _region(self, identity):
        return next((row for row in self._regions if row['id'] == identity), None) if identity else None

    @Slot(str, str, 'QVariant')
    def setBrushValue(self, identity, field, value):
        if not self._can_edit_regions() or identity != self._selected_region:
            return
        current = self._region(identity)
        if identity and not current:
            return
        recipe = current['recipe'] if current else None
        brush = self._brush(recipe)
        if field in DETAIL_DEFAULTS:
            field, value = 'detail', {**brush['detail'], field: value}
        if field not in _REGION_BRUSH_FIELDS:
            return
        if brush[field] == value:
            return
        changes = {field: value}
        if field == 'preset':
            changes['detail'] = None
        if not current:
            self._set_options({_REGION_BRUSH_FIELDS[key]: item for key, item in changes.items()})
            return
        brush.update(changes)
        if (brush['preset'] == style_catalog.DEFAULT_STYLE_ID
                and brush['technique'] == style_catalog.DEFAULT_TECHNIQUE_ID):
            self.removeRegion(identity)
            return
        try:
            replacement = {**current, 'recipe': region_settings([{**recipe, **brush}])[0]}
            rows = [replacement if row['id'] == identity else row for row in self._regions]
            self._commit_regions(rows, '已保存片段参数，可撤销。')
        except (ValueError, OSError) as error:
            self._error(error)

    def _can_edit_regions(self):
        return bool(self.project and not self._editing_blocked and not self._legacy)

    def _editor_block(self):
        if not self.project:
            return '请先载入调音工程。'
        if self._mode != 'single':
            return '切到单轨后可以改谱或手绘。'
        if self._legacy or self._draft_conflict or (self._origin and not self._confirmed):
            return '请先处理草稿或确认候选基线。'
        if self._preset:
            return '请先解除当前工程的预设绑定。'
        if self._options['expression_mode'] != 'preserve':
            return PRESERVE_FOR_EDIT
        return ''

    def _score_block(self):
        if reason := self._editor_block():
            return reason
        if self.project.project.get('format') not in SCORE_EDIT_FORMATS:
            return SCORE_EDIT_UNSUPPORTED
        active = self.project.tracks.get(self._active, {})
        if active.get('index') not in self.project.score_editor.editable_tracks:
            return active.get('reason') or '这条轨道没有可编辑的原生音符。'
        return ''

    def _pitch_block(self):
        if reason := self._editor_block():
            return reason
        if self.project.project.get('format') not in DRAW_FORMATS:
            return DRAW_UNSUPPORTED
        active = self.project.tracks.get(self._active, {})
        if active.get('index') not in self.project.pitch_layer.tracks:
            return '这条轨道没有可手绘的音高。'
        return ''

    @Slot(bool)
    def setNoteTool(self, enabled):
        if not self._editing_blocked and (not enabled or not self._score_block()):
            self._set_score_tool('note' if enabled else 'style')

    @Slot(str)
    def setPitchTool(self, kind):
        if not self._editing_blocked and (kind == 'style' or (kind in ('draw', 'erase') and not self._pitch_block())):
            self._set_score_tool(kind)

    def _set_score_tool(self, kind):
        if kind != self._score_tool:
            self._score_tool = kind
            self.changed.emit()

    @Slot(str, str, 'QVariantList', result=bool)
    def editPitchStroke(self, identity, kind, samples):
        return self._apply_pitch(identity, lambda project, _note: project.pitch_layer.gesture(
            project.pitch_strokes, project.note_patches, identity, kind, samples),
            '已保存手绘音高，可撤销。' if kind == 'draw' else '已擦除手绘，原曲线和风格保留。')

    @Slot(str, str, 'QVariantList', result=bool)
    def brushPitch(self, track, kind, samples):
        if self._editing_blocked or not track or track != self._active:
            return False
        try:
            if reason := self._pitch_block():
                raise ValueError(reason)
            project = self.project
            visible = [note for note in self._notes if note['trackId'] == track]
            strokes = project.pitch_layer.brush(project.pitch_strokes, project.note_patches, visible, kind, samples)
            notice = '已保存音高曲线，可撤销。' if kind == 'draw' else '已擦除手绘，原曲线和风格保留。'
            self._commit_pitch(strokes, notice)
            return True
        except (ValueError, ArithmeticError) as error:
            self._error(error)
            return False

    def _apply_pitch(self, identity, operation, notice):
        if self._editing_blocked:
            return False
        try:
            if reason := self._pitch_block():
                raise ValueError(reason)
            note = self.project.notes.get(identity)
            if not note or note['trackId'] != self._active:
                raise ValueError('请在当前轨道选择要修改的音符。')
            self._commit_pitch(operation(self.project, note), notice)
            return True
        except (ValueError, ArithmeticError) as error:
            self._remember_edit_issue(identity, error)
            self._error(error)
            return False

    def _commit_pitch(self, strokes, notice):
        project = self.project.with_edits(self.project.note_edits, strokes)
        if project is not self.project:
            self._edit_issue = None
            self._record_edit()
            self._replace_edits(project, self._regions, notice)

    @Slot(str, float, float, float, float, result=bool)
    def drawPitchLine(self, identity, first, first_cents, last, last_cents):
        def apply(project, note):
            if not all(math.isfinite(t) and note['start'] <= t <= note['end'] for t in (first, last)):
                raise ValueError('手绘直线需要留在同一个音符内。')
            return project.pitch_layer.gesture(project.pitch_strokes, project.note_patches, identity,
                                                'draw', [[first, first_cents], [last, last_cents]])

        return self._apply_pitch(identity, apply, '已保存音高直线，可撤销。')

    @Slot(str, float, int, float, float, result=bool)
    def editPitchNode(self, identity, span_start, index, seconds, cents):
        def apply(project, note):
            if not math.isfinite(seconds) or not note['start'] <= seconds <= note['end']:
                raise ValueError('音高节点需要留在原音符内。')
            return project.pitch_layer.edit_node(project.pitch_strokes, project.note_patches, identity,
                                                  span_start, index, seconds, cents)

        return self._apply_pitch(identity, apply, '已保存音高节点，可撤销。')

    @Slot(str, float, int, result=bool)
    def removePitchNode(self, identity, span_start, index):
        return self._apply_pitch(identity, lambda project, _note: project.pitch_layer.remove_node(
            project.pitch_strokes, identity, span_start, index), '已删除音高节点，可撤销。')

    def _editable_note(self, identity):
        if self._editing_blocked:
            return None
        if reason := self._score_block():
            raise ValueError(reason)
        note = self.project.notes.get(identity)
        if not note or note['trackId'] != self._active:
            raise ValueError('请在当前轨道选择要修改的音符。')
        return note

    @Slot(str, str, float, result=bool)
    def editNoteDetail(self, identity, field, value):
        try:
            if not self._editable_note(identity) or self._selected_notes != (identity,):
                return False
            if field not in DETAILS:
                raise ValueError('所选音符没有此原生参数，或该参数受保护。')
            return self._commit_note(identity, {field: value}, '音符参数已更新，可撤销。')
        except ValueError as error:
            self._remember_edit_issue(identity, error)
            self._error(error)
            return False

    @Slot(str, str, result=bool)
    def editNoteLyric(self, identity, lyric):
        try:
            note = self._editable_note(identity)
            if not note:
                return False
            if note['lyricReason']:
                raise ValueError(note['lyricReason'])
            return self._commit_note(identity, {'lyric': lyric}, '歌词已更新，可撤销。')
        except ValueError as error:
            self._remember_edit_issue(identity, error)
            self._error(error)
            return False

    def _batch_scope(self):
        return self.project, self._active, self._start, self._end, self._selected_notes

    def _invalidate_lyric_batch(self):
        if self._lyric_batch is not None:
            self._lyric_batch['scope'] = None

    @Property('QVariantMap', notify=changed)
    def lyricBatch(self):
        review = self._lyric_batch
        if review is None:
            return {}
        rows, extra = review['rows'], review['extra']
        scoped = review['scope'] == self._batch_scope()
        reason = review['reason'] or self._score_block()
        if not scoped:
            reason = '轨道、选区或草稿已变化，请重新核对贴词。'
        blocked = sum(bool(row.get('blocked')) for row in rows)
        changes = sum(row['changed'] for row in rows)
        return {'rows': [{**row, 'reason': tr(row['reason'])} for row in rows],
                'extra': extra, 'reason': tr(reason), 'blocked': blocked, 'changes': changes,
                'track': review['track'], 'canApply': bool(changes and not (reason or extra or blocked)
                                                          and not self._editing_blocked)}

    @Slot(str, result='QVariantMap')
    def previewLyricBatch(self, text):
        scope = self._batch_scope()
        track = self.project.tracks.get(self._active, {}).get('name', '') if self.project else ''
        rows, extra, reason = [], [], self._score_block()
        if not reason and not self._editing_blocked:
            try:
                rows, extra = self.project.lyric_batch(self._active, self._start, self._end, text,
                                                       selected=self._selected_notes)
            except ValueError as error:
                reason = str(error)
        self._lyric_batch = {'scope': scope, 'track': track, 'rows': rows, 'extra': extra, 'reason': reason}
        self.changed.emit()
        return self.lyricBatch

    @Slot(str, str)
    def reviseLyricBatch(self, identity, text):
        review = self._lyric_batch
        if review and review['scope'] == self._batch_scope() and not self._editing_blocked:
            for index, row in enumerate(review['rows']):
                if row['id'] == identity and not row['preserved']:
                    note = self.project.notes[identity]
                    review['rows'][index] = {**batch_lyric_row(self.project, note, text[:300]),
                                             'overlap': row['overlap']}
                    self.changed.emit()
                    break

    @Slot(result=bool)
    def applyLyricBatch(self):
        if not self.lyricBatch.get('canApply'):
            return False
        try:
            edits = self.project.score_editor.update_many(self.project.note_edits, {
                row['id']: {'lyric': row['after'].strip()} for row in self._lyric_batch['rows'] if row['changed']})
            project = self.project.with_note_edits(edits)
        except ValueError as error:
            self._lyric_batch['reason'] = str(error)
            self.changed.emit()
            return False
        self._record_edit()
        self._lyric_batch = None
        self._edit_issue = None
        self._replace_edits(project, self._regions, '整句歌词已更新，可一次撤销。')
        return True

    @Slot()
    def dismissLyricBatch(self):
        if self._lyric_batch is not None:
            self._lyric_batch = None
            self.changed.emit()

    def _remember_edit_issue(self, identity, error):
        if self.project and identity in self.project.notes:
            self._edit_issue = self.project, identity, str(error)

    @Slot(result='QVariantMap')
    def editingIssues(self):
        if not self.project:
            return {'rows': [], 'truncated': False}
        rows, truncated = self.project.editing_issues(self._active)
        if self._edit_issue and self._edit_issue[0] is self.project:
            _, identity, reason = self._edit_issue
            note = self.project.notes.get(identity)
            if note and note['trackId'] == self._active:
                rows.insert(0, {**note, 'reason': reason})
        return {'rows': [{**row, 'reason': tr(row['reason'])} for row in rows], 'truncated': truncated}

    def _note_gesture(self, identity, action, seconds, pitch, horizontal):
        if self._score_tool != 'note':
            return None
        note = self._editable_note(identity)
        if not note:
            return None
        if (horizontal or action == 'resize') and note['timingReason']:
            raise ValueError(note['timingReason'])
        if action == 'move' and pitch != note['pitch'] and note['pitchReason']:
            raise ValueError(note['pitchReason'])
        selected = self._selected_notes if identity in self._selected_notes else (identity,)
        return self.project.score_editor.gesture_selection(self.project.note_patches, selected, identity,
                                                           action, seconds, pitch, horizontal)

    @Slot(str, str, float, int, bool, result='QVariantMap')
    def previewNoteGeometry(self, identity, action, seconds, pitch, horizontal):
        try:
            changes = self._note_gesture(identity, action, seconds, pitch, horizontal)
            if changes is not None:
                return {'notes': [self.project.score_editor.geometry(note, self.project.note_patches.get(note, {}), change)
                                  for note, change in changes.items()]}
        except (ValueError, ArithmeticError) as error:
            return {'reason': tr(str(error))}
        return {}

    @Slot(str, str, float, int, bool, result=bool)
    def editNoteGeometry(self, identity, action, seconds, pitch, horizontal):
        try:
            changes = self._note_gesture(identity, action, seconds, pitch, horizontal)
            return changes is not None and self._commit_notes(changes, '音符已更新，可撤销。')
        except (ValueError, ArithmeticError) as error:
            self._remember_edit_issue(identity, error)
            self._error(error)
            return False

    @Slot(str, int, result='QVariantMap')
    def nextEditableLyric(self, identity, direction):
        if not self._editing_blocked and not self._score_block():
            next_note = self.project.next_note(identity, direction)
            if next_note and next_note['trackId'] == self._active:
                return next_note
        return {}

    def _commit_note(self, identity, changes, notice):
        return self._commit_notes({identity: changes}, notice)

    def _commit_notes(self, changes, notice):
        previous = self.project
        edits = previous.score_editor.update_many(previous.note_edits, changes)
        if tuple(edits) == previous.note_edits:
            return True
        project = previous.with_note_edits(edits)
        timing = any((previous.notes[key]['start'], previous.notes[key]['end'])
                     != (project.notes[key]['start'], project.notes[key]['end']) for key in changes)
        rows = project.rebase_regions(previous, self._regions) if timing else self._regions
        self._record_edit()
        self._selected_region, self._selected_notes = '', tuple(changes)
        self._start, self._end = self._note_bounds(project, self._selected_notes)
        self._replace_edits(project, rows, notice)
        return True

    def _edit_state(self):
        return self.project.note_edits, self.project.pitch_strokes, self._regions

    def _record_edit(self):
        self._region_undo.append(self._edit_state())
        del self._region_undo[:-_REGION_UNDO_LIMIT]
        self._region_redo.clear()

    def _replace_edits(self, project, rows, notice):
        changed_score = self.project.note_edits != project.note_edits
        changed_pitch = self.project.pitch_strokes != project.pitch_strokes
        keep_style = changed_pitch and not changed_score and rows == self._regions
        self.project = project
        self._regions = rows
        if self._selected_region not in {row['id'] for row in rows}:
            self._selected_region = ''
        self._preset = None
        self._preset_review = None
        if changed_score:
            span = min(self._span, max(1., project.maximum - project.minimum))
            self._left, self._span = max(project.minimum, min(self._left, project.maximum - span)), span
            self._matches = self.project.matches(self._query)
            self._lyric_match_index = -1
            self._update_notes()
            self.matchesChanged.emit()
        elif changed_pitch:
            self.notesChanged.emit()
        self.state, self.message = 'ready', tr(notice)
        self.regionsChanged.emit()
        self._dirty_draft(keep_style=keep_style)

    def _commit_regions(self, rows, notice):
        if rows == self._regions:
            return False
        self.project.verify_regions(rows)
        self._record_edit()
        self._replace_edits(self.project, rows, notice)
        return True

    @Slot()
    def addRegion(self):
        if not self._can_edit_regions():
            return
        try:
            time_range_settings((self._start, self._end))
            rows = [self.project.region(track, self._start, self._end, self._brush()) for track in self._targets]
            applied = [row for row in rows if row is not None]
            if not applied:
                raise ValueError('选区内没有所选演唱轨的音符。')
            updated = self.project.overlay_regions(self._regions, applied)
            if self._commit_regions(updated, '已添加逐轨片段；同轨重叠部分以新风格为准。'):
                self._selected_region, self._selected_notes = applied[-1]['id'], ()
                self._start = min(float(row['recipe']['start']) for row in applied)
                self._end = max(float(row['recipe']['end']) for row in applied)
                self.message = tr('已添加 {count} 个逐轨片段；同轨重叠部分已按新风格整理。').format(count=len(applied))
                self.state = 'ready'
                self.changed.emit()
        except (ValueError, OSError) as error:
            self._error(error)

    @Slot(str)
    def updateRegion(self, identity):
        self._edit_region(identity, self._start, self._end, use_current_brush=True)

    @Slot(str, float, float)
    def moveRegion(self, identity, start, end):
        self._edit_region(identity, start, end, use_current_brush=False)

    def _edit_region(self, identity, start, end, *, use_current_brush):
        if not self._can_edit_regions():
            return
        try:
            current = self._region(identity)
            if current is None:
                raise ValueError('该片段已不存在。')
            track = current['track_id']
            time_range_settings((start, end))
            brush = self._brush() if use_current_brush else self._brush(current['recipe'])
            replacement = self.project.region(track, start, end, brush, identity=identity)
            if replacement is None:
                raise ValueError('当前选区内没有此片段轨道可应用的音符。')
            base = [row for row in self._regions if row['id'] != identity]
            updated = self.project.overlay_regions(base, [replacement])
            if self._commit_regions(updated, '已更新片段；同轨重叠部分以当前整组风格为准。'
                                    if use_current_brush else '已将片段移动到完整音符范围；重叠部分以此片段为准。'):
                self._set_selection(replacement['recipe']['start'], replacement['recipe']['end'], region=identity)
        except (ValueError, OSError) as error:
            self._error(error)

    @Slot(str)
    def removeRegion(self, identity):
        if self._can_edit_regions():
            if self._selected_region == identity:
                self._selected_region = ''
            self._commit_regions([row for row in self._regions if row['id'] != identity], '已移除片段。')

    @Slot()
    def removeSelectedRegion(self):
        if self._selected_region:
            self.removeRegion(self._selected_region)

    @Slot()
    def clearRegions(self):
        if self._can_edit_regions():
            self._commit_regions([], '已清空片段；可用撤销恢复。')

    @Slot()
    def undoRegions(self):
        self._step_edit(self._region_undo, self._region_redo, '已撤销编辑。')

    @Slot()
    def redoRegions(self):
        self._step_edit(self._region_redo, self._region_undo, '已重做编辑。')

    def _step_edit(self, stack, other, notice):
        if self._can_edit_regions() and stack:
            edits, strokes, rows = stack[-1]
            if (edits or strokes) and (self._preset or self._options['expression_mode'] != 'preserve'):
                self._error(ValueError('请解除预设绑定或改用“保留原表现”，再恢复改谱或手绘。'))
                return
            project = self.project.with_edits(edits, strokes)
            other.append(self._edit_state())
            stack.pop()
            if self.project.note_edits != project.note_edits and self._selected_notes:
                self._start, self._end = self._note_bounds(project, self._selected_notes)
            self._replace_edits(project, rows, notice)

    @Slot(str)
    def editRegion(self, identity):
        if not self._can_edit_regions():
            return
        row = self._region(identity)
        if row is None:
            return
        recipe = row['recipe']
        self.focusTrack(row['track_id'])
        self._set_selection(recipe['start'], recipe['end'], region=identity)
        self.setViewport(recipe['start'] - self._span * .15, self._span)

    @Slot()
    def migrateLegacy(self):
        if self._legacy and not self._editing_blocked:
            try:
                if self._legacy_drafts.read(self.project.sha256)['revision'] != self._legacy_revision:
                    raise ValueError('旧草稿已被更新，请重新载入工程查看最新版。')
                rows = self.project.expand_legacy(self._legacy, self._targets)
                self._options.update(deepcopy(self._legacy['tool']))
                self._regions, self._legacy, self._preset = rows, None, None
                self.regionsChanged.emit()
                self._dirty_draft()
                self.message = tr('已将旧片段按原轨道展开；旧草稿未改动。')
                self.changed.emit()
            except (ValueError, OSError) as error:
                self._error(error)

    @Slot()
    def freeze(self):
        if self.worker.kind or not self._flush_draft():
            return
        try:
            options, preset = self._execution()
            project, job, artifact = self.project, self.job, self.artifact
            plan = freeze_plan(project, self._regions, options, preset)
        except (ValueError, OSError) as error:
            self._error(error)
            return

        def work(stop, send):
            _verify_input(project, job, artifact, stop)
            return _preview(project, options, stop, send)

        def finish(value):
            summary, self._parameter_style = value
            self._plan, self._summary = plan, summary
            self.state, self.message = 'frozen', tr('计划已冻结，可生成独立候选。')

        self._command('freezing', '正在核对输入、轨道和参数差异…', work, finish)

    @Slot()
    def run(self):
        if not self._plan or self.worker.kind:
            return
        expected, raw = self._plan
        project, job, artifact, session = self.project, self.job, self.artifact, self.session
        resumed = self._reload_session is not None
        baseline = Path(project.project['path'])
        original_source = self._display_source if resumed else str(baseline)

        def work(stop, send):
            data = json.loads(raw)
            if engine.digest(data) != expected or data['input_sha256'] != project.sha256:
                raise ValueError('冻结计划已变化，请重新预览。')
            _verify_input(project, job, artifact, stop)
            if not resumed and not Path(native_path(session)).is_file():
                workflow.create_session(baseline, session.parent, job=job or None)
            spec = workflow.read_session(session)
            identity = spec['input_sha256'], spec['source_sha256'], spec['source'], spec['job']
            if (identity != (project.sha256, project.sha256, original_source, job or None)
                    or resumed and workflow._owned(session.parent, session.parent / spec['input_file']) != baseline):
                raise ValueError('会话输入不再对应冻结计划。')
            plan_path = workflow._owned(session.parent, session.parent / ('quick-plan-' + expected + '.json'))
            if not plan_path.is_file():
                atomic_json(plan_path, {'digest': expected, 'plan': data})
            else:
                stored = json.loads(plan_path.read_text(encoding='utf-8'))
                if stored.get('digest') != expected or engine.digest(stored.get('plan')) != expected:
                    raise ValueError('已有逐轨计划的校验失败，未修改原工程或候选。')
            receipt = workflow.run_session(session, data['options'], cancel=lambda: _stop_check(stop),
                                           progress=send, preset_binding=data['preset'])
            try:
                _verify_input(project, job, artifact, stop)
                return receipt, ''
            except (OSError, ValueError) as stale:
                # Preserve a completed file even if the upstream job changed during rendering.
                return receipt, str(stale)

        def finish(value):
            receipt, stale = value
            self.candidate, self._candidate_stale = receipt, bool(stale)
            self._saved_copy = ''
            if stale:
                self.state, self.message = 'partial', tr('已保留候选，但当前不可登记。') + ' ' + tr(stale)
            elif not receipt.get('receipt_verified'):
                self.state, self.message = 'partial', tr('工程已保存，回执待补全；可用同一计划重试。')
            else:
                partial = receipt['status'] == 'partial'
                self.state = 'partial' if partial else 'done'
                if partial:
                    self.message = tr('候选已生成，部分参数保留原样；请打开工程核对。')
                elif receipt['status'] == 'unchanged' and receipt['report'].get('mode') in ('reference', 'retained'):
                    self.message = tr('候选已生成，参数与原工程相同。')
                elif receipt['status'] == 'unchanged' and project.project['format'] in ('acep', 'ufdata'):
                    self.message = tr('本次没有可写入的音高变化，请检查所选音符和风格。')
                else:
                    self.message = tr('候选已生成。请在编辑器中核对并合成歌声。')

        self._command('running', '正在生成独立调音候选…', work, finish)

    @Slot()
    def cancel(self):
        if self.worker.kind in ('tuning-loading', 'tuning-freezing', 'tuning-running', 'tuning-checking-calibration',
                                'tuning-exporting'):
            self.worker.cancel()
            self.message = tr('正在取消…')
            self.changed.emit()

    def _verify_candidate(self, stop):
        _verify_input(self.project, self.job, self.artifact, stop)
        verified = workflow.verified_result(self.session)
        if not self.candidate or any(verified.get(key) != self.candidate.get(key)
                                     for key in ('signature', 'output', 'output_sha256')):
            raise ValueError('候选身份已变化，请重新生成或载入。')
        return verified

    def _verify_historical_candidate(self, stop):
        """Recheck a preserved output without promoting it to a verified result."""
        _verify_input(self.project, self.job, self.artifact, stop)
        if not self.candidate or self.candidate.get('historical') is not True:
            raise ValueError('当前不是可核对的保留候选。')
        spec = workflow.read_session(self.session)
        if spec['input_sha256'] != self.project.sha256:
            raise ValueError('保留候选的输入快照与当前调音会话不一致。')
        root = Path(self.session).resolve().parent
        output = workflow._owned(root, Path(self.candidate.get('output', '')))
        receipt = workflow._owned(root, Path(self.candidate.get('receipt', ''))) if self.candidate.get('receipt') else None
        if (not output.is_file() or file_hash(output) != self.candidate.get('output_sha256')
                or output.stat().st_size != self.candidate.get('output_bytes')):
            raise ValueError('保留的历史候选已改变或缺失；未打开文件。')
        if receipt is not None and not receipt.is_file():
            raise ValueError('保留候选的原回执已缺失；未把它当作核验结果。')
        return {'output': str(output), 'receipt': str(receipt) if receipt is not None else ''}

    @Slot()
    def registerResult(self):
        if not self.view['canRegister']:
            return

        def work(stop, _send):
            from task_store import known_job

            self._verify_candidate(stop)
            return workflow.register_result(self.session, job=known_job(self.job))

        def finish(_value):
            self.state, self.message = 'registered', tr('已登记到准备任务。合成后的歌声可在简易混音中导入。')

        self._command('registering', '正在验签并登记调音工程…', work, finish)

    @Slot()
    def openCandidate(self):
        if self.project and self.project.project['format'] == 'acep':
            if not self._saved_copy:
                from ace_tuning import ACE_HOME
                self._error(ValueError(ACE_HOME))
                return
        elif self.project and self.project.project['format'] == 'ufdata':
            self.message = tr('请在 ACE Studio 的原工程中导入另存的 UFData。')
            self.changed.emit()
            return
        self._open_result('output')

    @Slot(result=QUrl)
    def suggestedCandidatePath(self):
        if self.project is None or self.project.project['format'] != 'acep':
            return QUrl()
        original = Path(self._display_source or self.project.project['path'])
        return QUrl.fromLocalFile(str(original.with_name(original.stem + '-VWA.acep')))

    @Slot()
    def openResultReceipt(self):
        self._open_result('receipt')

    def _open_result(self, key):
        if (self.worker.kind or not self.candidate
                or not (self.candidate.get('receipt_verified') or self.candidate.get('historical'))):
            return

        saved = self._saved_copy if key == 'output' and self.project.project['format'] == 'acep' else ''

        def work(stop, _send):
            result = self._verify_historical_candidate(stop) if self.candidate.get('historical') else self._verify_candidate(stop)
            target = saved or result.get(key)
            if saved:
                if file_hash(Path(target)) != result.get('output_sha256'):
                    raise ValueError('另存的 ACEP 已改变，请打开编辑器手动核对。')
            return target

        def finish(target):
            if not target:
                raise ValueError('保留结果没有可打开的回执文件。')
            self.state = 'ready' if self._candidate_stale else 'done'
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
                raise ValueError('系统未能打开文件，请按路径手动打开。')
            historical = self.candidate.get('historical') is True
            if key == 'output':
                self.message = tr('已打开保留的历史候选，请人工核对；它仍未通过当前完整回执核验。'
                                  if historical else '已打开独立候选，请在编辑器中核对。')
            else:
                self.message = tr('已打开保留候选的原回执，供人工核对。'
                                  if historical else '已打开核验回执。')

        self._command('checking', '正在核验候选工程…', work, finish)

    @Slot(QUrl)
    def exportCandidate(self, url):
        if self.worker.kind or not self.candidate or not self.candidate.get('receipt_verified') or not url.isLocalFile():
            return

        def work(stop, _send):
            return workflow.export_verified_copy(self._verify_candidate(stop), url.toLocalFile(),
                                                 cancel=lambda: _stop_check(stop))

        def finish(path):
            self._saved_copy = path
            self.state, self.message = 'done', tr('已另存并核验独立工程：') + path

        self._command('exporting', '正在核验并另存工程…', work, finish)

    @Slot()
    def openMixdown(self):
        self.parent().navigate('audio_mixdown')

    def _bound(self, document, raw, *, before=None):
        indices = self._indices()
        bindings = {'vocal_tracks': indices, 'confirm_candidate_baseline': self._confirmed,
                    'reference_audio': [row for row in self._options['reference_sources'] if row['track_index'] in indices],
                    'retained_project': [row for row in self._options['retained_sources'] if row['track_index'] in indices]}
        if self._options['reference_calibration']:
            bindings['calibration_profile'] = self._options['reference_calibration']
        return plan_application(document, self.project.project, bindings, raw_document=raw, before=before)

    def _check_preset_source(self, document):
        payload = document['payload']
        mode = payload['expression_mode']
        if mode == 'preserve':
            return
        self.project.validate_source({'expression_mode': mode, **{
            'reference_' + name: True for name in payload.get('reference_controls', [])}}, self._indices(segments=True))
        if self._regions or self.project.has_score_changes:
            raise ValueError('此预设包含参数来源。请在没有逐轨片段的工程上绑定。')

    def _rebind_preset(self):
        if self._preset and self.project:
            self._preset = self._bound(self._preset['document'], self._preset['document_text'])

    def _preset_review_stamp(self):
        return (self.project.sha256, self._indices(), self._confirmed,
                deepcopy(self._options), self._edit_state())

    @Slot(str)
    def previewPreset(self, key):
        if not self.project or key not in self._presets or self.worker.kind:
            return
        row = self._presets[key]
        self._preset_review = None

        def finish(value):
            if value['entry']['sha256'] != row['sha256']:
                raise ValueError('预设版本已改变，请重新选择。')
            self._check_preset_source(value['document'])
            plan = self._bound(value['document'], value['raw'], before=self._options)
            self._preset_review = {
                'entry': row, 'stamp': self._preset_review_stamp(),
                'summary': {'name': plan['preset']['name'], 'changes': plan['changes'],
                            'missing': plan['missing'] if not (self._regions or self.project.has_score_changes) else [],
                            'brushOnly': bool(self._regions or self.project.has_score_changes)},
            }
            self.message = tr('预设差异已准备，确认后才会应用。')
            self.state = 'ready'

        self._command('preset', '正在读取预设版本…',
                      lambda _stop, _send: self._library.get_snapshot(row['id'], row['version']), finish)

    @Slot()
    def dismissPresetReview(self):
        if self._preset_review is not None:
            self._preset_review = None
            self.changed.emit()

    @Slot()
    def confirmPreset(self):
        review = self._preset_review
        if not self.project or not review or self._editing_blocked:
            return
        row = review['entry']
        self._preset_review = None

        def finish(value):
            if value['entry']['sha256'] != row['sha256'] or self._preset_review_stamp() != review['stamp']:
                raise ValueError('工程或预设已变化，请重新预览。')
            self._check_preset_source(value['document'])
            plan = self._bound(value['document'], value['raw'])
            if self._regions or self.project.has_score_changes:
                self._options.update({key: deepcopy(plan['controls'][key]) for key in _BRUSH_KEYS})
                self._dirty_draft(invalidate=False)
                self.message = tr('预设已成为下一片段的风格；现有片段保留。')
            else:
                self._preset = plan
                if plan['options'] is not None:
                    self._options = deepcopy(plan['options'])
                else:
                    self._options.update(deepcopy(plan['controls']))
                    self._options.update(reference_calibration=plan['bindings'].get('calibration_profile'),
                                         reference_sources=plan['bindings'].get('reference_audio', []),
                                         retained_sources=plan['bindings'].get('retained_project', []),
                                         retained_project=None)
                self._dirty_draft()
                self.message = tr('已绑定预设到当前工程。冻结前可查看或补齐所需的本地来源。')
            self.state = 'ready'

        self._command('preset', '正在核验并应用预设…',
                      lambda _stop, _send: self._library.get_snapshot(row['id'], row['version']), finish)

    @Slot()
    def unbindPreset(self):
        if self._preset and not self._editing_blocked:
            self._preset = None
            self._preset_review = None
            self._dirty_draft()
            self.message = tr('已解除工程预设关联；当前设置保留。')
            self.changed.emit()

    @Slot(str)
    def savePreset(self, name):
        if self.worker.kind:
            return
        try:
            document = make_document(self._options, name)
        except ValueError as error:
            self._error(error)
            return

        def work(_stop, _send):
            self._library.put_document(document, expected_revision=self._library.snapshot()['revision'])
            return self._library.snapshot()

        self._preset_task(work, '预设已添加到本机库。')

    @Slot(QUrl)
    def importPresets(self, url):
        if not url.isLocalFile():
            return

        def work(_stop, _send):
            self._library.import_file(url.toLocalFile(), expected_revision=self._library.snapshot()['revision'])
            return self._library.snapshot()

        self._preset_task(work, '预设已附加到本机库；现有预设未覆盖。')

    @Slot(QUrl, str)
    def exportPreset(self, url, key):
        if not url.isLocalFile() or key not in self._presets:
            return
        row = self._presets[key]

        def work(_stop, _send):
            self._library.export_file(row['id'], row['version'], url.toLocalFile())
            return self._library.snapshot()

        self._preset_task(work, '预设已导出。')

    def _preset_task(self, work, label):
        def finish(snapshot):
            self._set_catalog(snapshot)
            self.presetLibraryChanged.emit()
            self.state, self.message = 'ready', tr(label)

        self._command('preset', '正在处理本机预设库…', work, finish)

    def close(self):
        self._autosave.stop()
        self._flush_draft()
