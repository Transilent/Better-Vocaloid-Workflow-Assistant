"""Prove manual priority, native curve preservation and bounded score ownership."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

from common import native_path
from tuning_pitch_layer import BOUNDS, PROTECTED, normalize_strokes
from tuning_timeline import ProjectNotes, freeze_plan, normalize_timeline_draft
import tuning_workflow as workflow
from ustx_tuning import _native_samples, encode
from vocal_expression import SV_QUARTER as Q


STYLE = {'preset': 'gentle', 'strength': .85, 'pitch': True}


def example(format, *, authored=True):
    if format == 'ustx':
        notes = [{'position': 120, 'duration': 720, 'tone': 60, 'lyric': '春'},
                 {'position': 960, 'duration': 480, 'tone': 64, 'lyric': '山'}]
        curves = [
            {'abbr': 'pitd', 'xs': [10, 130, 170, 740, 1020], 'ys': [0, 27, -8, 14, 0], 'meta': 'keep'},
            {'abbr': 'velc', 'xs': [0, 360], 'ys': [100, 107]},
        ] if authored else []
        return {'ustx_version': '0.10', 'resolution': 480, 'tempos': [{'position': 0, 'bpm': 120},
                 {'position': 240, 'bpm': 96}], 'time_signatures': [{'bar_position': 0, 'beat_per_bar': 4, 'beat_unit': 4}],
                'tracks': [{'track_name': 'Lead'}, {'track_name': 'Harmony'}], 'wave_parts': [],
                'voice_parts': [
                    {'track_no': 0, 'position': 0, 'duration': 1800, 'notes': notes, 'curves': curves},
                    {'track_no': 1, 'position': 0, 'duration': 1800, 'notes': deepcopy(notes)}]}
    tracks = []
    for name in ('Lead', 'Harmony'):
        tracks.append({'name': name, 'mainRef': {'blickOffset': Q // 8},
                       'mainGroup': {'notes': [
                           {'onset': Q // 4, 'duration': 3 * Q // 2, 'pitch': 60, 'lyrics': '春'},
                           {'onset': 2 * Q, 'duration': Q, 'pitch': 64, 'lyrics': '山'}],
                           'parameters': {'pitchDelta': {'mode': 'linear', 'points': [
                               -0.25, 2, Q / 4 + .5, 24, Q // 2 + .25, -8, Q * 3, 0], 'meta': 'keep'}}
                           if authored else {}}})
    return {'time': {'tempo': [{'position': 0, 'bpm': 120}, {'position': Q // 2, 'bpm': 96}]}, 'tracks': tracks}


def sample(data, format, x, channel=None):
    if format == 'ustx':
        curve = next((c for c in data['voice_parts'][0].get('curves', []) if c['abbr'] == (channel or 'pitd')), None)
        return _native_samples(x, curve['xs'], curve['ys']) if curve else np.zeros(len(x), dtype=int)
    curve = data['tracks'][0]['mainGroup']['parameters'].get(channel or 'pitchDelta')
    return np.interp(x, curve['points'][::2], curve['points'][1::2]) if curve else np.zeros(len(x))


class ManualPitchTest(unittest.TestCase):
    def setUp(self):
        parent = APP / 'work'
        parent.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(dir=native_path(parent), prefix='pitch-')
        self.addCleanup(scratch.cleanup)
        self.root = parent / Path(scratch.name).name

    def load(self, format, *, authored=True, data=None):
        source = self.root / ('score.' + format)
        native = example(format, authored=authored) if data is None else data
        source.write_bytes(encode(native, b'') if format == 'ustx'
                           else json.dumps(native, ensure_ascii=False).encode())
        project = ProjectNotes.load(source)
        a, b = project.visible([project.editable[0]], 0., 100.)
        return source, project, a, b

    def draw(self, p, note, lo=.032, hi=.118):
        start = note['start']
        return p.pitch_layer.gesture([], p.note_patches, note['id'], 'draw',
                                    [[start+lo, 0], [start + (lo+hi)/2, 70], [start+hi, -30]])

    def test_style_replacement_does_not_change_manual_samples_and_erase_exposes_style(self):
        for format, channel in [('ustx', 'dyn'), ('svp', 'loudness')]:
            with self.subTest(format=format):
                source, p, note, _ = self.load(format)
                snap = workflow.read_source(source)
                raw = source.read_bytes()
                strokes = self.draw(p, note)
                active = p.with_edits([], strokes)
                span, = p.pitch_layer.resolve(strokes, {})
                a, b = span.points[0][0], span.points[-1][0]
                if format == 'ustx':
                    grid = np.arange(-12, 1810, dtype=np.int64)
                else:
                    grid = np.unique(np.concatenate((np.linspace(-2.25, 3.2*Q, 2401),
                                                     [x for x, _ in span.points])))
                interior = (grid >= a) & (grid <= b)
                exterior = (grid <= span.left) | (grid >= span.right)
                target = sample(snap.data, format, grid[interior]) + np.interp(
                    grid[interior], [x for x, _ in span.points], [y for _, y in span.points])
                if format == 'ustx':
                    target = np.rint(target)
                baseline = []
                values = []
                for style in (STYLE, dict(STYLE, preset='bold', strength=.58)):
                    region = active.region(note['trackId'], note['start']+.01, note['end']-.01, style)
                    options = {'track_indices': [0], 'style_regions': [region['recipe']]}
                    styled, _ = workflow.apply_project(snap.data, options, format,
                                                      cancel=lambda: None, progress=lambda _: None)
                    _, plan = freeze_plan(active, [region], dict(options, pitch_strokes=strokes), None)
                    layers = {}
                    layered, report = workflow.apply_snapshot(snap, json.loads(plan)['options'],
                        cancel=lambda: None, progress=lambda _: None,
                        observe_style=lambda raw: layers.update(p.pitch_layer.curve_sources(raw)))
                    assert report['hand_pitch']['spans'] == 1 and report['notes_and_timing_unchanged']
                    plot = p.pitch_layer.plot([note], note['start'], note['end'], layers)
                    anchor = p.pitch_layer.notes[note['id']]
                    for name, native in [('original', snap.data), ('style', styled)]:
                        curve, = plot[name]
                        assert curve['noteId'] == note['id'] and 2 < len(curve['points']) <= 128
                        seconds, cents = zip(*curve['points'])
                        x = np.array([(p.pitch_layer.clock.to_position(t) - anchor.offset) / anchor.step
                                      for t in seconds])
                        np.testing.assert_allclose(cents, sample(native, format, x), rtol=0, atol=1e-6)
                    layer_values = sample(layered, format, grid)
                    style_values = sample(styled, format, grid)
                    np.testing.assert_allclose(layer_values[interior], target, rtol=0, atol=1e-10)
                    np.testing.assert_allclose(layer_values[exterior], style_values[exterior], rtol=0, atol=1e-10)
                    np.testing.assert_allclose(sample(layered, format, grid, channel),
                                               sample(styled, format, grid, channel), rtol=0, atol=1e-10)
                    baseline.append(style_values)
                    values.append(layer_values[interior])
                    if format == 'ustx':
                        clean = deepcopy(layered)
                        clean['voice_parts'][0]['curves'] = styled['voice_parts'][0]['curves']
                    else:
                        clean = deepcopy(layered)
                        clean['tracks'][0]['mainGroup']['parameters']['pitchDelta'] = (
                            styled['tracks'][0]['mainGroup']['parameters']['pitchDelta'])
                        self.assertIn(Q / 4 + .5, layered['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points'][::2])
                    self.assertEqual(clean, styled)
                self.assertGreater(float(np.max(np.abs(baseline[0][interior] - baseline[1][interior]))), 1)
                np.testing.assert_allclose(values[0], values[1], rtol=0, atol=1e-10)
                self.assertEqual(source.read_bytes(), raw)

    def test_replacement_partial_erase_move_and_draft_recovery(self):
        source, p, a, b = self.load('ustx', authored=False)
        old = self.draw(p, a, .04, .32)
        layer = p.pitch_layer
        replacement = layer.gesture(old, {}, a['id'], 'draw', [[a['start']+.25, -65], [a['start']+.11, -40]])
        self.assertEqual(len(replacement), 3)
        self.assertEqual([y for _, y in replacement[1]['points']], [-40, -65])
        erased = layer.gesture(replacement, {}, a['id'], 'erase', [[a['start']+.14, 0], [a['start']+.20, 0]])
        self.assertEqual(len(erased), 4)
        erased = layer.gesture(erased, {}, a['id'], 'erase', [[a['start']+.12, 0]])
        self.assertEqual(len(erased), 3)
        self.assertEqual(replacement[0]['points'], erased[0]['points'])

        edit = {'note_id': a['id'], 'onset': 200, 'pitch': 63}
        before = p.with_edits([], erased)
        edited = p.with_edits([edit], erased)
        self.assertEqual(before.pitch_strokes, edited.pitch_strokes)
        points = layer.display(edited.pitch_strokes, edited.note_patches, {a['id']})[0]['points']
        prior = layer.display(before.pitch_strokes, before.note_patches, {a['id']})[0]['points']
        self.assertGreater(points[0][0], prior[0][0])
        self.assertAlmostEqual(points[0][1], prior[0][1] + 3)
        with self.assertRaisesRegex(ValueError, BOUNDS):
            p.with_edits([dict(edit, duration=140)], erased)
        options = {'note_edits': [edit], 'pitch_strokes': erased}
        payload = normalize_timeline_draft(p.sha256, {'schema_version': 2, 'input_sha256': p.sha256, 'options': options})
        self.assertEqual(ProjectNotes.load(source).with_edits(payload['options']['note_edits'],
                                                              payload['options']['pitch_strokes']).pitch_strokes, edited.pitch_strokes)
        session = workflow.create_session(source, self.root / 'session')
        receipt = workflow.run_session(session, options)
        self.assertEqual(receipt['report']['hand_pitch']['spans'], 3)
        self.assertTrue(receipt['report']['changed'])
        self.assertIn('hand_pitch', receipt['report']['stages'])
        self.assertEqual(receipt['report']['track_indices'], [0])
        self.assertTrue(workflow.verified_result(session)['receipt_verified'])
        output = workflow.read_project(receipt['output'])
        self.assertEqual(output['voice_parts'][0]['notes'][0]['tone'], 63)
        self.assertEqual({c['abbr'] for c in output['voice_parts'][0]['curves']}, {'pitd'})
        self.assertEqual(edited.notes[b['id']]['lyric'], '山')

    def test_zero_is_an_explicit_override_and_unsupported_curves_are_blocked(self):
        source, p, a, _ = self.load('svp')
        start = a['start']
        zeros = p.pitch_layer.gesture([], {}, a['id'], 'draw', [[start+.03, 0], [start+.1, 0]])
        self.assertEqual(len(zeros), 1)
        with self.assertRaisesRegex(ValueError, '重叠'):
            normalize_strokes(zeros * 2)
        data = example('svp')
        data['tracks'][0]['mainGroup']['parameters']['pitchDelta']['mode'] = 'cubic'
        _, blocked, first, _ = self.load('svp', data=data)
        with self.assertRaisesRegex(ValueError, PROTECTED):
            self.draw(blocked, first)


if __name__ == '__main__':
    unittest.main()
