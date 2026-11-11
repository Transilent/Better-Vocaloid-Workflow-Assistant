"""Pure EQ response contracts shared by rendering and the Quick front end."""
import math
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
import mixdown_eq_math as eq


class AnalyticCurveTests(unittest.TestCase):
    def test_defaults_and_deep_copy_contract(self):
        first = eq.default_bands()
        second = eq.default_bands()
        self.assertEqual([band['type'] for band in first], list(eq.BAND_TYPES))
        self.assertEqual(len(first), 4)
        self.assertEqual(first, list(eq.DEFAULT_BANDS))
        first[0]['freq_hz'] = 123.0
        first.append({'type': 'bell'})
        self.assertNotEqual(first, second)

    def test_log_frequency_mapping_and_endpoints(self):
        frequencies = eq.logarithmic_frequencies(240)
        self.assertEqual(len(frequencies), 240)
        self.assertAlmostEqual(frequencies[0], eq.MIN_FREQUENCY_HZ)
        self.assertAlmostEqual(frequencies[-1], eq.MAX_FREQUENCY_HZ)
        self.assertAlmostEqual(eq.frequency_to_unit(10.0), 0.0)
        self.assertAlmostEqual(eq.frequency_to_unit(100.0),
                               math.log10(100.0 / 10.0) /
                               math.log10(eq.MAX_FREQUENCY_HZ / eq.MIN_FREQUENCY_HZ))
        self.assertAlmostEqual(eq.frequency_to_unit(20000.0), 1.0)
        for frequency in (10.0, 92.0, 440.0, 3200.0, 20000.0):
            restored = eq.unit_to_frequency(eq.frequency_to_unit(frequency))
            self.assertAlmostEqual(restored, frequency, places=6)

    def test_rbj_response_peak_direction(self):
        bell = {'type': 'bell', 'freq_hz': 1000.0, 'gain_db': 12.0,
                'q': 1.0, 'enabled': True}
        low, center, high = eq.band_response_db(bell, [80.0, 1000.0, 12000.0])
        self.assertGreater(center, 11.5)
        self.assertLess(center, 12.5)
        self.assertGreater(center, low + 6.0)
        self.assertGreater(center, high + 6.0)
        low_shelf = {'type': 'lowshelf', 'freq_hz': 180.0, 'gain_db': 6.0,
                     'q': 0.707, 'enabled': True}
        low_side, high_side = eq.band_response_db(low_shelf, [40.0, 10000.0])
        self.assertGreater(low_side, high_side + 3.0)
        high_shelf = {'type': 'highshelf', 'freq_hz': 5000.0, 'gain_db': 6.0,
                      'q': 0.707, 'enabled': True}
        low_side, high_side = eq.band_response_db(high_shelf, [40.0, 12000.0])
        self.assertGreater(high_side, low_side + 3.0)
        highpass = {'type': 'highpass', 'freq_hz': 100.0,
                    'gain_db': 0.0, 'q': 0.707, 'enabled': True}
        blocked, passed = eq.band_response_db(highpass, [20.0, 10000.0])
        self.assertLess(blocked, -20.0)
        self.assertGreater(passed, -0.2)
        low_rate = eq.band_response_db(highpass, [500.0, 3000.0], sample_rate=8000.0)
        self.assertTrue(all(math.isfinite(value) for value in low_rate))
        locked = eq.normalize_band({'type': 'highpass', 'freq_hz': 100.0,
                                    'gain_db': 12.0, 'q': 4.0, 'enabled': True})
        self.assertEqual(locked['gain_db'], 0.0)
        self.assertAlmostEqual(locked['q'], eq.HIGHPASS_FIXED_Q)

    def test_total_response_respects_bypass_and_has_240_points(self):
        bands = eq.default_bands()
        for band in bands:
            band['enabled'] = False
        bands[2]['enabled'] = True
        bands[2]['gain_db'] = 9.0
        frequencies, values = eq.response_curve(bands)
        self.assertEqual(len(frequencies), eq.RESPONSE_POINTS)
        self.assertEqual(len(values), eq.RESPONSE_POINTS)
        center_index = min(range(len(frequencies)), key=lambda index: abs(frequencies[index] - 3200.0))
        self.assertGreater(values[center_index], 8.0)
        zero = eq.frequency_response_db(bands, [3200.0], enabled_only=False)
        self.assertGreater(zero[0], 8.0)
        bands[2]['enabled'] = False
        self.assertEqual(eq.frequency_response_db(bands, [3200.0])[0], 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
