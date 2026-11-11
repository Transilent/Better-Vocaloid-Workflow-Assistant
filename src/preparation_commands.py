"""Preparation drafts and the desktop process adapter shared by both frontends."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import subprocess
from time import monotonic
from uuid import uuid4

import workflow
from common import ROOT, app_path, load_config, native_path


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


def _default_fields():
    return {**deepcopy(workflow.DEFAULT_OPTIONS), **_FIELDS, 'imported_stems': {}}


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


def create_from_draft(draft, *, reuse_job=''):
    """Compile once with the same preparation policy and task writer as Widgets."""
    from pipeline import create_job, reuse_download

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
    arguments = dict(
        lyrics=data['lyrics'], backing_lyrics=data['backing_lyrics'],
        imported_stems=data['imported_stems'], parent=data['output_directory'] or None,
        workflow_graph=graph, export_preferences=workflow.export_preferences(options),
        lyric_reference={'origin': reference} if reference else None,
        **workflow.execution_options(options, midi_enabled=data['target'] != 'separation'),
    )
    if reuse_job:
        from task_store import known_job
        return reuse_download(known_job(reuse_job), expected_bv=bilibili_draft_bv(draft), **arguments)
    return create_job(data['source'] if data['input_mode'] == 'bilibili' else '',
                      local_music=draft.music_path if data['input_mode'] == 'local' else None,
                      remember=False, **arguments)


def progress_view(job):
    from progress_state import TITLES, snapshot

    data = snapshot(job)
    state = data['status'].get('state', 'ready')
    message = data['event'].get('detail') or TITLES.get(data['current'], '')
    return {'state': state, 'job': str(job), 'current': data['current'] or '', 'message': message,
            'completed': data['completed'], 'total': len(data['stages']), 'elapsed': data['elapsed']}


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
