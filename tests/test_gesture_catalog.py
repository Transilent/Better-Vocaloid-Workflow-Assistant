"""Synthetic tests for deterministic, source-grounded method retrieval."""
from pathlib import Path
import copy
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from gesture_catalog import (  # noqa: E402
    DIRECTION_KEYS,
    EVIDENCE_LEVELS,
    FEATURE_VOCABULARY,
    _validate_catalog,
    catalog,
    retrieve_methods,
)


CARD_FIELDS = {
    'id', 'source_id', 'source_url', 'source_language', 'scope',
    'observed_method', 'alternative', 'phases', 'required_features',
    'optional_features', 'directions', 'limitations', 'evidence_level',
    'enabled',
}


class CatalogShapeTests(unittest.TestCase):
    def test_cards_are_complete_and_source_grounded(self):
        cards = catalog()
        self.assertIsInstance(cards, list)
        self.assertEqual(len({card['id'] for card in cards}), len(cards))
        self.assertEqual(
            {card['source_id'] for card in cards if card['evidence_level'] == 'readable_method'},
            {'R2-JA-01', 'R2-JA-02', 'R2-JA-03', 'R2-JA-04',
             'R2-JA-05', 'R2-JA-06', 'R2-ZH-04'},
        )
        for card in cards:
            self.assertEqual(set(card), CARD_FIELDS)
            self.assertTrue(card['source_url'].startswith('https://'))
            self.assertIn(card['source_language'], {'zh', 'ja', 'en'})
            self.assertIn(card['evidence_level'], EVIDENCE_LEVELS)
            self.assertIs(type(card['enabled']), bool)
            self.assertTrue(card['required_features'])
            self.assertLessEqual(set(card['required_features']), FEATURE_VOCABULARY)
            self.assertLessEqual(set(card['optional_features']), FEATURE_VOCABULARY)
            self.assertFalse(set(card['required_features']) & set(card['optional_features']))
            self.assertLessEqual(set(card['directions']), DIRECTION_KEYS)
            self.assertTrue(card['scope'].strip())
            self.assertTrue(card['observed_method'].strip())
            self.assertTrue(card['alternative'].strip())
            self.assertTrue(card['limitations'])
        json.dumps(cards, ensure_ascii=False, allow_nan=False)

    def test_index_only_leads_are_absent_and_context_bound_cards_are_disabled(self):
        cards = catalog()
        source_ids = {card['source_id'] for card in cards}
        self.assertNotIn('R2-ZH-05', source_ids)
        self.assertNotIn('R2-ZH-06', source_ids)
        disabled = [card for card in cards if not card['enabled']]
        self.assertEqual(
            {card['source_id'] for card in disabled},
            {'R2-JA-03', 'R2-JA-04', 'R2-JA-06',
             'OFF-01', 'OFF-02', 'OFF-03', 'OFF-04', 'OFF-05', 'OFF-06'},
        )
        for card in disabled:
            self.assertTrue(any('Disabled:' in item for item in card['limitations']))

    def test_official_index_evidence_is_not_promoted_to_verified_mechanisms(self):
        # These exact URLs are indexed_text in the saved main-sources.json.
        # The audit R01/R05 map to OFF-01/OFF-04 respectively; audit R06 is
        # a different limitation page, not catalog source OFF-06.
        indexed_urls = {
            'OFF-01': 'https://sv2.docs.dreamtonics.com/en/parameters',
            'OFF-02': 'https://sv2.docs.dreamtonics.com/en/pitch',
            'OFF-03': 'https://sv2.docs.dreamtonics.com/en/voice-panel',
            'OFF-04': 'https://sv2.docs.dreamtonics.com/en/phonemes',
            'OFF-05': 'https://sv1.docs.dreamtonics.com/en/synthv/advanced-usage/note-properties',
            'OFF-06': 'https://sv1.docs.dreamtonics.com/en/synthv/upgrade/migration-guide',
        }
        cards = catalog()
        official = [card for card in cards if card['source_id'] in indexed_urls]
        self.assertEqual(len(official), len(indexed_urls))
        for card in official:
            with self.subTest(source_id=card['source_id']):
                self.assertEqual(card['source_url'], indexed_urls[card['source_id']])
                self.assertEqual(card['evidence_level'], 'index_only')
                self.assertFalse(card['enabled'])
                self.assertIn('Index-only reference', card['scope'])
                self.assertIn('saved search index', card['observed_method'])
                self.assertTrue(any('Disabled:' in item and 'indexed_text' in item
                                    for item in card['limitations']))

        matches = retrieve_methods(
            language='en', features=FEATURE_VOCABULARY,
            goal='pitch phoneme vocal mode breathiness migration', limit=50)
        self.assertTrue(matches)
        self.assertTrue(all(item['evidence_level'] == 'readable_method'
                            for item in matches))
        self.assertTrue({item['source_id'] for item in matches}.isdisjoint(indexed_urls))

    def test_index_only_cards_cannot_be_enabled_by_catalog_edits(self):
        cards = catalog()
        indexed = next(card for card in cards if card['evidence_level'] == 'index_only')
        indexed['enabled'] = True
        with self.assertRaisesRegex(RuntimeError, 'index-only.*disabled'):
            _validate_catalog(cards)

    def test_catalog_is_a_deep_copy(self):
        first = catalog()
        before = copy.deepcopy(first)
        first[0]['required_features'].append('question')
        first[0]['directions'][0] = 'clear_onset'
        first[0]['limitations'][0] = 'mutated'
        second = catalog()
        self.assertEqual(second, before)
        self.assertNotEqual(first[0]['limitations'], second[0]['limitations'])

    def test_source_specific_scope_and_limitations_remain_visible(self):
        by_id = {card['id']: card for card in catalog()}
        self.assertEqual(by_id['quiet-connected-phrase-entry']['source_id'], 'R2-JA-05')
        self.assertEqual(by_id['phoneme-boundary-timing']['source_id'], 'R2-ZH-04')
        self.assertIn('Japanese quiet', by_id['quiet-connected-phrase-entry']['scope'])
        self.assertIn('English worked example',
                      by_id['phoneme-boundary-timing']['scope'])
        self.assertIn('DAW', by_id['rock-chorus-voice-role']['scope'])
        self.assertTrue(any('availability' in item
                            for item in by_id['ensemble-voice-role-balance']['limitations']))
        self.assertTrue(any('flat curve' in item
                            for item in by_id['intentional-synthetic-voice-role']['limitations']))


