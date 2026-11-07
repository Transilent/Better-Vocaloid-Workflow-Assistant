"""Portable mixing plans preserve bindings and default to a limited scope."""
import copy
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from mixdown_preset_documents import make_document, plan_application
from preset_builtin_documents import builtin_documents, example_documents
from preset_documents import PresetError


def snapshot():
    payload = copy.deepcopy(builtin_documents()[0]['payload'])
    payload.pop('track_template')
    payload['width'] = .67
    payload['eq_enabled'] = False
    return {'processing': payload, 'tracks': [
        {'pan': -.31, 'reverb_send': .23}, {'pan': .42, 'reverb_send': .71}]}


class MixingPlans(unittest.TestCase):
    def test_save_appends_identity_and_retains_bypassed_values(self):
        payload = snapshot()['processing']
        first = make_document(payload, 'My mix')
        second = make_document(payload, 'My mix')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotIn('width', first['payload'])
        self.assertFalse(first['payload']['eq_enabled'])
        self.assertEqual(first['payload']['eq_bands'], payload['eq_bands'])
        self.assertIn('width', make_document(payload, 'Wide', include_width=True)['payload'])
        with self.assertRaises(PresetError):
            make_document(dict(payload, source_path='private.wav'), 'Bad')

    def test_default_scope_keeps_width_and_every_track_and_does_not_mutate(self):
        before = snapshot()
        original = copy.deepcopy(before)
        plan = plan_application(builtin_documents()[4], before)
        self.assertEqual(before, original)
        self.assertEqual(plan['after']['tracks'], before['tracks'])
        self.assertEqual(plan['after']['processing']['width'], .67)
        self.assertFalse(plan['include_width'])
        self.assertFalse(plan['include_tracks'])
        self.assertTrue(plan['changes'])

    def test_explicit_scope_and_actual_gui_precision(self):
        doc = builtin_documents()[4]
        doc['payload'].update(width=1.234, compression_strength=.336,
                              reverb_mix=.215, reverb_predelay_ms=23.8)
        plan = plan_application(doc, snapshot(), sample_rate=16000,
                                include_width=True, include_tracks=True)
        payload = plan['after']['processing']
        self.assertEqual(payload['width'], 1.23)
        self.assertEqual(payload['compression_strength'], .34)
        self.assertEqual(payload['reverb_mix'], .22)
        self.assertEqual(payload['reverb_predelay_ms'], 24)
        self.assertLessEqual(max(b['freq_hz'] for b in payload['eq_bands']), 8000)
        self.assertNotEqual(plan['after']['tracks'], snapshot()['tracks'])
        self.assertTrue(plan['adjusted'])

    def test_refuse_unsupported_types_capabilities_and_scope(self):
        for doc in (example_documents()[0], example_documents()[2], example_documents()[3]):
            with self.assertRaises(PresetError):
                plan_application(doc, snapshot())
        doc = builtin_documents()[0]
        doc['requires']['capabilities'].append('mix.future_processor')
        with self.assertRaises(PresetError) as caught:
            plan_application(doc, snapshot())
        self.assertEqual(caught.exception.code, 'unsupported_capability')
        doc = make_document(snapshot()['processing'], 'No width')
        with self.assertRaises(PresetError):
            plan_application(doc, snapshot(), include_width=True)


if __name__ == '__main__':
    unittest.main()
