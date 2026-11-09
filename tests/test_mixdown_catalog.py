"""Production preset catalog and shared editable-EQ contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import mixdown_presets  # noqa: E402
from mixdown_presets import PRESET_KEYS, catalog, resolve_preset  # noqa: E402
from mixdown_contract import (  # noqa: E402
    EQ_BAND_TYPES,
    REVERB_PROFILES,
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
            self.assertIn(preset["reverb_profile"], REVERB_PROFILES)
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




class EqNormalizationTests(unittest.TestCase):
    def test_none_and_empty_preserve_legacy_and_explicit_empty_intent(self):
        self.assertIsNone(normalize_eq_bands(None))
        self.assertEqual(normalize_eq_bands([]), [])

    def test_valid_bands_are_copied_and_bounded(self):
        source = [
            {
                "type": "highpass",
                "freq_hz": 10,
                "gain_db": -24,
                "q": 0.1,
                "enabled": True,
            },
            {
                "type": "lowshelf",
                "freq_hz": 20_000,
                "gain_db": 24,
                "q": 18,
                "enabled": False,
            },
        ]
        normalized = normalize_eq_bands(source)
        self.assertEqual(normalized, source)
        self.assertIsNot(normalized, source)
        source[0]["freq_hz"] = 1
        self.assertEqual(normalized[0]["freq_hz"], 10.0)

    def test_invalid_band_inputs_are_rejected(self):
        invalid = (
            "not-a-sequence",
            [{"type": "highpass", "freq_hz": 9.9}],
            [{"type": "highpass", "freq_hz": 20_000.1}],
            [{"type": "invalid", "freq_hz": 100}],
            [{"type": "bell", "freq_hz": 100, "gain_db": 24.1}],
            [{"type": "bell", "freq_hz": 100, "q": 0.09}],
            [{"type": "bell", "freq_hz": 100, "enabled": 1}],
            [{"type": "bell", "freq_hz": 100}] * 5,
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_eq_bands(value)




if __name__ == "__main__":
    unittest.main(verbosity=2)
