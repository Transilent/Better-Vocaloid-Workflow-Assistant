"""Native USTX timing, curve preservation and candidate contracts."""

from bisect import bisect_left
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import agent_style
from tuning_timeline import ProjectNotes, freeze_plan
from tuning_workflow import create_session, export_verified_copy, read_session, run_session, verified_result
from ustx_tuning import Layout, encode, process_project, read


def project():
    def note(position, tone, lyric):
        return {'position': position, 'duration': 480, 'tone': tone, 'lyric': lyric,
                'pitch': {'data': [{'x': -40.0, 'y': 0.0}, {'x': 22.0, 'y': -4.5}], 'snap_first': True},
                'vibrato': {'length': 56, 'period': 175, 'depth': 21},
                'phoneme_overrides': [{'index': 0, 'phoneme': 'no', 'offset': 12}],
                'phoneme_expressions': [{'index': 0, 'abbr': 'vol', 'value': 82}]}

    return {
        'ustx_version': '0.10', 'resolution': 480,
        'tempos': [{'position': 0, 'bpm': 120}, {'position': 1200, 'bpm': 60}],
        'time_signatures': [{'bar_position': 0, 'beat_per_bar': 4, 'beat_unit': 4},
                            {'bar_position': 1, 'beat_per_bar': 3, 'beat_unit': 4}],
        'tracks': [
            {'track_name': 'Lead', 'singer': 'A', 'phonemizer': 'P', 'mute': False,
             'renderer_settings': {'renderer': 'CLASSIC', 'resampler': 'builtin'}},
            {'track_name': 'Harmony', 'singer': 'B', 'phonemizer': 'Q', 'mute': True},
            {'track_name': 'Backing', 'volume': -2},
        ],
        'voice_parts': [
            {'track_no': 0, 'position': 480, 'duration': 1920, 'notes': [note(120, 60, 'no'), note(960, 62, '012')],
             'curves': [
                 {'abbr': 'pitd', 'xs': [-12, 109, 987, 1379], 'ys': [9, -14, 47, 2], 'future_meta': 'off'},
                 {'abbr': 'dyn', 'xs': [0, 42, 1235], 'ys': [-6, 4, 13]},
                 {'abbr': 'velc', 'xs': [0, 480], 'ys': [100, 108]},
             ]},
            {'track_no': 0, 'position': 3100, 'duration': 960, 'notes': [note(0, 64, '春')]},
            {'track_no': 1, 'position': 480, 'duration': 960, 'notes': [note(120, 67, 'no')]},
        ],
        'wave_parts': [{'track_no': 2, 'position': 36, 'relative_path': 'media/backing.wav',
                        'file_duration_ms': 7000, 'skip_ms': 35.0, 'trim_ms': 55.0}],
        'custom': {'voice_color_alias': 'yes'},
    }


def write_source(directory, data):
    source = Path(directory) / 'source.ustx'
    source.write_bytes(encode(data, b'\xef\xbb\xbf').replace(b"lyric: '012'", b"lyric: 012"))
    return source


def sample(curve, x):
    xs, ys = curve['xs'], curve['ys']
    index = bisect_left(xs, x)
    if index < len(xs) and xs[index] == x:
        return ys[index]
    if not 0 < index < len(xs):
        return 0
    left, right = index - 1, index
    return round(ys[left] + (ys[right] - ys[left]) * (x - xs[left]) / (xs[right] - xs[left]))


