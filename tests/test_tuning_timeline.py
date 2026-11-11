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
from tuning_timeline import ProjectNotes, TimelineDraftStore, freeze_plan
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
