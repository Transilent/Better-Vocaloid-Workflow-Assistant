"""Pure Python mixdown view projection, export receipt, registration, presets & handoff service (Qt-free)."""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any, Callable
import uuid

from audio_device_recovery import DeviceReconnectGuard
from mixdown_contract import DEFAULT_DEESS_STRENGTH, MAX_VOCALS
from mixdown_runtime import file_sha256, normal_path_key
from project_convert_compat import strip_extended_prefix


def clean_local_path(value: Any) -> str:
    """Strip Windows \\\\?\\ extended path prefixes before QUrl.fromLocalFile."""
    return strip_extended_prefix(str(value or ''))


def make_reconnect_guard() -> DeviceReconnectGuard:
    return DeviceReconnectGuard()


def try_schedule_device_reconnect(guard: DeviceReconnectGuard, *, was_playing: bool, message: Any) -> int | None:
    """Consume the 1-shot hot-swap reconnect budget when playback hits a transient WASAPI device error."""
    if was_playing and guard.should_attempt(str(message or '')):
        guard.mark_attempt()
        return guard.backoff_ms
    return None


def artifacts_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get('artifacts'), list):
        value = value['artifacts']
    if not isinstance(value, (list, tuple)):
        raise ValueError('任务分轨必须来自 quick_results 的 artifacts 列表。')
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('任务产物条目无效。')
        rows.append(dict(item))
    return rows


def recompute_view(
    state: Any,
    *,
    player: Any,
    preview: Any,
    support: dict[str, Any],
    support_ready: bool,
    support_probe_busy: bool,
    initialized: bool,
    active_kind: str,
    source_edit_pending: bool,
    match_busy: bool,
    match_state: dict[str, Any],
    listen_reduction: str,
    export_receipt: dict[str, Any] | None,
    registered_mix_id: str,
    preset_plan: dict[str, Any] | None,
    preset_undo: Any,
) -> dict[str, Any]:
    if preview is not None and player is not None:
        state.set_duration(player.duration())
        state.playing = bool(player.play_intent and not player.failed)
        state.loading = bool(player.loading)
    can_play = bool(support_ready and player is not None and player.available and state.has_media())
    try:
        state.validate_output()
        inputs_ready = True
    except (OSError, ValueError, TypeError):
        inputs_ready = False
    can_export = bool(support_ready and inputs_ready and not active_kind and not support_probe_busy)
    can_register = bool(state.job and state.result and not state.result_stale
                        and export_receipt and not active_kind
                        and not support_probe_busy and not registered_mix_id)
    if active_kind == 'mixdown-export':
        state_name = 'exporting'
    elif active_kind == 'mixdown-register':
        state_name = 'registering'
    elif support_probe_busy or active_kind == 'mixdown-install':
        state_name = 'checking'
    elif player is not None and player.failed:
        state_name = 'error'
    elif state.loading:
        state_name = 'loading'
    elif state.playing:
        state_name = 'playing'
    elif state.result:
        state_name = 'result'
    elif state.has_media():
        state_name = 'ready'
    else:
        state_name = 'idle'
    state.state = state_name
    state.busy = bool(active_kind or state.loading or support_probe_busy)
    support['busy'] = bool(support_probe_busy or active_kind == 'mixdown-install')
    support['playerAvailable'] = bool(player is not None and player.available)
    support['visible'] = bool(initialized and (not support_ready or not support['playerAvailable']))
    support['canInstall'] = bool(
        initialized and not support_ready
        and support.get('installerAvailable')
        and not active_kind and not support_probe_busy)
    view = state.view(can_export=can_export, can_register=can_register, can_play=can_play)
    view['canSeek'] = bool(can_play and player.ready and not source_edit_pending)
    view['canMatchLoudness'] = bool(view['canSeek'] and not player.failed and not match_busy)
    view['loudnessMatch'] = copy.deepcopy(match_state)
    view['listenReductionTrack'] = listen_reduction
    receipt_output = ((export_receipt or {}).get('output')
                      if isinstance(export_receipt, dict) else {})
    export_sha = str((receipt_output or {}).get('sha256') or '')
    export_fresh = bool(state.result and export_sha
                        and not state.result_stale and not active_kind)
    registered_video = bool(export_fresh and state.job and registered_mix_id)
    independent_video = bool(export_fresh and not state.job)
    view['canComposeVideo'] = registered_video or independent_video
    render_result = (export_receipt or {}).get('render_result') or {}
    view['exportQuality'] = copy.deepcopy(render_result.get('quality') or {})
    view['videoHandoffMode'] = ('registered' if registered_video else
                                'independent' if independent_video else '')
    view['support'] = copy.deepcopy(support)
    view['supportVisible'] = bool(support['visible'])
    view['componentMissing'] = bool(initialized and not support_ready)
    view['supportMessage'] = str(support.get('message') or '')
    view['nativeOutputAvailable'] = bool(player is not None and player.available)
    view['presetPreview'] = copy.deepcopy(preset_plan['view']) if preset_plan else {}
    view['canUndoPreset'] = preset_undo is not None
    return view


