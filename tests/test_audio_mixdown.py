"""Behavioral isolation contracts for the simple stereo audio mixdown."""

from __future__ import annotations

import copy
import hashlib
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

APP = Path(__file__).resolve().parents[1]
DEPENDENCY_ENV = 'VWA_MIX_TEST_PEDALBOARD_PATH'

if not os.environ.get(DEPENDENCY_ENV):
    installed = APP / 'dependencies/optional/pedalboard/current/packages'
    if installed.is_dir():
        os.environ[DEPENDENCY_ENV] = str(installed)
    else:
        print('SKIP: ' + DEPENDENCY_ENV + ' is not set and the optional Pedalboard component is not installed.')
        raise SystemExit(0)

if not (APP / 'src/audio_mixdown.py').is_file():
    print('ERROR: src/audio_mixdown.py is missing; the shared render(request, cancel=None) interface is unavailable.')
    raise SystemExit(1)

import numpy as np
import soundfile as sf


def _pedalboard_import_root(value):
    root = Path(value).expanduser()
    if not root.exists():
        raise RuntimeError(DEPENDENCY_ENV + ' does not exist: ' + str(root))
    candidates = [root]
    if root.is_dir():
        candidates.extend((root / 'unpacked', root / 'site-packages'))
        candidates.extend(sorted(path for path in root.iterdir() if path.is_dir()))
    for candidate in candidates:
        if (candidate / 'pedalboard').is_dir():
            return candidate.resolve()
        if (candidate / 'pedalboard.py').is_file():
            return candidate.resolve()
    raise RuntimeError(DEPENDENCY_ENV + ' must identify an existing importable Pedalboard package')


try:
    pedalboard_root = _pedalboard_import_root(os.environ[DEPENDENCY_ENV])
except RuntimeError as error:
    print('ERROR: ' + str(error))
    raise SystemExit(1)

sys.path[:0] = [str(pedalboard_root), str(APP / 'src')]
try:
    import pedalboard  # noqa: F401 - verifies the explicitly selected dependency without installing it
    import audio_mixdown
except Exception as error:
    print('ERROR: cannot import the isolated mixing dependency: ' + type(error).__name__ + ': ' + str(error))
    raise SystemExit(1)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tone(seconds, rate, frequency, amplitude, channels=1, start=0.):
    samples = int(round(seconds * rate))
    start_sample = int(round(start * rate))
    time = (np.arange(samples, dtype=np.float64) - start_sample) / rate
    signal = np.where(time >= 0, amplitude * np.sin(2 * np.pi * frequency * time), 0.)
    signal = signal.astype(np.float32)
    return np.repeat(signal[:, None], channels, axis=1) if channels > 1 else signal


def _write(path, audio, rate):
    sf.write(str(path), np.asarray(audio, dtype=np.float32), int(rate), subtype='FLOAT')


def _read(path):
    audio, rate = sf.read(str(path), always_2d=True, dtype='float32')
    if not np.isfinite(audio).all():
        raise AssertionError('render wrote NaN or infinity to ' + str(path))
    return audio, int(rate)


def _rms(audio):
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def _spectral_amplitude(audio, rate, frequency):
    values = np.asarray(audio, dtype=np.float64)
    mono = values.mean(axis=1) if values.ndim == 2 else values
    windowed = mono * np.hanning(mono.size)
    spectrum = np.fft.rfft(windowed)
    index = int(round(frequency * mono.size / rate))
    return float(abs(spectrum[index]))


def _request(vocal, instrumental, output, *, offset=0., reverb=0., vocal_db=0.,
             instrumental_db=0., vocals=None):
    request = {
        'instrumental_path': str(instrumental),
        'output_path': str(output),
        'instrumental_db': instrumental_db,
        'eq_low_cut_hz': 0,
        'eq_presence_db': 0,
        'compression_strength': 0,
        'reverb_mix': reverb,
    }
    if vocals is None:
        request.update({
            'vocal_path': str(vocal),
            'vocal_db': vocal_db,
            'offset_seconds': offset,
        })
    else:
        request['vocals'] = vocals
    return request


