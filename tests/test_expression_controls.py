"""Shared expression boundaries and channel-aware acoustic cache compatibility."""
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import numpy as np
from expression_controls import (CONTROL_SPECS, STYLE_CONTROL_NAMES,
                                 adaptation_controls, calibration_controls,
                                 control_limits, neutral_values)
import vocal_timbre


class ExpressionBoundaryTests(unittest.TestCase):
    def test_consumers_keep_existing_units_neutrals_and_distinct_step_policies(self):
        import context_adapter
        import expression_calibration
        import style_presets
        import vocal_expression
        legacy = {
            'loudness': (-48., 12., 0.), 'tension': (-1., 1., 0.),
            'breathiness': (-1., 1., 0.), 'voicing': (0., 1., 1.),
            'gender': (-1., 1., 0.), 'toneShift': (-800., 800., 0.),
        }
        self.assertEqual(control_limits(), legacy)
        self.assertEqual(style_presets.CONTROLS, {name: legacy[name] for name in STYLE_CONTROL_NAMES})
        self.assertEqual(tuple(vocal_expression.CONTROL_NAMES), STYLE_CONTROL_NAMES)
        self.assertEqual(vocal_timbre.NEUTRAL, {'tension': 0., 'breathiness': 0., 'voicing': 1.})
        for name, spec in CONTROL_SPECS.items():
            self.assertEqual(expression_calibration.CONTROLS[name][:3], legacy[name])
            self.assertLessEqual(spec.minimum, spec.neutral)
            self.assertLessEqual(spec.neutral, spec.maximum)
            self.assertGreaterEqual(spec.neutral + spec.calibration_step, spec.minimum)
            self.assertLessEqual(spec.neutral + spec.calibration_step, spec.maximum)
        self.assertEqual(expression_calibration.CONTROLS['voicing'][3], -.2)
        self.assertEqual(context_adapter.CONTROLS['voicing']['step'], .04)
        self.assertEqual(expression_calibration.CONTROLS['tension'][3], .3)
        self.assertEqual(context_adapter.CONTROLS['tension']['step'], .08)
        self.assertEqual(set(context_adapter.CONTROLS), set(STYLE_CONTROL_NAMES))

    def test_projections_are_independent_and_canonical_specs_are_immutable(self):
        fit = calibration_controls()
        fit['voicing'] = (-1., 1., 0., 1.)
        adaptation = adaptation_controls()
        adaptation['voicing']['neutral'] = 0.
        self.assertEqual(calibration_controls()['voicing'], (0., 1., 1., -.2))
        self.assertEqual(adaptation_controls()['voicing']['neutral'], 1.)
        self.assertEqual(neutral_values(('voicing',)), {'voicing': 1.})
        with self.assertRaises(TypeError):
            CONTROL_SPECS['voicing'] = CONTROL_SPECS['tension']
        with self.assertRaises(FrozenInstanceError):
            CONTROL_SPECS['voicing'].neutral = 0.

    def test_registry_import_does_not_load_gui_or_acoustic_engines(self):
        code = ('import sys;sys.path.insert(0,sys.argv[1]);import expression_controls;'
                'assert not any(x in sys.modules for x in '
                '("numpy","PyQt5","style_presets","expression_calibration"))')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(APP / 'src')],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


class ChannelCacheTests(unittest.TestCase):
    def setUp(self):
        (APP / 'work').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='channel-cache-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / 'reference.wav'
        self.audio.write_bytes(b'owned reference identity; extractor is replaced')
        self.cache = self.root / 'features.npz'
        self.notes = [(0., 1., 60.)]
        self.frames = np.zeros((3, 7), dtype=np.float64)
        self.frames[:, 0] = (0., .02, .04)

    def test_explicit_layouts_never_reuse_other_layout_features(self):
        calls = []

        def extract(path, notes, cancel, *, channel_mode='standard'):
            calls.append(channel_mode)
            data = self.frames.copy()
            data[:, 1] = {'standard': 1., 'sv-extra': 2., 'sv-isolated': 3.}[channel_mode]
            return data, {'analyzer': 'fixture'}

        with patch.object(vocal_timbre, 'feature_frames', side_effect=extract):
            for mode, expected in (('standard', 1.), ('sv-extra', 2.), ('sv-isolated', 3.)):
                cold, metadata = vocal_timbre.cached_features(self.audio, self.notes, self.cache,
                                                             channel_mode=mode)
                self.assertFalse(metadata['cache_hit'])
                self.assertEqual(metadata['channel_mode'], mode)
                self.assertTrue(np.all(cold[:, 1] == expected))
                warm, metadata = vocal_timbre.cached_features(self.audio, self.notes, self.cache,
                                                             channel_mode=mode)
                self.assertTrue(metadata['cache_hit'])
                np.testing.assert_array_equal(warm, cold)
        self.assertEqual(calls, ['standard', 'sv-extra', 'sv-isolated'])

    def test_legacy_standard_cache_remains_warm_without_reanalysis(self):
        from common import file_hash
        from expression_component import SPEC, __file__ as loader_file
        # Frozen schema-2 layout from the pre-registry implementation. It had
        # no channel field because its public cache only analyzed standard WAV.
        legacy_identity = {
            'schema': 2, 'audio': file_hash(self.audio), 'analyzer': SPEC['sha256'],
            'extractor': vocal_timbre._feature_code_signature(),
            'loader': file_hash(Path(loader_file)),
            'notes': [[float(a), float(b), float(p)] for a, b, p in self.notes],
        }
        signature = hashlib.sha256(json.dumps(legacy_identity, sort_keys=True,
                                              allow_nan=False).encode()).hexdigest()
        np.savez_compressed(self.cache, signature=signature, frames=self.frames,
                            metadata=json.dumps({'analyzer': 'legacy fixture'}))
        with patch.object(vocal_timbre, 'feature_frames', side_effect=AssertionError('legacy cache reran')):
            _, metadata = vocal_timbre.cached_features(self.audio, self.notes, self.cache)
        self.assertTrue(metadata['cache_hit'])
        self.assertEqual(metadata['channel_mode'], 'standard')
        with patch.object(vocal_timbre, 'feature_frames', return_value=(self.frames, {})) as extract:
            _, metadata = vocal_timbre.cached_features(self.audio, self.notes, self.cache,
                                                       channel_mode='sv-extra')
        self.assertFalse(metadata['cache_hit'])
        self.assertEqual(extract.call_args.kwargs['channel_mode'], 'sv-extra')

    def test_invalid_mode_and_cancelled_analysis_preserve_readable_cache(self):
        with patch.object(vocal_timbre, 'feature_frames', return_value=(self.frames, {})):
            vocal_timbre.cached_features(self.audio, self.notes, self.cache)
        saved = self.cache.read_bytes()
        with self.assertRaises(ValueError):
            vocal_timbre.cached_features(self.audio, self.notes, self.cache, channel_mode='guess')
        with patch.object(vocal_timbre, 'feature_frames', side_effect=InterruptedError('cancelled')):
            with self.assertRaises(InterruptedError):
                vocal_timbre.cached_features(self.audio, self.notes, self.cache, channel_mode='sv-extra')
        self.assertEqual(self.cache.read_bytes(), saved)
        self.assertEqual(list(self.root.glob('*.pending')), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
