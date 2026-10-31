"""Full four-track real-audio acceptance for the simple mixdown workflow."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

DEPENDENCY_ENV = 'VWA_MIX_TEST_PEDALBOARD_PATH'
if not os.environ.get(DEPENDENCY_ENV):
    installed = APP / 'dependencies/optional/pedalboard/current/packages'
    if installed.is_dir():
        os.environ[DEPENDENCY_ENV] = str(installed)
    else:
        print('SKIP: ' + DEPENDENCY_ENV + ' is not set and Pedalboard is not installed.')
        raise SystemExit(0)

try:
    import numpy as np
    import soundfile as sf
    from PyQt5.QtWidgets import QApplication, QWidget
    import audio_mixdown_ui as ui
except Exception as error:
    print('ERROR: cannot import mixdown acceptance runtime: ' + str(error))
    raise SystemExit(1)


app = QApplication.instance() or QApplication([])


def _envelope(seconds, rate, spans):
    samples = np.arange(int(rate * seconds), dtype=np.float32) / rate
    gate = np.zeros_like(samples)
    for start, end, value in spans:
        gate[max(0, int(start * rate)):min(len(gate), int(end * rate))] = value
    return samples, gate


class FullSampleAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mixdown-full-', dir=APP / 'work')
        self.root = Path(self.temp.name)
        self.rate = 44100
        self.seconds = 12.0
        t, gate1 = _envelope(self.seconds, self.rate, ((0, 12, 1), (2.7, 4.1, .45), (8, 9, .35)))
        _, gate2 = _envelope(self.seconds, self.rate, ((0, 1.6, .4), (3, 7.8, 1), (9.2, 12, .7)))
        _, gate3 = _envelope(self.seconds, self.rate, ((0, .6, .2), (4.5, 10.5, 1)))
        vocal_signals = (
            (0.14 + 0.055 * np.sin(2 * np.pi * .17 * t)) * np.sin(
                2 * np.pi * 220 * t + .18 * np.sin(2 * np.pi * 5.2 * t)) * gate1,
            (0.13 + 0.045 * np.sin(2 * np.pi * .23 * t + .8)) * np.sin(
                2 * np.pi * 330 * t + .12 * np.sin(2 * np.pi * 4.4 * t)) * gate2,
            (0.12 + 0.04 * np.sin(2 * np.pi * .31 * t + 1.7)) * np.sin(
                2 * np.pi * 440 * t + .22 * np.sin(2 * np.pi * 6.1 * t)) * gate3,
        )
        self.vocals = []
        for index, signal in enumerate(vocal_signals, 1):
            path = self.root / ('vocal-%d.wav' % index)
            sf.write(path, np.stack((signal, signal * .93), axis=1).astype(np.float32),
                     self.rate, subtype='PCM_24')
            self.vocals.append(path)
        left = (.075 * np.sin(2 * np.pi * 110 * t) +
                .035 * np.sin(2 * np.pi * 220 * t) * (.5 + .5 * np.sin(2 * np.pi * .7 * t)) +
                .025 * np.sin(2 * np.pi * 880 * t))
        right = (.07 * np.sin(2 * np.pi * 113 * t + .4) +
                 .032 * np.sin(2 * np.pi * 330 * t) * (.5 + .5 * np.sin(2 * np.pi * .9 * t)) +
                 .022 * np.sin(2 * np.pi * 1320 * t))
        for hit in (1.4, 2.9, 4.6, 6.2, 8.1, 10.0):
            position = int(hit * self.rate)
            length = int(.06 * self.rate)
            decay = np.linspace(0, 1, length, dtype=np.float32)
            pulse = .13 * np.exp(-12 * decay) * np.sin(2 * np.pi * 72 * decay)
            left[position:position + length] += pulse
            right[position:position + length] += pulse * .8
        self.backing = self.root / 'backing.flac'
        sf.write(self.backing, np.stack((left, right), axis=1).astype(np.float32),
                 self.rate, format='FLAC', subtype='PCM_24')
        self.output = self.root / 'full-mix.wav'
        self.source_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (*self.vocals, self.backing)
        }

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _settle(rounds=6):
        for _ in range(rounds):
            app.processEvents()
            time.sleep(.003)

    def _wait_support(self, dialog, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
            if not dialog.support_checking() and dialog._audition_service_ready:
                self._settle()
                return
        self.fail('mixdown service did not become ready: ' +
                  dialog.support_state.text() + ' / ' + dialog.status.text())

    def _wait(self, dialog, timeout=45.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
            if (not dialog.support_checking() and not dialog.preview_busy()
                    and not dialog._preview_debounce.isActive()
                    and not dialog._preview_dirty):
                self._settle()
                return
        self.fail('mixdown did not settle: ' + dialog.preview_report.text() +
                  ' / ' + dialog.status.text())

    def _wait_change(self, dialog, action, timeout=45.0):
        old = (dialog._preview_result or {}).get('after', {}).get('output_path')
        started = time.monotonic()
        action()
        deadline = started + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.004)
            current = (dialog._preview_result or {}).get('after', {}).get('output_path')
            if (not dialog.preview_busy() and not dialog._preview_debounce.isActive()
                    and not dialog._preview_dirty and current and current != old):
                self._settle()
                return (time.monotonic() - started) * 1000.0
        self.fail('mixdown change did not settle: ' + dialog.preview_report.text())

    def test_four_track_sample_full_workflow(self):
        owner = QWidget()
        owner.show()
        ui._OPTIONAL_PACKAGES = str(
            Path(os.environ[DEPENDENCY_ENV]).resolve())
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
        dialog.show()
        try:
            self._settle()
            self._wait_support(dialog)
            # Opening the page starts the resident worker before files are chosen.
            self.assertTrue(dialog._audition_service_ready)
            self.assertIsNotNone(dialog._audition_service)
            self.assertTrue(dialog._audition_poll_timer.isActive())

            dialog.fields['vocal'].setText(str(self.vocals[0]))
            dialog.fields['instrumental'].setText(str(self.backing))
            dialog.add_vocal_track()
            dialog.add_vocal_track()
            for index, path in enumerate(self.vocals):
                dialog.vocal_tracks[index]['path'].setText(str(path))
                dialog.vocal_tracks[index]['gain_db'].setValue(6.0)
            dialog.preview_duration.setValue(self.seconds)
            dialog.preview_start.setValue(0.0)
            self._wait(dialog)
            self.assertTrue(dialog._preview_result, dialog.preview_report.text())
            initial = dialog._last_audition_report or {}
            self.assertEqual(initial.get('requested_variant'), 'both')
            self.assertIn('before', initial)
            self.assertIn('after', initial)
            before_file = Path(initial['before']['output_path'])
            after_file = Path(initial['after']['output_path'])
            before_audio, before_rate = sf.read(str(before_file), always_2d=True)
            after_audio, after_rate = sf.read(str(after_file), always_2d=True)
            self.assertEqual(before_rate, self.rate)
            self.assertEqual(after_rate, self.rate)
            self.assertEqual(before_audio.shape, after_audio.shape)
            self.assertLess(float(np.max(np.abs(after_audio - before_audio))), 1e-7)
            self.assertEqual(sf.info(str(after_file)).subtype, 'FLOAT')
            self.assertEqual(initial['after']['output_encoding']['writer'], 'ffmpeg')
            self.assertLessEqual(float(np.max(np.abs(after_audio))), .89125094 + 1e-4)

            for control in (dialog.eq_enabled, dialog.compression_enabled,
                            dialog.reverb_enabled):
                control.setChecked(True)
            self._wait(dialog)
            after_file = Path(dialog._preview_result['after']['output_path'])
            processed, _ = sf.read(str(after_file), always_2d=True)
            self.assertGreater(float(np.max(np.abs(processed - before_audio))), 1e-4)

            self._wait_change(dialog, lambda: dialog.eq_band_controls.rows[2]['gain'].setValue(5.0))
            self.assertEqual(dialog._last_audition_report['requested_variant'], 'after')
            self._wait_change(dialog, lambda: dialog.compression.setValue(60.0))
            gr = float(dialog._last_audition_report['after']['gr_db_p95'])
            self.assertGreater(gr, .05)
            self._wait_change(dialog, lambda: dialog.reverb.setValue(30.0))
            self._wait_change(dialog, lambda: dialog.reverb_predelay.setValue(25))
            edc = dialog._last_audition_report.get('reverb_edc') or {}
            self.assertTrue(edc.get('measured'))
            self.assertEqual(float(edc.get('predelay_ms')), 25.0)

            latencies = {}
            for label, action in (
                    ('vocal_gain', lambda: dialog.vocal_tracks[0]['gain_db'].setValue(-4.0)),
                    ('vocal_pan', lambda: dialog.vocal_tracks[0]['pan'].setValue(-75)),
                    ('vocal_mute', lambda: dialog.vocal_tracks[1]['mute'].setChecked(True)),
                    ('vocal_solo', lambda: dialog.vocal_tracks[2]['solo'].setChecked(True)),
                    ('backing_pan', lambda: dialog.instrumental_pan.setValue(45)),
                    ('width', lambda: dialog.width_control.set_value(0.0))):
                latencies[label] = self._wait_change(dialog, action)
                self.assertEqual(dialog._last_audition_report['requested_variant'], 'both')
            mono, _ = sf.read(str(dialog._preview_result['after']['output_path']),
                              always_2d=True)
            self.assertLess(float(np.max(np.abs(mono[:, 0] - mono[:, 1]))), 3e-3)
            self.assertTrue(all(value < 45_000 for value in latencies.values()), latencies)
        finally:
            while dialog.busy() or dialog.support_checking() or dialog.preview_busy():
                app.processEvents()
                time.sleep(.01)
            dialog.close()
            deadline = time.monotonic() + 8
            while not dialog._audition_cleanup_complete and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            self.assertTrue(dialog._audition_cleanup_complete)
            dialog.deleteLater()
            owner.close()
            owner.deleteLater()
            self._settle(10)

    def test_preset_export_and_source_immutability(self):
        owner = QWidget()
        owner.show()
        ui._OPTIONAL_PACKAGES = str(Path(os.environ[DEPENDENCY_ENV]).resolve())
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
        dialog.show()
        try:
            self._settle()
            self._wait_support(dialog)
            dialog.fields['vocal'].setText(str(self.vocals[0]))
            dialog.fields['instrumental'].setText(str(self.backing))
            dialog.add_vocal_track()
            dialog.add_vocal_track()
            for index, path in enumerate(self.vocals):
                dialog.vocal_tracks[index]['path'].setText(str(path))
            dialog.preview_duration.setValue(6.0)
            self._wait(dialog)
            self.assertTrue(dialog.apply_mix_preset('clear_lead', lock_mix=True))
            self._wait(dialog)
            self.assertTrue(dialog.eq_enabled.isChecked())
            self.assertEqual(len(dialog.eq_band_controls.rows), 4)
            dialog.fields['output'].setText(str(self.output))
            dialog.export()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and dialog.busy():
                app.processEvents()
                time.sleep(.01)
            self.assertFalse(dialog.busy(), dialog.status.text())
            self.assertTrue(self.output.is_file())
            info = sf.info(str(self.output))
            self.assertEqual(info.samplerate, self.rate)
            self.assertEqual(info.channels, 2)
            self.assertGreaterEqual(info.frames, self.rate * 5)
            self.assertEqual(info.subtype, 'PCM_24')
            receipt = dialog._export_receipt
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt['schema'], 'vwa.audio_mixdown.receipt/v2')
            self.assertEqual(len(receipt['inputs']['vocals']), 3)
            for path in (*self.vocals, self.backing):
                self.assertEqual(
                    self.source_hashes[path.name],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        finally:
            while dialog.busy() or dialog.support_checking() or dialog.preview_busy():
                app.processEvents()
                time.sleep(.01)
            dialog.close()
            deadline = time.monotonic() + 8
            while not dialog._audition_cleanup_complete and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            self.assertTrue(dialog._audition_cleanup_complete)
            dialog.deleteLater()
            owner.close()
            owner.deleteLater()
            self._settle(10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
