"""Restart/input recovery, task states, disabled CLI stages and progress I/O."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ['BVWA_TESTING'] = '1'
from PyQt5.QtWidgets import QApplication, QMessageBox
from app import Window
import common, workflow, pipeline, progress_state, task_store
import workspace_ui, components_ui, updates_ui, processing_workflow

application = QApplication([])
config = common.load_config()
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='review-fixes-') as folder:
    root = Path(folder)
    long_root = root / 'extended-path-case'
    long_file = long_root / ('deep-' * 12) / ('nested-' * 10) / ('folder-' * 10) / 'state.json'
    assert len(str(long_file)) > 300
    try:
        common.atomic_json(long_file, {'message': '长路径校验'})
        raw = Path(common.native_path(long_file)).read_bytes()
        assert json.loads(raw)['message'] == '长路径校验'
        assert common.file_hash(long_file) == hashlib.sha256(raw).hexdigest()
    finally:
        assert long_root.resolve().is_relative_to(root.resolve())
        shutil.rmtree(common.native_path(long_root))
    common.atomic_json(root / 'config.json', config)
    with patch.object(common, 'ROOT', root), patch.object(workflow, 'ROOT', root), patch.object(task_store, 'ROOT', root):
        workflow.save_state(workflow.default_graph(), dict(workflow.DEFAULT_OPTIONS, reference_mode='netease'), {})
        window = Window()
        window.show(); application.processEvents()
        assert window.reference_mode.currentData() == 'local'
        assert '网易云' in window.local_reference_status.text()
        assert window.options()['reference_mode'] == 'local'
        window.reference_mode.setCurrentIndex(window.reference_mode.findData('netease'))
        window.step_cards['reference'].toggle.setChecked(False)
        with patch.object(QMessageBox, 'warning') as warning, patch.object(window, 'launch') as launch:
            window.start_new(); application.processEvents()
            assert not warning.called and not launch.called
            assert window.step_cards['reference'].toggle.isChecked()
            assert '选择网易云歌曲' in window.status.text()
        window.reference_mode.setCurrentIndex(window.reference_mode.findData('local'))
        job = pipeline.create_job('BV1000000000', parent=root / 'jobs', project_formats=[], remember=False)
        for until in ('exports', 'unknown-stage'):
            try:
                pipeline.run(job, until=until)
                raise AssertionError('Unavailable terminal accepted')
            except ValueError as error:
                assert until in str(error)
        result = subprocess.run([sys.executable, '-B', str(APP / 'pipeline.py'), '--job', str(job), '--until', 'exports'],
                                capture_output=True, timeout=30)
        assert result.returncode == 1
        assert '未启用 exports' in result.stderr.decode('utf-8')
        assert b'Traceback' not in result.stderr and b'StopIteration' not in result.stderr
        common.atomic_json(job / 'status.json', {'state': 'running', 'current_step': 'midi', 'run_started': 100.,
                                                'planned_steps': ['midi'], 'steps': {}})
        with patch.object(task_store, 'locked', return_value=True), patch.object(window, 'show_failure') as failed:
            window.error_panel.show()
            window.show_job(job); application.processEvents()
            failed.assert_not_called()
            assert not window.error_panel.isVisible() and '正在运行' in window.status.text()
        with patch.object(task_store, 'locked', return_value=False), patch.object(window, 'show_failure') as failed:
            window.show_job(job)
            failed.assert_called_once()
        common.atomic_json(job / 'status.json', {'state': 'failed', 'error': 'fixture', 'steps': {}})
        with patch.object(window, 'show_failure') as failed:
            window.show_job(job)
            failed.assert_called_once()
        window.close(); application.processEvents()

    # 501 log events in half a second should retain the terminal count while
    # writing at most a handful of progress files. Phase/voice changes are immediate.
    now, writes = [1000.], []
    real_write = progress_state.atomic_json
    def counted(path, data):
        writes.append(dict(data)); real_write(path, data)
    progress_job = root / 'progress'; progress_job.mkdir()
    with patch.object(progress_state.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(progress_state.time, 'time', side_effect=lambda: now[0]), \
         patch.object(progress_state, 'atomic_json', side_effect=counted):
        for i in range(501):
            now[0] = 1000. + i / 1000
            progress_state.from_log(progress_job, f'ASR batch {i}/500 done', '主唱')
        assert len(writes) <= 5 and writes[-1]['done'] == 500
        before = len(writes)
        progress_state.publish(progress_job, 'midi', '和声 · 加载模型')
        assert len(writes) == before + 1
        progress_state.publish(progress_job, 'exports', '导出工程')
        assert len(writes) == before + 2

    common.atomic_json(progress_job / 'status.json', {'state': 'running', 'current_step': 'midi',
        'run_started': 100., 'elapsed_seconds': 7., 'planned_steps': ['midi'], 'steps': {}})
    common.atomic_json(progress_job / 'progress.json', {'stage': 'midi', 'updated': 99999., 'detail': 'pitch'})
    os.utime(progress_job / 'status.json', (150., 150.))
    os.utime(progress_job / 'progress.json', (160., 160.))
    with patch.object(task_store, 'locked', return_value=False), patch.object(progress_state.time, 'time', return_value=1000.):
        snapshot = progress_state.snapshot(progress_job)
        assert snapshot['status']['state'] == 'interrupted' and snapshot['elapsed'] == 67
    with patch.object(task_store, 'locked', return_value=False), patch.object(progress_state.time, 'time', return_value=2000.):
        assert progress_state.snapshot(progress_job)['elapsed'] == 67
print('PASS: long-path atomic state/hash, online-reference recovery, CLI bounds, live/stale/failed tasks, throttled terminal progress and bounded interrupted elapsed')
