"""Regression for cross-page ownership and operation identity."""
from pathlib import Path
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from workspace_controller import WorkspaceController

controller = WorkspaceController([('自动', 'auto'), ('直连', 'direct')])
rows = [
    {'id': 'builtin', 'title': 'Karaoke 2', 'summary': '', 'status_text': '已就绪',
     'model': 'dual', 'can_use': True, 'can_install': False, 'can_verify': False},
    {'id': 'roformer', 'title': 'BS-RoFormer', 'summary': '', 'status_text': '已就绪',
     'model': 'roformer', 'can_use': True, 'can_install': True, 'can_verify': True},
]
with tempfile.TemporaryDirectory(dir=APP / 'work') as folder:
    music = Path(folder) / '素材 example.wav'
    music.write_bytes(b'test source')
    assert controller.set_music(str(music))
    draft = controller.draft
    assert controller.navigate('components') and controller.draft == draft
    assert controller.set_catalog('cpu', rows)
    assert controller.set_query('roformer')
    assert controller.select('roformer')
    assert controller.use_component()
    assert controller.route == 'processing'
    assert controller.draft.music_path == str(music)
    assert controller.draft.separator_model == 'roformer'
    assert controller.draft.roformer_device == 'cpu'
    assert controller.draft.revision == draft.revision + 1
    assert controller.navigate('components')
    assert controller.query == 'roformer' and controller.selected_id == 'roformer'
    assert controller.set_runtime('cuda')
    assert not controller.use_component()
    assert controller.begin('install') is None
    assert not controller.set_catalog('cpu', rows)
    assert controller.set_catalog('cuda', rows)
    assert controller.set_download_source('direct')
    install = controller.begin('install', force=True)
    assert (install.component_id, install.runtime, install.source, install.force) == ('roformer', 'cuda', 'direct', True)
    assert not controller.set_runtime('cpu')
    assert controller.return_to_preparation()
    assert controller.active == install
    assert controller.progress(install.operation_id, 1, 3, '下载')
    assert controller.cancel()
    assert controller.progress(install.operation_id, 2, 3, '迟到的进度')
    assert controller.operation['state'] == 'cancelling'
    assert '取消' in controller.operation['message']
    assert not controller.complete('old-id', 'succeeded', '旧操作')
    assert controller.complete(install.operation_id, 'cancelled', '已取消')
    assert controller.active is None and controller.draft.music_path == str(music)
    assert controller.navigate('components')
    retry = controller.begin('verify')
    assert retry.operation_id != install.operation_id
    assert not controller.progress(install.operation_id, 3, 3, '旧操作')
    assert not controller.complete(install.operation_id, 'succeeded', '旧操作')
    assert controller.complete(retry.operation_id, 'succeeded', '校验通过')
assert not any(name == 'PyQt5' or name == 'PySide6' for name in sys.modules)
print('PASS: draft and selection across navigation, frozen commands, cancel, retry and stale event rejection')
