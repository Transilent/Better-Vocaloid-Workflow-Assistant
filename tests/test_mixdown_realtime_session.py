"""Worker lifecycle/control contracts, independent of audio hardware."""
import collections
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
from mixdown_realtime_session import RealtimeAuditionSession, RealtimeSessionError


class Renderer:
    sample_rate = 8000
    channels = 2
    duration_frames = 16000

    def __init__(self, loud=False):
        self.position_frame = 0
        self.gain = 1.0
        self.closed = 0
        self.loud = loud

    def render(self, frames):
        start = self.position_frame
        self.position_frame += frames
        data = ((np.arange(start, start + frames, dtype=np.float32) % 1000) * .0001 * self.gain)
        return np.full((frames, 2), 2.0, dtype=np.float32) if self.loud else np.column_stack((data, data))

    def seek(self, frame): self.position_frame = frame
    def set_parameters(self, request): self.gain = float(request['gain'])
    def close(self): self.closed += 1


class Output:
    """A partial-writing sink with an independently scheduled fake consumer."""
    def __init__(self):
        self.created_thread = threading.get_ident()
        self.lock = threading.Lock()
        self.queue = collections.deque()
        self.epoch = 0
        self.media = self.tail = 0
        self.playing = False
        self.closed = 0
        self.error = None
        self.accepted = []
        self.seek_log = []
        self.consumed = []
        self.done = threading.Event()
        self.consumer = threading.Thread(target=self._consume, daemon=True)
        self.consumer.start()

    def _consume(self):
        while not self.done.wait(.004):
            with self.lock:
                if self.playing:
                    count = min(len(self.queue), 32)
                    if count:
                        data = [self.queue.popleft() for _ in range(count)]
                        self.consumed.append((self.epoch, self.media, np.array(data)))
                        self.media += count

    def snapshot(self):
        with self.lock:
            return {'epoch': self.epoch, 'media_frame': self.media,
                    'queued_frames': len(self.queue), 'sample_rate': 8000,
                    'channels': 2, 'capacity_frames': 256, 'period_frames': 32,
                    'device_error': self.error, 'backend': 'test_sink',
                    'clock_quality': 'fake_callback'}

    def write(self, data, *, epoch, start_frame):
        with self.lock:
            assert epoch == self.epoch
            assert start_frame == self.tail, (start_frame, self.tail)
            count = min(len(data), 31, 256 - len(self.queue))
            self.queue.extend(data[:count].copy())
            self.accepted.append((epoch, start_frame, data[:count].copy()))
            self.tail += count
            return count

    def seek(self, *, frame, epoch):
        with self.lock:
            assert epoch > self.epoch
            self.queue.clear()
            self.epoch = epoch
            self.media = self.tail = frame
            self.seek_log.append((epoch, frame))

    def set_playing(self, value):
        with self.lock:
            self.playing = value

    def close(self):
        self.done.set()
        self.consumer.join(1)
        self.closed_thread = threading.get_ident()
        self.closed += 1


def wait(session, predicate, seconds=3):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = session.snapshot()
        if predicate(result):
            return result
        if result['state'] == 'failed':
            raise AssertionError(result)
        time.sleep(.002)
    raise AssertionError(session.snapshot())


