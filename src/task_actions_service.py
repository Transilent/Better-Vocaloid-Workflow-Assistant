"""Pure Python task safety, preparation/component execution, and artifact-dispatch service (Qt-free)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import component_operations as components
from i18n import tr
from operation_diagnostics import operation_event
import preparation_commands as preparation
import quick_results
from quick_media_handoff import artifact_identity

_CANCELLABLE_TUNING = frozenset(('tuning-loading', 'tuning-freezing', 'tuning-running',
                                 'tuning-checking-calibration', 'tuning-exporting'))


def can_cancel(kind: str) -> bool:
    if kind == 'video-register':
        return False
    return (kind in ('component', 'preparation', 'update-download', 'mixdown-export',
                     'mixdown-install', 'mixdown-support-probe') or kind in _CANCELLABLE_TUNING
            or kind.startswith(('calibration-', 'workshop-', 'video-', 'publication-', 'chorus-', 'convert-')))


def component_result(request, stop, send):
    def progress(done, total, message):
        mode = 'verify' if request.action == 'verify' else 'download'
        if request.action == 'install':
            if message.startswith(('正在安装', '正在验证', '正在确认', '正在检查', '正在校验')):
                mode = 'install' if message.startswith('正在安装') else 'verify'
            if mode != 'download':
                total = 0
        send({'operation_id': request.operation_id, 'done': done, 'total': total,
              'message': message, 'phase': mode,
              'unit': 'items' if request.action == 'verify' and request.component_id in ('builtin', 'expression')
                       else 'bytes'})

    if request.action == 'install':
        try:
            components.install(request.component_id, runtime=request.runtime, source=request.source,
                               force=request.force, progress=progress, check=stop.is_set)
        except InterruptedError:
            return 'cancelled', '安装已取消；可继续使用已保留的下载。', None
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


def run_preparation(draft, existing, retry_cpu, stop, send, *, reuse_job='', reprocess=''):
    from task_store import known_job

    if stop.is_set():
        raise InterruptedError('已取消任务。')
    job = (known_job(existing) if existing else preparation.reprocess_task(draft, reprocess) if reprocess else
           preparation.create_from_draft(draft, reuse_job=reuse_job) if reuse_job else preparation.create_from_draft(draft))
    send({'state': 'running', 'job': str(job), 'message': '正在启动准备任务…'})
    try:
        code = preparation.run_process(job, stop=stop, on_progress=send, retry_cpu=retry_cpu)
        if code:
            operation_event('progress', exit_code=code & 0xffffffff)
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
        if isinstance(exc, InterruptedError):
            return {'job': str(job), 'state': 'cancelled', 'message': tr('任务已停止')}
        from error_recovery import explain
        recovery = explain(exc)
        return {'job': str(job), 'state': 'failed', 'message': str(exc)[:1600],
                'recovery_title': recovery['title'], 'recovery_message': recovery['message'],
                'recovery_action': recovery['action']}


def controller_view(controller) -> dict[str, Any]:
    if controller is None:
        return {}
    value = getattr(controller, 'view', {})
    if not isinstance(value, dict):
        raise TypeError('task controller view is not a mapping')
    return dict(value)


def _file_url_to_local(text: str) -> str:
    parsed = urlparse(text)
    if parsed.scheme.casefold() != 'file':
        return ''
    raw = parsed.path or ''
    if parsed.netloc and parsed.netloc.casefold() != 'localhost':
        raw = f'//{parsed.netloc}{raw}'
    return url2pathname(unquote(raw))


def reference_paths(values, *, url_to_local_fn: Callable[[Any], str | None] | None = None) -> list[str]:
    """Extract only local path-shaped values from a selected controller view."""
    result = []
    for value in values:
        if url_to_local_fn is not None:
            converted = url_to_local_fn(value)
            if converted is not None:
                if converted:
                    result.append(converted)
                continue
        if not isinstance(value, (str, os.PathLike)):
            continue
        text = os.fspath(value).strip().strip('"').strip()
        if not text or ('://' in text and not text.casefold().startswith('file:')):
            continue
        if text.casefold().startswith('file:'):
            text = _file_url_to_local(text)
        if text:
            result.append(text)
    return list(dict.fromkeys(result))


def task_reference_groups(
    *,
    mixdown=None,
    mixdown_tracks=(),
    tuning=None,
    video=None,
    publication=None,
    calibration=None,
    subtitles=None,
    subtitle_drafts=None,
    preparation=None,
    operation=None,
    operations=None,
    chorus_paths=(),
    url_to_local_fn: Callable[[Any], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Build task-reference groups without coupling task safety to any one page."""
    groups = []

    def add(label, *values):
        paths = reference_paths(values, url_to_local_fn=url_to_local_fn)
        if paths:
            groups.append({'label': label, 'paths': paths})

    mix = controller_view(mixdown)
    track_rows = mixdown_tracks if isinstance(mixdown_tracks, (list, tuple)) else ()
    mix_label = ('简易混音 · 正在播放' if mix.get('playing') else
                 '简易混音 · 后台处理中' if mix.get('busy') else '简易混音 · 已载入')
    add(mix_label, mix.get('job'), mix.get('output'), mix.get('result'),
        *(row.get('path') for row in track_rows if isinstance(row, dict)))

    tune = controller_view(tuning)
    add('调音 · ' + ('正在处理' if tune.get('busy') else '已载入'),
        tune.get('job'), tune.get('source'), tune.get('candidate'), tune.get('savedCopy'))

    vid = controller_view(video)
    add('视频 · ' + ('正在处理' if vid.get('busy') else '已载入'),
        vid.get('job'), vid.get('sourceVideo'), vid.get('mixdown'), vid.get('output'),
        vid.get('preview'), vid.get('result'))

    publish = controller_view(publication)
    add('发布 · ' + ('正在处理' if publish.get('busy') else '已载入'),
        publish.get('job'), publish.get('video'), publish.get('bundle'), publish.get('coverFile'))

    calibrate = controller_view(calibration)
    add('声库校准 · ' + ('正在处理' if calibrate.get('busy') else '已载入'),
        calibrate.get('job'), calibrate.get('project'), calibrate.get('reference'),
        calibrate.get('folder'), calibrate.get('renders'), calibrate.get('session'),
        calibrate.get('result'), calibrate.get('report'))

    subtitle_view = controller_view(subtitles)
    add('字幕工具 · 已载入来源', subtitle_view.get('taskJobId'), subtitle_view.get('sourcePath'),
        subtitle_view.get('exportedPath'))
    drafts = subtitle_drafts if isinstance(subtitle_drafts, dict) else {}
    for kind in sorted(drafts):
        row = drafts.get(kind) if isinstance(drafts.get(kind), dict) else {}
        add('字幕工具 · ' + str(kind).upper() + ' 草稿', row.get('_task_job_id'),
            row.get('_source_path'), row.get('_exported_path'))
    add('修音工作台 · 已载入', *chorus_paths)

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


