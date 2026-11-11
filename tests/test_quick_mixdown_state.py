from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from mixdown_workspace_state import (
    TELEMETRY_SCHEMA,
    MixdownWorkspaceState,
    compression_controls,
    default_bands,
    eq_curve,
    meters_from_telemetry,
    reverb_curve_from_telemetry,
    spectrum_from_telemetry,
    strength_for_compressor,
    telemetry_identity,
)


class MixdownWorkspaceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / 'tests')
        self.root = Path(self.temp.name)
        self.vocal = self.root / 'vocals.wav'
        self.backing = self.root / 'instrumental.wav'
        self.vocal.write_bytes(b'RIFF-vocal-fixture')
        self.backing.write_bytes(b'RIFF-backing-fixture')
        self.output = self.root / 'mix.wav'

    def tearDown(self):
        self.temp.cleanup()

    def make_state(self):
        state = MixdownWorkspaceState(self.output)
        state.set_source('vocal-1', self.vocal)
        state.set_source('instrumental', self.backing)
        return state

    def test_request_uses_real_track_and_processing_fields(self):
        state = self.make_state()
        state.set_track('vocal-1', 'gain', -2.5)
        state.set_track('vocal-1', 'send', 0.7)
        state.set_track('instrumental', 'gain', -1.0)
        state.set_option('compressionEnabled', True)
        state.set_option('compressionStrength', 0.4)
        request = state.request(self.root / 'preview.wav')
        self.assertEqual(request['vocals'][0]['gain_db'], -2.5)
        self.assertEqual(request['vocals'][0]['reverb_send'], 0.7)
        self.assertEqual(request['instrumental_db'], -1.0)
        self.assertGreater(request['compression_strength'], 0.0)
        self.assertEqual(request['output_path'], str((self.root / 'preview.wav').resolve()))
        key, fingerprint = state.source_key(), state.render_fingerprint()
        state.set_sample_rate(44100)
        self.assertEqual(state.view(can_export=True, can_register=False, can_play=True)['sampleRate'], 44100)
        self.assertEqual(state.source_key(), key)
        self.assertEqual(state.render_fingerprint(), fingerprint)

    def test_monitor_volume_is_attenuation_and_backing_has_no_send(self):
        state = self.make_state()
        fingerprint = state.render_fingerprint()
        state.set_option('masterGain', 6)
        self.assertEqual(state.master_gain, 0)
        state.set_option('masterGain', -6)
        self.assertEqual(state.master_gain, -6)
        self.assertEqual(state.render_fingerprint(), fingerprint)
        with self.assertRaisesRegex(ValueError, '仅作用于歌声轨'):
            state.set_track('instrumental', 'send', 0.5)
        self.assertEqual(state.render_fingerprint(), fingerprint)

    def test_compressor_controls_are_a_derived_single_strength(self):
        derived = compression_controls(0.25)
        self.assertAlmostEqual(derived['threshold'], -11.0)
        self.assertAlmostEqual(derived['ratio'], 1.5)
        self.assertAlmostEqual(strength_for_compressor('compressionThreshold', -11.0), 0.25)
        self.assertAlmostEqual(strength_for_compressor('compressionRatio', 1.5), 0.25)
        self.assertAlmostEqual(strength_for_compressor('compressorAttack', 22.5), 0.25)
        self.assertAlmostEqual(strength_for_compressor('compressorRelease', 140.0), 0.25)

    def test_shared_eq_math_produces_normalized_qml_points(self):
        points = eq_curve(default_bands(), 48000, points=64)
        self.assertEqual(len(points), 64)
        self.assertEqual(points[0]['x'], 0.0)
        self.assertEqual(points[-1]['x'], 1.0)
        self.assertTrue(all(0.0 <= row['x'] <= 1.0 for row in points))
        self.assertTrue(all(isinstance(row['y'], float) for row in points))

    def test_task_stems_replace_mix_and_keep_verification_identity(self):
        job = self.root / 'job'
        audio = job / 'audio'
        audio.mkdir(parents=True)
        vocals = audio / 'lead.wav'
        backing = audio / 'instrumental.wav'
        vocals.write_bytes(b'lead')
        backing.write_bytes(b'back')
        state = MixdownWorkspaceState(self.output)
        state.load_task_sources(job, [
            {'id': 'vocal-1', 'label': '主唱分轨', 'path': str(vocals),
             'artifact': {'id': 'a', 'revision': 'r1', 'sha256': '1' * 64,
                          'provenance': 'task_separation'}},
            {'id': 'instrumental', 'label': '伴奏', 'path': str(backing),
             'artifact': {'id': 'b', 'revision': 'r2', 'sha256': '2' * 64,
                          'provenance': 'task_separation'}},
        ])
        self.assertEqual(state.job, str(job.resolve()))
        self.assertEqual(state.tracks[0]['name'], 'lead.wav')
        self.assertEqual([row['id'] for row in state.task_artifacts()], ['a', 'b'])
        self.assertNotEqual(Path(state.output), self.output)

    def test_frame_and_parameter_mismatch_is_rejected(self):
        snapshot = {'native': {'media_frame': 12}, 'parameter_revision': 3}
        telemetry = {
            'schema': TELEMETRY_SCHEMA, 'status': 'ready', 'source': 'realtime_dsp',
            'media_frame': 12, 'parameter_revision': 3, 'history_epoch': 7,
        }
        self.assertEqual(telemetry_identity(telemetry, snapshot), (12, 3, 7, 'realtime_dsp'))
        telemetry['media_frame'] = 11
        with self.assertRaisesRegex(ValueError, 'frame'):
            telemetry_identity(telemetry, snapshot)
        telemetry['media_frame'] = 12
        telemetry['parameter_revision'] = 2
        with self.assertRaisesRegex(ValueError, 'revision'):
            telemetry_identity(telemetry, snapshot)

    def test_measured_meters_spectrum_and_ir_curve_are_separate(self):
        telemetry = {
            'source': 'realtime_dsp',
            'tracks': [
                {'role': 'vocal', 'index': 0, 'rms_dbfs': -18.0, 'peak_dbfs': -9.0},
                {'role': 'backing', 'index': 0, 'rms_dbfs': -20.0, 'peak_dbfs': -11.0},
            ],
            'bus': {'limiter_gr_db': 1.5,
                    'final_output': {'rms_dbfs': -16.0, 'peak_dbfs': -1.1}},
            'dynamics': {'compressor': {'gr_db': 2.25}},
            'reverb': {
                'return_rms_dbfs': -30.0,
                'kernel_edc': {
                    'available': True, 'measured': True,
                    'time_seconds': [0.0, 0.5, 1.0],
                    'edc_db': [0.0, -20.0, -60.0],
                },
            },
            'spectrum': {
                'source': 'dsp_fft_pre_post_eq',
                'freq_hz': [100.0, 1000.0],
                'before_dbfs': [-30.0, -40.0],
                'after_dbfs': [-28.0, -39.0],
            },
        }
        meters = meters_from_telemetry(telemetry, ['vocal-1'])
        self.assertEqual([row['id'] for row in meters['lanes']], ['vocal-1', 'instrumental', 'master'])
        self.assertEqual(meters['gr'], 2.25)
        self.assertEqual(meters['limiter'], 1.5)
        self.assertEqual(meters['tail'], -30.0)
        spectrum = spectrum_from_telemetry(telemetry)
        self.assertEqual(spectrum['freqs'], [100.0, 1000.0])
        curve = reverb_curve_from_telemetry(telemetry)
        self.assertEqual(len(curve), 3)
        self.assertEqual(curve[0]['y'], 1.0)
        self.assertLess(curve[-1]['y'], 0.01)
        telemetry['reverb']['kernel_edc']['measured'] = False
        self.assertEqual(reverb_curve_from_telemetry(telemetry), [])


