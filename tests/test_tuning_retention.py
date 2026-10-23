"""Focused retention invariants: exact identity, one-to-one matching, authored curves."""
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

from calibration_workflow import retain_parameters, retain_project
from tuning_engine import process_project

Q = 705600000
DRIFT = Q // 5
TEST_TMP = Path(os.environ.get('BVWA_RETENTION_TMP', tempfile.gettempdir()))
TEST_TMP.mkdir(parents=True, exist_ok=True)


def note(onset=0, duration=Q, pitch=60, lyric='a', attributes=None):
    value = {'onset': onset, 'duration': duration, 'pitch': pitch, 'lyrics': lyric}
    if attributes is not None:
        value['attributes'] = copy.deepcopy(attributes)
    return value


def vocal(name, notes, parameters=None, *, pitch_delta=None):
    main_group = {
        'uuid': name + '-group',
        'notes': copy.deepcopy(notes),
        'parameters': copy.deepcopy(parameters) if parameters is not None else {
            'loudness': {'mode': 'linear', 'points': [0, 0., 2 * Q, 1.]},
        },
    }
    if pitch_delta is not None:
        main_group['parameters']['pitchDelta'] = copy.deepcopy(pitch_delta)
    return {
        'name': name,
        'mainRef': {
            'blickOffset': 0,
            'pitchOffset': 0,
            'isInstrumental': False,
            'database': {'name': 'Test Bank', 'language': 'mandarin'},
            'voice': {'preset': name, 'paramLoudness': .25, 'unknownVoiceKeep': name},
        },
        'mainGroup': main_group,
        'retakes': {'keep': name},
        'groups': [],
    }


