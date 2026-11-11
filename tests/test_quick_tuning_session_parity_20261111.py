"""Synthetic acceptance for Quick tuning resume, location, task and keyboard parity."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

from quick_runtime import configure_quick_runtime

QT6_ROOT = os.environ.get('VWA_TEST_QT6_ROOT')
assert QT6_ROOT, 'Run this test with the verified isolated Qt 6 runtime'
configure_quick_runtime(APP, QT6_ROOT, software=True)

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from common import atomic_json, file_hash
import quick_results
import quick_support
import quick_tuning as quick_tuning_module
import task_store
import tuning_workflow as workflow
from quick_tuning import QuickTuning
from quick_work import WorkerLane


assert os.environ.get('VWA_TESTING') == '1', 'The test must run against isolated test state'
Q = 705600000
app = QGuiApplication([])


def score():
    return {
        'version': '1.0.0', 'uuid': 'quick-tuning-parity-fixture',
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
                 'tempo': [{'position': 0, 'bpm': 120}]},
        'tracks': [
            {'name': 'Lead Vocal',
             'mainRef': {'blickOffset': 0, 'isInstrumental': False,
                         'database': {'name': 'Synthetic Bank'}, 'voice': {'preset': 'fixture'}},
             'mainGroup': {'uuid': 'lead-group', 'parameters': {}, 'notes': [
                 {'onset': 0, 'duration': 2 * Q, 'pitch': 60, 'lyrics': 'a'},
                 {'onset': 2 * Q, 'duration': Q, 'pitch': 64, 'lyrics': 'b'},
             ]}},
            {'name': 'Instrumental',
             'mainRef': {'isInstrumental': True, 'audio': {'filename': 'media/accompaniment.wav'}},
             'mainGroup': {'notes': [], 'parameters': {}}},
        ],
    }


def write_score(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(score(), ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\x00')
    return path


def wait(label, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate(), label
    print('PASS', label, flush=True)


def finished(tune):
    return not tune.worker.kind


class FakeTaskBridge(QObject):
    taskChanged = Signal()

    def __init__(self):
        super().__init__()
        self.taskModel = SimpleNamespace(rows=())
        self.selectedTaskId = ''
        self.taskDetail = {}
        self.detail = {}
        self.calls = []

    def refreshTasks(self):
        self.calls.append(('refresh',))

    def selectTask(self, job):
        self.calls.append(('select', job))
        self.selectedTaskId = job
        self.taskDetail = self.detail or {'jobId': job, 'artifacts': []}
        self.taskChanged.emit()

    def tuneArtifact(self, artifact_id):
        self.calls.append(('tune', artifact_id))


with tempfile.TemporaryDirectory(prefix='quick-tuning-parity-', dir=APP / 'work') as directory:
    root = Path(directory)
    media = root / 'media/accompaniment.wav'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'synthetic accompaniment')
    source = write_score(root / 'source.svp')
    options = {'track_indices': [0], 'expression_density': 'original',
               'performance_style': 'soft_ballad', 'style_strength': .42}

    valid_session = workflow.create_session(source, root / 'arbitrary/valid')
    valid_result = workflow.run_session(valid_session, options)
    assert valid_result['receipt_verified'] is True
    valid_output = Path(valid_result['output'])
    valid_bytes = valid_output.read_bytes()

    tune = QuickTuning(None, WorkerLane(None, 'quick-tuning-resume'), root=root / 'state-resume')
    tune.resumeSession(QUrl.fromLocalFile(str(valid_session)))
    wait('arbitrary existing session resumes', lambda: tune.project is not None and finished(tune))
    assert Path(tune.session) == valid_session.resolve()
    assert tune.view['styleStrength'] == .42
    assert set(tune.view['selectedTracks'])
    assert tune.view['candidateVerified'] is True
    assert Path(tune.view['candidate']) == valid_output
    assert valid_output.read_bytes() == valid_bytes

    stale_session = workflow.create_session(source, root / 'arbitrary/stale-output')
    stale_result = workflow.run_session(stale_session, options)
    stale_output = Path(stale_result['output'])
    stale_output.write_bytes(stale_output.read_bytes() + b' ')
    stale_bytes = stale_output.read_bytes()
    historical = QuickTuning(None, WorkerLane(None, 'quick-tuning-historical'), root=root / 'state-historical')
    historical.resumeSession(QUrl.fromLocalFile(str(stale_session)))
    wait('changed output remains visible as historical', lambda: historical.project is not None and finished(historical))
    assert historical.view['candidateHistorical'] is True
    assert historical.view['candidateVerified'] is False
    assert historical.view['canRegister'] is False
    assert Path(historical.view['candidate']) == stale_output and stale_output.read_bytes() == stale_bytes
    checked = historical._verify_historical_candidate(threading.Event())
    assert Path(checked['output']) == stale_output
    assert Path(checked['receipt']).is_file()

    broken_session = workflow.create_session(source, root / 'arbitrary/broken-snapshot')
    (broken_session.parent / 'inputs/source.svp').write_bytes(b'changed snapshot')
    historical.resumeSession(QUrl.fromLocalFile(str(broken_session)))
    wait('tampered session snapshot is rejected', lambda: historical.state == 'failed' and finished(historical))
    assert historical.view['candidateHistorical'] is True
    assert Path(historical.view['candidate']) == stale_output

    location_source = root / 'location-source.svp'
    location_source.write_bytes(source.read_bytes())
    location_tune = QuickTuning(None, WorkerLane(None, 'quick-tuning-location'), root=root / 'state-location')
    location_tune.chooseProject(QUrl.fromLocalFile(str(location_source)))
    wait('source loads before location selection', lambda: location_tune.project is not None and finished(location_tune))
    assert location_tune.view['canChooseSessionLocation'] is True
    chosen = root / 'chosen-session-parent'
    chosen.mkdir()
    location_tune.setSessionLocation(QUrl.fromLocalFile(str(chosen)))
    assert Path(location_tune.session).is_relative_to(chosen)
    before_draft = location_tune._drafts.read(location_tune.project.sha256)
    location_tune.saveDraftRevision()
    wait('explicit shared draft revision saves', lambda: location_tune._draft_revision is not None and finished(location_tune))
    first_revision = location_tune._drafts.read(location_tune.project.sha256)['revision']
    location_tune.setStyleStrength(.55)
    wait('changed draft content autosaves', lambda: not location_tune._dirty and finished(location_tune))
    location_tune.saveDraftRevision()
    wait('a second explicit shared draft revision saves', lambda: finished(location_tune)
         and location_tune._drafts.read(location_tune.project.sha256)['revision'] != first_revision)
    assert before_draft['revision'] != first_revision
    location_tune.reloadLatestDraft()
    wait('latest shared draft reloads', lambda: location_tune.state == 'ready' and finished(location_tune))

    legacy_source = root / 'legacy-preferences.svp'
    legacy_source.write_bytes(source.read_bytes())
    legacy_state = {'tuning': {'preferences': {
        'performance_style': 'sad', 'style_strength': .63,
        'style_detail': {'onset': .44}, 'style_pitch': True,
        'dynamics_strength': .74, 'expression_density': 'dense',
        'reference_dynamics': True, 'reference_sources': [{'track_index': 9, 'path': 'private.wav'}],
        'reference_calibration': 'private/calibration.json', 'retained_project': 'private/old.svp',
        'track_indices': [42], 'style_scope': 'custom', 'unknown_future_key': 'ignored'}}}
    legacy_tune = QuickTuning(None, WorkerLane(None, 'quick-tuning-legacy'), root=root / 'state-legacy')
    with patch.object(quick_tuning_module, 'workspace_state', return_value=legacy_state):
        legacy_tune.chooseProject(QUrl.fromLocalFile(str(legacy_source)))
        wait('portable legacy preferences apply to a new project', lambda: legacy_tune.project is not None and finished(legacy_tune))
    assert legacy_tune.view['styleStrength'] == .63
    assert legacy_tune.view['dynamics_strength'] == .74
    assert legacy_tune.view['expression_density'] == 'dense'
    assert legacy_tune.view['expressionMode'] == 'preserve'
    assert legacy_tune._options['reference_sources'] == []
    assert legacy_tune._options['retained_sources'] == []
    assert legacy_tune._options['reference_calibration'] is None
    assert legacy_tune._options['retained_project'] is None
    assert legacy_tune._options['track_indices'] is None
    assert '可移植调音偏好' in legacy_tune.view['message']

    draft_tune = QuickTuning(None, WorkerLane(None, 'quick-tuning-draft-priority'), root=root / 'state-location')
    with patch.object(quick_tuning_module, 'workspace_state', return_value=legacy_state):
        draft_tune.chooseProject(QUrl.fromLocalFile(str(location_source)))
        wait('an existing draft takes priority over legacy defaults', lambda: draft_tune.project is not None and finished(draft_tune))
    assert draft_tune.view['styleStrength'] == .55
    assert draft_tune.view['style'] != 'sad'

    location_tune.setStyle('gentle')
    location_tune.selectSpan(0, 2)
    location_tune.addRegion()
    wait('synthetic region is selected for keyboard deletion', lambda: bool(location_tune.view['selectedRegion']))
    location_tune.removeSelectedRegion()
    assert not location_tune.regions
    location_tune.undoRegions()
    assert location_tune.regions
    location_tune.clearSelection()
    assert location_tune.view['start'] == location_tune.view['end']

    location_tune.freeze()
    wait('freeze completes before first generation', lambda: location_tune._plan is not None and finished(location_tune))
    location_tune.run()
    wait('first generation creates the chosen standalone session', lambda: location_tune.candidate is not None and finished(location_tune))
    created_session = Path(location_tune.session)
    created_output = Path(location_tune.view['candidate'])
    assert created_session.is_file() and created_session.is_relative_to(chosen)
    assert created_output.is_file() and created_output.is_relative_to(created_session.parent / 'outputs')

    old_sha = location_tune.project.sha256
    location_source.write_bytes(location_source.read_bytes() + b' ')
    location_tune.reloadProject()
    wait('stale source reload is rejected', lambda: location_tune.state == 'failed' and finished(location_tune))
    assert location_tune.project.sha256 == old_sha
    assert Path(location_tune.view['candidate']) == created_output and created_output.is_file()

    task_source = root / 'task-project.svp'
    task_source.write_bytes(source.read_bytes())
    job = root / 'jobs/quick-tuning-task-fixture'
    job.mkdir(parents=True)
    atomic_json(job / 'request.json', {'source_title': 'Synthetic tuning task'})
    atomic_json(job / 'status.json', {'state': 'done'})
    atomic_json(job / 'task.json', {'name': 'Synthetic tuning task'})
    picker = QuickTuning(None, WorkerLane(None, 'quick-tuning-task-picker'), root=root / 'state-task')
    with patch.object(task_store, 'job_directories', return_value=[job]):
        project_id = quick_results.register_external(
            job, 'project', task_source, expected_binding_id='', expected_binding_revision='')
        task_detail = quick_results.detail(job)
        picker.refreshTuningTasks()
        wait('direct task list loads', lambda: len(picker.tuningTasks) == 1 and finished(picker))
        wait('task project artifacts verify', lambda: len(picker.tuningTaskProjects) == 1 and finished(picker))
        task = picker.tuningTaskProjects[0]
        assert task['artifactId'] == project_id
        picker.loadTuningTaskArtifact(picker.tuningTasks[0]['jobId'], task['artifactId'])
        wait('direct task project loads after reverification', lambda: picker.project is not None and finished(picker))
    assert Path(picker.job) == job.resolve()
    assert picker.project.sha256 == file_hash(task_source)

    bridge = FakeTaskBridge()
    bridge.taskModel.rows = ({'jobId': str(job.resolve()), 'title': 'Synthetic tuning task',
                              'statusText': 'done'},)
    bridge.selectedTaskId = str(job.resolve())
    bridge.detail = bridge.taskDetail = task_detail
    routed = QuickTuning(bridge, WorkerLane(None, 'quick-tuning-bridge-bridge'), root=root / 'state-bridge')
    routed.refreshTuningTasks()
    routed.selectTuningTask(str(job.resolve()))
    artifact_id = next(row['artifactId'] for row in routed.tuningTaskProjects
                       if row['artifactId'] == project_id)
    routed.loadTuningTaskArtifact(str(job.resolve()), artifact_id)
    assert bridge.calls[:1] == [('refresh',)]
    assert ('select', str(job.resolve())) in bridge.calls
    assert bridge.calls[-1] == ('tune', project_id)
    print('PASS task picker delegates to the existing Results controller', flush=True)

    qml_path = APP / 'src/quick_ui/TuningPage.qml'
    qml = qml_path.read_text(encoding='utf-8')
    for marker in ('tuningResumeSession', 'tuningReloadProject', 'tuningChooseSessionLocation',
                   'tuningEditorFocus', 'tuningOpenTask', 'reloadLatestTuningDraft',
                   'saveTuningDraftRevision', 'tuningUndoShortcut', 'tuningRedoShortcut',
                   'tuningRedoShiftShortcut', 'tuningDeleteRegionShortcut', 'tuningEscapeShortcut'):
        assert marker in qml, marker
    assert 'sequence: "F11"' not in qml
    assert 'showFullScreen' not in qml
    assert 'Ctrl+Z' in qml and 'Ctrl+Y' in qml and 'Ctrl+Shift+Z' in qml
    assert 'focusBlocksEditorShortcut' in qml
    assert 'page.Window.activeFocusItem' in qml
    assert 'TextInput' in qml and 'SpinBox' in qml and 'ComboBox' in qml
    main_qml = (APP / 'src/quick_ui/Main.qml').read_text(encoding='utf-8')
    window_modes = (APP / 'src/quick_window_modes.py').read_text(encoding='utf-8')
    assert 'Qt.WindowMaximizeButtonHint' in main_qml
    assert 'native maximize, restore and move' in window_modes
    for kind in ('tuning-loading-session', 'tuning-draft-reload', 'tuning-draft-save',
                 'tuning-task-picker-scan', 'tuning-task-picker-detail', 'tuning-task-picker-load'):
        assert quick_support._operation_label(kind) == '调音'
    print('PASS session and task worker kinds are tracked as active tuning operations', flush=True)

    qml_engine = QQmlEngine()
    qml_engine.addImportPath(str(APP / 'src/quick_ui'))
    component = QQmlComponent(qml_engine, QUrl.fromLocalFile(str(qml_path)))
    if component.status() != QQmlComponent.Ready:
        raise AssertionError('\n'.join(error.toString() for error in component.errors()))
    print('PASS tuning page compiles for an isolated normal view', flush=True)

print('PASS Quick tuning session resume, historical output, location, drafts, shortcuts and task picker')
