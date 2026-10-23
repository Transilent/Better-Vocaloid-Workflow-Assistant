"""Backend acceptance for the pure BVWA tuning transaction.

Heavy acoustic extraction is stubbed where the behavior under test is routing,
protection, option validation, or transaction ordering. Real two-song checks are
gated and only write candidates below work/acceptance.
"""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import calibration_refinement
import tuning_engine
from tuning_engine import process_project, settings, sources_for
from calibration_workflow import retain_project


Q = 705600000
TEST_TEMP = APP / 'work/acceptance/test-temp'
TEST_TEMP.mkdir(parents=True, exist_ok=True)
ALL_CONTROLS = ('loudness', 'tension', 'breathiness', 'voicing', 'gender', 'toneShift')


def note(onset=0, duration=Q, pitch=60, lyric='a', attributes=None):
    value = {'onset': onset, 'duration': duration, 'pitch': pitch, 'lyrics': lyric}
    if attributes is not None:
        value['attributes'] = copy.deepcopy(attributes)
    return value


def vocal(name='Lead Vocal', offset=0, notes=None, parameters=None, bank='Test Bank'):
    return {
        'name': name,
        'mainRef': {
            'blickOffset': offset,
            'pitchOffset': 0,
            'isInstrumental': False,
            'database': {'name': bank, 'language': 'mandarin'},
            'voice': {'preset': name, 'paramLoudness': .25},
        },
        'mainGroup': {
            'uuid': name + '-group',
            'notes': copy.deepcopy(notes) if notes is not None else [
                note(0, Q, 60, '你', {'paramTension': .1, 'retakes': [3, 1]}),
                note(Q, 2 * Q, 64, '好', {'paramLoudness': .2}),
            ],
            'parameters': copy.deepcopy(parameters) if parameters is not None else {
                'loudness': {'mode': 'linear', 'points': [0, -2., 4 * Q, 2.]},
                'tension': {'mode': 'linear', 'points': [0, 0., Q, .1]},
                'pitchDelta': {'mode': 'linear', 'points': [0, 1., Q, 2.]},
                'unknownVendor': {'mode': 'cosine', 'points': [0, 7., Q, 9.], 'tag': 'keep'},
            },
        },
        'retakes': {'keep': True},
        'groups': [],
    }


def base_project():
    return {
        'version': '1.0.0',
        'uuid': 'fixture-project',
        'library': [{'name': 'song', 'secret': 9}],
        'renderConfig': {'keep': True},
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}, {'position': 2 * Q, 'bpm': 75}],
        },
        'tracks': [
            vocal(offset=Q // 3),
            vocal('Backing Vocal', offset=0, notes=[note(Q, Q, 55, 'x')], parameters={
                'loudness': {'mode': 'linear', 'points': [0, -1., 2 * Q, 1.]},
                'tension': {'mode': 'cubic', 'points': [0, 0., Q, .04]},
                'unknownBacking': {'mode': 'linear', 'points': [0, 3., Q, 4.]},
            }, bank='Backing Bank'),
            {'name': 'Instrumental', 'mainRef': {'isInstrumental': True, 'audio': {'filename': 'accompaniment.wav'}},
             'mainGroup': {'notes': [], 'parameters': {}}, 'retakes': {'instrumental': True}},
        ],
    }