def project(lead_notes=None, parameters=None, pitch_delta=None):
    lead_notes = copy.deepcopy(lead_notes) if lead_notes is not None else [
        note(0, Q, 60, '你', {'retakes': [3, 1], 'unknownNoteKeep': 'target'}),
        note(Q, Q, 64, '好', {'paramLoudness': .2}),
    ]
    return {
        'version': '1.0.0',
        'uuid': 'retention-fixture',
        'library': [{'name': 'song', 'secret': 9}],
        'renderConfig': {'keep': True},
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}],
        },
        'tracks': [
            vocal('Lead Vocal', lead_notes, parameters, pitch_delta=pitch_delta),
            vocal('Backing Vocal', [note(Q, Q, 55, 'x')]),
            {
                'name': 'Instrumental',
                'mainRef': {'isInstrumental': True},
                'mainGroup': {'notes': [], 'parameters': {}},
            },
        ],
    }


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TuningRetentionTests(unittest.TestCase):
    def test_tuning_engine_retained_route_defaults_to_exact_matching(self):
        source = project()
        target = project([
            note(DRIFT, Q, 60, '你'),
            note(Q + DRIFT, Q, 64, '好'),
        ])
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        options = {
            'expression_mode': 'retained', 'track_indices': [0],
            'retained_sources': [{'track_index': 0, 'project': 'source.svp',
                                 'source_track_index': 0, 'voice': 'lead'}],
            'expression_density': 'original',
        }
        with patch('calibration_session.project_data', return_value=source):
            with self.assertRaisesRegex(ValueError, '严格沿用参数.*未匹配'):
                process_project(target, options)
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)


    def test_exact_default_rejects_retimed_notes_and_keeps_both_inputs(self):
        source = project()
        target = project([
            note(DRIFT, Q, 60, '你'),
            note(Q + DRIFT, Q, 64, '好'),
        ])
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        with self.assertRaisesRegex(ValueError, '严格沿用参数.*未匹配'):
            retain_project(target, source)
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)

    def test_exact_and_legacy_reject_cropped_source_or_target_main_ref(self):
        for side in ('source', 'target'):
            source = project()
            target = project()
            selected = source if side == 'source' else target
            selected['tracks'][0]['mainRef']['blickAbsoluteBegin'] = 1
            selected['tracks'][0]['mainRef']['blickAbsoluteEnd'] = 2 * Q
            source_before, target_before = copy.deepcopy(source), copy.deepcopy(target)
            for policy in ('exact', 'legacy'):
                with self.subTest(side=side, policy=policy):
                    with self.assertRaisesRegex(ValueError, '裁剪 mainRef.*未裁剪 mainGroup'):
                        retain_project(target, source, match_policy=policy)
                    self.assertEqual(source, source_before)
                    self.assertEqual(target, target_before)


    def test_exact_rejects_duration_or_pitch_changes(self):
        source = project([note(0, Q, 60, '你')])
        source_before = copy.deepcopy(source)
        for changed in (note(0, Q + 1, 60, '你'), note(0, Q, 61, '你')):
            target = project([changed])
            target_before = copy.deepcopy(target)
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, '严格沿用参数.*未匹配'):
                    retain_project(target, source)
                self.assertEqual(target, target_before)
                self.assertEqual(source, source_before)


    def test_exact_rejects_extra_target_note_before_copying_absolute_curves(self):
        source = project([note(0, Q, 60, '你')], {
            'loudness': {'mode': 'linear', 'points': [0, 20., Q, 0.]},
        })
        target = project([
            note(0, Q, 60, '你'),
            note(Q, Q, 64, '新'),
        ])
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        with self.assertRaisesRegex(ValueError, '额外音符.*绝对作者曲线'):
            retain_project(target, source)
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)

    def test_duplicate_note_identity_fails_closed_without_many_to_one(self):
        source = project([note(0, Q, 60, '你')])
        target = project([note(0, Q, 60, '你'), note(0, Q, 60, '你')])
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        with self.assertRaisesRegex(ValueError, '匹配不唯一.*一对一'):
            retain_project(target, source, match_policy='exact')
        with self.assertRaisesRegex(ValueError, '额外音符'):
            retain_project(target, source, match_policy='legacy')
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)

    def test_author_authored_out_of_solver_range_curve_is_preserved_verbatim(self):
        source_curves = {
            'loudness': {
                'mode': 'cubic',
                'points': [0., 20., Q + .5, .5, 2 * Q + .25, -60.],
                'authoredTag': {'keep': True},
            },
            'tension': {
                'mode': 'linear',
                'points': [0, 1.75, Q, -.25, 2 * Q, -2.25],
            },
            'pitchDelta': {'mode': 'linear', 'points': [0, 7., Q, 9.]},
        }
        source = project(parameters=source_curves)
        target_pitch = {'mode': 'linear', 'points': [0, 1., Q, 2.], 'keep': 'target'}
        target = project(
            parameters={'loudness': {'mode': 'linear', 'points': [0, 0., Q, 1.]}},
            pitch_delta=target_pitch,
        )
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)
        output, report = retain_project(target, source)
        self.assertEqual(target, target_before)
        self.assertEqual(source, source_before)
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['loudness'],
                         source_before['tracks'][0]['mainGroup']['parameters']['loudness'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['tension'],
                         source_before['tracks'][0]['mainGroup']['parameters']['tension'])
        self.assertEqual(output['tracks'][0]['mainGroup']['parameters']['pitchDelta'], target_pitch)
        self.assertEqual(report['match_policy'], 'exact')
        self.assertEqual(report['curve_source'], 'author_authored_preserved')
        self.assertEqual(report['controls']['loudness']['outside_nominal_range_count'], 2)
        self.assertEqual(report['controls']['tension']['outside_nominal_range_count'], 2)
        self.assertEqual(report['author_authored_outside_nominal_range']['loudness'][0]['value'], 20.)
        self.assertTrue(report['nominal_ranges_are_solver_only'])
        for index in (1, 2):
            self.assertEqual(output['tracks'][index], target_before['tracks'][index])
        for old, new in zip(target_before['tracks'][0]['mainGroup']['notes'],
                            output['tracks'][0]['mainGroup']['notes']):
            self.assertEqual({key: new[key] for key in ('onset', 'duration', 'pitch', 'lyrics')},
                             {key: old[key] for key in ('onset', 'duration', 'pitch', 'lyrics')})
            for key in ('retakes', 'unknownNoteKeep'):
                if key in old['attributes']:
                    self.assertEqual(new['attributes'][key], old['attributes'][key])

    def test_legacy_file_wrapper_is_explicit_one_to_one_and_reports_every_delta(self):
        source = project()
        target = project([
            note(DRIFT, Q, 60, '你', {'retakes': [3, 1], 'unknownNoteKeep': 'target'}),
            note(Q + DRIFT, Q, 64, '好', {'paramLoudness': .2}),
        ])
        target_before, source_before = copy.deepcopy(target), copy.deepcopy(source)

        def read_project(path):
            return json.loads(Path(path).read_text(encoding='utf-8'))

        with tempfile.TemporaryDirectory(prefix='legacy-wrapper-', dir=TEST_TMP) as temp:
            target_path, source_path = Path(temp) / 'target.svp', Path(temp) / 'source.svp'
            target_path.write_text(json.dumps(target, ensure_ascii=False), encoding='utf-8')
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
            source_digest = digest(source_path)
            with patch('calibration_session.project_data', side_effect=read_project):
                report = retain_parameters(target_path, source_path)
            self.assertEqual(digest(source_path), source_digest)
            self.assertEqual(read_project(source_path), source_before)
            written = read_project(target_path)

        self.assertEqual(report['match_policy'], 'legacy')
        self.assertEqual(report['wrapper'], {'name': 'retain_parameters', 'match_policy': 'legacy'})
        self.assertTrue(report['matching']['one_to_one'])
        self.assertTrue(report['matching']['complete'])
        self.assertTrue(report['matching']['legacy_tolerance_used'])
        self.assertEqual(report['matching']['time_tolerance_seconds'], .12)
        self.assertEqual(report['matching']['pitch_tolerance_semitones'], 1.)
        self.assertEqual(len(report['matching']['matches']), 2)
        self.assertTrue(all(row['used_time_tolerance'] for row in report['matching']['matches']))
        self.assertGreater(report['matching']['matches'][0]['time_delta_seconds'], 0)
        self.assertEqual(len({row['target_index'] for row in report['matching']['matches']}), 2)
        self.assertEqual(written['tracks'][0]['mainGroup']['notes'][0]['onset'], DRIFT)
        self.assertEqual(written['tracks'][0]['mainGroup']['notes'][0]['attributes']['retakes'], [3, 1])
        self.assertEqual(written['tracks'][0]['mainGroup']['notes'][0]['attributes']['unknownNoteKeep'], 'target')
        self.assertEqual(target_before['tracks'][0]['mainGroup']['notes'][1]['attributes']['paramLoudness'], .2)


if __name__ == '__main__':
    unittest.main()