class UstxTuningTest(unittest.TestCase):
    def test_native_session_only_changes_selected_curve_samples_and_wave_reference(self):
        original = project()
        with tempfile.TemporaryDirectory(prefix='ustx-session-', dir=APP) as root:
            source = write_source(root, original)
            self.assertEqual(read(source.read_bytes()), original)
            before = sha256(source.read_bytes()).hexdigest()
            notes = ProjectNotes.load(source)
            lead, harmony = notes.editable
            self.assertEqual([row['noteCount'] for row in notes.tracks.values()], [3, 1])
            first, second, third = notes.visible([lead], 0, 100)
            self.assertEqual((first['start'], first['end'], first['pitch'], first['lyric']), (.625, 1.125, 60, 'no'))
            self.assertEqual(second['start'], 1.75)
            self.assertEqual(second['lyric'], '012')
            self.assertEqual(notes.matches('春')[0]['id'], third['id'])
            self.assertEqual({r['trackId'] for r in notes.matches('no')}, {lead, harmony})
            region = notes.region(lead, .7, 1.0, {'preset': 'bold', 'pitch': True})
            opts = {'track_indices': [0], 'style_regions': [region['recipe']]}
            _, plan = freeze_plan(notes, [region], opts, None)
            session = create_session(source, Path(root) / 'session')
            self.assertEqual(read_session(session)['input_file'], 'inputs/source.ustx')
            result = run_session(session, json.loads(plan)['options'])
            self.assertTrue(result['receipt_verified'])
            self.assertEqual(result['status'], 'partial')  # SVP-only style channels are reported, not invented.
            self.assertTrue(result['report']['pitch_changed'])
            self.assertEqual([(r['track_index'], r['group_placement'], r['parameter'])
                              for r in result['report']['curve_changes']], [(0, 'part:0', 'dyn'), (0, 'part:0', 'pitd')])
            self.assertEqual(verified_result(session)['output_sha256'], result['output_sha256'])
            with self.assertRaisesRegex(ValueError, '格式相同'):
                export_verified_copy(result, Path(root) / 'wrong.svp')
            saved = Path(export_verified_copy(result, Path(root) / 'candidate.ustx'))
            self.assertTrue(ProjectNotes.load(saved).project['candidate_origin']['verified'])
            self.assertEqual(sha256(saved.read_bytes()).hexdigest(), result['output_sha256'])
            self.assertEqual(sha256(source.read_bytes()).hexdigest(), before)
            actual = read(saved.read_bytes())
            self.assertTrue(saved.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(actual['wave_parts'][0]['relative_path'], str(Path(root) / 'media/backing.wav'))
            actual['wave_parts'][0]['relative_path'] = 'media/backing.wav'
            for i in (0, 1):
                old = original['voice_parts'][0]['curves'][i]
                new = actual['voice_parts'][0]['curves'][i]
                self.assertTrue(any(sample(old, x) != sample(new, x) for x in range(120, 601)))
                self.assertEqual([sample(old, x) for x in (*range(-30, 120), *range(601, 1420))],
                                 [sample(new, x) for x in (*range(-30, 120), *range(601, 1420))])
                new.update(xs=old['xs'], ys=old['ys'])
            self.assertEqual(actual, original)
            self.assertEqual(len(ProjectNotes.load(saved).notes), len(notes.notes))
            self.assertTrue(run_session(session, opts)['cached'])

    def test_agent_note_identity_routes_to_second_part(self):
        with tempfile.TemporaryDirectory(prefix='ustx-agent-', dir=APP) as root:
            source = write_source(root, project())
            context = agent_style.read_context(source)
            note = next(n for n in context['notes'] if n['lyric'] == '春')
            plan = agent_style.plan_style(source, {
                'track_indices': [0], 'brief': '保留同轨其他片段',
                'regions': [{'start_note_id': note['note_id'], 'end_note_id': note['note_id'],
                             'preset': 'gentle', 'strength': .8, 'style_pitch': False,
                             'rationale': 'fixture', 'evidence_refs': []}],
            })
            result = agent_style.render_style(source, plan, Path(root) / 'output')
            self.assertTrue(result['receipt_verified'])
            self.assertTrue(result['candidate_path'].endswith('.ustx'))
            self.assertEqual(result['report']['curve_changes'],
                             [{'track_index': 0, 'group_placement': 'part:1', 'parameter': 'dyn'}])
            self.assertFalse(result['media_relocation']['audio_copied'])

    def test_legacy_constant_tempo_can_preview_without_claiming_write_support(self):
        data = project()
        data.update(ustx_version='0.5', bpm=164, beat_per_bar=4, beat_unit=4)
        data.pop('tempos')
        with tempfile.TemporaryDirectory(prefix='ustx-legacy-', dir=APP) as root:
            path = write_source(root, data)
            notes = ProjectNotes.load(path)
            self.assertEqual(notes.project['format_version'], '0.5')
            self.assertFalse(notes.editable)
            lead = next(iter(notes.tracks))
            self.assertIn('OpenUtau 另存', notes.tracks[lead]['reason'])
            self.assertGreater(notes.tracks[lead]['noteCount'], 0)
        self.assertEqual(Layout(data).score['time']['tempo'], [{'position': 0, 'bpm': 164}])
        with self.assertRaisesRegex(ValueError, 'OpenUtau 另存'):
            process_project(data, {'track_indices': [0], 'performance_style': 'bold'})

    def test_unsupported_versions_graph_overlap_and_custom_expression_are_preserved(self):
        base = project()
        opts = {'track_indices': [0], 'performance_style': 'bold', 'style_pitch': False}
        for edit, why in (
            (lambda d: d.update(ustx_version='0.11'), '尚未验证'),
            (lambda d: d.update(expression_graphs=[{'id': 'test', 'nodes': []}]), '表达式图'),
            (lambda d: d['voice_parts'][1].update(position=540), '重叠'),
        ):
            data = deepcopy(base)
            edit(data)
            self.assertFalse(Layout(data).tracks[0].writable)
            with self.assertRaisesRegex(ValueError, why):
                process_project(data, opts)
        base['expressions'] = {'dyn': {'abbr': 'dyn', 'type': 'Numerical'}}
        after, receipt = process_project(base, opts)
        self.assertEqual(after, base)
        self.assertEqual(receipt['curve_changes'], [])
        self.assertIn('ustx_custom_expression_preserved', json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, '保留原表现'):
            process_project(base, {'track_indices': [0], 'expression_mode': 'reference', 'reference_dynamics': True})
        for raw in (b'ustx_version: 0.10\ntracks: []\ntracks: []',
                    b'ustx_version: 0.10\ntracks: &x [*x]'):
            with self.assertRaises(ValueError):
                read(raw)


if __name__ == '__main__':
    unittest.main()
