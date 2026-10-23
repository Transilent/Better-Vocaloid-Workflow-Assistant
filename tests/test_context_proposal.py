"""End-to-end proposal persistence, recovery and cache tests on synthetic jobs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from v2m_runtime import configure
configure(APP / 'dependencies/vocal2midi2')
from common import atomic_json, file_hash
from inference.API.ustx_api import save_ustx
sys.path.insert(0, str(APP / 'src'))
import context_workflow
from context_planner import proposal_fingerprint


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_digest(value):
    import hashlib
    unsigned = {key: item for key, item in value.items() if key != 'source_sha256'}
    return hashlib.sha256(json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class ProposalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix='context-proposal-', dir=APP / 'work')
        self.job = Path(self.temporary.name)
        native = self.job / 'midi/vocals.ustx'
        native.parent.mkdir()
        save_ustx([
            SimpleNamespace(onset=.5, offset=1.5, pitch=60, lyric='你'),
            SimpleNamespace(onset=1.5, offset=2., pitch=60, lyric='-'),
            SimpleNamespace(onset=2., offset=3., pitch=62, lyric='好'),
        ], native, tempo=120)
        quality = native.with_name('vocals.quality.json')
        atomic_json(quality, {'syllables': []})
        atomic_json(native.parent / 'report.json', {
            'native_sha256': {'ustx': file_hash(native)},
            'output_sha256': {'quality.json': file_hash(quality)},
        })
        self.request = {
            'voice_mode': 'single', 'language': 'zh', 'lyrics': '你，好',
            'project_formats': [], 'audio_formats': [],
            'include_instrumental': False, 'export_pitch': False,
        }
        atomic_json(self.job / 'request.json', self.request)
        self.native = native
        self.words = {'vocals': [
            {'span': [0, 1], 'source': 'synthetic_manual_fixture'},
            {'span': [2, 3], 'source': 'synthetic_manual_fixture'},
        ]}
        atomic_json(self.job / 'words.json', self.words)
        self.svp = self.job / 'exports/voices.svp'
        self.svp.parent.mkdir()
        self.svp.write_bytes(b'unselected-baseline-bytes\x00')

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments, expect=0):
        command = [sys.executable, '-B', str(APP / 'context_workflow.py'),
                   *map(str, arguments)]
        result = subprocess.run(
            command, cwd=APP, capture_output=True, timeout=60,
            env={**__import__('os').environ, 'PYTHONUTF8': '1'})
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        self.assertEqual(result.returncode, expect, output)
        if expect == 0:
            start = output.find('{')
            self.assertGreaterEqual(start, 0, output)
            return json.loads(output[start:])
        return output

    def propose(self, *extra, expect=0):
        return self.run_cli('propose', '--job', self.job, *extra, expect=expect)

    def test_cli_persists_hashed_inputs_and_reuses_generated_plan(self):
        request_bytes = (self.job / 'request.json').read_bytes()
        native_bytes = self.native.read_bytes()
        svp_bytes = self.svp.read_bytes()
        result = self.propose('--goal', 'unparsed synthetic goal',
                              '--words', self.job / 'words.json')
        self.assertEqual(result['status'], 'advisory_only')
        self.assertEqual(result['parameter_application'], 'not_applied_advisory_only')
        self.assertEqual(result['proposal_status'], 'ready')
        plan = read(self.job / 'context/plan.json')
        proposal = read(self.job / 'context/proposal.json')
        source = read(self.job / 'context/proposal-input.json')
        self.assertIsNone(plan['selected_candidate'])
        self.assertIsNone(proposal['selected_candidate'])
        self.assertGreaterEqual(len(plan['candidates']), 2)
        self.assertEqual(source['goal'], 'unparsed synthetic goal')
        self.assertEqual(source['source_sha256'], source_digest(source))
        self.assertEqual(proposal['commit']['state'], 'ready')
        self.assertEqual((self.job / 'request.json').read_bytes(), request_bytes)
        self.assertEqual(self.native.read_bytes(), native_bytes)
        self.assertEqual(self.svp.read_bytes(), svp_bytes)
        self.assertTrue(context_workflow.outputs_intact(
            self.job, {**self.request, 'context_enabled': True}))
        for name in ('exports/context/vocals.evidence.json',
                     'exports/context/vocals.structure.json',
                     'exports/context/plan.snapshot.json',
                     'exports/context/proposal-input.snapshot.json',
                     'exports/context/proposal.snapshot.json',
                     'exports/context/preview.json'):
            self.assertTrue((self.job / name).is_file(), name)
        retried = self.propose()
        self.assertEqual(retried['plan_commit'], 'reused_generated_plan')
        self.assertEqual(read(self.job / 'context/proposal-input.json')['goal'],
                         'unparsed synthetic goal')

    def test_manual_plan_protection_and_explicit_backup(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        plan_path = self.job / 'context/plan.json'
        source_path = self.job / 'context/proposal-input.json'
        proposal_path = self.job / 'context/proposal.json'
        plan = read(plan_path)
        plan['candidates'][0]['intent'] = 'manual edit must survive'
        atomic_json(plan_path, plan)
        manual = plan_path.read_bytes()
        source = source_path.read_bytes()
        proposal = proposal_path.read_bytes()
        output = self.propose('--goal', 'B', expect=1)
        self.assertIn('手工编辑', output)
        self.assertEqual(plan_path.read_bytes(), manual)
        self.assertEqual(source_path.read_bytes(), source)
        self.assertEqual(proposal_path.read_bytes(), proposal)
        self.propose('--goal', 'B', '--refresh-draft')
        self.assertNotEqual(plan_path.read_bytes(), manual)
        self.assertTrue(any(path.read_bytes() == manual
                            for path in (self.job / 'backups').iterdir()))

    def test_invalid_semantic_and_external_plan_do_not_poison_inputs(self):
        self.propose('--goal', 'valid', '--words', self.job / 'words.json')
        paths = [self.job / 'context/plan.json',
                 self.job / 'context/proposal.json',
                 self.job / 'context/proposal-input.json']
        before = [path.read_bytes() for path in paths]
        bad = self.job / 'bad-semantic.json'
        atomic_json(bad, {'schema_version': 1, 'kind': 'bad'})
        self.propose('--goal', 'invalid', '--refresh-draft',
                     '--semantic-response', bad, expect=1)
        self.assertEqual(before, [path.read_bytes() for path in paths])
        external = self.job / 'invalid-external-plan.json'
        atomic_json(external, {'schema_version': 1, 'kind': 'bad'})
        external_before = external.read_bytes()
        self.propose('--goal', 'external-invalid', '--plan', external, expect=1)
        self.assertEqual(before, [path.read_bytes() for path in paths])
        self.assertEqual(external.read_bytes(), external_before)

    def test_oversized_proposal_is_rejected_before_persistence(self):
        baseline = self.svp.read_bytes()
        with patch('context_workflow.MAX_RECORD_BYTES', 2048):
            with self.assertRaisesRegex(ValueError, '记录容量'):
                context_workflow.propose_context(
                    self.job, {**self.request, 'context_enabled': True}, goal='A')
        for name in ('context/proposal-input.json', 'context/proposal.json', 'context/plan.json'):
            self.assertFalse((self.job / name).exists())
        self.assertEqual(self.svp.read_bytes(), baseline)

    def test_invalid_semantic_with_new_words_preserves_existing_inputs(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        paths = ['context/word-spans.json', 'context/proposal-input.json',
                 'context/proposal.json', 'context/plan.json']
        before = {name: (self.job / name).read_bytes() for name in paths}
        changed = self.job / 'changed-words.json'
        atomic_json(changed, {'vocals': [self.words['vocals'][0]]})
        invalid = self.job / 'invalid-semantic.json'
        atomic_json(invalid, {'kind': 'invalid'})
        failure = self.propose('--goal', 'B', '--words', changed,
                               '--semantic-response', invalid, '--refresh-draft', expect=1)
        self.assertIn('response', failure)
        self.assertEqual(before, {name: (self.job / name).read_bytes() for name in paths})

    def test_imported_response_changes_focus_targets_and_stale_copy_is_rejected(self):
        request = dict(self.request)
        evidence = context_workflow.collect_evidence(
            self.job, {**request, 'context_word_spans': self.words})
        item = evidence['vocals']
        response = {
            'schema_version': 1,
            'kind': 'vwa.context-semantic-response',
            'evidence_ids': {'vocals': item['evidence_id']},
            'provider': {'name': 'synthetic-fixture', 'model': 'invented'},
            'interpretations': [
                {'id': 'entry-focus', 'voice': 'vocals', 'span': [0, 3],
                 'quote': '你，好', 'intent': 'entry hypothesis',
                 'focus': [{'span': [0, 1], 'quote': '你', 'role': 'focus'}],
                 'explanation': 'Fixture only.', 'alternatives': [], 'unknowns': []},
                {'id': 'tail-focus', 'voice': 'vocals', 'span': [0, 3],
                 'quote': '你，好', 'intent': 'tail hypothesis',
                 'focus': [{'span': [2, 3], 'quote': '好', 'role': 'focus'}],
                 'explanation': 'Fixture only.', 'alternatives': [], 'unknowns': []},
            ],
        }
        response_path = self.job / 'semantic.json'
        atomic_json(response_path, response)
        self.propose('--goal', 'unparsed goal', '--refresh-draft',
                     '--semantic-response', response_path, '--words', self.job / 'words.json')
        proposal = read(self.job / 'context/proposal.json')
        self.assertEqual(proposal['semantics']['status'], 'imported_unverified')
        semantic = [row for row in proposal['analysis']['candidate_provenance']
                    if row['basis'] == 'imported_semantic_claim']
        self.assertEqual(len(semantic), 2)
        self.assertNotEqual(semantic[0]['scopes'][0]['note_ids'],
                            semantic[1]['scopes'][0]['note_ids'])
        self.assertEqual(proposal['analysis']['inputs']['semantic_goal_binding'],
                         'unknown_for_import')

        source_path = self.job / 'context/proposal-input.json'
        source = read(source_path)
        source['semantic_response']['interpretations'][0]['intent'] = 'edited after commit'
        source['source_sha256'] = source_digest(source)
        atomic_json(source_path, source)
        request_bytes = (self.job / 'request.json').read_bytes()
        native_bytes = self.native.read_bytes()
        output = self.run_cli('export', '--job', self.job, expect=1)
        self.assertIn('proposal', output.lower())
        self.assertEqual((self.job / 'request.json').read_bytes(), request_bytes)
        self.assertEqual(self.native.read_bytes(), native_bytes)

    def test_plan_edit_keeps_proposal_alternatives_when_reexporting(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        proposal_before = read(self.job / 'context/proposal.json')
        plan_path = self.job / 'context/plan.json'
        plan = read(plan_path)
        plan['candidates'][0]['intent'] = 'manual plan edit'
        atomic_json(plan_path, plan)
        self.assertEqual(proposal_before, read(self.job / 'context/proposal.json'))
        self.run_cli('export', '--job', self.job)
        snapshot = read(self.job / 'exports/context/proposal.snapshot.json')
        self.assertEqual(snapshot['plan'], proposal_before['plan'])
        self.assertEqual(read(self.job / 'context/plan.json')['candidates'][0]['intent'],
                         'manual plan edit')
        self.assertTrue(context_workflow.outputs_intact(
            self.job, {**self.request, 'context_enabled': True}))

    def test_ready_a_to_interrupted_b_recovers_without_refresh(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        plan_path = self.job / 'context/plan.json'
        first = plan_path.read_bytes()
        effective = {**self.request, 'context_enabled': True}
        real_write = context_workflow.atomic_json
        def interrupt_plan(path, value):
            if Path(path).name == 'plan.json':
                raise OSError('injected interruption before plan commit')
            return real_write(path, value)
        evidence_id = context_workflow.collect_evidence(self.job, effective)['vocals']['evidence_id']
        response_b = {
            'schema_version': 1,
            'kind': 'vwa.context-semantic-response',
            'evidence_ids': {'vocals': evidence_id},
            'provider': {'name': 'fixture', 'model': 'invented'},
            'interpretations': [{
                'id': 'interrupt-focus', 'voice': 'vocals', 'span': [0, 3],
                'quote': '你，好', 'intent': 'interrupt B',
                'focus': [{'span': [0, 1], 'quote': '你', 'role': 'focus'}],
                'explanation': 'synthetic fixture', 'alternatives': [], 'unknowns': [],
            }],
        }
        with patch('context_workflow.atomic_json', side_effect=interrupt_plan):
            with self.assertRaises(OSError):
                context_workflow.propose_context(
                    self.job, effective, goal='B', semantic_response=response_b)
        self.assertEqual(plan_path.read_bytes(), first)
        pending = read(self.job / 'context/proposal.json')
        self.assertEqual(pending['commit']['state'], 'preparing')
        self.assertNotEqual(pending['plan'], read(plan_path))
        resumed = context_workflow.propose_context(
            self.job, effective, goal='B', semantic_response=response_b)
        self.assertEqual(resumed['proposal_status'], 'ready')
        self.assertEqual(read(self.job / 'context/proposal.json')['commit']['state'], 'ready')
        self.assertEqual(read(plan_path), pending['plan'])

    def test_interruption_after_plan_commit_recovers_without_refresh(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        effective = {**self.request, 'context_enabled': True}
        evidence_id = context_workflow.collect_evidence(self.job, effective)['vocals']['evidence_id']
        semantic = {
            'schema_version': 1, 'kind': 'vwa.context-semantic-response',
            'evidence_ids': {'vocals': evidence_id},
            'provider': {'name': 'synthetic_fixture', 'model': 'none'},
            'interpretations': [{'id': 'committed-focus', 'voice': 'vocals',
                'span': [0, 3], 'quote': '你，好', 'intent': 'B focus',
                'focus': [{'span': [2, 3], 'quote': '好', 'role': 'focus'}],
                'explanation': 'Fault injection after plan write.',
                'alternatives': [], 'unknowns': []}],
        }
        real_write = context_workflow.atomic_json
        def fail_ready(path, value):
            if (Path(path).name == 'proposal.json'
                    and value.get('commit', {}).get('state') == 'ready'):
                raise OSError('injected interruption after plan commit')
            return real_write(path, value)
        with patch('context_workflow.atomic_json', side_effect=fail_ready):
            with self.assertRaises(OSError):
                context_workflow.propose_context(self.job, effective,
                                                goal='B', semantic_response=semantic)
        pending = read(self.job / 'context/proposal.json')
        self.assertEqual(pending['commit']['state'], 'preparing')
        self.assertEqual(read(self.job / 'context/plan.json'), pending['plan'])
        resumed = context_workflow.propose_context(self.job, effective)
        self.assertEqual(resumed['proposal_status'], 'ready')
        self.assertEqual(read(self.job / 'context/proposal.json')['commit']['state'], 'ready')

    def test_cancellation_does_not_replace_plan_and_can_resume(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        plan_path = self.job / 'context/plan.json'
        before = plan_path.read_bytes()
        (self.job / 'cancel.flag').write_text('cancel', encoding='utf-8')
        with self.assertRaises(InterruptedError):
            context_workflow.propose_context(
                self.job, {**self.request, 'context_enabled': True}, goal='B')
        self.assertEqual(plan_path.read_bytes(), before)
        (self.job / 'cancel.flag').unlink()
        context_workflow.propose_context(
            self.job, {**self.request, 'context_enabled': True}, goal='B')
        self.assertEqual(read(self.job / 'context/proposal.json')['commit']['state'], 'ready')


    def test_plan_edit_during_generation_is_not_overwritten(self):
        self.propose('--goal', 'A', '--words', self.job / 'words.json')
        plan_path = self.job / 'context/plan.json'
        source_path = self.job / 'context/proposal-input.json'
        proposal_path = self.job / 'context/proposal.json'
        source_before = source_path.read_bytes()
        proposal_before = proposal_path.read_bytes()
        real_plan = context_workflow.propose_context.__globals__
        import context_planner
        real = context_planner.propose_plan
        def racing_plan(*args, **kwargs):
            manual = read(plan_path)
            manual['candidates'][0]['intent'] = 'user edit during generation'
            atomic_json(plan_path, manual)
            return real(*args, **kwargs)
        with patch('context_planner.propose_plan', side_effect=racing_plan):
            with self.assertRaises(ValueError):
                context_workflow.propose_context(
                    self.job, {**self.request, 'context_enabled': True}, goal='B')
        self.assertEqual(read(plan_path)['candidates'][0]['intent'],
                         'user edit during generation')
        self.assertEqual(source_path.read_bytes(), source_before)
        self.assertEqual(proposal_path.read_bytes(), proposal_before)
    def test_unchanged_inputs_use_cache_and_changed_goal_invalidates_only_context(self):
        request_bytes = (self.job / 'request.json').read_bytes()
        native_bytes = self.native.read_bytes()
        svp_bytes = self.svp.read_bytes()
        first = self.propose('--goal', 'A', '--words', self.job / 'words.json')
        receipts_before = json.loads((self.job / 'exports/receipts.json').read_text(encoding='utf-8'))
        second = self.run_cli('export', '--job', self.job)
        receipts_after = json.loads((self.job / 'exports/receipts.json').read_text(encoding='utf-8'))
        names = [row['file'] for row in first['outputs']]
        self.assertTrue(names)
        for name in names:
            self.assertEqual(receipts_before[name]['input_signature'],
                             receipts_after[name]['input_signature'])
        self.propose('--goal', 'B', '--refresh-draft')
        self.assertFalse(context_workflow.outputs_intact(
            self.job, {**self.request, 'context_goal': 'A', 'context_enabled': True}))
        self.assertTrue(context_workflow.outputs_intact(
            self.job, {**self.request, 'context_enabled': True}))
        self.assertEqual((self.job / 'request.json').read_bytes(), request_bytes)
        self.assertEqual(self.native.read_bytes(), native_bytes)
        self.assertEqual(self.svp.read_bytes(), svp_bytes)
if __name__ == '__main__':
    unittest.main(verbosity=2)


