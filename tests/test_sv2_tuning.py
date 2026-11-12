"""Style round trips over independently placed SV2 note groups."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import agent_style
from tuning_engine import process_project
from tuning_timeline import ProjectNotes, freeze_plan
from tuning_workflow import create_session, export_verified_copy, run_session


Q = 705600000


def ref(target, **values):
    return {'groupID': target, 'blickOffset': 0, 'pitchOffset': 0,
            'blickAbsoluteBegin': 0, 'blickAbsoluteEnd': -1, 'mute': False, **values}


def group(name, onset):
    return {'uuid': name, 'notes': [
        {'onset': onset, 'duration': 2 * Q, 'pitch': 60, 'lyrics': '山',
         'retakes': {'active': 1}, 'attributes': {'dF0Vbr': 0}},
        {'onset': onset + 2 * Q, 'duration': Q, 'pitch': 64, 'lyrics': '海'},
    ], 'parameters': {}}


def project():
    a = group('a', 20 * Q)
    a.update(pitchControls=[{'position': 20 * Q, 'custom': True}],
             vocalModes={'original': [1, 2, 3]})
    a['parameters'].update(
        loudness={'mode': 'cosine', 'points': [19 * Q, 0., 22 * Q, 1.]},
        tension={'mode': 'linear', 'points': [19 * Q, .1, 24 * Q, .1]},
        mouthOpening={'mode': 'cubic', 'points': [20 * Q, .2]},
    )
    empty = {'uuid': 'empty', 'notes': [], 'parameters': {}}
    return {
        'version': 196, 'time': {'tempo': [{'position': 0, 'bpm': 120},
                                          {'position': 12 * Q, 'bpm': 60}]},
        'library': [a, group('b', 0), group('c', Q)],
        'tracks': [
            {'name': 'Lead Vocal', 'mainGroup': empty, 'mainRef': ref('empty'), 'groups': [
                ref('a', blickOffset=-12 * Q, blickAbsoluteBegin=8 * Q, blickAbsoluteEnd=11 * Q,
                    pitchOffset=2, takes={'seed': 4231}, database={'name': 'fixture'}),
                ref('b', blickOffset=13 * Q),
            ]},
            {'name': 'Harmony', 'mainRef': ref('c', blickOffset=6 * Q), 'groups': []},
        ],
        'projectMixer': {'untouched': ['test', 3]},
    }


def write_project(directory, data, *, native_envelope=False):
    path = Path(directory) / 'source.svp'
    text = json.dumps(data, ensure_ascii=False)
    path.write_bytes(text.encode('utf-8-sig') + b'\0' if native_envelope else text.encode('utf-8'))
    return path


def note_layout(project):
    return [[(n['pitch'], n['lyric'], n['start'], n['end'])
             for n in project.visible([key], -1, 1000)] for key in project.tracks]


class SV2TuningTest(unittest.TestCase):
    def test_real_group_coordinates_survive_timeline_session_and_export(self):
        source = project()
        with tempfile.TemporaryDirectory(prefix='sv2-tuning-', dir=APP) as temp:
            path = write_project(temp, source, native_envelope=True)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            notes = ProjectNotes.load(path)
            lead, harmony = notes.editable
            self.assertEqual(notes.project['format_version'], 196)
            self.assertEqual([row['noteCount'] for row in notes.tracks.values()], [4, 2])
            first, second, third, _ = notes.visible([lead], 0, 20)
            self.assertEqual((first['start'], first['end'], first['pitch']), (4., 5., 62))
            self.assertEqual((second['start'], third['start']), (5., 7.))
            self.assertEqual(notes.visible([harmony], 0, 20)[0]['start'], 3.5)
            self.assertEqual(len(notes.matches('山海')), 3)
            region = notes.region(lead, 4.2, 4.8, {'preset': 'gentle', 'pitch': True})
            opts = {'track_indices': [0], 'style_regions': [region['recipe']]}
            _, frozen = freeze_plan(notes, [region], opts, None)
            session = create_session(path, Path(temp) / 'session')
            candidate = run_session(session, json.loads(frozen)['options'])
            self.assertTrue(candidate['receipt_verified'])
            self.assertEqual(candidate['status'], 'partial')
            report = candidate['report']
            self.assertIn('native_pitch_controls_preserved', json.dumps(report))
            self.assertTrue(all(row['track_index'] == 0 and row['group_placement'] == 'group:0'
                                for row in report['curve_changes']))
            self.assertEqual({row['parameter'] for row in report['curve_changes']},
                             {'tension', 'breathiness', 'voicing'})
            exported = export_verified_copy(candidate, Path(temp) / 'edited.svp')
            self.assertTrue(ProjectNotes.load(exported).project['candidate_origin']['verified'])
            edited = Path(exported)
            payload = edited.read_bytes()
            self.assertTrue(payload.startswith(b'\xef\xbb\xbf') and payload.endswith(b'\0'))
            self.assertEqual(hashlib.sha256(payload).hexdigest(), candidate['output_sha256'])
            actual = json.loads(payload.decode('utf-8-sig').rstrip('\0'))
            points = actual['library'][0]['parameters']['tension']['points']
            np.testing.assert_allclose(np.interp([20 * Q - Q / 4, 22 * Q + Q / 4],
                                                points[::2], points[1::2]), [.1, .1], atol=1e-12)
            self.assertEqual(note_layout(ProjectNotes.load(edited)), note_layout(notes))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
            for row in report['curve_changes']:
                name = row['parameter']
                if name in source['library'][0]['parameters']:
                    self.assertNotEqual(actual['library'][0]['parameters'][name],
                                        source['library'][0]['parameters'][name])
                    actual['library'][0]['parameters'][name] = source['library'][0]['parameters'][name]
                else:
                    self.assertTrue(actual['library'][0]['parameters'].pop(name)['points'])
            self.assertEqual(actual, source)
            self.assertTrue(run_session(session, opts)['cached'])

    def test_agent_and_style_engine_address_the_same_placement(self):
        source = project()
        with tempfile.TemporaryDirectory(prefix='sv2-agent-', dir=APP) as temp:
            path = write_project(temp, source)
            project_notes = ProjectNotes.load(path)
            lead = project_notes.editable[0]
            note = project_notes.visible([lead], 7., 9.)[0]
            plan = agent_style.plan_style(path, {
                'track_indices': [0], 'brief': '单独处理第二组的第一个音符',
                'regions': [{'start_note_id': note['id'], 'end_note_id': note['id'],
                             'preset': 'bold', 'strength': .6, 'style_pitch': True,
                             'rationale': 'fixture', 'evidence_refs': []}],
            })
            result = agent_style.render_style(path, plan, Path(temp) / 'render')
            self.assertTrue(result['receipt_verified'])
            output = json.loads(Path(result['candidate_path']).read_bytes())
            self.assertEqual(output['tracks'], source['tracks'])
            self.assertEqual(output['library'][::2], source['library'][::2])
            self.assertTrue(output['library'][1]['parameters']['pitchDelta']['points'])
            self.assertTrue(all(row['group_placement'] == 'group:1' and row['track_index'] == 0
                                for row in result['report']['curve_changes']))

    def test_shared_and_cropped_groups_leave_independent_groups_editable(self):
        source = project()
        shared = deepcopy(source)
        # A muted copy still refers to the same native source.
        shared['tracks'][1]['groups'] = [ref('a', mute=True)]
        cropped = deepcopy(source)
        cropped['tracks'][0]['groups'][0]['blickAbsoluteBegin'] = 9 * Q
        for data, why in ((shared, '多个片段'), (cropped, '裁剪')):
            with self.subTest(why=why), tempfile.TemporaryDirectory(dir=APP) as temp:
                notes = ProjectNotes.load(write_project(temp, data))
                lead = notes.editable[0]
                visible = notes.visible([lead], 0, 20)
                self.assertEqual(visible[0]['start'], 4.5 if data is cropped else 4.)
                self.assertIn(why, visible[0]['readOnlyReason'])
                self.assertIsNone(notes.region(lead, 4.5, 5., {'preset': 'gentle'}))
                self.assertIsNotNone(notes.region(lead, 7.2, 7.7, {'preset': 'gentle'}))
                result, report = process_project(data, {'track_indices': [0], 'performance_style': 'gentle'})
                self.assertEqual(result['library'][::2], data['library'][::2])
                self.assertEqual(result['tracks'], data['tracks'])
                self.assertEqual(report['status'], 'partial')
                self.assertIn(why, report['style']['protected_groups'][0]['message'])
                self.assertTrue(report['curve_changes'])
                self.assertTrue(all(row['group_placement']=='group:1' for row in report['curve_changes']))

        # When there are no independent groups, the track stays read-only.
        shared['tracks'][0]['groups'].pop()
        with self.assertRaisesRegex(ValueError, '多个片段'):
            process_project(shared, {'track_indices': [0], 'performance_style': 'gentle'})

    def test_ambiguous_or_unverified_svp_remains_read_only(self):
        source = project()
        overlap = deepcopy(source)
        overlap['tracks'][0]['groups'][1]['blickOffset'] = 9 * Q
        missing = deepcopy(source)
        missing['tracks'][0]['groups'][0]['groupID'] = 'unresolved'
        future = {**source, 'version': 999}
        for data, why in ((overlap, '重叠'), (missing, '引用缺失'), (future, '尚未验证')):
            with self.subTest(why=why), tempfile.TemporaryDirectory(dir=APP) as temp:
                notes = ProjectNotes.load(write_project(temp, data))
                lead = next(iter(notes.tracks))
                self.assertFalse(notes.tracks[lead]['editable'])
                self.assertIn(why, notes.tracks[lead]['reason'])
                self.assertGreater(notes.tracks[lead]['noteCount'], 0)
                with self.assertRaisesRegex(ValueError, why):
                    process_project(data, {'track_indices': [0], 'performance_style': 'gentle'})
                with self.assertRaises(ValueError):
                    notes.region(lead, 4, 5, {'preset': 'gentle'})

    def test_group_sources_gate_replacement_modes(self):
        with self.assertRaisesRegex(ValueError, '只支持.*保留原表现'):
            process_project(project(), {'track_indices': [1], 'expression_mode': 'reference',
                                        'reference_dynamics': True})


if __name__ == '__main__':
    unittest.main()