class QuickMixdownLifecycleTests(unittest.TestCase):
    def test_constructor_is_read_only_until_enter(self):
        try:
            from PySide6.QtCore import QCoreApplication
            from quick_mixdown import QuickMixdown
            from quick_work import WorkerLane
        except ImportError as error:
            self.skipTest(str(error))
        app = QCoreApplication.instance() or QCoreApplication([])
        worker = WorkerLane(app, 'quick-mixdown-lifecycle-test')
        controller = QuickMixdown(app, worker)
        try:
            self.assertFalse(controller._initialized)
            self.assertIsNone(controller._player)
            self.assertIsNone(controller._cache_dir)
            self.assertEqual(controller._support_lane.kind, '')
            self.assertFalse(controller.supportVisible)
            self.assertFalse(controller.view['componentMissing'])
            self.assertFalse(controller._poll_timer.isActive())
            controller._notice('登记被拒绝')
            self.assertEqual(controller.view['message'], '登记被拒绝')
        finally:
            controller.close()
            worker.close()


    def test_enter_owns_cache_without_using_shared_lane(self):
        try:
            from unittest.mock import patch
            from PySide6.QtCore import QCoreApplication
            from quick_mixdown import QuickMixdown
            from quick_work import WorkerLane
            from common import ROOT
        except ImportError as error:
            self.skipTest(str(error))
        app = QCoreApplication.instance() or QCoreApplication([])
        worker = WorkerLane(app, 'quick-mixdown-enter-test')
        controller = QuickMixdown(app, worker)
        try:
            with patch.object(controller, '_create_player'), patch.object(controller, '_start_support_check'):
                self.assertTrue(controller.enter())
            self.assertTrue(controller._initialized)
            self.assertEqual(worker.kind, '')
            self.assertIsNotNone(controller._cache_dir)
            self.assertTrue(Path(controller._cache_dir).is_relative_to(ROOT / 'cache' / 'mixdown-preview'))
            self.assertFalse(controller._support_lane.kind)
        finally:
            controller.close()
            worker.close()


    def test_accept_task_uses_only_exact_verified_separation_files(self):
        try:
            from unittest.mock import patch
            from PySide6.QtCore import QCoreApplication
            from quick_mixdown import QuickMixdown
            from quick_work import WorkerLane
        except ImportError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory(dir=Path.cwd() / 'tests') as temporary:
            job = Path(temporary)
            audio = job / 'audio'
            audio.mkdir()
            (job / 'request.json').write_text('{"voice_mode":"dual"}', encoding='utf-8')
            for name in ('source.wav', 'vocals.wav', 'lead.wav', 'backing.wav', 'instrumental.wav'):
                (audio / name).write_bytes(b'wav:' + name.encode('ascii'))
            rows = []
            for index, name in enumerate(('source.wav', 'vocals.wav', 'lead.wav', 'backing.wav', 'instrumental.wav')):
                rows.append({
                    'id': f'a{index}', 'revision': f'r{index}', 'sha256': str(index + 1) * 64,
                    'role': 'audio', 'state': 'verified', 'available': True,
                    'path': str(audio / name),
                })
            app = QCoreApplication.instance() or QCoreApplication([])
            worker = WorkerLane(app, 'quick-mixdown-task-test')
            controller = QuickMixdown(app, worker)
            try:
                with patch.object(controller, '_ensure_entered', return_value=True):
                    self.assertTrue(controller.accept_task(job, rows))
                names = [Path(row['path']).name for row in controller.tracks]
                self.assertEqual(names, ['lead.wav', 'backing.wav', 'instrumental.wav'])
                self.assertNotIn('source.wav', names)
                self.assertNotIn('vocals.wav', names)
            finally:
                controller.close()
                worker.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
