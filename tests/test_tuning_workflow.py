"""Backend acceptance for immutable, recoverable tuning sessions."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import tuning_workflow
from common import file_hash
from tuning_workflow import (create_session, inspect_project, job_projects, read_session,
                             register_result, run_session)


Q = 705600000
TEST_TEMP = APP / 'work/acceptance/test-temp'
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def note(onset, duration, pitch, lyric):
    return {'onset': onset, 'duration': duration, 'pitch': pitch, 'lyrics': lyric,
            'attributes': {'retakes': [2, 1], 'paramTension': .1}}


def project(media='media/accompaniment.wav'):
    return {
        'version': '1.0.0',
        'uuid': 'session-fixture',
        'library': [{'name': 'keep', 'private': 7}],
        'renderConfig': {'setting': 'keep'},
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}],
        },
        'tracks': [{
            'name': 'Lead Vocal',
            'mainRef': {'blickOffset': Q // 3, 'pitchOffset': 0, 'isInstrumental': False,
                        'database': {'name': 'Test Bank'}, 'voice': {'preset': 'keep'}},
            'mainGroup': {'uuid': 'lead-group', 'notes': [note(0, 2 * Q, 60, 'a'), note(2 * Q, Q, 64, 'b')],
                          'parameters': {
                              'loudness': {'mode': 'linear', 'points': [0, -2., 3 * Q, 1.]},
                              'tension': {'mode': 'linear', 'points': [0, 0., Q, .1]},
                              'pitchDelta': {'mode': 'linear', 'points': [0, 1., Q, 2.]},
                              'unknownVendor': {'mode': 'cosine', 'points': [0, 4., Q, 5.], 'tag': 'keep'},
                          }},
            'retakes': {'keep': True}, 'groups': [],
        }, {
            'name': 'Instrumental',
            'mainRef': {'isInstrumental': True, 'audio': {'filename': media, 'gain': 1.0}},
            'mainGroup': {'notes': [], 'parameters': {}},
        }],
    }


def write_svp(path, data=None, *, nul=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data or project(), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if nul:
        payload += b'\x00'
    path.write_bytes(payload)
    return path


def write_job(root, model=None):
    root = Path(root)
    (root / 'exports').mkdir(parents=True, exist_ok=True)
    (root / 'audio').mkdir(parents=True, exist_ok=True)
    (root / 'request.json').write_text(json.dumps({'language': 'zh'}), encoding='utf-8')
    baseline = write_svp(root / 'exports/voices.svp', model or project())
    return baseline


def make_media(root):
    media = Path(root) / 'media/accompaniment.wav'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'fixed-media-bytes')
    return media


def make_profile(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'neutral.wav').write_bytes(b'neutral')
    (root / 'loudness.wav').write_bytes(b'probe')
    profile = root / 'calibration.json'
    profile.write_text(json.dumps({
        'schema': 1, 'fit_strategy': 'match', 'tracks': {'lead': {
            'score_sha256': '0' * 64, 'database': {'name': 'Test Bank'}, 'voice': {},
            'baseline': 'neutral.wav', 'probes': [{'parameter': 'loudness', 'step': .3, 'audio': 'loudness.wav'}],
        }}
    }), encoding='utf-8')
    return profile


class TuningWorkflowTests(unittest.TestCase):
    def test_inspect_has_actual_indices_and_rejects_non_svp_or_no_vocal(self):
        with tempfile.TemporaryDirectory(prefix='workflow-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source = write_svp(root / 'source.svp')
            info = inspect_project(source)
            self.assertEqual(info['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual([row['track_index'] for row in info['tracks']], [0, 1])
            self.assertEqual([row['note_count'] for row in info['tracks']], [2, 0])
            self.assertEqual([row['instrumental'] for row in info['tracks']], [False, True])

            wrong = root / 'source.ustx'
            wrong.write_bytes(source.read_bytes())
            with self.assertRaisesRegex(ValueError, '支持SVP'):
                inspect_project(wrong)
            malformed = root / 'broken.svp'
            malformed.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '有效的SVP'):
                inspect_project(malformed)
            instrumental = project()
            instrumental['tracks'] = [instrumental['tracks'][1]]
            with self.assertRaisesRegex(ValueError, '没有演唱轨'):
                inspect_project(write_svp(root / 'no-vocal.svp', instrumental))

    def test_job_projects_puts_verified_original_first_and_lists_styled_only_for_import(self):
        with tempfile.TemporaryDirectory(prefix='job-', dir=TEST_TEMP) as temp:
            job = Path(temp)
            baseline = write_job(job)
            styled = write_svp(job / 'exports/styled.svp', project())
            receipts = {
                'exports/voices.svp': {'kind': 'project', 'active': True, 'sha256': file_hash(baseline)},
                'exports/styled.svp': {'kind': 'style_candidate', 'active': True, 'sha256': file_hash(styled)},
            }
            (job / 'exports/receipts.json').write_text(json.dumps(receipts), encoding='utf-8')
            rows = job_projects(job)
            self.assertEqual(rows[0]['kind'], 'baseline')
            self.assertTrue(rows[0]['verified'])
            self.assertEqual(Path(rows[0]['path']), baseline.resolve())
            self.assertEqual(rows[1]['kind'], 'style_candidate')
            self.assertTrue(rows[1]['verified'])
            self.assertGreaterEqual(len(rows), 2)

    def test_create_session_snapshots_bytes_and_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory(prefix='create-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            job = root / 'job'
            write_job(job)
            media = make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            raw = source.read_bytes()
            spec_path = create_session(source, root / 'session', job=job)
            spec = read_session(spec_path)
            self.assertEqual(spec['source_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(spec['input_sha256'], spec['source_sha256'])
            self.assertEqual(Path(spec['job']), job.resolve())
            self.assertEqual((root / 'session/inputs/source.svp').read_bytes(), raw)
            self.assertEqual(source.read_bytes(), raw)
            with self.assertRaises(FileExistsError):
                create_session(source, root / 'session')
            self.assertTrue(media.is_file())

    def test_invalid_job_does_not_leave_partial_session_directory(self):
        with tempfile.TemporaryDirectory(prefix='bad-job-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            destination = root / 'must-not-exist'
            with self.assertRaisesRegex(ValueError, '关联准备任务不存在'):
                create_session(source, destination, job=root / 'missing-job')
            self.assertFalse(destination.exists())

    def test_run_relocates_media_once_but_does_not_modify_source_and_cache_is_independent(self):
        with tempfile.TemporaryDirectory(prefix='run-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            raw = source.read_bytes()
            session = create_session(source, root / 'session')
            options = {'track_indices': [0], 'expression_density': 'original'}
            first = run_session(session, options)
            self.assertFalse(first['cached'])
            self.assertEqual(first['project_commit_count'], 1)
            self.assertEqual(first['status'], 'unchanged')
            self.assertEqual(Path(first['output']).name, 'tuned-' + first['signature'][:16] + '.svp')
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual((root / 'session/inputs/source.svp').read_bytes(), raw)
            output = json.loads(Path(first['output']).read_text(encoding='utf-8'))
            audio = output['tracks'][1]['mainRef']['audio']['filename']
            self.assertTrue(Path(audio).is_absolute())
            self.assertEqual(Path(audio), media.resolve())
            self.assertEqual(first['media_references'][0]['original_reference'], 'media/accompaniment.wav')
            self.assertFalse(first['media_references'][0]['audio_modified'])
            receipt = json.loads(Path(first['receipt']).read_text(encoding='utf-8'))
            self.assertEqual(receipt['output_sha256'], file_hash(first['output']))
            before = Path(first['output']).read_bytes()
            second = run_session(session, options)
            self.assertTrue(second['cached'])
            self.assertEqual(Path(second['output']).read_bytes(), before)
            self.assertEqual(len(list((root / 'session/outputs').glob('tuned-*.svp'))), 1)
            self.assertEqual((root / 'session/inputs/source.svp').read_bytes(), raw)

    def test_manual_candidate_edit_is_backed_up_then_rebuilt_without_stacking(self):
        with tempfile.TemporaryDirectory(prefix='manual-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            options = {'track_indices': [0], 'expression_density': 'dense',
                       'performance_style': 'soft_ballad', 'style_strength': .2}
            first = run_session(session, options)
            generated = Path(first['output']).read_bytes()
            edited = json.loads(generated)
            edited['manualReview'] = {'keep': True}
            edited_bytes = json.dumps(edited, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            Path(first['output']).write_bytes(edited_bytes)
            second = run_session(session, options)
            self.assertFalse(second['cached'])
            self.assertTrue(second['preserved_copy'])
            backup = root / 'session' / second['preserved_copy']
            self.assertEqual(backup.read_bytes(), edited_bytes)
            self.assertEqual(Path(second['output']).read_bytes(), generated)
            rebuilt = json.loads(Path(second['output']).read_text(encoding='utf-8'))
            self.assertNotIn('manualReview', rebuilt)
            self.assertEqual(second['project_commit_count'], 1)

    def test_original_change_stops_and_missing_original_still_recovers_from_snapshot(self):
        with tempfile.TemporaryDirectory(prefix='source-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            changed = project(media='media/accompaniment.wav')
            changed['uuid'] = 'changed-external'
            source.write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '外部改变'):
                run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            status = json.loads((root / 'session/status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'], 'failed')
            self.assertFalse(list((root / 'session/outputs').glob('tuned-*.svp')) if (root / 'session/outputs').exists() else [])

            second_source = write_svp(root / 'second.svp', project(media='media/accompaniment.wav'))
            second_session = create_session(second_source, root / 'recoverable')
            original_bytes = second_source.read_bytes()
            second_source.unlink()
            result = run_session(second_session, {'track_indices': [0], 'expression_density': 'original'})
            self.assertEqual(result['source_sha256'], hashlib.sha256(original_bytes).hexdigest())
            self.assertTrue(Path(result['output']).is_file())
            self.assertEqual((root / 'recoverable/inputs/source.svp').read_bytes(), original_bytes)

    def test_cancel_or_failure_never_commits_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory(prefix='cancel-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            options = {'track_indices': [0], 'expression_density': 'original'}

            def cancel_at_start(text):
                if text == '从固定输入快照开始处理':
                    raise InterruptedError('stop-before-processing')
            with self.assertRaises(InterruptedError):
                run_session(session, options, progress=cancel_at_start)
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'cancelled')
            self.assertFalse(list((root / 'session/outputs').glob('tuned-*.svp')) if (root / 'session/outputs').exists() else [])

            with patch.object(tuning_workflow, 'process_project', side_effect=RuntimeError('synthetic engine failure')):
                with self.assertRaisesRegex(RuntimeError, 'synthetic engine failure'):
                    run_session(session, options)
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'failed')
            result = run_session(session, options)
            self.assertFalse(result['cached'])
            self.assertTrue(Path(result['output']).is_file())
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'done')

    def test_audio_and_profile_changes_invalidate_result_signatures(self):
        with tempfile.TemporaryDirectory(prefix='dependencies-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            reference = root / 'reference.wav'
            reference.write_bytes(b'first-reference')
            session = create_session(source, root / 'session')
            options = {
                'expression_mode': 'reference', 'track_indices': [0], 'reference_dynamics': True,
                'dynamics_strength': 0,
                'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
                'expression_density': 'original',
            }
            first = run_session(session, options)
            reference.write_bytes(b'second-reference')
            second = run_session(session, options)
            self.assertFalse(second['cached'])
            self.assertNotEqual(first['signature'], second['signature'])
            self.assertNotEqual(first['output'], second['output'])

            profile = make_profile(root / 'profile')
            profile_options = {
                'expression_mode': 'calibrated', 'track_indices': [0],
                'reference_calibration': str(profile), 'dynamics_strength': 0,
                'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
                'expression_density': 'original',
            }
            third = run_session(session, profile_options)
            profile.write_text(profile.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            fourth = run_session(session, profile_options)
            self.assertFalse(fourth['cached'])
            self.assertNotEqual(third['signature'], fourth['signature'])

    def test_snapshot_tamper_is_rejected_before_run(self):
        with tempfile.TemporaryDirectory(prefix='tamper-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            snapshot = root / 'session/inputs/source.svp'
            snapshot.write_bytes(snapshot.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, '输入快照已改变'):
                read_session(session)
            with self.assertRaisesRegex(ValueError, '输入快照已改变'):
                run_session(session, {'track_indices': [0], 'expression_density': 'original'})

    def test_manual_result_change_is_not_registered(self):
        with tempfile.TemporaryDirectory(prefix='register-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            write_job(root / 'job')
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session', job=root / 'job')
            result = run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            output = Path(result['output'])
            output.write_text(output.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '已改变'):
                register_result(session)

    def test_reference_change_during_run_fails_before_commit(self):
        with tempfile.TemporaryDirectory(prefix='midrun-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            reference = root / 'reference.wav'
            reference.write_bytes(b'first')
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            options = {
                'expression_mode': 'reference', 'track_indices': [0], 'reference_dynamics': True,
                'dynamics_strength': 0,
                'reference_sources': [{'track_index': 0, 'voice': 'lead', 'path': str(reference)}],
                'expression_density': 'original',
            }
            def mutate_dependency(data, normalized, **kwargs):
                reference.write_bytes(b'changed-during-run')
                return copy.deepcopy(data), {'status': 'unchanged'}
            with patch.object(tuning_workflow, 'process_project', side_effect=mutate_dependency):
                with self.assertRaisesRegex(ValueError, '参考音频、配置或算法发生变化'):
                    run_session(session, options)
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'failed')
            self.assertFalse(list((root / 'session/outputs').glob('tuned-*.svp')) if (root / 'session/outputs').exists() else [])

    def test_snapshot_change_during_run_is_rechecked_before_commit(self):
        with tempfile.TemporaryDirectory(prefix='snapshot-midrun-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            snapshot = root / 'session/inputs/source.svp'
            def mutate_snapshot(data, normalized, **kwargs):
                snapshot.write_bytes(snapshot.read_bytes() + b' ')
                return copy.deepcopy(data), {'status': 'unchanged'}
            with patch.object(tuning_workflow, 'process_project', side_effect=mutate_snapshot):
                with self.assertRaisesRegex(ValueError, '输入快照已改变'):
                    run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'failed')
            self.assertFalse(list((root / 'session/outputs').glob('tuned-*.svp')) if (root / 'session/outputs').exists() else [])

    def test_final_replace_failure_leaves_no_committed_candidate(self):
        with tempfile.TemporaryDirectory(prefix='atomic-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            original_replace = Path.replace
            def fail_candidate_replace(self, target):
                if self.suffix == '.pending' and Path(target).suffix == '.svp':
                    raise OSError('synthetic replace failure')
                return original_replace(self, target)
            with patch.object(Path, 'replace', autospec=True, side_effect=fail_candidate_replace):
                with self.assertRaisesRegex(OSError, 'synthetic replace failure'):
                    run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            self.assertEqual(json.loads((root / 'session/status.json').read_text(encoding='utf-8'))['state'], 'failed')
            self.assertFalse(list((root / 'session/outputs').glob('tuned-*.svp')) if (root / 'session/outputs').exists() else [])
            self.assertFalse(list((root / 'session/outputs').glob('*.pending')) if (root / 'session/outputs').exists() else [])
            retried = run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            self.assertTrue(Path(retried['output']).is_file())

    def test_completed_result_registers_only_by_explicit_job_action(self):
        with tempfile.TemporaryDirectory(prefix='register-ok-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            write_job(root / 'job')
            request_before = (root / 'job/request.json').read_bytes()
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session', job=root / 'job')
            result = run_session(session, {'track_indices': [0], 'expression_density': 'original'})
            registered = register_result(session)
            self.assertEqual(Path(registered).resolve(), Path(result['output']).resolve())
            self.assertEqual((root / 'job/request.json').read_bytes(), request_before)
            metadata = json.loads((root / 'job/task.json').read_text(encoding='utf-8'))
            self.assertIn('project', metadata.get('bindings', {}))

    def test_cli_reads_session_and_writes_only_session_output(self):
        with tempfile.TemporaryDirectory(prefix='cli-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            make_media(root)
            source = write_svp(root / 'source.svp', project(media='media/accompaniment.wav'))
            session = create_session(source, root / 'session')
            options_path = root / 'options.json'
            options_path.write_text(json.dumps({'track_indices': [0], 'expression_density': 'original'}), encoding='utf-8')
            command = [sys.executable, '-B', str(APP / 'src/tuning_workflow.py'),
                       '--session', str(session), '--options', str(options_path)]
            completed = subprocess.run(command, cwd=APP, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace', timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            result = json.loads(lines[-1])
            self.assertTrue(Path(result['output']).is_relative_to(root / 'session'))
            self.assertEqual(source.read_bytes(), write_svp(root / 'expected.svp', project(media='media/accompaniment.wav')).read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
