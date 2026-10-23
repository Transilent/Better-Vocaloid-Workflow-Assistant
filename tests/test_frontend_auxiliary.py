"""Isolated presentation checks for task, component, settings and cache helpers."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['BVWA_TESTING'] = '1'

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

import components_ui
import results_ui
import storage_ui
import updates_ui
from task_contract_ui import TaskFilesDialog
from ui_components import AdaptiveColumns

app = QApplication.instance() or QApplication([])

# The visual radio cards and the original combos must remain one value source.
manifest = {
    'weights': [{'bytes': 1}],
    'shared_wheels': [{'bytes': 1}],
    'torch_wheels': {'cuda': {'bytes': 1}, 'cpu': {'bytes': 1}},
    'install_bytes': {'cuda': 1, 'cpu': 1},
}
owner = SimpleNamespace(
    runner=None, processing_active=False,
    storage=SimpleNamespace(busy=lambda: False),
    update_component_state=lambda: None,
)
with patch.object(components_ui.components, 'manifest', return_value=manifest), \
        patch.object(components_ui.components, 'status', return_value={'ready': False, 'runtime': None}), \
        patch('dependency_profiles.status', return_value={'chinese': {'ready': False, 'bytes': 1}}), \
        patch('expression_component.status', return_value={'ready': False}), \
        patch('expression_component.SPEC', {'bytes': 1}):
    components = components_ui.ComponentsPage(owner)
    assert components.runtime.isHidden() and not components.source.isHidden()
    assert len(components.runtime_choice.buttons) == 2
    components.runtime_choice.buttons[1].click()
    app.processEvents()
    assert components.runtime.currentData() == 'cpu'
    components.source.setCurrentIndex(components.source.findData('mirror'))
    app.processEvents()
    assert components.source.currentData() == 'mirror'
    components.close()

# Results uses one adaptive list/detail container, and long actions wrap by width.
result_owner = SimpleNamespace(
    processing_active=False, job=None,
    open_folder=lambda: None, resume_last=lambda: None, reprocess_last=lambda: None,
)
with patch.object(results_ui.ResultsPage, 'reload', lambda self: None):
    results = results_ui.ResultsPage(result_owner)
assert isinstance(results.columns, AdaptiveColumns)
assert results.columns.breakpoint == 940 and results.columns.ratio == (3, 2)
assert results.search.isClearButtonEnabled()

host = QWidget()
column = QVBoxLayout(host)
actions = results_ui.FlowLayout(spacing=10)
for text in ('打开结果目录', '继续 / 重试', '用当前工作流重做', '查看进度与错误', '文件与下一步', '管理任务'):
    actions.addWidget(QPushButton(text))
column.addLayout(actions)
wide_height = actions.heightForWidth(1200)
narrow_height = actions.heightForWidth(360)
assert narrow_height > wide_height, (wide_height, narrow_height)
results.close()
host.close()

# The task-file dialog remains scrollable and can shrink below the old fixed size.
dialog_owner = QWidget()
with patch.object(TaskFilesDialog, 'refresh', lambda self: None):
    dialog = TaskFilesDialog(dialog_owner, Path('.'))
assert dialog.minimumWidth() == 620 and dialog.minimumHeight() == 520
assert dialog.findChild(QScrollArea) is not None
assert dialog.tree.wordWrap() and dialog.tree.minimumHeight() == 270
dialog.close()
dialog_owner.close()

# Cache summary follows actual checkbox state and protected rows stay disabled.
cache_owner = SimpleNamespace()
cache = storage_ui.StoragePage(cache_owner, compact=True)
cache.ready({
    'rows': [
        {'title': '可再生成缓存', 'bytes': 10, 'path': str(APP / 'cache/demo')},
        {'title': '正在使用', 'bytes': 20, 'path': str(APP / 'cache/busy'),
         'protected': '任务处理中'},
    ],
    'free': 1000, 'tasks': 100, 'publication': 10, 'dependencies': 200,
})
selectable = cache.tree.topLevelItem(0)
protected = cache.tree.topLevelItem(1)
assert selectable.flags() & Qt.ItemIsUserCheckable
assert not (protected.flags() & Qt.ItemIsEnabled)
selectable.setCheckState(0, Qt.Checked)
app.processEvents()
assert '已选择 1 项' in cache.selection.text()
cache.close()

# Settings keeps combos as value owners while grouped cards expose compact choices.
settings_owner = QWidget()
settings_owner.components = SimpleNamespace(source=QComboBox())
with patch.object(updates_ui.updater, 'pending_update', return_value=None):
    settings = updates_ui.UpdatesPage(settings_owner)
assert not settings.source.isHidden() and settings.download_workers.isHidden()
assert settings.source.findData('gh-proxy') >= 0 and settings.source.findData('ghfast') >= 0
assert settings.source.findData('hf-mirror') >= 0
assert len(settings.workers_choice.buttons) == 4
assert hasattr(settings, 'body_layout')
settings.close()
settings_owner.close()

app.processEvents()
print('PASS: AUX adaptive layout, combo-card binding, wrapping actions, scrollable dialog and cache summary')
