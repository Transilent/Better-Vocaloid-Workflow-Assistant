"""Preparation drafts and the desktop process adapter shared by both frontends."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import subprocess
from time import monotonic
from uuid import uuid4

import workflow
from common import ROOT, app_path, atomic_json, load_config, native_path, workspace_state


_STEMS = ('lead', 'backing', 'instrumental')
_CHOICES = {
    'input_mode': ('local', 'bilibili'), 'voice_mode': ('single', 'dual', 'import', 'roformer'),
    'separator_model': ('dual', 'roformer'), 'target': ('separation', 'midi', 'subtitles'),
    'language': ('zh', 'ja'), 'zh_lyric_mode': ('hanzi', 'pinyin'),
    'roformer_device': ('auto', 'cpu', 'cuda'), 'separation_flow': ('cascade', 'independent'),
    'separation_device': ('cpu', 'dml'), 'midi_device': ('cpu', 'dml'),
    'midi_backend': ('v1', 'v2'), 'alignment_engine': ('hfa', 'tifa'), 'midi_steps': (8, 16, 32),
    'reference_mode': ('local', 'netease'),
}
_TEXT = {'source': 4096, 'lyrics': 65536, 'backing_lyrics': 65536, 'bpm': 24, 'output_directory': 4096,
         'lyrics_lookup': 4096}
_BOOL = {'recognize_lyrics', 'include_instrumental', 'export_pitch', 'advanced_expanded'}
_FORMATS = {'project_formats': ('ustx', 'vsqx', 'svp'), 'audio_formats': ('wav', 'flac', 'mp3', 'ogg')}
_FIELDS = {'source': '', 'lyrics': '', 'backing_lyrics': '', 'target': 'midi', 'input_mode': 'local',
           'output_directory': '', 'bpm': '', 'reference_mode': 'local',
           'lyrics_lookup': '', 'advanced_expanded': False}
_PERSIST_OPTION_KEYS = (
    'language', 'recognize_lyrics', 'zh_lyric_mode', 'midi_steps', 'midi_backend',
    'alignment_engine', 'separation_flow', 'separation_device', 'midi_device',
    'roformer_device', 'dml_adapter', 'cpu_threads', 'bpm', 'project_formats',
    'audio_formats', 'project_export_enabled', 'audio_export_enabled', 'export_pitch',
    'include_instrumental', 'subtitles_mode',
)


def _default_fields():
    return {**deepcopy(workflow.DEFAULT_OPTIONS), **_FIELDS, 'imported_stems': {}}


_PREPARATION_PRESET_VERSION = '0.1.0'
_PREPARATION_CAPABILITY = 'preparation.parameters'


def _preset_label(name):
    label = name.strip() if isinstance(name, str) else ''
    if not label or len(label) > 80 or label.startswith('内置') or label == '自定义':
        raise ValueError('请填写 1–80 字的名称，不以“内置”开头。')
    return label


def make_preset_document(draft, name):
    """Build a portable preparation preset without song, lyric or machine data."""
    from preset_documents import CONTRACTS, PREPARATION_KEYS, validate_document

    label = _preset_label(name)
    values = draft.preferences()
    options = {key: deepcopy(values[key]) for key in PREPARATION_KEYS}
    slots = []
    if options['voice_mode'] == 'import':
        slots.append({'id': 'stems', 'kind': 'separated_stems', 'required': True})
    document = {
        'format': 'vwa.preset',
        'format_version': 1,
        'id': 'user.preparation.' + uuid4().hex,
        'type': 'preparation',
        'version': _PREPARATION_PRESET_VERSION,
        'metadata': {
            'name': label,
            'description': '保存自 Quick 准备页；只包含处理参数和工作流步骤，不包含歌曲、歌词、路径或设备选择。',
        },
        'requires': {
            'preset_api': 1,
            'engine_contract': CONTRACTS['preparation'],
            'capabilities': [_PREPARATION_CAPABILITY],
        },
        'binding_slots': slots,
        'payload': {'graph': graph_for(draft.view['target']), 'options': options},
    }
    return validate_document(document)


def append_preset(library, draft, name):
    """Append one uniquely identified user preset without replacing library rows."""
    document = make_preset_document(draft, name)
    revision = library.snapshot()['revision']
    return library.put_document(document, expected_revision=revision)


def remove_preset(library, identifier, version):
    """Remove one active user-library row while retaining immutable history bytes."""
    revision = library.snapshot()['revision']
    return library.delete(identifier, version, expected_revision=revision)


def apply_preset(draft, payload):
    """Apply preparation choices while retaining the draft's media and device choices."""
    if not isinstance(payload, dict) or not isinstance(payload.get('options'), dict):
        raise ValueError('准备预设参数格式无效。')
    graph = deepcopy(payload.get('graph'))
    if not isinstance(graph, dict):
        raise ValueError('准备预设工作流格式无效。')
    values = {**draft.preferences(), **deepcopy(payload['options'])}
    return draft.with_options(workflow.validate_options(values), graph)