def build_export_receipt(report: dict[str, Any], context: dict[str, Any],
                         source_rows: list[dict[str, Any]],
                         task_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    output = Path(report['output_path']).resolve()
    info = output.stat()
    parameters = dict(context.get('parameters') or {})
    parameters['output_path'] = str(output)
    inputs = dict(context.get('inputs') or {})
    hashes = report.get('source_sha256') or {}
    hashes = {normal_path_key(path): value for path, value in hashes.items()}

    def with_hash(entry):
        row = dict(entry)
        row['sha256'] = hashes.get(normal_path_key(row['path']))
        return row

    saved_inputs = {
        'vocals': [with_hash(entry) for entry in inputs.get('vocals', [])],
        'instrumental_path': with_hash(inputs['instrumental_path']),
    }
    provenance = [
        {'track_id': row['id'], 'path': row['path'],
         'artifact_id': row.get('artifact_id'),
         'artifact_revision': row.get('artifact_revision'),
         'provenance': row.get('provenance')}
        for row in source_rows
    ]
    dependencies = [
        {'id': str(row.get('id') or ''), 'revision': str(row.get('revision') or '')}
        for row in task_artifacts
        if row.get('id') and row.get('revision')
    ]
    return {
        'schema': 'vwa.audio_mixdown.receipt/v2',
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'backend': 'audio_mixdown.render',
        'registration': 'explicit_user_action',
        'source_job': context.get('source_job'),
        'dependencies': dependencies,
        'inputs': saved_inputs,
        'input_provenance': provenance,
        'parameters': parameters,
        'output': {'path': str(output), 'bytes': info.st_size,
                   'mtime_ns': info.st_mtime_ns,
                   'sha256': report.get('output_sha256')},
        'render_result': {
            'sample_rate': report.get('sample_rate'),
            'duration_seconds': report.get('duration_seconds'),
            'peak_dbfs': report.get('peak_dbfs'),
            'quality': copy.deepcopy(report.get('quality') or {}),
            'warnings': list(report.get('warnings') or []),
        },
    }


def artifact_identities(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return sorted((str(row.get('id') or ''), str(row.get('revision') or ''),
                   str(row.get('sha256') or '')) for row in rows)


def execute_mix_registration(
    *,
    job: str,
    destination: Path,
    expected_sha: str,
    expected_artifacts: list[tuple[str, str, str]],
    expected_token: Any,
    get_token_fn: Callable[[], Any],
    export_plan: Any,
    receipt: dict[str, Any],
    stop: Any,
) -> tuple[str, str]:
    from task_store import locked
    from task_contract import bind, inspect, update_metadata

    if locked(job):
        raise ValueError('任务在登记开始前进入运行状态；本次没有写入任务登记。')
    if get_token_fn() != expected_token:
        raise ValueError('登记期间混音输入或参数已变化；本次没有写入任务登记。')
    if not export_plan.signatures_unchanged():
        raise ValueError('登记期间源音频已变化；本次没有写入任务登记。')
    if file_sha256(destination, stop.is_set) != expected_sha:
        raise ValueError('导出 WAV 在登记前发生变化；请重新导出后再登记。')
    if expected_artifacts:
        rows = inspect(job, check=stop.is_set,
                       artifact_ids=[identifier for identifier, _, _ in expected_artifacts])
        by_id = {str(row.get('id') or ''): row for row in rows}
        for identifier, revision, digest in expected_artifacts:
            row = by_id.get(identifier)
            if (row is None or str(row.get('revision') or '') != revision
                    or str(row.get('sha256') or '') != digest
                    or row.get('state') != 'verified'):
                raise ValueError('任务分轨已变化或校验失效；本次没有写入任务登记。')
    if get_token_fn() != expected_token:
        raise ValueError('登记写入前混音输入或参数已变化；本次没有写入任务登记。')
    if locked(job):
        raise ValueError('任务在登记写入前进入运行状态；本次没有写入任务登记。')
    dependencies = [{'id': identifier, 'revision': revision}
                    for identifier, revision, _digest in expected_artifacts]
    artifact_id = bind(job, 'mixdown', destination,
                       dependencies=dependencies,
                       expected_sha256=expected_sha)
    saved = copy.deepcopy(receipt)
    saved['artifact_id'] = artifact_id
    saved['registered_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    update_metadata(job, audio_mixdown=saved)
    return artifact_id, str(destination)


def load_preset_catalog(library: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    custom = {'key': 'custom', 'label': '自定义', 'value': 'custom'}
    snapshot = library.snapshot()
    rows = [custom]
    meta: dict[str, dict[str, Any]] = {}
    labels: dict[str, int] = {}
    for entry in snapshot.get('entries', []):
        if entry.get('type') != 'mixdown':
            continue
        identifier = str(entry.get('id') or '')
        version = str(entry.get('version') or '')
        if not identifier or not version:
            continue
        if identifier.startswith('builtin.mixdown.'):
            key = identifier[len('builtin.mixdown.'):]
        else:
            key = identifier + '@' + version
        label = str(entry.get('name') or identifier)
        labels[label] = labels.get(label, 0) + 1
        if labels[label] > 1:
            label += ' · 本机'
        rows.append({'key': key, 'label': label, 'value': key})
        meta[key] = {'id': identifier, 'version': version,
                     'sha256': entry.get('sha256'), 'label': label}
    return rows, meta


def build_preset_preview(document: dict[str, Any], entry: dict[str, Any], key: str,
                         stamp: tuple, *, include_width: bool, include_tracks: bool) -> dict[str, Any]:
    from mixdown_preset_documents import plan_application

    label = str(entry.get('name') or key)
    plan = plan_application(document, stamp[4], sample_rate=stamp[2],
                            include_width=include_width, include_tracks=include_tracks)
    return {'stamp': stamp, 'plan': plan, 'view': {
        'key': key, 'name': label, 'changes': plan['changes'], 'adjusted': plan['adjusted'],
        'hasWidth': 'width' in document['payload'],
        'hasTracks': 'track_template' in document['payload'] or 'vocal_deess' in document['payload'],
        'hasTrackTemplate': 'track_template' in document['payload'],
        'hasDeess': 'vocal_deess' in document['payload'],
        'includeWidth': include_width, 'includeTracks': include_tracks,
    }}


def build_saved_preset_document(preset_state: dict[str, Any], name: str,
                                *, include_width: bool) -> dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError('预设名称不能为空。')
    from mixdown_preset_documents import make_document

    payload = preset_state['processing']
    if any(row['deess_enabled'] or row['deess_strength'] != DEFAULT_DEESS_STRENGTH
           for row in preset_state['tracks']):
        payload['vocal_deess'] = [
            {'enabled': row['deess_enabled'], 'strength': row['deess_strength']}
            for row in preset_state['tracks']
        ]
    return make_document(payload, name.strip(), include_width=include_width)


def resolve_task_sources(job: Any, artifacts: Any) -> tuple[Path, list[dict[str, Any]]]:
    job_path = Path(job).expanduser().resolve()
    if not (job_path / 'request.json').is_file():
        raise ValueError('任务缺少 request.json。')
    rows = artifacts_list(artifacts)
    by_relative = {}
    for row in rows:
        if row.get('state') != 'verified' or row.get('available') is False:
            continue
        path_text = str(row.get('path') or '')
        if not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        if not path.is_file() or not path.is_relative_to(job_path):
            raise ValueError('任务分轨必须位于该任务目录内。')
        relative = path.relative_to(job_path).as_posix()
        if relative not in ('audio/vocals.wav', 'audio/lead.wav',
                            'audio/backing.wav', 'audio/instrumental.wav'):
            continue
        if row.get('role') != 'audio':
            continue
        identity = {
            'id': str(row.get('id') or ''), 'revision': str(row.get('revision') or ''),
            'sha256': str(row.get('sha256') or ''), 'provenance': 'task_separation',
            'relative': relative,
        }
        if not identity['id'] or not identity['revision'] or not identity['sha256']:
            raise ValueError('任务分轨缺少完整产物身份。')
        by_relative[relative] = {'path': str(path), 'artifact': identity}
    backing = by_relative.get('audio/instrumental.wav')
    if backing is None:
        raise ValueError('任务缺少已核验的 audio/instrumental.wav。')
    request_config = json.loads((job_path / 'request.json').read_text(encoding='utf-8'))
    if not isinstance(request_config, dict):
        raise ValueError('任务 request.json 必须是对象。')
    voice_mode = str(request_config.get('voice_mode') or 'single')
    if voice_mode in ('dual', 'roformer', 'roformer_onnx'):
        if not ('audio/lead.wav' in by_relative and 'audio/backing.wav' in by_relative):
            raise ValueError('双声部任务缺少已核验的 lead/backing 分轨。')
        vocal_names = (('audio/lead.wav', '主唱分轨'), ('audio/backing.wav', '和声分轨'))
    elif voice_mode == 'single':
        if 'audio/vocals.wav' not in by_relative:
            raise ValueError('单声部任务缺少已核验的 vocals.wav。')
        vocal_names = (('audio/vocals.wav', '人声分轨'),)
    elif 'audio/lead.wav' in by_relative and 'audio/backing.wav' in by_relative:
        vocal_names = (('audio/lead.wav', '主唱分轨'), ('audio/backing.wav', '和声分轨'))
    elif 'audio/vocals.wav' in by_relative:
        vocal_names = (('audio/vocals.wav', '人声分轨'),)
    else:
        raise ValueError('任务缺少已核验的真实歌声分轨。')
    if len(vocal_names) > MAX_VOCALS:
        raise ValueError('任务歌声分轨数量超过混音上限。')
    sources = []
    for index, (name, label) in enumerate(vocal_names):
        item = by_relative[name]
        sources.append({'id': f'vocal-{index + 1}', 'label': label,
                        'path': item['path'], 'artifact': item['artifact']})
    sources.append({'id': 'instrumental', 'label': '伴奏',
                    'path': backing['path'], 'artifact': backing['artifact']})
    return job_path, sources


def resolve_chorus_handoff(payload: Any) -> tuple[Any, Any, Path | None, str]:
    if not isinstance(payload, dict):
        raise ValueError('修音交接数据无效。')
    vocals = payload.get('vocals') or ()
    backing = payload.get('backing')
    title = str(payload.get('title') or '').strip()
    variant = str(payload.get('variant') or 'processed')
    variant_label = {'original': '原声', 'timing': '仅对齐', 'pitch': '仅修音',
                     'processed': '处理后'}.get(variant, variant)
    safe_title = ''.join('_' if ch in '\\/:*?"<>|' else ch for ch in title).strip(' .')
    output_path = None
    if safe_title:
        from common import output_directory
        stamp = time.strftime('%Y%m%d-%H%M%S')
        root = Path(output_directory()).expanduser().resolve()
        candidate = root / f'{safe_title}-mixdown-{stamp}.wav'
        while candidate.exists():
            candidate = root / f'{safe_title}-mixdown-{stamp}-{uuid.uuid4().hex[:6]}.wav'
        output_path = candidate
    if backing and backing.get('path'):
        notice = f'已从修音工作台载入 {len(vocals)} 路人声（{variant_label}）与伴奏，可直接试听混音。'
    else:
        notice = f'已从修音工作台载入 {len(vocals)} 路人声（{variant_label}）；请选择伴奏文件即可开始试听。'
    return vocals, backing, output_path, notice
