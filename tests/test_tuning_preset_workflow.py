"""Backend integration acceptance for tuning presets and project bindings."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import tuning_workflow
from common import file_hash,native_path
from expression_calibration import score_identity
from preset_documents import PresetError, encode_document
from preset_library import PresetLibrary
from tuning_preset_documents import make_document, plan_application, validate_plan
from tuning_workflow import create_session, inspect_project, register_result, run_session


class LongPathTemporaryDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Cleanup must address long immutable filenames as well as the root.
        self.name=native_path(self.name)
        super().cleanup()


Q = 705600000
TEST_TEMP = APP / 'work/acceptance/test-temp'
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def note(onset, duration, pitch, lyric):
    return {
        'onset': onset,
        'duration': duration,
        'pitch': pitch,
        'lyrics': lyric,
        'attributes': {'retakes': [2, 1]},
    }


def mini_project(*, lead_parameters=None, database=None, voice=None, media=None,
                 marker='fixture'):
    database = copy.deepcopy(database or {'name': 'Fixture Bank', 'languageOverride': 'mandarin'})
    voice = copy.deepcopy(voice or {'vocalModePreset': 'Clear'})
    parameters = {} if lead_parameters is None else copy.deepcopy(lead_parameters)
    tracks = [{
        'name': 'Lead Vocal',
        'mainRef': {
            'blickOffset': Q // 4,
            'pitchOffset': 0,
            'isInstrumental': False,
            'database': database,
            'voice': voice,
        },
        'mainGroup': {
            'uuid': marker + '-lead-group',
            'notes': [note(0, Q, 60, '你'), note(Q, Q, 64, '好')],
            'parameters': parameters,
        },
        'retakes': {'keep': marker},
        'groups': [],
    }, {
        'name': 'Backing Vocal',
        'mainRef': {
            'blickOffset': 0,
            'pitchOffset': 0,
            'isInstrumental': False,
            'database': copy.deepcopy(database),
            'voice': copy.deepcopy(voice),
        },
        'mainGroup': {
            'uuid': marker + '-backing-group',
            'notes': [note(0, Q, 55, '伴')],
            'parameters': {},
        },
        'groups': [],
    }]
    if media is not None:
        tracks.append({
            'name': 'Instrumental',
            'mainRef': {
                'isInstrumental': True,
                'audio': {'filename': str(Path(media).resolve()), 'gain': 1.0},
            },
            'mainGroup': {'notes': [], 'parameters': {}},
        })
    return {
        'version': '1.0.0',
        'uuid': marker,
        'library': [{'name': 'keep', 'private': 7}],
        'renderConfig': {'setting': 'keep'},
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}],
        },
        'tracks': tracks,
    }


def write_svp(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\x00')
    return path


def write_wav(path, *, samples=160, rate=16000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * samples)
    return path


def preserve_options(tracks=(0,)):
    return {
        'expression_mode': 'preserve',
        'track_indices': list(tracks),
        'expression_density': 'original',
        'performance_style': 'none',
        'style_strength': 0.0,
        'style_technique': 'none',
        'technique_strength': 0.0,
    }


def exact_raw(document):
    return encode_document(document).replace(b'\n', b'\r\n')


def freeze_plan(root, document, project, bindings, *, raw=None, tag='preset'):
    raw = exact_raw(document) if raw is None else bytes(raw)
    imports = Path(root) / (tag + '-imports')
    imports.mkdir(parents=True, exist_ok=True)
    source = imports / (tag + '.json')
    source.write_bytes(raw)
    library = PresetLibrary(Path(root) / (tag + '-library'))
    imported = library.import_file(source, expected_revision=None)
    snapshot = library.get_snapshot(document['id'], document['version'])
    plan = plan_application(snapshot['document'], project, bindings, raw_document=snapshot['raw'])
    return library, imported, snapshot, plan


def write_profile(directory, project, *, score_sha256=None, database=None, voice=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_wav(directory / 'neutral.wav')
    write_wav(directory / 'loudness.wav')
    track = project['tracks'][0]
    row = {
        'score_sha256': score_sha256 or score_identity(track, project),
        'database': copy.deepcopy(database if database is not None
                                 else track['mainRef']['database']),
        'voice': copy.deepcopy(voice if voice is not None
                               else track['mainRef']['voice']),
        'baseline': 'neutral.wav',
        'probes': [{'parameter': 'loudness', 'step': 0.3, 'audio': 'loudness.wav'}],
    }
    path = directory / 'calibration.json'
    path.write_text(json.dumps({
        'schema': 1,
        'fit_strategy': 'match',
        'tracks': {'lead': row},
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def write_style_sidecar(candidate, *, source_sha='1' * 64):
    candidate = Path(candidate)
    sidecar = Path(str(candidate) + '.style.json')
    sidecar.write_text(json.dumps({
        'schema': 1,
        'kind': 'vwa.style-candidate',
        'source_sha256': source_sha,
        'output_sha256': file_hash(candidate),
        'label': 'fixture candidate',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return sidecar


class TuningPresetWorkflowTests(unittest.TestCase):
    def test_preserve_and_retained_candidates_keep_inputs_and_reuse_without_stacking(self):
        with LongPathTemporaryDirectory(prefix='preset-runs-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            target = mini_project(media=media, marker='target')
            source = write_svp(root / 'source.svp', target)
            raw = source.read_bytes()
            info = inspect_project(source)
            session = create_session(source, root / 'session')

            preserve_document = make_document(
                preserve_options(), 'preserve fixture', identifier='user.test.preserve')
            _, _, snapshot, preserve_plan = freeze_plan(
                root, preserve_document, info, {'vocal_tracks': [0]}, tag='preserve')
            self.assertEqual(snapshot['raw'], exact_raw(preserve_document))
            self.assertEqual(preserve_plan['input_sha256'], info['sha256'])

            first = run_session(session, preserve_plan['options'], preset_binding=preserve_plan)
            self.assertFalse(first['cached'])
            self.assertEqual(Path(first['output']).read_bytes(), raw)
            self.assertEqual(first['output_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual((root / 'session/inputs/source.svp').read_bytes(), raw)
            self.assertEqual(len(list((root / 'session/outputs').glob('tuned-*.svp'))), 1)

            reused = run_session(session, preserve_plan['options'], preset_binding=preserve_plan)
            self.assertTrue(reused['cached'])
            self.assertEqual(Path(reused['output']).read_bytes(), raw)
            self.assertEqual(reused['signature'], first['signature'])
            self.assertEqual(len(list((root / 'session/outputs').glob('tuned-*.svp'))), 1)

            authored = {'loudness': {'mode': 'linear', 'points': [0, 2.5, Q, -1.5, 2 * Q, 0.75]}}
            prior_value = mini_project(lead_parameters=authored, media=media, marker='prior')
            prior = write_svp(root / 'prior.svp', prior_value)
            retained_options = {
                'expression_mode': 'retained',
                'track_indices': [0],
                'retained_sources': [{
                    'track_index': 0,
                    'project': str(prior),
                    'source_track_index': 0,
                }],
                'expression_density': 'original',
                'performance_style': 'none',
                'style_strength': 0.0,
                'style_technique': 'none',
                'technique_strength': 0.0,
            }
            retained_document = make_document(
                retained_options, 'retained fixture', identifier='user.test.retained')
            _, _, _, retained_plan = freeze_plan(
                root,
                retained_document,
                info,
                {'vocal_tracks': [0], 'retained_project': [{
                    'track_index': 0,
                    'project': str(prior),
                    'source_track_index': 0,
                }]},
                tag='retained',
            )
            retained = run_session(session, retained_plan['options'], preset_binding=retained_plan)
            self.assertNotEqual(retained['signature'], first['signature'])
            self.assertEqual(retained['report']['mode'], 'retained')
            retained_value = json.loads(Path(retained['output']).read_bytes().rstrip(b'\x00'))
            self.assertEqual(
                retained_value['tracks'][0]['mainGroup']['parameters']['loudness'],
                authored['loudness'])
            self.assertNotEqual(Path(retained['output']).read_bytes(), raw)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual((root / 'session/inputs/source.svp').read_bytes(), raw)

            retained_bytes = Path(retained['output']).read_bytes()
            retained_reused = run_session(
                session, retained_plan['options'], preset_binding=retained_plan)
            self.assertTrue(retained_reused['cached'])
            self.assertEqual(retained_reused['signature'], retained['signature'])
            self.assertEqual(Path(retained_reused['output']).read_bytes(), retained_bytes)
            self.assertEqual(len(list((root / 'session/outputs').glob('tuned-*.svp'))), 2)

    def test_preset_raw_identity_binding_and_input_sha_change_signatures(self):
        with LongPathTemporaryDirectory(prefix='preset-signatures-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            target = mini_project(media=media, marker='signature')
            source = write_svp(root / 'source.svp', target)
            info = inspect_project(source)
            session = create_session(source, root / 'session')

            document = make_document(
                preserve_options(), 'signature fixture', identifier='user.test.signature')
            _, _, _, baseline = freeze_plan(
                root, document, info, {'vocal_tracks': [0]}, tag='baseline')
            baseline = validate_plan(baseline)
            baseline_result = run_session(session, baseline['options'], preset_binding=baseline)
            self.assertEqual(baseline['input_sha256'], info['sha256'])

            raw_variant = exact_raw(document) + b'   '
            raw_plan = plan_application(
                document, info, {'vocal_tracks': [0]}, raw_document=raw_variant)
            self.assertEqual(raw_plan['preset']['canonical_sha256'],
                             baseline['preset']['canonical_sha256'])
            self.assertNotEqual(raw_plan['preset']['sha256'], baseline['preset']['sha256'])
            raw_result = run_session(session, raw_plan['options'], preset_binding=raw_plan)
            self.assertNotEqual(raw_result['signature'], baseline_result['signature'])

            identity_document = copy.deepcopy(document)
            identity_document['id'] = 'user.test.signature.identity-change'
            identity_plan = plan_application(
                identity_document, info, {'vocal_tracks': [0]},
                raw_document=exact_raw(identity_document))
            identity_result = run_session(
                session, identity_plan['options'], preset_binding=identity_plan)
            self.assertNotEqual(identity_result['signature'], baseline_result['signature'])
            self.assertNotEqual(identity_result['signature'], raw_result['signature'])

            binding_plan = plan_application(
                document, info, {'vocal_tracks': [1]}, raw_document=exact_raw(document))
            binding_result = run_session(
                session, binding_plan['options'], preset_binding=binding_plan)
            self.assertNotEqual(binding_result['signature'], baseline_result['signature'])

            changed_value = copy.deepcopy(target)
            changed_value['uuid'] = 'signature-input-change'
            changed_source = write_svp(root / 'changed.svp', changed_value)
            changed_info = inspect_project(changed_source)
            self.assertNotEqual(changed_info['sha256'], info['sha256'])
            changed_session = create_session(changed_source, root / 'changed-session')
            changed_plan = plan_application(
                document, changed_info, {'vocal_tracks': [0]}, raw_document=exact_raw(document))
            self.assertEqual(changed_plan['input_sha256'], changed_info['sha256'])
            changed_result = run_session(
                changed_session, changed_plan['options'], preset_binding=changed_plan)
            self.assertNotEqual(changed_result['signature'], baseline_result['signature'])

    def test_tampered_plan_and_options_do_not_run(self):
        with LongPathTemporaryDirectory(prefix='preset-tamper-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            source = write_svp(root / 'source.svp',
                               mini_project(media=media, marker='tamper'))
            info = inspect_project(source)
            session = create_session(source, root / 'session')
            document = make_document(
                preserve_options(), 'tamper fixture', identifier='user.test.tamper')
            _, _, _, plan = freeze_plan(
                root, document, info, {'vocal_tracks': [0]}, tag='tamper')

            altered_plan = copy.deepcopy(plan)
            altered_plan['controls']['expression_density'] = 'dense'
            with patch.object(
                    tuning_workflow, 'process_project',
                    side_effect=AssertionError('tampered plan must not reach the engine')) as engine:
                with self.assertRaises(PresetError):
                    run_session(session, altered_plan['options'], preset_binding=altered_plan)
            self.assertFalse(engine.called)

            altered_options = copy.deepcopy(plan['options'])
            altered_options['expression_density'] = 'dense'
            with patch.object(
                    tuning_workflow, 'process_project',
                    side_effect=AssertionError('tampered options must not reach the engine')) as engine:
                with self.assertRaisesRegex(ValueError, '实际调音参数不一致'):
                    run_session(session, altered_options, preset_binding=plan)
            self.assertFalse(engine.called)
            outputs = root / 'session' / 'outputs'
            self.assertEqual(list(outputs.glob('tuned-*.svp')) if outputs.exists() else [], [])

    def test_frozen_plan_executes_after_library_deletion(self):
        with LongPathTemporaryDirectory(prefix='preset-deleted-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            source = write_svp(root / 'source.svp',
                               mini_project(media=media, marker='deleted-library'))
            raw = source.read_bytes()
            info = inspect_project(source)
            document = make_document(
                preserve_options(), 'deleted fixture', identifier='user.test.deleted')
            library, imported, snapshot, plan = freeze_plan(
                root, document, info, {'vocal_tracks': [0]}, tag='deleted')
            self.assertEqual(snapshot['raw'], exact_raw(document))

            library.delete(
                document['id'], document['version'], expected_revision=imported['revision'])
            with self.assertRaises(PresetError) as caught:
                library.get_snapshot(document['id'], document['version'])
            self.assertEqual(caught.exception.code, 'preset_not_found')

            session = create_session(source, root / 'session')
            result = run_session(session, plan['options'], preset_binding=plan)
            self.assertFalse(result['cached'])
            self.assertEqual(Path(result['output']).read_bytes(), raw)
            self.assertEqual(result['preset_binding']['preset'], plan['preset'])
            self.assertEqual(source.read_bytes(), raw)

    def test_known_candidate_requires_explicit_baseline_confirmation(self):
        with LongPathTemporaryDirectory(prefix='preset-origin-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            source = write_svp(root / 'candidate.svp',
                               mini_project(marker='candidate-origin'))
            write_style_sidecar(source)
            info = inspect_project(source)
            self.assertIsNotNone(info['candidate_origin'])
            session = create_session(source, root / 'session')
            document = make_document(
                preserve_options(), 'candidate fixture', identifier='user.test.candidate')

            pending = plan_application(document, info, {'vocal_tracks': [0]})
            self.assertIsNone(pending['options'])
            self.assertTrue(any('候选作为新基线' in item for item in pending['missing']))
            self.assertNotIn('confirm_candidate_baseline',
                             json.dumps(document, ensure_ascii=False))
            with self.assertRaises(PresetError) as caught:
                validate_plan(pending)
            self.assertEqual(caught.exception.code, 'plan_incomplete')

            confirmed = plan_application(
                document, info,
                {'vocal_tracks': [0], 'confirm_candidate_baseline': True})
            self.assertEqual(confirmed['bindings']['confirm_candidate_baseline'], True)
            result = run_session(session, confirmed['options'], preset_binding=confirmed)
            self.assertIs(result['preset_binding']['bindings']['confirm_candidate_baseline'], True)
            receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
            self.assertIs(receipt['preset_binding']['bindings']['confirm_candidate_baseline'], True)

    def test_calibrated_zero_strength_rejects_wrong_score_bank_and_voice_profile(self):
        with LongPathTemporaryDirectory(prefix='preset-calibration-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            target = mini_project(media=media, marker='calibration')
            source = write_svp(root / 'source.svp', target)
            info = inspect_project(source)
            session = create_session(source, root / 'session')
            reference = write_wav(root / 'reference' / 'lead.wav')
            correct_profile = write_profile(root / 'profiles' / 'correct', target)
            bad_score_profile = write_profile(
                root / 'profiles' / 'bad-score', target, score_sha256='0' * 64)
            wrong_bank = copy.deepcopy(target['tracks'][0]['mainRef']['database'])
            wrong_bank['name'] = 'Wrong Bank'
            bad_bank_profile = write_profile(
                root / 'profiles' / 'bad-bank', target, database=wrong_bank)
            wrong_voice = copy.deepcopy(target['tracks'][0]['mainRef']['voice'])
            wrong_voice['vocalModePreset'] = 'Dark'
            bad_voice_profile = write_profile(
                root / 'profiles' / 'bad-voice', target, voice=wrong_voice)
            reference_rows = [{
                'track_index': 0,
                'voice': 'lead',
                'path': str(reference),
            }]
            options = {
                'expression_mode': 'calibrated',
                'track_indices': [0],
                'reference_sources': reference_rows,
                'reference_calibration': str(correct_profile),
                'dynamics_strength': 0.0,
                'expression_density': 'original',
                'performance_style': 'none',
                'style_strength': 0.0,
                'style_technique': 'none',
                'technique_strength': 0.0,
            }
            document = make_document(
                options, 'calibrated fixture', identifier='user.test.calibrated')

            correct_bindings = {
                'vocal_tracks': [0],
                'reference_audio': reference_rows,
                'calibration_profile': str(correct_profile),
            }
            correct_plan = plan_application(document, info, correct_bindings)
            result = run_session(
                session, correct_plan['options'], preset_binding=correct_plan)
            self.assertEqual(result['report']['mode'], 'calibrated')
            self.assertEqual(result['report']['primary']['reason'], 'zero_strength')
            output_count = len(list((root / 'session' / 'outputs').glob('tuned-*.svp')))
            self.assertEqual(output_count, 1)

            cases = [
                (bad_score_profile, '音符或速度表'),
                (bad_bank_profile, '声库配置'),
                (bad_voice_profile, '非表现参数'),
            ]
            for profile, message in cases:
                with self.subTest(profile=profile.parent.name):
                    bindings = {
                        'vocal_tracks': [0],
                        'reference_audio': reference_rows,
                        'calibration_profile': str(profile),
                    }
                    bad_plan = plan_application(document, info, bindings)
                    with patch.object(
                            tuning_workflow, 'process_project',
                            side_effect=AssertionError('invalid calibration must not be analyzed')) as engine:
                        with self.assertRaisesRegex(ValueError, message):
                            run_session(session, bad_plan['options'], preset_binding=bad_plan)
                    self.assertFalse(engine.called)
                    self.assertEqual(
                        len(list((root / 'session' / 'outputs').glob('tuned-*.svp'))),
                        output_count)

    def test_receipt_failure_and_intent_recovery_preserve_and_verify_binding(self):
        with LongPathTemporaryDirectory(prefix='preset-recovery-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            source = write_svp(root / 'source.svp',
                               mini_project(media=media, marker='receipt-recovery'))
            job = root / 'job'
            job.mkdir()
            (job / 'request.json').write_text(json.dumps({'language': 'zh'}), encoding='utf-8')
            info = inspect_project(source)
            session = create_session(source, root / 'session', job=job)
            document = make_document(
                preserve_options(), 'recovery fixture', identifier='user.test.recovery')
            _, _, _, plan = freeze_plan(
                root, document, info, {'vocal_tracks': [0]}, tag='recovery')
            canonical = validate_plan(plan)
            real_atomic_json = tuning_workflow.atomic_json

            def fail_receipt(path, value):
                if str(path).endswith('.svp.tuning.json'):
                    raise OSError('synthetic receipt failure')
                return real_atomic_json(path, value)

            with patch.object(tuning_workflow, 'atomic_json', side_effect=fail_receipt):
                failed_receipt = run_session(
                    session, plan['options'], preset_binding=plan)
            self.assertEqual(failed_receipt['commit_state'], 'committed_unreceipted')
            self.assertFalse(failed_receipt['receipt_verified'])
            self.assertFalse(Path(failed_receipt['receipt']).exists())
            root_dir = root / 'session'
            intent = root_dir / failed_receipt['commit_intent']
            self.assertTrue(intent.is_file())
            intent_value = json.loads(intent.read_text(encoding='utf-8'))
            self.assertEqual(intent_value['preset_binding'], canonical)
            self.assertEqual(
                json.loads((root_dir / 'last-preset-binding.json').read_text(encoding='utf-8')),
                canonical)

            with patch.object(
                    tuning_workflow, 'process_project',
                    side_effect=AssertionError('intent recovery must not synthesize')) as engine:
                recovered = run_session(session, plan['options'], preset_binding=plan)
            self.assertFalse(engine.called)
            self.assertTrue(recovered['recovered_from_intent'])
            self.assertTrue(recovered['receipt_verified'])
            receipt_path = Path(recovered['receipt'])
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            self.assertEqual(receipt['preset_binding'], canonical)

            receipt['options']['expression_density'] = 'dense'
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
            with self.assertRaises(ValueError):
                register_result(session)
            self.assertFalse((job / 'task.json').exists())

            with patch.object(
                    tuning_workflow, 'process_project',
                    side_effect=AssertionError('tampered receipt must repair from intent')) as engine:
                repaired = run_session(session, plan['options'], preset_binding=plan)
            self.assertFalse(engine.called)
            self.assertEqual(repaired['preset_binding'], canonical)
            self.assertTrue(repaired['preserved_receipt'])
            repaired_receipt = json.loads(Path(repaired['receipt']).read_text(encoding='utf-8'))
            self.assertEqual(repaired_receipt['preset_binding'], canonical)
            registered = register_result(session)
            self.assertEqual(Path(registered).resolve(), Path(repaired['output']).resolve())
            task = json.loads((job / 'task.json').read_text(encoding='utf-8'))
            self.assertIn('project', task.get('bindings', {}))

    def test_plain_run_session_without_preset_remains_unchanged(self):
        with LongPathTemporaryDirectory(prefix='preset-plain-', dir=TEST_TEMP) as temp:
            root = Path(temp)
            media = write_wav(root / 'media' / 'song.wav')
            source = write_svp(root / 'source.svp', mini_project(media=media, marker='plain'))
            raw = source.read_bytes()
            session = create_session(source, root / 'session')
            options = {'track_indices': [0], 'expression_density': 'original'}

            first = run_session(session, options)
            self.assertNotIn('preset_binding', first)
            self.assertNotIn('preset', first['dependencies'])
            self.assertEqual(Path(first['output']).read_bytes(), raw)
            self.assertEqual(source.read_bytes(), raw)
            self.assertIsNone(json.loads(
                (root / 'session/last-preset-binding.json').read_text(encoding='utf-8')))

            second = run_session(session, options)
            self.assertTrue(second['cached'])
            self.assertEqual(second['signature'], first['signature'])
            self.assertNotIn('preset_binding', second)
            self.assertNotIn('preset', second['dependencies'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
