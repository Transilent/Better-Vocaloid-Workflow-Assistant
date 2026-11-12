"""Qt-neutral transport ownership and snapshot passthrough."""
import copy
import os
from pathlib import Path
import queue
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mixdown_realtime_core import PlaybackState, RealtimePreviewPlayerCore


class Service:
    def __init__(self, *_args, **_kwargs):
        self.events = queue.Queue()
        self.commands = []
        self.closed = False
        self.serial = 0

    def start(self):
        self.commands.append(('start',))

    def open(self, request, **kwargs):
        self.commands.append(('open', kwargs['request_id'], copy.deepcopy(request), dict(kwargs)))

    def _control(self, kind, value=None):
        self.serial += 1
        self.commands.append((kind, self.serial, copy.deepcopy(value)))
        return str(self.serial)

    def seek(self, position_ms, autoplay=None):
        return self._control('seek', (position_ms, autoplay))

    def parameters(self, request):
        return self._control('parameters', request)

    def play(self): return self._control('play')
    def pause(self): return self._control('pause')
    def stop(self): return self._control('stop')
    def release(self): return self._control('release')
    def set_volume(self, value): return self._control('volume', value)

    def close(self, timeout=None):
        self.closed = True

    def poll(self, **_kwargs):
        rows = []
        while True:
            try:
                rows.append(self.events.get_nowait())
            except queue.Empty:
                return rows


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.core = RealtimePreviewPlayerCore(
            'fixture.dll', cache_dir='unused', service_factory=Service,
            event_sink=lambda name, *args: self.events.append((name, args)))
        self.service = self.core._service

    def tearDown(self):
        if self.core.is_alive:
            self.core.close()

    def wait(self, predicate):
        deadline = time.perf_counter() + 2
        while not predicate() and time.perf_counter() < deadline:
            self.core.poll()
            time.sleep(.002)
        self.assertTrue(predicate())

    def prepare(self):
        self.assertTrue(self.core.open({'gain': 1.0}, position_ms=0, autoplay=True))
        generation = self.core.generation
        self.wait(lambda: any(row[0] == 'open' and row[1] == generation
                              for row in self.service.commands))
        self.service.events.put(dict(type='live_ready', request_id=generation,
                                     sample_rate=48000, channels=2,
                                     duration_frames=480000))
        self.core.poll()
        self.assertTrue(self.core.ready)
        self.assertEqual(self.core.duration(), 10000)
        return generation

    def test_import_does_not_load_a_qt_binding(self):
        code = (
            f"import sys; sys.path.insert(0, {str(SRC)!r}); import mixdown_realtime_core; "
            "loaded=[n for n in sys.modules if n=='PyQt5' or n.startswith('PyQt5.') "
            "or n=='PySide6' or n.startswith('PySide6.')]; "
            "assert not loaded, loaded"
        )
        env = dict(os.environ)
        env['PYTHONPATH'] = str(SRC)
        subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, env=env,
                       check=True, timeout=10)

    def test_real_snapshot_and_ack_fields_are_forwarded_unchanged(self):
        generation = self.prepare()
        self.assertTrue(self.core.seek(3000, autoplay=False))
        self.wait(lambda: self.core._pending_seek is not None
                  and self.core._pending_seek['control_id'] is not None)
        control_id = self.core._pending_seek['control_id']
        snapshot = {
            'state': 'playing',
            'sample_rate': 48000,
            'duration_frames': 480000,
            'history_epoch': 9,
            'parameter_revision': 4,
            'seek_control_id': control_id,
            'last_seek': {'frame': 144000, 'phase': 'submitted'},
            'native': {'media_frame': 144256, 'sample_rate': 48000, 'queued_frames': 256},
            'telemetry': {'tracks': [{'rms': 0.25}], 'bus': {'peak': 0.5}},
        }
        self.service.events.put(dict(type='live_state', request_id=generation,
                                     snapshot=copy.deepcopy(snapshot)))
        self.core.poll()
        self.assertIsNone(self.core._pending_seek)
        self.assertEqual(self.core.state(), PlaybackState.PlayingState)
        self.assertTrue(3000 <= self.core.position() < 3010)
        self.assertEqual(self.core.snapshot, snapshot)
        telemetry = [args[0] for name, args in self.events if name == 'telemetryChanged']
        self.assertEqual(telemetry, [snapshot])
        self.assertIn(('seekConfirmed', ()), self.events)

    def test_failed_seek_returns_to_last_confirmed_media_position(self):
        generation = self.prepare()
        self.service.events.put(dict(
            type='live_state', request_id=generation,
            snapshot=dict(state='paused', sample_rate=48000, duration_frames=480000,
                          native=dict(media_frame=96000, sample_rate=48000))))
        self.core.poll()
        self.assertEqual(self.core.position(), 2000)
        self.assertTrue(self.core.seek(6500, autoplay=False))
        self.assertEqual(self.core.position(), 6500)
        self.service.events.put(dict(type='error', request_id=generation, message='output lost'))
        self.core.poll()
        self.assertTrue(self.core.failed)
        self.assertFalse(self.core.seek_pending)
        self.assertEqual(self.core.position(), 2000)
        self.assertNotIn(('seekConfirmed', ()), self.events)

    def test_loop_boundary_keeps_the_existing_range_seek(self):
        generation = self.prepare()
        self.core.set_loop_range(1000, 5000)
        self.core.set_loop(True)
        self.service.events.put(dict(
            type='live_state', request_id=generation,
            snapshot=dict(
                state='playing', sample_rate=48000, duration_frames=480000,
                native=dict(media_frame=288000, sample_rate=48000, queued_frames=0))))
        self.core.poll()
        self.assertIsNotNone(self.core._pending_seek)
        self.assertEqual(self.core._pending_seek['target'], 1000)
        self.assertEqual(self.core.position(), 1000)
        self.wait(lambda: any(row[0] == 'seek' and row[2][0] == 1000
                              for row in self.service.commands))


if __name__ == '__main__':
    unittest.main()
