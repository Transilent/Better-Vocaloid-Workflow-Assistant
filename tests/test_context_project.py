"""Independent artifacts, explicit application and recovery over small scores."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from context_project import prepare_bundle, apply_bundle, read_json, densify_linear
from context_evidence import build_evidence
from context_alignment import align_lyrics


def score():
    return {'version': 154, 'time': {'tempo':[{'position':0,'bpm':120}], 'meter':[]},
        'tracks':[{'name':'Lead', 'groups':[], 'mainRef':{'blickOffset':0, 'pitchOffset':0,
            'database':{'name':'Test bank'}, 'isInstrumental':False},
            'mainGroup':{'notes':[{'onset':i*705600000, 'duration':600000000, 'pitch':60,
                                  'lyrics':c} for i,c in enumerate('你好再见')],
                'parameters':{'loudness':{'mode':'linear','points':[0,0,2822400000,0]},
                              'pitchDelta':{'mode':'linear','points':[0,12,2822400000,-8]},
                              'privateUnknown':{'mode':'cubic','points':[0,77]}}}}]}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'original.svp'
        self.source.write_text(json.dumps(score(), ensure_ascii=False), encoding='utf-8')
        self.original = self.source.read_bytes()
        self.bundle = self.root/'analysis'

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        return prepare_bundle(self.source, {'lead':'你好再见'}, 'zh', self.bundle)

    def test_tempo_dependency_invalidates_prepared_cache_without_overwrite(self):
        import context_project
        self.prepare()
        before=(self.bundle/'manifest.json').read_bytes()
        dependency=Path(context_project.__file__).parent/'vocal_expression.py'
        original=dependency.read_bytes()
        try:
            dependency.write_bytes(original+b'\n# isolated TempoMap dependency revision\n')
            with self.assertRaisesRegex(ValueError,'different inputs'):
                self.prepare()
            self.assertEqual((self.bundle/'manifest.json').read_bytes(),before)
            self.assertEqual(self.source.read_bytes(),self.original)
        finally:dependency.write_bytes(original)

    def application(self):
        ev = read_json(self.bundle/'evidence.json')['lead']
        return {'schema_version':1,'kind':'vwa.context-application',
            'evidence_ids':{'lead':ev['evidence_id']},'track_bindings':{'lead':0},
            'selections':[{'id':'hello','voice':'lead','note_ids':[ev['notes'][0]['id']],
                'phase':'onset','recipe':'clear-entry','amount':.6,
                'rationale':'explicit synthetic emphasis', 'source_span':[0,1], 'source_quote':'你'}]}

    def profile(self):
        return {'schema_version':1,'kind':'vwa.context-adapter-profile','id':'test',
            'voicebank':'Test bank','allow_unbound_voicebank':False,'status':'experimental_unrendered',
            'recipes':{'clear-entry':{'phase':'onset','controls':{'loudness':.8}}}}

    def test_preparation_and_application_cache_are_real_artifacts(self):
        first = self.prepare()
        self.assertEqual(first, self.prepare())
        result = apply_bundle(self.bundle, self.application(), self.profile(), self.root/'pair')
        self.assertEqual(result, apply_bundle(self.bundle, self.application(), self.profile(), self.root/'pair'))
        self.assertEqual((self.root/'pair/A-before.svp').read_bytes(), self.original)
        self.assertNotEqual(read_json(self.root/'pair/A-before.svp'), read_json(self.root/'pair/B-context-trial.svp'))
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(result['audio_rendered'])

    def test_changed_inputs_and_tampered_artifacts_never_overwrite(self):
        self.prepare()
        saved = (self.bundle/'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            prepare_bundle(self.source, {'lead':'再见'}, 'zh', self.bundle)
        self.assertEqual((self.bundle/'manifest.json').read_bytes(), saved)
        (self.bundle/'baseline.svp').write_bytes(b'{}')
        with self.assertRaises(ValueError):
            apply_bundle(self.bundle, self.application(), self.profile(), self.root/'pair')
        self.assertFalse((self.root/'pair').exists())

    def test_stale_code_and_wrong_binding_rejected(self):
        self.prepare()
        bad = self.application()
        bad['track_bindings'] = {'lead':1}
        with self.assertRaises(ValueError):
            apply_bundle(self.bundle, bad, self.profile(), self.root/'pair')
        with patch('context_project._code', return_value={'changed':'code'}):
            with self.assertRaises(ValueError):
                apply_bundle(self.bundle, self.application(), self.profile(), self.root/'pair')

    def test_interrupted_commit_does_not_create_ready_output(self):
        with patch('context_project.os.rename', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.prepare()['status'], 'prepared')

    def test_explicit_partial_alignment_flows_into_evidence(self):
        notes=[{'lyric':c,'start':i,'end':i+.8,'pitch':60} for i,c in enumerate('你错再见')]
        alignment=align_lyrics('你好再见','zh',notes)
        ev=build_evidence('你好再见','zh',notes,alignment=alignment)
        self.assertTrue(ev['notes'][-1]['text_unit_ids'])
        self.assertFalse(ev['notes'][1]['text_unit_ids'])
        self.assertEqual(ev['coverage']['status'], 'partial')
        strict=build_evidence('你好再见','zh',notes)
        self.assertEqual(strict['coverage']['notes']['text_mapped'],0)

    def test_density_retains_continuous_values_between_knots(self):
        obj=score();q=705600000
        obj['tracks'][0]['mainGroup']['parameters']['voicing']={
            'mode':'linear','points':[0,.4,q,.9,2*q,.95,4*q,1.]}
        before=copy.deepcopy(obj)
        report=densify_linear(obj)
        points=obj['tracks'][0]['mainGroup']['parameters']['voicing']['points']
        lookup=dict(zip(points[::2],points[1::2]))
        self.assertAlmostEqual(lookup[q//2],.65,places=12)
        self.assertEqual(obj['tracks'][0]['mainGroup']['notes'],before['tracks'][0]['mainGroup']['notes'])
        self.assertEqual(report['interpolation'],'original_piecewise_linear')

    def test_resealed_manifest_cannot_rebind_a_changed_baseline(self):
        self.prepare()
        p=self.bundle/'baseline.svp';obj=read_json(p)
        obj['tracks'][0]['mixer']={'gainDecibel':9.25}
        p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
        m=read_json(self.bundle/'manifest.json')
        m['files']['baseline.svp']=hashlib.sha256(p.read_bytes()).hexdigest()
        (self.bundle/'manifest.json').write_text(json.dumps(m),encoding='utf-8')
        with self.assertRaises(ValueError):
            apply_bundle(self.bundle,self.application(),self.profile(),self.root/'pair')
        self.assertFalse((self.root/'pair').exists())

    def test_unselected_overlapping_note_cannot_share_effect_window(self):
        obj=score()
        obj['tracks'][0]['mainGroup']['notes'][1]['onset']=0
        self.source.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
        self.prepare()
        with self.assertRaisesRegex(ValueError,'未选音符'):
            apply_bundle(self.bundle,self.application(),self.profile(),self.root/'pair')


if __name__ == '__main__':
    unittest.main()
