"""Post-commit recovery without rerunning fitting or publishing user files."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))
os.environ.setdefault('BVWA_TESTING', '1')

import calibration_session
import tuning_workflow
from common import file_hash, native_path
from expression_calibration import CONTROLS
from tuning_workflow import create_session, register_result, run_session

Q = 705600000
TEST_TEMP = APP / 'work/acceptance/test-temp'
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def tuning_project(media='media/accompaniment.wav'):
    return {
        'version': '1.0.0', 'uuid': 'commit-recovery',
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
                 'tempo': [{'position': 0, 'bpm': 120}]},
        'tracks': [
            {'name': 'Lead', 'mainRef': {'blickOffset': 0, 'isInstrumental': False},
             'mainGroup': {'uuid': 'lead', 'notes': [{'onset': Q, 'duration': Q, 'pitch': 60, 'lyrics': 'a'}],
                           'parameters': {}}, 'groups': []},
            {'name': 'Inst', 'mainRef': {'isInstrumental': True, 'audio': {'filename': media}},
             'mainGroup': {'notes': [], 'parameters': {}}},
        ],
    }


def write_svp(path, data=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data or tuning_project(), ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\0')
    return path


def tuning_session(root):
    media = Path(root) / 'media/accompaniment.wav'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'fixed-media')
    source = write_svp(Path(root) / 'source.svp')
    return source, create_session(source, Path(root) / 'session')


def fake_tuning(data, options, *, cancel=lambda: None, progress=lambda text: None, cache=None):
    progress('stub engine')
    output = copy.deepcopy(data)
    output['commitRecoveryProbe'] = {'options': options.get('expression_density')}
    return output, {'status': 'applied', 'stub': True}


class TuningCommitRecoveryTests(unittest.TestCase):
    def test_missing_receipt_is_repaired_without_reprocessing(self):
        with tempfile.TemporaryDirectory(prefix='tune-receipt-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source, session = tuning_session(root)
            source_before = source.read_bytes()
            real_atomic = tuning_workflow.atomic_json
            def fail_receipt(path, data):
                if Path(path).name.endswith('.tuning.json'):
                    raise OSError('injected receipt failure')
                return real_atomic(path, data)
            options = {'track_indices': [0], 'expression_density': 'original'}
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning) as engine:
                with patch.object(tuning_workflow, 'atomic_json', side_effect=fail_receipt):
                    first = run_session(session, options)
            self.assertEqual(engine.call_count, 1)
            output = Path(first['output'])
            self.assertTrue(output.is_file())
            self.assertFalse(first['receipt_verified'])
            self.assertEqual(first['commit_state'], 'committed_unreceipted')
            self.assertEqual(json.loads((session.parent / 'status.json').read_text(encoding='utf-8'))['state'],
                             'committed_unreceipted')
            self.assertEqual(source.read_bytes(), source_before)
            with patch.object(tuning_workflow, 'process_project', side_effect=AssertionError('must not refit')) as retry_engine:
                repaired = run_session(session, options)
            self.assertEqual(retry_engine.call_count, 0)
            self.assertTrue(repaired['cached'])
            self.assertTrue(repaired['recovered_from_intent'])
            self.assertTrue(repaired['receipt_verified'])
            self.assertEqual(repaired['commit_state'], 'receipted')
            self.assertTrue(Path(repaired['receipt']).is_file())
            self.assertEqual(json.loads((session.parent / 'status.json').read_text(encoding='utf-8'))['state'], 'done')

    def test_deep_windows_path_still_repairs_the_bound_receipt(self):
        root = Path(tempfile.mkdtemp(prefix='tune-deep-', dir=TEST_TEMP))
        try:
            source, session = tuning_session(root / 'short')
            source_before = source.read_bytes()
            deep = root
            while len(str(deep / 'session' / 'commits' / ('tuning-' + '0' * 24 + '.json'))) < 265:
                deep = deep / ('nested-' + 'x' * 48)
                Path(native_path(deep)).mkdir(parents=True, exist_ok=True)
            deep_session = deep / 'session'
            shutil.move(native_path(session.parent), native_path(deep_session))
            session = deep_session / 'session.json'
            self.assertGreater(len(str(deep_session / 'commits' / ('tuning-' + '0' * 24 + '.json'))), 260)
            real_atomic = tuning_workflow.atomic_json
            def fail_receipt(path, data):
                if Path(path).name.endswith('.tuning.json'):
                    raise OSError('injected deep-path receipt failure')
                return real_atomic(path, data)
            options = {'track_indices': [0], 'expression_density': 'original'}
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning) as engine:
                with patch.object(tuning_workflow, 'atomic_json', side_effect=fail_receipt):
                    first = run_session(deep_session, options)
            intent = deep_session / first['commit_intent']
            self.assertGreater(len(str(intent)), 260)
            self.assertTrue(Path(native_path(intent)).is_file())
            with patch.object(tuning_workflow, 'process_project', side_effect=AssertionError('must not refit')) as retry_engine:
                repaired = run_session(deep_session, options)
            self.assertEqual(engine.call_count, 1)
            self.assertEqual(retry_engine.call_count, 0)
            self.assertTrue(repaired['receipt_verified'])
            self.assertEqual(source.read_bytes(), source_before)
        finally:
            shutil.rmtree(native_path(root), ignore_errors=False)

    def test_register_requires_a_complete_verified_receipt(self):
        with tempfile.TemporaryDirectory(prefix='tune-register-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            job = root / 'job'
            job.mkdir()
            (job / 'request.json').write_text(json.dumps({'language': 'zh'}), encoding='utf-8')
            media = root / 'media/accompaniment.wav'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'fixed-media')
            source = write_svp(root / 'source.svp')
            session = create_session(source, root / 'session', job=job)
            options = {'track_indices': [0], 'expression_density': 'original'}
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                result = run_session(session, options)
            self.assertEqual(Path(register_result(session)), Path(result['output']))
            receipt_path = Path(result['receipt'])
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            receipt['dependencies'] = {'files': {'forged': '0' * 64}, 'code': {}}
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '不匹配'):
                register_result(session)

    def test_progress_after_commit_is_an_observer_not_a_failure(self):
        with tempfile.TemporaryDirectory(prefix='tune-progress-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            _, session = tuning_session(root)
            def progress(text):
                if text.startswith('独立调音工程已提交'):
                    raise RuntimeError('injected progress observer failure')
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                result = run_session(session, {'track_indices': [0], 'expression_density': 'original'}, progress=progress)
            self.assertTrue(result['receipt_verified'])
            self.assertEqual(result['commit_state'], 'receipted')
            self.assertEqual(result['observer_errors'][0]['kind'], 'progress')
            self.assertEqual(json.loads((session.parent / 'status.json').read_text(encoding='utf-8'))['state'], 'done')
            self.assertTrue(Path(result['output']).is_file())

    def test_source_change_after_commit_is_rejected_before_receipt_repair(self):
        with tempfile.TemporaryDirectory(prefix='tune-source-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source, session = tuning_session(root)
            real_atomic = tuning_workflow.atomic_json
            def fail_receipt(path, data):
                if Path(path).name.endswith('.tuning.json'):
                    raise OSError('injected receipt failure')
                return real_atomic(path, data)
            options = {'track_indices': [0], 'expression_density': 'original'}
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                with patch.object(tuning_workflow, 'atomic_json', side_effect=fail_receipt):
                    first = run_session(session, options)
            output_before = Path(first['output']).read_bytes()
            changed = tuning_project()
            changed['uuid'] = 'external-edit'
            write_svp(source, changed)
            with patch.object(tuning_workflow, 'process_project', side_effect=AssertionError('must not refit')):
                with self.assertRaisesRegex(ValueError, '外部改变'):
                    run_session(session, options)
            self.assertEqual(Path(first['output']).read_bytes(), output_before)
            self.assertFalse(Path(first['receipt']).exists())

    def test_user_target_edit_is_backed_up_not_adopted_as_intent_output(self):
        with tempfile.TemporaryDirectory(prefix='tune-target-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            _, session = tuning_session(root)
            real_atomic = tuning_workflow.atomic_json
            def fail_receipt(path, data):
                if Path(path).name.endswith('.tuning.json'):
                    raise OSError('injected receipt failure')
                return real_atomic(path, data)
            options = {'track_indices': [0], 'expression_density': 'original'}
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                with patch.object(tuning_workflow, 'atomic_json', side_effect=fail_receipt):
                    first = run_session(session, options)
                edited = json.loads(Path(first['output']).read_text(encoding='utf-8'))
                edited['manualEdit'] = {'preserve': True}
                edited_bytes = json.dumps(edited, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                Path(first['output']).write_bytes(edited_bytes)
                rebuilt = run_session(session, options)
            self.assertFalse(rebuilt['cached'])
            self.assertTrue(rebuilt['preserved_copy'])
            self.assertEqual((session.parent / rebuilt['preserved_copy']).read_bytes(), edited_bytes)
            self.assertNotIn('manualEdit', json.loads(Path(rebuilt['output']).read_text(encoding='utf-8')))

    def test_cancel_before_commit_aborts_and_post_commit_observer_interruption_keeps_result(self):
        with tempfile.TemporaryDirectory(prefix='tune-cancel-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            _, session = tuning_session(root)
            options = {'track_indices': [0], 'expression_density': 'original'}
            calls = []
            def stop():
                calls.append(1)
                if len(calls) == 3:
                    raise InterruptedError('stop after durable intent before publish')
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                with self.assertRaises(InterruptedError):
                    run_session(session, options, cancel=stop)
            self.assertEqual(len(calls), 3)
            self.assertFalse(list((session.parent / 'outputs').glob('tuned-*.svp')))
            self.assertEqual(json.loads((session.parent / 'status.json').read_text(encoding='utf-8'))['state'], 'cancelled')

            def interrupt_observer(text):
                if text.startswith('独立调音工程已提交'):
                    raise InterruptedError('late observer interruption')
            with patch.object(tuning_workflow, 'process_project', side_effect=fake_tuning):
                result = run_session(session, options, progress=interrupt_observer)
            self.assertTrue(Path(result['output']).is_file())
            self.assertTrue(result['receipt_verified'])
            self.assertEqual(result['observer_errors'][0]['error_type'], 'InterruptedError')


def calibration_fixture(root):
    root = Path(root)
    job = root / 'job'
    audio = job / 'audio'
    audio.mkdir(parents=True)
    reference = audio / 'lead.wav'
    reference.write_bytes(b'PRIVATE-SYNTHETIC-REFERENCE')
    project_path = root / 'source.svp'
    project = {'time': {'tempo': [{'position': 0, 'bpm': 120}]},
               'tracks': [{'name': 'Lead', 'mainRef': {'isInstrumental': False},
                           'mainGroup': {'notes': [{'onset': Q, 'duration': Q, 'pitch': 60, 'lyrics': 'a'}], 'parameters': {}}}]}
    project_path.write_text(json.dumps(project), encoding='utf-8')
    profile = root / 'calibration.json'
    profile.write_text(json.dumps({'schema': 1, 'tracks': {'lead': {}}}), encoding='utf-8')
    receipt = {'schema': 1, 'render_channel_mode': 'standard',
               'renders': {name: {'path': str(reference), 'sha256': file_hash(reference), 'frames': 100,
                                  'sample_rate': 44100, 'channels': 1}
                           for name in ('neutral', *CONTROLS)}}
    (root / 'render-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    outputs = {name: 'BVWA_commit_' + name for name in ('neutral', *CONTROLS)}
    spec = {'schema': 1, 'project': str(project_path), 'project_sha256': file_hash(project_path),
            'reference': str(reference), 'reference_sha256': file_hash(reference), 'voice': 'lead',
            'track_index': 0, 'score_sha256': calibration_session.score_identity(project['tracks'][0], project),
            'strength': .6, 'minimum_duration': 1,
            'outputs': outputs, 'render_folder': str(root / 'renders'), 'render_channel_mode': 'standard'}
    session = root / 'session.json'
    session.write_text(json.dumps(spec), encoding='utf-8')
    return session, project_path, reference


def fake_calibration(data, options, *, cancel=lambda: None, progress=lambda text: None, cache=None):
    output = copy.deepcopy(data)
    output['calibrationRecoveryProbe'] = True
    return output, {'status': 'applied', 'stages': ['primary', 'density']}


class CalibrationCommitRecoveryTests(unittest.TestCase):
    def run_fit(self, session, **kwargs):
        with patch('tuning_engine.process_project', side_effect=fake_calibration), \
             patch('tuning_engine.primary_report', return_value={'status': 'applied', 'voicebank_calibrated': False}), \
             patch('calibration_workflow.neutral_project', return_value=session.parent / 'neutral.svp'), \
             patch('pitch_accuracy.audit_svp_pitch', return_value={'status': 'ok'}):
            return calibration_session.fit_session(session, **kwargs)

    def test_report_failure_repairs_same_name_without_refit(self):
        with tempfile.TemporaryDirectory(prefix='cal-report-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            real_atomic = calibration_session.atomic_json
            def fail_report(path, data):
                if Path(path).name == 'fit-report.json':
                    raise OSError('injected report failure')
                return real_atomic(path, data)
            with patch.object(calibration_session, 'atomic_json', side_effect=fail_report):
                first = self.run_fit(session)
            output = Path(first['output'])
            self.assertTrue(output.is_file())
            self.assertFalse(first['report_verified'])
            self.assertTrue(first['report_pending'])
            self.assertFalse((session.parent / 'fit-report.json').exists())
            self.assertTrue(any((session.parent / 'commits').glob('fit-*.json')))
            with patch('tuning_engine.process_project', side_effect=AssertionError('query must stay read-only')):
                self.assertEqual(calibration_session.recoverable_fit_output(session), 'calibrated.svp')
            self.assertIsNone(calibration_session.recoverable_fit_output(session, strength=.4))
            self.assertIsNone(calibration_session.recoverable_fit_output(session, channel_mode='sv-extra'))
            profile = session.parent / 'calibration.json'
            profile_bytes = profile.read_bytes()
            profile.write_bytes(profile_bytes + b' ')
            self.assertIsNone(calibration_session.recoverable_fit_output(session))
            profile.write_bytes(profile_bytes)
            reference = Path(json.loads((session.parent / 'render-receipt.json').read_text(encoding='utf-8'))['renders']['neutral']['path'])
            reference_bytes = reference.read_bytes()
            reference.write_bytes(reference_bytes + b' ')
            self.assertIsNone(calibration_session.recoverable_fit_output(session))
            reference.write_bytes(reference_bytes)
            with patch('tuning_engine.process_project', side_effect=AssertionError('must not refit')), \
                 patch('calibration_workflow.neutral_project', side_effect=AssertionError('must not recreate neutral')):
                repaired = calibration_session.fit_session(session)
            self.assertTrue(Path(repaired['output']).is_file())
            self.assertTrue(repaired['report_verified'])
            self.assertTrue(repaired['recovered_report'])
            self.assertEqual(Path(repaired['output']).read_bytes(), output.read_bytes())
            self.assertIsNone(calibration_session.recoverable_fit_output(session))
            with self.assertRaises(FileExistsError):
                self.run_fit(session)

    def test_changed_input_or_user_target_blocks_same_name_recovery(self):
        with tempfile.TemporaryDirectory(prefix='cal-protect-', dir=TEST_TEMP) as temp:
            session, project_path, _ = calibration_fixture(temp)
            real_atomic = calibration_session.atomic_json
            def fail_report(path, data):
                if Path(path).name == 'fit-report.json':
                    raise OSError('injected report failure')
                return real_atomic(path, data)
            with patch.object(calibration_session, 'atomic_json', side_effect=fail_report):
                first = self.run_fit(session)
            output = Path(first['output'])
            project_path.write_text(project_path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            self.assertIsNone(calibration_session.recoverable_fit_output(session))
            with self.assertRaisesRegex(ValueError, '原工程已改变'):
                self.run_fit(session)
            self.assertTrue(output.is_file())
            self.assertFalse((session.parent / 'fit-report.json').exists())

        with tempfile.TemporaryDirectory(prefix='cal-target-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            real_atomic = calibration_session.atomic_json
            def fail_report(path, data):
                if Path(path).name == 'fit-report.json':
                    raise OSError('injected report failure')
                return real_atomic(path, data)
            with patch.object(calibration_session, 'atomic_json', side_effect=fail_report):
                first = self.run_fit(session)
            output = Path(first['output'])
            edited = json.loads(output.read_text(encoding='utf-8'))
            edited['manualEdit'] = True
            edited_bytes = json.dumps(edited, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            output.write_bytes(edited_bytes)
            with self.assertRaises(FileExistsError):
                self.run_fit(session)
            self.assertEqual(output.read_bytes(), edited_bytes)

    def test_recoverable_query_finds_only_matching_commit_slots(self):
        with tempfile.TemporaryDirectory(prefix='cal-query-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            real_atomic = calibration_session.atomic_json
            def fail_report(path, data):
                if Path(path).name == 'calibrated-dated.fit-report.json':
                    raise OSError('injected report failure')
                return real_atomic(path, data)
            with patch.object(calibration_session, 'atomic_json', side_effect=fail_report):
                first = self.run_fit(session, output_name='calibrated-dated.svp')
            self.assertTrue(Path(first['output']).is_file())
            with patch('tuning_engine.process_project', side_effect=AssertionError('query must stay read-only')):
                self.assertEqual(calibration_session.recoverable_fit_output(session), 'calibrated-dated.svp')
            self.assertIsNone(calibration_session.recoverable_fit_output(session, strength=.4))
            self.assertIsNone(calibration_session.recoverable_fit_output(session, channel_mode='sv-extra'))
            output = Path(first['output'])
            edited = json.loads(output.read_text(encoding='utf-8'))
            edited['manual'] = True
            output.write_text(json.dumps(edited, separators=(',', ':')), encoding='utf-8')
            self.assertIsNone(calibration_session.recoverable_fit_output(session))

        with tempfile.TemporaryDirectory(prefix='cal-foreign-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            foreign = session.parent / 'calibrated.svp'
            foreign.write_text(json.dumps({'time': {'tempo': []}, 'tracks': []}), encoding='utf-8')
            self.assertIsNone(calibration_session.recoverable_fit_output(session))

    def test_diagnostic_failure_before_commit_creates_no_candidate(self):
        with tempfile.TemporaryDirectory(prefix='cal-diagnostic-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            with patch('tuning_engine.process_project', side_effect=fake_calibration), \
                 patch('tuning_engine.primary_report', return_value={'status': 'applied', 'voicebank_calibrated': False}), \
                 patch('calibration_workflow.neutral_project', return_value=session.parent / 'neutral.svp'), \
                 patch('pitch_accuracy.audit_svp_pitch', side_effect=RuntimeError('diagnostic failure')):
                with self.assertRaisesRegex(RuntimeError, 'diagnostic failure'):
                    calibration_session.fit_session(session)
            self.assertFalse((session.parent / 'calibrated.svp').exists())
            self.assertFalse((session.parent / 'commits').exists() and any((session.parent / 'commits').glob('fit-*.json')))
            self.assertFalse((session.parent / 'fit.pending.svp').exists())

    def test_cancel_before_commit_aborts_and_post_commit_registration_error_is_nonfatal(self):
        with tempfile.TemporaryDirectory(prefix='cal-cancel-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            state = {'intent_written': False}
            real_atomic = calibration_session.atomic_json
            def track_intent(path, data):
                real_atomic(path, data)
                if Path(path).parent.name == 'commits':
                    state['intent_written'] = True
            def cancel():
                if state['intent_written']:
                    raise InterruptedError('cancel after durable intent before publish')
            with patch.object(calibration_session, 'atomic_json', side_effect=track_intent):
                with self.assertRaises(InterruptedError):
                    self.run_fit(session, cancel=cancel)
            self.assertTrue(state['intent_written'])
            self.assertFalse((session.parent / 'calibrated.svp').exists())
            self.assertFalse((session.parent / 'fit.pending.svp').exists())

        with tempfile.TemporaryDirectory(prefix='cal-observer-', dir=TEST_TEMP) as temp:
            session, _, _ = calibration_fixture(temp)
            with patch('tuning_engine.process_project', side_effect=fake_calibration), \
                 patch('tuning_engine.primary_report', return_value={'status': 'applied', 'voicebank_calibrated': True}), \
                 patch('calibration_workflow.neutral_project', return_value=session.parent / 'neutral.svp'), \
                 patch('pitch_accuracy.audit_svp_pitch', return_value={'status': 'ok'}), \
                 patch('calibration_workflow.register_expression', side_effect=InterruptedError('late observer interruption')):
                result = calibration_session.fit_session(session)
            self.assertTrue(Path(result['output']).is_file())
            self.assertTrue(result['report_verified'])
            self.assertIn('late observer interruption', result['expression_registration_error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