def preset_catalog(snapshot, legacy_presets):
    """Merge shared-library preparation entries with readable legacy workflow saves."""
    entries = []
    values = snapshot.get('entries', []) if isinstance(snapshot, dict) else []
    for row in values:
        if not isinstance(row, dict) or row.get('type') != 'preparation':
            continue
        identifier = row.get('id')
        version = row.get('version')
        if not isinstance(identifier, str) or not isinstance(version, str):
            continue
        name = row.get('name') if isinstance(row.get('name'), str) else identifier
        entries.append({
            'key': 'library:' + identifier + '@' + version,
            'name': name,
            'preset_name': name,
            'id': identifier,
            'version': version,
            'source': 'library',
            'builtin': bool(row.get('builtin')),
            'removable': not bool(row.get('builtin')),
        })
    known_names = {row['name'] for row in entries}
    legacy = legacy_presets if isinstance(legacy_presets, dict) else {}
    for name, preset in legacy.items():
        if (not isinstance(name, str) or not isinstance(preset, dict)
                or not isinstance(preset.get('graph'), dict)
                or not isinstance(preset.get('options'), dict)):
            continue
        entries.append({
            'key': 'legacy:' + name,
            'name': name + ' · 旧版' if name in known_names else name,
            'preset_name': name,
            'id': '',
            'version': '',
            'source': 'legacy',
            'builtin': False,
            'removable': True,
        })
    return entries


def fallback_preset_catalog(legacy_presets):
    """Keep built-ins and legacy saves available if the shared library is unreadable."""
    entries = []
    for name in workflow.builtins():
        entries.append({
            'key': 'workflow:' + name,
            'name': name,
            'preset_name': name,
            'id': '',
            'version': '',
            'source': 'workflow',
            'builtin': True,
            'removable': False,
        })
    known_names = {row['name'] for row in entries}
    legacy = legacy_presets if isinstance(legacy_presets, dict) else {}
    for name, preset in legacy.items():
        if (not isinstance(name, str) or not isinstance(preset, dict)
                or not isinstance(preset.get('graph'), dict)
                or not isinstance(preset.get('options'), dict)):
            continue
        entries.append({
            'key': 'legacy:' + name,
            'name': name + ' · 旧版' if name in known_names else name,
            'preset_name': name,
            'id': '',
            'version': '',
            'source': 'legacy',
            'builtin': False,
            'removable': True,
        })
    return entries


def graph_for(target):
    if target == 'separation':
        return {'nodes': {'source': [60, 150], 'separation': [295, 150]}, 'edges': [['source', 'separation']]}
    return workflow.default_graph(target == 'subtitles')


