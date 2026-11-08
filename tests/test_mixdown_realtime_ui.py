"""Actual mixer widgets routed to the native transport with no audio device."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(0, str(APP / 'tests'))
from PyQt5.QtWidgets import QApplication, QWidget
import audio_mixdown_ui as ui
from mixdown_realtime_player import RealtimePreviewPlayer
from test_mixdown_realtime_player import Service

QT = QApplication.instance() or QApplication([])


class MixerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.owner = QWidget()
        with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
                patch.object(ui.AudioMixdownDialog, '_start_component_support_check'), \
                patch('mixdown_realtime_player.native_output_path', return_value=None):
            self.dialog = ui.AudioMixdownDialog(self.owner, None, self.owner, embedded=True)
        d = self.dialog
        d._connect_preview_player(d._player, connect=False)
        player = RealtimePreviewPlayer('fixture.dll', cache_dir='unused', service_factory=Service)
        self.player = player
        d._native_preview_player = d._player = player
        d._audition_service = player
        d._native_source_key = 'fixture-sources'
        d._support_ready = True
        d._sources_ready = lambda: True
        d._current_preview_source_key = lambda: 'fixture-sources'
        d._request = Mock(return_value={'gain': 1})
        d._connect_preview_player(player)
        player.prepared.connect(d._native_preview_prepared)
        player.parametersApplied.connect(d._native_parameters_applied)
        d._preview_dirty = False
        d._preview_resume_seconds = 0
        self.assertTrue(d._start_native_preview())
        self.wait(lambda: any(c[0] == 'open' for c in player._service.commands))
        self.gen = player._generation
        player._service.events.put(dict(type='live_ready', request_id=self.gen,
                                         sample_rate=48000, channels=2, duration_frames=480000))
        player.poll()

    def wait(self, predicate):
        deadline = time.perf_counter() + 2
        while not predicate() and time.perf_counter() < deadline:
            QT.processEvents()
            self.player.poll()
            time.sleep(.002)
        self.assertTrue(predicate())

    def tearDown(self):
        self.dialog._audition_poll_timer.stop()
        self.dialog._preview_debounce.stop()
        self.player.close()
        self.dialog._audition_cleanup_complete = True
        self.dialog._audition_service = None
        self.dialog.deleteLater()
        self.owner.deleteLater()
        QT.processEvents()

    def state(self, frame, **values):
        snapshot = dict(state='paused', sample_rate=48000, duration_frames=480000,
                        native=dict(media_frame=frame, sample_rate=48000, queued_frames=1024))
        snapshot.update(values)
        self.player._service.events.put(dict(type='live_state', request_id=self.gen, snapshot=snapshot))
        self.player.poll()

    def test_drag_does_not_seek_until_release_or_accept_old_position(self):
        d = self.dialog
        self.assertTrue(d.player_position.isEnabled())
        d._begin_player_seek()
        for value in range(1000, 7001, 20):
            d.player_position.setValue(value)
            d._seek_player_preview(value)
        self.assertFalse(any(c[0] == 'seek' for c in self.player._service.commands))
        d._finish_player_seek()
        self.wait(lambda: self.player._pending_seek['control_id'] is not None)
        self.state(0)
        self.assertEqual(d.player_position.value(), 7000)
        self.assertIsNotNone(d._pending_player_seek)
        identifier = self.player._pending_seek['control_id']
        self.state(336000, seek_control_id=identifier, last_seek=dict(frame=336000, phase='queued'))
        self.assertIsNone(d._pending_player_seek)
        self.assertEqual(d.player_position.value(), 7000)
        self.assertEqual(len([c for c in self.player._service.commands if c[0] == 'seek']), 1)

    def test_ab_and_parameter_changes_do_not_render_wav_or_replace_source(self):
        d = self.dialog
        with patch.object(d, '_start_live_preview_legacy') as old_render:
            d.play_preview('before')
            self.wait(lambda: self.player._pending_parameters is not None
                      and self.player._pending_parameters['control_id'] is not None)
            identifier = self.player._pending_parameters['control_id']
            self.assertEqual(self.player._generation, self.gen)
            self.assertEqual(d._request.call_args.kwargs['bypass_processing'], True)
            self.state(0, parameter_control_id=identifier, parameter_first_frame=0)
            self.assertTrue(d.preview_before_button.isChecked())
            self.assertEqual(len([c for c in self.player._service.commands if c[0] == 'open']), 1)
            old_render.assert_not_called()
            self.assertIsNone(d._preview_result)

    def test_failure_holds_target_and_explicit_play_reopens(self):
        d = self.dialog
        self.player._service.events.put(dict(type='error', request_id=self.gen, message='device lost'))
        self.player.poll()
        self.assertIn('点击播放重试', d.status.text())
        self.assertFalse(d._preview_play_intent)
        self.assertEqual(len([c for c in self.player._service.commands if c[0] == 'open']), 1)
        d._start_native_preview()
        self.assertEqual(len([c for c in self.player._service.commands if c[0] == 'open']), 1)
        d.toggle_preview_playback()
        self.wait(lambda: len([c for c in self.player._service.commands if c[0] == 'open']) == 2)
        self.assertNotEqual(self.gen, self.player._generation)

    def test_mode_switch_finishing_after_close_does_not_start_actor(self):
        d = self.dialog
        d._native_switching = True
        d._native_switch_result = (True, '')
        d._audition_closing = True
        with patch.object(d, '_shutdown_audition_service') as close_service, \
                patch('mixdown_realtime_player.RealtimePreviewPlayer') as create:
            d._finish_continuous_preview_switch()
            create.assert_not_called()
            close_service.assert_called_once()
        self.assertFalse(d._native_switching)


if __name__ == '__main__':
    unittest.main()
