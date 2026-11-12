"""Quick video task pinning, immutable render, explicit bind and publish gates."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import common
import task_contract


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def wait_for(application, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    application.processEvents()
    raise AssertionError('timed out waiting for worker')


class FakeHandoff(ModuleType):
    def __init__(self):
        super().__init__('quick_media_handoff')
        self.pins = {}
        self.calls = []

    def verify_task_artifact(self, job, row, role, check=None):
        self.calls.append((str(job), dict(row), role))
        if check is not None and check():
            raise InterruptedError('cancelled fixture')
        expected = self.pins.get(role)
        if not expected:
            raise ValueError('unknown fixture role')
        for key in ('id', 'revision', 'sha256', 'path', 'role'):
            if str(row.get(key) or '') != str(expected.get(key) or ''):
                raise ValueError('pinned row changed: ' + key)
        if not Path(expected['path']).is_file():
            raise ValueError('pinned file missing')
        return dict(expected)


@unittest.skipIf(not (APP / 'src/quick_video.py').is_file(), 'quick video controller unavailable')
class QuickVideoTests(unittest.TestCase):
    def setUp(self):
        try:
            from PySide6.QtCore import QCoreApplication, QUrl
            from quick_video import QuickVideo
            from quick_work import WorkerLane
        except ImportError as error:
            self.skipTest(str(error))
        import quick_video
        self.quick_video = quick_video
        self.QCoreApplication = QCoreApplication
        self.QUrl = QUrl
        self.application = QCoreApplication.instance() or QCoreApplication([])
        base = Path(os.environ.get('VWA_TEST_TMPDIR', str(Path.cwd() / 'work')))
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.app_root = self.root / 'app'
        self.job = self.root / 'jobs' / '20261111_001_fixture'
        self.job.mkdir(parents=True)
        (self.job / 'request.json').write_text('{}', encoding='utf-8')
        self.source = self.job / 'video' / 'source.mp4'
        self.mix = self.job / 'finished.wav'
        self.external_mix = self.job / 'manual.wav'
        self.source.parent.mkdir()
        self.source.write_bytes(b'video-fixture')
        self.mix.write_bytes(b'mix-fixture')
        self.external_mix.write_bytes(b'external-mix-fixture')
        self.source_row = {
            'id': 'source-id', 'revision': 'source-r1', 'sha256': digest(self.source.read_bytes()),
            'path': str(self.source.resolve()), 'role': 'source_video', 'state': 'verified',
            'available': True,
        }
        self.mix_row = {
            'id': 'mix-id', 'revision': 'mix-r1', 'sha256': digest(self.mix.read_bytes()),
            'path': str(self.mix.resolve()), 'role': 'mixdown', 'state': 'verified',
            'available': True,
        }
        self.handoff = FakeHandoff()
        self.handoff.pins = {'source_video': dict(self.source_row), 'mixdown': dict(self.mix_row)}
        self.patches = [
            patch.object(common, 'ROOT', self.app_root),
            patch.object(quick_video, 'ROOT', self.app_root),
            patch.dict(sys.modules, {'quick_media_handoff': self.handoff}),
        ]
        for item in self.patches:
            item.start()
        self.worker = WorkerLane(self.application, 'quick-video-test')
        self.controller = QuickVideo(self.application, self.worker)

    def tearDown(self):
        self.controller.close()
        self.worker.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_pinned_workflow_render_register_publish_then_external_render_is_standalone(self):
        self.assertTrue(self.controller.accept_task(self.job, self.mix_row, self.source_row))
        view = self.controller.view
        required = {
            'job', 'sourceVideo', 'mixdown', 'output', 'offset', 'autoAlign', 'normalize',
            'state', 'message', 'busy', 'preview', 'result', 'resultStale',
            'canAnalyze', 'canRender', 'canRegister', 'canPublish', 'canCancel',
        }
        self.assertTrue(required.issubset(view))
        self.assertTrue(view['canAnalyze'] and view['canRender'])
        self.assertFalse(view['canRegister'] or view['canPublish'])

        analysis = {
            'accepted': True, 'offset_seconds': 0.42, 'method': 'fixture-corr',
            'correlation': 0.91, 'margin': 0.2,
        }

        def fake_analyze(job, source, mixdown, *, reset_cancel=True):
            self.assertFalse(reset_cancel)
            return dict(analysis)

        with patch.object(self.quick_video.video_mixdown, 'analyze_alignment', side_effect=fake_analyze):
            self.controller.analyze()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertEqual(self.controller.view['state'], 'analyzed')
        self.assertIn('0.910', self.controller.view['alignmentSummary'])
        self.assertEqual([call[2] for call in self.handoff.calls[-2:]], ['mixdown', 'source_video'])

        render_calls = []
        bind_calls = []

        def fake_render(job, source, mixdown, output, **options):
            render_calls.append((job, source, mixdown, output, dict(options)))
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'formal-mp4')
            return {
                'output': str(output.resolve()),
                'sha256': digest(output.read_bytes()),
                'inputs': {
                    str(Path(source).resolve()): digest(Path(source).read_bytes()),
                    str(Path(mixdown).resolve()): digest(Path(mixdown).read_bytes()),
                },
                'alignment': dict(options.get('alignment_report') or analysis),
                'normalized': bool(options.get('normalize')),
                'loudness_measurement': None,
            }

        with patch.object(self.quick_video.video_mixdown, 'render', side_effect=fake_render), \
                patch.object(task_contract, 'bind', side_effect=lambda *args, **kwargs: bind_calls.append((args, kwargs))):
            self.controller.render()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertEqual(self.controller.view['state'], 'done')
        self.assertTrue(Path(self.controller.view['result']).is_file())
        self.assertTrue(self.controller.view['canRegister'])
        self.assertFalse(bind_calls, 'render auto-bound the generated MP4')
        self.assertEqual(render_calls[0][4]['alignment_report']['offset_seconds'], 0.42)
        retained = self.controller.view['result']
        self.controller.setOption('normalize', True)
        self.assertEqual(self.controller.view['result'], retained)
        self.assertTrue(self.controller.view['resultStale'])
        self.assertFalse(self.controller.view['canRegister'])
        self.controller.setOption('normalize', False)
        self.assertFalse(self.controller.view['resultStale'])
        self.assertTrue(self.controller.view['canRegister'])

        result = Path(self.controller.view['result'])
        video_sha = digest(result.read_bytes())
        registered_row = {
            'id': 'video-id', 'revision': 'video-r1', 'sha256': video_sha,
            'path': str(result.resolve()), 'role': 'video', 'state': 'verified',
            'available': True,
            'dependencies': [{'id': 'mix-id', 'revision': 'mix-r1'},
                             {'id': 'source-id', 'revision': 'source-r1'}],
        }
        bound = []

        def fake_bind(job, kind, destination, **kwargs):
            bound.append((Path(job), kind, Path(destination), kwargs))
            return 'video-id'

        with patch.object(task_contract, 'bind', side_effect=fake_bind), \
                patch.object(task_contract, 'inspect', return_value=[registered_row]), \
                patch('task_store.known_job', side_effect=lambda value: Path(value).resolve()), \
                patch('task_store.locked', return_value=False):
            self.controller.registerResult()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertEqual(self.controller.view['state'], 'registered')
        self.assertTrue(self.controller.view['canPublish'])
        job, kind, destination, kwargs = bound[0]
        self.assertEqual((job, kind, destination), (self.job.resolve(), 'video', result.resolve()))
        self.assertEqual(kwargs['expected_sha256'], video_sha)
        self.assertIsNone(kwargs['video_source'])
        self.assertTrue(callable(kwargs['check']))
        self.assertEqual(kwargs['dependencies'], [
            {'id': 'mix-id', 'revision': 'mix-r1'},
            {'id': 'source-id', 'revision': 'source-r1'},
        ])
        self.assertEqual(self.controller.view['receipt']['sha256'], video_sha)

        self.handoff.pins['video'] = dict(registered_row)
        published = []
        self.controller.publishRequested.connect(lambda job_id, row: published.append((job_id, row)))
        with patch('task_store.known_job', side_effect=lambda value: Path(value).resolve()):
            self.controller.publishResult()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertEqual(published[0][0], str(self.job.resolve()))
        self.assertEqual(published[0][1]['id'], 'video-id')

        second_output = self.job / 'rendered' / 'manual.mp4'
        self.controller.chooseFile('mixdown', self.QUrl.fromLocalFile(str(self.external_mix)))
        self.controller.chooseFile('output', self.QUrl.fromLocalFile(str(second_output)))
        self.controller.setOption('autoAlign', False)
        self.controller.setOption('offset', -0.125)
        self.controller.setOption('normalize', True)
        with patch.object(self.quick_video.video_mixdown, 'render', side_effect=fake_render), \
                patch.object(task_contract, 'bind', side_effect=AssertionError('external mix was registered')):
            self.controller.render()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertTrue(second_output.is_file())
        self.assertFalse(self.controller.view['canRegister'])
        self.assertFalse(self.controller.view['receipt']['mixdown']['artifact'])
        self.assertEqual(render_calls[-1][4]['offset'], -0.125)
        self.assertTrue(render_calls[-1][4]['normalize'])

    def test_cancel_sets_owned_flag_and_preview_is_local(self):
        entered = threading.Event()

        def blocked(job, source, mixdown, *, reset_cancel=True):
            entered.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (Path(job) / 'cancel.flag').is_file():
                    raise InterruptedError('cancelled fixture')
                time.sleep(0.01)
            raise AssertionError('cancel flag not written')

        with patch.object(self.quick_video.video_mixdown, 'analyze_alignment', side_effect=blocked):
            self.controller.chooseFile('sourceVideo', self.QUrl.fromLocalFile(str(self.source)))
            self.controller.chooseFile('mixdown', self.QUrl.fromLocalFile(str(self.mix)))
            self.controller.analyze()
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.controller.view['state'], 'analyzing')
            self.assertTrue(self.controller.view['busy'] and self.controller.view['canCancel'])
            self.controller.cancel()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertEqual(self.controller.view['state'], 'cancelled')

        def fake_cover(source, destination, seconds, *, require_audio):
            self.assertFalse(require_audio)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return {'source': str(source), 'seconds': seconds, 'sha256': '0' * 64}

        with patch.object(self.quick_video.publication, 'extract_cover', side_effect=fake_cover):
            self.controller.previewFrame(1.25)
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertTrue(self.controller.view['preview'].startswith('file:'))
        self.assertEqual(self.controller.view['previewTime'], 1.25)
        self.assertEqual(self.controller.view['result'], '')


    def test_verified_task_source_can_load_without_registered_mix(self):
        detail = {
            'title': 'source-only fixture',
            'artifacts': [dict(self.source_row)],
            'studioTargets': {},
        }
        with patch('quick_results.detail', return_value=detail), \
                patch('task_store.known_job', side_effect=lambda value: Path(value).resolve()):
            self.controller.restoreTaskSource(str(self.job), '')
            wait_for(self.application, lambda: not self.controller.view['busy'])

        view = self.controller.view
        self.assertEqual(view['job'], str(self.job.resolve()))
        self.assertEqual(view['sourceVideo'], str(self.source.resolve()))
        self.assertEqual(view['mixdown'], '')
        self.assertFalse(view['canAnalyze'] or view['canRender'] or view['canRegister'])
        self.assertEqual(view['state'], 'ready')
        self.assertEqual(view['receipt'], {})
        self.assertFalse(view['canRegister'])

        output = self.job / 'rendered' / 'manual-source.mp4'
        self.controller.chooseFile('mixdown', self.QUrl.fromLocalFile(str(self.external_mix)))
        self.controller.chooseFile('output', self.QUrl.fromLocalFile(str(output)))
        view = self.controller.view
        self.assertTrue(view['canAnalyze'] and view['canRender'])
        self.assertFalse(view['canRegister'])

        bind_calls = []
        def fake_render(job, source, mixdown, output, **options):
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'source-only-standalone-mp4')
            return {
                'output': str(output.resolve()),
                'sha256': digest(output.read_bytes()),
                'inputs': {
                    str(Path(source).resolve()): digest(Path(source).read_bytes()),
                    str(Path(mixdown).resolve()): digest(Path(mixdown).read_bytes()),
                },
                'alignment': {}, 'normalized': False, 'loudness_measurement': None,
            }

        with patch.object(self.quick_video.video_mixdown, 'render', side_effect=fake_render), \
                patch.object(task_contract, 'bind', side_effect=lambda *args, **kwargs: bind_calls.append((args, kwargs))):
            self.controller.render()
            wait_for(self.application, lambda: not self.controller.view['busy'])
        self.assertTrue(output.is_file())
        self.assertEqual(self.controller.view['state'], 'done')
        self.assertFalse(self.controller.view['canRegister'])
        self.assertEqual(bind_calls, [])
        self.assertFalse(self.controller.view['receipt']['mixdown']['artifact'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
