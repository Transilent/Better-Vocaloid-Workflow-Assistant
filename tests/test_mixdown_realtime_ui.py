"""Real mixer widgets wired to one continuous transport, with a fake service.

No native audio device is opened here.  ``Service`` records the transport
commands and injects state snapshots so QWidget interactions can be exercised
deterministically.  Real WASAPI output remains an integration-test concern.
"""
import os
from pathlib import Path
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(0, str(APP / 'tests'))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QKeyEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QWidget

import audio_mixdown_ui as ui
from mixdown_realtime_player import PlaybackState, RealtimePreviewPlayer
from test_mixdown_realtime_player import Service

QT = QApplication.instance() or QApplication([])


def settle():
    for _ in range(4):
        QT.processEvents()
        QTest.qWait(2)


class Probe:
    sample_rate = 48000


class MixerRoutingTests(unittest.TestCase):
    def setUp(self):
        (APP / 'work').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(APP / 'work'))
        self.owner = QWidget()
        self.players = []

        def create_player(*args, **kwargs):
            kwargs['service_factory'] = Service
            player = RealtimePreviewPlayer(*args, **kwargs)
            self.players.append(player)
            return player

        with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
                patch.object(ui.AudioMixdownDialog, '_start_component_support_check'), \
                patch('mixdown_realtime_player.native_output_path',
                      return_value=Path('fixture-native-output.dll')), \
                patch('mixdown_realtime_player.RealtimePreviewPlayer',
                      side_effect=create_player):
            self.dialog = ui.AudioMixdownDialog(
                self.owner, None, self.owner, embedded=False)

        self.addCleanup(self.dispose)
        self.vocal = self._wav('vocal.wav')
        self.instrumental = self._wav('instrumental.wav')
        self.output = Path(self.temp.name) / 'mix.wav'
        self.probe = patch.object(
            ui._mixdown_backend, '_probe_audio', return_value=Probe())
        self.probe.start()
        self.addCleanup(self.probe.stop)

        d = self.dialog
        d._support_ready = False
        d.support_panel.hide()
        d._syncing = True
        try:
            d.fields['vocal'].setText(str(self.vocal))
            d.fields['instrumental'].setText(str(self.instrumental))
            d.fields['output'].setText(str(self.output))
            d.vocal_tracks[0]['gain_db'].setValue(1.5)
            d.compression_enabled.setChecked(True)
        finally:
            d._syncing = False
        d._support_ready = True
        d._update_source_states()
        d._update_output_state()
        d._sync_controls()
        d.resize(1440, 900)
        d.show()
        settle()
        d._preview_debounce.stop()
        d._main_scroll.ensureWidgetVisible(d.preview_card)
        settle()
        self.player = d._player

    def dispose(self):
        d = self.dialog
        d._audition_poll_timer.stop()
        d._preview_debounce.stop()
        d._player_seek_timer.stop()
        d._audition_closing = True
        d._audition_cleanup_complete = True
        d._audition_service = None
        for player in reversed(self.players):
            if player.is_alive:
                player.close()
        d.hide()
        d.deleteLater()
        self.owner.deleteLater()
        settle()
        self.temp.cleanup()

    def _wav(self, name):
        path = Path(self.temp.name) / name
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b'\0\0' * 4800)
        return path

    def click(self, widget):
        self.dialog._main_scroll.ensureWidgetVisible(widget)
        settle()
        self.assertTrue(widget.isVisible(), widget.objectName())
        self.assertTrue(widget.isEnabled(), widget.objectName())
        QTest.mouseClick(widget, Qt.LeftButton)
        settle()

    def wait(self, predicate, timeout=2.0):
        deadline = time.perf_counter() + timeout
        while not predicate() and time.perf_counter() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), repr(self.player._service.commands))

    def commands(self, kind):
        return [row for row in self.player._service.commands if row[0] == kind]

    def emit_state(self, frame, **values):
        snapshot = dict(
            state='playing', sample_rate=48000, duration_frames=480000,
            native=dict(media_frame=frame, sample_rate=48000,
                        queued_frames=1024))
        snapshot.update(values)
        self.player._service.events.put(dict(
            type='live_state', request_id=self.player._generation,
            snapshot=snapshot))

    def open_session(self):
        self.assertIs(self.dialog._player, self.player)
        self.click(self.dialog.preview_after_button)
        self.wait(lambda: self.commands('open'))
        generation = self.player._generation
        self.player._service.events.put(dict(
            type='live_ready', request_id=generation, sample_rate=48000,
            channels=2, duration_frames=480000))
        self.wait(lambda: self.player.ready and self.player.duration() == 10000)
        self.emit_state(96000)
        self.wait(lambda: self.dialog.player_position.value() == 2000)
        return generation

    def test_hide_show_keeps_transport_and_allows_real_button_pause_resume(self):
        d = self.dialog
        generation = self.open_session()
        self.assertTrue(d._audition_poll_timer.isActive())
        self.assertTrue(d._player.play_intent)
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(d.player_position.value(), 2000)

        d.hide()
        settle()
        self.assertTrue(d._audition_poll_timer.isActive())
        self.assertEqual(self.player._generation, generation)
        self.assertTrue(d._player.play_intent)
        self.emit_state(144000)
        self.wait(lambda: self.player.position() == 3000)
        self.assertEqual(d.player_position.value(), 3000)

        d.show()
        settle()
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(self.player.position(), 3000)
        self.assertEqual(d.player_position.value(), 3000)
        self.assertTrue(d._player.play_intent)
        self.assertTrue(d._audition_poll_timer.isActive())
        self.assertEqual(len(self.commands('open')), 1)
        self.assertFalse(any(row[0] == 'release' for row in self.player._service.commands))

        self.click(d.player_toggle_button)
        self.wait(lambda: self.commands('pause'))
        self.assertFalse(d._player.play_intent)
        self.emit_state(144000, state='paused')
        self.wait(lambda: d.player_toggle_button.text() == ui._tr('播放'))
        self.assertEqual(self.player._generation, generation)

        self.click(d.player_toggle_button)
        self.wait(lambda: self.commands('play'))
        self.assertTrue(d._player.play_intent)
        self.emit_state(168000)
        self.wait(lambda: d.player_toggle_button.text() == ui._tr('暂停'))
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(len(self.commands('open')), 1)

    def test_real_slider_drag_submits_one_seek_and_waits_for_matching_ack(self):
        d = self.dialog
        generation = self.open_session()
        slider = d.player_position
        self.assertTrue(slider.isVisible())
        self.assertEqual(slider.maximum(), 10000)
        before = len(self.commands('seek'))
        width = slider.width()
        start = QPoint(int(width * .22), slider.height() // 2)
        end = QPoint(int(width * .78), slider.height() // 2)

        QTest.mousePress(slider, Qt.LeftButton, pos=start)
        settle()
        for fraction in (.34, .51, .68, .78):
            point = QPoint(int(width * fraction), slider.height() // 2)
            event = QMouseEvent(
                QEvent.MouseMove, QPointF(point), Qt.NoButton,
                Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(slider, event)
            settle()
        self.assertEqual(len(self.commands('seek')), before)
        self.assertTrue(slider.isSliderDown())

        QTest.mouseRelease(slider, Qt.LeftButton, pos=end)
        self.wait(lambda: len(self.commands('seek')) == before + 1)
        self.wait(lambda: self.player._pending_seek is not None
                  and self.player._pending_seek['control_id'] is not None)
        target = slider.value()
        identifier = self.player._pending_seek['control_id']

        self.emit_state(48000, seek_control_id='stale',
                        last_seek=dict(frame=48000, phase='submitted'))
        settle()
        self.assertEqual(slider.value(), target)
        self.assertIsNotNone(d._preview.pending_seek)
        self.assertIsNotNone(self.player._pending_seek)

        frame = int(round(target * 48000 / 1000))
        self.emit_state(frame, seek_control_id=identifier,
                        last_seek=dict(frame=frame, phase='submitted'))
        self.wait(lambda: self.player._pending_seek is None)
        self.wait(lambda: d._preview.pending_seek is None)
        self.assertEqual(slider.value(), target)
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(len(self.commands('seek')), before + 1)
        self.assertEqual(len(self.commands('open')), 1)

    def test_pause_then_edit_and_seek_do_not_restore_stale_play_intent(self):
        d = self.dialog
        self.open_session()
        self.click(d.player_toggle_button)
        self.assertFalse(d._player.play_intent)
        # No paused state has arrived yet: the last backend snapshot still
        # says playing. UI edits must honor the user's newer pause command.
        d.vocal_tracks[0]['gain_db'].setValue(-5)
        self.assertFalse(d._player.play_intent)
        d._seek_to_song_position(4000, commit=True)
        self.wait(lambda: bool(self.commands('seek')))
        self.assertFalse(self.commands('seek')[-1][2][1])
        self.assertFalse(d._player.play_intent)

    def test_background_ir_finishes_at_the_same_paused_media_frame(self):
        d = self.dialog
        self.open_session()
        self.click(d.player_toggle_button)
        telemetry = dict(schema='vwa.audio_mixdown.realtime-telemetry/v1',
                         source='realtime_dsp',status='ready',sample_rate=48000,
                         media_frame=96000,position_ms=2000,duration_ms=10000,
                         history_epoch=1,parameter_revision=1,tracks=[],bus={},
                         spectrum=None,reverb=dict(kernel_edc=dict(available=False)))
        self.emit_state(96000,state='paused',telemetry=telemetry)
        self.wait(lambda:d._live_measurement_frame is not None)
        self.assertFalse(d.reverb_decay.curve_points())
        telemetry['reverb']['kernel_edc'] = dict(available=True,measured=True,
            profile='natural',sample_rate=48000,predelay_ms=0,
            time_seconds=[0,1],edc_db=[0,-48])
        self.emit_state(96000,state='paused',telemetry=telemetry)
        self.wait(lambda:len(d.reverb_decay.curve_points())==2)

    def test_space_toggles_once_from_controls_and_does_not_repeat(self):
        d = self.dialog
        generation = self.open_session()
        d.activateWindow()
        d.player_loop_button.setFocus()
        initial_loop = d.player_loop_button.isChecked()
        pauses = len(self.commands('pause'))
        QTest.keyClick(d.player_loop_button, Qt.Key_Space)
        self.wait(lambda: len(self.commands('pause')) == pauses + 1)
        self.assertFalse(d._player.play_intent)
        self.assertEqual(d.player_loop_button.isChecked(), initial_loop)
        QApplication.sendEvent(d.player_loop_button,
            QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, ' ', True, 1))
        settle()
        self.assertFalse(d._player.play_intent)
        self.assertEqual(len(self.commands('pause')), pauses + 1)
        self.emit_state(96000, state='paused')
        settle()
        plays = len(self.commands('play'))
        d.player_position.setFocus()
        QTest.keyClick(d.player_position, Qt.Key_Space)
        self.wait(lambda: len(self.commands('play')) == plays + 1)
        self.assertTrue(d._player.play_intent)
        self.assertEqual(self.player._generation, generation)

    def test_space_preserves_text_modal_and_other_page_input(self):
        d = self.dialog
        self.open_session()
        d.activateWindow()
        editor = d.fields['output']
        editor.setFocus()
        editor.setCursorPosition(len(editor.text()))
        before = editor.text()
        QTest.keyClick(editor, Qt.Key_Space)
        self.assertEqual(editor.text(), before + ' ')
        self.assertTrue(d._player.play_intent)
        d.compression.setFocus()
        QTest.keyClick(d.compression, Qt.Key_Space)
        self.assertTrue(d._player.play_intent)
        modal = QDialog(d)
        modal.setModal(True)
        field = QLineEdit(modal)
        modal.show();field.setFocus();settle()
        QTest.keyClick(field, Qt.Key_Space)
        self.assertEqual(field.text(), ' ')
        self.assertTrue(d._player.play_intent)
        modal.close();modal.deleteLater();settle()
        other = QLineEdit()
        other.show();other.setFocus();d.hide();settle()
        QTest.keyClick(other, Qt.Key_Space)
        self.assertEqual(other.text(), ' ')
        self.assertTrue(d._player.play_intent)
        other.close();other.deleteLater()

    def test_meter_updates_do_not_resolve_audio_files(self):
        d = self.dialog
        frame = dict(tracks=[dict(role='vocal', index=0, peak_dbfs=-6, rms_dbfs=-12)],
                     bus=dict(peak_dbfs=-4, rms_dbfs=-10))
        with patch.object(Path, 'resolve', side_effect=AssertionError('Filesystem access in meter refresh')):
            for _ in range(20):
                d._apply_live_meter_snapshot(frame)
        self.assertIn('-4.0', d.bus_peak_value.text())

    def test_ab_buttons_update_same_session_and_wire_real_request_values(self):
        d = self.dialog
        generation = self.open_session()
        opens = self.commands('open')
        self.assertEqual(len(opens), 1)
        opened = opens[0][2]
        self.assertEqual(Path(opened['vocals'][0]['path']), self.vocal.resolve())
        self.assertEqual(Path(opened['instrumental_path']), self.instrumental.resolve())
        self.assertEqual(opened['vocals'][0]['gain_db'], 1.5)
        self.assertGreater(opened['compression_strength'], 0)

        self.click(d.preview_before_button)
        self.wait(lambda: len(self.commands('parameters')) == 1)
        self.wait(lambda: self.player._pending_parameters is not None
                  and self.player._pending_parameters['control_id'] is not None)
        before_request = self.commands('parameters')[0][2]
        self.assertEqual(Path(before_request['vocals'][0]['path']), self.vocal.resolve())
        self.assertEqual(Path(before_request['instrumental_path']), self.instrumental.resolve())
        self.assertEqual(before_request['compression_strength'], 0.0)
        before_id = self.player._pending_parameters['control_id']
        self.emit_state(1000, parameter_control_id=before_id,
                        parameter_first_frame=0)
        self.wait(lambda: self.player.selected_key == 'before'
                  and self.player._pending_parameters is None)
        self.assertTrue(d.preview_before_button.isChecked())
        self.assertFalse(d.preview_after_button.isChecked())
        self.assertEqual(self.player._generation, generation)

        self.click(d.preview_after_button)
        self.wait(lambda: len(self.commands('parameters')) == 2)
        self.wait(lambda: self.player._pending_parameters is not None
                  and self.player._pending_parameters['control_id'] is not None)
        after_request = self.commands('parameters')[1][2]
        self.assertEqual(Path(after_request['vocals'][0]['path']), self.vocal.resolve())
        self.assertGreater(after_request['compression_strength'], 0)
        after_id = self.player._pending_parameters['control_id']
        self.emit_state(1500, parameter_control_id=after_id,
                        parameter_first_frame=1000)
        self.wait(lambda: self.player.selected_key == 'after'
                  and self.player._pending_parameters is None)
        self.assertTrue(d.preview_after_button.isChecked())
        self.assertFalse(d.preview_before_button.isChecked())
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(len(self.commands('open')), 1)
        self.assertIs(d._player, d._preview.player)

    def test_device_failure_requires_explicit_button_retry_in_same_player(self):
        d = self.dialog
        generation = self.open_session()
        self.player._service.events.put(dict(
            type='error', request_id=generation, message='device lost'))
        self.wait(lambda: self.player.failed)
        self.assertIn('点击播放重试', d.status.text())
        self.assertFalse(d._player.play_intent)
        self.assertEqual(len(self.commands('open')), 1)
        self.assertIs(d._player, d._preview.player)

        self.click(d.player_toggle_button)
        self.wait(lambda: len(self.commands('open')) == 2)
        self.assertNotEqual(self.player._generation, generation)
        self.assertIs(d._player, d._preview.player)


if __name__ == '__main__':
    unittest.main()
