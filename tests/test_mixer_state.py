"""Pure-state contracts for the P1 mixdown presets and request boundary."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mixdown_presets import (  # noqa: E402
    PRESET_KEYS,
    apply_preset,
    catalog,
    resolve_preset,
)
from mixer_state import (  # noqa: E402
    EQ_BAND_TYPES,
    REVERB_PROFILES,
    MixState,
    TrackState,
    db_to_norm,
    norm_to_db,
    normalize_eq_bands,
)


LABELS = {
    "clean_lead": "清透主唱",
    "thick_harmony": "厚实和声",
    "intimate": "贴耳近讲",
    "wide_chorus": "宽阔副歌",
    "opera_bright": "戏腔清亮",
    "duet_balance": "对唱平衡",
}


def full_state():
    return MixState(
        vocals=[
            TrackState(
                "lead.wav",
                gain_db=-4.5,
                offset_seconds=0.125,
                mute=True,
                solo=False,
                pan=-0.35,
                reverb_send=0.28,
            ),
            TrackState(
                "harmony-1.wav",
                gain_db=-8.0,
                offset_seconds=-0.25,
                solo=True,
                pan=0.10,
                reverb_send=0.42,
            ),
            TrackState("harmony-2.wav", gain_db=-10.0),
        ],
        instrumental_path="backing.wav",
        output_path="mix.wav",
        instrumental_db=-2.5,
        instrumental_mute=False,
        instrumental_solo=False,
        instrumental_pan=0.15,
        eq_low_cut_hz=75.0,
        eq_presence_db=1.25,
        compression_strength=0.20,
        reverb_mix=0.10,
        reverb_profile="natural",
    )


class PresetCatalogTests(unittest.TestCase):
    def test_catalog_has_six_ordered_and_bounded_presets(self):
        documents = catalog()
        self.assertEqual(tuple(item["key"] for item in documents), PRESET_KEYS)
        self.assertEqual(
            [item["label"] for item in documents],
            [LABELS[key] for key in PRESET_KEYS],
        )
        for preset in documents:
            self.assertEqual(preset["description"], "起点预设，需按素材微调。")
            bands = preset["eq_bands"]
            self.assertEqual(len(bands), 4)
            self.assertEqual(
                tuple(band["type"] for band in bands), EQ_BAND_TYPES
            )
            for band in bands:
                self.assertGreaterEqual(band["freq_hz"], 10.0)
                self.assertLessEqual(band["freq_hz"], 20_000.0)
                self.assertGreaterEqual(band["gain_db"], -24.0)
                self.assertLessEqual(band["gain_db"], 24.0)
                self.assertGreaterEqual(band["q"], 0.1)
                self.assertLessEqual(band["q"], 18.0)
                self.assertIs(type(band["enabled"]), bool)
            self.assertGreaterEqual(preset["compression_strength"], 0.0)
            self.assertLessEqual(preset["compression_strength"], 1.0)
            self.assertIn(preset["reverb_profile"], ("small", "natural", "wide"))
            self.assertGreaterEqual(preset["reverb_mix"], 0.0)
            self.assertLessEqual(preset["reverb_mix"], 1.0)
            self.assertGreaterEqual(preset["reverb_predelay_ms"], 0.0)
            self.assertLessEqual(preset["reverb_predelay_ms"], 120.0)
            self.assertGreaterEqual(preset["width"], 0.0)
            self.assertLessEqual(preset["width"], 2.0)
            self.assertGreaterEqual(preset["pan"], -1.0)
            self.assertLessEqual(preset["pan"], 1.0)
            self.assertGreaterEqual(preset["reverb_send"], 0.0)
            self.assertLessEqual(preset["reverb_send"], 1.0)
            self.assertEqual(resolve_preset(preset["label"]), preset)

    def test_clean_lead_uses_integrated_natural_profile(self):
        preset = resolve_preset("clean_lead")
        self.assertEqual(preset["reverb_profile"], "natural")
        self.assertEqual(preset["reverb_predelay_ms"], 20.0)
        self.assertEqual(REVERB_PROFILES, ("small", "natural", "wide"))

    def test_catalog_and_resolution_return_independent_copies(self):
        first = catalog()
        first[0]["eq_bands"][0]["freq_hz"] = 1.0
        self.assertEqual(catalog()[0]["eq_bands"][0]["freq_hz"], 90.0)
        resolved = resolve_preset("清透主唱")
        resolved["eq_bands"].clear()
        self.assertEqual(len(resolve_preset("清透主唱")["eq_bands"]), 4)
        with self.assertRaises(ValueError):
            resolve_preset("not-a-preset")

    def test_catalog_json_is_stable(self):
        first = json.dumps(
            catalog(), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        second = json.dumps(
            catalog(), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        self.assertEqual(first, second)


class PresetApplyTests(unittest.TestCase):
    def test_lock_mix_true_preserves_all_user_mix_values(self):
        state = full_state()
        before = [
            (
                track.gain_db,
                track.offset_seconds,
                track.mute,
                track.solo,
                track.pan,
                track.reverb_send,
            )
            for track in state.vocals
        ]
        returned = apply_preset(state, "clean_lead", lock_mix=True)
        self.assertIs(returned, state)
        after = [
            (
                track.gain_db,
                track.offset_seconds,
                track.mute,
                track.solo,
                track.pan,
                track.reverb_send,
            )
            for track in state.vocals
        ]
        self.assertEqual(after, before)
        self.assertEqual(state.compression_strength, 0.30)
        self.assertEqual(state.reverb_profile, "natural")
        self.assertEqual(state.reverb_mix, 0.14)
        self.assertEqual(state.reverb_predelay_ms, 20.0)
        self.assertEqual(len(state.eq_bands), 4)
        self.assertEqual(state.active_preset, "clean_lead")
        self.assertTrue(state.uses_v3())

    def test_lock_mix_false_applies_suggested_pan_and_send(self):
        state = full_state()
        gains = [track.gain_db for track in state.vocals]
        offsets = [track.offset_seconds for track in state.vocals]
        apply_preset(state, "duet_balance", lock_mix=False)
        self.assertEqual([track.pan for track in state.vocals], [0.0, 0.25, -0.25])
        self.assertEqual(
            [track.reverb_send for track in state.vocals], [0.75, 0.75, 0.75]
        )
        self.assertEqual([track.gain_db for track in state.vocals], gains)
        self.assertEqual(
            [track.offset_seconds for track in state.vocals], offsets
        )
        self.assertEqual(state.active_preset, "duet_balance")

    def test_alternate_harmony_pan_is_bounded(self):
        state = MixState(vocals=[TrackState("a.wav"), TrackState("b.wav")])
        apply_preset(state, "thick_harmony", lock_mix=False)
        self.assertEqual([track.pan for track in state.vocals], [-0.20, 0.20])

    def test_lock_mix_type_is_strict(self):
        with self.assertRaises(ValueError):
            apply_preset(full_state(), "intimate", lock_mix=1)


class BoundaryTests(unittest.TestCase):
    def test_track_boundaries_and_rejections(self):
        self.assertEqual(TrackState("a.wav", gain_db=-120, pan=-1, reverb_send=0).pan, -1)
        self.assertEqual(TrackState("a.wav", gain_db=24, pan=1, reverb_send=1).gain_db, 24)
        invalid = (
            {"gain_db": -120.1},
            {"gain_db": 24.1},
            {"offset_seconds": -21600.1},
            {"offset_seconds": 21600.1},
            {"pan": -1.1},
            {"pan": 1.1},
            {"reverb_send": -0.01},
            {"reverb_send": 1.01},
            {"gain_db": float("nan")},
            {"mute": 1},
            {"eq_bypass": "false"},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                TrackState("a.wav", **values)

    def test_mix_boundaries_and_rejections(self):
        self.assertEqual(MixState(width=0.0).width, 0.0)
        self.assertEqual(MixState(width=2.0).width, 2.0)
        self.assertEqual(MixState(reverb_predelay_ms=120.0).reverb_predelay_ms, 120.0)
        invalid = (
            {"width": -0.01},
            {"width": 2.01},
            {"reverb_predelay_ms": -0.1},
            {"reverb_predelay_ms": 120.1},
            {"compression_strength": -0.1},
            {"reverb_mix": 1.1},
            {"deesser_amount": 1.1},
            {"eq_low_cut_hz": 5.0},
            {"reverb_profile": "hall"},
            {"vocals": [TrackState()] * 4},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                MixState(**values)

    def test_eq_boundary_and_rejections(self):
        bands = normalize_eq_bands([
            {"type": "highpass", "freq_hz": 10, "gain_db": -24, "q": 0.1, "enabled": True},
            {"type": "lowshelf", "freq_hz": 20000, "gain_db": 24, "q": 18, "enabled": False},
        ])
        self.assertEqual(len(bands), 2)
        invalid = (
            [{"type": "highpass", "freq_hz": 9.9}],
            [{"type": "highpass", "freq_hz": 20000.1}],
            [{"type": "invalid", "freq_hz": 100}],
            [{"type": "bell", "freq_hz": 100, "gain_db": 24.1}],
            [{"type": "bell", "freq_hz": 100, "q": 0.09}],
            [{"type": "bell", "freq_hz": 100, "enabled": 1}],
            [{"type": "bell", "freq_hz": 100}] * 5,
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_eq_bands(value)


class RequestTests(unittest.TestCase):
    def test_legacy_request_roundtrip_keeps_old_three_keys(self):
        request = {
            "vocal_path": "lead.wav",
            "vocal_db": -3.5,
            "offset_seconds": 0.25,
            "instrumental_path": "backing.wav",
            "output_path": "mix.wav",
            "instrumental_db": -2.0,
            "eq_low_cut_hz": 80.0,
            "eq_presence_db": 1.5,
            "compression_strength": 0.2,
            "reverb_mix": 0.1,
            "reverb_profile": "natural",
        }
        state = MixState.from_request(request)
        self.assertTrue(state.legacy_vocal_keys)
        self.assertFalse(state.uses_v3())
        self.assertEqual(state.to_request(), request)
        built = state.to_request(legacy=True)
        self.assertEqual(
            set(built) & {"vocal_path", "vocal_db", "offset_seconds"},
            {"vocal_path", "vocal_db", "offset_seconds"},
        )
        self.assertNotIn("vocals", built)
        for key in (
            "eq_bands",
            "reverb_predelay_ms",
            "width",
            "instrumental_mute",
            "instrumental_solo",
            "instrumental_pan",
        ):
            self.assertNotIn(key, built)

    def test_default_vocal_track_emits_only_the_old_three_object_keys(self):
        state = MixState(vocals=[TrackState("lead.wav")])
        request = state.to_request()
        self.assertEqual(
            request["vocals"], [
                {"path": "lead.wav", "gain_db": 0.0, "offset_seconds": 0.0}
            ],
        )
        self.assertFalse(state.uses_v3())
        for key in ("eq_bands", "reverb_predelay_ms", "width", "deesser_amount"):
            self.assertNotIn(key, request)

    def test_nondefault_fields_are_emitted_and_detected(self):
        state = MixState(
            vocals=[TrackState("lead.wav", mute=True, solo=True, pan=0.4, reverb_send=0.5, eq_bypass=True)],
            instrumental_mute=True,
            instrumental_solo=True,
            instrumental_pan=-0.3,
            width=1.2,
            reverb_predelay_ms=15.0,
            deesser_amount=0.1,
            eq_bands=normalize_eq_bands(resolve_preset("wide_chorus")["eq_bands"]),
        )
        self.assertTrue(state.uses_v3())
        request = state.to_request()
        track = request["vocals"][0]
        self.assertEqual(
            {key: track[key] for key in ("mute", "solo", "pan", "reverb_send", "eq_bypass")},
            {"mute": True, "solo": True, "pan": 0.4, "reverb_send": 0.5, "eq_bypass": True},
        )
        self.assertTrue(request["instrumental_mute"])
        self.assertTrue(request["instrumental_solo"])
        self.assertEqual(request["instrumental_pan"], -0.3)
        self.assertEqual(request["width"], 1.2)
        self.assertEqual(request["reverb_predelay_ms"], 15.0)
        self.assertEqual(request["deesser_amount"], 0.1)
        self.assertEqual(len(request["eq_bands"]), 4)

    def test_preset_request_and_json_roundtrip(self):
        state = full_state()
        apply_preset(state, "wide_chorus", lock_mix=False)
        request = state.to_request()
        restored = MixState.from_request(request)
        self.assertEqual(restored.to_request(), request)
        self.assertTrue(restored.uses_v3())
        self.assertEqual(restored.width, 1.3)
        self.assertEqual(restored.reverb_profile, "wide")
        self.assertEqual(len(restored.eq_bands), 4)
        first = state.to_json()
        self.assertEqual(first, state.to_json())
        self.assertEqual(MixState.from_json(first).to_json(), first)

    def test_request_rejects_ambiguous_vocal_layouts(self):
        with self.assertRaises(ValueError):
            MixState.from_request({
                "vocals": [{"path": "lead.wav"}],
                "vocal_path": "other.wav",
            })

    def test_legacy_shape_rejects_tracks_with_v3_fields(self):
        state = MixState(
            vocals=[TrackState("lead.wav", pan=0.2)],
            legacy_vocal_keys=True,
        )
        with self.assertRaises(ValueError):
            state.to_request()


class FaderMappingTests(unittest.TestCase):
    def test_anchor_positions_and_zero_db(self):
        anchors = {
            -60.0: 0.235,
            -48.0: 0.330,
            -24.0: 0.505,
            -12.0: 0.632,
            -6.0: 0.712,
            0.0: 0.800,
            6.0: 0.905,
            12.0: 1.000,
        }
        for decibels, expected in anchors.items():
            with self.subTest(db=decibels):
                self.assertAlmostEqual(db_to_norm(decibels), expected, places=12)
                self.assertAlmostEqual(norm_to_db(expected), decibels, places=7)
        self.assertAlmostEqual(db_to_norm(0.0), 0.800, delta=0.001)

    def test_mapping_is_monotone_and_reversible(self):
        values = [-60 + index for index in range(73)]
        positions = [db_to_norm(value) for value in values]
        self.assertEqual(positions, sorted(positions))
        for decibels in values:
            with self.subTest(db=decibels):
                self.assertLess(
                    abs(norm_to_db(db_to_norm(decibels)) - decibels), 0.05
                )
        positions = [index / 100.0 for index in range(1, 101)]
        reconstructed = [db_to_norm(norm_to_db(value)) for value in positions]
        self.assertEqual(reconstructed, sorted(reconstructed))
        for expected, actual in zip(positions, reconstructed):
            self.assertLess(abs(expected - actual), 1e-9)

    def test_bottom_and_invalid_values(self):
        self.assertEqual(db_to_norm(-math.inf), 0.0)
        self.assertEqual(db_to_norm(-1_000.0), 0.0)
        self.assertEqual(db_to_norm(100.0), 1.0)
        self.assertEqual(norm_to_db(0.0), -math.inf)
        for value in (math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                db_to_norm(value)
        for value in (-0.01, 1.01, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                norm_to_db(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)