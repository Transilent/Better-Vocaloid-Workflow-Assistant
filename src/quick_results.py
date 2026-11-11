"""Qt-independent task library and result handoff projections.

The module deliberately owns no QML objects.  A worker can scan and inspect
tasks off the UI thread, while the bridge keeps query and selection state.
Task directories are the stable public identifiers used by ``jobId``; every
path is resolved by :mod:`task_contract` before it reaches the page.
"""
from datetime import datetime
from pathlib import Path

from i18n import tr

import task_contract
import task_store
from quick_media_handoff import studio_targets


TASK_STATES = {
    'done': '已完成',
    'completed': '已完成',
    'partial': '阶段完成',
    'failed': '失败',
    'running': '处理中',
    'cancelled': '已停止',
    'interrupted': '已中断',
    'ready': '待处理',
}
ARTIFACT_STATES = {
    'verified': '校验通过',
    'unchecked': '尚未校验',
    'modified': '文件已修改',
    'missing': '文件缺失',
    'unreadable': '无法读取',
    'stale': '上游文件已变化',
    'missing_dependency': '上游文件缺失',
    'invalid_dependency': '文件关联无效',
}
ARTIFACT_ROLES = {
    'midi': 'MIDI',
    'editor_project': '编辑器工程',
    'tuned_project': '人工调音工程',
    'absolute_pitch': '原始音高线',
    'audio': '分轨音频',
    'source_video': '原视频',
    'cover': '视频截图',
    'original_cover': '本家封面',
    'subtitles': '字幕',
    'report': '处理记录',
    'source_metadata': '来源资料',
    'description_draft': '简介草稿',
    'mixdown': 'Mixdown',
    'video': '成品视频',
    'publication': '发布资料',
    'publication_asset': '发布附件',
    'publication_source': '发布资料来源',
    'project_audio': '工程音频附件',
    'calibration_profile': '声库校准配置',
    'calibration_audio': '校准试音',
    'retained_expression': '保留的演唱参数',
    'calibration_session': '校准导出任务',
    'calibration_input': '校准输入',
    'calibrated_project': '校准后的工程',
}
MODE_LABELS = {
    'dual': 'Karaoke 2 双轨',
    'roformer': 'BS-RoFormer 双轨',
    'import': '外部分轨',
    'single': '人声单轨',
}
DEVICE_LABELS = {'cpu': 'CPU', 'dml': 'GPU / DirectML'}
PRODUCER_LABELS = {
    'metadata': '来源资料',
    'download': '下载 / 导入',
    'audio': '音频解码',
    'separation': '分离声部',
    'midi': '提取 MIDI',
    'subtitles': '导出字幕',
    'exports': '导出文件',
    'human': '人工登记',
    'video': '视频合成',
    'video_source': '手选原视频',
    'publication': '发布准备',
    'calibration_result': '声库校准',
    'project_reference': '工程附件',
}
BINDING_KINDS = ('project', 'mixdown', 'video')
BINDING_LABELS = {
    'project': 'SVP 工程',
    'mixdown': 'Mixdown',
    'video': '成品视频',
}
_RESUMABLE_ACTIONS = frozenset(('retry', 'continue', 'ready'))
OPENABLE_SUFFIXES = frozenset(('.wav', '.flac', '.ogg', '.mp3', '.m4a', '.mp4', '.mkv', '.webm',
                               '.mid', '.midi', '.svp', '.ustx', '.vsqx', '.srt', '.vtt', '.ass', '.lrc',
                               '.txt', '.log', '.csv', '.json', '.png', '.jpg', '.jpeg', '.webp'))


def _cancelled(check):
    if check is not None and check():
        raise InterruptedError('task scan cancelled')


def _job_id(path):
    """Return the stable task-directory identity used by model and commands."""
    return str(Path(path).resolve())


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _time_text(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OSError, OverflowError):
        return tr('时间未记录')


def _status_text(state):
    state = _text(state) or 'ready'
    return tr(TASK_STATES.get(state, state))


def _next_text(action):
    action = _text(action) or 'needs_review'
    return tr(task_contract.NEXT.get(action, action))


