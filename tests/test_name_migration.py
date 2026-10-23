"""VWA naming compatibility, dual locks and launcher-alias migration."""
import copy
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))
assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', 'Use tests/run_tests.py'
from common import canonical_kind, environment_value
import process_control
from PyQt5.QtCore import QLockFile
from tools import update_apply as apply
from tools.build_app_update import require_compatible_clients
import tuning_workflow
tuning_module = Path(tuning_workflow.__file__).resolve()
tuning_source = (APP / 'src/tuning_workflow.py').resolve()
assert tuning_module in {tuning_source, (APP / 'tuning_workflow.py').resolve()}
assert tuning_module.read_bytes() == tuning_source.read_bytes()

WORK = APP / 'work'
WORK.mkdir(parents=True, exist_ok=True)
MIGRATION = {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True}
OLD_LAUNCHER = b'MZ-old-bvwa-launcher-fixture; never execute'
NEW_LAUNCHER = b'MZ-new-vwa-launcher-fixture; never execute'
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_ARCHIVE = 0x20
PENDING_NAMES = ('version.json.vwa-update', 'version.json.bvwa-update')


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write_tree(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def file_attributes(path):
    return os.stat(path).st_file_attributes if os.name == 'nt' else None


def set_file_attributes(path, value):
    if os.name != 'nt':
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetFileAttributesW.argtypes = (ctypes.c_wchar_p, ctypes.c_ulong)
    if not kernel.SetFileAttributesW(str(path), value):
        raise ctypes.WinError(ctypes.get_last_error())


def write_tuning_svp(path):
    """Minimal real SVP shape from test_tuning_workflow.py's session fixture."""
    note_duration = 705600000
    project = {
        'version': '1.0.0',
        'uuid': 'legacy-kind-session-fixture',
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}],
        },
        'tracks': [{
            'name': 'Lead Vocal',
            'mainRef': {'blickOffset': 0, 'pitchOffset': 0, 'isInstrumental': False,
                        'database': {'name': 'Test Bank'}, 'voice': {'preset': 'fixture'}},
            'mainGroup': {'uuid': 'lead-group', 'notes': [
                {'onset': 0, 'duration': note_duration, 'pitch': 60, 'lyrics': 'la'},
                {'onset': note_duration, 'duration': note_duration, 'pitch': 64, 'lyrics': 'la'},
            ], 'parameters': {}},
            'groups': [],
        }],
    }
    payload = json.dumps(project, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\x00'
    path.write_bytes(payload)
    return path


# VWA names win, while released BVWA callers remain supported.
with patch.dict(os.environ, {'VWA_TESTING': 'vwa', 'BVWA_TESTING': 'bvwa'}):
    assert environment_value('VWA_TESTING', 'fallback') == 'vwa'
with patch.dict(os.environ, {'BVWA_TESTING': 'legacy-only'}):
    os.environ.pop('VWA_TESTING', None)
    assert environment_value('VWA_TESTING', 'fallback') == 'legacy-only'
with patch.dict(os.environ, {'VWA_TESTING': ''}):
    assert environment_value('VWA_TESTING', 'fallback') == ''
with patch.dict(os.environ, {'BVWA_SAMPLE': 'alias'}):
    os.environ.pop('VWA_SAMPLE', None)
    assert environment_value('VWA_SAMPLE', default='fallback') == 'alias'
with patch.dict(os.environ, {'VWA_SAMPLE': 'preferred', 'BVWA_SAMPLE': 'alias'}):
    assert environment_value('VWA_SAMPLE', default='fallback') == 'preferred'
with patch.dict(os.environ, {'VWA_SAMPLE': 'present'}):
    assert environment_value('VWA_SAMPLE', default='fallback') == 'present'
assert environment_value('VWA_DEFINITELY_ABSENT_FOR_NAME_MIGRATION', 'default') == 'default'

# Legacy signed record types compare as VWA without changing their bytes or dict shape.
assert canonical_kind('bvwa.tuning-session') == 'vwa.tuning-session'
assert canonical_kind('bvwa.calibration-commit') == 'vwa.calibration-commit'
assert canonical_kind('vwa.tuning-session') == 'vwa.tuning-session'
assert canonical_kind('dwa.tuning-session') == 'dwa.tuning-session'
assert canonical_kind(None) is None
legacy_record = {'kind': 'bvwa.tuning-session', 'signature': 'unchanged'}
serialized = json.dumps(legacy_record, sort_keys=True)
assert canonical_kind(legacy_record['kind']) == 'vwa.tuning-session'
assert legacy_record == {'kind': 'bvwa.tuning-session', 'signature': 'unchanged'}
assert json.dumps(legacy_record, sort_keys=True) == serialized

# Both canonical and released launchers accept the VWA/BVWA archive families.
for name in ('VWA-App-Update.zip', 'VWA-Program-Update.zip',
             'VWA-App-Update-v1.2.3.zip', 'VWA-Program-Update-v1.2.3.zip',
             'BVWA-App-Update.zip', 'BVWA-Program-Update.zip',
             'BVWA-App-Update-v1.2.3.zip', 'BVWA-Program-Update-v1.2.3.zip'):
    assert apply.program_name({'name': name}) == name
for name in ('VWA-Runtime-Upgrade.zip', 'VWA-Runtime-Upgrade-v1.2.3.zip',
             'BVWA-Runtime-Upgrade.zip', 'BVWA-Runtime-Upgrade-v1.2.3.zip'):
    assert apply._runtime_name({'name': name}) == name

bad_program_names = (
    '../VWA-App-Update.zip', '..\\VWA-App-Update.zip', 'VWA-App-Update.zip/../../secret',
    'VWA-App-Update.zip\\..\\..\\secret', 'VWA-App-Update.zip.exe',
    'VWA-App-Update-v1.2.zip', 'DWA-App-Update.zip', 'vwa-app-update.zip',
    'VWA-App-Update-v1.2.3.zip:stream', 'VWA-App-Update-v1.2.3.zip/extra', '', None,
)
bad_runtime_names = (
    '../VWA-Runtime-Upgrade.zip', '..\\VWA-Runtime-Upgrade.zip',
    'VWA-Runtime-Upgrade.zip/../../secret', 'VWA-Runtime-Upgrade.zip\\..\\..\\secret',
    'VWA-Runtime-Upgrade-v1.2.zip', 'VWA-Runtime-Upgrade-v1.2.3.exe',
    'BVWA-Runtime-Upgrade-v1.2.3.zip.exe', 'Other-Runtime-Upgrade.zip',
    'vwa-runtime-upgrade.zip', 'VWA-Runtime-Upgrade-v1.2.3.zip:stream', '',
)
for name in bad_program_names:
    try:
        apply.program_name({'name': name})
        raise AssertionError('Unsafe program archive name accepted: %r' % (name,))
    except ValueError:
        pass
for name in bad_runtime_names:
    try:
        apply._runtime_name({'name': name})
        raise AssertionError('Unsafe runtime archive name accepted: %r' % (name,))
    except ValueError:
        pass
for function in (apply.program_name, apply._runtime_name):
    try:
        function([])
        raise AssertionError('Non-object release metadata accepted')
    except (AttributeError, ValueError):
        pass

assert {'VWA.exe', 'BVWA.exe'}.issubset(apply.ROOT_FILES)
try:
    require_compatible_clients([record('VWA.exe', NEW_LAUNCHER)])
    raise AssertionError('A frozen old client accepted a direct VWA.exe update')
except ValueError as error:
    assert 'first hop' in str(error)
require_compatible_clients([record('BVWA.exe', OLD_LAUNCHER)])

with tempfile.TemporaryDirectory(prefix='nm-', dir=WORK) as folder:
    scratch = Path(folder)
    lock_root = scratch / 'locks'
    guard = apply.application_lock(lock_root)
    assert guard is not None and guard.isLocked()
    try:
        assert (lock_root / 'cache/BVWA.lock').is_file()
        assert (lock_root / 'cache/VWA.lock').is_file()
        raw_old = QLockFile(str(lock_root / 'cache/BVWA.lock'))
        raw_new = QLockFile(str(lock_root / 'cache/VWA.lock'))
        assert not raw_old.tryLock(0)
        assert not raw_new.tryLock(0)
        assert process_control.application_lock(lock_root) is None
    finally:
        guard.unlock()
    assert not guard.isLocked()

    for filename in ('BVWA.lock', 'VWA.lock'):
        raw = QLockFile(str(lock_root / 'cache' / filename))
        assert raw.tryLock(0), 'A released raw lock must be distinguishable from a stale file'
        try:
            assert apply.application_lock(lock_root) is None, (
                'New guard failed to exclude raw owner ' + filename)
            assert process_control.application_lock(lock_root) is None, (
                'Delegating guard failed to exclude raw owner ' + filename)
        finally:
            raw.unlock()
    delegated = process_control.application_lock(lock_root)
    assert delegated is not None and delegated.isLocked()
    assert apply.application_lock(lock_root) is None
    delegated.unlock()
    assert not delegated.isLocked()

    # Exercise the real session loader with a legacy kind in the persisted JSON.
    tuning_root = scratch / 'legacy-tuning-session'
    tuning_root.mkdir()
    source = write_tuning_svp(tuning_root / 'source.svp')
    source_bytes = source.read_bytes()
    session_path = tuning_workflow.create_session(source, tuning_root / 'session')
    session = json.loads(session_path.read_text(encoding='utf-8'))
    assert session['kind'] == 'vwa.tuning-session'
    input_snapshot = session_path.parent / session['input_file']
    input_snapshot_bytes = input_snapshot.read_bytes()
    assert input_snapshot_bytes == source_bytes
    original_session_bytes = session_path.read_bytes()
    current_token = b'"kind": "vwa.tuning-session"'
    legacy_token = b'"kind": "bvwa.tuning-session"'
    assert original_session_bytes.count(current_token) == 1
    legacy_bytes = original_session_bytes.replace(current_token, legacy_token, 1)
    session_path.write_bytes(legacy_bytes)
    legacy_bytes_snapshot = session_path.read_bytes()
    parsed = tuning_workflow.read_session(session_path)
    assert parsed['kind'] == 'bvwa.tuning-session'
    assert parsed['input_sha256'] == hashlib.sha256(source_bytes).hexdigest()
    assert session_path.read_bytes() == legacy_bytes_snapshot
    assert legacy_token in session_path.read_bytes()
    assert source.read_bytes() == source_bytes
    assert input_snapshot.read_bytes() == input_snapshot_bytes

    model_sentinel = b'unchanged shared model sentinel'
    old_engine = b'old runtime engine'
    new_engine = b'new runtime engine'
    private_files = {
        'config.json': b'{"private":"settings"}',
        'jobs/private-song/song.mid': b'private tuned MIDI',
        'cache/publish-browser/login.txt': b'private account session',
        'dependencies/optional/private-model.bin': b'private optional model',
    }
    protected_names = ('app.py', 'version.json', 'dependencies/manifest.json',
                       'dependencies/runtime/engine.py', 'models/original.bin', *private_files)

    def seed_install(root, alias_attributes):
        base = {name: data for name, data in (
            ('models/original.bin', model_sentinel),
            ('dependencies/runtime/engine.py', old_engine),
        )}
        base_manifest = json.dumps({'entries': [record(name, data) for name, data in base.items()]}).encode()
        write_tree(root, {**base, **private_files,
                          'dependencies/manifest.json': base_manifest,
                          'app.py': b'old application',
                          'version.json': b'{"version":"0.3.0"}'})
        if alias_attributes is not None:
            alias = root / 'BVWA.exe'
            alias.write_bytes(OLD_LAUNCHER)
            set_file_attributes(alias, alias_attributes)

    def build_archive(migration=MIGRATION, include_bvwa_payload=False):
        unchanged = {'models/original.bin': model_sentinel}
        changes = {'dependencies/runtime/engine.py': new_engine}
        base_manifest = json.dumps({'entries': [record(name, data) for name, data in {
            **unchanged, 'dependencies/runtime/engine.py': old_engine}.items()]}).encode()
        target_manifest = json.dumps({'entries': [record(name, data) for name, data in
                                                   {**unchanged, **changes}.items()]}).encode()
        files = {
            **changes,
            'app.py': b'new application',
            'VWA.exe': NEW_LAUNCHER,
            'version.json': b'{"version":"0.4.0","channel":"stable"}',
            'dependencies/manifest.json': target_manifest,
        }
        if include_bvwa_payload:
            files['BVWA.exe'] = NEW_LAUNCHER
        plan = {
            'format': 1,
            'version': '0.4.0',
            'source_runtime_sha256': hashlib.sha256(base_manifest).hexdigest(),
            'target_runtime_sha256': hashlib.sha256(target_manifest).hexdigest(),
            'entries': [record(name, files[name]) for name in sorted(files)],
            'removed': [],
        }
        if migration is not None:
            plan['launcher_migration'] = copy.deepcopy(migration)
            assert plan['launcher_migration'] == migration
        else:
            assert 'launcher_migration' not in plan
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('runtime-upgrade.json', json.dumps(plan))
        raw = buffer.getvalue()
        item = {
            'name': 'VWA-Runtime-Upgrade-v0.4.0.zip',
            'version': '0.4.0',
            'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'source_runtime_sha256': plan['source_runtime_sha256'],
            'target_runtime_sha256': plan['target_runtime_sha256'],
        }

        def download(selected, target, *args):
            target.mkdir(parents=True, exist_ok=True)
            path = target / selected['name']
            path.write_bytes(raw)
            return path
        return item, download, files

    old_root = scratch / 'old'
    hidden_root = scratch / 'hidden'
    fresh_root = scratch / 'fresh'
    prepared_by_root = {}
    initial_attributes = {}
    installations = (
        (old_root, FILE_ATTRIBUTE_ARCHIVE),
        (hidden_root, FILE_ATTRIBUTE_ARCHIVE | FILE_ATTRIBUTE_HIDDEN),
        (fresh_root, None),
    )
    for root, alias_attributes in installations:
        seed_install(root, alias_attributes)
        if alias_attributes is not None:
            initial_attributes[root] = file_attributes(root / 'BVWA.exe')
        item, download, target_files = build_archive()
        prepared = apply.prepare_runtime_upgrade(item, root, downloader=download)
        assert (root / 'app.py').read_bytes() == b'old application'
        assert (root / 'dependencies/runtime/engine.py').read_bytes() == old_engine
        assert not (root / 'VWA.exe').exists()
        if alias_attributes is None:
            assert not (root / 'BVWA.exe').exists()
        else:
            assert (root / 'BVWA.exe').read_bytes() == OLD_LAUNCHER
            if os.name == 'nt':
                assert file_attributes(root / 'BVWA.exe') == alias_attributes
        prepared_by_root[root] = (prepared, target_files)

    old_plan, target_files = prepared_by_root[old_root]
    hidden_plan, hidden_target_files = prepared_by_root[hidden_root]
    assert hidden_target_files == target_files
    real_replace, real_os_replace = Path.replace, os.replace

    def force_version_commit_failure(root, plan_path):
        alias = root / 'BVWA.exe'
        alias_before = alias.read_bytes()
        attributes_before = file_attributes(alias)
        protected_before = {name: (root / name).read_bytes() for name in protected_names}
        observed = {}

        def stop_before_version(source):
            if Path(source).name not in PENDING_NAMES:
                return False
            observed['launcher'] = alias.read_bytes()
            observed['attributes'] = file_attributes(alias)
            return True

        def fail_version_commit(source, *args, **kwargs):
            if stop_before_version(source):
                raise PermissionError('forced version commit failure')
            return real_replace(source, *args, **kwargs)

        def fail_version_commit_os(source, target):
            if stop_before_version(source):
                raise PermissionError('forced version commit failure')
            return real_os_replace(source, target)

        with patch.object(Path, 'replace', fail_version_commit), patch.object(os, 'replace', fail_version_commit_os):
            try:
                apply.apply_update(plan_path)
                raise AssertionError('Forced version commit failure was accepted')
            except PermissionError as error:
                assert 'forced version commit failure' in str(error)

        assert observed.get('launcher') == NEW_LAUNCHER, (
            'Alias migration must complete before the version commit can be retried')
        if os.name == 'nt':
            assert observed.get('attributes') & FILE_ATTRIBUTE_HIDDEN, (
                'The migrated alias must be hidden before the version commit')
        assert plan_path.is_file(), 'A failed apply must leave the staged plan retryable'
        assert alias.read_bytes() == alias_before
        assert file_attributes(alias) == attributes_before
        assert not (root / 'VWA.exe').exists()
        assert not list(root.rglob('*.vwa-update')) and not list(root.rglob('*.bvwa-update'))
        assert all((root / name).read_bytes() == data for name, data in protected_before.items())

    def retry_and_assert(root, plan_path, expected_files):
        result = apply.apply_update(plan_path)
        assert result['state'] == 'passed' and result['runtime_upgraded']
        assert (root / 'VWA.exe').read_bytes() == NEW_LAUNCHER
        assert (root / 'BVWA.exe').read_bytes() == NEW_LAUNCHER
        if os.name == 'nt':
            assert file_attributes(root / 'BVWA.exe') & FILE_ATTRIBUTE_HIDDEN, (
                'The retained BVWA alias must be hidden on Windows')
        assert all((root / name).read_bytes() == data for name, data in expected_files.items())
        assert (root / 'models/original.bin').read_bytes() == model_sentinel
        assert all((root / name).read_bytes() == data for name, data in private_files.items())

    force_version_commit_failure(old_root, old_plan)
    retry_and_assert(old_root, old_plan, target_files)

    if os.name == 'nt':
        assert initial_attributes[hidden_root] & FILE_ATTRIBUTE_HIDDEN
    force_version_commit_failure(hidden_root, hidden_plan)
    if os.name == 'nt':
        assert file_attributes(hidden_root / 'BVWA.exe') == initial_attributes[hidden_root]
    retry_and_assert(hidden_root, hidden_plan, hidden_target_files)

    fresh_plan, _ = prepared_by_root[fresh_root]
    fresh_result = apply.apply_update(fresh_plan)
    assert fresh_result['state'] == 'passed' and fresh_result['runtime_upgraded']
    assert (fresh_root / 'VWA.exe').read_bytes() == NEW_LAUNCHER
    assert not (fresh_root / 'BVWA.exe').exists(), 'A fresh install must not gain a legacy alias'
    assert (fresh_root / 'models/original.bin').read_bytes() == model_sentinel
    assert all((fresh_root / name).read_bytes() == data for name, data in private_files.items())

    bad_migrations = (
        {'from': 'Other.exe', 'to': 'VWA.exe', 'legacy_alias': True},
        {'from': '../BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True},
        {'from': 'BVWA.exe', 'to': 'Other.exe', 'legacy_alias': True},
        {'from': 'BVWA.exe', 'to': '../VWA.exe', 'legacy_alias': True},
        {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': False},
        {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': 1},
        {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True, 'delete_old': True},
    )
    for index, migration in enumerate(bad_migrations):
        root = scratch / ('invalid-' + str(index))
        seed_install(root, FILE_ATTRIBUTE_ARCHIVE)
        before = {name: (root / name).read_bytes() for name in protected_names}
        attrs = file_attributes(root / 'BVWA.exe')
        item, download, _ = build_archive(migration)
        rejected = False
        try:
            prepared = apply.prepare_runtime_upgrade(item, root, downloader=download)
        except ValueError:
            rejected = True
        else:
            try:
                apply.apply_update(prepared)
            except ValueError:
                rejected = True
        assert rejected, 'Unknown launcher migration was accepted: %r' % (migration,)
        assert not (root / 'VWA.exe').exists()
        assert not (root / 'Other.exe').exists()
        assert (root / 'BVWA.exe').read_bytes() == OLD_LAUNCHER
        assert file_attributes(root / 'BVWA.exe') == attrs
        assert all((root / name).read_bytes() == data for name, data in before.items())

    both_root = scratch / 'invalid-both-launchers'
    seed_install(both_root, FILE_ATTRIBUTE_ARCHIVE)
    both_before = {name: (both_root / name).read_bytes() for name in protected_names}
    both_attributes = file_attributes(both_root / 'BVWA.exe')
    item, download, _ = build_archive(include_bvwa_payload=True)
    rejected = False
    try:
        prepared = apply.prepare_runtime_upgrade(item, both_root, downloader=download)
    except ValueError:
        rejected = True
    else:
        try:
            apply.apply_update(prepared)
        except ValueError:
            rejected = True
    assert rejected, 'A runtime payload containing both launchers was accepted'
    assert not (both_root / 'VWA.exe').exists()
    assert (both_root / 'BVWA.exe').read_bytes() == OLD_LAUNCHER
    assert file_attributes(both_root / 'BVWA.exe') == both_attributes
    assert all((both_root / name).read_bytes() == data for name, data in both_before.items())

    missing_root = scratch / 'missing'
    seed_install(missing_root, FILE_ATTRIBUTE_ARCHIVE)
    missing_before = {name: (missing_root / name).read_bytes() for name in protected_names}
    missing_attributes = file_attributes(missing_root / 'BVWA.exe')
    item, download, _ = build_archive(migration=None)
    rejected = False
    try:
        prepared = apply.prepare_runtime_upgrade(item, missing_root, downloader=download)
    except ValueError as error:
        assert '迁移声明' in str(error)
        rejected = True
    else:
        try:
            apply.apply_update(prepared)
        except ValueError as error:
            assert '迁移声明' in str(error)
            rejected = True
    assert rejected, 'A VWA runtime payload without its migration declaration was accepted'
    assert not (missing_root / 'VWA.exe').exists()
    assert (missing_root / 'BVWA.exe').read_bytes() == OLD_LAUNCHER
    assert file_attributes(missing_root / 'BVWA.exe') == missing_attributes
    assert all((missing_root / name).read_bytes() == data for name, data in missing_before.items())

print('PASS: VWA compatibility, legacy session loading, dual locks, strict names and launcher migration rollback')
