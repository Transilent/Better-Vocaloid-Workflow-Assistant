"""GUI transport generations, seek ownership, recovery and bounded shutdown."""
import os
from pathlib import Path
import queue
import sys
import threading
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PyQt5.QtWidgets import QApplication
from mixdown_realtime_player import RealtimePreviewPlayer

APP = QApplication.instance() or QApplication([])


class Service:
    def __init__(self, *_args, **_kwargs):
        self.events = queue.Queue()
        self.commands = []
        self.closed = False
        self.serial = 0

    def start(self):
        self.closed = False
        self.commands.append(('start',))

    def open(self, request, **kwargs):
        self.generation = kwargs['request_id']
        self.commands.append(('open', self.generation, request, kwargs))

    def _control(self, action, value=None):
        self.serial += 1
        self.commands.append((action, self.serial, value))
        return str(self.serial)

    def seek(self, position_ms, autoplay=None): return self._control('seek', (position_ms, autoplay))
    def parameters(self, request): return self._control('parameters', request)
    def play(self): return self._control('play')
    def pause(self): return self._control('pause')
    def stop(self): return self._control('stop')
    def release(self): return self._control('release')
    def set_volume(self, value): return self._control('volume', value)
    def close(self, timeout=None): self.closed = True
    def poll(self, **_kwargs):
        result = []
        while True:
            try: result.append(self.events.get_nowait())
            except queue.Empty: return result


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.player = RealtimePreviewPlayer('fixture.dll', cache_dir='unused', service_factory=Service)
        self.service = self.player._service

    def tearDown(self):
        self.player.close()

    def wait(self, condition):
        deadline = time.perf_counter() + 2
        while not condition() and time.perf_counter() < deadline:
            self.player.poll()
            APP.processEvents()
            time.sleep(.002)
        self.assertTrue(condition())

    def prepare(self):
        self.player.open({'gain': 1}, position_ms=0)
        generation = self.player._generation
        self.wait(lambda: any(c[0] == 'open' and c[1] == generation
                              for c in self.service.commands))
        self.service.events.put(dict(type='live_ready', request_id=generation,
                                     sample_rate=48000, channels=2, duration_frames=480000))
        self.player.poll()
        return generation

    def state(self, generation, frame, **values):
        snapshot = dict(state='playing', sample_rate=48000, duration_frames=480000,
                        native=dict(media_frame=frame, sample_rate=48000, queued_frames=1024))
        snapshot.update(values)
        self.service.events.put(dict(type='live_state', request_id=generation, snapshot=snapshot))
        self.player.poll()

    def test_latest_seek_holds_target_until_matching_ack(self):
        gen = self.prepare()
        positions = []
        self.player.positionChanged.connect(positions.append)
        self.player.seek(8000)
        self.wait(lambda: self.player._pending_seek['control_id'] is not None)
        first = self.player._pending_seek['control_id']
        self.player.seek(3000)
        self.wait(lambda: self.player._pending_seek['control_id'] is not None)
        latest = self.player._pending_seek['control_id']
        self.state(gen, 384000, seek_control_id=first,
                   last_seek=dict(frame=384000, phase='submitted'))
        self.assertEqual(self.player.position(), 3000)
        self.assertEqual(positions, [])
        self.state(gen, 144256, seek_control_id=latest,
                   last_seek=dict(frame=144000, phase='submitted'))
        self.assertIsNone(self.player._pending_seek)
        self.assertTrue(3000 <= self.player.position() < 3010)
        self.assertEqual(len(positions), 1)

    def test_paused_seek_accepts_queued_not_stale_snapshot(self):
        gen = self.prepare()
        self.player.seek(6500, autoplay=False)
        self.wait(lambda: self.player._pending_seek['control_id'] is not None)
        identifier = self.player._pending_seek['control_id']
        self.state(gen, 0, state='paused')
        self.assertIsNotNone(self.player._pending_seek)
        self.state(gen, 312000, state='paused', seek_control_id=identifier,
                   last_seek=dict(frame=312000, phase='queued'))
        self.assertEqual(self.player.position(), 6500)
        self.assertIsNone(self.player._pending_seek)

    def test_replacement_ignores_old_generation_and_failure_retries_once(self):
        old = self.prepare()
        self.service.events.put(dict(type='error', request_id=old, message='device lost'))
        errors = []
        self.player.errorOccurred.connect(errors.append)
        self.player.poll()
        self.assertTrue(self.player.failed)
        self.assertEqual(len([c for c in self.service.commands if c[0] == 'open']), 1)
        new = self.prepare()
        self.assertNotEqual(old, new)
        self.state(old, 400000)
        self.assertEqual(self.player.position(), 0)
        self.assertFalse(self.player.failed)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len([c for c in self.service.commands if c[0] == 'open']), 2)

    def test_ab_only_promotes_applied_submitted_parameters(self):
        gen = self.prepare()
        self.player.parameters({'bypass': True}, key='before')
        self.wait(lambda: self.player._pending_parameters['control_id'] is not None)
        identifier = self.player._pending_parameters['control_id']
        self.state(gen, 1000, parameter_control_id=identifier, parameter_first_frame=2000)
        self.assertEqual(self.player.selected_key, 'after')
        self.state(gen, 2512, parameter_control_id=identifier, parameter_first_frame=2000)
        self.assertEqual(self.player.selected_key, 'before')

    def test_release_and_close_do_not_reopen_output(self):
        gen = self.prepare()
        self.player.release()
        self.wait(lambda: any(c[0] == 'release' for c in self.service.commands))
        self.state(gen, 400000)
        self.assertIsNone(self.player.source())
        self.player.close()
        self.assertTrue(self.player._done.is_set())
        self.assertTrue(self.service.closed)
        self.assertEqual(len([c for c in self.service.commands if c[0] == 'open']), 1)

    def test_eof_seek_and_unknown_submission_do_not_jump_or_hang(self):
        gen = self.prepare()
        self.player.seek(10000, autoplay=False)
        self.wait(lambda: self.player._pending_seek['control_id'] is not None)
        identifier = self.player._pending_seek['control_id']
        self.service.events.put(dict(type='live_state', request_id=gen,
            snapshot=dict(state='preparing', native=dict(sample_rate=48000, media_frame=None),
                          seek_control_id=identifier,
                          last_seek=dict(frame=480000, phase='queued'))))
        self.player.poll()
        self.assertIsNotNone(self.player._pending_seek)
        self.state(gen, 480000, state='ended', seek_control_id=identifier,
                   last_seek=dict(frame=480000, phase='ended'))
        self.assertIsNone(self.player._pending_seek)
        self.assertEqual(self.player.position(), 10000)


if __name__ == '__main__':
    unittest.main()
