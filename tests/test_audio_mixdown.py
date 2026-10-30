"""Behavioral isolation contracts for the simple stereo audio mixdown."""

from __future__ import annotations

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


def _request(vocal, instrumental, output, *, offset=0., reverb=0., vocal_db=0., instrumental_db=0.):
    return {
        'vocal_path': str(vocal),
        'instrumental_path': str(instrumental),
        'output_path': str(output),
        'vocal_db': vocal_db,
        'instrumental_db': instrumental_db,
        'offset_seconds': offset,
        'eq_low_cut_hz': 0,
        'eq_presence_db': 0,
        'compression_strength': 0,
        'reverb_mix': reverb,
    }


def _verify_result(test, report, request, root):
    test.assertIsInstance(report, dict)
    for key in ('output_path', 'duration_seconds', 'peak_dbfs'):
        test.assertIn(key, report)
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
    test.assertTrue(output == root or output.is_relative_to(root))
    for source in (request['vocal_path'], request['instrumental_path']):
        source = Path(source).resolve()
        test.assertTrue(source == root or source.is_relative_to(root))
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

    def test_preview_uses_the_same_samples_as_a_full_render(self):
        vocal = self.root / 'preview-vocal.wav'
        instrumental = self.root / 'preview-instrumental.wav'
        full_path = self.root / 'full.wav'
        preview_path = self.root / 'preview.wav'
        _write(vocal, _tone(.52, 24000, 440, .06, start=.03), 24000)
        _write(instrumental, _tone(.72, 44100, 220, .035, channels=2), 44100)
        full_request = _request(vocal, instrumental, full_path)
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

    def test_cancellation_cleans_private_work_without_publishing(self):
        vocal = self.root / 'cancel-vocal.wav'
        instrumental = self.root / 'cancel-instrumental.wav'
        destination = self.root / 'cancelled-output.wav'
        _write(vocal, _tone(.40, 16000, 440, .05), 16000)
        _write(instrumental, _tone(.40, 16000, 220, .03), 16000)
        sources = {path: _digest(path) for path in (vocal, instrumental)}
        calls = []
        request = _request(vocal, instrumental, destination)
        with self.assertRaises(InterruptedError):
            audio_mixdown.render(request, cancel=lambda: calls.append(True) or True)
        self.assertTrue(calls, 'render did not poll the cancellation callback')
        self.assertFalse(destination.exists())
        self.assertEqual({path: _digest(path) for path in (vocal, instrumental)}, sources)
        self.assertEqual(set(path.name for path in self.root.iterdir()),
                         {'cancel-vocal.wav', 'cancel-instrumental.wav'})
        self.assertFalse(list(self.root.glob('.audio-mixdown-*')))
        self.assertFalse(list(self.root.glob('*.part')))
        self.assertFalse(list(self.root.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