def _search_text(row, path, status_text, next_text):
    request = row.get('request') if isinstance(row.get('request'), dict) else {}
    values = [
        row.get('title'), path.name, str(path), row.get('state'), status_text, next_text,
        request.get('source_title'), request.get('bv'), request.get('source_audio'),
        request.get('source_kind'), request.get('voice_mode'),
    ]
    return ' '.join(str(value) for value in values if value not in (None, ''))


def _list_row(row):
    path = Path(row['path']).resolve()
    title = _text(row.get('title')) or path.name or tr('未命名任务')
    state = _text(row.get('state')) or _text((row.get('status') or {}).get('state')) or 'ready'
    next_action = _text(row.get('next_action')) or 'needs_review'
    status_text = _status_text(state)
    next_text = _next_text(next_action)
    updated = _time_text(row.get('modified'))
    return {
        'jobId': _job_id(path),
        'title': title,
        'statusText': status_text,
        'summary': updated + ' · ' + next_text,
        'state': state,
        'modified': float(row.get('modified') or 0),
        'searchText': _search_text(row, path, status_text, next_text),
    }


def scan(check=None):
    """Return the current task-library projection as ``list[dict]``.

    ``check`` is the shared worker's zero-argument cancellation callback.  A
    cancelled scan raises ``InterruptedError`` and never exposes a partial list.
    """
    rows = task_store.records(check=check)
    _cancelled(check)
    return [_list_row(row) for row in rows]


def _artifact(job, row):
    state = _text(row.get('state')) or 'unknown'
    path = _text(row.get('path'))
    role = _text(row.get('role')) or 'artifact'
    audio_label = {'download': '导入音频', 'audio': '原曲音频', 'exports': '导出音频'}.get(row.get('producer'))
    label = tr((audio_label if role == 'audio' else None) or ARTIFACT_ROLES.get(role, role))
    filename = Path(path).name if path else ''
    if filename:
        label += ' · ' + filename
    status_text = tr(ARTIFACT_STATES.get(state, state))
    return {
        'id': _text(row.get('id')),
        'label': label,
        'statusText': status_text,
        'available': state == 'verified' and bool(path),
        'openable': state == 'verified' and Path(path).suffix.casefold() in OPENABLE_SUFFIXES,
        'path': path,
        'displayPath': str(Path(path).relative_to(job)) if path and Path(path).is_relative_to(job) else path,
        'role': role,
        'state': state,
        'sha256': _text(row.get('sha256')),
        'revision': _text(row.get('revision')),
    }


def _artifact_details(row, by_id):
    """Project verified provenance without inferring dependencies."""
    dependencies = []
    for dependency in row.get('dependencies') or ():
        if not isinstance(dependency, dict):
            continue
        key = _text(dependency.get('id'))
        upstream = by_id.get(key)
        if upstream is None:
            dependencies.append({
                'label': tr('上游文件未登记'),
                'displayPath': '',
                'statusText': tr('上游文件缺失'),
                'revision': _text(dependency.get('revision')),
            })
            continue
        path = _text(upstream.get('path'))
        role = _text(upstream.get('role'))
        state = _text(upstream.get('state'))
        dependencies.append({
            'label': tr(ARTIFACT_ROLES.get(role, role or '产物')),
            'displayPath': Path(path).name if path else '',
            'statusText': tr(ARTIFACT_STATES.get(state, state or '未知')),
            'revision': _text(upstream.get('revision')),
        })
    sha256 = _text(row.get('sha256'))
    producer = _text(row.get('producer'))
    return {
        'producerText': tr(PRODUCER_LABELS.get(producer, producer or '未知步骤')),
        'sha256': sha256,
        'sha256Short': sha256[:12] + ('…' if len(sha256) > 12 else ''),
        'dependencies': dependencies,
    }


