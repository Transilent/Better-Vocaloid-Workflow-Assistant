"""Offline support/recovery parity checks for the Quick-only settings surface."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
os.environ['VWA_TESTING'] = '1'
os.environ['BVWA_TESTING'] = '1'
import sys
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from workspace_settings import SettingsState, load_update_recovery


def write_json(path, value, *, padding=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + (' ' * padding), encoding='utf-8')


def make_state(root):
    state = SettingsState.__new__(SettingsState)
    state.values = {
        'ui_language': 'zh-CN', 'ui_scale': 100, 'check_for_updates': True,
        'auto_restart_updates': True, 'download_source': 'auto', 'download_workers': 4,
    }
    state.preferences = {}
    state.release = {}
    state.update_plan = None
    state.pending_version = ''
    state.pending_notes = ''
    state.last_update_result = {}
    state.restart_pending = False
    state.auto_restart_active = False
    state.restart_deferred = False
    state.restart_remaining = None
    state.operation_blockers = ()
    state.update = {'update_open': False, 'update_state': 'idle', 'message': '', 'done': 0, 'total': 0}
    state.tour_open = False
    state.tour_step = 0
    state.installed_quick_frontend = lambda: True
    state.root = Path(root)
    return state


def recovery_checks():
    with tempfile.TemporaryDirectory(prefix='vwa-quick-support-', dir=APP / 'work') as value:
        root = Path(value)
        write_json(root / 'version.json', {'version': '1.0.0'})
        plan = root / 'cache/updates/work/plan.json'
        plan.parent.mkdir(parents=True)
        write_json(plan, {'root': str(root), 'version': '2.0.0'})
        write_json(root / 'cache/updates/pending.json', {'version': '2.0.0', 'plan': str(plan)})
        write_json(root / 'cache/updates/last-notice.json', {'version': '2.0.0', 'notes': 'bounded release notes'})
        write_json(root / 'cache/updates/last-result.json', {'state': 'failed', 'error': 'previous failure'})
        recovered = load_update_recovery(root)
        assert recovered['pending']['version'] == '2.0.0'
        assert Path(recovered['pending']['plan']) == plan.resolve()
        assert recovered['pending']['notes'] == 'bounded release notes'
        assert recovered['last_result']['error'] == 'previous failure'

        state = make_state(root)
        assert state.restore_update_recovery(recovered)
        assert state.update_plan == plan.resolve()
        assert state.restart_pending and not state.auto_restart_active
        assert state.release['notes'] == 'bounded release notes'
        assert state.startup_view['apply_ready'] is True

        state.set_update_plan(plan, '2.0.0')
        assert state.begin_auto_restart()
        assert state.restart_remaining == 10
        assert state.tick_auto_restart(('任务修改',)) is False
        assert state.auto_restart_active is False and state.restart_deferred is True
        assert '任务修改' in state.update['message'] and state.update_plan == plan.resolve()

        state.set_update_plan(plan, '2.0.0')
        assert state.begin_auto_restart()
        for _ in range(10):
            assert state.tick_auto_restart(()) is False
        assert state.restart_remaining == 0
        assert state.tick_auto_restart(()) is True
        assert state.update['update_state'] == 'applying'

    with tempfile.TemporaryDirectory(prefix='vwa-quick-support-large-', dir=APP / 'work') as value:
        root = Path(value)
        write_json(root / 'version.json', {'version': '1.0.0'})
        (root / 'cache/updates').mkdir(parents=True)
        (root / 'cache/updates/last-result.json').write_text(
            json.dumps({'state': 'failed', 'error': 'x' * 100}) + ' ' * (70 * 1024), encoding='utf-8')
        recovered = load_update_recovery(root)
        assert recovered['pending'] is None
        assert recovered['last_result'] is None


def qml_and_source_checks():
    qml_root = APP / 'src/quick_ui'
    startup = (qml_root / 'StartupDialogs.qml').read_text(encoding='utf-8-sig')
    settings = (qml_root / 'SettingsPage.qml').read_text(encoding='utf-8-sig')
    bridge = (APP / 'src/quick_bridge.py').read_text(encoding='utf-8-sig')
    support = (APP / 'src/quick_support.py').read_text(encoding='utf-8-sig')
    for page in ('processing', 'components', 'tuning', 'audio_mixdown', 'video_mixdown', 'publishing', 'updates'):
        assert f'pageId: "{page}"' in startup
    assert 'controller.tutorialStepCount' in startup
    assert 'objectName: "tutorialPrevious"' in startup
    assert 'sequence: "Left"' in startup and 'previousTutorial()' in startup
    assert 'objectName: "updateReleaseNotes"' in settings
    assert 'objectName: "presetInspectText"' in settings
    assert 'setMonitorActive(true)' in settings and 'setMonitorActive(false)' in settings
    assert 'self._mixdown.viewChanged.connect(self._support.refreshActivity)' not in bridge
    assert 'self._task_work.changed' in bridge
    assert "add('任务修改')" in support
    assert "getattr(mixdown, '_preview', None)" in support
    assert "getattr(mixdown, '_preview_pending', False)" in support


def dynamic_checks():
    runtime = os.environ.get('VWA_TEST_QT6_ROOT')
    if not runtime:
        print('SKIP: dynamic Quick support import requires VWA_TEST_QT6_ROOT')
        return
    from quick_runtime import configure_quick_runtime
    configure_quick_runtime(APP, runtime, software=True)
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    app = QCoreApplication.instance() or QCoreApplication([])
    import quick_support as qs

    with tempfile.TemporaryDirectory(prefix='vwa-quick-support-dynamic-', dir=APP / 'work') as value:
        root = Path(value)
        plan = root / 'cache/updates/work/plan.json'
        plan.parent.mkdir(parents=True)
        write_json(plan, {'root': str(root), 'version': '2.0.0'})
        state = make_state(root)
        state.update_plan = plan.resolve()
        state.pending_version = '2.0.0'
        state.restart_pending = True

        class View:
            def __init__(self, **values):
                self.view = values

        class Mixdown(View):
            pass

        class Owner(QObject):
            closeReady = Signal()
            def __init__(self):
                super().__init__()
                self.operationKind = ''
                self.taskMutationBusy = False
                self.calibration = View(busy=False)
                self.tuning = View(busy=False)
                self.mixdown = Mixdown(busy=False, playing=False)
                self.mixdown._preview = None
                self.mixdown._preview_pending = False
                self.mixdown._preview_timer = type('Timer', (), {'isActive': lambda self: False})()
                self.video = View(busy=False)
                self.publication = View(busy=False, browserSession={'active': False})
                self.preparationTask = {'state': 'idle'}
            def set_component_source(self, _value):
                return None

        owner = Owner()
        worker = qs.WorkerLane(owner, 'vwa-support-test-worker')
        with patch.object(qs, 'SettingsState', return_value=state), \
                patch.object(qs, 'load_update_recovery', return_value={}), \
                patch.object(qs.updater, 'current_version', return_value='1.1.0'):
            support = qs.QuickSupport(owner, worker, [('Auto', 'auto')])
        try:
            support.setMonitorActive(False)
            assert support.refreshActivity() is False
            assert support._activity_timer.isActive() is False
            owner.operationKind = 'task-rename'
            owner.taskMutationBusy = True
            support.setMonitorActive(True)
            assert '任务修改' in support.operationBlockers
            owner.mixdown._preview = object()
            owner.mixdown._preview_pending = True
            support.setMonitorActive(False)
            support.setMonitorActive(True)
            assert '简易混音试听会话' in support.operationBlockers
            assert '简易混音试听准备' in support.operationBlockers
            support.refreshActivity()
            assert support._activity_timer.isActive() is True

            state.tour_open = True
            state.tour_step = 2
            support.previousTutorial()
            assert state.tour_step == 1
            assert support.tutorialStepCount == 7

            class FakeLane:
                kind = ''
                def start(self, _kind, work, finish):
                    finish(work(None, None), None)
                    return True
                def close(self):
                    self.kind = ''

            class FakeLibrary:
                def get_snapshot(self, identifier, version):
                    return {'raw': b'{"id":"user.test","version":"0.1.0"}',
                            'document': {'id': identifier, 'version': version}}

            support.inspect_lane = FakeLane()
            support._preset_view.update(loaded=True)
            support._library = lambda: FakeLibrary()
            assert support.inspectPreset('user.test', '0.1.0')
            assert '"user.test"' in support.presetLibrary['inspect_text']
            assert support.presetLibrary['inspect_error'] == ''

            owner.operationKind = ''
            owner.taskMutationBusy = False
            owner.mixdown._preview = None
            owner.mixdown._preview_pending = False
            state.set_update_plan(plan, '2.0.0')
            with patch.object(qs.updater, 'restart_to_apply', side_effect=AssertionError('must not restart in tests')):
                assert support._apply_restart_plan() is False
            assert state.update_plan == plan.resolve()
            assert '测试模式' in state.update['message']
        finally:
            support.close()
            worker.close()


recovery_checks()
qml_and_source_checks()
dynamic_checks()
print('PASS: Quick support recovery, tutorial, preset inspection, monitor coalescing and test-safe restart')