class SessionTests(unittest.TestCase):
    def test_parameter_validation_does_not_starve_the_producer(self):
        entered = threading.Event()
        proceed = threading.Event()
        class ValidatingRenderer(Renderer):
            def prepare_parameters(self, request):
                entered.set()
                if not proceed.wait(3):
                    raise AssertionError('test did not release control-side validation')
                return float(request['gain'])
            def apply_parameters(self, plan): self.gain = plan
        renderer, output = ValidatingRenderer(), Output()
        session = RealtimeAuditionSession(renderer, output, block_frames=64)
        self.addCleanup(session.close)
        session.play()
        session.start()
        wait(session, lambda s: s['native']['media_frame'] >= 64)
        before = session.snapshot()['accepted_frames']
        errors = []
        def update():
            try:
                session.set_parameters({'gain': .5})
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=update)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            wait(session, lambda s: s['accepted_frames'] > before, seconds=2)
        finally:
            proceed.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        wait(session, lambda s: s['parameter_revision'] == 1)
        self.assertEqual(renderer.gain, .5)

    def test_factory_has_one_control_thread_for_creation_and_close(self):
        created = []
        def factory():
            output = Output()
            created.append(output)
            return output
        session = RealtimeAuditionSession(Renderer(), factory, block_frames=64)
        self.addCleanup(session.close)
        self.assertFalse(created)
        session.start()
        wait(session, lambda s: s['accepted_frames'] > 0)
        session.close()
        self.assertNotEqual(created[0].created_thread, threading.get_ident())
        self.assertEqual(created[0].created_thread, created[0].closed_thread)

    def make(self, **kwargs):
        renderer, output = Renderer(), Output()
        session = RealtimeAuditionSession(renderer, output, block_frames=64, **kwargs)
        self.addCleanup(session.close)
        return session, renderer, output

    def test_latest_seek_pause_parameter_resume_and_partial_writes(self):
        session, renderer, output = self.make()
        session.seek(1000)
        session.play()
        session.seek(2000)
        last = session.seek(3000, autoplay=True)
        session.start()
        wait(session, lambda s: (s.get('last_seek') or {}).get('phase') == 'submitted')
        self.assertEqual(session.snapshot()['last_seek']['seek_id'], last)
        self.assertEqual([frame for _, frame in output.seek_log], [0, 3000])
        self.assertTrue(output.consumed)
        first = output.consumed[0]
        self.assertEqual(first[1], 3000)
        expected = ((np.arange(3000, 3000 + len(first[2]), dtype=np.float32) % 1000) * .0001)
        np.testing.assert_array_equal(first[2][:, 0], expected)
        session.pause()
        snap = wait(session, lambda s: s['state'] == 'paused')
        held = snap['native']['media_frame']
        time.sleep(.03)
        self.assertEqual(session.snapshot()['native']['media_frame'], held)
        request = {'gain': .5}
        revision = session.set_parameters(request)
        request['gain'] = 99.0
        wait(session, lambda s: s['parameter_revision'] == revision and s['native']['queued_frames'] >= 64)
        self.assertEqual(renderer.gain, .5)
        self.assertEqual(output.seek_log[-1][1], held)
        session.play()
        wait(session, lambda s: s['native']['media_frame'] > held)
        stopped = session.stop()
        wait(session, lambda s: (s.get('last_seek') or {}).get('seek_id') == stopped and s['state'] == 'paused')
        self.assertFalse(output.playing)
        self.assertLessEqual(session.snapshot()['max_queue_frames'], session.target_queue_frames)
        session.close()
        session.close()
        self.assertEqual((renderer.closed, output.closed), (1, 1))
        self.assertFalse(session._thread.is_alive())

    def test_silent_probe_still_processes_graph(self):
        session, renderer, output = self.make(silent=True)
        session.play()
        session.start()
        wait(session, lambda s: s['native']['media_frame'] >= 128)
        session.close()
        self.assertGreater(session.snapshot()['sample_peak'], 0)
        self.assertTrue(all(not data.any() for _, _, data in output.consumed))

    def test_resume_with_a_partial_prefilled_queue(self):
        session, renderer, output = self.make()
        session.play()
        session.start()
        wait(session, lambda s: s['native']['media_frame'] >= 32)
        session.pause()
        wait(session, lambda s: s['state'] == 'paused' and s['native']['queued_frames'] > 64)
        # A callback may leave any remainder between one and two DSP blocks.
        # Resume must not wait for a completely full queue while the producer
        # is correctly waiting for room for another complete block.
        with output.lock:
            if len(output.queue) == 128:
                output.queue.popleft()
                output.media += 1
            held = output.media
            self.assertTrue(64 < len(output.queue) < 128)
        session.play()
        wait(session, lambda s: s['native']['media_frame'] > held)

    def test_end_of_song_is_stable_and_does_not_render_forever(self):
        session, renderer, output = self.make()
        renderer.duration_frames = 96
        session.play()
        session.start()
        snap = wait(session, lambda s: s['state'] == 'ended')
        self.assertEqual(snap['native']['media_frame'], 96)
        self.assertEqual(snap['rendered_frames'], 96)
        time.sleep(.02)
        self.assertEqual(session.snapshot()['state'], 'ended')

    def test_loud_premix_fails_before_writing_and_closes(self):
        renderer, output = Renderer(loud=True), Output()
        session = RealtimeAuditionSession(renderer, output, block_frames=64)
        self.addCleanup(session.close)
        session.start()
        until = time.monotonic() + 3
        while session.snapshot()['state'] != 'failed' and time.monotonic() < until:
            time.sleep(.002)
        self.assertEqual(session.snapshot()['state'], 'failed')
        self.assertIn('safe output ceiling', session.snapshot()['error'])
        session.close()
        self.assertFalse(output.accepted)
        self.assertEqual((renderer.closed, output.closed), (1, 1))

    def test_device_failure_does_not_restart(self):
        session, renderer, output = self.make()
        session.start()
        wait(session, lambda s: s['accepted_frames'] > 0)
        output.error = 'device invalidated'
        until = time.monotonic() + 3
        while session.snapshot()['state'] != 'failed' and time.monotonic() < until:
            time.sleep(.002)
        self.assertEqual(session.snapshot()['state'], 'failed')
        self.assertIn('device invalidated', session.snapshot()['error'])
        session.close()
        self.assertEqual(output.closed, 1)

    def test_stalled_wasapi_callback_fails_without_starting_second_output(self):
        class StalledOutput(Output):
            def snapshot(self):
                state = super().snapshot()
                state.update(backend='wasapi-shared', callback_count=12)
                return state
        created = []
        def factory():
            output = StalledOutput()
            created.append(output)
            return output
        renderer = Renderer()
        session = RealtimeAuditionSession(renderer, factory, block_frames=64)
        self.addCleanup(session.close)
        session.start()
        snap = wait(session, lambda s: s['state'] == 'failed', seconds=3)
        self.assertIn('stopped responding', snap['error'])
        session.close()
        self.assertEqual(len(created), 1)
        self.assertEqual((renderer.closed, created[0].closed), (1, 1))

    def test_close_before_start_and_invalid_commands(self):
        session, renderer, output = self.make()
        with self.assertRaises(ValueError): session.seek(-1)
        with self.assertRaises(ValueError): session.seek(renderer.duration_frames + 1)
        session.close()
        session.close()
        self.assertEqual((renderer.closed, output.closed), (1, 1))
        with self.assertRaises(RealtimeSessionError): session.play()
        with self.assertRaises(RealtimeSessionError): session.start()


if __name__ == '__main__':
    unittest.main()
