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
    import audio_mixdown_audition
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
                'peak_after_effects_dbfs', 'gr_db_p95', 'uses_v3', 'warnings',
                'sample_rate', 'channels', 'tracks', 'source_sha256', 'output_sha256'):
        test.assertIn(key, report)
    test.assertEqual(report['schema'], 'vwa.audio_mixdown.render/v3' if report.get('uses_v3')
                     else 'vwa.audio_mixdown.render/v2')
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
            ('vocal', index, Path(item['path']), item['gain_db'],
             item['offset_seconds'], item.get('mute', False),
             item.get('solo', False), item.get('pan', 0.0))
            for index, item in enumerate(request['vocals'])
        ]
    else:
        expected = [
            ('vocal', 0, Path(request['vocal_path']), request['vocal_db'],
             request['offset_seconds'], False, False, 0.0)
        ]
    expected.append(('backing', 0, Path(request['instrumental_path']),
                     request['instrumental_db'], 0.,
                     request.get('instrumental_mute', False),
                     request.get('instrumental_solo', False),
                     request.get('instrumental_pan', 0.0)))
    tracks = report['tracks']
    test.assertEqual(len(tracks), len(expected))
    output_hash = _digest(output)
    test.assertEqual(report['output_sha256'], output_hash)
    for expected_track, track in zip(expected, tracks):
        role, index, source, gain_db, offset, mute, solo, pan = expected_track
        source = source.resolve()
        test.assertTrue(source == root or source.is_relative_to(root))
        test.assertEqual(track['role'], role)
        test.assertEqual(track['index'], index)
        test.assertEqual(Path(track['path']).resolve(), source)
        test.assertAlmostEqual(float(track['gain_db']), float(gain_db), delta=1e-9)
        test.assertAlmostEqual(float(track['offset_seconds']), float(offset), delta=1e-9)
        test.assertEqual(track['mute'], mute)
        test.assertEqual(track['solo'], solo)
        test.assertAlmostEqual(float(track['pan']), float(pan), delta=1e-9)
        test.assertIn(track['audible'], (True, False))
        test.assertEqual(track['input_sha256'], _digest(source))
        test.assertEqual(track['output_sha256'], output_hash)
        test.assertIn('peak_dbfs', track)
        test.assertTrue(math.isfinite(float(track['peak_dbfs'])))
        test.assertEqual(report['source_sha256'][str(source)], _digest(source))
    expected_sources = {str(track[2].resolve()) for track in expected}
    test.assertEqual(set(report['source_sha256']), expected_sources)
    test.assertIn('meters', report)
    meters = report['meters']['tracks']
    test.assertEqual(len(meters), len(tracks))
    for track, meter in zip(tracks, meters):
        test.assertEqual(meter['role'], track['role'])
        test.assertEqual(meter['index'], track['index'])
        test.assertEqual(meter['audible'], track['audible'])
        test.assertEqual(meter['peak_dbfs'], track['peak_dbfs'])
    test.assertTrue(math.isfinite(float(report['meters']['bus_peak_dbfs'])))
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

    def test_silent_sources_report_finite_sentinel_for_every_meter(self):
        rate = 16000
        vocal = self.root / 'silent-vocal.wav'
        instrumental = self.root / 'silent-backing.wav'
        output = self.root / 'silent-mix.wav'
        _write(vocal, np.zeros(rate // 10, dtype=np.float32), rate)
        _write(instrumental, np.zeros(rate // 8, dtype=np.float32), rate)
        report = audio_mixdown.render(_request(vocal, instrumental, output))
        self.assertEqual(float(report['peak_dbfs']), audio_mixdown.SILENCE_DBFS)
        self.assertEqual(len(report['meters']['tracks']), 2)
        self.assertTrue(all(
            float(row['peak_dbfs']) == audio_mixdown.SILENCE_DBFS
            for row in report['meters']['tracks']
        ))
        self.assertEqual(
            float(report['meters']['bus_peak_dbfs']), audio_mixdown.SILENCE_DBFS)

    def test_mute_solo_and_pan_route_tracks_and_upgrade_to_stereo(self):
        rate = 16000
        vocal1 = self.root / 'route-vocal-1.wav'
        vocal2 = self.root / 'route-vocal-2.wav'
        backing = self.root / 'route-backing.wav'
        _write(vocal1, _tone(.16, rate, 440, .055), rate)
        _write(vocal2, _tone(.16, rate, 880, .055), rate)
        _write(backing, _tone(.16, rate, 220, .03), rate)

        for index, pan in enumerate((-1.0, 1.0)):
            output = self.root / ('route-left.wav' if pan < 0 else 'route-right.wav')
            vocals = [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'mute': True, 'solo': False, 'pan': 0.},
                {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.,
                 'mute': False, 'solo': True, 'pan': pan},
            ]
            request = _request(vocal1, backing, output, vocals=vocals)
            report = audio_mixdown.render(request)
            audio, actual_rate, _ = _verify_result(self, report, request, self.root)
            self.assertEqual(actual_rate, rate)
            self.assertEqual(audio.shape[1], 2)
            left = float(np.sqrt(np.mean(np.square(audio[:, 0], dtype=np.float64))))
            right = float(np.sqrt(np.mean(np.square(audio[:, 1], dtype=np.float64))))
            if pan < 0:
                self.assertGreater(left, .01)
                self.assertEqual(right, 0.)
                self.assertFalse(np.any(audio[:, 1]))
            else:
                self.assertEqual(left, 0.)
                self.assertFalse(np.any(audio[:, 0]))
                self.assertGreater(right, .01)
            self.assertEqual([row['audible'] for row in report['tracks']], [False, True, False])
            self.assertEqual(float(report['tracks'][0]['peak_dbfs']), audio_mixdown.SILENCE_DBFS)
            self.assertAlmostEqual(float(report['tracks'][1]['pan']), pan, delta=1e-9)
            self.assertFalse(report['meters']['tracks'][0]['audible'])
            self.assertTrue(report['meters']['tracks'][1]['audible'])
            self.assertFalse(report['meters']['tracks'][2]['audible'])
            self.assertEqual(set(report['source_sha256']), {
                str(vocal1.resolve()), str(vocal2.resolve()), str(backing.resolve())
            })

        silent_output = self.root / 'route-all-muted.wav'
        silent_vocals = [
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
             'mute': True, 'solo': False, 'pan': 0.},
            {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.,
             'mute': True, 'solo': True, 'pan': 0.},
        ]
        silent_request = _request(
            vocal1, backing, silent_output, vocals=silent_vocals,
            instrumental_db=0., reverb=.8)
        silent_request['instrumental_mute'] = True
        silent_report = audio_mixdown.render(silent_request)
        self.assertEqual(float(silent_report['peak_dbfs']), audio_mixdown.SILENCE_DBFS)
        self.assertEqual([row['audible'] for row in silent_report['tracks']],
                         [False, False, False])
        self.assertTrue(all(float(row['peak_dbfs']) == audio_mixdown.SILENCE_DBFS
                            for row in silent_report['meters']['tracks']))
        silent_audio, silent_rate = _read(silent_output)
        self.assertEqual(len(silent_audio), int(round(.16 * silent_rate)))

    def test_legacy_three_key_request_matches_explicit_default_track(self):
        rate = 16000
        vocal = self.root / 'legacy-vocal.wav'
        backing = self.root / 'legacy-backing.wav'
        legacy_output = self.root / 'legacy-request.wav'
        explicit_output = self.root / 'explicit-request.wav'
        _write(vocal, _tone(.17, rate, 440, .05, start=.013), rate)
        _write(backing, _tone(.24, rate, 220, .025), rate)
        legacy_request = _request(
            vocal, backing, legacy_output, offset=.031, vocal_db=-2.5, reverb=.2)
        explicit_request = dict(legacy_request)
        explicit_request['output_path'] = str(explicit_output)
        for name in ('vocal_path', 'vocal_db', 'offset_seconds'):
            explicit_request.pop(name)
        explicit_request['vocals'] = [{
            'path': str(vocal), 'gain_db': -2.5, 'offset_seconds': .031,
        }]

        legacy_report = audio_mixdown.render(legacy_request)
        explicit_report = audio_mixdown.render(explicit_request)
        legacy_audio, legacy_rate, _ = _verify_result(
            self, legacy_report, legacy_request, self.root)
        explicit_audio, explicit_rate, _ = _verify_result(
            self, explicit_report, explicit_request, self.root)
        self.assertEqual(legacy_rate, explicit_rate)
        self.assertEqual(legacy_audio.shape, explicit_audio.shape)
        self.assertTrue(np.array_equal(legacy_audio, explicit_audio))
        self.assertEqual(legacy_report['tracks'][0]['mute'], False)
        self.assertEqual(legacy_report['tracks'][0]['solo'], False)
        self.assertEqual(legacy_report['tracks'][0]['pan'], 0.)
        self.assertFalse(legacy_report['uses_v3'])
        self.assertEqual(legacy_report['warnings'], [])

    def test_mute_precedes_solo_and_backing_shares_the_solo_group(self):
        rate = 16000
        vocal1 = self.root / 'group-vocal-1.wav'
        vocal2 = self.root / 'group-vocal-2.wav'
        backing = self.root / 'group-backing.wav'
        for path, frequency in ((vocal1, 440), (vocal2, 660), (backing, 220)):
            _write(path, _tone(.12, rate, frequency, .025), rate)

        first_vocals = [
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
             'mute': True, 'solo': True, 'pan': 1.},
            {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.},
        ]
        first_output = self.root / 'muted-solo-does-not-suppress.wav'
        first_request = _request(
            vocal1, backing, first_output, vocals=first_vocals)
        first_report = audio_mixdown.render(first_request)
        first_audio, _, _ = _verify_result(
            self, first_report, first_request, self.root)
        self.assertEqual([row['audible'] for row in first_report['tracks']],
                         [False, True, True])
        self.assertEqual(first_audio.shape[1], 1)

        second_vocals = [
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
             'solo': True},
            {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.,
             'mute': True, 'solo': True, 'pan': -1.},
        ]
        second_output = self.root / 'active-solo-mutes-muted-track.wav'
        second_request = _request(
            vocal1, backing, second_output, vocals=second_vocals)
        second_report = audio_mixdown.render(second_request)
        second_audio, _, _ = _verify_result(
            self, second_report, second_request, self.root)
        self.assertEqual([row['audible'] for row in second_report['tracks']],
                         [True, False, False])
        self.assertEqual(second_audio.shape[1], 1)

        backing_solo_request = _request(
            vocal1, backing, self.root / 'backing-solo.wav',
            vocals=[
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.},
                {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.},
            ])
        backing_solo_request['instrumental_solo'] = True
        backing_solo_report = audio_mixdown.render(backing_solo_request)
        _verify_result(self, backing_solo_report, backing_solo_request, self.root)
        self.assertEqual(
            [row['audible'] for row in backing_solo_report['tracks']],
            [False, False, True])

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

    def test_v3_eq_bands_validation_application_and_frequency_warning(self):
        rate = 16000
        vocal = self.root / 'eq-band-vocal.wav'
        backing = self.root / 'eq-band-backing.wav'
        _write(vocal, _tone(.35, rate, 440, .10), rate)
        _write(backing, np.zeros(int(.45 * rate), dtype=np.float32), rate)
        output = self.root / 'eq-band-output.wav'
        no_eq_output = self.root / 'eq-band-none.wav'
        base = _request(vocal, backing, output)
        bands = [
            {'type': 'highpass', 'freq_hz': 1000, 'gain_db': 0, 'q': .707,
             'enabled': True},
            {'type': 'lowshelf', 'freq_hz': 220, 'gain_db': 2, 'q': .707,
             'enabled': False},
            {'type': 'bell', 'freq_hz': 440, 'gain_db': 3, 'q': 1.0,
             'enabled': True},
            {'type': 'highshelf', 'freq_hz': 5000, 'gain_db': 1, 'q': .707,
             'enabled': True},
        ]
        request = copy.deepcopy(base)
        request['eq_bands'] = bands
        report = audio_mixdown.render(request)
        audio, actual_rate, _ = _verify_result(self, report, request, self.root)
        self.assertTrue(report['uses_v3'])
        self.assertEqual(report['warnings'], [])
        self.assertEqual(actual_rate, rate)

        no_eq_request = copy.deepcopy(base)
        no_eq_request['output_path'] = str(no_eq_output)
        no_eq_request['eq_bands'] = []
        no_eq_report = audio_mixdown.render(no_eq_request)
        no_eq_audio, _, _ = _verify_result(
            self, no_eq_report, no_eq_request, self.root)
        self.assertGreater(float(np.max(np.abs(audio - no_eq_audio))), .001)

        # eq_bands is authoritative once present; the legacy two EQ values are
        # not required and must not be silently re-applied.
        minimal = copy.deepcopy(base)
        minimal['output_path'] = str(self.root / 'eq-band-minimal.wav')
        minimal.pop('eq_low_cut_hz')
        minimal.pop('eq_presence_db')
        minimal['eq_bands'] = [bands[2]]
        minimal_report = audio_mixdown.render(minimal)
        _verify_result(self, minimal_report, minimal, self.root)

        clamped = copy.deepcopy(base)
        clamped['output_path'] = str(self.root / 'eq-band-clamped.wav')
        clamped['eq_bands'] = [{
            'type': 'highshelf', 'freq_hz': 20000, 'gain_db': 1,
            'q': .707, 'enabled': True,
        }]
        clamped_report = audio_mixdown.render(clamped)
        _verify_result(self, clamped_report, clamped, self.root)
        self.assertTrue(any('clamped' in warning
                            for warning in clamped_report['warnings']))

        invalid_bands = [
            [dict(bands[0]), dict(bands[1]), dict(bands[2]), dict(bands[3]), dict(bands[0])],
            [{'type': 'peak', 'freq_hz': 440, 'gain_db': 0, 'q': 1, 'enabled': True}],
            [{'type': 'bell', 'freq_hz': 440, 'gain_db': 0, 'q': 1}],
            [{'type': 'bell', 'freq_hz': 9, 'gain_db': 0, 'q': 1, 'enabled': True}],
            [{'type': 'bell', 'freq_hz': 440, 'gain_db': 25, 'q': 1, 'enabled': True}],
            [{'type': 'bell', 'freq_hz': 440, 'gain_db': 0, 'q': .09, 'enabled': True}],
            [{'type': 'bell', 'freq_hz': 440, 'gain_db': 0, 'q': 1, 'enabled': 1}],
        ]
        for index, invalid in enumerate(invalid_bands):
            with self.subTest(index=index):
                bad = copy.deepcopy(base)
                bad['output_path'] = str(self.root / f'invalid-eq-{index}.wav')
                bad['eq_bands'] = invalid
                with self.assertRaises((ValueError, TypeError)):
                    audio_mixdown.render(bad)
                self.assertFalse(Path(bad['output_path']).exists())

    def test_v3_reverb_send_is_per_track_and_predelay_delays_return(self):
        rate = 16000
        vocal1 = self.root / 'send-vocal-1.wav'
        vocal2 = self.root / 'send-vocal-2.wav'
        backing = self.root / 'send-backing.wav'
        _write(vocal1, _tone(.28, rate, 440, .09), rate)
        _write(vocal2, _tone(.28, rate, 880, .09), rate)
        _write(backing, np.zeros(int(1.20 * rate), dtype=np.float32), rate)
        vocals = [
            {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
             'reverb_send': 1.0},
            {'path': str(vocal2), 'gain_db': 0., 'offset_seconds': 0.,
             'reverb_send': 0.0},
        ]
        request = _request(vocal1, backing, self.root / 'send-output.wav',
                           vocals=vocals, reverb=.8)
        request.update({'reverb_predelay_ms': 0, 'width': 1.0})
        report = audio_mixdown.render(request)
        audio, _, _ = _verify_result(self, report, request, self.root)
        self.assertEqual(report['tracks'][0]['reverb_send'], 1.0)
        self.assertEqual(report['tracks'][1]['reverb_send'], 0.0)
        self.assertGreater(len(audio) / rate, 1.25)
        tail = audio[int(1.23 * rate):]
        self.assertGreater(_spectral_amplitude(tail, rate, 440),
                           _spectral_amplitude(tail, rate, 880) * 1.2)

        no_send_vocals = copy.deepcopy(vocals)
        no_send_vocals[0]['reverb_send'] = 0.0
        no_send_request = _request(
            vocal1, backing, self.root / 'no-send-output.wav',
            vocals=no_send_vocals, reverb=.8)
        no_send_request.update({'reverb_predelay_ms': 0, 'width': 1.0})
        no_send_report = audio_mixdown.render(no_send_request)
        no_send_audio, _, _ = _verify_result(
            self, no_send_report, no_send_request, self.root)
        self.assertLessEqual(len(no_send_audio), int(round(1.20 * rate)))

        delayed_request = _request(
            vocal1, backing, self.root / 'predelay-output.wav',
            vocals=[{
                'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                'reverb_send': 1.0,
            }], reverb=1.0)
        delayed_request.update({'reverb_predelay_ms': 80, 'width': 1.0})
        delayed_report = audio_mixdown.render(delayed_request)
        delayed_audio, _, _ = _verify_result(
            self, delayed_report, delayed_request, self.root)
        early = delayed_audio[:int(round(.04 * rate))]
        self.assertLess(float(np.max(np.abs(early), initial=0.)), .002)

    def test_v3_width_uses_mid_side_and_preserves_center_level(self):
        rate = 16000
        backing = self.root / 'width-backing.wav'
        _write(backing, np.zeros(int(.30 * rate), dtype=np.float32), rate)
        left = _tone(.22, rate, 440, .12)
        right = _tone(.22, rate, 660, .12)
        stereo = np.column_stack((left, right))
        vocal = self.root / 'width-vocal.wav'
        _write(vocal, stereo, rate)
        outputs = {}
        for width in (0., 1., 2.):
            request = _request(
                vocal, backing, self.root / f'width-{width:g}.wav', vocals=[{
                    'path': str(vocal), 'gain_db': 0., 'offset_seconds': 0.,
                }])
            request['width'] = width
            report = audio_mixdown.render(request)
            audio, _, _ = _verify_result(self, report, request, self.root)
            self.assertEqual(audio.shape[1], 2)
            outputs[width] = audio
        self.assertEqual(float(np.max(np.abs(outputs[0.][:, 0] - outputs[0.][:, 1]))), 0.)
        self.assertGreater(
            float(np.sqrt(np.mean(np.square(outputs[2.][:, 0] - outputs[2.][:, 1])))),
            float(np.sqrt(np.mean(np.square(outputs[1.][:, 0] - outputs[1.][:, 1])))),
        )
        center_vocal = self.root / 'center-vocal.wav'
        _write(center_vocal, np.column_stack((left, left)), rate)
        center_outputs = []
        for width in (0., 2.):
            request = _request(
                center_vocal, backing, self.root / f'center-{width:g}.wav',
                vocals=[{'path': str(center_vocal), 'gain_db': 0.,
                         'offset_seconds': 0.}])
            request['width'] = width
            report = audio_mixdown.render(request)
            audio, _, _ = _verify_result(self, report, request, self.root)
            center_outputs.append(audio)
        self.assertLess(float(np.max(np.abs(center_outputs[0] - center_outputs[1]))), 1e-5)

    def test_v3_compression_reports_block_gr_and_post_effect_peak(self):
        rate = 16000
        vocal = self.root / 'gr-vocal.wav'
        backing = self.root / 'gr-backing.wav'
        _write(vocal, _tone(.32, rate, 440, .55), rate)
        _write(backing, np.zeros(int(.40 * rate), dtype=np.float32), rate)
        request = _request(vocal, backing, self.root / 'gr-output.wav')
        request.update({'compression_strength': 1.0, 'width': 1.0})
        report = audio_mixdown.render(request)
        _verify_result(self, report, request, self.root)
        self.assertLess(float(report['gr_db_p95']), -1.0)
        self.assertLess(float(report['tracks'][0]['gr_db_p95']), -1.0)
        self.assertTrue(math.isfinite(float(report['peak_after_effects_dbfs'])))
        self.assertGreaterEqual(float(report['peak_after_effects_dbfs']),
                                float(report['peak_dbfs']))

    def test_progress_is_blockwise_report_neutral_and_finalized(self):
        rate = 8000
        vocal = self.root / 'progress-vocal.wav'
        backing = self.root / 'progress-backing.wav'
        _write(vocal, _tone(5.0, rate, 330, .035), rate)
        _write(backing, _tone(5.0, rate, 110, .02), rate)
        first = _request(vocal, backing, self.root / 'progress-with.wav')
        first.update({'width': 1.0, 'eq_bands': []})
        events = []
        first_report = audio_mixdown.render(first, progress=events.append)
        _verify_result(self, first_report, first, self.root)
        self.assertGreaterEqual(len(events), 2)
        last = -1.0
        for event in events:
            self.assertEqual(event['phase'], 'mix')
            self.assertGreaterEqual(float(event['fraction']), last)
            last = float(event['fraction'])
            self.assertLessEqual(float(event['fraction']), 1.0)
            self.assertIn('frames_done', event)
            self.assertIn('frames_total', event)
            self.assertIsInstance(event['tracks'], list)
            self.assertTrue(math.isfinite(float(event['bus_peak'])))
        self.assertEqual(float(events[-1]['fraction']), 1.0)
        self.assertEqual(events[-1]['frames_done'], events[-1]['frames_total'])

        second = copy.deepcopy(first)
        second['output_path'] = str(self.root / 'progress-without.wav')
        second_report = audio_mixdown.render(second)
        _verify_result(self, second_report, second, self.root)
        self.assertEqual(first_report['output_sha256'], second_report['output_sha256'])
        self.assertEqual(first_report['peak_dbfs'], second_report['peak_dbfs'])
        self.assertEqual(first_report['peak_after_effects_dbfs'],
                         second_report['peak_after_effects_dbfs'])
        self.assertEqual(first_report['gr_db_p95'], second_report['gr_db_p95'])

    def test_legacy_eq_low_cut_keeps_original_filter_semantics(self):
        rate = 16000
        vocal = self.root / 'legacy-eq-vocal.wav'
        backing = self.root / 'legacy-eq-backing.wav'
        _write(vocal, _tone(.30, rate, 80, .12), rate)
        _write(backing, np.zeros(int(.38 * rate), dtype=np.float32), rate)
        bypass = _request(vocal, backing, self.root / 'legacy-eq-bypass.wav')
        cut_output = self.root / 'legacy-eq-cut.wav'
        cut = _request(vocal, backing, cut_output)
        cut['eq_low_cut_hz'] = 500
        bypass_report = audio_mixdown.render(bypass)
        bypass_audio, _, _ = _verify_result(self, bypass_report, bypass, self.root)
        cut_audio, _, _ = _verify_result(
            self, audio_mixdown.render(cut), cut, self.root)
        self.assertLess(_spectral_amplitude(cut_audio, rate, 80),
                        _spectral_amplitude(bypass_audio, rate, 80) * .6)

    def test_v3_scalar_fields_are_strictly_validated(self):
        rate = 16000
        vocal = self.root / 'scalar-vocal.wav'
        backing = self.root / 'scalar-backing.wav'
        _write(vocal, _tone(.10, rate, 440, .05), rate)
        _write(backing, _tone(.10, rate, 220, .03), rate)
        output = self.root / 'scalar-output.wav'
        base = _request(vocal, backing, output, vocals=[{
            'path': str(vocal), 'gain_db': 0., 'offset_seconds': 0.,
            'reverb_send': 0.5,
        }])
        base['eq_bands'] = []
        invalid = [
            {'reverb_send': -0.01},
            {'reverb_send': 1.01},
            {'reverb_send': True},
            {'reverb_predelay_ms': -1},
            {'reverb_predelay_ms': 121},
            {'reverb_predelay_ms': True},
            {'width': -0.01},
            {'width': 2.01},
            {'width': True},
            {'width': '1.0'},
        ]
        for index, change in enumerate(invalid):
            with self.subTest(change=change):
                request = copy.deepcopy(base)
                request['output_path'] = str(self.root / f'scalar-invalid-{index}.wav')
                if 'reverb_send' in change:
                    request['vocals'][0].update(change)
                else:
                    request.update(change)
                with self.assertRaises((ValueError, TypeError)):
                    audio_mixdown.render(request)
                self.assertFalse(Path(request['output_path']).exists())

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
            'non-bool instrumental mute': {'instrumental_mute': 1},
            'non-bool instrumental solo': {'instrumental_solo': 'false'},
            'non-finite instrumental pan': {'instrumental_pan': math.nan},
            'instrumental pan above range': {'instrumental_pan': 1.01},
            'bool instrumental pan': {'instrumental_pan': False},
            'string instrumental pan': {'instrumental_pan': '0.5'},
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
            'non-bool mute': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'mute': 'true'}
            ]},
            'non-bool solo': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'solo': 1}
            ]},
            'non-finite pan': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'pan': math.nan}
            ]},
            'pan above range': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'pan': 1.01}
            ]},
            'bool pan': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'pan': True}
            ]},
            'string pan': {'vocals': [
                {'path': str(vocal1), 'gain_db': 0., 'offset_seconds': 0.,
                 'pan': '0.5'}
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



class AuditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mix-audition-')
        self.root = Path(self.temp.name)
        self.rate = 16000
        self.vocals = [self.root / ('audition-vocal-%d.wav' % index) for index in range(1, 4)]
        for index, path in enumerate(self.vocals, 1):
            _write(path, _tone(1.8, self.rate, 180 + 90 * index, .045), self.rate)
        self.backing = self.root / 'audition-backing.wav'
        _write(self.backing, _tone(1.8, self.rate, 95, .03), self.rate)
        self.cache = self.root / 'audition-cache'
        self.outputs = []
        self.request = _request(
            self.vocals[0], self.backing, self.root / 'audition-after-1.wav',
            reverb=.18, vocals=[
                {'path': str(self.vocals[0]), 'gain_db': -1.0, 'offset_seconds': .02,
                 'mute': False, 'solo': False, 'pan': -.25},
                {'path': str(self.vocals[1]), 'gain_db': -3.0, 'offset_seconds': .10,
                 'mute': False, 'solo': True, 'pan': .30},
                {'path': str(self.vocals[2]), 'gain_db': 2.0, 'offset_seconds': 0.0,
                 'mute': True, 'solo': False, 'pan': .0},
            ])
        self.request.update({
            'eq_low_cut_hz': 70,
            'eq_presence_db': 1.5,
            'compression_strength': .25,
            'instrumental_db': -5.0,
            'instrumental_pan': .15,
            'preview_start_seconds': .45,
            'preview_duration_seconds': .55,
        })

    def tearDown(self):
        self.temp.cleanup()

    def _render(self, name):
        before = self.root / ('audition-before-' + name + '.wav')
        after = self.root / ('audition-after-' + name + '.wav')
        request = copy.deepcopy(self.request)
        request['output_path'] = str(after)
        report = audio_mixdown_audition.render_audition_pair(
            request, before, after, self.cache)
        self.outputs.extend((before, after))
        return report, before, after

    def test_live_segment_uses_decode_cache_and_keeps_export_contract_separate(self):
        source_hashes = {path: _digest(path) for path in (*self.vocals, self.backing)}
        first, before1, after1 = self._render('1')
        self.assertEqual({path: _digest(path) for path in source_hashes}, source_hashes)
        self.assertFalse(first['cache_hit'])
        self.assertEqual(first['schema'], 'vwa.audio_mixdown.audition/v1')
        for path in (before1, after1):
            audio, rate = _read(path)
            self.assertEqual(rate, self.rate)
            self.assertEqual(audio.shape[1], 2)
            self.assertAlmostEqual(len(audio) / rate, .55, delta=.02)
            self.assertLessEqual(float(np.max(np.abs(audio))), 10 ** (-1 / 20) + 1e-4)
        self.assertEqual(first['after']['preview_start_seconds'], .45)
        self.assertTrue(all(row['role'] in ('vocal', 'backing')
                            for row in first['after']['meters']['tracks']))
        audible = {row['role'] + ':' + str(row['index']): row['audible']
                   for row in first['after']['tracks']}
        self.assertFalse(audible['vocal:0'])
        self.assertTrue(audible['vocal:1'])
        self.assertFalse(audible['vocal:2'])
        self.assertFalse(audible['backing:0'])
        cache_files = sorted(path for path in first_cache_files(first, self.cache)
                             if path.is_file())
        self.assertGreaterEqual(len(cache_files), 4)
        cache_mtimes = {path: path.stat().st_mtime_ns for path in cache_files}

        self.request['vocals'][1]['gain_db'] = -7.5
        second, before2, after2 = self._render('2')
        self.assertTrue(second['cache_hit'])
        self.assertTrue(before2.is_file() and after2.is_file())
        self.assertEqual({path: path.stat().st_mtime_ns for path in cache_files},
                         cache_mtimes)
        self.assertAlmostEqual(second['after']['preview_start_seconds'], .45, places=4)
        self.assertNotEqual(before1.read_bytes(), before2.read_bytes())

    def test_source_change_invalidates_decode_cache(self):
        first, _before, _after = self._render('1')
        first_generation = first['cache_generation']
        _write(self.vocals[1], _tone(1.8, self.rate, 880, .04), self.rate)
        second, _before2, _after2 = self._render('2')
        self.assertFalse(second['cache_hit'])
        self.assertNotEqual(first_generation, second['cache_generation'])
        self.assertTrue(all(path.is_file() for path in second_cache_files(second, self.cache)))


def first_cache_files(report, cache_root):
    generation = cache_root / report['cache_generation']
    return sorted(generation.glob('*.wav'))


def second_cache_files(report, cache_root):
    return first_cache_files(report, cache_root)




if __name__ == '__main__':
    unittest.main(verbosity=2)