class RetrievalTests(unittest.TestCase):
    def test_required_features_are_strict_and_context_remains_pending(self):
        matches = retrieve_methods(
            language='ja-JP',
            features=['rest_before', 'phrase_start'],
            goal='静かな息を整える',
            limit=50,
        )
        quiet = next(item for item in matches if item['id'] == 'quiet-connected-phrase-entry')
        self.assertEqual(matches[0]['id'], 'quiet-connected-phrase-entry')
        self.assertIn('required_feature:phrase_start', quiet['match_reasons'])
        self.assertIn('optional_feature:rest_before', quiet['match_reasons'])
        self.assertEqual(quiet['score_kind'], 'deterministic_match_points')
        self.assertEqual(quiet['applicability']['status'], 'structural_match_only')
        self.assertEqual(quiet['applicability']['context_status'],
                         'pending_source_context_review')
        self.assertEqual(quiet['applicability']['language_status'],
                         'same_source_language_only')
        self.assertEqual(quiet['applicability']['language'], 'ja')
        self.assertTrue(quiet['applicability']['pending_context_checks'])
        self.assertTrue(quiet['limitations'])

        only_end = retrieve_methods(
            language='ja', features={'phrase_end'}, limit=50)
        ids = {item['id'] for item in only_end}
        self.assertIn('phrase-end-direction-choice', ids)
        self.assertNotIn('forceful-selected-word-entry', ids)
        self.assertNotIn('phoneme-boundary-timing', ids)
        self.assertNotIn('quiet-connected-phrase-entry', ids)

    def test_cross_language_and_worked_example_have_separate_status(self):
        matches = retrieve_methods(
            language='zh-CN', features={'word_start'}, limit=50)
        by_id = {item['id']: item for item in matches}
        strong = by_id['forceful-selected-word-entry']
        self.assertEqual(strong['applicability']['status'], 'structural_match_only')
        self.assertEqual(strong['applicability']['language_status'],
                         'conditional_cross_language_transfer')
        self.assertIn('language:cross_language_conditional', strong['match_reasons'])
        self.assertTrue(strong['applicability']['conditions'])
        phoneme = by_id['phoneme-boundary-timing']
        self.assertEqual(phoneme['applicability']['status'], 'structural_match_only')
        self.assertEqual(phoneme['applicability']['language_status'],
                         'conditional_worked_example_language')
        self.assertTrue(any('English' in item
                            for item in phoneme['applicability']['conditions']))

    def test_same_language_gets_no_context_or_score_claim(self):
        same = retrieve_methods(language='ja', features={'word_start'}, limit=50)
        cross = retrieve_methods(language='zh', features={'word_start'}, limit=50)
        same_card = next(item for item in same
                         if item['id'] == 'phrased-word-articulation')
        cross_card = next(item for item in cross
                          if item['id'] == 'phrased-word-articulation')
        self.assertEqual(same_card['score'], cross_card['score'])
        self.assertEqual(same_card['applicability']['status'], 'structural_match_only')
        self.assertEqual(cross_card['applicability']['status'], 'structural_match_only')
        self.assertEqual(same_card['applicability']['language_status'],
                         'same_source_language_only')
        self.assertEqual(cross_card['applicability']['language_status'],
                         'conditional_cross_language_transfer')

    def test_goal_is_unparsed_and_does_not_affect_retrieval(self):
        neutral = retrieve_methods(
            language='zh', features={'word_start'}, limit=50)
        for goal in ('辅音 音素', 'sad 悲伤', 'quiet forceful', '静かに 強く',
                     'rock ensemble migration', '不强调气声，不要强声'):
            with self.subTest(goal=goal):
                self.assertEqual(retrieve_methods(
                    language='zh', features={'word_start'}, goal=goal, limit=50),
                    neutral)
                self.assertEqual(retrieve_methods(
                    language='zh', features=set(), goal=goal, limit=50), [])
        for item in neutral:
            self.assertFalse(any(reason.startswith('goal_lexical:')
                                 for reason in item['match_reasons']))

    def test_negated_quiet_cjk_goal_cannot_boost_quiet_card(self):
        features = {'phrase_start', 'word_start'}
        neutral = retrieve_methods(language='zh', features=features, limit=50)
        with_goal = retrieve_methods(
            language='zh', features=features, goal='不安静，要克制', limit=50)
        self.assertIn('quiet-connected-phrase-entry', {item['id'] for item in neutral})
        self.assertEqual(with_goal, neutral)

    def test_embedded_strong_character_cannot_boost_forceful_card(self):
        features = {'phrase_start', 'word_start'}
        neutral = retrieve_methods(language='zh', features=features, limit=50)
        with_goal = retrieve_methods(
            language='zh', features=features, goal='勉强保持克制', limit=50)
        self.assertIn('forceful-selected-word-entry', {item['id'] for item in neutral})
        self.assertEqual(with_goal, neutral)

    def test_results_are_stable_copies_without_confidence_claims(self):
        features = ['phrase_start', 'rest_before', 'long_note']
        before = list(features)
        first = retrieve_methods(language='ja', features=features, limit=9)
        second = retrieve_methods(language='ja', features=features, limit=9)
        reversed_order = retrieve_methods(
            language='ja', features=list(reversed(features)), limit=9)
        self.assertEqual(first, second)
        self.assertEqual(first, reversed_order)
        self.assertEqual(features, before)
        self.assertTrue(first)
        ordering = [(
            -item['score'],
            0 if item['evidence_level'] == 'readable_method' else 1,
            item['id'],
        ) for item in first]
        self.assertEqual(ordering, sorted(ordering))
        for item in first:
            self.assertEqual(item['score_kind'], 'deterministic_match_points')
            self.assertEqual(item['score'],
                             2 * len(item['required_features'])
                             + len(set(item['optional_features']) & set(features)))
            self.assertNotIn('confidence', item)
            self.assertNotIn('probability', item)
            self.assertNotIn('accuracy', item)
            self.assertEqual(item['applicability']['status'], 'structural_match_only')
            self.assertTrue(item['applicability']['pending_context_checks'])
            self.assertTrue(item['limitations'])
        first[0]['limitations'].append('mutated')
        fresh = retrieve_methods(language='ja', features=features, limit=9)
        self.assertNotIn('mutated', fresh[0]['limitations'])

    def test_disabled_context_proxy_cards_and_migration_never_match(self):
        all_disabled = {
            'rock-chorus-voice-role',
            'ensemble-voice-role-balance',
            'intentional-synthetic-voice-role',
            'official-consonant-space-mechanism',
            'official-pitch-and-note-texture-mechanism',
            'official-vocal-mode-dimensions',
            'official-base-parameter-separation',
            'official-generated-pitch-context',
            'legacy-phonetic-note-migration',
        }
        cases = (
            {'repeat'},
            {'contrast'},
            {'word_start', 'long_note', 'phrase_end', 'repeat', 'contrast'},
            FEATURE_VOCABULARY,
        )
        for features in cases:
            with self.subTest(features=features):
                ids = {item['id'] for item in retrieve_methods(
                    language='en', features=features,
                    goal='rock ensemble migration', limit=50,
                )}
                self.assertTrue(ids.isdisjoint(all_disabled))

    def test_limit_zero_and_validation(self):
        self.assertEqual(retrieve_methods(
            language='zh', features={'word_start'}, limit=0), [])
        bad_calls = (
            dict(language='', features={'word_start'}),
            dict(language='zh', features='word_start'),
            dict(language='zh', features={'emotion'}),
            dict(language='zh', features={3}),
            dict(language='zh', features={'word_start'}, goal=None),
            dict(language='zh', features={'word_start'}, limit=True),
            dict(language='zh', features={'word_start'}, limit=-1),
            dict(language='zh', features=None),
        )
        for call in bad_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                retrieve_methods(**call)


if __name__ == '__main__':
    unittest.main(verbosity=2)

