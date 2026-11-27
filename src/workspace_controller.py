"""Own the preparation draft, component browser and cross-page commands."""

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from workspace_routes import PAGE_SPECS
from preparation_commands import PreparationDraft
from preparation_languages import PROFILES, QWEN_FEATURE


@dataclass(frozen=True)
class ComponentRequest:
    operation_id: str
    action: str
    component_id: str
    runtime: str
    source: str
    force: bool = False
    precision: str = 'auto'


class WorkspaceController:
    """All mutable state belongs to the application, independent of a page instance."""

    available_routes = ('processing', 'tuning', 'tuning_workshop', 'chorus', 'audio_mixdown', 'video_mixdown', 'publishing',
                        'project_convert', 'components', 'results', 'updates')

    def __init__(self, component_sources=None):
        self.draft = PreparationDraft()
        self.route = 'processing'
        self.return_route = 'processing'
        self.runtime = 'cpu'
        self.roformer_precision = 'auto'
        self.component_sources = dict(component_sources or {})
        self.query = ''
        self.selected_id = 'builtin'
        self.rows = ()
        self.rows_runtime = None
        self.rows_precision = None
        self.active = None
        self.operation = {'state': 'idle', 'action': '', 'message': '', 'done': 0, 'total': 0}
        self.preparation_task = {'id': '', 'state': 'idle', 'job': '', 'message': '',
                                 'current': '', 'completed': 0, 'total': 0, 'elapsed': 0,
                                 'can_retry': False, 'recovery_action': ''}
        self.task_rows = ()
        self.task_query = ''
        self.selected_task_id = ''
        self.selected_task_artifact_id = ''
        self.task_detail = {}
        self.handoff = {}

    @property
    def draft_view(self):
        return self.draft.view

    @property
    def routes(self):
        return sorted((spec for spec in PAGE_SPECS if spec.page_id in self.available_routes),
                      key=lambda spec: spec.sidebar_order)

    def navigate(self, destination):
        spec = next((spec for spec in PAGE_SPECS if spec.page_id == destination), None)
        target = (spec.target or spec.page_id) if spec else None
        if target not in self.available_routes or target == self.route:
            return False
        if target == 'components':
            self.return_route = self.route
        self.route = target
        return True

    def return_to_preparation(self):
        return self.navigate(self.return_route)

    def set_music(self, path):
        if path:
            candidate = Path(path).expanduser()
            if not candidate.is_file():
                raise ValueError('所选音频文件无法读取。')
            path = str(candidate.resolve())
        if self.draft.music_path != path:
            self.draft = replace(self.draft, music_path=path, revision=self.draft.revision + 1)
            return True
        return False

    def set_draft(self, draft):
        if self.draft == draft:
            return False
        self.draft = draft
        return True

    def set_preparation_field(self, name, value):
        return self.set_draft(self.draft.edit(name, value))

    @property
    def visible_tasks(self):
        needle = self.task_query.strip().casefold()
        return tuple(row for row in self.task_rows if not needle or needle in
                     str(row.get('searchText') or row['title']).casefold())

    def set_task_rows(self, rows):
        self.task_rows = tuple(rows)

    def focus_task(self, job_id):
        """Switch the inspected task without keeping an unrelated file selected."""
        if job_id == self.selected_task_id:
            return False
        self.selected_task_id = job_id
        self.selected_task_artifact_id = ''
        self.task_detail = {}
        return True

    def select_task(self, job_id):
        if not any(row['jobId'] == job_id for row in self.task_rows):
            return False
        return self.focus_task(job_id)

    @property
    def selected_task_artifact(self):
        return next((row for row in self.task_detail.get('artifacts', ())
                     if row['id'] == self.selected_task_artifact_id), {})

    def select_task_artifact(self, artifact_id):
        if (artifact_id == self.selected_task_artifact_id or
                not any(row['id'] == artifact_id for row in self.task_detail.get('artifacts', ()))):
            return False
        self.selected_task_artifact_id = artifact_id
        return True

    def set_task_detail(self, job_id, detail):
        if self.selected_task_id != job_id or detail.get('jobId', job_id) != job_id:
            return False
        self.task_detail = detail
        artifacts = detail.get('artifacts', ())
        if not self.selected_task_artifact:
            by_id = {row['id']: row for row in artifacts}
            delivery = detail.get('delivery', {})
            preferred = (delivery.get('openArtifactId'), *delivery.get('artifactIds', ()))
            self.selected_task_artifact_id = next(
                (key for key in preferred if key in by_id and by_id[key].get('available')),
                next((row['id'] for row in artifacts if row.get('available')),
                     artifacts[0]['id'] if artifacts else ''),
            )
        return True

    @property
    def visible_rows(self):
        needle = self.query.strip().casefold()
        return tuple(row for row in self.rows if not needle or needle in
                     ' '.join(str(row.get(key, '')) for key in ('title', 'summary', 'status_text')).casefold())

    @property
    def selected(self):
        return next((row for row in self.visible_rows if row['id'] == self.selected_id), {})

    def set_catalog(self, runtime, rows, *, precision=None):
        if runtime != self.runtime or (precision is not None and precision != self.roformer_precision):
            return False
        self.rows = tuple(dict(row) for row in rows)
        self.rows_runtime = runtime
        self.rows_precision = self.roformer_precision if precision is None else precision
        return True

    def set_query(self, query):
        if query == self.query:
            return False
        self.query = query
        return True

    def select(self, component_id):
        if component_id == self.selected_id or not any(row['id'] == component_id for row in self.visible_rows):
            return False
        self.selected_id = component_id
        return True

    def set_runtime(self, runtime):
        if runtime == 'cuda':
            runtime = 'webgpu'
        if self.active or runtime not in ('cpu', 'webgpu') or self.runtime == runtime:
            return False
        self.runtime = runtime
        return True

    def set_roformer_precision(self, precision):
        if self.active or precision not in ('auto', 'fp16_10s', 'fp16', 'fp32') or self.roformer_precision == precision:
            return False
        self.roformer_precision = precision
        return True

    @property
    def source_options(self):
        return self.selected.get('download_sources', ())

    @property
    def source(self):
        preferred = self.component_sources.get(self.selected_id, 'auto')
        options = self.source_options
        if preferred == 'mirror' and len(options) > 2:
            return options[2][1]  # Old "prefer mirrors" maps to this component's primary station.
        return preferred if any(value == preferred for _, value in options) else 'auto'

    def set_download_source(self, source, *, save=None):
        if self.active or source == self.source or source not in {value for _, value in self.source_options}:
            return False
        preferences = {**self.component_sources, self.selected_id: source}
        if save is not None:
            save(preferences)
        self.component_sources = preferences
        return True

    @property
    def component_plan(self):
        """Separate what preparation chose from the target browsed in the catalog."""
        draft = self.draft_view
        voice = draft['voice_mode']
        if voice == 'import':
            separator = ''
        elif voice == 'roformer' or (voice == 'dual' and draft['separator_model'] == 'roformer'):
            separator = 'roformer'
        else:
            separator = 'builtin'
        required = [separator] if separator else []
        if (draft['target'] != 'separation' and draft['recognize_lyrics']
                and PROFILES[draft['language']].asr_feature == QWEN_FEATURE):
            required.append(QWEN_FEATURE)

        known = self.rows_runtime == self.runtime and bool(self.rows)
        by_id = {row['id']: row for row in self.rows} if known else {}
        ready = {key for key, row in by_id.items() if row['status'] == 'ready'}
        if separator == 'roformer' and known:
            installed = by_id.get('roformer', {}).get('health', {}).get('ready_runtime')
            if installed == 'cuda':
                installed = 'webgpu'
            requested = draft['roformer_device']
            if requested == 'cuda':
                requested = 'webgpu'
            # A WebGPU build can run the CPU path; an explicit WebGPU request needs
            # that build. This is a file check, not a GPU/driver guarantee.
            if installed in ('cpu', 'webgpu') and (requested != 'webgpu' or installed == 'webgpu'):
                ready.add('roformer')
            else:
                ready.discard('roformer')
        return {
            'components': required, 'ready': known,
            'readyIds': [key for key in by_id if key in ready],
            'missingIds': [key for key in required if key not in ready] if known else [],
            'builtinReady': 'builtin' in ready,
        }

    @property
    def component_browser_rows(self):
        plan = self.component_plan
        missing, ready = set(plan['missingIds']), set(plan['readyIds'])
        groups = ('missing', 'ready', 'optional')
        grouped = [{**row, 'component_group': 'missing' if row['id'] in missing else
                    'ready' if row['id'] in ready or row['status'] == 'ready' else 'optional'}
                   for row in self.visible_rows]
        return sorted(grouped, key=lambda row: groups.index(row['component_group']))

    def use_component(self):
        return self._use_separation_row(self.selected)

    def use_builtin_component(self):
        plan = self.component_plan
        if not plan['builtinReady'] or 'roformer' not in plan['missingIds']:
            return False
        return self._use_separation_row(next(row for row in self.rows if row['id'] == 'builtin'))

    def _use_separation_row(self, row):
        if self.active or self.rows_runtime != self.runtime or not row.get('can_use'):
            return False
        model = row.get('model')
        if model not in ('dual', 'roformer'):
            return False
        device = self.runtime if model == 'roformer' else 'auto'
        if (self.draft.voice_mode, self.draft.separator_model, self.draft.roformer_device) != ('dual', model, device):
            self.draft = replace(self.draft, voice_mode='dual', separator_model=model, roformer_device=device,
                                 revision=self.draft.revision + 1)
        self.navigate('processing')
        return True

    def begin(self, action, *, force=False):
        capability = {'install': 'can_install', 'verify': 'can_verify'}.get(action)
        row = self.selected
        if self.active or not capability or self.rows_runtime != self.runtime or not row.get(capability):
            return None
        request = ComponentRequest(
            uuid4().hex, action, row['id'], self.runtime, self.source, bool(force), self.roformer_precision,
        )
        self.active = request
        self.operation = {'state': 'running', 'action': action, 'component': row['title'],
                          'message': '正在准备…', 'done': 0, 'total': 0}
        return request

    def progress(self, operation_id, done, total, message):
        if self.active is None or self.active.operation_id != operation_id:
            return False
        self.operation = {**self.operation, 'done': max(0, int(done or 0)), 'total': max(0, int(total or 0))}
        if self.operation['state'] == 'running':
            self.operation['message'] = str(message)
        return True

    def cancel(self):
        if not self.active or self.operation['state'] != 'running':
            return False
        self.operation = {**self.operation, 'state': 'cancelling', 'message': '正在取消，等待当前操作收尾…'}
        return True

    def complete(self, operation_id, state, message):
        if self.active is None or self.active.operation_id != operation_id:
            return False
        self.active = None
        self.operation = {**self.operation, 'state': state, 'message': str(message)}
        return True
