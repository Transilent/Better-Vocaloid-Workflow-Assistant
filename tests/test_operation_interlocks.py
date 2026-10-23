"""Synthetic checks for browser/cache, update and source-task interlocks."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['BVWA_TESTING'] = '1'
from PyQt5.QtWidgets import QApplication
import publishing_ui
import results_ui
import storage_ui
import task_store
import updates_ui

application = QApplication.instance() or QApplication([])


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class Text:
    def __init__(self, value):
        self.value = value

    def text(self):
        return str(self.value)


def busy(value):
    return lambda: value


def write_session(path, project, reference, renders):
    outputs = {name: 'BVWA_' + name for name in
               ('neutral', 'loudness', 'tension', 'breathiness', 'voicing', 'gender', 'toneShift')}
    spec = {'schema': 1, 'project': str(project), 'reference': str(reference),
            'project_sha256': '0' * 64, 'reference_sha256': '1' * 64, 'score_sha256': '2' * 64,
            'voice': 'lead', 'track_index': 0, 'minimum_duration': 1.0, 'strength': 0.6,
            'outputs': outputs, 'render_folder': str(renders)}
    path.write_text(json.dumps(spec), encoding='utf-8')
    return path


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='operation-interlocks-') as directory:
    root = Path(directory)
    cache = root / 'cache/publish-browser'
    cache.mkdir(parents=True)

    # Browser helpers and profile leases are both part of publishing activity.
    with patch.object(publishing_ui, 'ROOT', root), patch.object(task_store, 'locked', return_value=False):
        tab = SimpleNamespace(processes={'bilibili': FakeProcess()})
        assert publishing_ui.PublishingTab.live_browser_platforms(tab) == ('bilibili',)
        tab.live_browser_platforms = lambda: publishing_ui.PublishingTab.live_browser_platforms(tab)
        assert publishing_ui.PublishingTab.live_browser_active(tab)
        tab.processes = {'bilibili': FakeProcess(0)}
        assert publishing_ui.PublishingTab.live_browser_platforms(tab) == ()
    with patch.object(publishing_ui, 'ROOT', root), \
         patch.object(task_store, 'locked', side_effect=lambda path: path.name == 'xiaohongshu'):
        tab = SimpleNamespace(processes={})
        assert publishing_ui.PublishingTab.live_browser_platforms(tab) == ('xiaohongshu',)

    # Both login and upload entrypoints reject while storage owns the shared cache.
    owner = SimpleNamespace(storage=SimpleNamespace(busy=busy(True)))
    launch = SimpleNamespace(owner=owner, status=Mock(), processes={},
                             browser_location=Mock(side_effect=AssertionError('browser probe must not run')))
    with patch.object(publishing_ui.QMessageBox, 'warning') as warning:
        publishing_ui.PublishingTab.launch_browser(launch, 'bilibili', login_only=True)
    warning.assert_called_once()
    assert '缓存' in launch.status.setText.call_args.args[0]
    upload = SimpleNamespace(owner=owner, status=Mock(), busy=busy(False), content=lambda: {'bilibili': {}},
        bundle=object(), bundle_inputs=('same',), input_snapshot=lambda: ('same',), launch_browser=Mock())
    with patch.object(publishing_ui.QMessageBox, 'warning') as warning:
        publishing_ui.PublishingTab.upload_selected(upload)
    warning.assert_called_once()
    upload.launch_browser.assert_not_called()

    # Storage polling notices a live login/upload child even after publishing preparation ended.
    polled = []
    publishing = SimpleNamespace(busy=busy(False), poll_browser=lambda: polled.append(True),
                                 live_browser_active=busy(True))
    owner = SimpleNamespace(processing_active=False, components=SimpleNamespace(busy=busy(False)),
        updates=SimpleNamespace(busy=busy(False)), publishing=publishing,
        video_mixdown=SimpleNamespace(busy=busy(False)), calibration_dialog=None)
    with patch.object(storage_ui, 'StorageWorker') as worker:
        page = storage_ui.StoragePage(owner, compact=True)
        page.start([root / 'cache/browser-temp'])
        assert polled == [True]
        assert page.worker is None
        worker.assert_not_called()
        assert '浏览器' in page.summary.text()
        page.close()

    # Update gating includes a child browser while preserving the same startup/manual gate.
    polled = []
    owner = SimpleNamespace(processing_active=False, components=SimpleNamespace(busy=busy(False)),
        publishing=SimpleNamespace(busy=busy(False), poll_browser=lambda: polled.append(True),
                                   live_browser_active=busy(True)),
        storage=SimpleNamespace(busy=busy(False)), video_mixdown=SimpleNamespace(busy=busy(False)),
        calibration_dialog=None)
    update_page = SimpleNamespace(owner=owner, busy=busy(False))
    assert updates_ui.UpdatesPage.active_operations(update_page)
    assert polled == [True]
    manual = SimpleNamespace(plan=str(root / 'plan.json'), owner=SimpleNamespace(workflow=Mock()),
                             active_operations=busy(True), message=Mock())
    with patch.object(updates_ui.updater, 'restart_to_apply') as restarting:
        updates_ui.UpdatesPage.primary_action(manual)
    restarting.assert_not_called()
    assert '浏览器' in manual.message.setText.call_args.args[0]

    # Destructive task management follows the worker's captured inputs, not later UI text.
    job = root / 'jobs/current'; other = root / 'jobs/other'
    job.mkdir(parents=True); other.mkdir()
    video_b = {'video': str(other / 'source.mp4'), 'mixdown': str(other / 'mix.wav'),
               'output': str(other / 'out.mp4')}
    video = SimpleNamespace(busy=busy(True), source_job=other,
                            active_inputs={'source_job': job, 'video': job / 'video/source.mp4',
                                           'mixdown': job / 'audio/lead.wav', 'output': None},
                            fields=video_b)
    calibration = SimpleNamespace(busy=busy(False), session=None, job_context=lambda: None)
    owner = SimpleNamespace(video_mixdown=video, calibration_dialog=calibration)
    assert results_ui.active_task_operations(owner, job) == ('视频合成',)
    assert results_ui.active_task_operations(owner, other) == ()
    video.active_inputs = {}
    assert results_ui.active_task_operations(owner, other) == ('视频合成',)
    assert results_ui.active_task_operations(owner, job) == ()

    video.busy = busy(False)
    calibration.busy = busy(True)
    fixed_b = write_session(root / 'calibration-b.json', other / 'exports/voices.svp',
                            other / 'audio/lead.wav', other / 'renders')
    calibration.project = Text(job / 'exports/other.svp')
    calibration.reference = Text(job / 'audio/other.wav')
    calibration.renders = Text(job / 'renders')
    calibration.session = fixed_b
    calibration.job_context = lambda: job
    assert results_ui.active_task_operations(owner, job) == ()
    assert results_ui.active_task_operations(owner, other) == ('校准',)

    fixed_a = write_session(root / 'calibration-a.json', job / 'exports/voices.svp',
                            job / 'audio/lead.wav', job / 'renders')
    calibration.session = fixed_a
    assert results_ui.active_task_operations(owner, job) == ('校准',)
    calibration.session = root / 'missing-session.json'
    assert results_ui.active_task_operations(owner, job) == ('校准',)
    assert results_ui.active_task_operations(owner, other) == ('校准',)
    calibration.session = None
    calibration.project = Text(job / 'exports/voices.svp')
    calibration.reference = Text(job / 'audio/lead.wav')
    calibration.renders = Text(job / 'renders')
    calibration.job_context = lambda: None
    assert results_ui.active_task_operations(owner, job) == ('校准',)
    assert '此操作无法撤销' in (APP / 'src/results_ui.py').read_text(encoding='utf-8')

print('PASS: synthetic browser/cache, update and source-task interlock probes')
