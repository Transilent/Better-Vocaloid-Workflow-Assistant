"""Real SVP note identities, independent tracks, legacy migration and candidate safety."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import agent_style
from style_drafts import DraftConflict, DraftStore
from style_presets import region_settings
from tuning_engine import digest, settings
from tuning_timeline import ProjectNotes, TimelineDraftStore, freeze_plan, normalize_timeline_draft
import tuning_workflow

Q = 705600000
STYLE = {'preset': 'gentle', 'strength': .7, 'pitch': False}


def score():
    def track(name, offset, pitches):
        notes = [
            {'onset': i * Q, 'duration': Q, 'pitch': pitch, 'lyrics': lyric}
            for i, (pitch, lyric) in enumerate(zip(pitches, ('你', '好', '你', '好')))
        ]
        return {'name': name, 'mainRef': {'isInstrumental': False, 'blickOffset': offset},
                'mainGroup': {'notes': notes, 'parameters': {'loudness': {'mode': 'linear', 'points': [0, 0., 4 * Q, 0.]},
                              'pitchDelta': {'mode': 'linear', 'points': [0, 1., 4 * Q, 1.]}}}}

    return {'time': {'tempo': [{'position': 0, 'bpm': 120}, {'position': 2 * Q, 'bpm': 90}]},
            'tracks': [track('主唱', 0, [60, 62, 64, 65]), track('和声', Q // 4, [67, 69, 70, 72]),
                       track('保留轨', Q // 2, [48, 49, 50, 51])]}


class TimelineTest(unittest.TestCase):
    def setUp(self):
        (APP / 'work').mkdir(exist_ok=True)
        work = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='tuning-timeline-')
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.raw = score()
        self.source = self.root / '歌词与三轨.svp'
        self.source.write_text(json.dumps(self.raw, ensure_ascii=False), encoding='utf-8')
        self.project = ProjectNotes.load(self.source)
        self.lead, self.backing, self.third = self.project.editable

    def test_phrase_identity_tempo_offset_and_overlap(self):
        match = self.project.matches('你 好')
        self.assertEqual(len(match), 6)
        self.assertEqual({row['trackId'] for row in match}, set(self.project.editable))
        context = agent_style.read_context(self.source)
        self.assertEqual(set(self.project.notes), {row['note_id'] for row in context['notes']})
        self.assertEqual(match[0]['start'], 0)
        self.assertEqual(next(row for row in match if row['trackId'] == self.backing)['start'], .125)
        self.assertAlmostEqual(next(row for row in match if row['trackId'] == self.lead and row['start'] > 0)['end'], 7 / 3)

        a = self.project.region(self.lead, .20, .45, STYLE)
        b = self.project.region(self.backing, .20, .45, {'preset': 'sad', 'strength': .8, 'pitch': False})
        c = self.project.region(self.lead, .5, .7, STYLE)
        self.assertEqual(a['recipe']['end'], c['recipe']['start'])
        self.assertEqual(b['recipe']['start'], .125)
        regions = [a, b, c]
        options = settings({'track_indices': [0, 1], 'style_regions': self.project.verify_regions(regions),
                            'expression_density': 'original'})
        fingerprint, frozen = freeze_plan(self.project, regions, options, None)
        self.assertEqual(digest(json.loads(frozen)), fingerprint)
        with self.assertRaisesRegex(ValueError, '同一演唱轨'):
            self.project.verify_regions([a, self.project.region(self.lead, .4, .6, STYLE)])
        forged = copy.deepcopy(regions)
        forged[0]['start_note_id'] = b['start_note_id']
        with self.assertRaisesRegex(ValueError, '轨道身份'):
            freeze_plan(self.project, forged, options, None)

        session = tuning_workflow.create_session(self.source, self.root / 'session')
        original = self.source.read_bytes()
        result = tuning_workflow.run_session(session, options)
        self.assertTrue(result['receipt_verified'])
        reread = tuning_workflow.verified_result(session)
        self.assertEqual(reread['output_sha256'], result['output_sha256'])
        output = json.loads(Path(result['output']).read_bytes())
        self.assertEqual(output['tracks'][2], self.raw['tracks'][2])
        for actual, expected in zip(output['tracks'], self.raw['tracks']):
            self.assertEqual(actual['mainGroup']['notes'], expected['mainGroup']['notes'])
            self.assertEqual(actual['mainGroup']['parameters']['pitchDelta'], expected['mainGroup']['parameters']['pitchDelta'])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual([row['track_indices'] for row in result['report']['style']['regions']], [[0], [0], [1]])
        self.assertTrue(tuning_workflow.run_session(session, options)['cached'])
        self.source.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, '原始工程'):
            tuning_workflow.verified_result(session)
        with self.assertRaisesRegex(ValueError, '交接'):
            ProjectNotes.load(self.source, expected_sha256=self.project.sha256)

    def test_later_region_paints_after_snapping_and_keeps_track_fragments(self):
        old = self.project.region(self.lead, 0., self.project.maximum, STYLE)
        other = self.project.region(self.backing, .2, .9,
                                    {'preset': 'sad', 'strength': .8, 'pitch': False})
        incoming = self.project.region(self.lead, 1., 1.5,
                                       {'preset': 'bold', 'strength': .31, 'pitch': False})
        before = copy.deepcopy([old, other])

        rows = self.project.overlay_regions([old, other], [incoming])

        self.assertEqual([old, other], before)
        self.assertEqual(self.project.overlay_regions([old, other], [incoming]), rows)
        lead = [row for row in rows if row['track_id'] == self.lead]
        self.assertEqual(len(lead), 3)
        self.assertAlmostEqual(lead[0]['recipe']['start'], 0.)
        self.assertAlmostEqual(lead[0]['recipe']['end'], 1.)
        self.assertAlmostEqual(lead[1]['recipe']['start'], 1.)
        self.assertAlmostEqual(lead[1]['recipe']['end'], 5 / 3)
        self.assertAlmostEqual(lead[2]['recipe']['start'], 5 / 3)
        self.assertAlmostEqual(lead[2]['recipe']['end'], 7 / 3)
        self.assertEqual([row['recipe']['preset'] for row in lead], ['gentle', 'bold', 'gentle'])
        self.assertEqual([row['recipe']['strength'] for row in lead], [.7, .31, .7])
        self.assertEqual(lead[1]['id'], incoming['id'])
        self.assertEqual(len({row['id'] for row in rows}), len(rows))
        self.assertTrue(all(len(row['id']) == 32 for row in rows))
        self.assertEqual(self.project.verify_regions(rows),
                         region_settings([row['recipe'] for row in rows]))
        self.assertIn(other, rows)
        self.assertEqual([row for row in rows if row['track_id'] == self.backing], [other])

        with self.assertRaisesRegex(ValueError, '同一演唱轨'):
            region_settings([old['recipe'], incoming['recipe']])

    def test_replacement_identity_wins_and_draft_matches_frozen_plan(self):
        first = self.project.region(self.lead, 0., .9, STYLE)
        second = self.project.region(self.lead, 1.01, 2.2,
                                     {'preset': 'sad', 'strength': .8, 'pitch': False})
        moved = self.project.region(self.lead, 1., 1.5, STYLE, identity=first['id'])
        rows = self.project.overlay_regions([second], [moved])
        self.assertEqual(rows[0]['id'], first['id'])
        self.assertEqual(rows[0]['recipe']['preset'], 'gentle')
        self.assertEqual(rows[1]['recipe']['preset'], 'sad')
        self.assertAlmostEqual(rows[1]['recipe']['start'], 5 / 3)

        updated = self.project.region(
            self.lead, 1., 1.5,
            {'preset': 'sad', 'strength': .35, 'pitch': False,
             'technique': 'chinese_opera', 'technique_strength': .4, 'technique_pitch': False},
            identity=first['id'])
        rows = self.project.overlay_regions([rows[1]], [updated])
        self.assertEqual(rows[0]['id'], first['id'])
        self.assertEqual(rows[0]['recipe']['preset'], 'sad')
        self.assertEqual(rows[0]['recipe']['strength'], .35)
        self.assertEqual(rows[0]['recipe']['technique'], 'chinese_opera')
        self.assertEqual(rows[1]['recipe']['preset'], 'sad')
        self.assertEqual(rows[1]['recipe']['strength'], .8)

        draft = normalize_timeline_draft(self.project.sha256, {
            'schema_version': 2, 'input_sha256': self.project.sha256, 'regions': rows,
            'options': settings({}), 'confirmed': False,
        })
        self.assertEqual(draft['regions'], rows)
        options = settings({'track_indices': [0], 'expression_density': 'original',
                            'style_regions': self.project.verify_regions(rows)})
        fingerprint, frozen = freeze_plan(self.project, rows, options, None)
        payload = json.loads(frozen)
        self.assertEqual(payload['regions'], rows)
        self.assertEqual(payload['options']['style_regions'], options['style_regions'])
        self.assertEqual(digest(payload), fingerprint)

    def test_gap_fragments_keep_only_styled_notes_outside_new_range(self):
        raw = {'time': {'tempo': [{'position': 0, 'bpm': 120}]}, 'tracks': [{
            'name': 'Lead', 'mainRef': {'isInstrumental': False, 'blickOffset': 0},
            'mainGroup': {'notes': [
                {'onset': 0, 'duration': Q // 2, 'pitch': 60, 'lyrics': '你'},
                {'onset': 2 * Q, 'duration': Q // 2, 'pitch': 62, 'lyrics': '好'},
                {'onset': 4 * Q, 'duration': Q // 2, 'pitch': 64, 'lyrics': '呀'},
            ], 'parameters': {}},
        }]}
        source = self.root / 'gapped-fragments.svp'
        source.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        project = ProjectNotes.load(source)
        track = project.editable[0]
        old = project.region(track, 0., 2.5, STYLE)
        incoming = project.region(track, 1., 1.2,
                                  {'preset': 'sad', 'strength': .8, 'pitch': False})
        rows = project.overlay_regions([old], [incoming])
        self.assertEqual([(row['recipe']['preset'], row['recipe']['start'], row['recipe']['end'])
                          for row in rows],
                         [('gentle', 0., .25), ('sad', 1., 1.25), ('gentle', 2., 2.25)])
        self.assertEqual(len({row['id'] for row in rows}), 3)
        project.verify_regions(rows)

    def test_unrepresentable_old_fragment_rejects_without_mutating_rows(self):
        raw = {'time': {'tempo': [{'position': 0, 'bpm': 120}]}, 'tracks': [{
            'name': 'Lead', 'mainRef': {'isInstrumental': False, 'blickOffset': 0},
            'mainGroup': {'notes': [
                {'onset': 0, 'duration': 28_224_000, 'pitch': 60, 'lyrics': '短'},
                {'onset': Q, 'duration': Q // 2, 'pitch': 62, 'lyrics': '你'},
                {'onset': 2 * Q, 'duration': Q // 2, 'pitch': 64, 'lyrics': '好'},
            ], 'parameters': {}},
        }]}
        source = self.root / 'short-fragment.svp'
        source.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        project = ProjectNotes.load(source)
        track = project.editable[0]
        old = project.region(track, 0., .9, STYLE)
        incoming = project.region(track, .5, .9,
                                  {'preset': 'sad', 'strength': .8, 'pitch': False})
        before = copy.deepcopy([old])
        with self.assertRaises(ValueError):
            project.overlay_regions([old], [incoming])
        self.assertEqual([old], before)

    def test_v1_to_v2_retains_original_and_rejects_lost_updates(self):
        root = self.root / 'drafts'
        v1, v2 = DraftStore(root), TimelineDraftStore(root / 'v2')
        identity = self.project.sha256
        legacy = {'input_sha256': identity, 'mode': 'regions', 'track_indices': [0, 1],
                  'style_regions': [{**STYLE, 'start': 0., 'end': .49}]}
        saved = v1.write(identity, None, legacy, writer='fixture')
        rows = self.project.expand_legacy(saved['data'], [self.third])
        self.assertEqual({row['track_id'] for row in rows}, {self.lead, self.backing})
        doc = {'schema_version': 2, 'input_sha256': identity, 'regions': rows, 'options': settings({})}
        result = v2.write(identity, None, doc, writer='quick')
        self.assertEqual(v1.read(identity)['revision'], saved['revision'])
        self.assertEqual(v2.read(identity)['data']['regions'], rows)
        with self.assertRaises(DraftConflict):
            v2.write(identity, None, {**doc, 'regions': []}, writer='concurrent')
        self.assertEqual(v2.read(identity)['revision'], result['revision'])
        with self.assertRaisesRegex(ValueError, '旧的全轨'):
            region_settings([legacy['style_regions'][0], rows[0]['recipe']])

    def test_dense_visible_window_limits_note_projection(self):
        large = self.raw
        base = large['tracks'][0]['mainGroup']['notes'][0]
        large['tracks'][0]['mainGroup']['notes'] = [
            {**base, 'onset': index * Q // 8, 'duration': Q // 8} for index in range(20000)
        ]
        self.source.write_text(json.dumps(large, ensure_ascii=False), encoding='utf-8')
        loaded = ProjectNotes.load(self.source)
        visible = loaded.visible([loaded.editable[0]], 300., 301.)
        self.assertLess(len(visible), 20)
        self.assertTrue(all(row['start'] < 301 and row['end'] > 300 for row in visible))
        self.assertEqual(len(loaded.visible(loaded.editable, 0., 1800.)), 4000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
