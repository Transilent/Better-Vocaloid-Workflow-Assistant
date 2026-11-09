"""Pure tests for the shared mixdown processing and request boundary."""

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

from mixdown_request import (  # noqa: E402
    PROCESSING_APPLY_SCOPE,
    build_mixdown_request,
    build_processing_state,
    processing_request_fields,
)


def eq_bands():
    return [
        {"type": "highpass", "freq_hz": 82.0, "gain_db": -3.0,
         "q": 1.2, "enabled": True},
        {"type": "lowshelf", "freq_hz": 240.0, "gain_db": -1.5,
         "q": 0.8, "enabled": True},
        {"type": "bell", "freq_hz": 2600.0, "gain_db": 4.5,
         "q": 1.1, "enabled": False},
        {"type": "highshelf", "freq_hz": 20_000.0, "gain_db": 2.0,
         "q": 0.7, "enabled": True},
    ]


def snapshot(**overrides):
    values = {
        "sample_rate": 32_000.0,
        "eq_enabled": True,
        "eq_bands": eq_bands(),
        "compression_enabled": True,
        "compression_strength": 0.45,
        "reverb_enabled": True,
        "reverb_profile": "natural",
        "reverb_mix": 0.3,
        "reverb_predelay_ms": 23,
        "width": 1.35,
    }
    values.update(overrides)
    return build_processing_state(**values)


def tracks():
    return [
        {"path": "C:/no-such-directory/vocal-a.wav", "gain_db": -4.25,
         "offset_seconds": 0.11, "mute": True, "solo": False,
         "pan": -0.25, "reverb_send": 0.2},
        {"path": "C:/no-such-directory/vocal-b.wav", "gain_db": 2.5,
         "offset_seconds": -0.22, "mute": False, "solo": True,
         "pan": 0.35, "reverb_send": 0.75},
        {"path": "C:/no-such-directory/vocal-c.wav", "gain_db": 0.0,
         "offset_seconds": 0.0, "mute": False, "solo": False,
         "pan": 0.0, "reverb_send": 1.0},
    ]


def request(state, *, before=False, vocal_rows=None):
    return build_mixdown_request(
        state,
        vocals=tracks() if vocal_rows is None else vocal_rows,
        instrumental_path="C:/no-such-directory/backing.flac",
        output_path="C:/no-such-directory/output.wav",
        instrumental_db=-6.5,
        instrumental_mute=True,
        instrumental_solo=False,
        instrumental_pan=0.2,
        sample_rate=32_000.0,
        bypass_processing=before,
    )


