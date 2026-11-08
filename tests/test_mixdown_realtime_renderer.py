"""Contracts for the persistent four-track PRE-LIMITER block renderer."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True

APP = Path(__file__).resolve().parents[1]
PEDALBOARD_ENV = "VWA_MIX_TEST_PEDALBOARD_PATH"
FFMPEG_ENV = "VWA_MIX_TEST_FFMPEG_PATH"

if not os.environ.get(PEDALBOARD_ENV):
    installed = APP / "dependencies/optional/pedalboard/current/packages"
    if installed.is_dir():
        os.environ[PEDALBOARD_ENV] = str(installed)
    else:
        print(
            "SKIP: " + PEDALBOARD_ENV
            + " is not set and Pedalboard is not installed."
        )
        raise SystemExit(0)


def _pedalboard_import_root(value):
    root = Path(value).expanduser()
    if not root.exists():
        raise RuntimeError(PEDALBOARD_ENV + " does not exist: " + str(root))
    candidates = [root]
    if root.is_dir():
        candidates.extend((root / "unpacked", root / "site-packages"))
        candidates.extend(sorted(path for path in root.iterdir() if path.is_dir()))
    for candidate in candidates:
        if (candidate / "pedalboard").is_dir():
            return candidate.resolve()
        if (candidate / "pedalboard.py").is_file():
            return candidate.resolve()
    raise RuntimeError(
        PEDALBOARD_ENV + " must identify an importable Pedalboard package"
    )


try:
    pedalboard_root = _pedalboard_import_root(os.environ[PEDALBOARD_ENV])
except RuntimeError as error:
    print("ERROR: " + str(error))
    raise SystemExit(1)

sys.path[:0] = [str(pedalboard_root), str(APP / "src")]
try:
    import numpy as np
    import soundfile as sf
    import pedalboard  # noqa: F401 - dependency is injected explicitly below
    import audio_mixdown as backend
    import audio_mixdown_audition as audition
    import mixdown_realtime_renderer as realtime
except Exception as error:
    print(
        "ERROR: cannot import realtime renderer runtime: "
        + type(error).__name__ + ": " + str(error)
    )
    raise SystemExit(1)


def _tone(seconds, rate, frequency, amplitude):
    frames = int(round(seconds * rate))
    time_axis = np.arange(frames, dtype=np.float64) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * time_axis)).astype(
        np.float32
    )


def _write(path, samples, rate):
    sf.write(str(path), np.asarray(samples, dtype=np.float32), int(rate),
             subtype="FLOAT")


class PreLimiterMixSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = Path(os.environ.get(FFMPEG_ENV, "")).expanduser()
        if not str(os.environ.get(FFMPEG_ENV, "")).strip():
            try:
                cls.ffmpeg = backend._ffmpeg_executable()
            except Exception:
                cls.ffmpeg = Path(shutil.which("ffmpeg") or "")
        if not cls.ffmpeg.is_file():
            print(
                "SKIP: FFmpeg is unavailable; set " + FFMPEG_ENV
                + " to an existing executable."
            )
            raise SystemExit(0)

    def setUp(self):
        temp_root = APP / "work/temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(
            prefix="vwa-prelimiter-", dir=temp_root,
        )
        self.root = Path(self.temp.name)
        self.cache = self.root / "decode-cache"
        self.rate = 48000
        self.seconds = 0.82
        self.sources = []
        for index in range(4):
            path = self.root / ("track-%d.wav" % index)
            _write(
                path,
                _tone(
                    self.seconds, self.rate, 173.0 + index * 91.0,
                    0.055 + index * 0.012,
                ),
                self.rate,
            )
            self.sources.append(path)
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.temp.cleanup()

    def _request(self, **overrides):
        request = {
            "instrumental_path": str(self.sources[3]),
            "output_path": str(self.root / "planned-output.wav"),
            "instrumental_db": -5.5,
            "instrumental_mute": False,
            "instrumental_solo": False,
            "instrumental_pan": 0.0,
            "vocals": [
                {
                    "path": str(path),
                    "gain_db": -3.0 + index * 1.2,
                    "offset_seconds": (0.037, 0.011, 0.063)[index],
                    "mute": False,
                    "solo": False,
                    "pan": (-0.22, 0.16, 0.0)[index],
                    "reverb_send": (0.25, 0.7, 0.48)[index],
                }
                for index, path in enumerate(self.sources[:3])
            ],
            "eq_bands": [
                {
                    "type": "highpass", "freq_hz": 68.0, "gain_db": 0.0,
                    "q": 0.707, "enabled": True,
                },
                {
                    "type": "bell", "freq_hz": 1350.0, "gain_db": 1.8,
                    "q": 0.82, "enabled": True,
                },
            ],
            "eq_low_cut_hz": 0.0,
            "eq_presence_db": 0.0,
            "compression_strength": 0.52,
            "reverb_mix": 0.24,
            "reverb_profile": "wide",
            "reverb_predelay_ms": 23.0,
            "width": 0.84,
        }
        request.update(overrides)
        return request

    def _session(self, request):
        session = realtime.PreLimiterMixSession(
            request, self.cache, numpy=np, soundfile=sf,
            pedalboard=pedalboard, ffmpeg=self.ffmpeg,
        )
        self.sessions.append(session)
        return session

    @staticmethod
    def _render_blocks(session, pattern):
        blocks = []
        remaining = int(session.duration_frames) - int(session.position_frame)
        index = 0
        while remaining > 0:
            count = min(int(pattern[index % len(pattern)]), remaining)
            tail = remaining - count
            if 0 < tail < realtime.MIN_STATEFUL_BLOCK_FRAMES:
                count += tail
            block = session.render(count)
            blocks.append(block)
            remaining -= count
            index += 1
        return np.concatenate(blocks, axis=0) if blocks else np.zeros(
            (0, session.channels), dtype=np.float32
        )

    def _reference(self, session, label):
        settings = session._request_settings
        prepared = session._prepared
        target = self.root / (label + ".wav")
        backend._render_mix_v3(
            settings,
            list(zip(prepared.vocal_paths, prepared.vocal_frames)),
            prepared.backing_path,
            session.sample_rate,
            session.channels,
            prepared.backing_frames,
            target,
            pedalboard,
            np,
            sf,
            None,
        )
        audio, rate = sf.read(str(target), always_2d=True, dtype="float32")
        self.assertEqual(rate, session.sample_rate)
        return audio

    def test_small_blocks_match_existing_pre_limiter_with_real_effects(self):
        session = self._session(self._request())
        actual = self._render_blocks(session, (127, 251, 509, 73))
        expected = self._reference(session, "pre-limiter-reference")
        self.assertEqual(actual.shape, (session.duration_frames, 2))
        self.assertEqual(actual.dtype, np.float32)
        self.assertTrue(actual.flags.c_contiguous)
        self.assertTrue(np.isfinite(actual).all())
        self.assertEqual(expected.shape, actual.shape)
        difference = np.abs(actual.astype(np.float64) - expected.astype(np.float64))
        maximum_difference = float(difference.max(initial=0.0))
        self.assertLess(
            maximum_difference, 5.0e-5,
            "small-block output diverged from CHUNK_FRAMES pre-limiter path",
        )

        # Pedalboard infers channels from the smaller array dimension.  Shapes
        # (2,1) and (2,2) are therefore unsafe for continuous stateful blocks.
        micro = self._session(self._request())
        for short_frames in (0, 1, 2, 3):
            with self.subTest(short_frames=short_frames):
                with self.assertRaises(ValueError):
                    micro.render(short_frames)
                self.assertEqual(micro.position_frame, 0)

        for block_frames in (4, 31, 32, 33, 127):
            with self.subTest(block_frames=block_frames):
                sized = self._session(self._request())
                short = []
                total = (2048 // block_frames) * block_frames
                remaining = total
                while remaining:
                    short.append(sized.render(block_frames))
                    remaining -= block_frames
                short = np.concatenate(short, axis=0)
                self.assertEqual(short.shape, (total, 2))
                np.testing.assert_array_equal(short, expected[:total])

        seek_frame = 7000
        sought = self._session(self._request())
        sought.seek(seek_frame, preroll_frames=seek_frame)
        sought_audio = self._render_blocks(sought, (127, 251, 509, 73))
        self.assertEqual(sought_audio.shape, expected[seek_frame:].shape)
        self.assertLess(
            float(np.max(np.abs(
                sought_audio - expected[seek_frame:]
            ), initial=0.0)),
            5.0e-5,
        )
        self.assertEqual(sought.position_frame, sought.duration_frames)
        self.assertFalse(sought.snapshot()["last_warmup"]["bounded"])

        long_history = self._session(self._request())
        long_history.seek(32769, preroll_frames=32769)
        self.assertEqual(long_history.position_frame, 32769)
        self.assertEqual(
            long_history.snapshot()["last_warmup"]["warmup_frames"], 32769,
        )
        self.assertFalse(
            long_history.snapshot()["last_warmup"][
                "skipped_below_safe_quantum"
            ],
        )

        short_history = self._session(self._request())
        short_history.seek(2, preroll_frames=2)
        self.assertEqual(short_history.position_frame, 2)
        self.assertEqual(
            short_history.snapshot()["last_warmup"]["warmup_frames"], 0,
        )
        self.assertTrue(
            short_history.snapshot()["last_warmup"][
                "skipped_below_safe_quantum"
            ],
        )
        self.assertEqual(session.position_frame, session.duration_frames)
        self.assertEqual(session.output_stage, "pre_limiter")
        self.assertEqual(session.snapshot()["stage"], "pre_limiter")

    def test_fixed_stereo_mono_semantics_pan_width_and_no_redecode(self):
        request = self._request(
            eq_bands=[], compression_strength=0.0, reverb_mix=0.0,
            width=1.0,
            vocals=[
                {
                    "path": str(path), "gain_db": 0.0,
                    "offset_seconds": offset, "mute": False, "solo": False,
                    "pan": 0.0, "reverb_send": 0.0,
                }
                for path, offset in zip(self.sources[:3], (0.02, 0.0, 0.01))
            ],
        )
        session = self._session(request)
        decoded = (*session._prepared.vocal_paths, session._prepared.backing_path)
        self.assertTrue(all(sf.info(str(path)).channels == 2 for path in decoded))
        mono_upmix_gain = 10.0 ** (realtime.MONO_UPNMIX_DBFS / 20.0)
        for index, path in enumerate(decoded):
            audio, _ = sf.read(str(path), always_2d=True, dtype="float32")
            np.testing.assert_allclose(
                audio[:, 0], audio[:, 1], rtol=0.0, atol=1.0e-7,
                err_msg="mono source was not duplicated explicitly at %d" % index,
            )
            original, _ = sf.read(str(self.sources[index]), dtype="float32")
            np.testing.assert_allclose(
                audio[:, 0], original * mono_upmix_gain,
                rtol=0.0, atol=2.0e-7,
                err_msg="unexpected mono upmix gain at %d" % index,
            )

        actual = self._render_blocks(session, (4, 7, 31, 64, 257))
        offsets = [int(round(value * self.rate)) for value in (0.02, 0.0, 0.01)]
        expected = np.zeros(session.source_end_frame, dtype=np.float64)
        for index, offset in enumerate(offsets):
            samples, _ = sf.read(str(self.sources[index]), dtype="float32")
            destination = max(0, offset)
            source_start = max(0, -offset)
            count = min(len(samples) - source_start, len(expected) - destination)
            expected[destination:destination + count] += samples[
                source_start:source_start + count
            ].astype(np.float64)
        backing, _ = sf.read(str(self.sources[3]), dtype="float32")
        expected[:len(backing)] += (
            backing.astype(np.float64) * (10.0 ** (-5.5 / 20.0))
        )
        np.testing.assert_allclose(
            actual[:len(expected), 0], expected * mono_upmix_gain,
            rtol=0.0, atol=2.0e-7,
        )
        np.testing.assert_allclose(
            actual[:len(expected), 0], actual[:len(expected), 1],
            rtol=0.0, atol=1.0e-7,
        )

        cache_before = {
            path.relative_to(self.cache).as_posix(): (
                path.stat().st_size, path.stat().st_mtime_ns,
            )
            for path in self.cache.rglob("*") if path.is_file()
        }
        rebuild_before = session.effect_rebuild_count
        routed = self._request(
            eq_bands=[], compression_strength=0.0, reverb_mix=0.0,
            width=0.45, instrumental_pan=0.31,
            vocals=[
                {
                    "path": str(path), "gain_db": 0.0,
                    "offset_seconds": offset, "mute": False, "solo": False,
                    "pan": pan, "reverb_send": 0.0,
                }
                for path, offset, pan in zip(
                    self.sources[:3], (0.02, 0.0, 0.01), (-0.6, 0.35, 0.0)
                )
            ],
        )
        with mock.patch.object(
            audition, "_prepare_sources",
            side_effect=AssertionError("pan/width change re-decoded sources"),
        ):
            session.set_parameters(routed)
        self.assertEqual(session.effect_rebuild_count, rebuild_before)
        self.assertEqual(session.position_frame, session.duration_frames)

        session.seek(0, preroll_frames=0)
        generated = self._render_blocks(session, (89, 233, 377))
        reference = self._reference(session, "mono-routed-reference")
        self.assertLess(
            float(np.max(np.abs(generated - reference), initial=0.0)), 5.0e-5,
        )
        cache_after = {
            path.relative_to(self.cache).as_posix(): (
                path.stat().st_size, path.stat().st_mtime_ns,
            )
            for path in self.cache.rglob("*") if path.is_file()
        }
        self.assertEqual(cache_after, cache_before)

    def test_set_parameters_preserves_nonstructural_fx_state_then_warms(self):
        session = self._session(self._request())
        rendered = session.render(4097)
        self.assertEqual(rendered.shape, (4097, 2))
        baseline = session.snapshot()
        processor_ids = (
            id(session._eq_chain),
            id(session._compressor),
            id(session._reverb),
        )
        changed = copy.deepcopy(self._request())
        changed["vocals"][0]["gain_db"] += 2.5
        changed["vocals"][1]["pan"] = -0.55
        changed["vocals"][2]["reverb_send"] = 0.12
        changed["vocals"][2]["mute"] = True
        changed["width"] = 1.35
        session.set_parameters(changed)
        scalar_only = session.snapshot()
        self.assertEqual(
            scalar_only["effect_rebuild_count"],
            baseline["effect_rebuild_count"],
        )
        self.assertEqual(
            scalar_only["position_frame"], baseline["position_frame"],
        )
        self.assertEqual(
            processor_ids,
            (
                id(session._eq_chain),
                id(session._compressor),
                id(session._reverb),
            ),
        )

        structural = copy.deepcopy(changed)
        structural["compression_strength"] = 0.81
        session.set_parameters(structural)
        rebuilt = session.snapshot()
        self.assertEqual(
            rebuilt["effect_rebuild_count"],
            scalar_only["effect_rebuild_count"] + 1,
        )
        self.assertEqual(rebuilt["position_frame"], scalar_only["position_frame"])
        self.assertEqual(
            rebuilt["last_warmup"]["reason"], "structural_parameter_change",
        )
        self.assertGreater(rebuilt["last_warmup"]["warmup_frames"], 0)

    def test_source_identity_cache_and_post_prepare_io_contract(self):
        request = self._request()
        first = self._session(request)
        token = first.source_token
        cache_files_before = sorted(
            path.relative_to(self.cache).as_posix()
            for path in self.cache.rglob("*") if path.is_file()
        )
        with mock.patch.object(
            backend, "_run_ffmpeg",
            side_effect=AssertionError("render started FFmpeg"),
        ):
            first.render(1024)
            changed = copy.deepcopy(request)
            changed["vocals"][0]["gain_db"] += 1.0
            first.set_parameters(changed)
        cache_files_after = sorted(
            path.relative_to(self.cache).as_posix()
            for path in self.cache.rglob("*") if path.is_file()
        )
        self.assertEqual(cache_files_after, cache_files_before)
        first.close()

        with mock.patch.object(
            backend, "_decode_to_float_wav",
            side_effect=AssertionError("warm cache decoded a source again"),
        ):
            second = realtime.PreLimiterMixSession(
                request, self.cache, numpy=np, soundfile=sf,
                pedalboard=pedalboard, ffmpeg=self.ffmpeg,
            )
        self.sessions.append(second)
        self.assertEqual(second.source_token, token)
        self.assertTrue(second.snapshot()["cache_hit"])
        self.assertIn(
            second.snapshot()["cache_source"], {"memory", "disk"},
        )

        reordered = copy.deepcopy(request)
        reordered["vocals"][0], reordered["vocals"][1] = (
            reordered["vocals"][1], reordered["vocals"][0]
        )
        with self.assertRaisesRegex(ValueError, "源路径或轨道顺序"):
            second.set_parameters(reordered)
        moved = copy.deepcopy(request)
        moved["vocals"][0]["path"] = str(self.sources[2])
        with self.assertRaisesRegex(ValueError, "源路径或轨道顺序"):
            second.set_parameters(moved)

        source_stat = os.stat(self.sources[3])
        os.utime(
            self.sources[3],
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns + 1_000_000),
        )
        with self.assertRaisesRegex(ValueError, "同一源文件状态"):
            second.set_parameters(request)

    def test_exact_block_contract_tail_zeros_and_pre_limiter_is_not_clipped(self):
        request = self._request(
            eq_bands=[], compression_strength=0.0, reverb_mix=0.0,
            width=1.0, instrumental_mute=True,
            vocals=[
                {
                    "path": str(path), "gain_db": 18.0,
                    "offset_seconds": 0.0, "mute": False, "solo": False,
                    "pan": 0.0, "reverb_send": 0.0,
                }
                for path in self.sources[:3]
            ],
        )
        session = self._session(request)
        with self.assertRaisesRegex(ValueError, "至少 4 帧"):
            session.render(1)
        self.assertEqual(session.position_frame, 0)
        head = session.render(4)
        self.assertEqual(head.shape, (4, 2))
        self.assertEqual(head.dtype, np.float32)
        self.assertTrue(head.flags.c_contiguous)
        rest_frames = int(session.duration_frames) - 4 + 17
        rest = session.render(rest_frames)
        self.assertEqual(rest.shape, (rest_frames, 2))
        combined = np.concatenate((head, rest), axis=0)
        self.assertGreater(
            float(np.max(np.abs(combined), initial=0.0)), backend.PEAK_CEILING,
        )
        self.assertTrue(np.all(rest[-17:] == 0.0))
        self.assertEqual(session.output_stage, "pre_limiter")

        for remainder in (1, 2, 3):
            with self.subTest(terminal_remainder=remainder):
                terminal = self._session(self._request(reverb_mix=0.0))
                target = int(terminal.duration_frames) - remainder
                while terminal.position_frame < target:
                    terminal.render(min(
                        4096, target - int(terminal.position_frame),
                    ))
                final = terminal.render(remainder)
                self.assertEqual(final.shape, (remainder, 2))
                self.assertEqual(
                    terminal.position_frame, terminal.duration_frames,
                )
                after = terminal.render(4)
                self.assertEqual(after.shape, (4, 2))
                self.assertTrue(np.all(after == 0.0))

    def test_prepared_plan_applies_without_source_validation_on_producer(self):
        session = self._session(self._request())
        changed = copy.deepcopy(self._request())
        changed['vocals'][0]['gain_db'] -= 1.0
        plan = session.prepare_parameters(changed)
        with mock.patch.object(backend, '_settings', side_effect=AssertionError('path parsing on DSP worker')), \
                mock.patch.object(realtime.audition, '_source_rows', side_effect=AssertionError('source stat on DSP worker')), \
                mock.patch.object(realtime.audition, '_decoder_identity', side_effect=AssertionError('decoder stat on DSP worker')):
            session.apply_parameters(plan)
            self.assertEqual(len(session.render(64)), 64)
        other = self._session(self._request())
        with self.assertRaises(ValueError):
            other.apply_parameters(plan)

    def test_close_is_idempotent_and_rejects_later_render(self):
        session = self._session(self._request())
        session.close()
        session.close()
        with self.assertRaisesRegex(RuntimeError, "已关闭"):
            session.render(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