@dataclass(frozen=True)
class PreparationDraft:
    music_path: str = ''
    voice_mode: str = 'dual'
    separator_model: str = 'dual'
    roformer_device: str = 'auto'
    revision: int = 0
    fields: dict = field(default_factory=_default_fields)
    lyric_reference: dict | None = None

    @property
    def view(self):
        return {**deepcopy(self.fields), 'music_path': self.music_path, 'music_name': Path(self.music_path).name,
                'voice_mode': self.voice_mode, 'separator_model': self.separator_model,
                'roformer_device': self.roformer_device, 'revision': self.revision,
                'reference_title': (self.lyric_reference or {}).get('title', '')}

    def with_options(self, options, graph):
        mode = options['voice_mode']
        values = {**self.fields, **deepcopy(options)}
        values['target'] = workflow.validate_graph(graph)
        values['bpm'] = str(options['bpm']) if options['bpm'] is not None else ''
        for key in ('project', 'audio'):
            if not options[key + '_export_enabled']:
                values[key + '_formats'] = []
        values['reference_mode'] = self.fields.get('reference_mode', 'local')
        return replace(self, fields=values, voice_mode='dual' if mode == 'roformer' else mode,
                       separator_model='roformer' if mode == 'roformer' else 'dual',
                       roformer_device=options['roformer_device'], revision=self.revision + 1)

    def edit(self, name, value):
        if name in _CHOICES:
            if isinstance(value, bool) or value not in _CHOICES[name]:
                raise ValueError('选项无效。')
        elif name in _TEXT:
            if not isinstance(value, str) or len(value) > _TEXT[name]:
                raise ValueError('输入文本过长或格式无效。')
        elif name in _BOOL:
            if type(value) is not bool:
                raise ValueError('开关值无效。')
        elif name in _FORMATS:
            if not isinstance(value, list) or any(item not in _FORMATS[name] for item in value):
                raise ValueError('导出格式无效。')
            value = [item for item in _FORMATS[name] if item in value]
        elif name == 'dml_adapter':
            if value != 'auto' and (type(value) is not int or value < 0):
                raise ValueError('DirectML 显卡选择无效。')
        elif name == 'cpu_threads':
            if type(value) is not int or not 0 <= value <= 64:
                raise ValueError('CPU 线程数应为自动或 1–64。')
        else:
            raise ValueError('未知的准备选项。')
        if name in ('voice_mode', 'separator_model', 'roformer_device'):
            return self if getattr(self, name) == value else replace(self, **{name: value}, revision=self.revision + 1)
        if self.fields[name] == value:
            return self
        fields = {**self.fields, name: value}
        if name == 'lyrics' and not value.strip():
            fields['reference_mode'] = 'local'
            return replace(self, fields=fields, lyric_reference=None, revision=self.revision + 1)
        return replace(self, fields=fields, revision=self.revision + 1)

    def set_stem(self, key, path):
        if key not in _STEMS:
            raise ValueError('分轨名称无效。')
        stems = dict(self.fields['imported_stems'])
        if path:
            file = Path(path)
            if not file.is_file():
                raise ValueError('所选分轨文件无法读取。')
            stems[key] = str(file.resolve())
        else:
            stems.pop(key, None)
        if stems == self.fields['imported_stems']:
            return self
        return replace(self, fields={**self.fields, 'imported_stems': stems}, revision=self.revision + 1)

    def apply_lyrics(self, voice, reference):
        name = 'lyrics' if voice == 'lead' else 'backing_lyrics'
        updated = self.edit(name, reference.text)
        if voice != 'lead':
            return updated
        return replace(updated, lyric_reference={'origin': deepcopy(reference.origin),
                        'title': reference.origin.get('title') or reference.origin.get('name')
                        or reference.origin.get('song_id', '')})

    def preferences(self):
        values = self.view
        values['voice_mode'] = self.separator_model if self.voice_mode == 'dual' else self.voice_mode
        values['separation_flow'] = values['separation_flow'] if values['voice_mode'] == 'roformer' else 'cascade'
        values['bpm'] = float(values['bpm']) if values['bpm'].strip() else None
        for key in ('project', 'audio'):
            values[key + '_export_enabled'] = bool(values[key + '_formats'])
        return workflow.validate_options(values)


def save_draft_settings(draft):
    """Persist reusable preparation choices in common workspace settings."""
    try:
        values = draft.preferences()
    except (TypeError, ValueError, OverflowError):
        # Intermediate text such as a partially typed BPM is allowed in the
        # draft; retain the last valid persisted choices until it becomes valid.
        return
    options = {key: deepcopy(values[key]) for key in _PERSIST_OPTION_KEYS}
    options['target'] = draft.view['target']
    state = workspace_state()
    state['quick_preparation'] = {
        'version': 1,
        'options': options,
        'input_mode': draft.view['input_mode'],
        'output_directory': str(draft.view['output_directory'])[:4096],
        'advanced_expanded': bool(draft.view['advanced_expanded']),
    }
    atomic_json(ROOT / 'cache/workspace.json', state)