class ProcessingSnapshotTests(unittest.TestCase):
    def test_snapshot_is_portable_independent_and_rate_projected(self):
        source = eq_bands()
        state = snapshot(eq_bands=source, reverb_predelay_ms=23.6)

        self.assertEqual(state["apply_scope"], PROCESSING_APPLY_SCOPE)
        self.assertEqual(state["eq_bands"][0]["gain_db"], 0.0)
        self.assertEqual(state["eq_bands"][0]["q"], 0.707)
        self.assertEqual(state["eq_bands"][3]["freq_hz"], 15_200.0)
        self.assertEqual(state["reverb_predelay_ms"], 24)
        self.assertEqual(state["width"], 1.35)
        json.dumps(state, allow_nan=False)

        source[0]["freq_hz"] = 9.0
        self.assertEqual(state["eq_bands"][0]["freq_hz"], 82.0)
        state["eq_bands"][0]["freq_hz"] = 10.0
        self.assertEqual(source[1]["freq_hz"], 240.0)

    def test_snapshot_rejects_invalid_toggle_and_range_alias_values(self):
        invalid = (
            {"compression_enabled": 1},
            {"reverb_enabled": None},
            {"compression_strength": math.inf},
            {"reverb_mix": 1.01},
            {"reverb_predelay_ms": 121},
            {"width": -0.01},
            {"reverb_profile": "large"},
            {"sample_rate": 0},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                snapshot(**change)


class RequestConstructionTests(unittest.TestCase):
    def test_before_keeps_mix_routing_and_width_while_bypassing_processing(self):
        state = snapshot()
        after = request(state)
        before = request(state, before=True)

        for key in ("vocals", "instrumental_path", "output_path",
                    "instrumental_db", "instrumental_mute",
                    "instrumental_solo", "instrumental_pan", "width"):
            self.assertEqual(before[key], after[key], key)

        self.assertEqual(after["eq_low_cut_hz"], 82.0)
        self.assertEqual(after["eq_presence_db"], 4.5)
        self.assertEqual(after["compression_strength"], 0.45)
        self.assertEqual(after["reverb_mix"], 0.3)
        self.assertEqual(after["reverb_predelay_ms"], 23)
        self.assertEqual(after["reverb_profile"], "natural")
        self.assertEqual(after["width"], 1.35)
        self.assertEqual([row["enabled"] for row in after["eq_bands"]],
                         [True, True, False, True])

        self.assertEqual(before["eq_low_cut_hz"], 0.0)
        self.assertEqual(before["eq_presence_db"], 0.0)
        self.assertEqual(before["compression_strength"], 0.0)
        self.assertEqual(before["reverb_mix"], 0.0)
        self.assertEqual(before["reverb_predelay_ms"], 0.0)
        self.assertEqual(before["reverb_profile"], "natural")
        self.assertFalse(any(row["enabled"] for row in before["eq_bands"]))
        self.assertEqual(
            [row["freq_hz"] for row in before["eq_bands"]],
            [row["freq_hz"] for row in after["eq_bands"]])

    def test_independent_global_switches_preserve_existing_field_semantics(self):
        state = snapshot(eq_enabled=False, compression_enabled=False,
                         reverb_enabled=False)
        built = request(state)

        self.assertEqual(built["eq_low_cut_hz"], 0.0)
        self.assertEqual(built["eq_presence_db"], 0.0)
        self.assertFalse(any(row["enabled"] for row in built["eq_bands"]))
        self.assertEqual(built["compression_strength"], 0.0)
        self.assertEqual(built["reverb_mix"], 0.0)
        self.assertEqual(built["reverb_predelay_ms"], 0.0)
        self.assertEqual(built["width"], 1.35)

        fields = processing_request_fields(
            state, sample_rate=32_000.0)
        self.assertEqual(fields["eq_bands"], built["eq_bands"])

    def test_request_has_no_aliases_to_processing_tracks_or_lists(self):
        state = snapshot()
        source_tracks = tracks()
        built = request(state, vocal_rows=source_tracks)

        state["eq_bands"][0]["freq_hz"] = 10_000.0
        source_tracks[0]["gain_db"] = 12.0
        self.assertEqual(built["eq_bands"][0]["freq_hz"], 82.0)
        self.assertEqual(built["vocals"][0]["gain_db"], -4.25)

        built["eq_bands"][1]["freq_hz"] = 20.0
        built["vocals"][1]["pan"] = -1.0
        self.assertEqual(state["eq_bands"][1]["freq_hz"], 240.0)
        self.assertEqual(source_tracks[1]["pan"], 0.35)
        self.assertIsNot(built["eq_bands"], state["eq_bands"])
        self.assertIsNot(built["vocals"], source_tracks)
        self.assertIsNot(built["vocals"][0], source_tracks[0])

    def test_builder_does_not_probe_media_and_rejects_bad_snapshot_keys(self):
        built = request(snapshot())
        self.assertTrue(built["instrumental_path"].endswith("backing.flac"))
        self.assertTrue(built["output_path"].endswith("output.wav"))
        self.assertFalse(Path(built["instrumental_path"]).exists())

        broken = copy.deepcopy(snapshot())
        broken["apply_scope"] = "vocal_processing"
        with self.assertRaises(ValueError):
            request(broken)
        missing = copy.deepcopy(snapshot())
        del missing["reverb_mix"]
        with self.assertRaises(ValueError):
            request(missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
