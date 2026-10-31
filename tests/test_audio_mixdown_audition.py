"""Behavioral contracts for the segmented live-audition engine."""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True

APP = Path(__file__).resolve().parents[1]
DEPENDENCY_ENV = "VWA_MIX_TEST_PEDALBOARD_PATH"

if not os.environ.get(DEPENDENCY_ENV):
    installed = APP / "dependencies/optional/pedalboard/current/packages"
    if installed.is_dir():
        os.environ[DEPENDENCY_ENV] = str(installed)
    else:
        print("SKIP: " + DEPENDENCY_ENV + " is not set and Pedalboard is not installed.")
        raise SystemExit(0)


def _pedalboard_import_root(value):
    root = Path(value).expanduser()
    if not root.exists():
        raise RuntimeError(DEPENDENCY_ENV + " does not exist: " + str(root))
    candidates = [root]
    if root.is_dir():
        candidates.extend((root / "unpacked", root / "site-packages"))
        candidates.extend(sorted(path for path in root.iterdir() if path.is_dir()))
    for candidate in candidates:
        if (candidate / "pedalboard").is_dir():
            return candidate.resolve()
        if (candidate / "pedalboard.py").is_file():
            return candidate.resolve()
    raise RuntimeError(DEPENDENCY_ENV + " must identify an importable Pedalboard package")


try:
    pedalboard_root = _pedalboard_import_root(os.environ[DEPENDENCY_ENV])
except RuntimeError as error:
    print("ERROR: " + str(error))
    raise SystemExit(1)

sys.path[:0] = [str(pedalboard_root), str(APP / "src")]
try:
    import numpy as np
    import soundfile as sf
    import audio_mixdown
    import audio_mixdown_audition as audition
except Exception as error:
    print("ERROR: cannot import audition runtime: " + type(error).__name__ + ": " + str(error))
    raise SystemExit(1)


def _tone(seconds, rate, frequency, amplitude, channels=1):
    samples = int(round(seconds * rate))
    time_axis = np.arange(samples, dtype=np.float64) / rate
    signal = amplitude * np.sin(2 * np.pi * frequency * time_axis)
    signal = signal.astype(np.float32)
    return np.repeat(signal[:, None], channels, axis=1) if channels > 1 else signal


def _write(path, audio, rate):
    sf.write(str(path), np.asarray(audio, dtype=np.float32), int(rate), subtype="FLOAT")


def _read(path):
    audio, rate = sf.read(str(path), always_2d=True, dtype="float32")
    if not np.isfinite(audio).all():
        raise AssertionError("render wrote NaN or infinity to " + str(path))
    return audio, int(rate)


def _audition_artifacts(directory):
    return sorted(
        path.name for path in Path(directory).iterdir()
        if path.name.startswith(".audition")
        or path.name.startswith("audition-encode-")
    )


def _v3_request(request):
    value = copy.deepcopy(request)
    value.update({
        "eq_bands": [
            {"type": "highpass", "freq_hz": 80.0, "gain_db": 0.0,
             "q": 0.707, "enabled": True},
            {"type": "bell", "freq_hz": 2200.0, "gain_db": 2.0,
             "q": 0.8, "enabled": True},
        ],
        "reverb_mix": 0.18,
        "reverb_predelay_ms": 20.0,
        "width": 0.85,
        "compression_strength": 0.4,
    })
    return value


def _request(root, vocals, backing, *, seconds=2.0, reverb=0.0,
             compression=0.0):
    return {
        "instrumental_path": str(backing),
        "output_path": str(root / "planned-output.wav"),
        "instrumental_db": -4.0,
        "vocals": vocals,
        "eq_low_cut_hz": 70.0,
        "eq_presence_db": 0.0,
        "compression_strength": compression,
        "reverb_mix": reverb,
        "reverb_profile": "natural",
        "preview_start_seconds": 0.15,
        "preview_duration_seconds": seconds,
    }


class AuditionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mix-audition-engine-")
        self.root = Path(self.temp.name)
        self.rate = 16000
        self.vocals = [self.root / "vocal-1.wav", self.root / "vocal-2.wav"]
        _write(self.vocals[0], _tone(3.0, self.rate, 190, 0.22), self.rate)
        _write(self.vocals[1], _tone(3.0, self.rate, 310, 0.18), self.rate)
        self.backing = self.root / "backing.wav"
        stereo = np.stack((
            0.11 * np.sin(2 * np.pi * 95 * np.arange(self.rate * 3) / self.rate),
            0.08 * np.sin(2 * np.pi * 137 * np.arange(self.rate * 3) / self.rate),
        ), axis=1).astype(np.float32)
        _write(self.backing, stereo, self.rate)
        self.vocal_rows = [
            {
                "path": str(self.vocals[0]), "gain_db": -1.0,
                "offset_seconds": 0.01, "mute": False, "solo": False,
                "pan": -0.15, "reverb_send": 0.35,
            },
            {
                "path": str(self.vocals[1]), "gain_db": -3.0,
                "offset_seconds": 0.04, "mute": False, "solo": False,
                "pan": 0.25, "reverb_send": 0.65,
            },
        ]
        self.cache = self.root / "cache"
        self.request = _request(self.root, self.vocal_rows, self.backing, seconds=1.6)

    def tearDown(self):
        self.temp.cleanup()

    def _paths(self, tag):
        directory = self.root / tag
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "before.wav", directory / "after.wav"

    def test_peak_envelopes_and_gain_reduction_are_meter_ready(self):
        request = copy.deepcopy(self.request)
        request["compression_strength"] = 1.0
        request["reverb_mix"] = 0.0
        request["vocals"][0]["gain_db"] = 6.0
        request["vocals"][1]["gain_db"] = 4.0
        before, after = self._paths("meter")
        report = audition.render_audition_pair(
            request, before, after, self.cache, variant="after"
        )
        result = report["after"]
        envelope = result["peak_envelope"]
        self.assertEqual(envelope["hz"], 30.0)
        self.assertEqual(envelope["unit"], "dBFS")
        self.assertEqual(envelope["stage"], "pre_limiter")
        self.assertEqual(len(envelope["tracks"]), 3)
        self.assertEqual(
            len(envelope["time_seconds"]), len(envelope["bus"]["values_dbfs"])
        )
        for row in envelope["tracks"]:
            self.assertEqual(len(row["values_dbfs"]), len(envelope["time_seconds"]))
            self.assertTrue(all(math.isfinite(value) for value in row["values_dbfs"]))
            self.assertTrue(all(value >= -144.0 for value in row["values_dbfs"]))
        self.assertGreater(max(envelope["bus"]["values_dbfs"]), -30.0)
        self.assertGreater(result["gr_db_p95"], 0.1)
        self.assertEqual(
            len(result["gr_envelope"]["values_db"]),
            len(result["gr_envelope"]["time_seconds"]),
        )
        self.assertTrue(result["gr_envelope"]["enabled"])
        self.assertTrue(math.isfinite(result["gr_db_p95"]))
        audio, rate = _read(after)
        self.assertEqual(rate, self.rate)
        self.assertTrue(audio.size > 0)
        info = sf.info(str(after))
        self.assertEqual(info.subtype, "FLOAT")
        self.assertEqual(result["output_encoding"]["subtype"], "FLOAT")
        self.assertEqual(result["output_encoding"]["writer"], "ffmpeg")
        self.assertGreaterEqual(result["timings_ms"]["encode"], 0.0)
        self.assertGreaterEqual(result["timings_ms"]["inspect"], 0.0)
        self.assertLessEqual(
            float(np.max(np.abs(audio))),
            audio_mixdown.PEAK_CEILING + 1e-5,
        )
        self.assertFalse(before.exists())

    def test_measured_reverb_edc_has_time_axis_predelay_key_and_cache(self):
        request = copy.deepcopy(self.request)
        request["reverb_mix"] = 0.22
        request["reverb_profile"] = "wide"
        request["reverb_predelay_ms"] = 35.0
        before1, after1 = self._paths("edc-1")
        first = audition.render_audition_pair(
            request, before1, after1, self.cache, variant="after"
        )
        edc = first["reverb_edc"]
        self.assertTrue(edc["measured"])
        self.assertFalse(edc["cache_hit"])
        self.assertEqual(edc["profile"], "wide")
        self.assertEqual(edc["predelay_ms"], 35.0)
        self.assertIn("wide", edc["key"])
        self.assertIn("35.000ms", edc["key"])
        self.assertEqual(edc["curve_range_db"], [-60.0, 0.0])
        self.assertEqual(len(edc["time_seconds"]), len(edc["edc_db"]))
        self.assertGreaterEqual(len(edc["time_seconds"]), 100)
        self.assertTrue(all(
            b >= a for a, b in zip(edc["time_seconds"], edc["time_seconds"][1:])
        ))
        self.assertGreaterEqual(min(edc["edc_db"]), -60.0)
        self.assertLessEqual(max(edc["edc_db"]), 0.0)
        plateau = [value for time_value, value in zip(
            edc["time_seconds"], edc["edc_db"]
        ) if time_value < 0.02]
        self.assertTrue(plateau)
        self.assertGreater(max(plateau), -1.0)

        before2, after2 = self._paths("edc-2")
        second = audition.render_audition_pair(
            request, before2, after2, self.cache, variant="after"
        )
        self.assertTrue(second["reverb_edc"]["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["decode_cache_source"], "memory")
        self.assertTrue(after1.is_file() and after2.is_file())

    def test_v3_fields_are_guarded_applied_and_reported(self):
        request = copy.deepcopy(self.request)
        request.update({
            "eq_bands": [
                {"type": "highpass", "freq_hz": 90, "gain_db": 0, "q": 0.707, "enabled": True},
                {"type": "lowshelf", "freq_hz": 260, "gain_db": 1.5, "q": 0.707, "enabled": True},
                {"type": "bell", "freq_hz": 2400, "gain_db": 2.5, "q": 0.8, "enabled": True},
                {"type": "highshelf", "freq_hz": 5200, "gain_db": 1.0, "q": 0.707, "enabled": True},
            ],
            "reverb_mix": 0.20,
            "reverb_predelay_ms": 20.0,
            "width": 0.0,
        })
        before, after = self._paths("v3")
        report = audition.render_audition_pair(
            request, before, after, self.cache, variant="after"
        )
        self.assertTrue(report["request_features"]["uses_v3"])
        self.assertEqual(report["request_features"]["eq_band_count"], 4)
        self.assertEqual(report["request_features"]["reverb_sends"], [0.35, 0.65])
        self.assertEqual(report["request_features"]["reverb_predelay_ms"], 20.0)
        self.assertEqual(report["request_features"]["width"], 0.0)
        self.assertEqual(report["v3_compatibility"]["status"], "native")
        self.assertEqual(report["v3_compatibility"]["shimmed_fields"], [])
        self.assertTrue(all(report["v3_compatibility"]["backend_fields"].values()))
        self.assertTrue(report["reverb_edc"]["measured"])
        audio, _rate = _read(after)
        self.assertLess(float(np.max(np.abs(audio[:, 0] - audio[:, 1]))), 2e-3)

    def test_ab_both_retain_bus_width_when_vocal_effects_are_bypassed(self):
        request = copy.deepcopy(self.request)
        request.update({'eq_bands': [], 'eq_low_cut_hz': 0.0,
                        'eq_presence_db': 0.0, 'compression_strength': 0.0,
                        'reverb_mix': 0.0, 'width': 0.0})
        before, after = self._paths('width-ab')
        audition.render_audition_pair(request, before, after, self.cache, variant='both')
        dry, _ = _read(before)
        wet, _ = _read(after)
        self.assertLess(float(np.max(np.abs(dry[:, 0] - dry[:, 1]))), 1e-5)
        self.assertLess(float(np.max(np.abs(dry - wet))), 1e-5)

    def test_audition_matches_export_across_limiter_block_boundary(self):
        rng = np.random.default_rng(94)
        noise = rng.normal(0, .01, (self.rate * 4, 2)).astype(np.float32)
        vocal = self.root / 'noise.wav'
        backing = self.root / 'silence.wav'
        _write(vocal, noise, self.rate)
        _write(backing, np.zeros_like(noise), self.rate)
        request = _request(self.root, [{'path': str(vocal), 'gain_db': 0,
                           'offset_seconds': 0, 'reverb_send': 1}], backing)
        request.update({'instrumental_db': 0, 'eq_bands': [], 'eq_low_cut_hz': 0,
                        'eq_presence_db': 0, 'width': 1.0,
                        'preview_start_seconds': 1.0, 'preview_duration_seconds': 2.0})
        exported = self.root / 'expected-segment.wav'
        request['output_path'] = str(exported)
        audio_mixdown.render(request)
        request['output_path'] = str(self.root / 'unused.wav')
        before, after = self._paths('block-alignment')
        audition.render_audition_pair(request, before, after, self.cache, variant='after')
        expected, _ = _read(exported)
        observed, _ = _read(after)
        self.assertEqual(expected.shape, observed.shape)
        self.assertLess(float(np.max(np.abs(expected - observed))), 1e-6)

    def test_preview_clamps_eq_frequency_same_as_export(self):
        request = copy.deepcopy(self.request)
        band = {'type': 'highshelf', 'freq_hz': 12000.0, 'gain_db': 6.0,
                'q': .707, 'enabled': True}
        request.update({'eq_bands': [band], 'eq_low_cut_hz': 0, 'width': 1.0})
        before, high = self._paths('eq-high')
        report = audition.render_audition_pair(request, before, high, self.cache, variant='after')
        request['eq_bands'][0]['freq_hz'] = self.rate * .45
        before, clamped = self._paths('eq-clamped')
        audition.render_audition_pair(request, before, clamped, self.cache, variant='after')
        one, _ = _read(high)
        two, _ = _read(clamped)
        self.assertLess(float(np.max(np.abs(one - two))), 1e-6)
        self.assertTrue(any('限制' in warning for warning in report['warnings']))

    def test_hot_excerpt_matches_full_export_at_limiter_lookahead(self):
        t = np.arange(self.rate * 5, dtype=np.float64) / self.rate
        vocal_audio = (.78 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        backing_audio = (.76 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
        # A loud transient immediately after the excerpt must affect the
        # limiter before the boundary, just as it does in the complete export.
        vocal_audio[int(3.002 * self.rate):int(3.006 * self.rate)] = .99
        vocal = self.root / 'hot-vocal.wav'
        backing = self.root / 'hot-backing.wav'
        _write(vocal, np.column_stack((vocal_audio, vocal_audio)), self.rate)
        _write(backing, np.column_stack((backing_audio, backing_audio)), self.rate)
        request = _request(self.root, [{'path': str(vocal), 'gain_db': 0,
                           'offset_seconds': 0, 'reverb_send': 1}], backing)
        request.update({'instrumental_db': 0, 'eq_bands': [], 'eq_low_cut_hz': 0,
                        'eq_presence_db': 0, 'width': 1.0})
        full_path = self.root / 'hot-full-export.wav'
        full_request = copy.deepcopy(request)
        full_request.pop('preview_start_seconds')
        full_request.pop('preview_duration_seconds')
        full_request['output_path'] = str(full_path)
        audio_mixdown.render(full_request)
        request.update({'output_path': str(self.root / 'hot-unused.wav'),
                        'preview_start_seconds': 1.0, 'preview_duration_seconds': 2.0})
        before, after = self._paths('hot-limiter-lookahead')
        audition.render_audition_pair(request, before, after, self.cache, variant='after')
        full, _ = _read(full_path)
        observed, _ = _read(after)
        expected = full[self.rate:self.rate + len(observed)]
        self.assertEqual(len(observed), 2 * self.rate)
        self.assertEqual(expected.shape, observed.shape)
        self.assertGreater(float(np.max(np.abs(expected))), .85)
        self.assertLess(float(np.max(np.abs(expected - observed))), 1e-6)

    def test_fractional_predelay_matches_export_at_44100(self):
        rate = 44100
        root = self.root / 'predelay-44100'
        root.mkdir()
        vocal = root / 'vocal.wav'
        backing = root / 'backing.wav'
        t = np.arange(int(rate * .9), dtype=np.float64) / rate
        signal = (.07 * np.sin(2 * np.pi * 330 * t)
                  + .04 * np.sin(2 * np.pi * 3700 * t)).astype(np.float32)
        stereo = np.column_stack((signal, signal))
        _write(vocal, stereo, rate)
        _write(backing, np.zeros_like(stereo), rate)
        request = _request(
            root,
            [{'path': str(vocal), 'gain_db': 0, 'offset_seconds': 0,
              'reverb_send': 1}],
            backing, seconds=.45, reverb=.7)
        request.update({
            'eq_bands': [], 'eq_low_cut_hz': 0, 'eq_presence_db': 0,
            'reverb_profile': 'wide', 'reverb_predelay_ms': 15.0,
            'width': 1.0, 'preview_start_seconds': .1,
            'preview_duration_seconds': .45,
        })
        exported = root / 'export.wav'
        request['output_path'] = str(exported)
        audio_mixdown.render(request)
        request['output_path'] = str(root / 'unused.wav')
        before, after = self._paths('predelay-44100')
        audition.render_audition_pair(
            request, before, after, self.cache, variant='after')
        expected_audio, _ = _read(exported)
        observed, _ = _read(after)
        self.assertEqual(audio_mixdown._predelay_frames(15.0, rate), 662)
        self.assertEqual(expected_audio.shape, observed.shape)
        self.assertLess(
            float(np.max(np.abs(expected_audio - observed))), 1e-6)

    def test_low_frequency_high_q_eq_keeps_transient_history(self):
        rate = 16000
        root = self.root / 'low-q-eq-history'
        root.mkdir()
        vocal = root / 'vocal.wav'
        backing = root / 'backing.wav'
        frames = int(rate * 1.3)
        rng = np.random.default_rng(817)
        noise = np.zeros(frames, dtype=np.float32)
        noise[:int(rate * .18)] = rng.normal(0, .012, int(rate * .18))
        _write(vocal, noise, rate)
        _write(backing, np.zeros(frames, dtype=np.float32), rate)
        request = _request(
            root,
            [{'path': str(vocal), 'gain_db': 0, 'offset_seconds': 0,
              'reverb_send': 1}],
            backing, seconds=.25)
        request.update({
            'eq_bands': [{
                'type': 'bell', 'freq_hz': 10.0, 'gain_db': 18.0,
                'q': 18.0, 'enabled': True,
            }],
            'eq_low_cut_hz': 0, 'eq_presence_db': 0,
            'compression_strength': 0, 'reverb_mix': 0, 'width': 1.0,
            'preview_start_seconds': .8, 'preview_duration_seconds': .25,
        })
        default_settings = audio_mixdown._settings(self.request)
        eq_settings = audio_mixdown._settings(request)
        self.assertAlmostEqual(
            audition._audition_preroll_seconds(default_settings, rate, False),
            audition.AUDITION_PREROLL_SECONDS)
        self.assertGreater(
            audition._audition_preroll_seconds(eq_settings, rate, False),
            audition.AUDITION_PREROLL_SECONDS * 4)

        exported = root / 'export.wav'
        request['output_path'] = str(exported)
        audio_mixdown.render(request)
        request['output_path'] = str(root / 'unused.wav')
        before, after = self._paths('low-q-eq-history')
        audition.render_audition_pair(
            request, before, after, self.cache, variant='after')
        expected_audio, _ = _read(exported)
        observed, _ = _read(after)
        self.assertEqual(expected_audio.shape, observed.shape)
        self.assertGreater(float(np.max(np.abs(expected_audio))), 1e-6)
        self.assertLess(
            float(np.max(np.abs(expected_audio - observed))), 1e-6)

    def test_reverb_tail_budget_includes_predelay_in_both_timelines(self):
        rate = 44100
        root = self.root / 'tail-budget-predelay'
        root.mkdir()
        seconds = .12
        vocal = root / 'vocal.wav'
        backing = root / 'backing.wav'
        _write(vocal, _tone(seconds, rate, 440, .08), rate)
        _write(backing, np.zeros(int(round(seconds * rate)), dtype=np.float32), rate)
        request = _request(
            root,
            [{'path': str(vocal), 'gain_db': 0, 'offset_seconds': 0,
              'reverb_send': 1}],
            backing, seconds=.1, reverb=.5)
        request.update({
            'eq_bands': [], 'eq_low_cut_hz': 0, 'eq_presence_db': 0,
            'reverb_profile': 'wide', 'reverb_predelay_ms': 15.0,
            'width': 1.0, 'preview_start_seconds': 0,
            'preview_duration_seconds': .1,
        })
        base_frames = int(round(seconds * rate))
        delay_frames = audio_mixdown._predelay_frames(15.0, rate)
        expected_maximum = (
            base_frames + int(round(audio_mixdown.MAX_REVERB_TAIL_SECONDS * rate))
            + delay_frames)

        progress = []
        full_request = copy.deepcopy(request)
        full_request.pop('preview_start_seconds')
        full_request.pop('preview_duration_seconds')
        full_request['output_path'] = str(root / 'full-export.wav')
        audio_mixdown.render(full_request, progress=progress.append)
        self.assertTrue(progress)
        self.assertEqual(progress[-1]['frames_total'], expected_maximum)

        request['output_path'] = str(root / 'unused.wav')
        before, after = self._paths('tail-budget')
        report = audition.render_audition_pair(
            request, before, after, self.cache, variant='after')
        self.assertAlmostEqual(
            report['after']['maximum_source_duration_seconds'],
            expected_maximum / float(rate), places=6)
        self.assertAlmostEqual(report['after']['source_duration_seconds'], seconds, places=6)
        self.assertFalse(report['after']['source_duration_measured'])

    def test_both_variants_end_at_measured_export_tail(self):
        rate = self.rate
        seconds = .6
        vocal = self.root / 'quiet-tail-vocal.wav'
        backing = self.root / 'quiet-tail-backing.wav'
        _write(vocal, _tone(seconds, rate, 440, .002), rate)
        _write(backing, np.zeros(int(seconds * rate), dtype=np.float32), rate)
        request = _request(self.root, [{'path': str(vocal), 'gain_db': 0,
                           'offset_seconds': 0, 'reverb_send': 1}], backing)
        request.update({'instrumental_db': 0, 'eq_bands': [], 'eq_low_cut_hz': 0,
                        'eq_presence_db': 0, 'width': 1.0, 'reverb_mix': .01,
                        'preview_start_seconds': 0, 'preview_duration_seconds': 5.0})
        full_request = copy.deepcopy(request)
        full_request.pop('preview_start_seconds')
        full_request.pop('preview_duration_seconds')
        full_request['output_path'] = str(self.root / 'quiet-full.wav')
        audio_mixdown.render(full_request)
        request['output_path'] = str(self.root / 'quiet-unused.wav')
        before, after = self._paths('quiet-tail')
        report = audition.render_audition_pair(request, before, after, self.cache, variant='both')
        full, _ = _read(full_request['output_path'])
        dry, _ = _read(before)
        wet, _ = _read(after)
        self.assertTrue(report['after']['source_duration_measured'])
        self.assertEqual(len(full), len(wet))
        self.assertEqual(len(dry), len(wet))
        self.assertLess(len(wet), 5 * rate)
        self.assertAlmostEqual(report['after']['source_duration_seconds'], len(full) / rate)
        self.assertLess(float(np.max(np.abs(full - wet))), 1e-6)

        # The conservative upper bound is not itself a valid playback range.
        request['preview_start_seconds'] = len(full) / rate + .1
        request['preview_duration_seconds'] = .4
        before, after = self._paths('past-quiet-tail')
        with self.assertRaisesRegex(audio_mixdown.MixdownError, '超过实际混音长度'):
            audition.render_audition_pair(request, before, after, self.cache, variant='after')
        self.assertFalse(after.exists())

    def test_decoder_change_invalidates_decoded_cache(self):
        first_before, first_after = self._paths('decoder-first')
        same_before, same_after = self._paths('decoder-same')
        new_before, new_after = self._paths('decoder-changed')
        with mock.patch.object(audition, '_decoder_identity', return_value={'path': 'decoder-a'}):
            first = audition.render_audition_pair(
                self.request, first_before, first_after, self.cache, variant='after')
            same = audition.render_audition_pair(
                self.request, same_before, same_after, self.cache, variant='after')
        with mock.patch.object(audition, '_decoder_identity', return_value={'path': 'decoder-b'}):
            changed = audition.render_audition_pair(
                self.request, new_before, new_after, self.cache, variant='after')
        self.assertFalse(first['cache_hit'])
        self.assertTrue(same['cache_hit'])
        self.assertFalse(changed['cache_hit'])
        self.assertNotEqual(first['cache_generation'], changed['cache_generation'])

    def test_audition_preserves_prior_note_reverb_tail(self):
        impulse = np.zeros((self.rate * 4, 2), dtype=np.float32)
        t = np.arange(self.rate // 10) / self.rate
        impulse[:len(t), :] = (.15 * np.sin(2 * np.pi * 440 * t))[:, None]
        vocal = self.root / 'short-note.wav'
        backing = self.root / 'silent-backing.wav'
        _write(vocal, impulse, self.rate)
        _write(backing, np.zeros_like(impulse), self.rate)
        request = _request(self.root, [{'path': str(vocal), 'gain_db': 0,
                           'offset_seconds': 0, 'reverb_send': 1}], backing)
        request.update({'instrumental_db': 0, 'eq_bands': [], 'eq_low_cut_hz': 0,
                        'eq_presence_db': 0, 'width': 1.0, 'reverb_mix': .8,
                        'reverb_profile': 'wide', 'reverb_predelay_ms': 35.0,
                        'preview_start_seconds': .8,
                        'preview_duration_seconds': .8})
        exported = self.root / 'reverb-tail-export.wav'
        request['output_path'] = str(exported)
        audio_mixdown.render(request)
        request['output_path'] = str(self.root / 'unused-reverb.wav')
        before, after = self._paths('reverb-tail')
        audition.render_audition_pair(request, before, after, self.cache, variant='after')
        expected, _ = _read(exported)
        observed, _ = _read(after)
        self.assertGreater(float(np.sqrt(np.mean(expected ** 2))), .001)
        self.assertLess(float(np.max(np.abs(expected - observed))), 1e-6)

    def test_mono_preview_and_end_tail_match_export(self):
        vocal = self.root / 'mono-short.wav'
        backing = self.root / 'mono-backing.wav'
        _write(vocal, _tone(.20, self.rate, 440, .15), self.rate)
        _write(backing, np.zeros(int(.60 * self.rate), dtype=np.float32), self.rate)
        request = _request(self.root, [{'path': str(vocal), 'gain_db': 0,
                           'offset_seconds': 0, 'reverb_send': 1}], backing)
        request.update({'instrumental_db': 0, 'eq_bands': [], 'eq_low_cut_hz': 0,
                        'eq_presence_db': 0, 'width': 1.0, 'reverb_mix': .6,
                        'reverb_profile': 'wide', 'preview_start_seconds': .58,
                        'preview_duration_seconds': .8})
        exported = self.root / 'mono-end-export.wav'
        request['output_path'] = str(exported)
        export_report = audio_mixdown.render(request)
        request['output_path'] = str(self.root / 'unused-mono.wav')
        before, after = self._paths('mono-end')
        report = audition.render_audition_pair(request, before, after, self.cache, variant='after')
        expected, _ = _read(exported)
        observed, _ = _read(after)
        self.assertEqual(export_report['channels'], 1)
        self.assertEqual(report['after']['channels'], 1)
        self.assertEqual(expected.shape, observed.shape)
        self.assertGreater(float(np.sqrt(np.mean(expected ** 2))), .001)
        self.assertLess(float(np.max(np.abs(expected - observed))), 1e-6)

    def test_pan_alone_selects_same_v3_audio_path_as_export(self):
        request = copy.deepcopy(self.request)
        for track in request['vocals']:
            track.pop('reverb_send', None)
            track.pop('solo', None)
            track.pop('mute', None)
        request.update({'eq_low_cut_hz': 0, 'eq_presence_db': 1.5,
                        'reverb_mix': .4, 'compression_strength': .6})
        exported = self.root / 'pan-only-export.wav'
        request['output_path'] = str(exported)
        export_report = audio_mixdown.render(request)
        request['output_path'] = str(self.root / 'unused-pan.wav')
        before, after = self._paths('pan-only')
        report = audition.render_audition_pair(request, before, after, self.cache, variant='after')
        expected, _ = _read(exported)
        observed, _ = _read(after)
        self.assertTrue(export_report['uses_v3'])
        self.assertTrue(report['request_features']['uses_v3'])
        self.assertLess(float(np.max(np.abs(expected - observed))), 1e-6)

    def test_v3_shim_branch_remains_supported_for_legacy_backend(self):
        request = copy.deepcopy(self.request)
        request.update({
            "eq_bands": [
                {"type": "bell", "freq_hz": 1800, "gain_db": 2.0,
                 "q": 0.8, "enabled": True},
            ],
            "reverb_predelay_ms": 15.0,
            "width": 0.75,
        })
        capabilities = {
            "eq_bands": False,
            "reverb_predelay_ms": False,
            "width": False,
            "vocal_reverb_send": False,
        }
        before, after = self._paths("v3-shim")
        with mock.patch.object(
                audition, "_backend_capabilities", return_value=capabilities):
            report = audition.render_audition_pair(
                request, before, after, self.cache, variant="after"
            )
        compatibility = report["v3_compatibility"]
        self.assertEqual(compatibility["status"], "audition_shim")
        self.assertEqual(
            set(compatibility["shimmed_fields"]),
            {"eq_bands", "vocals[].reverb_send", "reverb_predelay_ms", "width"},
        )
        self.assertTrue(any("兼容层" in item for item in report["warnings"]))
        self.assertEqual(report["request_features"]["width"], 0.75)
        self.assertTrue(after.is_file())
        self.assertFalse(before.exists())

    def test_segment_and_repeated_renders_have_stable_cache_identity(self):
        request = _v3_request(self.request)
        before1, after1 = self._paths("identity-repeat-1")
        first = audition.render_audition_pair(
            request, before1, after1, self.cache, variant="after"
        )
        first_identity = first["cache_identity"]
        self.assertFalse(first_identity["processed_audio_cached"])
        self.assertTrue(
            {"eq_bands", "vocals[].reverb_send", "compression_strength",
             "width", "reverb_predelay_ms"}.issubset(
                set(first_identity["tone_fields"]))
        )
        self.assertEqual(first["rendered_variants"], ["after"])
        self.assertNotIn("before", first)
        self.assertEqual(first["reverb_edc"], first["after"]["reverb_edc"])

        before2, after2 = self._paths("identity-repeat-2")
        repeated = audition.render_audition_pair(
            request, before2, after2, self.cache, variant="after"
        )
        repeated_identity = repeated["cache_identity"]
        self.assertTrue(repeated["cache_hit"])
        self.assertTrue(repeated["reverb_edc"]["cache_hit"])
        self.assertEqual(
            repeated_identity["tone_cache_key"],
            first_identity["tone_cache_key"],
        )
        self.assertEqual(
            repeated_identity["segment_cache_key"],
            first_identity["segment_cache_key"],
        )
        self.assertEqual(
            repeated_identity["render_cache_key"],
            first_identity["render_cache_key"],
        )
        audio1, rate1 = _read(after1)
        audio2, rate2 = _read(after2)
        self.assertEqual(rate1, rate2)
        np.testing.assert_allclose(audio1, audio2, rtol=0.0, atol=1e-7)

        request["preview_start_seconds"] = 0.85
        before3, after3 = self._paths("identity-segment")
        moved = audition.render_audition_pair(
            request, before3, after3, self.cache, variant="after"
        )
        moved_identity = moved["cache_identity"]
        self.assertTrue(moved["cache_hit"])
        self.assertAlmostEqual(moved["preview_start_seconds"], 0.85, places=4)
        self.assertAlmostEqual(moved["after"]["preview_start_seconds"], 0.85, places=4)
        self.assertEqual(
            moved_identity["tone_cache_key"],
            first_identity["tone_cache_key"],
        )
        self.assertNotEqual(
            moved_identity["segment_cache_key"],
            first_identity["segment_cache_key"],
        )
        self.assertNotEqual(
            moved_identity["render_cache_key"],
            first_identity["render_cache_key"],
        )
        self.assertFalse(before3.exists())
        self.assertTrue(after3.is_file())
        self.assertEqual(_audition_artifacts(after1.parent), [])

    def test_cache_identity_covers_each_v3_tone_parameter(self):
        base = _v3_request(self.request)
        before, after = self._paths("identity-tone-base")
        baseline = audition.render_audition_pair(
            base, before, after, self.cache, variant="after"
        )
        baseline_identity = baseline["cache_identity"]
        mutations = [
            ("eq_bands", lambda value: value["eq_bands"][1].update(
                freq_hz=2800.0, gain_db=-2.5)),
            ("vocal_reverb_send", lambda value: (
                value["vocals"][0].update(reverb_send=0.12),
                value["vocals"][1].update(reverb_send=0.88),
            )),
            ("compression_strength", lambda value: value.update(
                compression_strength=0.78)),
            ("width", lambda value: value.update(width=0.41)),
            ("reverb_predelay_ms", lambda value: value.update(
                reverb_predelay_ms=43.0)),
        ]
        tone_keys = {baseline_identity["tone_cache_key"]}
        render_keys = {baseline_identity["render_cache_key"]}
        generations = {baseline["cache_generation"]}
        for index, (label, mutate) in enumerate(mutations, 1):
            request = copy.deepcopy(base)
            mutate(request)
            side_before, side_after = self._paths("identity-tone-%d" % index)
            report = audition.render_audition_pair(
                request, side_before, side_after, self.cache, variant="after"
            )
            identity = report["cache_identity"]
            self.assertTrue(report["cache_hit"], label)
            self.assertEqual(report["decode_cache_source"], "memory", label)
            self.assertEqual(
                identity["segment_cache_key"],
                baseline_identity["segment_cache_key"],
                label,
            )
            self.assertNotIn(identity["tone_cache_key"], tone_keys, label)
            self.assertNotIn(identity["render_cache_key"], render_keys, label)
            tone_keys.add(identity["tone_cache_key"])
            render_keys.add(identity["render_cache_key"])
            generations.add(report["cache_generation"])
            self.assertFalse(side_before.exists())
        self.assertEqual(len(generations), 1)
        self.assertEqual(_audition_artifacts(after.parent), [])

    def test_single_side_skips_unused_output_but_default_still_renders_both(self):
        request = copy.deepcopy(self.request)
        before1, after1 = self._paths("single-after")
        first = audition.render_audition_pair(
            request, before1, after1, self.cache, variant="after"
        )
        self.assertEqual(first["rendered_variants"], ["after"])
        self.assertIn("after", first)
        self.assertNotIn("before", first)
        self.assertTrue(after1.is_file())
        self.assertFalse(before1.exists())

        request["vocals"][1]["gain_db"] = -7.0
        before2, after2 = self._paths("single-before")
        second = audition.render_audition_pair(
            request, before2, after2, self.cache, variant="before"
        )
        self.assertEqual(second["rendered_variants"], ["before"])
        self.assertIn("before", second)
        self.assertNotIn("after", second)
        self.assertTrue(before2.is_file())
        self.assertFalse(after2.exists())
        self.assertFalse(second["reverb_edc"]["measured"])
        self.assertEqual(
            second["reverb_edc"]["reason"], "after_variant_not_requested"
        )

        before3, after3 = self._paths("both")
        both = audition.render_audition_pair(request, before3, after3, self.cache)
        self.assertEqual(both["rendered_variants"], ["before", "after"])
        self.assertTrue(before3.is_file() and after3.is_file())

    def test_cancellation_and_existing_target_are_atomic(self):
        warm_before, warm_after = self._paths("cancel-warm")
        audition.render_audition_pair(
            copy.deepcopy(self.request), warm_before, warm_after,
            self.cache, variant="after",
        )
        cancellation = {"calls": 0}

        def cancel():
            cancellation["calls"] += 1
            return cancellation["calls"] >= 5

        before, after = self._paths("cancel")
        with self.assertRaises(audio_mixdown.MixdownCancelled):
            audition.render_audition_pair(
                copy.deepcopy(self.request), before, after,
                self.cache, cancel=cancel, variant="after",
            )
        self.assertFalse(before.exists() or after.exists())
        self.assertEqual(_audition_artifacts(after.parent), [])
        self.assertEqual(list(self.cache.rglob("s-*")), [])
        self.assertEqual(list(self.cache.rglob("*.tmp")), [])

        existing = self._paths("existing")[1]
        original = b"do-not-replace"
        existing.write_bytes(original)
        with self.assertRaises(FileExistsError):
            audition.render_audition_pair(
                copy.deepcopy(self.request), self.root / "unused.wav", existing,
                self.cache, variant="after",
            )
        self.assertEqual(existing.read_bytes(), original)

    def test_thirty_second_window_is_the_engine_upper_bound(self):
        rate = 8000
        root = self.root / "max-window"
        root.mkdir()
        vocals = [root / "v1.wav", root / "v2.wav"]
        _write(vocals[0], _tone(45.0, rate, 170, 0.12), rate)
        _write(vocals[1], _tone(45.0, rate, 290, 0.10), rate)
        backing = root / "backing.wav"
        _write(backing, _tone(45.0, rate, 95, 0.08, channels=2), rate)
        rows = [
            {"path": str(vocals[0]), "gain_db": -1.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": -0.1, "reverb_send": 0.4},
            {"path": str(vocals[1]), "gain_db": -2.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": 0.15, "reverb_send": 0.6},
        ]
        request = _v3_request(_request(root, rows, backing, seconds=45.0))
        request["preview_start_seconds"] = 0.0
        request["preview_duration_seconds"] = 45.0
        after = root / "after.wav"
        report = audition.render_audition_pair(
            request, root / "unused-before.wav", after, root / "cache",
            variant="after",
        )
        self.assertEqual(
            report["preview_duration_seconds"],
            audition.AUDITION_MAX_SEGMENT_SECONDS,
        )
        self.assertAlmostEqual(report["after"]["duration_seconds"], 30.0, places=4)
        audio, output_rate = _read(after)
        self.assertEqual(output_rate, rate)
        self.assertEqual(len(audio), 30 * rate)
        self.assertEqual(report["cache_identity"]["processed_audio_cached"], False)
        self.assertEqual(_audition_artifacts(after.parent), [])

    def test_cached_twelve_second_after_only_meets_latency_target(self):
        rate = 8000
        long_root = self.root / "latency"
        long_root.mkdir()
        vocals = [long_root / "v1.wav", long_root / "v2.wav"]
        _write(vocals[0], _tone(12.0, rate, 170, 0.12), rate)
        _write(vocals[1], _tone(12.0, rate, 290, 0.10), rate)
        backing = long_root / "b.wav"
        _write(backing, _tone(12.0, rate, 95, 0.08, channels=2), rate)
        rows = [
            {"path": str(vocals[0]), "gain_db": -1.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": -0.1, "reverb_send": 0.4},
            {"path": str(vocals[1]), "gain_db": -2.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": 0.15, "reverb_send": 0.6},
        ]
        request = _request(long_root, rows, backing, seconds=12.0, reverb=0.18)
        request['preview_start_seconds'] = 0.0
        request.update({
            "eq_bands": [
                {"type": "highpass", "freq_hz": 80, "gain_db": 0, "q": 0.707, "enabled": True},
                {"type": "bell", "freq_hz": 2200, "gain_db": 2.0, "q": 0.8, "enabled": True},
            ],
            "reverb_predelay_ms": 20.0,
            "width": 0.85,
            "compression_strength": 0.4,
        })
        cache = long_root / "cache"
        first = audition.render_audition_pair(
            request, long_root / "b1.wav", long_root / "a1.wav", cache,
            variant="after",
        )
        self.assertFalse(first["cache_hit"])
        started = time.perf_counter()
        second = audition.render_audition_pair(
            request, long_root / "b2.wav", long_root / "a2.wav", cache,
            variant="after",
        )
        elapsed = time.perf_counter() - started
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["decode_cache_source"], "memory")
        self.assertTrue(second["reverb_edc"]["cache_hit"])
        self.assertEqual(second["preview_duration_seconds"], 12.0)
        self.assertLess(elapsed, 0.350, "cached 12 s after-only render: %.3f s" % elapsed)
        self.assertLess(second["timings_ms"]["total"], 350.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