def _binding_projection(job, rows):
    """Project active manual selections without treating them as inferred outputs."""
    metadata = task_store.read_json(Path(job) / 'task.json')
    bindings = metadata.get('bindings') if isinstance(metadata.get('bindings'), dict) else {}
    by_id = {row['id']: row for row in rows}
    projected = {}
    for kind in BINDING_KINDS:
        binding = bindings.get(kind)
        if not isinstance(binding, dict):
            continue
        key = _text(binding.get('id'))
        row = by_id.get(key)
        if row is None:
            continue
        artifact = _artifact(job, row)
        projected[kind] = {
            'kind': kind,
            'label': tr(BINDING_LABELS[kind]),
            'id': key,
            'path': artifact['path'],
            'fileName': Path(artifact['path']).name,
            'displayPath': artifact['displayPath'],
            'state': artifact['state'],
            'statusText': artifact['statusText'],
            'available': artifact['available'],
            'openable': artifact['openable'],
            'sha256': artifact['sha256'],
            'revision': artifact['revision'],
        }
    return projected


def _binding_identity(job, kind):
    metadata = task_store.read_json(Path(job) / 'task.json')
    bindings = metadata.get('bindings') if isinstance(metadata.get('bindings'), dict) else {}
    binding = bindings.get(kind) if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        return '', ''
    entry = binding.get('entry') if isinstance(binding.get('entry'), dict) else {}
    return _text(binding.get('id')), _text(entry.get('revision'))


def _require_binding_identity(job, kind, expected_id, expected_revision):
    actual = _binding_identity(job, kind)
    expected = (_text(expected_id), _text(expected_revision))
    if actual != expected:
        raise task_contract.ContractError('任务的人工登记已变化，请重新核对后再试。')
    return actual


def _artifact_index_revision(job):
    """Return a cheap snapshot token for an explicit rebuild confirmation."""
    path = Path(job) / 'artifacts.json'
    try:
        stat = path.stat()
    except FileNotFoundError:
        return ''
    return f'{stat.st_size}:{stat.st_mtime_ns}'


def _request_summary(row):
    request = row.get('request') if isinstance(row.get('request'), dict) else {}
    source_title = _text(request.get('source_title'))
    bv = _text(request.get('bv'))
    source_audio = _text(request.get('source_audio'))
    source = source_title or (Path(source_audio).name if source_audio else '')
    if bv:
        source = (source + ' · ' + bv) if source else bv
    mode = _text(request.get('voice_mode')) or 'single'
    language = '日语' if _text(request.get('language')).lower() == 'ja' else '中文'
    steps = request.get('midi_steps', 16)
    if type(steps) is not int or steps <= 0:
        steps = 16
    tools = request.get('tools') if isinstance(request.get('tools'), dict) else {}
    separation_device = _text(tools.get('separation_device')) or 'dml'
    midi_device = _text(tools.get('midi_device')) or 'dml'
    created = _text(request.get('created_at')).replace('T', ' ')[:19]
    values = [
        ('来源', source),
        ('处理方案', tr(MODE_LABELS.get(mode, mode)) + ' · ' + str(steps) + tr(' 步')),
        ('语言', tr(language)),
        ('设备', tr('分离 ') + DEVICE_LABELS.get(separation_device, separation_device)
         + tr(' / MIDI ') + DEVICE_LABELS.get(midi_device, midi_device)),
        ('创建', created),
        ('更新', _time_text(row.get('modified'))),
    ]
    rows = [{'label': tr(label), 'value': value} for label, value in values if value]
    preview = ' · '.join(value for _, value in values[:3] if value)
    return {'requestSummary': rows, 'requestPreview': preview}


def _task_log_path(job):
    root = Path(job).resolve()
    candidate = (root / 'pipeline.log').resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _delivery(job, action, rows):
    """Resolve the actual artifacts for the advertised action from one inspection."""
    verified = [row for row in rows if row.get('state') == 'verified']
    by_path = {Path(row['path']): row for row in verified}
    binding = {'mixdown_ready': 'mixdown', 'video_ready': 'video'}.get(action)
    if binding:
        path = task_contract.selected(job, binding, rows=rows)
        return ([by_path[path]], path) if path in by_path else ([], None)
    if action in ('awaiting_upload', 'awaiting_publish'):
        return [row for row in verified if row['role'] == 'publication'], None
    if action == 'awaiting_tuning':
        project = task_contract.selected(job, 'project', rows=rows)
        if project in by_path:
            return [by_path[project]], project
        exports = [row for row in verified if row['producer'] == 'exports' and row['role'] == 'editor_project']
        svps = [row for row in exports if Path(row['path']).suffix.casefold() == '.svp']
        baseline = next((row for row in svps if Path(row['path']) == job / 'exports/voices.svp'), None)
        midi = [row for row in verified if row['role'] == 'midi']
        selected = baseline or (svps[0] if len(svps) == 1 else exports[0] if len(exports) == 1 else None)
        return exports + midi, Path(selected['path']) if selected else None
    if action == 'audio_ready':
        from pipeline import requirements

        request = task_store.read_json(job / 'request.json')
        stems = [job / name for name in requirements('separation', request) if name.startswith('audio/')]
        if all(path in by_path and by_path[path]['role'] == 'audio' for path in stems):
            return [by_path[path] for path in stems], None
    return [], None


