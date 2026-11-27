"""Pure Python tuning session persistence, resume, preview, run & view-projection store (Qt-free)."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from common import atomic_json, file_hash, native_path
from i18n import tr
from project_convert_compat import strip_extended_prefix
from style_presets import recipe_has_pitch
import tuning_engine as engine
from tuning_execution import EDITOR_OPTION_KEYS
from tuning_pitch_layer import EDGE_SECONDS, MAX_CENTS, MIN_STROKE_SECONDS
from tuning_timeline import ProjectNotes, draft_options, normalize_timeline_draft
import tuning_workflow as workflow

_REFERENCE_FLAGS = ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing')
_SOURCES = ('preserve', 'reference', 'calibrated', 'retained')
_SETTING_KEYS = _REFERENCE_FLAGS + ('dynamics_strength', 'expression_density', 'style_technique',
                                    'technique_strength', 'technique_pitch')
_REGION_BRUSH_FIELDS = {
    'preset': 'performance_style', 'strength': 'style_strength', 'pitch': 'style_pitch',
    'detail': 'style_detail', 'technique': 'style_technique', 'technique_strength': 'technique_strength',
    'technique_pitch': 'technique_pitch', 'style_recipe': 'style_recipe',
}


class SourceChanged(ValueError):
    """The loaded source or its task revision is no longer current."""


def local_file_path(value) -> str:
    """Return a clean local path suitable for QUrl.fromLocalFile on Windows."""
    return strip_extended_prefix(str(value or ''))


def initial_viewport(project, active_track: str, default_span: float = 18.0) -> tuple[float, float]:
    """Start at project.minimum, or advance to the first vocal note on long-intro tracks."""
    span = min(default_span, max(1.0, project.maximum - project.minimum))
    left = project.minimum
    windows = getattr(project, '_windows', None) or {}
    notes = (windows.get(active_track) or ((), ()))[0]
    if not notes:
        for track_notes, _ in windows.values():
            if track_notes:
                notes = track_notes
                break
    if notes:
        first = float(notes[0]['start'])
        if first >= left + span * 0.85:
            upper = max(project.minimum, project.maximum - span)
            left = max(project.minimum, min(upper, first - 1.0))
    return left, span


def read_json_file(path, *, limit: int = 2 * 1024 * 1024):
    """Read a small workflow sidecar without trusting its size or schema."""
    path = Path(path)
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def session_candidate(session, reason: str = ''):
    """Return the reverified result, or preserve a safe historical output."""
    try:
        return workflow.verified_result(session), ''
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = str(error)
    root = Path(session).resolve().parent
    state = read_json_file(root / 'status.json')
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
    receipt = read_json_file(receipt_path)
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


def draft_projection(project, payload):
    options = payload['options'] if payload else {}
    result = project.with_edits(options.get('note_edits', []), options.get('pitch_strokes', []))
    if payload:
        result.verify_regions(payload['regions'])
    return result


def session_intent(session, project, *, default_options_fn: Callable[[], dict[str, Any]]):
    """Recover the last execution options and note identity where they are representable."""
    root = Path(session).resolve().parent
    warning = ''
    options = default_options_fn()
    raw_options = read_json_file(root / 'last-options.json')
    if raw_options is not None:
        try:
            options = draft_options(raw_options)
            project = draft_projection(project, {'options': options, 'regions': []})
        except (TypeError, ValueError) as error:
            warning = str(error)
            options = default_options_fn()
    recipes = options.get('style_regions', []) if isinstance(options, dict) else []
    by_index = {row['index']: key for key, row in project.tracks.items() if key in project.editable}
    regions = []
    for recipe in recipes if isinstance(recipes, list) else []:
        try:
            track = by_index[recipe['track_index']]
            brush = {key: deepcopy(recipe.get(key, engine.DEFAULTS[value]))
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


def stop_check(stop) -> None:
    if stop.is_set():
        raise InterruptedError('调音操作已取消。')


def verify_input(project, job: str, artifact, stop) -> None:
    stop_check(stop)
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


def load_project(path, job: str, artifact, drafts, legacy, sessions: Path, stop):
    stop_check(stop)
    project = ProjectNotes.load(path, expected_sha256=artifact.get('sha256') if artifact else None)
    verify_input(project, job, artifact, stop)
    restored = drafts.read(project.sha256)
    previous = legacy.read(project.sha256) if not restored['exists'] else {'data': None, 'revision': None}
    key = engine.digest([project.project['path'], project.sha256, job])[:24]
    root = sessions / key
    session = root / 'session.json'
    if Path(native_path(session)).is_file():
        data = workflow.read_session(session)
        if (data['input_sha256'], data['source'], data['job']) != (project.sha256, project.project['path'], job or None):
            raise ValueError('已有调音会话的源文件或任务不匹配。')
        result, warning = session_candidate(session)
    else:
        if Path(native_path(root)).exists():
            session = root.with_name(key + '-' + uuid4().hex[:8]) / 'session.json'
        result, warning = None, ''
    return project, restored, previous, session, result, warning


def resume_project(session, drafts, legacy, stop, *, default_options_fn: Callable[[], dict[str, Any]]):
    """Load any existing session from its immutable input snapshot."""
    path = Path(session).resolve()
    path = path / 'session.json' if path.is_dir() else path
    spec = workflow.read_session(path)
    source_warning = ''
    try:
        workflow.inspect_session_project(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        source_warning = str(error)
    snapshot = path.parent / spec['input_file']
    project = ProjectNotes.load(snapshot, expected_sha256=spec['input_sha256'])
    project.project['candidate_origin'] = deepcopy(spec.get('input_origin'))
    verify_input(project, '', None, stop)
    restored = drafts.read(project.sha256)
    previous = legacy.read(project.sha256) if not restored['exists'] else {'data': None, 'revision': None}
    if restored['exists']:
        intent, warning = restored, ''
    else:
        payload, warning = session_intent(path, project, default_options_fn=default_options_fn)
        intent = {'data': payload, 'revision': None, 'recoveries': []}
    result, result_warning = session_candidate(path)
    warning = ' '.join(value for value in (source_warning, warning, result_warning if result else '') if value)
    return project, intent, previous, path, result, warning, spec['source'], spec.get('job') or ''


def preview_project(project, options, stop, send, *, inspect_report=None):
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
    cancel = lambda: stop_check(stop)

    def capture(data):
        style.update(project.pitch_layer.curve_sources(data, cancel=cancel))

    _, report = workflow.apply_snapshot(snapshot, options, cancel=cancel, progress=send, observe_style=capture)
    if inspect_report is not None:
        inspect_report(report)
    indices = report['track_indices']
    curves = len(report['curve_changes'])
    if options.get('note_edits'):
        label = tr('预览：{tracks} 轨 · {notes} 个音符已修改 · {regions} 个风格片段 · {curves} 条曲线有变化。')
    else:
        label = tr('预览：{tracks} 轨 · {regions} 个逐轨片段 · {curves} 条曲线有变化。音符和时值保留。')
    label = label.format(tracks=len(indices), regions=len(options['style_regions']), curves=curves,
                         notes=len(report.get('note_changes', [])))
    if options.get('style_recipe') or any(region.get('style_recipe') for region in options['style_regions']):
        applied = report.get('style', {}).get('applied_controls', 0)
        skipped = report.get('style', {}).get('skipped_controls', 0)
        label += ' ' + tr('风格通道应用 {applied} 项，跳过 {skipped} 项。').format(applied=applied, skipped=skipped)
    if options.get('pitch_strokes'):
        label += ' ' + tr('手绘 {count} 段；覆盖风格音高偏移。').format(count=report['hand_pitch']['spans'])
    if format in workflow.STYLE_ADAPTERS and (not EDITOR_OPTION_KEYS.intersection(options) or options['style_regions']):
        adapter_note = {'vpr': ' VPR 只写 DYN 和 PIT；其他配方参数保留原样。',
                        'ustx': ' USTX 只写 DYN 和 PITD；其他配方参数保留原样。'}.get(format)
        label += tr(adapter_note) if adapter_note else ' ' + tr(workflow.style_adapter(format).STYLE_ONLY)
        if report.get('native_curve_skips'):
            label += tr(' 有原生曲线未能安全叠加，请核对回执。')
    elif report['status'] == 'partial':
        label += tr(' 有局部参数无法安全应用，生成后请核对结果。')
    return label, style


def execute_frozen_run(
    *,
    expected: str,
    raw: str,
    project,
    job: str,
    artifact,
    session: Path,
    resumed: bool,
    baseline: Path,
    original_source: str,
    stop,
    send,
):
    data = json.loads(raw)
    if engine.digest(data) != expected or data['input_sha256'] != project.sha256:
        raise ValueError('冻结计划已变化，请重新预览。')
    verify_input(project, job, artifact, stop)
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
    receipt = workflow.run_session(session, data['options'], cancel=lambda: stop_check(stop),
                                   progress=send, preset_binding=data['preset'])
    try:
        verify_input(project, job, artifact, stop)
        return receipt, ''
    except (OSError, ValueError) as stale:
        # Preserve a completed file even if the upstream job changed during rendering.
        return receipt, str(stale)


def classify_run_result(receipt: dict[str, Any], stale: str, project_format: str) -> tuple[str, str]:
    if stale:
        return 'partial', tr('已保留候选，但当前不可登记。') + ' ' + tr(stale)
    if not receipt.get('receipt_verified'):
        return 'partial', tr('工程已保存，回执待补全；可用同一计划重试。')
    partial = receipt['status'] == 'partial'
    state = 'partial' if partial else 'done'
    if partial:
        msg = tr('候选已生成，部分参数保留原样；请打开工程核对。')
    elif receipt['status'] == 'unchanged' and receipt['report'].get('mode') in ('reference', 'retained'):
        msg = tr('候选已生成，参数与原工程相同。')
    elif receipt['status'] == 'unchanged' and project_format in ('acep', 'ufdata'):
        msg = tr('本次没有可写入的音高变化，请检查所选音符和风格。')
    else:
        msg = tr('候选已生成。请在编辑器中核对并合成歌声。')
    return state, msg


def filter_task_projects(detail: dict[str, Any] | None) -> list[dict[str, str]]:
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


def format_task_rows(rows) -> list[dict[str, str]]:
    return [
        {'jobId': str(row.get('jobId') or ''), 'title': str(row.get('title') or row.get('jobId') or ''),
         'statusText': str(row.get('statusText') or '')}
        for row in rows if isinstance(row, dict) and row.get('jobId')
    ]


def build_view(
    *,
    busy: bool,
    project_obj,
    candidate,
    preset,
    regions,
    targets,
    legacy,
    draft_conflict,
    draft_review,
    session,
    job: str,
    session_parent: Path,
    lyric_match_scope: list,
    lyric_match_index: int,
    active_track: str,
    options: dict[str, Any],
    indices_fn: Callable,
    source_mode_reason_fn: Callable,
    score_block: str,
    pitch_block: str,
    selected_region: str,
    style_brush: dict[str, Any],
    display_source: str,
    mode: str,
    selected_tracks,
    score_tool: str,
    selected_note_view,
    selected_notes,
    lyric_context,
    query: str,
    start: float,
    end: float,
    left: float,
    span: float,
    state: str,
    message: str,
    region_undo,
    region_redo,
    view_preferences: dict[str, Any],
    origin,
    confirmed: bool,
    plan,
    candidate_stale: bool,
    saved_copy: str,
    summary: str,
    drafts_root: Path,
    draft_description_fn: Callable,
    preset_review,
    file_url_fn: Callable,
) -> dict[str, Any]:
    project = project_obj.project if project_obj else {}
    receipt = candidate or {}
    report = receipt.get('report', {})
    missing = ' '.join(preset.get('missing', [])) if preset else ''
    duration = project_obj.maximum - project_obj.minimum if project_obj else 0.
    note_count = len(project_obj.note_edits) if project_obj else 0
    pitch_count = len(project_obj.pitch_strokes) if project_obj else 0
    usable = bool(regions or targets or note_count or pitch_count) and not legacy and not missing and not draft_conflict
    session_exists = bool(session and Path(native_path(session)).is_file())
    session_root = (Path(session).parent if session_exists else
                    ((Path(job) / 'tuning-sessions') if job else session_parent))
    match_position = (lyric_match_scope.index(lyric_match_index)
                      if lyric_match_index in lyric_match_scope else -1)
    active = project_obj.tracks.get(active_track, {}) if project_obj else {}
    preset_options = (preset.get('options') or {}) if preset else {}
    expr_mode = preset_options.get('expression_mode', options['expression_mode'])
    source_indices = preset_options.get('track_indices') or indices_fn(segments=True)
    source_reasons = {value: tr(source_mode_reason_fn(value, source_indices)) for value in _SOURCES}
    reference_controls = project_obj.reference_controls(source_indices) if project_obj else ()
    usable = usable and not source_reasons.get(expr_mode)
    fmt = project.get('format', 'svp')
    brush = dict(style_brush)
    recipe = brush.pop('style_recipe')
    if recipe is not None:
        brush['recipeName'] = recipe['document']['metadata']['name']
        brush['recipeHasPitch'] = recipe_has_pitch(recipe)
    return {
        'source': display_source or project.get('path', ''),
        'title': Path(display_source or project.get('path', '')).stem if project else '',
        'sha256': project.get('sha256', ''), 'job': job,
        'projectFormat': (fmt.upper() + ' · ' + str(project['format_version'])) if fmt in workflow.STYLE_ADAPTERS else
                         ('SV2 · ' + str(project['format_version'])) if project.get('synthesizer_v_2') else
                         ('SVP · ' + str(project['format_version'])) if project and project.get('format_version') else
                         ('SVP' if project else ''),
        'projectSuffix': fmt,
        'formatNotice': tr(project['format_notice']) if project.get('format_notice') else '',
        'formatReadOnly': bool(project and fmt in workflow.STYLE_ADAPTERS and not project_obj.editable),
        'trackReadOnlyReason': tr(active['reason']) if active.get('reason') else '',
        'expressionSourceReasons': source_reasons,
        'referenceControls': list(reference_controls),
        'session': str(session) if session else '',
        'sessionExists': session_exists,
        'sessionLocation': str(session_root),
        'sessionOutput': str(session_root / 'outputs'),
        'sessionFolder': file_url_fn(str(session_root)),
        'canChooseSessionLocation': bool(project and not job and not session_exists
                                          and not receipt and not busy),
        'canReload': bool(project and not busy),
        'mode': mode, 'activeTrack': active_track, 'selectedTracks': list(selected_tracks),
        'noteTool': score_tool == 'note' and not score_block, 'scoreEditReason': tr(score_block),
        'styleTool': mode == 'multi' or score_tool == 'style',
        'pitchTool': score_tool if score_tool in ('draw', 'erase') and not pitch_block else '',
        'pitchEditReason': tr(pitch_block), 'pitchStrokeCount': pitch_count,
        'pitchMaxCents': MAX_CENTS, 'pitchEdgeSeconds': EDGE_SECONDS,
        'pitchStrokeMinimum': MIN_STROKE_SECONDS,
        'noteEditCount': note_count,
        'selectedRegion': selected_region,
        'styleBrush': brush,
        'selectedNote': selected_note_view,
        'selectedNoteIds': list(selected_notes),
        'lyricContext': lyric_context,
        'lyricMatchIndex': match_position,
        'lyricMatchCount': len(lyric_match_scope),
        'canPreviousLyricMatch': bool(lyric_match_scope) and match_position != 0,
        'canNextLyricMatch': bool(lyric_match_scope) and match_position != len(lyric_match_scope) - 1,
        'query': query, 'start': start, 'end': end,
        'viewportStart': left, 'viewportSpan': span, 'duration': duration,
        'timelineStart': project_obj.minimum if project_obj else 0.,
        'style': options['performance_style'], 'styleStrength': options['style_strength'],
        'hasStyleRecipe': options.get('style_recipe') is not None,
        'styleDetail': dict(options['style_detail']),
        'stylePitch': options['style_pitch'], 'busy': busy, 'state': state, 'message': message,
        'canUndoRegions': bool(region_undo), 'canRedoRegions': bool(region_redo),
        'canReloadDraft': bool(project and not busy and not legacy),
        'canSaveDraftRevision': bool(project and not busy and not legacy),
        'snapEnabled': view_preferences['snap'],
        'autoApplySelection': view_preferences['auto_apply_selection'],
        'parameterOpen': view_preferences['parameter_open'],
        'beatDivision': view_preferences['beat_division'],
        'canUseBeatGrid': bool(project_obj and project_obj.score_editor.clock),
        'canFreeze': bool(project and not busy and usable and (not origin or confirmed)),
        'canRun': bool(plan and not busy), 'canRegister': bool(job and receipt.get('receipt_verified')
                   and fmt != 'acep' and not candidate_stale and not busy and state != 'registered'),
        'candidate': receipt.get('output', ''), 'candidateStale': candidate_stale,
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
        'savedCopy': saved_copy,
        'previewSummary': summary,
        'draftConflict': draft_conflict or '',
        'draftFolder': file_url_fn(str(drafts_root)),
        'draftReview': {
            'path': draft_review['path'],
            'revision': (draft_review['latest']['revision'] or '—')[:12],
            'shared': draft_description_fn(draft_review['latest']['data']),
            'local': draft_description_fn(draft_review['local']),
        } if draft_review else {},
        'legacyDraft': ({
            'summary': tr('发现 {count} 个旧版全轨片段。转换后会另存逐轨草稿。')
                .format(count=len(legacy['style_regions'])),
            'missingTracks': [] if legacy['track_indices'] else ['targets'],
        } if legacy else None),
        'candidateNeedsConfirmation': bool(origin), 'candidateConfirmed': confirmed,
        'presetName': preset['preset']['name'] if preset else '', 'presetPending': missing,
        'presetReview': preset_review['summary'] if preset_review else {},
        'expressionMode': options['expression_mode'], 'calibration': options['reference_calibration'] or '',
        **{key: options[key] for key in _SETTING_KEYS},
    }
