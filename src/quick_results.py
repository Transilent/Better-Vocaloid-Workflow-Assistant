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
_RESUMABLE_ACTIONS = frozenset(('retry', 'continue', 'ready'))
OPENABLE_SUFFIXES = frozenset(('.wav', '.flac', '.ogg', '.mp3', '.m4a', '.mp4', '.mkv', '.webm',
                               '.mid', '.midi', '.svp', '.ustx', '.vsqx', '.srt', '.ass', '.lrc',
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
        midi = [row for row in verified if row['role'] == 'midi']
        return exports + midi, Path(exports[0]['path']) if len(exports) == 1 else None
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
    artifacts, delivery, open_path = [], [], None
    error_message = ''
    try:
        rows = task_contract.inspect(path, check=check)
        _cancelled(check)
        action = task_contract.handoff(path, rows=rows)
        delivery, open_path = _delivery(path, action, rows)
        deliver_ids = {item['id'] for item in delivery}
        ordered = sorted(rows, key=lambda row: (
            row['id'] not in deliver_ids,
            row['state'] == 'verified',
            row['role'] not in ('editor_project', 'midi', 'mixdown', 'video', 'audio'),
        ))
        artifacts = [_artifact(path, item) for item in ordered]
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
        'artifacts': artifacts,
        'handoff': action,
        'delivery': {
            'artifactIds': [item['id'] for item in delivery],
            'openArtifactId': next((item['id'] for item in delivery if Path(item['path']) == open_path), ''),
        },
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


__all__ = (
    'ARTIFACT_ROLES', 'ARTIFACT_STATES', 'TASK_STATES', 'OPENABLE_SUFFIXES',
    'scan', 'detail', 'rename', 'delete',
)