def _verify_result(test, report, request, root):
    test.assertIsInstance(report, dict)
    for key in ('schema', 'output_path', 'duration_seconds', 'peak_dbfs',
                'sample_rate', 'channels', 'tracks', 'source_sha256', 'output_sha256'):
        test.assertIn(key, report)
    test.assertEqual(report['schema'], 'vwa.audio_mixdown.render/v2')
    output = Path(report['output_path']).resolve()
    test.assertEqual(output, Path(request['output_path']).resolve())
    test.assertTrue(output.is_file())
    audio, rate = _read(output)
    duration = len(audio) / rate
    test.assertTrue(math.isfinite(float(report['duration_seconds'])))
    test.assertAlmostEqual(float(report['duration_seconds']), duration, delta=0.03)
    sample_peak = float(np.max(np.abs(audio), initial=0.))
    db = 20 * math.log10(sample_peak) if sample_peak > 0 else -math.inf
    test.assertTrue(math.isfinite(float(report['peak_dbfs'])))
    test.assertAlmostEqual(float(report['peak_dbfs']), db, delta=0.6)
    test.assertEqual(int(report['sample_rate']), rate)
    test.assertEqual(int(report['channels']), audio.shape[1])
    test.assertTrue(output == root or output.is_relative_to(root))

    if 'vocals' in request:
        expected = [
            ('vocal', index, Path(item['path']), item['gain_db'], item['offset_seconds'])
            for index, item in enumerate(request['vocals'])
        ]
    else:
        expected = [
            ('vocal', 0, Path(request['vocal_path']), request['vocal_db'],
             request['offset_seconds'])
        ]
    expected.append(('backing', 0, Path(request['instrumental_path']),
                     request['instrumental_db'], 0.))
    tracks = report['tracks']
    test.assertEqual(len(tracks), len(expected))
    output_hash = _digest(output)
    test.assertEqual(report['output_sha256'], output_hash)
    for expected_track, track in zip(expected, tracks):
        role, index, source, gain_db, offset = expected_track
        source = source.resolve()
        test.assertTrue(source == root or source.is_relative_to(root))
        test.assertEqual(track['role'], role)
        test.assertEqual(track['index'], index)
        test.assertEqual(Path(track['path']).resolve(), source)
        test.assertAlmostEqual(float(track['gain_db']), float(gain_db), delta=1e-9)
        test.assertAlmostEqual(float(track['offset_seconds']), float(offset), delta=1e-9)
        test.assertEqual(track['input_sha256'], _digest(source))
        test.assertEqual(track['output_sha256'], output_hash)
        test.assertEqual(report['source_sha256'][str(source)], _digest(source))
    expected_sources = {str(track[2].resolve()) for track in expected}
    test.assertEqual(set(report['source_sha256']), expected_sources)
    return audio, rate, output


class AudioMixdownContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='audio-mixdown-')
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_inputs_are_read_only_and_output_is_never_a_source(self):
        vocal = self.root / 'vocal.wav'
        instrumental = self.root / 'instrumental.wav'
        direct = self.root / 'direct.wav'
        _write(vocal, _tone(.22, 24000, 440, .08, channels=1), 24000)
        _write(instrumental, _tone(.31, 48000, 220, .04, channels=2), 48000)
        originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (vocal, instrumental)}

        report = audio_mixdown.render(_request(vocal, instrumental, direct))
        audio, rate, output = _verify_result(self, report, _request(vocal, instrumental, direct), self.root)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreaterEqual(len(audio) / rate, .28)

        for path in (vocal, instrumental):
            before, before_mtime = originals[path]
            self.assertEqual(path.read_bytes(), before, str(path) + ' was modified')
            self.assertEqual(path.stat().st_mtime_ns, before_mtime, str(path) + ' mtime changed')

        same_as_vocal = self.root / 'same-as-vocal.wav'
        shutil.copy2(vocal, same_as_vocal)
        same_source_hash = _digest(same_as_vocal)
        with self.assertRaises((ValueError, TypeError)):
            audio_mixdown.render(_request(same_as_vocal, instrumental, same_as_vocal))
        self.assertEqual(_digest(same_as_vocal), same_source_hash)
        self.assertNotEqual(output.resolve(), vocal.resolve())

    def test_three_vocals_apply_each_gain_and_offset_before_summing(self):
        rate = 16000
        vocal1 = self.root / 'vocal-1.wav'
        vocal2 = self.root / 'vocal-2.wav'
        vocal3 = self.root / 'vocal-3.wav'
        instrumental = self.root / 'three-vocal-backing.wav'
        output = self.root / 'three-vocals.wav'
        _write(vocal1, _tone(.14, rate, 300, .08, start=.01), rate)
        _write(vocal2, _tone(.16, rate, 600, .05), rate)
        _write(vocal3, _tone(.12, rate, 900, .04), rate)
        _write(instrumental, _tone(.23, rate, 150, .02), rate)
        vocals = [
            {'path': str(vocal1), 'gain_db': -6., 'offset_seconds': .02},
            {'path': str(vocal2), 'gain_db': 1.5, 'offset_seconds': .10},
            {'path': str(vocal3), 'gain_db': -12., 'offset_seconds': .27},
        ]
        request = _request(vocal1, instrumental, output, vocals=vocals,
                           instrumental_db=-3.)
        sources = [vocal1, vocal2, vocal3, instrumental]
        originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sources}

        report = audio_mixdown.render(request)
        audio, actual_rate, _ = _verify_result(self, report, request, self.root)
        self.assertEqual(actual_rate, rate)
        self.assertEqual(audio.shape[1], 1)

        backing, backing_rate = sf.read(str(instrumental), always_2d=True, dtype='float64')
        self.assertEqual(backing_rate, rate)
        expected_frames = max(
            len(backing),
            *[len(sf.read(str(path), dtype='float64')[0])
              + int(round(spec['offset_seconds'] * rate))
              for path, spec in zip((vocal1, vocal2, vocal3), vocals)],
        )
        expected = np.zeros(expected_frames, dtype=np.float64)
        expected[:len(backing)] += backing[:, 0] * (10. ** (-3. / 20.))
        for source, spec in zip((vocal1, vocal2, vocal3), vocals):
            samples, source_rate = sf.read(str(source), dtype='float64')
            self.assertEqual(source_rate, rate)
            offset = int(round(spec['offset_seconds'] * rate))
            source_start = max(0, -offset)
            destination = max(0, offset)
            count = min(len(samples) - source_start, expected_frames - destination)
            expected[destination:destination + count] += (
                samples[source_start:source_start + count]
                * (10. ** (spec['gain_db'] / 20.))
            )
        difference = audio[:, 0].astype(np.float64) - expected
        self.assertLess(float(np.max(np.abs(difference), initial=0.)), .004)
        self.assertGreater(_rms(audio), .015)

        for path in sources:
            before, before_mtime = originals[path]
            self.assertEqual(path.read_bytes(), before, str(path) + ' was modified')
            self.assertEqual(path.stat().st_mtime_ns, before_mtime, str(path) + ' mtime changed')
        self.assertEqual([track['role'] for track in report['tracks']],
                         ['vocal', 'vocal', 'vocal', 'backing'])
        self.assertEqual(len({track['input_sha256'] for track in report['tracks']}), 4)

    def test_duration_and_positive_offset_use_timeline_units(self):
        vocal = self.root / 'vocal.wav'
        instrumental = self.root / 'instrumental.wav'
        output = self.root / 'offset.wav'
        _write(vocal, _tone(.18, 24000, 440, .12, start=.03), 24000)
        _write(instrumental, np.zeros((int(.40 * 44100), 2), dtype=np.float32), 44100)
        request = _request(vocal, instrumental, output, offset=.23)
        report = audio_mixdown.render(request)
        audio, rate, _ = _verify_result(self, report, request, self.root)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreaterEqual(len(audio) / rate, .39)
        self.assertLessEqual(len(audio) / rate, .47)
        early = audio[:int(round(.22 * rate))]
        delayed = audio[int(round(.27 * rate)):]
        self.assertLess(float(np.max(np.abs(early), initial=0.)), .01)
        self.assertGreater(_rms(delayed), .02)

        prefix = self.root / 'prefix.wav'
        _write(prefix, _tone(.12, 24000, 880, .12, start=.01), 24000)
        near_end = self.root / 'near-end.wav'
        near_report = audio_mixdown.render(_request(prefix, instrumental, near_end, offset=.55))
        near_audio, near_rate, _ = _verify_result(self, near_report,
            _request(prefix, instrumental, near_end, offset=.55), self.root)
        self.assertGreaterEqual(len(near_audio) / near_rate, .60)
        self.assertLessEqual(len(near_audio) / near_rate, .72)
        self.assertLess(float(np.max(np.abs(near_audio[:int(round(.54 * near_rate))]), initial=0.)), .01)
        self.assertGreater(float(np.max(np.abs(near_audio), initial=0.)), .03)

    def test_mixed_sample_rates_and_channel_layouts_keep_both_lanes(self):
        vocal = self.root / 'vocal-mono-22k.wav'
        instrumental = self.root / 'instrumental-stereo-44k.wav'
        output = self.root / 'mixed.wav'
        _write(vocal, _tone(.27, 22050, 440, .09, channels=1), 22050)
        instrumental_audio = np.column_stack((
            _tone(.36, 44100, 220, .035),
            _tone(.36, 44100, 330, .035),
        ))
        _write(instrumental, instrumental_audio, 44100)
        request = _request(vocal, instrumental, output)
        report = audio_mixdown.render(request)
        audio, rate, _ = _verify_result(self, report, request, self.root)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreaterEqual(len(audio) / rate, .32)
        self.assertLessEqual(len(audio) / rate, .40)
        self.assertGreater(_spectral_amplitude(audio[:, 0], rate, 220), .3)
        self.assertGreater(_spectral_amplitude(audio[:, 1], rate, 330), .3)
        self.assertGreater(_spectral_amplitude(audio[:, 0], rate, 440), .3)
        self.assertGreater(_spectral_amplitude(audio[:, 1], rate, 440), .3)

    def test_vocal_and_instrumental_gain_controls_are_independent(self):
        vocal = self.root / 'vocal-gain.wav'
        instrumental = self.root / 'instrumental-gain.wav'
        _write(vocal, _tone(.42, 24000, 440, .055), 24000)
        _write(instrumental, _tone(.50, 44100, 220, .025, channels=2), 44100)

        quiet_vocal = self.root / 'quiet-vocal.wav'
        loud_vocal = self.root / 'loud-vocal.wav'
        first = audio_mixdown.render(_request(vocal, instrumental, quiet_vocal, vocal_db=-9, instrumental_db=-12))
        second = audio_mixdown.render(_request(vocal, instrumental, loud_vocal, vocal_db=3, instrumental_db=-12))
        a, ar, _ = _verify_result(self, first, _request(vocal, instrumental, quiet_vocal, vocal_db=-9, instrumental_db=-12), self.root)
        b, br, _ = _verify_result(self, second, _request(vocal, instrumental, loud_vocal, vocal_db=3, instrumental_db=-12), self.root)
        self.assertEqual(ar, br)
        self.assertAlmostEqual(
            20 * math.log10(_spectral_amplitude(b, br, 440) / _spectral_amplitude(a, ar, 440)),
            12., delta=1.2)
        self.assertAlmostEqual(
            20 * math.log10(_spectral_amplitude(b, br, 220) / _spectral_amplitude(a, ar, 220)),
            0., delta=1.0)

        quiet_instrumental = self.root / 'quiet-instrumental.wav'
        quiet_instrumental_report = audio_mixdown.render(_request(vocal, instrumental, quiet_instrumental, vocal_db=3, instrumental_db=-20))
        c, cr, _ = _verify_result(self, quiet_instrumental_report, _request(vocal, instrumental, quiet_instrumental, vocal_db=3, instrumental_db=-20), self.root)
        self.assertAlmostEqual(
            20 * math.log10(_spectral_amplitude(b, br, 220) / _spectral_amplitude(c, cr, 220)),
            8., delta=1.4)
        self.assertAlmostEqual(
            20 * math.log10(_spectral_amplitude(b, br, 440) / _spectral_amplitude(c, cr, 440)),
            0., delta=1.0)

    def test_reverb_is_audible_after_both_dry_lanes_end(self):
        vocal = self.root / 'reverb-vocal.wav'
        instrumental = self.root / 'reverb-instrumental.wav'
        output = self.root / 'reverb.wav'
        _write(vocal, _tone(.13, 24000, 330, .10), 24000)
        _write(instrumental, np.zeros((int(.25 * 44100), 2), dtype=np.float32), 44100)
        request = _request(vocal, instrumental, output, reverb=.65)
        report = audio_mixdown.render(request)
        audio, rate, _ = _verify_result(self, report, request, self.root)
        start = int(round(.28 * rate))
        stop = int(round(.55 * rate))
        tail = audio[start:min(stop, len(audio))]
        self.assertGreater(len(audio) / rate, .28)
        self.assertGreater(_rms(tail), .001)
        self.assertGreater(float(np.max(np.abs(tail), initial=0.)), .003)

    def test_reverb_space_choices_produce_distinct_outputs(self):
        vocal, instrumental = self.root/'space-vocal.wav', self.root/'space-instrumental.wav'
        _write(vocal, _tone(.3, 44100, 330, .10), 44100)
        _write(instrumental, np.zeros(44100, dtype=np.float32), 44100)
        results = []
        for profile in ('small', 'natural', 'wide'):
            output = self.root / ('space-' + profile + '.wav')
            request = _request(vocal, instrumental, output, reverb=.5)
            request['reverb_profile'] = profile
            audio_mixdown.render(request)
            results.append(_digest(output))
        self.assertEqual(len(set(results)), 3)

    def test_multi_vocal_preview_uses_the_same_samples_as_a_full_render(self):
        vocal1 = self.root / 'preview-vocal-1.wav'
        vocal2 = self.root / 'preview-vocal-2.wav'
        vocal3 = self.root / 'preview-vocal-3.wav'
        instrumental = self.root / 'preview-instrumental.wav'
        full_path = self.root / 'full.wav'
        preview_path = self.root / 'preview.wav'
        _write(vocal1, _tone(.32, 24000, 440, .06, start=.03), 24000)
        _write(vocal2, _tone(.28, 32000, 660, .04), 32000)
        _write(vocal3, _tone(.24, 16000, 880, .03), 16000)
        _write(instrumental, _tone(.72, 44100, 220, .035, channels=2), 44100)
        vocals = [
            {'path': str(vocal1), 'gain_db': -2., 'offset_seconds': -.01},
            {'path': str(vocal2), 'gain_db': 1., 'offset_seconds': .04},
            {'path': str(vocal3), 'gain_db': -5., 'offset_seconds': .11},
        ]
        full_request = _request(vocal1, instrumental, full_path, vocals=vocals)
        full_request.update(eq_low_cut_hz=80, eq_presence_db=2, compression_strength=.3, reverb_mix=.35)
        full_report = audio_mixdown.render(full_request)
        full_audio, full_rate, _ = _verify_result(self, full_report, full_request, self.root)
        preview_request = dict(full_request)
        preview_request.update(output_path=str(preview_path), preview_start_seconds=.18,
                               preview_duration_seconds=.31)
        preview_report = audio_mixdown.render(preview_request)
        preview_audio, preview_rate, _ = _verify_result(self, preview_report, preview_request, self.root)
        self.assertEqual(preview_rate, full_rate)
        expected_start = int(round(.18 * full_rate))
        count = min(int(round(.31 * full_rate)), len(preview_audio), len(full_audio) - expected_start)
        self.assertGreater(count, int(.2 * full_rate))
        difference = preview_audio[:count] - full_audio[expected_start:expected_start + count]
        self.assertLess(float(np.max(np.abs(difference), initial=0.)), .002)

    def test_invalid_values_fail_before_publishing_output(self):
        vocal = self.root / 'invalid-vocal.wav'
        instrumental = self.root / 'invalid-instrumental.wav'
        destination = self.root / 'invalid-output.wav'
        _write(vocal, _tone(.10, 16000, 440, .05), 16000)
        _write(instrumental, _tone(.10, 16000, 220, .03), 16000)
        source_hashes = {path: _digest(path) for path in (vocal, instrumental)}
        base = _request(vocal, instrumental, destination)
        invalid_cases = {
            'non-finite offset': {'offset_seconds': math.nan},
            'negative low cut': {'eq_low_cut_hz': -1},
            'non-finite presence': {'eq_presence_db': math.inf},
            'compression below range': {'compression_strength': -.01},
            'compression above range': {'compression_strength': 1.01},
            'reverb below range': {'reverb_mix': -.01},
            'reverb above range': {'reverb_mix': 1.01},
            'non-finite vocal gain': {'vocal_db': math.nan},
            'non-finite instrumental gain': {'instrumental_db': math.inf},
            'negative preview start': {'preview_start_seconds': -.1},
            'zero preview duration': {'preview_duration_seconds': 0},
        }
        for name, changes in invalid_cases.items():
            with self.subTest(name=name):
                request = dict(base)
                request.update(changes)
                with self.assertRaises((ValueError, TypeError)):
                    audio_mixdown.render(request)
                self.assertFalse(destination.exists())
                self.assertEqual({path: _digest(path) for path in (vocal, instrumental)}, source_hashes)
                self.assertEqual(set(path.name for path in self.root.iterdir()),
                                 {'invalid-vocal.wav', 'invalid-instrumental.wav'})

    def test_multi_vocal_validation_limits_and_source_collisions_are_safe(self):
        rate = 16000
        vocal1 = self.root / 'valid-1.wav'
        vocal2 = self.root / 'valid-2.wav'
        vocal3 = self.root / 'valid-3.wav'
        backing = self.root / 'valid-backing.wav'
        destination = self.root / 'rejected.wav'
        for path, frequency in ((vocal1, 440), (vocal2, 550), (vocal3, 660)):
            _write(path, _tone(.08, rate, frequency, .04), rate)
        _write(backing, _tone(.10, rate, 220, .03), rate)
        vocals = [
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.},
            {'path': str(vocal2), 'gain_db': -2., 'offset_seconds': .01},
            {'path': str(vocal3), 'gain_db': 1., 'offset_seconds': -.01},
        ]
        base = _request(vocal1, backing, destination, vocals=vocals)
        sources = (vocal1, vocal2, vocal3, backing)
        originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sources}
        expected_names = {path.name for path in sources}
        invalid_cases = {
            'not a list': {'vocals': vocals[0]},
            'empty list': {'vocals': []},
            'four vocals': {'vocals': vocals + [copy.deepcopy(vocals[0])]},
            'non-object entry': {'vocals': [vocals[0], 'not-an-object']},
            'missing path': {'vocals': [{'gain_db': 0., 'offset_seconds': 0.}]},
            'missing gain': {'vocals': [{'path': str(vocal1), 'offset_seconds': 0.}]},
            'missing offset': {'vocals': [{'path': str(vocal1), 'gain_db': 0.}]},
            'empty path': {'vocals': [{'path': '', 'gain_db': 0., 'offset_seconds': 0.}]},
            'non-finite gain': {'vocals': [
                {'path': str(vocal1), 'gain_db': math.nan, 'offset_seconds': 0.}
            ]},
            'gain above limit': {'vocals': [
                {'path': str(vocal1), 'gain_db': 24.1, 'offset_seconds': 0.}
            ]},
            'non-finite offset': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': math.inf}
            ]},
            'offset above limit': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 21601.}
            ]},
            'mixed request forms': {
                'vocals': vocals,
                'vocal_path': str(vocal1),
                'vocal_db': 0.,
                'offset_seconds': 0.,
            },
        }
        for name, changes in invalid_cases.items():
            with self.subTest(name=name):
                request = copy.deepcopy(base)
                request.update(changes)
                with self.assertRaises((ValueError, TypeError)):
                    audio_mixdown.render(request)
                self.assertFalse(destination.exists())
                self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in sources}, originals)
                self.assertEqual({path.name for path in self.root.iterdir()}, expected_names)
                self.assertFalse(list(self.root.glob('.audio-mixdown-*')))

        missing_backing = copy.deepcopy(base)
        del missing_backing['instrumental_path']
        with self.assertRaises((ValueError, TypeError)):
            audio_mixdown.render(missing_backing)

        for source in sources:
            with self.subTest(output_collision=source.name):
                request = copy.deepcopy(base)
                request['output_path'] = str(source)
                with self.assertRaises((ValueError, TypeError)):
                    audio_mixdown.render(request)
                self.assertEqual(source.read_bytes(), originals[source][0])
                self.assertEqual(source.stat().st_mtime_ns, originals[source][1])
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob('.audio-mixdown-*')))

    def test_processing_or_parameter_failure_is_atomic(self):
        vocal = self.root / 'broken-vocal.wav'
        instrumental = self.root / 'instrumental.wav'
        destination = self.root / 'failed-output.wav'
        vocal.write_bytes(b'not a wave file')
        _write(instrumental, _tone(.10, 16000, 220, .03), 16000)
        request = _request(vocal, instrumental, destination)
        with self.assertRaises((ValueError, TypeError, RuntimeError, OSError)):
            audio_mixdown.render(request)
        self.assertFalse(destination.exists())
        self.assertEqual(set(path.name for path in self.root.iterdir()), {'broken-vocal.wav', 'instrumental.wav'})
        self.assertFalse(list(self.root.glob('.audio-mixdown-*')))

        repaired = self.root / 'repaired-vocal.wav'
        _write(repaired, _tone(.10, 16000, 440, .05), 16000)
        repaired_hash = _digest(repaired)
        with self.assertRaises((ValueError, TypeError)):
            audio_mixdown.render(_request(repaired, instrumental, repaired))
        self.assertEqual(_digest(repaired), repaired_hash)
        self.assertFalse(destination.exists())

    def test_multi_vocal_cancellation_cleans_work_without_publishing(self):
        vocal1 = self.root / 'cancel-vocal-1.wav'
        vocal2 = self.root / 'cancel-vocal-2.wav'
        vocal3 = self.root / 'cancel-vocal-3.wav'
        instrumental = self.root / 'cancel-instrumental.wav'
        destination = self.root / 'cancelled-output.wav'
        for path, frequency in ((vocal1, 440), (vocal2, 550), (vocal3, 660)):
            _write(path, _tone(.40, 16000, frequency, .04), 16000)
        _write(instrumental, _tone(.40, 16000, 220, .03), 16000)
        sources = (vocal1, vocal2, vocal3, instrumental)
        source_hashes = {path: _digest(path) for path in sources}
        calls = []
        request = _request(vocal1, instrumental, destination, vocals=[
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.},
            {'path': str(vocal2), 'gain_db': -2., 'offset_seconds': .02},
            {'path': str(vocal3), 'gain_db': 1.5, 'offset_seconds': -.01},
        ])
        with self.assertRaises(InterruptedError):
            audio_mixdown.render(request, cancel=lambda: calls.append(True) or True)
        self.assertTrue(calls, 'render did not poll the cancellation callback')
        self.assertFalse(destination.exists())
        self.assertEqual({path: _digest(path) for path in sources}, source_hashes)
        self.assertEqual({path.name for path in self.root.iterdir()},
                         {path.name for path in sources})
        self.assertFalse(list(self.root.glob('.audio-mixdown-*')))
        self.assertFalse(list(self.root.glob('*.part')))
        self.assertFalse(list(self.root.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