def write_dummy(path, payload=b'not-real-audio'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def profile_row(bank='Test Bank', probe_names=ALL_CONTROLS, *, voice=None, database=None):
    probes = []
    steps = {'loudness': .3, 'tension': .1, 'breathiness': .1,
             'voicing': -.1, 'gender': .1, 'toneShift': 10.}
    for name in probe_names:
        probes.append({'parameter': name, 'step': steps[name], 'audio': name + '.wav'})
    return {
        'score_sha256': '0' * 64,
        'database': copy.deepcopy(database) if database is not None else {
            'name': bank, 'language': 'mandarin'
        },
        'voice': copy.deepcopy(voice) if voice is not None else {'preset': 'Lead Vocal'},
        'baseline': 'neutral.wav',
        'probes': probes,
        'render_channel_mode': 'standard',
    }


def write_profile(root, row):
    root = Path(root)
    for name in ('neutral.wav', *(p['audio'] for p in row['probes'])):
        write_dummy(root / name)
    path = root / 'calibration.json'
    path.write_text(json.dumps({'schema': 1, 'fit_strategy': 'match', 'tracks': {'lead': row}}), encoding='utf-8')
    return path


class TuningEngineTests(unittest.TestCase):
    def test_default_and_dense_preserve_are_pure_and_protected(self):
        data = base_project()
        frozen = copy.deepcopy(data)
        output, report = process_project(data, {'track_indices': [0], 'expression_density': 'original'})
        self.assertEqual(output, frozen)
        self.assertEqual(report['status'], 'unchanged')
        self.assertEqual(report['stages'], ['primary', 'density'])
        self.assertEqual(report['final_density_passes'], 1)
        self.assertFalse(report['synthesized'])
        self.assertFalse(report['listened'])
        self.assertEqual(data, frozen)

        output, report = process_project(data, {'track_indices': [0], 'expression_density': 'dense'})
        self.assertEqual(data, frozen)
        self.assertTrue(report['protected_content_unchanged'])
        self.assertEqual(output['tracks'][1:], frozen['tracks'][1:])
        lead, original = output['tracks'][0], frozen['tracks'][0]
        self.assertEqual(lead['mainGroup']['notes'], original['mainGroup']['notes'])
        self.assertEqual(lead['mainGroup']['parameters']['pitchDelta'], original['mainGroup']['parameters']['pitchDelta'])
        self.assertEqual(lead['mainGroup']['parameters']['unknownVendor'], original['mainGroup']['parameters']['unknownVendor'])
        self.assertEqual(lead['mainRef'], original['mainRef'])
        self.assertEqual(lead['retakes'], original['retakes'])
        for name in ('loudness', 'tension'):
            before = original['mainGroup']['parameters'][name]['points']
            after = lead['mainGroup']['parameters'][name]['points']
            self.assertTrue(set(before[::2]).issubset(set(after[::2])))

    def test_option_modes_and_reference_mapping_are_explicit(self):
        with self.assertRaisesRegex(ValueError, '只能选择一种'):
            settings({'expression_mode': 'preserve', 'reference_dynamics': True})
        with self.assertRaisesRegex(ValueError, '校准配置'):
            settings({'expression_mode': 'calibrated'})
        with self.assertRaisesRegex(ValueError, '原参数工程'):
            settings({'expression_mode': 'retained'})
        with self.assertRaisesRegex(ValueError, '不重复'):
            settings({'expression_mode': 'preserve', 'track_indices': [0, 0]})
        with self.assertRaisesRegex(ValueError, '未知调音选项'):
            settings({'expression_mode': 'preserve', 'invented': True})

        with tempfile.TemporaryDirectory(prefix='engine-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            lead = write_dummy(root / 'lead.wav')
            backing = write_dummy(root / 'backing.wav')
            options = settings({
                'expression_mode': 'reference', 'track_indices': [0, 1], 'reference_dynamics': True,
                'reference_sources': [
                    {'track_index': 1, 'voice': 'backing', 'path': str(backing)},
                    {'track_index': 0, 'voice': 'lead', 'path': str(lead)},
                ],
            })
            mapped = sources_for(options, [0, 1])
            self.assertEqual([row['voice'] for row in mapped], ['lead', 'backing'])
            self.assertEqual([Path(row['path']).name for row in mapped], ['lead.wav', 'backing.wav'])
            with self.assertRaisesRegex(ValueError, '每条所选演唱轨'):
                sources_for(options, [0])
            duplicate_voice = copy.deepcopy(options)
            duplicate_voice['reference_sources'][1]['voice'] = 'backing'
            repeated=sources_for(duplicate_voice, [0, 1])
            self.assertEqual([row['voice'] for row in repeated],['backing','backing'])
            self.assertEqual([row['cache_key'] for row in repeated],['track-0','track-1'])

    def test_reference_routing_uses_real_track_indices_not_zip_order(self):
        data = base_project()
        # Routing uses a neutral offset baseline; nonzero authored offsets are
        # separately rejected before reference analysis below.
        for track in data['tracks'][:2]:
            track['mainRef']['voice'].pop('paramLoudness',None)
            for item in track['mainGroup']['notes']:
                item.get('attributes',{}).pop('paramLoudness',None)
        captured = {}

        def fake_apply(view, sources, strength, cancel, **kwargs):
            captured['indices'] = [track['name'] for track in view['tracks']]
            captured['sources'] = copy.deepcopy(sources)
            captured['kwargs'] = copy.deepcopy(kwargs)
            output = copy.deepcopy(view)
            for index, source in enumerate(sources):
                output['tracks'][index]['mainGroup']['parameters']['loudness'] = {
                    'mode': 'linear', 'points': [0, -1. if source['voice'] == 'backing' else -3., Q, 0.]
                }
            return output, {'status': 'applied', 'mode': 'reference', 'parameters': ['loudness'],
                            'tracks': [{'voice': s['voice']} for s in sources]}

        with tempfile.TemporaryDirectory(prefix='engine-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            lead = write_dummy(root / 'lead.wav')
            backing = write_dummy(root / 'backing.wav')
            options = {
                'expression_mode': 'reference', 'track_indices': [1, 0], 'reference_dynamics': True,
                'reference_sources': [
                    {'track_index': 0, 'voice': 'lead', 'path': str(lead)},
                    {'track_index': 1, 'voice': 'backing', 'path': str(backing)},
                ],
                'expression_density': 'original',
            }
            with patch('vocal_expression.apply_project', side_effect=fake_apply):
                output, report = process_project(data, options, cache=root / 'features')
        self.assertEqual(captured['indices'], ['Backing Vocal', 'Lead Vocal'])
        self.assertEqual([row['voice'] for row in captured['sources']], ['backing', 'lead'])
        self.assertFalse(captured['kwargs']['finalize'])
        self.assertEqual(Path(captured['kwargs']['feature_cache']).name, 'features')
        self.assertEqual(output['tracks'][1]['mainGroup']['parameters']['loudness']['points'][1], -1.)
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['loudness']['points'][1], -3.)
        self.assertEqual(output['tracks'][2], data['tracks'][2])
        self.assertEqual(report['stages'], ['primary', 'density'])

    def test_reference_refuses_to_stack_existing_selected_channel_offsets(self):
        data=base_project();frozen=copy.deepcopy(data)
        with tempfile.TemporaryDirectory(prefix='reference-bias-',dir=TEST_TEMP) as temp:
            reference=write_dummy(Path(temp)/'reference.wav')
            options={'expression_mode':'reference','track_indices':[0],'reference_dynamics':True,
                     'reference_sources':[{'track_index':0,'voice':'lead','path':str(reference)}],
                     'expression_density':'original'}
            with patch('vocal_expression.apply_project',side_effect=AssertionError('analysis must not run')):
                with self.assertRaisesRegex(ValueError,'不能静默叠加'):
                    process_project(data,options)
            data['tracks'][0]['mainRef']['voice'].pop('paramLoudness')
            with patch('vocal_expression.apply_project',side_effect=AssertionError('analysis must not run')):
                with self.assertRaisesRegex(ValueError,'不能静默叠加'):
                    process_project(data,options)
        self.assertEqual(frozen['tracks'][0]['mainRef']['voice']['paramLoudness'],.25)
        self.assertEqual(data['tracks'][0]['mainGroup'],frozen['tracks'][0]['mainGroup'])

    def test_retained_sources_map_each_actual_raw_track_to_unique_ordinal(self):
        data = base_project()
        data['tracks'] = [data['tracks'][0], data['tracks'][2], data['tracks'][1]]
        calls = []
        def fake_project_data(path):
            _ = Path(path).name
            prior = copy.deepcopy(data)
            prior['tracks'][0]['mainGroup']['parameters']['loudness'] = {
                'mode': 'linear', 'points': [0, -1., Q, 0.]
            }
            prior['tracks'][2]['mainGroup']['parameters']['loudness'] = {
                'mode': 'linear', 'points': [0, -3., Q, 0.]
            }
            return prior
        def fake_retain(target_data, source_data, *, track_index=0, source_track_index=0):
            calls.append((track_index, source_track_index))
            output = copy.deepcopy(target_data)
            vocal_rows = [(i, track) for i, track in enumerate(output['tracks'])
                          if not track.get('mainRef', {}).get('isInstrumental')]
            raw_index, track = vocal_rows[track_index]
            source_vocal = [(i, row) for i, row in enumerate(source_data['tracks'])
                            if not row.get('mainRef', {}).get('isInstrumental')][source_track_index][1]
            track['mainGroup']['parameters']['loudness'] = copy.deepcopy(
                source_vocal['mainGroup']['parameters']['loudness'])
            return output, {'kind': 'retained_automation', 'target_track_index': raw_index,
                            'source_track_index': source_track_index, 'new_fit': False}
        options = {
            'expression_mode': 'retained', 'track_indices': [2, 0],
            'retained_sources': [
                {'track_index': 2, 'project': 'backing.svp', 'source_track_index': 1, 'voice': 'backing'},
                {'track_index': 0, 'project': 'lead.svp', 'source_track_index': 0, 'voice': 'lead'},
            ],
            'expression_density': 'original',
        }
        with patch('calibration_session.project_data', side_effect=fake_project_data), \
             patch('calibration_workflow.retain_project', side_effect=fake_retain):
            output, report = process_project(data, options)
        self.assertEqual(calls, [(1, 1), (0, 0)])
        self.assertEqual(output['tracks'][2]['mainGroup']['parameters']['loudness']['points'][1], -3.)
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['loudness']['points'][1], -1.)
        self.assertEqual([row['track_index'] for row in report['primary']['tracks']], [2, 0])
        self.assertEqual([row['voice'] for row in report['primary']['tracks']], ['backing', 'lead'])
        self.assertFalse(report['primary']['new_fit'])

    def test_style_pitch_has_one_final_density_and_protects_note_bank_retakes(self):
        data = base_project()
        frozen = copy.deepcopy(data)
        real_finalize = calibration_refinement.finalize_expression
        with patch('calibration_refinement.finalize_expression', wraps=real_finalize) as spy:
            output, report = process_project(data, {
                'expression_mode': 'preserve', 'track_indices': [0], 'performance_style': 'power_pop',
                'style_strength': .6, 'style_pitch': True, 'expression_density': 'dense',
            })
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(report['stages'], ['primary', 'style', 'density'])
        self.assertEqual(report['final_density_passes'], 1)
        self.assertEqual(data, frozen)
        self.assertTrue(report['pitch_changed'])
        lead, original = output['tracks'][0], frozen['tracks'][0]
        self.assertNotEqual(lead['mainGroup']['parameters']['pitchDelta'], original['mainGroup']['parameters']['pitchDelta'])
        self.assertEqual(lead['mainGroup']['notes'], original['mainGroup']['notes'])
        self.assertEqual(lead['mainRef'], original['mainRef'])
        self.assertEqual(lead['retakes'], original['retakes'])
        self.assertEqual(lead['mainGroup']['parameters']['unknownVendor'], original['mainGroup']['parameters']['unknownVendor'])
        self.assertEqual(output['tracks'][1:], frozen['tracks'][1:])

    def test_calibration_partial_profile_rejects_unmeasured_authored_bias(self):
        data = base_project()
        with tempfile.TemporaryDirectory(prefix='engine-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            partial = write_profile(root / 'partial', profile_row(probe_names=('loudness',)))
            reference = write_dummy(root / 'reference.wav')
            options = {
                'expression_mode': 'calibrated', 'track_indices': [0],
                'reference_calibration': str(partial), 'dynamics_strength': .6,
                'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
            }
            with self.assertRaisesRegex(ValueError, 'tension'):
                process_project(data, options)

            biased = copy.deepcopy(data)
            biased['tracks'][0]['mainGroup']['parameters'].pop('tension')
            biased['tracks'][0]['mainRef']['voice']['paramTension'] = .2
            profile = write_profile(root / 'partial-bias', profile_row(
                probe_names=('loudness', 'breathiness', 'voicing', 'gender', 'toneShift')))
            options['reference_calibration'] = str(profile)
            with self.assertRaisesRegex(ValueError, 'paramTension'):
                process_project(biased, options)

    def test_calibration_preflight_rejects_voice_and_database_config_drift(self):
        data = base_project()
        with tempfile.TemporaryDirectory(prefix='cal-preflight-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            reference = write_dummy(root / 'reference.wav')
            variants = [
                (profile_row(voice={'preset': 'Lead Vocal', 'unknownVoice': 1}),
                 '非表现参数的声库设置已改变'),
                (profile_row(database={'name': 'Test Bank', 'language': 'japanese'}),
                 '声库配置与试音配置不同'),
                (profile_row(database={'name': 'Test Bank', 'language': 'mandarin', 'formant': 2}),
                 '声库配置与试音配置不同'),
            ]
            for index, (row, message) in enumerate(variants):
                profile = write_profile(root / ('profile-' + str(index)), row)
                options = {
                    'expression_mode': 'calibrated', 'track_indices': [0],
                    'reference_calibration': str(profile), 'dynamics_strength': .6,
                    'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
                    'expression_density': 'original',
                }
                with patch('expression_calibration.apply_project',
                           side_effect=AssertionError('adapter must not run after preflight rejection')):
                    with self.assertRaisesRegex(ValueError, message):
                        process_project(data, options)

    def test_complete_calibration_profile_is_allowed_and_unselected_track_is_protected(self):
        data = base_project()
        frozen = copy.deepcopy(data)
        with tempfile.TemporaryDirectory(prefix='engine-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            profile = write_profile(root / 'complete', profile_row())
            reference = write_dummy(root / 'reference.wav')

            def fake_apply(view, sources, profile_path, strength, cancel, **kwargs):
                output = copy.deepcopy(view)
                output['tracks'][0]['mainGroup']['parameters']['toneShift'] = {
                    'mode': 'linear', 'points': [0, 0., Q, 12.]
                }
                return output, {'status': 'applied', 'schema': 1, 'tracks': [{'voice': 'lead', 'status': 'applied'}],
                                'strategy': 'match', 'expression_density': {'status': 'deferred'},
                                'deferred': ['expression_density'], 'voicebank_calibrated': True,
                                'perceptual_improvement_verified': False}

            options = {
                'expression_mode': 'calibrated', 'track_indices': [0],
                'reference_calibration': str(profile), 'dynamics_strength': .6,
                'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
                'expression_density': 'original',
            }
            with patch('expression_calibration.apply_project', side_effect=fake_apply) as adapter:
                output, report = process_project(data, options)
        self.assertEqual(adapter.call_count, 1)
        self.assertFalse(adapter.call_args.kwargs['finalize'])
        self.assertEqual(output['tracks'][1:], frozen['tracks'][1:])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['toneShift']['points'], [0, 0., Q, 12.])
        self.assertIn('primary', report['stages'])
        self.assertEqual(report['calibration_metrics_scope'], 'primary_stage_before_style')
        self.assertEqual(report['expression_density']['status'], 'preserved_original')

    def test_zero_strength_primary_does_not_analyze_or_change_input(self):
        data = base_project()
        frozen = copy.deepcopy(data)
        with tempfile.TemporaryDirectory(prefix='zero-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            reference = write_dummy(root / 'reference.wav')
            profile = write_dummy(root / 'profile.json', b'{}')
            options = [
                {'expression_mode': 'reference', 'track_indices': [0], 'reference_dynamics': True,
                 'dynamics_strength': 0, 'expression_density': 'original',
                 'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}]},
                {'expression_mode': 'calibrated', 'track_indices': [0],
                 'reference_calibration': str(profile), 'dynamics_strength': 0,
                 'expression_density': 'original',
                 'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}]},
            ]
            with (patch('vocal_expression.apply_project', side_effect=AssertionError('reference analysis ran')),
                  patch('expression_calibration.apply_project', side_effect=AssertionError('calibration analysis ran'))):
                for option in options:
                    output, report = process_project(data, option)
                    self.assertEqual(output, frozen)
                    self.assertEqual(report['primary']['status'], 'not_selected')
                    self.assertEqual(report['primary']['reason'], 'zero_strength')
                    self.assertEqual(report['primary']['tracks'], [])
                    self.assertFalse(report['primary']['voicebank_calibrated'])
        self.assertEqual(data, frozen)

    def test_nonlinear_and_fractional_authoring_fail_closed_without_truncation(self):
        data = base_project()
        cubic = {'mode': 'cubic', 'points': [0, 0., Q, .1]}
        data['tracks'][0]['mainGroup']['parameters'] = {'tension': cubic}
        frozen = copy.deepcopy(data)
        output, report = process_project(data, {
            'track_indices': [0], 'performance_style': 'soft_ballad', 'expression_density': 'dense'
        })
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['tension'], cubic)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['style']['tracks'][0]['controls']['tension']['reason'], 'preserved_nonlinear')
        self.assertEqual(report['expression_density']['unsupported_nonlinear_controls'], 1)

        fractional = {'mode': 'linear', 'points': [.25, 0., float(Q) + .25, .5]}
        data = base_project()
        data['tracks'][0]['mainGroup']['parameters'] = {'tension': fractional}
        frozen = copy.deepcopy(data)
        output, report = process_project(data, {
            'track_indices': [0], 'expression_density': 'dense', 'performance_style': 'soft_ballad'
        })
        self.assertEqual(data,frozen)
        points=output['tracks'][0]['mainGroup']['parameters']['tension']['points']
        locations={position:slot for slot,position in enumerate(points[::2])}
        for position in fractional['points'][::2]:
            self.assertIn(position,locations)
            self.assertIs(type(points[2*locations[position]]),type(position))
        before_density,_=process_project(data,{
            'track_indices':[0],'expression_density':'original','performance_style':'soft_ballad'})
        baseline=before_density['tracks'][0]['mainGroup']['parameters']['tension']['points']
        import numpy as np
        probes=np.linspace(min(points[::2]),max(points[::2]),1001)
        np.testing.assert_allclose(np.interp(probes,points[::2],points[1::2]),
                                   np.interp(probes,baseline[::2],baseline[1::2]),rtol=0,atol=1e-12)
        self.assertGreater(len(points),len(baseline))

        duplicate = {'mode': 'linear', 'points': [0, 0., Q, .1, Q, .2]}
        data = base_project()
        data['tracks'][0]['mainGroup']['parameters'] = {'tension': duplicate}
        frozen = copy.deepcopy(data)
        with self.assertRaises(ValueError):
            process_project(data, {'track_indices': [0], 'expression_density': 'dense'})
        self.assertEqual(data, frozen)

    def test_cancel_at_each_composite_stage_keeps_input_unchanged(self):
        data = base_project()
        frozen = copy.deepcopy(data)

        def at_stage(label):
            started = {'value': False}
            def cancel():
                if started['value']:
                    raise InterruptedError('cancel:' + label)
            def progress(text):
                if text == label:
                    started['value'] = True
            return cancel, progress

        for label in ('叠加所选风格增量', '统一保形加密与保护校验'):
            cancel, progress = at_stage(label)
            with self.assertRaises(InterruptedError):
                process_project(data, {
                    'track_indices': [0], 'performance_style': 'soft_ballad', 'style_strength': .6,
                    'style_pitch': True, 'expression_density': 'dense',
                }, cancel=cancel, progress=progress)
            self.assertEqual(data, frozen)

    def test_retain_project_is_pure_and_keeps_target_score_and_pitch(self):
        target = base_project()
        source = base_project()
        source['tracks'][0]['mainGroup']['parameters'] = {
            'loudness': {'mode': 'linear', 'points': [0, -4., Q, 1.]},
            'tension': {'mode': 'linear', 'points': [0, -.2, Q, .2]},
            'pitchDelta': {'mode': 'linear', 'points': [0, 9., Q, 10.]},
        }
        source['tracks'][0]['mainRef']['voice']['paramLoudness'] = .5
        target['tracks'][0]['mainRef']['voice']['unknownKeep'] = 'target'
        source['tracks'][0]['mainRef']['voice']['unknownKeep'] = 'source'
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        output, report = retain_project(target, source, track_index=0, source_track_index=0)
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)
        self.assertEqual(output['tracks'][0]['mainGroup']['notes'], target_before['tracks'][0]['mainGroup']['notes'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['pitchDelta'],
                         target_before['tracks'][0]['mainGroup']['parameters']['pitchDelta'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['loudness'],
                         source_before['tracks'][0]['mainGroup']['parameters']['loudness'])
        self.assertEqual(output['tracks'][0]['mainRef']['voice']['unknownKeep'], 'target')
        self.assertEqual(output['tracks'][0]['mainRef']['voice']['paramLoudness'], .5)
        self.assertFalse(report['new_fit'])
        self.assertGreaterEqual(report['bias_cleanup']['removed_count'], 1)

    def test_legacy_adapter_prefers_calibration_and_finalizes_once(self):
        data = base_project()
        with tempfile.TemporaryDirectory(prefix='engine-', dir=TEST_TEMP) as temp:
            job = Path(temp)
            calls = {}
            def fake_process(project, options, **kwargs):
                calls['options'] = copy.deepcopy(options)
                calls['finalize'] = kwargs.get('finalize')
                return copy.deepcopy(project), {
                    'schema': 1, 'mode': options['expression_mode'], 'track_indices': options['track_indices'],
                    'stages': ['primary'], 'status': 'applied', 'primary': {'status': 'applied', 'mode': options['expression_mode']},
                    'style': {'status': 'not_selected'}, 'expression_density': {'status': 'deferred', 'deferred': True},
                    'deferred': ['expression_density'], 'changed': False,
                }
            def fake_finalize(output, indices, mode, cancel):
                calls['finalize_calls'] = calls.get('finalize_calls', 0) + 1
                calls['density_indices'] = list(indices)
                return {'status': 'preserved_original', 'mode': mode, 'added_points': 0,
                        'unsupported_nonlinear_controls': 0, 'tracks': []}
            request = {
                'language': 'zh', 'reference_calibration': 'profile.json', 'prepare_only': False,
                'reference_dynamics': True, 'reference_tension': True, 'dynamics_strength': .6,
                'expression_density': 'original',
                'retained_expression': {'tracks': {'lead': {'file': 'old.svp', 'track_index': 0}}},
            }
            tracks = [{'voice': 'lead'}, {'voice': 'backing'}]
            with patch.object(tuning_engine, 'process_project', side_effect=fake_process), \
                 patch.object(tuning_engine, 'finalize_project', side_effect=fake_finalize), \
                 patch('calibration_workflow.retained_signature', side_effect=AssertionError('retained must not override profile')):
                output, details = tuning_engine.legacy_export(data, job, request, tracks)
        self.assertEqual(calls['options']['expression_mode'], 'calibrated')
        self.assertFalse(calls['finalize'])
        self.assertEqual(calls['finalize_calls'], 1)
        self.assertEqual(calls['density_indices'], [0, 1])
        self.assertEqual(details['parameter_source_policy'], 'calibrated_then_retained_then_reference_exclusive')
        self.assertIn('calibration_fit', details)
        self.assertNotIn('retained_expression', details)
        self.assertNotIn('reference_dynamics', details)
        self.assertEqual(details['expression_density']['status'], 'preserved_original')

    @unittest.skipUnless(os.environ.get('BVWA_RUN_BASELINE_ACCEPTANCE') == '1',
                         'set BVWA_RUN_BASELINE_ACCEPTANCE=1 for real two-song baseline check')
    def test_external_baseline_preserve_style_pitch_dense_protection(self):
        manifest_path = Path(os.environ['BVWA_BASELINE_MANIFEST']).resolve()
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        sources = manifest.get('sources')
        self.assertIsInstance(sources, list)
        self.assertTrue(sources)
        destination = APP / 'work/acceptance/baseline-candidates'
        destination.mkdir(parents=True, exist_ok=True)
        summary = {'manifest': str(manifest_path), 'audio_synthesized': False,
                   'listened': False, 'perceptual_claim': False, 'sources': {}}
        for ordinal, item in enumerate(sources):
            with self.subTest(source=ordinal):
                self.assertIsInstance(item, dict)
                source = Path(item['path']).resolve()
                expected_sha = item['expected_sha']
                raw = source.read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_sha)
                data = json.loads(raw.rstrip(b'\x00').decode('utf-8-sig'))
                frozen = copy.deepcopy(data)
                vocals = [i for i, track in enumerate(data['tracks'])
                          if not track.get('mainRef', {}).get('isInstrumental')]
                selected = list(item.get('track_indices') or vocals[:1])
                self.assertTrue(selected)
                self.assertTrue(set(selected).issubset(vocals))

                preserved, preserve_report = process_project(data, {
                    'track_indices': selected, 'expression_density': 'original'
                })
                self.assertEqual(preserved, frozen)
                self.assertEqual(preserve_report['status'], 'unchanged')

                output, report = process_project(data, {
                    'track_indices': selected,
                    'performance_style': item.get('performance_style', 'soft_ballad'),
                    'style_strength': item.get('style_strength', .2),
                    'style_pitch': item.get('style_pitch', True),
                    'expression_density': item.get('expression_density', 'dense'),
                })
                self.assertEqual(source.read_bytes(), raw)
                self.assertEqual(data, frozen)
                self.assertEqual(report['stages'], ['primary', 'style', 'density'])
                self.assertEqual(report['final_density_passes'], 1)
                self.assertTrue(report['protected_content_unchanged'])
                self.assertTrue(report['notes_and_timing_unchanged'])
                for index in range(len(data['tracks'])):
                    if index not in selected:
                        self.assertEqual(output['tracks'][index], frozen['tracks'][index])
                for index in selected:
                    self.assertEqual(output['tracks'][index]['mainGroup']['notes'],
                                     frozen['tracks'][index]['mainGroup']['notes'])
                    self.assertEqual(output['tracks'][index]['mainRef'],
                                     frozen['tracks'][index]['mainRef'])
                    self.assertEqual(output['tracks'][index].get('retakes'),
                                     frozen['tracks'][index].get('retakes'))
                    before_params = frozen['tracks'][index]['mainGroup']['parameters']
                    after_params = output['tracks'][index]['mainGroup']['parameters']
                    for name, curve in before_params.items():
                        if name not in {'loudness', 'tension', 'breathiness', 'voicing',
                                        'gender', 'toneShift', 'pitchDelta', 'vibratoEnv'}:
                            self.assertEqual(after_params[name], curve)
                        elif curve.get('mode', 'cubic') == 'linear' and curve.get('points'):
                            self.assertTrue(set(curve['points'][::2]).issubset(
                                set(after_params[name]['points'][::2])))
                stem = str(item.get('candidate_stem') or ('source-%02d' % ordinal))
                candidate = destination / (stem + '.svp')
                payload = json.dumps(output, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
                candidate.write_bytes(payload)
                self.assertEqual(json.loads(candidate.read_bytes()), output)
                runtime = APP / 'dependencies/export'
                self.assertTrue((runtime / 'libresvip/__init__.py').is_file())
                old_appdata, old_localappdata = os.environ.get('APPDATA'), os.environ.get('LOCALAPPDATA')
                state = APP / 'work/acceptance/libresvip-state'
                state.mkdir(parents=True, exist_ok=True)
                os.environ['APPDATA'] = os.environ['LOCALAPPDATA'] = str(state)
                if str(runtime) not in sys.path:
                    sys.path.insert(0, str(runtime))
                try:
                    from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
                    from libresvip.model.base import SingingTrack
                    loaded = SynthVStudioConverter.load(candidate, {
                        'import_pitch': True, 'import_instrumental_track': False
                    })
                    libresvip_notes = sum(len(track.note_list) for track in loaded.track_list
                                           if isinstance(track, SingingTrack))
                finally:
                    if old_appdata is None:
                        os.environ.pop('APPDATA', None)
                    else:
                        os.environ['APPDATA'] = old_appdata
                    if old_localappdata is None:
                        os.environ.pop('LOCALAPPDATA', None)
                    else:
                        os.environ['LOCALAPPDATA'] = old_localappdata
                source_notes = sum(len(track.get('mainGroup', {}).get('notes', []))
                                   for track in frozen['tracks']
                                   if not track.get('mainRef', {}).get('isInstrumental'))
                candidate_notes = sum(len(track.get('mainGroup', {}).get('notes', []))
                                      for track in output['tracks']
                                      if not track.get('mainRef', {}).get('isInstrumental'))
                self.assertEqual(source_notes, candidate_notes)
                self.assertEqual(candidate_notes, libresvip_notes)

                summary['sources'][stem] = {
                    'source': str(source), 'source_sha256': expected_sha, 'candidate': str(candidate),
                    'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    'selected_track_indices': selected, 'status': report['status'], 'stages': report['stages'],
                    'final_density_passes': report['final_density_passes'], 'changed': report['changed'],
                    'style_status': report['style']['status'], 'pitch_changed': report['pitch_changed'],
                    'notes_unchanged': report['notes_and_timing_unchanged'],
                    'protected_content_unchanged': report['protected_content_unchanged'],
                    'unsupported_nonlinear_controls': report['expression_density']['unsupported_nonlinear_controls'],
                    'libresvip_note_count': libresvip_notes,
                }
        (APP / 'work/acceptance/baseline-semantic-protection.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main(verbosity=2)