def _recovery(row, message=''):
    status = row.get('status') if isinstance(row.get('status'), dict) else {}
    state = _text(row.get('state')) or _text(status.get('state'))
    if not message:
        for key in ('error', 'failure', 'message', 'detail'):
            value = status.get(key)
            if isinstance(value, str) and value.strip():
                message = value.strip()
                break
    if message:
        message = message[:1600]
    if state in ('failed', 'cancelled', 'interrupted') or message:
        return {'state': state or 'unknown', 'message': message}
    return None


def detail(job, check=None):
    """Inspect one registered task and return its result/action projection.

    Artifact integrity and handoff are derived from the same inspected rows so
    the page never advertises a file or next step that the contract rejected.
    Unreadable or version-unknown task contracts keep their recorded task state,
    expose no openable artifacts, and disable task actions.
    """
    _cancelled(check)
    row = task_store.record(job)
    path = row['path']
    state = _text(row.get('state')) or _text((row.get('status') or {}).get('state')) or 'ready'
    title = _text(row.get('title')) or path.name or tr('未命名任务')
    status_text = _status_text(state)
    action = 'needs_review'
    artifacts, artifact_details, delivery, mix, open_path, targets = [], {}, [], [], None, {}
    binding_rows = {}
    error_message = ''
    try:
        rows = task_contract.inspect(path, check=check)
        _cancelled(check)
        action = task_contract.handoff(path, rows=rows)
        delivery, open_path = _delivery(path, action, rows)
        if action != 'processing':
            targets = studio_targets(path, rows)
        if state in ('done', 'partial') and action not in ('needs_review', 'processing'):
            mix, _ = _delivery(path, 'audio_ready', rows)
        deliver_ids = {item['id'] for item in delivery}
        ordered = sorted(rows, key=lambda row: (
            row['id'] not in deliver_ids,
            row['state'] == 'verified',
            row['role'] not in ('editor_project', 'midi', 'mixdown', 'video', 'audio'),
        ))
        artifacts = [_artifact(path, item) for item in ordered]
        by_id = {item['id']: item for item in rows}
        artifact_details = {item['id']: _artifact_details(item, by_id) for item in ordered}
        binding_rows = _binding_projection(path, rows)
        if action == 'audio_ready' and not delivery:
            action = 'needs_review'
    except InterruptedError:
        raise
    except (task_contract.ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        error_message = str(exc) or exc.__class__.__name__
        action = 'needs_review'

    locked = task_store.locked(path)
    known_state = state in TASK_STATES
    manageable = not locked and known_state and not error_message
    metadata = task_store.read_json(path / 'task.json')
    bindings = metadata.get('bindings') if isinstance(metadata.get('bindings'), dict) else {}
    has_video_binding = bool(bindings.get('video'))
    has_project_binding = bool(bindings.get('project'))
    has_mixdown_binding = bool(bindings.get('mixdown'))
    has_publication = bool(metadata.get('publication'))
    log_path = _task_log_path(path)
    request = _request_summary(row)
    result = {
        'jobId': _job_id(path),
        'title': title,
        'state': state,
        'statusText': status_text,
        'nextAction': _next_text(action),
        'canResume': not locked and not error_message and action in _RESUMABLE_ACTIONS,
        'canDispatch': not locked and not error_message and bool(delivery),
        'canRename': manageable,
        'canDelete': manageable,
        'canRegisterProject': manageable,
        'canRegisterMixdown': manageable,
        'canUnbindProject': manageable and has_project_binding,
        'canUnbindMixdown': manageable and has_mixdown_binding,
        'canUnbindVideo': manageable and has_video_binding,
        'canRebuildArtifacts': manageable,
        'canRegisterVideo': manageable and not has_video_binding and not has_publication,
        'hasProjectBinding': has_project_binding,
        'hasMixdownBinding': has_mixdown_binding,
        'hasVideoBinding': has_video_binding,
        'hasPublication': has_publication,
        'hasLog': log_path is not None,
        'logPath': str(log_path or ''),
        'artifactIndexRevision': _artifact_index_revision(path),
        'bindings': binding_rows if manageable else {},
        **request,
        'artifacts': artifacts,
        'artifactDetails': artifact_details,
        'handoff': action,
        'delivery': {
            'artifactIds': [item['id'] for item in delivery],
            'openArtifactId': next((item['id'] for item in delivery if Path(item['path']) == open_path), ''),
        },
        'mixArtifactIds': [item['id'] for item in mix] if not locked and not error_message else [],
        'studioTargets': targets if not locked and not error_message else {},
    }
    recovery = _recovery(row, error_message)
    if recovery is not None:
        result['recovery'] = recovery
    return result


def rename(job, name):
    """Rename task metadata through the existing bounded store command."""
    if not isinstance(name, str):
        raise ValueError('任务名必须是文本。')
    return task_store.rename(job, name)


def delete(job):
    """Delete a registered task through the existing reference-safe command."""
    return task_store.delete(job)


def task_log(job):
    """Return the bounded log path for one registered task."""
    row = task_store.record(job)
    path = _task_log_path(row['path'])
    if path is None:
        raise ValueError('此任务还没有处理日志。')
    return path


def register_external(job, kind, path, *, expected_binding_id, expected_binding_revision, check=None):
    """Validate and bind an external SVP project or Mixdown without moving it."""
    if kind not in ('project', 'mixdown'):
        raise ValueError('Quick 只从此入口登记 SVP 工程或 Mixdown。')
    from pipeline import job_lock

    job = task_store.known_job(job)
    source = Path(path).resolve()
    with job_lock(job):
        _cancelled(check)
        _require_binding_identity(job, kind, expected_binding_id, expected_binding_revision)
        return task_contract.bind(job, kind, source, validate=True, dependencies=None, check=check)


def unlink_external(job, kind, *, expected_binding_id, expected_binding_revision, check=None):
    """Remove one task reference while leaving the referenced file untouched."""
    if kind not in BINDING_KINDS:
        raise ValueError('未知的人工登记类型。')
    from pipeline import job_lock

    job = task_store.known_job(job)
    with job_lock(job):
        _cancelled(check)
        artifact_id, revision = _require_binding_identity(
            job, kind, expected_binding_id, expected_binding_revision)
        if not artifact_id or not revision:
            raise task_contract.ContractError('任务当前没有此项人工登记。')
        task_contract.unbind(job, kind)
        return artifact_id


def rebuild_artifacts(job, *, expected_index_revision, check=None):
    """Rebuild the task index after explicit confirmation, preserving its backup."""
    job = task_store.known_job(job)
    _cancelled(check)
    if (_text(expected_index_revision) != _artifact_index_revision(job)):
        raise task_contract.ContractError('任务文件清单已变化，请重新打开确认后再重建。')
    _cancelled(check)
    backup = task_contract.rebuild(job)
    return str(backup or '')


def register_existing_video(job, path, check=None):
    """Explicitly bind an independent local video without inferred upstreams."""
    from pipeline import job_lock

    job = task_store.known_job(job)
    video = Path(path).resolve()
    with job_lock(job):
        _cancelled(check)
        return task_contract.bind(job, 'video', video, validate=True, dependencies=[], check=check, only_unbound=True)


__all__ = (
    'ARTIFACT_ROLES', 'ARTIFACT_STATES', 'BINDING_KINDS', 'BINDING_LABELS', 'TASK_STATES',
    'OPENABLE_SUFFIXES', 'scan', 'detail', 'rename', 'delete', 'task_log',
    'register_existing_video', 'register_external', 'unlink_external', 'rebuild_artifacts',
)