def inspect_task_progress_work(expected_job: str, stop) -> dict[str, Any]:
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


def validate_independent_mix_candidate(path: str, sha256: str, video_view: dict[str, Any]) -> tuple[Path, str]:
    candidate = Path(path).expanduser().resolve()
    expected = str(sha256 or '').strip().lower()
    if not candidate.is_file() or not expected:
        raise ValueError('独立 Mixdown 文件或摘要无效。')
    current = str(video_view.get('mixdown') or '')
    current_path = Path(current).expanduser().resolve() if current else None
    if video_view.get('job'):
        raise ValueError('视频页仍关联任务；请先回到任务与结果切换输入，未覆盖现有 Mixdown。')
    if current_path is not None and current_path != candidate:
        raise ValueError('视频页已选择另一份 Mixdown；为避免覆盖，请先在视频页处理现有输入。')
    return candidate, expected


def verify_independent_mix_work(candidate: Path, expected: str, stop) -> str:
    from common import file_hash

    if not candidate.is_file():
        raise ValueError('独立 Mixdown 已不存在，请从简易混音重新导出。')
    if file_hash(candidate, check=stop.is_set) != expected:
        raise ValueError('独立 Mixdown 在交接前发生变化，请重新导出。')
    return str(candidate)


def validate_independent_mix_commit(verified: str, video_view: dict[str, Any]) -> Path:
    current = str(video_view.get('mixdown') or '')
    current_path = Path(current).expanduser().resolve() if current else None
    target = Path(verified).resolve()
    if video_view.get('job'):
        raise ValueError('视频页在交接期间关联了任务；本次未覆盖 Mixdown。')
    if current_path is not None and current_path != target:
        raise ValueError('视频页在交接期间选择了另一份 Mixdown；本次未覆盖。')
    return target


