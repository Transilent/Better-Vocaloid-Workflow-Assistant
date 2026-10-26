"""Contract tests for the local MCP Agent control boundary."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path.insert(0, str(SRC))
import agent_control


class FakeProcess:
    def __init__(self, pid=4242):
        self.pid = pid


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class ControlFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='agent-control-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        self.assets = self.root / 'assets'
        self.jobs = self.root / 'jobs'
        self.state = self.root / 'state'
        self.cache = self.root / 'user-cache'
        for directory in (self.app, self.assets, self.jobs, self.cache):
            directory.mkdir(parents=True)
        self._install_fixture_app()
        self.config_path = self.root / 'control.json'
        self.write_config()
        self.control = self.new_control()

    def _install_fixture_app(self):
        for name in agent_control.CODE_FILES:
            destination = self.app / name
            if name in ('agent_control.py', 'agent_worker.py'):
                shutil.copy2(SRC / name, destination)
            elif name == 'agent_score.py':
                destination.write_text(
                    "from pathlib import Path\n"
                    "def read_score(path):\n"
                    "    return {'fixture': True, 'path': str(path), 'notes': []}\n"
                    "def review_score(path):\n"
                    "    return {'fixture': True, 'path': str(path), 'hints': []}\n"
                    "def plan_patch(path, expected_sha256, edits):\n"
                    "    return {'fixture': True, 'path': str(path),\n"
                    "            'source_sha256': expected_sha256, 'edits': edits,\n"
                    "            'plan_sha256': '0' * 64}\n",
                    encoding='utf-8')
            elif name == 'agent_style.py':
                destination.write_text(
                    "from pathlib import Path\n"
                    "def read_context(path, track_indices=None):\n"
                    "    return {'fixture': True, 'path': str(path),\n"
                    "            'track_indices': track_indices}\n"
                    "def plan_style(path, proposal):\n"
                    "    return {'fixture': True, 'path': str(path), 'proposal': proposal}\n",
                    encoding='utf-8')
            else:
                destination.write_text('"""Private control-test fixture."""\n', encoding='utf-8')
        # The worker imports this extra fixture module for genuinely read-only jobs.
        (self.app / 'progress_state.py').write_text(
            "import json\n"
            "from pathlib import Path\n"
            "def snapshot(job):\n"
            "    job = Path(job)\n"
            "    status = json.loads((job / 'status.json').read_text(encoding='utf-8'))\n"
            "    progress = json.loads((job / 'progress.json').read_text(encoding='utf-8'))\n"
            "    return {'fixture_read_only': True, 'status': status, 'event': progress}\n",
            encoding='utf-8')

    def write_config(self, **changes):
        value = {
            'schema': agent_control.SCHEMA,
            'application_root': str(self.app),
            'worker_python': sys.executable,
            'state_root': str(self.state),
            'allowed_asset_roots': [str(self.assets)],
            'allowed_job_roots': [str(self.jobs)],
            'source_commit': 'fixture-commit',
        }
        value.update(changes)
        write_json(self.config_path, value)
        return value

    def new_control(self, path=None):
        return agent_control.Control(path or self.config_path)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(agent_control.ControlError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def register(self, name, data=b'MThd'):
        path = self.assets / name
        path.write_bytes(data)
        return self.control.register_artifact(path)

    def start_lock_holder(self, path):
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from agent_control import file_lock\n"
            "with file_lock(sys.argv[1]):\n"
            "    print('ready', flush=True)\n"
            "    sys.stdin.readline()\n"
            "print('released', flush=True)\n"
        )
        process = subprocess.Popen(
            [sys.executable, '-B', '-u', '-c', code, str(path), str(self.app)],
            cwd=self.app, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop_lock_holder, process)
        ready = process.stdout.readline().strip()
        if ready != 'ready':
            stderr = process.stderr.read()
            self.fail('lock holder failed to start: ' + stderr)
        return process

    def stop_lock_holder(self, process):
        if process.poll() is None:
            try:
                process.stdin.write('x\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=15)

    def tree_snapshot(self, root):
        result = {}
        for path in sorted(Path(root).rglob('*')):
            relative = str(path.relative_to(root))
            if path.is_dir():
                result[relative] = ('directory', path.stat().st_mtime_ns)
            else:
                result[relative] = ('file', path.read_bytes(), path.stat().st_mtime_ns)
        return result

    def test_safe_path_requires_absolute_paths_and_confines_explicit_roots(self):
        self.assert_error('invalid_path', agent_control.safe_path,
                          'relative/score.mid', [self.assets])
        outside = self.root / 'outside.mid'
        outside.write_bytes(b'outside')
        self.assert_error('outside_roots', agent_control.safe_path,
                          str(outside), [self.assets])
        inside = self.assets / 'inside.mid'
        inside.write_bytes(b'inside')
        self.assertEqual(agent_control.safe_path(str(inside), [self.assets]), inside)

    def test_safe_path_rejects_symlink_and_windows_junction_reparse_attributes(self):
        target = self.assets / 'target'
        target.mkdir()
        file_path = target / 'score.mid'
        file_path.write_bytes(b'midi')

        link = self.assets / 'link'
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pass
        else:
            try:
                self.assert_error('unsafe_path', agent_control.safe_path,
                                  str(link / 'score.mid'), [self.assets])
            finally:
                if os.path.islink(link):
                    link.rmdir()

        # Also exercise the link branch when Windows policy denies symlink creation.
        missing_link = self.assets / 'simulated-link'
        missing_link.mkdir()
        with patch.object(Path, 'is_symlink', lambda path: path in (missing_link, agent_control.io_path(missing_link))):
            self.assert_error('unsafe_path', agent_control.safe_path,
                              str(missing_link / 'score.mid'), [self.assets])

        # Reparse-point creation is commonly denied to sandboxed test runners.
        # Exercise the exact Windows junction attribute without touching user paths.
        junction = self.assets / 'junction'
        junction.mkdir()
        original_lstat = Path.lstat

        def fake_lstat(path):
            if path in (junction, agent_control.io_path(junction)):
                real = original_lstat(path)
                return SimpleNamespace(st_mode=real.st_mode, st_file_attributes=1024)
            return original_lstat(path)

        with patch.object(Path, 'lstat', fake_lstat):
            self.assert_error('unsafe_path', agent_control.safe_path,
                              str(junction / 'score.mid'), [self.assets])

    def test_json_size_record_shape_and_object_ids_are_bounded(self):
        oversized = self.root / 'oversized.json'
        with oversized.open('wb') as handle:
            handle.truncate(agent_control.MAX_JSON + 1)
        self.assert_error('too_large', agent_control.read_json, oversized)

        array_record = self.root / 'array.json'
        write_json(array_record, [])
        self.assert_error('invalid_record', agent_control.read_json, array_record)
        self.assert_error('invalid_path', agent_control.read_json, 'array.json')

        valid_id = 'op_' + 'a' * 32
        write_json(self.state / 'operations' / (valid_id + '.json'),
                   {'schema': agent_control.SCHEMA, 'id': 'op_' + 'b' * 32,
                    'state': 'failed', 'request': {}})
        self.assert_error('invalid_record', self.control.operation_get, valid_id)
        for bad_id in ('op_' + 'a' * 31, 'op_' + 'g' * 32, '../' + valid_id,
                       'OP_' + 'a' * 32, None):
            self.assert_error('invalid_id', self.control.operation_get, bad_id)
        for bad_id in ('job_' + 'a' * 31, '../job_' + 'a' * 32, 7):
            self.assert_error('invalid_id', self.control.job_get, bad_id)

    def test_job_reads_do_not_write_job_or_user_cache_trees(self):
        job = self.jobs / 'fixture-job'
        job.mkdir()
        write_json(job / 'request.json', {'language': 'zh', 'name': 'fixture'})
        write_json(job / 'status.json', {'state': 'running', 'current_step': 'midi'})
        write_json(job / 'progress.json', {'stage': 'midi', 'detail': 'fixture'})
        (job / 'run.lock').write_bytes(b'0')
        (self.cache / 'private.json').write_text('{"user": true}', encoding='utf-8')
        jobs_before = self.tree_snapshot(self.jobs)
        cache_before = self.tree_snapshot(self.cache)

        listed = self.control.jobs_list()
        self.assertEqual(listed['total'], 1)
        job_id = listed['jobs'][0]['id']
        snapshot = self.control.job_get(job_id)
        self.assertTrue(snapshot['fixture_read_only'])
        self.assertEqual(snapshot['id'], job_id)
        self.assertEqual(self.tree_snapshot(self.jobs), jobs_before)
        self.assertEqual(self.tree_snapshot(self.cache), cache_before)

    def test_import_asset_is_an_immutable_copy_and_preserves_the_source(self):
        audio = self.assets / 'voice.wav'
        audio.write_bytes(b'audio')
        source = self.assets / 'source.svp'
        source_value = {
            'tracks': [{'mainRef': {'audio': {'filename': 'voice.wav'}}}]
        }
        source.write_text(json.dumps(source_value), encoding='utf-8')
        original = source.read_bytes()

        record = self.control.import_asset(str(source))
        snapshot_path = Path(record['path'])
        snapshot_bytes = snapshot_path.read_bytes()
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(snapshot_path.is_relative_to(self.state / 'inputs'))
        self.assertNotEqual(snapshot_path, source)
        snapshot_value = json.loads(snapshot_bytes.rstrip(b'\x00'))
        self.assertEqual(snapshot_value['tracks'][0]['mainRef']['audio']['filename'],
                         str(audio.resolve()))
        self.assertEqual(record['origin']['path'], str(source.resolve()))
        self.assertEqual(record['origin']['media_references'][0]['original'], 'voice.wav')

        self.assertEqual(self.control.import_asset(str(source)), record)
        source.write_bytes(original + b'\n')
        self.assertEqual(self.control.artifact(record['id']), record)
        self.assertEqual(snapshot_path.read_bytes(), snapshot_bytes)
        self.assertNotEqual(source.read_bytes(), original)

    def test_changed_artifact_digest_is_stale_and_verify_fails(self):
        path = self.assets / 'candidate.mid'
        path.write_bytes(b'AAAA')
        record = self.control.register_artifact(path)
        path.write_bytes(b'BBBB')
        self.assert_error('stale_artifact', self.control.artifact, record['id'])
        with patch.object(agent_control.subprocess, 'Popen', return_value=FakeProcess()):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': record['id']}, 'verify-stale')
        finished = self.control.run_operation(operation['id'])
        self.assertEqual(finished['state'], 'failed')
        self.assertEqual(finished['error']['code'], 'receipt_mismatch')

    def test_idempotency_reuses_the_request_and_rejects_changed_arguments(self):
        arguments = {'artifact_id': 'art_' + 'a' * 32}
        with patch.object(agent_control.subprocess, 'Popen',
                          return_value=FakeProcess()) as popen:
            first = self.control.start_operation(
                'verify_artifact', arguments, 'same-request')
            second = self.new_control().start_operation(
                'verify_artifact', arguments, 'same-request')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(popen.call_count, 1)
        self.assert_error('idempotency_conflict', self.control.start_operation,
                          'verify_artifact', {'artifact_id': 'art_' + 'b' * 32},
                          'same-request')
        for key in ('', 'x' * 129, None, 3):
            self.assert_error('invalid_idempotency_key', self.control.start_operation,
                              'verify_artifact', arguments, key)

    def test_real_sidecar_lock_is_exclusive_and_released(self):
        lock = self.root / 'locks' / 'exclusive.lock'
        holder = self.start_lock_holder(lock)
        self.assertTrue(agent_control.lock_held(lock))
        self.assert_error('busy', self._nonblocking_lock, lock)
        holder.stdin.write('release\n')
        holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), 'released')
        holder.communicate(timeout=15)
        self.assertFalse(agent_control.lock_held(lock))

    @staticmethod
    def _nonblocking_lock(path):
        with agent_control.file_lock(path, blocking=False):
            pass

    def test_held_worker_lock_prevents_a_false_interruption(self):
        with patch.object(agent_control.subprocess, 'Popen', return_value=FakeProcess()):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': 'art_' + 'c' * 32}, 'lock-crash')
        self.control.update_operation(operation['id'], created=time.time() - 31)
        run_lock = self.state / 'operations' / operation['id'] / 'run.lock'
        holder = self.start_lock_holder(run_lock)
        self.assertEqual(self.control.operation_get(operation['id'])['state'], 'queued')
        holder.stdin.write('release\n')
        holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), 'released')
        holder.communicate(timeout=15)
        crashed = self.control.operation_get(operation['id'])
        self.assertEqual(crashed['state'], 'interrupted')
        self.assertEqual(crashed['error']['code'], 'worker_interrupted')

    def test_cancel_is_cooperative_and_terminal_state_stays_terminal(self):
        with patch.object(agent_control.subprocess, 'Popen', return_value=FakeProcess()):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': 'art_' + 'd' * 32}, 'cancel-me')
        requested = self.control.operation_cancel(operation['id'])
        self.assertEqual(requested['state'], 'queued')
        self.assertTrue(requested['cancel_requested'])
        cancel_path = self.state / 'operations' / operation['id'] / 'cancel.request'
        cancel_bytes = cancel_path.read_bytes()

        cancelled = self.control.run_operation(operation['id'])
        self.assertEqual(cancelled['state'], 'cancelled')
        terminal = self.control.operation_cancel(operation['id'])
        self.assertEqual(terminal['state'], 'cancelled')
        self.assertEqual(cancel_path.read_bytes(), cancel_bytes)

    def test_spawn_failure_is_distinct_and_terminal_cancel_is_a_noop(self):
        with patch.object(agent_control.subprocess, 'Popen',
                          side_effect=OSError('fixture launch denied')):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': 'art_' + 'e' * 32}, 'spawn-fail')
        self.assertEqual(operation['state'], 'failed')
        self.assertEqual(operation['error']['code'], 'spawn_failed')
        cancel_path = self.state / 'operations' / operation['id'] / 'cancel.request'
        result = self.control.operation_cancel(operation['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(cancel_path.exists())

    def test_changed_code_revision_rejects_old_plan_and_pending_operation(self):
        source = self.assets / 'input.mid'
        source.write_bytes(b'MThd fixture')
        record = self.control.import_asset(str(source))
        plan = self.control.make_plan(
            'score', record['id'], expected_sha256=record['sha256'], edits=[])
        with patch.object(agent_control.subprocess, 'Popen', return_value=FakeProcess()):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': record['id']}, 'old-code')

        fixture = self.app / 'agent_score.py'
        fixture.write_text(fixture.read_text(encoding='utf-8') + '\n# revision changed\n',
                           encoding='utf-8')
        changed = self.new_control()
        self.assertNotEqual(changed.code_revision, self.control.code_revision)
        self.assert_error('stale_plan', changed.start_plan, plan['id'], 'score', 'new-plan')
        failed = changed.run_operation(operation['id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['error']['code'], 'stale_code')


    def test_successful_terminal_state_is_not_rewritten_by_late_cancel(self):
        path = self.assets / 'verified.mid'
        path.write_bytes(b'verified')
        record = self.control.register_artifact(path)
        with patch.object(agent_control.subprocess, 'Popen', return_value=FakeProcess()):
            operation = self.control.start_operation(
                'verify_artifact', {'artifact_id': record['id']}, 'successful-verify')
        finished = self.control.run_operation(operation['id'])
        self.assertEqual(finished['state'], 'succeeded')
        self.assertTrue(finished['result']['verified'])
        late = self.control.operation_cancel(operation['id'])
        self.assertEqual(late['state'], 'succeeded')
        self.assertFalse(late['cancel_requested'])
    def test_invalid_configuration_is_rejected_as_a_control_error(self):
        value = read_json(self.config_path)
        value.pop('worker_python')
        broken = self.root / 'missing-field.json'
        write_json(broken, value)
        self.assert_error('invalid_config', self.new_control, broken)

    def test_unowned_job_cannot_execute_arbitrary_request_tools(self):
        job = self.jobs / 'unowned'
        write_json(job/'request.json', {'language':'zh','tools':{'python':'C:/untrusted/python.exe'}})
        identifier = 'job_' + agent_control.digest(str(job.resolve()))[:32]
        with patch.object(agent_control.subprocess,'Popen') as spawn:
            self.assert_error('not_found',self.control.preparation_start,identifier,None,'unowned')
            spawn.assert_not_called()

    def test_preparation_failure_partial_and_cancel_are_not_upgraded(self):
        job = self.jobs / 'owned-preparation'
        write_json(job/'request.json',{'language':'zh'})
        identifier = 'job_' + agent_control.digest(str(job.resolve()))[:32]
        owned = {'schema':1,'id':identifier,'path':str(job.resolve()),
            'request_sha256':agent_control.file_hash(job/'request.json'),
            'code_revision':self.control.code_revision,'plan_id':'fixture'}
        agent_control.atomic_json(self.state/'preparations'/(identifier+'.json'),owned)
        for status,expected in (('failed','failed'),('cancelled','cancelled'),
                                ('partial','failed'),('done','failed')):
            with self.subTest(status=status):
                with patch.object(agent_control.subprocess,'Popen',return_value=FakeProcess()):
                    operation=self.control.preparation_start(identifier,None,'status-'+status)
                receipt={'status':status,'job_path':str(job),'files':[],
                         'error':{'code':'fixture','message':'fixture state '+status}}
                module=SimpleNamespace(run_preparation=lambda *args,**kwargs:receipt)
                with patch.dict(sys.modules,{'agent_preparation':module}):
                    finished=self.control.run_operation(operation['id'])
                self.assertEqual(finished['state'],expected)
                self.assertEqual(finished['business_receipt']['status'],status)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing violations')
    def test_atomic_record_retries_a_transient_windows_reader(self):
        path = self.state / 'reader-race.json'
        original = agent_control.os.replace
        attempts = []
        def replace(source, destination):
            attempts.append(1)
            if len(attempts) == 1:
                error = PermissionError(13, 'fixture reader holds a sharing lock')
                error.winerror = 32
                raise error
            return original(source, destination)
        with patch.object(agent_control.os, 'replace', replace):
            agent_control.atomic_json(path, {'state': 'committed'})
        self.assertEqual(agent_control.read_json(path), {'state': 'committed'})
        self.assertEqual(len(attempts), 2)

    def test_malformed_svp_track_is_rejected_as_invalid_score(self):
        source = self.assets / 'malformed.svp'
        write_json(source, {'tracks': [{'mainRef': None}]})
        self.assert_error('invalid_score', self.control.import_asset, str(source))

    def test_malformed_job_artifact_entry_is_rejected_as_invalid_record(self):
        job = self.jobs / 'malformed-artifacts'
        job.mkdir()
        write_json(job / 'request.json', {'language': 'zh'})
        job_id = self.control.jobs_list()['jobs'][0]['id']
        write_json(job / 'artifacts.json', {'entries': {'bad': ['not', 'a', 'mapping']}})
        self.assert_error('invalid_record', self.control.artifacts_list, job_id)


if __name__ == '__main__':
    unittest.main(verbosity=2)