def restore_draft_settings(draft):
    """Restore reusable choices without loading song, lyric, path or account data."""
    record = workspace_state().get('quick_preparation', {})
    if not isinstance(record, dict) or record.get('version') != 1:
        return draft
    saved = record.get('options', {})
    if not isinstance(saved, dict):
        return draft
    try:
        options = {key: deepcopy(saved[key]) for key in (*_PERSIST_OPTION_KEYS, 'target') if key in saved}
        target = options.get('target', 'midi')
        if target not in ('separation', 'midi', 'subtitles'):
            raise ValueError('保存的准备步骤无效。')
        values = {**draft.preferences(), **options}
        restored = draft.with_options(workflow.validate_options(values), graph_for(target))
        input_mode = record.get('input_mode', 'local')
        if input_mode not in ('local', 'bilibili'):
            raise ValueError('保存的来源模式无效。')
        restored = restored.edit('input_mode', input_mode)
        output = record.get('output_directory', '')
        if isinstance(output, str):
            restored = restored.edit('output_directory', output[:4096])
        advanced = record.get('advanced_expanded', False)
        if type(advanced) is bool:
            restored = restored.edit('advanced_expanded', advanced)
        return restored
    except (KeyError, TypeError, ValueError):
        return draft


def validate_imported_stems(draft):
    """Validate the complete external WAV set before any preparation job is written."""
    if draft.voice_mode != 'import':
        return {}
    labels = {'lead': '主唱', 'backing': '和声', 'instrumental': '伴奏'}
    paths = {}
    values = draft.view['imported_stems']
    for key in _STEMS:
        raw = values.get(key) if isinstance(values, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('请分别选择主唱、和声、伴奏的完整 WAV 文件。')
        path = app_path(raw)
        if path.suffix.casefold() != '.wav':
            raise ValueError(labels[key] + '分轨须为 WAV 文件。')
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError
            import soundfile as sf
            info = sf.info(str(path))
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise ValueError(labels[key] + '分轨不是可读取的完整 WAV 文件。') from error
        if (str(info.format).upper() != 'WAV' or info.frames <= 0
                or info.channels not in (1, 2) or info.samplerate <= 0):
            raise ValueError(labels[key] + '分轨不是可用的单/双声道 WAV 文件。')
        paths[key] = path
    if len(set(paths.values())) != len(_STEMS):
        raise ValueError('主唱、和声、伴奏须为三个不同的完整 WAV 文件。')
    return paths


def selected_component_issues(draft):
    """Check only the optional components selected for this preparation."""
    data = draft.view
    issues = []
    if draft.voice_mode == 'roformer' or (draft.voice_mode == 'dual' and draft.separator_model == 'roformer'):
        import optional_components
        if not optional_components.status()['ready']:
            issues.append('BS-RoFormer 尚未就绪。请安装组件，或改用内置分离。')
    if data['target'] != 'separation' and data['alignment_engine'] == 'tifa':
        if data['midi_backend'] != 'v2':
            issues.append('TiFA 需要 Vocal2Midi 2.0；也可以切回 HubertFA。')
        elif not (ROOT / 'dependencies/vocal2midi2/models/tifa-1.0-onnx').is_dir():
            issues.append('TiFA 模型尚未安装。请安装模型，或切回 HubertFA。')
    return issues


def bilibili_draft_bv(draft):
    from common import extract_bv
    from source_contract import reject_unsupported_bilibili_subpage
    data = draft.view
    if data['input_mode'] != 'bilibili':
        raise ValueError('当前准备未选择 B站来源。')
    reject_unsupported_bilibili_subpage(data['source'])
    return extract_bv(data['source'])


def _job_arguments(draft):
    data = draft.view
    options = draft.preferences()
    graph = graph_for(data['target'])
    issues = selected_component_issues(draft)
    if issues:
        raise ValueError(' '.join(issues))
    if (options['reference_mode'] == 'netease' and data['target'] != 'separation'
            and options['recognize_lyrics'] and
            (not (draft.lyric_reference or {}).get('origin', {}).get('provider') == 'netease' or not data['lyrics'].strip())):
        raise ValueError('请先获取网易云歌词，或切换为本地填写。')
    reference = (draft.lyric_reference or {}).get('origin')
    return dict(
        lyrics=data['lyrics'], backing_lyrics=data['backing_lyrics'],
        imported_stems=data['imported_stems'], parent=data['output_directory'] or None,
        workflow_graph=graph, export_preferences=workflow.export_preferences(options),
        lyric_reference={'origin': reference} if reference else None,
        **workflow.execution_options(options, midi_enabled=data['target'] != 'separation'),
    )


def create_from_draft(draft, *, reuse_job=''):
    """Compile the draft with the shared preparation policy and task writer."""
    validate_imported_stems(draft)
    from pipeline import create_job, reuse_download
    data = draft.view
    arguments = _job_arguments(draft)
    if reuse_job:
        from task_store import known_job
        return reuse_download(known_job(reuse_job), expected_bv=bilibili_draft_bv(draft), **arguments)
    return create_job(data['source'] if data['input_mode'] == 'bilibili' else '',
                      local_music=draft.music_path if data['input_mode'] == 'local' else None,
                      remember=False, **arguments)


def reprocess_task(draft, previous):
    """Use a known task's source with the current draft, creating a new job."""
    validate_imported_stems(draft)
    from pipeline import reuse_download
    from task_store import known_job
    return reuse_download(known_job(previous), **_job_arguments(draft))


def progress_view(job):
    from progress_state import TITLES, snapshot

    data = snapshot(job)
    state = data['status'].get('state', 'ready')
    message = data['event'].get('detail') or TITLES.get(data['current'], '')
    steps = data['status'].get('steps', {})
    stages = [{'key': key, 'label': TITLES.get(key, key), 'state': steps.get(key, {}).get('state', 'pending')}
              for key in data['stages']]
    return {'state': state, 'job': str(job), 'current': data['current'] or '', 'message': message,
            'completed': data['completed'], 'total': len(stages), 'elapsed': data['elapsed'], 'stages': stages}


def recovery_view(job):
    from error_recovery import failure

    detail = failure(job)
    return {'message': detail['raw'][:1600], 'recovery_action': detail['action'],
            'recovery_message': detail['message']}


def read_log_tail(path, size=16384):
    """Read recent diagnostics without loading or watching the entire log."""
    with Path(native_path(path)).open('rb') as stream:
        end = stream.seek(0, 2)
        stream.seek(max(0, end - size))
        data = stream.read(size)
    if end > size and b'\n' in data:
        data = data.split(b'\n', 1)[1]
    return data.decode('utf-8', errors='replace').replace('\x00', '')


def run_process(job, *, stop=None, on_progress=None, on_line=None, retry_cpu=False):
    """Run the established pipeline in a child, with a cancellation flag unique to it."""
    from process_control import terminate_tree

    job = app_path(job)
    token = uuid4().hex if stop is not None else None
    marker = Path(native_path(job / f'.cancel-{token}.flag')) if token else None
    argv = [load_config()['python'], '-B', '-u', str(ROOT / 'pipeline.py'), '--job', str(job)]
    if token:
        argv.extend(('--cancel-token', token))
    if retry_cpu:
        argv.append('--retry-cpu')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('VWA_RUN_CANCEL_TOKEN', None)
    path = Path(native_path(job / 'pipeline.log'))
    requested = False
    last_read = 0.
    try:
        if marker is not None and stop.is_set():
            marker.touch()
            requested = True
        with path.open('ab', buffering=0) as log:
            offset = log.tell()
            with path.open('rb') as reader:
                reader.seek(offset)
                process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    while True:
                        if on_line:
                            for line in reader.readlines(65536):
                                on_line(line.decode('utf-8', errors='replace').rstrip())
                        now = monotonic()
                        if on_progress and now - last_read >= .20:
                            try:
                                value = progress_view(job)
                            except (OSError, TypeError, ValueError):
                                value = None
                            if value is not None:
                                on_progress(value)
                            last_read = now
                        try:
                            code = process.wait(timeout=.10)
                        except subprocess.TimeoutExpired:
                            if stop is not None and stop.is_set() and not requested:
                                marker.touch()
                                requested = True
                            continue
                        if on_line:
                            for line in reader:
                                on_line(line.decode('utf-8', errors='replace').rstrip())
                        return code
                finally:
                    if process.poll() is None:
                        terminate_tree(process)
                        process.wait(timeout=10)
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)