def wanted_artifact_ids(detail: dict[str, Any], artifact_id: str | None, route: str | None):
    if route == 'audio_mixdown':
        return detail.get('mixArtifactIds', ())
    if route in ('video_mixdown', 'publishing'):
        return detail.get('studioTargets', {}).get(route, ())
    if artifact_id is not None:
        return [artifact_id]
    return detail.get('delivery', {}).get('artifactIds', ())


def resolve_checked_artifact_plan(
    shown: dict[str, Any],
    detail: dict[str, Any],
    job: str,
    artifact_id: str | None,
    route: str | None,
    expected: list,
    expected_action: dict[str, Any] | None,
) -> dict[str, Any]:
    if expected_action is not None:
        actual = quick_results.task_primary_action(detail)
        if any(actual[key] != expected_action[key] for key in ('kind', 'artifactId', 'artifactIds')):
            raise ValueError('当前交付已变化，请核对后再继续。')
    by_id = {row['id']: row for row in detail['artifacts'] if row['available']}
    if artifact_id is not None:
        row = by_id.get(artifact_id)
        if not row or not row['openable'] or [artifact_identity(row)] != expected:
            raise ValueError('该文件已变更、无法核验或不支持直接打开。')
        if route == 'tuning':
            return {'action': 'tuning', 'row': row}
        return {'action': 'open_local', 'path': Path(row['path'])}
    action = detail.get('handoff')
    delivery = detail['delivery']
    if route in ('video_mixdown', 'publishing'):
        rows = [by_id[key] for key in wanted_artifact_ids(detail, artifact_id, route) if key in by_id]
        if [artifact_identity(row) for row in rows] != expected:
            raise ValueError('文件或上游版本已变化，请重新核对。')
        return {'action': 'accept_media', 'route': route, 'rows': rows}
    if route == 'audio_mixdown':
        rows = [by_id[key] for key in wanted_artifact_ids(detail, artifact_id, route) if key in by_id]
        if [artifact_identity(row) for row in rows] != expected:
            raise ValueError('任务分轨或上游版本已变化，请重新核对。')
        return {'action': 'audio_mixdown', 'rows': rows}
    rows = [by_id[key] for key in delivery['artifactIds'] if key in by_id]
    if (not detail['canDispatch'] or action != shown.get('handoff')
            or [artifact_identity(row) for row in rows] != expected):
        raise ValueError('产物或上游版本已变化，请核对结果后再交接。')
    row = by_id.get(delivery['openArtifactId'])
    directories = {Path(item['path']).parent for item in rows}
    directory = next(iter(directories)) if len(directories) == 1 else Path(job)
    destination = Path(row['path']) if row and row['openable'] else directory
    handoff = {'jobId': job, 'action': action, 'artifacts': rows, 'destination': str(destination)}
    return {'action': 'dispatch', 'handoff_kind': action, 'handoff': handoff,
            'row': row, 'rows': rows, 'destination': destination}
