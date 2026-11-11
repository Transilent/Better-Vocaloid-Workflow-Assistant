"""Own the preparation draft, component browser and cross-page commands."""

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from workspace_routes import PAGE_SPECS
from preparation_commands import PreparationDraft


@dataclass(frozen=True)
class ComponentRequest:
    operation_id: str
    action: str
    component_id: str
    runtime: str
    source: str
    force: bool = False


class WorkspaceController:
    """All mutable state belongs to the application, independent of a page instance."""

    available_routes = ('processing', 'components', 'results', 'updates')

    def __init__(self, sources):
        self.draft = PreparationDraft()
        self.route = 'processing'
        self.return_route = 'processing'
        self.runtime = 'cpu'
        self.source_options = tuple(sources)
        self.source = next((value for _, value in self.source_options if value == 'auto'),
                           self.source_options[0][1])
        self.query = ''
        self.selected_id = 'builtin'
        self.rows = ()
        self.rows_runtime = None
        self.active = None
        self.operation = {'state': 'idle', 'action': '', 'message': '', 'done': 0, 'total': 0}
        self.preparation_task = {'id': '', 'state': 'idle', 'job': '', 'message': '',
                                 'current': '', 'completed': 0, 'total': 0, 'elapsed': 0,
                                 'can_retry': False, 'recovery_action': ''}
        self.task_rows = ()
        self.task_query = ''
        self.selected_task_id = ''
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

    def select_task(self, job_id):
        if not any(row['jobId'] == job_id for row in self.task_rows):
            return False
        self.selected_task_id = job_id
        self.task_detail = {}
        return True

    def set_task_detail(self, job_id, detail):
        if self.selected_task_id != job_id:
            return False
        self.task_detail = detail
        return True

    @property
    def visible_rows(self):
        needle = self.query.strip().casefold()
        return tuple(row for row in self.rows if not needle or needle in
                     ' '.join(str(row.get(key, '')) for key in ('title', 'summary', 'status_text')).casefold())

    @property
    def selected(self):
        return next((row for row in self.visible_rows if row['id'] == self.selected_id), {})

    def set_catalog(self, runtime, rows):
        if runtime != self.runtime:
            return False
        self.rows = tuple(dict(row) for row in rows)
        self.rows_runtime = runtime
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
        if self.active or runtime not in ('cpu', 'cuda') or self.runtime == runtime:
            return False
        self.runtime = runtime
        return True

    def set_download_source(self, source):
        if self.active or source == self.source or source not in {value for _, value in self.source_options}:
            return False
        self.source = source
        return True

    def use_component(self):
        row = self.selected
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
        request = ComponentRequest(uuid4().hex, action, row['id'], self.runtime, self.source, bool(force))
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
