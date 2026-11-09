"""Pure tuning document and application-plan adapter tests."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from preset_documents import PresetError, encode_document
from tuning_preset_documents import (
    PLAN_SCHEMA,
    SUPPORTED_CAPABILITIES,
    make_document,
    plan_application,
    validate_plan,
)

REF_0 = 'C:/fixtures/reference-zero.wav'
REF_2 = 'C:/fixtures/reference-two.wav'
PROFILE = 'C:/fixtures/calibration.json'
PRIOR = 'C:/fixtures/prior.svp'


def current_options(mode):
    values = {
        'expression_mode': mode,
        'track_indices': [0, 2],
        'performance_style': 'gentle',
        'style_strength': .55,
        'style_pitch': True,
        'style_detail': {'onset': .8, 'sustain': .9, 'connection': .7, 'release': .6},
        'style_regions': [],
        'style_scope': 'lead',
        'expression_density': 'original',
        'style_technique': 'chinese_opera',
        'technique_strength': .35,
        'technique_pitch': False,
        'dynamics_strength': .45,
        'retained_match_policy': 'exact',
    }
    if mode == 'reference':
        values.update(reference_dynamics=True, reference_tension=True,
                      reference_sources=reference_rows())
    elif mode == 'calibrated':
        values.update(reference_calibration=PROFILE, reference_sources=reference_rows())
    elif mode == 'retained':
        values.update(retained_project=PRIOR, retained_track_index=3,
                      retained_sources=retained_rows())
    return values


def reference_rows(order=(0, 2)):
    voices = {0: 'lead', 2: 'backing'}
    paths = {0: REF_0, 2: REF_2}
    return [{'track_index': index, 'voice': voices[index], 'path': paths[index]}
            for index in order]


def retained_rows(order=(0, 2)):
    return [{'track_index': index, 'project': PRIOR,
             'source_track_index': 3 if index == 0 else 5} for index in order]


def bindings_for(mode, order=(0, 2)):
    result = {'vocal_tracks': list(order)}
    if mode in ('reference', 'calibrated'):
        result['reference_audio'] = reference_rows(tuple(order))
    if mode == 'calibrated':
        result['calibration_profile'] = PROFILE
    if mode == 'retained':
        result['retained_project'] = retained_rows(tuple(order))
    return result


def project(*, candidate_origin=None, reordered=False):
    tracks = [
        {'track_index': 0, 'name': 'Lead', 'note_count': 4, 'instrumental': False},
        {'track_index': 1, 'name': 'Instrumental', 'note_count': 0, 'instrumental': True},
        {'track_index': 2, 'name': 'Harmony', 'note_count': 3, 'instrumental': False},
    ]
    if reordered:
        tracks.reverse()
    return {
        'path': 'C:/fixtures/current.svp',
        'sha256': 'a' * 64,
        'bytes': 1234,
        'tracks': tracks,
        'candidate_origin': copy.deepcopy(candidate_origin),
    }


def document(mode, suffix=''):
    return make_document(current_options(mode), f'{mode}{suffix}',
                         identifier=f'user.test.{mode}{suffix}')


class TuningPresetDocumentTests(unittest.TestCase):
    def test_all_four_modes_freeze_and_validate_ready_plans(self):
        expected_slots = {
            'preserve': {'vocal_tracks'},
            'reference': {'vocal_tracks', 'reference_audio'},
            'calibrated': {'vocal_tracks', 'reference_audio', 'calibration_profile'},
            'retained': {'vocal_tracks', 'retained_project'},
        }
        for mode in expected_slots:
            with self.subTest(mode=mode):
                value = document(mode)
                self.assertEqual({row['kind'] for row in value['binding_slots']}, expected_slots[mode])
                self.assertIn('tuning.svp-main-group', value['requires']['capabilities'])
                self.assertIn('style.builtin.gentle', value['requires']['capabilities'])
                self.assertIn('technique.builtin.chinese_opera', value['requires']['capabilities'])
                self.assertTrue(set(value['requires']['capabilities']) <= SUPPORTED_CAPABILITIES)

                plan = plan_application(value, project(), bindings_for(mode), before=current_options(mode))
                self.assertEqual(plan['schema'], PLAN_SCHEMA)
                self.assertEqual(plan['input_sha256'], 'a' * 64)
                self.assertEqual(plan['missing'], [])
                self.assertIsNotNone(plan['options'])
                self.assertEqual(plan['options']['expression_mode'], mode)
                self.assertEqual(plan['options']['track_indices'], [0, 2])
                self.assertEqual(plan['options']['performance_style'], 'gentle')
                self.assertEqual(plan['options']['style_technique'], 'chinese_opera')
                self.assertIsInstance(plan['changes'], list)
                self.assertEqual(validate_plan(plan), validate_plan(plan, options=plan['options']))
                self.assertEqual(validate_plan(plan)['changes'], [])

                if mode in ('reference', 'calibrated'):
                    self.assertEqual([row['track_index'] for row in plan['options']['reference_sources']], [0, 2])
                if mode == 'calibrated':
                    self.assertEqual(plan['options']['reference_calibration'], PROFILE)
                if mode == 'retained':
                    self.assertEqual([row['source_track_index'] for row in plan['options']['retained_sources']], [3, 5])

    def test_saving_can_precede_bindings_and_legacy_calibrated_needs_reference_audio(self):
        options = current_options('calibrated')
        options['reference_calibration'] = ''
        value = make_document(options, 'Offline calibration', identifier='user.test.offline_calibrated')
        self.assertEqual({row['kind'] for row in value['binding_slots']},
                         {'vocal_tracks', 'reference_audio', 'calibration_profile'})

        legacy = copy.deepcopy(value)
        legacy['binding_slots'] = [row for row in legacy['binding_slots'] if row['kind'] != 'reference_audio']
        plan = plan_application(legacy, project(), bindings_for('calibrated'))
        self.assertEqual(plan['missing'], [])
        self.assertEqual(len(plan['options']['reference_sources']), 2)

        no_audio = plan_application(legacy, project(), {'vocal_tracks': [0, 2], 'calibration_profile': PROFILE})
        self.assertTrue(no_audio['missing'])
        self.assertIsNone(no_audio['options'])
        with self.assertRaises(PresetError) as caught:
            validate_plan(no_audio)
        self.assertEqual(caught.exception.code, 'plan_incomplete')

    def test_document_excludes_private_inputs_and_rejects_regions_or_foreign_refs(self):
        value = document('calibrated')
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for private in (REF_0, REF_2, PROFILE, PRIOR, 'current.svp'):
            self.assertNotIn(private, encoded)
        self.assertNotIn('track_indices', encoded)
        self.assertNotIn('style_regions', encoded)

        first = make_document(current_options('preserve'), 'First')
        second = make_document(current_options('preserve'), 'Second')
        self.assertNotEqual(first['id'], second['id'])

        with self.assertRaises(PresetError) as caught:
            make_document({'style_regions': [{'start': 0, 'end': 1}]}, 'Regions')
        self.assertEqual(caught.exception.code, 'unsupported_style_regions')
        with self.assertRaises(PresetError) as caught:
            make_document({'performance_style': 'soft_ballad'}, 'Legacy')
        self.assertEqual(caught.exception.code, 'unsupported_reference')
        with self.assertRaises(PresetError) as caught:
            make_document({'confirm_candidate_baseline': True}, 'Local only')
        self.assertEqual(caught.exception.code, 'invalid_document')

    def test_raw_text_and_canonical_digest_are_independent_and_not_caller_asserted(self):
        value = document('reference')
        canonical = encode_document(value)
        original = plan_application(value, project(), bindings_for('reference'))
        self.assertEqual(original['document_text'], canonical.decode('utf-8'))
        self.assertEqual(original['preset']['sha256'], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(original['preset']['canonical_sha256'], hashlib.sha256(canonical).hexdigest())

        text = ('\ufeff' + json.dumps(value, ensure_ascii=False, indent=4) + '\r\n')
        raw = text.encode('utf-8')
        variant = plan_application(value, project(), bindings_for('reference'), raw_document=raw)
        self.assertEqual(variant['document'], original['document'])
        self.assertEqual(variant['preset']['canonical_sha256'], original['preset']['canonical_sha256'])
        self.assertNotEqual(variant['preset']['sha256'], original['preset']['sha256'])
        self.assertEqual(variant['preset']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(variant['document_text'].startswith('\ufeff'))
        self.assertTrue(variant['document_text'].endswith('\r\n'))
        self.assertEqual(variant['input_sha256'], project()['sha256'])
        self.assertEqual(validate_plan(variant)['preset'], variant['preset'])

        forged = copy.deepcopy(original)
        forged['preset']['sha256'] = 'b' * 64
        with self.assertRaises(PresetError):
            validate_plan(forged)

    def test_missing_inputs_remain_pending_and_target_order_is_resolved_by_index(self):
        value = document('reference')
        pending = plan_application(value, project(reordered=True), {})
        self.assertEqual(pending['missing'], ['请选择目标演唱轨。'])
        self.assertIsNone(pending['options'])
        self.assertIsNone(pending['controls']['track_indices'])
        with self.assertRaises(PresetError) as caught:
            validate_plan(pending)
        self.assertEqual(caught.exception.code, 'plan_incomplete')

        order = (2, 0)
        bindings = {
            'vocal_tracks': [2, 0],
            'reference_audio': reference_rows((0, 2)),
        }
        plan = plan_application(value, project(reordered=True), bindings)
        self.assertEqual(plan['controls']['track_indices'], [2, 0])
        self.assertEqual(plan['options']['track_indices'], [2, 0])
        self.assertEqual([row['track_index'] for row in plan['bindings']['reference_audio']], [2, 0])
        self.assertEqual([row['voice'] for row in plan['options']['reference_sources']], ['backing', 'lead'])
        self.assertEqual(validate_plan(plan)['bindings']['vocal_tracks'], [2, 0])

        partial = plan_application(value, project(), {
            'vocal_tracks': [0, 2],
            'reference_audio': reference_rows((0,)),
        })
        self.assertIn('轨道 3 缺少参考音频。', partial['missing'])
        self.assertIsNone(partial['options'])

        incomplete_form = plan_application(value, project(), {
            'vocal_tracks': [0, 2],
            'reference_audio': [
                {'track_index': 0, 'voice': None, 'path': ''},
                {'track_index': 2, 'voice': 'backing', 'path': ''},
            ],
        })
        self.assertEqual(incomplete_form['missing'],
                         ['轨道 1 缺少参考音频。', '轨道 3 缺少参考音频。'])
        self.assertEqual(incomplete_form['bindings']['reference_audio'], [
            {'track_index': 0, 'voice': None, 'path': ''},
            {'track_index': 2, 'voice': 'backing', 'path': ''},
        ])
        self.assertIsNone(incomplete_form['options'])

        with self.assertRaises(PresetError):
            plan_application(value, project(), {'vocal_tracks': [1]})

    def test_partial_retained_binding_is_preserved_without_guessing(self):
        value = document('retained')
        plan = plan_application(value, project(), {
            'vocal_tracks': [0, 2],
            'retained_project': [
                {'track_index': 0, 'project': '', 'source_track_index': 3},
                {'track_index': 2, 'project': PRIOR, 'source_track_index': -1},
            ],
        })
        self.assertEqual(plan['bindings']['retained_project'], [
            {'track_index': 0, 'project': '', 'source_track_index': 3},
            {'track_index': 2, 'project': PRIOR, 'source_track_index': -1},
        ])
        self.assertEqual(plan['missing'],
                         ['轨道 1 缺少沿用工程映射。', '轨道 3 缺少沿用工程映射。'])
        self.assertIsNone(plan['options'])

    def test_project_free_preview_has_no_input_hash_and_validate_stays_pending(self):
        value = document('reference')
        plan = plan_application(value, None, {
            'vocal_tracks': [0],
            'reference_audio': [{'track_index': 0, 'voice': 'backing', 'path': ''}],
        })
        self.assertIsNone(plan['project'])
        self.assertIsNone(plan['input_sha256'])
        self.assertIsNone(plan['options'])
        self.assertIn('尚未载入目标工程。', plan['missing'])
        self.assertEqual(plan['bindings']['reference_audio'], [
            {'track_index': 0, 'voice': 'backing', 'path': ''},
        ])
        with self.assertRaises(PresetError) as caught:
            validate_plan(plan)
        self.assertEqual(caught.exception.code, 'plan_incomplete')

    def test_candidate_baseline_requires_explicit_local_confirmation(self):
        value = document('preserve')
        candidate_project = project(candidate_origin={
            'kind': 'sidecar', 'verified': False, 'candidate': 'C:/fixtures/candidate.svp'})
        pending = plan_application(value, candidate_project, {'vocal_tracks': [0, 2]})
        self.assertEqual(pending['missing'], ['请确认将此候选作为新基线。'])
        self.assertIsNone(pending['options'])
        self.assertEqual(pending['project']['candidate_origin']['verified'], False)
        with self.assertRaises(PresetError) as caught:
            validate_plan(pending)
        self.assertEqual(caught.exception.code, 'plan_incomplete')

        denied = plan_application(value, candidate_project, {
            'vocal_tracks': [0, 2], 'confirm_candidate_baseline': False})
        self.assertEqual(denied['missing'], ['请确认将此候选作为新基线。'])
        ready = plan_application(value, candidate_project, {
            'vocal_tracks': [0, 2], 'confirm_candidate_baseline': True})
        self.assertEqual(ready['missing'], [])
        self.assertIs(ready['bindings']['confirm_candidate_baseline'], True)
        self.assertEqual(validate_plan(ready)['bindings']['confirm_candidate_baseline'], True)

        ordinary = plan_application(value, project(), {'vocal_tracks': [0, 2]})
        self.assertEqual(ordinary['missing'], [])
        self.assertNotIn('confirm_candidate_baseline', ordinary['bindings'])

    def test_unknown_capability_external_reference_and_dependency_are_rejected(self):
        value = document('preserve')
        unknown = copy.deepcopy(value)
        unknown['requires']['capabilities'].append('tuning.future-source')
        with self.assertRaises(PresetError) as caught:
            plan_application(unknown, project(), {'vocal_tracks': [0, 2]})
        self.assertEqual(caught.exception.code, 'unsupported_capability')

        external = {'id': 'user.external.style', 'version': '0.1.0', 'sha256': 'b' * 64}
        dependency = copy.deepcopy(value)
        dependency['payload']['style']['ref'] = external
        dependency['requires']['dependencies'] = [copy.deepcopy(external)]
        with self.assertRaises(PresetError) as caught:
            plan_application(dependency, project(), {'vocal_tracks': [0, 2]})
        self.assertEqual(caught.exception.code, 'unsupported_dependency')

    def test_plan_and_options_tampering_are_rejected_while_display_changes_are_ignored(self):
        value = document('reference')
        before = current_options('reference')
        before.update(performance_style='bold', track_indices=[0])
        shown = plan_application(value, project(), bindings_for('reference'), before=before)
        quiet = plan_application(value, project(), bindings_for('reference'))
        self.assertEqual(shown['input_sha256'], quiet['input_sha256'])
        self.assertTrue(shown['changes'])
        self.assertEqual(quiet['changes'], [])
        self.assertEqual(validate_plan(shown)['changes'], [])

        changed_display = copy.deepcopy(ordinary := plan_application(
            value, project(), bindings_for('reference')))
        changed_display['changes'] = [{'field': 'display.only', 'before': 1, 'after': 2}]
        self.assertEqual(validate_plan(changed_display)['input_sha256'], ordinary['input_sha256'])

        mutations = []
        item = copy.deepcopy(ordinary); item['controls']['style_strength'] = .1; mutations.append(item)
        item = copy.deepcopy(ordinary); item['options']['performance_style'] = 'sad'; mutations.append(item)
        item = copy.deepcopy(ordinary); item['bindings']['reference_audio'][0]['path'] = 'C:/changed.wav'; mutations.append(item)
        item = copy.deepcopy(ordinary); item['input_sha256'] = 'c' * 64; mutations.append(item)
        item = copy.deepcopy(ordinary); item['document']['payload']['style']['strength'] = .1; mutations.append(item)
        for item in mutations:
            with self.subTest(field=item.get('input_sha256')):
                with self.assertRaises(PresetError):
                    validate_plan(item)

        tampered_options = copy.deepcopy(ordinary['options'])
        tampered_options['style_strength'] = .12
        with self.assertRaises(PresetError) as caught:
            validate_plan(ordinary, options=tampered_options)
        self.assertEqual(caught.exception.code, 'options_mismatch')

        changed_project = project()
        changed_project['sha256'] = 'd' * 64
        with self.assertRaises(PresetError):
            validate_plan(ordinary, project=changed_project)

    def test_inputs_and_plan_nested_values_do_not_share_mutation(self):
        value = document('retained')
        original_value = copy.deepcopy(value)
        live_project = project()
        original_project = copy.deepcopy(live_project)
        live_bindings = bindings_for('retained')
        original_bindings = copy.deepcopy(live_bindings)
        plan = plan_application(value, live_project, live_bindings)
        frozen = copy.deepcopy(plan)

        value['payload']['style']['strength'] = .01
        live_project['tracks'][0]['name'] = 'Changed'
        live_bindings['retained_project'][0]['project'] = 'C:/changed.svp'
        plan['document']['payload']['style']['strength'] = .02
        plan['bindings']['retained_project'][0]['source_track_index'] = 99

        self.assertEqual(value['payload']['style']['strength'], .01)
        self.assertEqual(original_value['payload']['style']['strength'], .55)
        self.assertEqual(live_project['tracks'][0]['name'], 'Changed')
        self.assertEqual(live_bindings['retained_project'][0]['project'], 'C:/changed.svp')
        self.assertEqual(frozen['document']['payload']['style']['strength'], .55)
        self.assertEqual(frozen['project']['tracks'][0]['name'], 'Lead')
        self.assertEqual(frozen['bindings']['retained_project'][0]['source_track_index'], 3)
        self.assertEqual(original_project['tracks'][0]['name'], 'Lead')
        self.assertEqual(original_bindings['retained_project'][0]['source_track_index'], 3)

    def test_planner_has_no_file_or_path_metadata_in_safe_controls(self):
        value = document('calibrated')
        plan = plan_application(value, project(), bindings_for('calibrated'))
        serialized = json.dumps(plan['document'], ensure_ascii=False) + json.dumps(plan['controls'], ensure_ascii=False)
        for private in (REF_0, REF_2, PROFILE, 'current.svp'):
            self.assertNotIn(private, serialized)
        self.assertEqual(plan['controls']['style_regions'], [])
        self.assertNotIn('reference_sources', plan['controls'])
        self.assertNotIn('reference_calibration', plan['controls'])
        self.assertNotIn('retained_sources', plan['controls'])


if __name__ == '__main__':
    unittest.main()