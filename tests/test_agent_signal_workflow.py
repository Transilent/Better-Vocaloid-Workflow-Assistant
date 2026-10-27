"""Measured singing pitch must survive corrected MIDI, export and styling."""
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path.insert(0, str(SRC))

import mido
import numpy as np

from agent_control import Control, ControlError
from agent_exports import apply_export
from agent_export_audit import audit_export
from agent_style import plan_style, read_context, render_style
from agent_workflow import Workflow

RUNTIME = Path(r'C:/Users/DevWind/Documents/Codex/2026-10-08/or-1-2-3-midi4-midi/outputs/Better-Vocaloid-Workflow-Assistant/dependencies/export')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SignalWorkflowTests(unittest.TestCase):
    def setUp(self):
        (APP/'work').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=APP/'work', prefix='signal-flow-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets, self.jobs = self.root/'assets', self.root/'jobs'
        self.assets.mkdir(); self.jobs.mkdir()
        config = self.root/'control.json'
        save(config, {'schema': 1, 'application_root': str(SRC), 'worker_python': sys.executable,
            'state_root': str(self.root/'state'), 'allowed_asset_roots': [str(self.assets)],
            'allowed_job_roots': [str(self.jobs)], 'source_commit': 'signal-fixture',
            'export_runtime': str(RUNTIME)})
        self.control = Control(config)
        self.pitch = self.assets/'original.pitch.npz'
        t = np.arange(300)*.01
        p = np.where(t < .7, 60.25+.24*np.sin(t*2*np.pi*5), 63.85+.3*np.sin(t*2*np.pi*4))
        mask = ((t >= .1)&(t < .6)) | ((t >= 1)&(t < 2.0))
        np.savez_compressed(self.pitch, schema=1, time_step=.01,
                            midi_pitch=p.astype(np.float32), voiced_mask=mask)
        midi = mido.MidiFile(type=1, ticks_per_beat=480, charset='utf8')
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000),
            mido.MetaMessage('time_signature', numerator=4, denominator=4)]))
        # The first corrected note intentionally differs from the measured F0.
        # Reconstructing from note keys would lose the recording's contour.
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage('track_name', name='Vocals'),
            mido.MetaMessage('lyrics', text='山', time=96),
            mido.Message('note_on', note=62, velocity=80),
            mido.Message('note_off', note=62, time=480),
            mido.MetaMessage('lyrics', text='河', time=384),
            mido.Message('note_on', note=64, velocity=80),
            mido.Message('note_off', note=64, time=960)]))
        self.score = self.assets/'corrected.mid'
        midi.save(self.score)
        self.artifact = self.control.import_asset(str(self.score))

    def make_job(self):
        job = self.jobs/'prepared-song'; job.mkdir()
        save(job/'request.json', {'voice_mode': 'single', 'language': 'zh', 'version': 2})
        save(job/'status.json', {'steps': {'midi': {'state': 'done'}}, 'state': 'done'})
        folder = job/'midi'; folder.mkdir()
        target = folder/'vocals.pitch.npz'; target.write_bytes(self.pitch.read_bytes())
        save(folder/'report.json', {'pitch_source': 'absolute_rmvpe',
                                  'output_sha256': {'pitch.npz': sha(target)}})
        identifier = next(k for k,v in self.control._job_paths().items() if v == job)
        return identifier, target

    def test_authorized_job_pitch_is_bound_even_when_instrumental_is_off(self):
        job_id, target = self.make_job()
        record = self.control.make_plan('export', self.artifact['id'],
            formats=['svp'], options={'pitch_source_job_id': job_id, 'include_instrumental': False})
        self.assertEqual(record['plan']['pitch']['status'], 'planned')
        self.assertEqual(record['plan']['options']['pitch_sources'][0]['path'], str(target))
        target.unlink()
        with self.assertRaises(ControlError) as raised:
            self.control.make_plan('export', self.artifact['id'], formats=['svp'],
                                   options={'pitch_source_job_id': job_id})
        self.assertEqual(raised.exception.code, 'pitch_source_missing')

    def test_raw_pitch_paths_and_conflicting_intent_are_rejected(self):
        for options, code in [({'pitch_sources': [{'path': str(self.pitch)}]}, 'unsafe_pitch_path'),
            ({'preserve_pitch': False, 'pitch_bindings': []}, 'conflicting_pitch_source')]:
            with self.assertRaises(ControlError) as raised:
                self.control.export_options(options)
            self.assertEqual(raised.exception.code, code)

    def test_no_reference_is_disclosed_as_a_loss_and_full_review_is_explicit(self):
        record = self.control.make_plan('export', self.artifact['id'])
        self.assertEqual(record['plan']['pitch']['status'], 'unavailable')
        self.assertIn('continuous_pitch_unavailable', {x['code'] for x in record['plan']['conversion_losses']})
        workflow = Workflow(self.control)
        full = workflow.create(score_artifact_id=self.artifact['id'], idempotency_key='full')
        self.assertEqual(full['request']['review_mode'], 'full')
        self.assertNotIn('midi_segments', workflow._read(full['id']))
        segmented = workflow.create(score_artifact_id=self.artifact['id'], review_mode='segments', idempotency_key='segments')
        self.assertTrue(workflow._read(segmented['id'])['midi_segments'])

    def test_repair_sample_progress_reaches_operation_and_returns_new_job(self):
        source = self.jobs/'failed-source'
        source.mkdir()
        record = self.control.save_plan('separation_repair', None,
            {'source_job': str(source), 'plan_sha256': '1'*64})

        def recover(plan, parent, *, cancel, progress):
            progress(17, 100)
            job = parent/'recovered'
            job.mkdir()
            save(job/'request.json', {'voice_mode': 'dual'})
            return {'job_path': str(job), 'model_execution': False}

        with patch('agent_control.subprocess.Popen') as spawn:
            spawn.return_value.pid = 424242
            operation = self.control.preparation_repair_start(record['id'], 'progress-contract')
        with patch('agent_separation_repair.apply_separation_repair', side_effect=recover):
            result = self.control.run_operation(operation['id'])
        self.assertEqual(result['state'], 'succeeded', result.get('error'))
        self.assertEqual(result['progress']['stage'], 'separation')
        self.assertEqual((result['progress']['done'], result['progress']['total']), (17, 100))
        self.assertFalse(result['result']['model_execution'])
        self.assertEqual(result['result']['next']['tool'], 'vwa_preparation_start')
        self.assertTrue(result['result']['next']['arguments']['idempotency_key'])
        self.assertEqual(self.control.job_path(result['result']['job_id']), self.jobs/'recovered')

    @unittest.skipUnless((RUNTIME/'libresvip').is_dir(), 'optional export runtime is unavailable')
    def test_contour_survives_real_conversion_independent_audit_and_timbre_style(self):
        reference = self.control.import_reference(str(self.pitch), 'pitch')
        record = self.control.make_plan('export', self.artifact['id'], formats=['svp'],
            options={'pitch_bindings': [{'pitch_artifact_id': reference['id'], 'vocal_track_index': 0}]})
        result = apply_export(self.artifact['path'], record['plan'], self.root/'export', export_runtime=RUNTIME)
        self.assertEqual(result['candidates'][0]['pitch']['status'], 'preserved')
        audit = audit_export(self.artifact['path'], result, export_runtime=RUNTIME, output_root=self.root/'audit')
        self.assertFalse(audit['blockers'], audit['blockers'])
        self.assertTrue(audit['candidates'][0]['continuous_pitch']['verified'])
        baseline = Path(result['candidates'][0]['candidate_path'])
        raw = json.loads(baseline.read_text(encoding='utf-8-sig').rstrip('\0'))
        original_points = raw['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
        self.assertGreater(len(original_points), 50)
        self.assertGreater(len(set(round(v, 2) for v in original_points[1::2])), 10)
        context = read_context(baseline)
        end = max(n['end_seconds'] for n in context['notes'])
        plan = plan_style(baseline, {'track_indices': [0], 'brief': '只加柔和音色，保留原唱音高。',
            'regions': [{'start': 0, 'end': end, 'preset': 'gentle', 'strength': .5,
                         'style_pitch': False, 'rationale': '保持原唱连续音高以进行音色比较。', 'evidence_refs': []}]})
        styled = render_style(baseline, plan, self.root/'styled')
        data = json.loads(Path(styled['candidate_path']).read_text(encoding='utf-8-sig').rstrip('\0'))
        new_points = data['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
        new_values = dict(zip(new_points[::2], new_points[1::2]))
        for x,y in zip(original_points[::2], original_points[1::2]):
            self.assertAlmostEqual(new_values[x], y, places=6)


if __name__ == '__main__':
    unittest.main()
