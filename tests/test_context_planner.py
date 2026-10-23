"""Pure contract tests for context proposal planning and local provenance."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from context_evidence import build_evidence
from context_planner import propose_plan, proposal_fingerprint, validate_proposal


def evidence(text, lyrics, *, language='zh', words=(), spans=None):
    notes = []
    for index, lyric in enumerate(lyrics):
        notes.append({'lyric': lyric, 'start': float(index),
                      'end': index + 1.0, 'pitch': 60 + index})
    spans = list(spans or [])
    return build_evidence(
        text, language, notes, voice='vocals', provenance={'fixture': True},
        word_spans=[{'span': span, 'source': 'synthetic_manual_fixture'}
                    for span in spans])


def response_for(item, interpretations):
    return {
        'schema_version': 1,
        'kind': 'vwa.context-semantic-response',
        'evidence_ids': {'vocals': item['evidence_id']},
        'provider': {'name': 'synthetic-fixture', 'model': 'invented'},
        'interpretations': interpretations,
    }


def interpretation(identifier, span, quote, focus, *, intent='hypothesis'):
    return {
        'id': identifier,
        'voice': 'vocals',
        'span': span,
        'quote': quote,
        'intent': intent,
        'focus': [{'span': focus, 'quote': 'x', 'role': 'focus'}],
        'explanation': 'Synthetic fixture only.',
        'alternatives': [],
        'unknowns': [],
    }


class PlannerTests(unittest.TestCase):
    def test_aligned_candidates_are_local_distinct_and_onset_safe(self):
        item = evidence('你，好', ['你', '-', '好'], spans=[(0, 1), (2, 3)])
        before = copy.deepcopy(item)
        proposal = propose_plan({'vocals': item})
        self.assertEqual(item, before)
        self.assertEqual(proposal['kind'], 'vwa.context-proposal')
        self.assertIsNone(proposal['selected_candidate'])
        self.assertIsNone(proposal['plan']['selected_candidate'])
        candidates = proposal['plan']['candidates']
        self.assertGreaterEqual(len(candidates), 2)
        signatures = {json.dumps(row['gestures'], sort_keys=True, ensure_ascii=False)
                      for row in candidates}
        self.assertGreaterEqual(len(signatures), 2)
        lyric_by_id = {row['id']: row['lyric'] for row in item['notes']}
        for candidate in candidates:
            for gesture in candidate['gestures']:
                if gesture['phase'] == 'onset':
                    self.assertEqual(len(gesture['note_ids']), 1)
                    self.assertNotIn(lyric_by_id[gesture['note_ids'][0]], ('-', '+~'))
        self.assertTrue(any(row.startswith('goal_not_used_for_gestures:')
                            for row in proposal['limitations']))
        self.assertEqual(validate_proposal(proposal, {'vocals': item}), proposal)
        self.assertEqual(proposal_fingerprint(proposal),
                         proposal_fingerprint(copy.deepcopy(proposal)))
        json.dumps(proposal, ensure_ascii=False, allow_nan=False)

    def test_unparsed_goal_never_changes_gesture_structures(self):
        item = evidence('你好', ['你', '好'], spans=[(0, 1), (1, 2)])
        first = propose_plan({'vocals': item})
        long_goal = 'g' * 1000
        second = propose_plan({'vocals': item}, goal=long_goal)
        self.assertEqual(
            [row['gestures'] for row in first['plan']['candidates']],
            [row['gestures'] for row in second['plan']['candidates']])
        self.assertEqual(second['analysis']['goal_handling'], 'unparsed_user_goal')
        self.assertTrue(second['analysis']['inputs']['goal_not_used_for_gestures'])
        self.assertTrue(any(row.startswith('goal_not_used_for_gestures:')
                            for row in second['limitations']))

    def test_unknown_alignment_has_no_semantic_or_structural_gestures(self):
        item = evidence('你好', ['ni', 'hao'])
        self.assertEqual(item['coverage']['status'], 'unknown')
        proposal = propose_plan({'vocals': item})
        self.assertEqual(proposal['plan']['candidates'], [])
        self.assertEqual(proposal['analysis']['semantic_gesture_candidates'], 0)

    def test_different_focus_spans_target_different_note_groups(self):
        item = evidence('你好', ['你', '好'], spans=[(0, 1), (1, 2)])
        first = interpretation('first', [0, 2], '你好', [0, 1])
        first['focus'][0]['quote'] = '你'
        second = interpretation('second', [0, 2], '你好', [1, 2])
        second['focus'][0]['quote'] = '好'
        proposal = propose_plan(
            {'vocals': item}, semantic_response=response_for(item, [first, second]))
        semantic = [row for row in proposal['analysis']['candidate_provenance']
                    if row['basis'] == 'imported_semantic_claim']
        self.assertEqual(len(semantic), 2)
        targets = [tuple(scope['note_ids']) for row in semantic for scope in row['scopes']]
        self.assertNotEqual(targets[0], targets[1])
        gesture_targets = []
        for provenance in semantic:
            candidate = next(row for row in proposal['plan']['candidates']
                             if row['id'] == provenance['id'])
            gesture_targets.append(tuple(
                note_id for gesture in candidate['gestures']
                for note_id in gesture['note_ids']))
        self.assertNotEqual(gesture_targets[0], gesture_targets[1])

    def test_explicit_unknown_focus_does_not_expand_to_full_span(self):
        item = evidence('你、好', ['你', '好'], spans=[(0, 1), (2, 3)])
        claim = interpretation('unknown-focus', [0, 3], '你、好', [1, 2])
        claim['focus'][0]['quote'] = '、'
        proposal = propose_plan(
            {'vocals': item}, semantic_response=response_for(item, [claim]))
        self.assertFalse([
            row for row in proposal['analysis']['candidate_provenance']
            if row['basis'] == 'imported_semantic_claim'])
        self.assertTrue(any(row['status'] == 'focus_alignment_unknown'
                            for row in proposal['analysis']['semantic_focus_fallbacks']))

    def test_local_matches_use_scope_features_and_word_note_boundaries(self):
        item = evidence('一二三四', ['一', '二', '三', '四'], spans=[(1, 3)])
        claim = interpretation('word-tail', [1, 3], '二三', [2, 3])
        claim['focus'][0]['quote'] = '三'
        proposal = propose_plan(
            {'vocals': item}, semantic_response=response_for(item, [claim]))
        row = next(row for row in proposal['analysis']['candidate_provenance']
                   if row['basis'] == 'imported_semantic_claim')
        local = row['local_matches'][0]
        self.assertEqual(local['scope_id'], 'word-tail.focus-0')
        self.assertIn('word_end', local['features'])
        self.assertNotIn('word_start', local['features'])
        self.assertTrue(all(reason and method['match_reasons']
                            for method in local['methods']
                            for reason in method['match_reasons']))
        self.assertTrue(all(method['use'] == 'conditional_reference_only'
                            for method in row['source_references']))

    def test_focus_and_intent_limits_are_explicit(self):
        chars = '一二三四五六七八'
        item = evidence(chars + '。', list(chars))
        focus = [{'span': [index, index + 1], 'quote': chars[index], 'role': 'focus'}
                 for index in range(len(chars))]
        claim = interpretation('many', [0, len(chars)], chars, [0, 1],
                               intent='i' * 500)
        claim['focus'] = focus
        proposal = propose_plan(
            {'vocals': item}, semantic_response=response_for(item, [claim]))
        semantic = next(row for row in proposal['plan']['candidates']
                        if any(flag in row['intent'] for flag in ()) or True)
        self.assertTrue(any(len(candidate['intent']) == 500
                            for candidate in proposal['plan']['candidates']))
        self.assertTrue(any(event['code'] == 'max_local_scopes'
                            for event in proposal['analysis']['coverage']['limit_events']))
        self.assertEqual(proposal['analysis']['coverage']['status'], 'local_subset')
        self.assertTrue(any(row['truncated'] for row in
                            proposal['analysis']['candidate_provenance']))

    def test_provider_failure_keeps_structural_baseline(self):
        item = evidence('你好', ['你', '好'], spans=[(0, 1), (1, 2)])
        def failed(_request):
            raise RuntimeError('fixture failure PRIVATE-provider-payload')
        proposal = propose_plan({'vocals': item}, provider=failed)
        self.assertEqual(proposal['semantics']['status'], 'provider_failed')
        self.assertGreaterEqual(len(proposal['plan']['candidates']), 2)
        self.assertEqual(proposal['analysis']['semantic_gesture_candidates'], 0)
        self.assertTrue(any('RuntimeError' in row for row in proposal['limitations']))
        self.assertFalse(any('fixture failure' in row for row in proposal['limitations']))
        self.assertNotIn('PRIVATE-provider-payload', json.dumps(proposal, ensure_ascii=False))

    def test_evidence_content_fingerprint_is_verified(self):
        item = evidence('你好', ['你', '好'])
        changed = copy.deepcopy(item)
        changed['text']['original'] = '再会'
        with self.assertRaises(ValueError):
            propose_plan({'vocals': changed})


if __name__ == '__main__':
    unittest.main(verbosity=2)
