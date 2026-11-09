"""Session replacement and timeline gestures share a single playback intent."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mixdown_preview_controller import MixdownPreviewController


class Player:
    def __init__(self):
        self.available = True
        self.ready = self.loading = self.failed = self.seek_pending = False
        self.play_intent = False
        self.cursor = 0
        self.length = 60000
        self.commands = []

    def position(self): return self.cursor
    def duration(self): return self.length
    def set_play_intent(self, enabled): self.play_intent = enabled
    def set_loop_range(self, *values): self.loop_range = values
    def open(self, request, **options):
        self.loading = True
        self.ready = self.failed = self.seek_pending = False
        self.cursor = options['position_ms']
        self.play_intent = options['autoplay']
        self.commands.append(('open', copy.deepcopy(request), options))
        return True
    def parameters(self, request, **options):
        self.commands.append(('parameters', copy.deepcopy(request), options))
        return True
    def seek(self, position, autoplay):
        self.cursor = position
        self.play_intent = autoplay
        self.seek_pending = True
        self.commands.append(('seek', position, autoplay))
        return True
    def play(self):
        self.play_intent = True
        self.commands.append(('play',))
        return True
    def pause(self):
        self.play_intent = False
        self.commands.append(('pause',))
        return True
    def release(self):
        self.play_intent = False
        self.ready = self.loading = self.seek_pending = False
        self.cursor = 0
        self.commands.append(('release',))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.player = Player()
        self.now = 100.0
        self.controller = MixdownPreviewController(self.player, clock=lambda: self.now)

    def prepare(self):
        self.controller.refresh({'gain': 1}, ('source-a',), start_ms=1200)
        self.player.loading = False
        self.player.ready = True

    def test_loading_edits_coalesce_in_transport_without_reopening(self):
        c = self.controller
        c.set_playing(True)
        self.assertFalse(self.player.commands)
        self.assertTrue(c.refresh({'gain': 1}, ('a',), start_ms=1200))
        for gain in (2, 3, 4):
            c.refresh({'gain': gain}, ('a',))
        self.assertEqual([row[0] for row in self.player.commands],
                         ['open', 'parameters', 'parameters', 'parameters'])
        self.assertEqual(self.player.commands[0][2]['position_ms'], 1200)
        self.assertTrue(self.player.commands[0][2]['autoplay'])

    def test_pause_is_owned_by_transport_during_pending_edit_and_seek(self):
        self.prepare()
        c = self.controller
        c.set_playing(True)
        c.dirty = True
        c.set_playing(False)
        c.stage_seek(8000)
        self.assertTrue(c.commit_seek(8000))
        self.assertEqual(self.player.commands[-1], ('seek', 8000, False))
        self.assertFalse(c.play_intent)
        c.refresh({'gain': 2}, ('source-a',))
        self.assertFalse(c.play_intent)

    def test_cursor_stays_local_until_latest_seek_acknowledged(self):
        self.prepare()
        c = self.controller
        c.begin_seek()
        c.stage_seek(7000)
        self.assertFalse(c.follow_position())
        self.assertEqual(c.position_ms, 7000)
        c.dragging = False
        self.assertFalse(c.follow_position())  # keyboard debounce not yet sent
        c.commit_seek(7000)
        c.stage_seek(9000)
        c.commit_seek(9000)
        self.assertFalse(c.follow_position())  # stale native packet
        self.player.seek_pending = False
        self.player.cursor = 9003
        self.now += .02
        self.assertTrue(c.follow_position())
        self.assertIsNone(c.pending_seek)
        self.assertEqual(c.last_seek['target_ms'], 9000)
        self.assertEqual(c.last_seek['elapsed_ms'], 20)
        self.assertEqual(c.position_ms, 9003)

    def test_source_replacement_retires_seek_but_same_source_keeps_position(self):
        self.prepare()
        c = self.controller
        c.stage_seek(8000)
        c.commit_seek(8000)
        c.refresh({'gain': 2}, ('source-a',))
        self.assertIsNotNone(c.pending_seek)
        c.refresh({'gain': 2}, ('source-b',), start_ms=400)
        self.assertIsNone(c.pending_seek)
        self.assertEqual(self.player.commands[-1][0], 'open')
        self.assertEqual(self.player.commands[-1][2]['position_ms'], 400)

    def test_device_failure_requires_explicit_play_and_preserves_position(self):
        self.prepare()
        self.player.cursor = 12000
        self.player.failed = True
        self.player.ready = False
        c = self.controller
        c.failed()
        self.assertFalse(c.refresh({'gain': 2}, ('source-a',)))
        c.set_playing(True)
        self.assertTrue(c.refresh({'gain': 2}, ('source-a',)))
        self.assertEqual(self.player.commands[-1][2]['position_ms'], 12000)
        self.assertTrue(self.player.commands[-1][2]['autoplay'])

    def test_failure_releases_timeline_ownership_and_pauses_old_audio(self):
        self.prepare()
        c = self.controller
        c.stage_seek(9000)
        c.failed()
        self.assertIsNone(c.pending_seek)
        self.assertEqual(c.last_seek['phase'], 'failed')
        self.assertEqual(self.player.commands[-1], ('pause',))
        self.assertTrue(c.follow_position())

    def test_release_retires_source_and_intent(self):
        self.prepare()
        c = self.controller
        c.set_playing(True)
        c.select('before')
        c.release()
        self.assertFalse(c.play_intent)
        self.assertIsNone(c.source_key)
        self.assertTrue(c.dirty)


if __name__ == '__main__':
    unittest.main()
