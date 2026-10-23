"""Offline word ownership and pronunciation boundaries, with no model or network."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from context_reading import phones_to_moras, reading_units_from_frontend


class ReadingTests(unittest.TestCase):
    def test_phones_and_closure_are_not_collapsed(self):
        self.assertEqual(phones_to_moras(['k','o','k','o','n','i','t','a','cl','t','e','N']),
                         ['ko','ko','ni','ta','cl','te','n'])
        self.assertEqual(phones_to_moras(['sh','I','ky','o','o']), ['shi','kyo','o'])
        with self.assertRaises(ValueError):
            phones_to_moras(['k'])

    def test_multi_kanji_word_owns_whole_surface_span(self):
        rows = [{'string':'今日','pron':'キョー'}, {'string':'は','pron':'ワ'}]
        phones = {'キョー':['ky','o','o'], 'ワ':['w','a']}
        data = reading_units_from_frontend('今日は', rows, phones.__getitem__)
        self.assertEqual(data['units'][0]['span'], [0,2])
        self.assertEqual(data['units'][0]['tokens'], ['kyo','o'])
        self.assertEqual(data['units'][1]['tokens'], ['wa'])
        self.assertFalse(data['issues'])

    def test_mismatch_stops_without_finding_another_occurrence(self):
        data = reading_units_from_frontend('私は君を', [{'string':'君','pron':'キミ'}], lambda _: ['k','i','m','i'])
        self.assertEqual(data['units'], [])
        self.assertIn('frontend_surface_mismatch', {x['code'] for x in data['issues']})

    def test_punctuation_keeps_source_offsets(self):
        data = reading_units_from_frontend(' 僕。', [{'string':'僕','pron':'ボク'}, {'string':'。','pron':'、'}],
                                           lambda _: ['b','o','k','u'])
        self.assertEqual(data['units'][0]['span'], [1,2])
        self.assertEqual(len(data['units']), 1)
        self.assertEqual(data['metadata']['reading_status'], 'dictionary_hypothesis_not_acoustic_verification')


if __name__ == '__main__':
    unittest.main()
