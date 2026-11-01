"""VWA 1.1.0 source identity, shared-runtime routing and candidate-build guards."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

APP = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(APP))

from tools import build_runtime_upgrade as runtime_builder
from tools import build_update_catalog as catalog_builder
from tools.build_runtime_upgrade import build_bootstrap, source_identity
from tools.build_update_catalog import build as build_catalog, verify_catalog_assets
from tools.update_apply import _normalise_source_policy, select_update_route


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def source_record(version, runtime_sha, seed):
    files = {
        'app.py': hashlib.sha256((seed + ' app').encode()).hexdigest(),
        'updater.py': hashlib.sha256((seed + ' updater').encode()).hexdigest(),
        'updates_ui.py': hashlib.sha256((seed + ' updates').encode()).hexdigest(),
        'tools/update_apply.py': hashlib.sha256((seed + ' helper').encode()).hexdigest(),
    }
    return {'version': version, 'runtime_sha256': runtime_sha, 'files': files}


def make_route(folder, target_manifest, target_sha, sources, *, runtime_name,
               bootstrap_name, legacy_name=None, policy_override=None,
               plan_policy_override=None, helper_override=None):
    manifest = json.loads(target_manifest)['entries']
    target_file = b'target runtime file'
    manifest.append(record('dependencies/runtime/target.bin', target_file))
    target_bytes = json.dumps({'entries': sorted(manifest, key=lambda item: item['file'])}).encode()
    target_path = folder / 'explicit-target.json'
    target_path.write_bytes(target_bytes)
    actual_target_sha = hashlib.sha256(target_bytes).hexdigest()
    helper = helper_override if helper_override is not None else (APP / 'tools/update_apply.py').read_bytes()
    payload = {
        'app.py': b'# target app',
        'updater.py': b'# target updater',
        'updates_ui.py': b'# target updates ui',
        'tools/update_apply.py': helper,
        'version.json': json.dumps({'version': '1.1.0', 'channel': 'stable'}).encode(),
        'dependencies/manifest.json': target_bytes,
        'dependencies/runtime/target.bin': target_file,
    }
    policy = {'format': 1, 'allowed_sources': [
        source_record(version, sha, version) for version, sha in sources]}
    if policy_override is not None:
        policy = policy_override
    source_sha = sources[0][1]
    plan = {'format': 1, 'version': '1.1.0', 'source_runtime_sha256': source_sha,
            'target_runtime_sha256': actual_target_sha,
            'source_policy': plan_policy_override if plan_policy_override is not None else policy,
            'entries': [record(name, data) for name, data in payload.items()], 'removed': []}
    runtime_path = folder / runtime_name
    with zipfile.ZipFile(runtime_path, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in payload.items():
            zipped.writestr(name, data)
        zipped.writestr('runtime-upgrade.json', json.dumps(plan))
    item = {'name': runtime_name, 'version': '1.1.0', 'bytes': runtime_path.stat().st_size,
            'sha256': hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            'source_runtime_sha256': source_sha, 'target_runtime_sha256': actual_target_sha,
            'source_policy': policy}
    update = build_bootstrap(folder, item, filename=bootstrap_name)
    legacy = None
    if legacy_name:
        shutil.copy2(folder / bootstrap_name, folder / legacy_name)
        legacy = dict(update, name=legacy_name)
    report = {'update': update, 'runtime_upgrade': item,
              'source_versions': [version for version, _ in sources]}
    if legacy:
        report['legacy_update'] = legacy
    return report, target_path, actual_target_sha


# The identity contract hashes installed POSIX paths from the frozen release
# distribution, never mutable user configuration or candidate binaries. This
# fixture reader keeps the source test independent of an external .git.
fixture_client = APP / 'tests/fixtures/update-client-1.0.0'
fixture_hashes = {
    'app.py': 'fac7084b6621ae5ec0dfd119164dca76d8e77548f33cdb2a28e4b68d8c2be64e',
    'updater.py': '5772f3cb6b51310a0bb71731a6c9b139a295ac59c8e2f670130a81b1f74b2c4f',
    'updates_ui.py': '57dbf54031a17f6ba0866d823bf224d1a37c01d21825e6d5c8597727047638e2',
    'tools/update_apply.py': '84a9550707bf65c2d77e7fb56911d03a7e560b71cebc08a34a2c2dbba6dfdd1d',
}
fixture_sources = {
    'app.py': 'src/app.py',
    'updater.py': 'src/updater.py',
    'updates_ui.py': 'src/updates_ui.py',
    'tools/update_apply.py': 'tools/update_apply.py',
}
fixture_files = {
    'src/app.py': 'app.py',
    'src/updater.py': 'updater.py',
    'src/updates_ui.py': 'updates_ui.py',
    'tools/update_apply.py': 'update_apply.py',
}
runtime_manifest = (APP / 'dependencies/manifest.json').read_bytes()
assert hashlib.sha256(runtime_manifest).hexdigest() == '61a99c9f30945dab1ce1c2dd5f64567f909268856544f83b279231a20ea1a23c'
fixture_blobs = {
    'packaging/version.json': json.dumps({'version': '1.0.0', 'channel': 'stable'}).encode('utf-8'),
    'dependencies/manifest.json': runtime_manifest,
    'packaging/portable-files.json': json.dumps({'format': 1, 'entries': [
        {'file': installed, 'source': source} for installed, source in fixture_sources.items()
    ]}, ensure_ascii=False).encode('utf-8'),
}
for source, fixture in fixture_files.items():
    data = (fixture_client / fixture).read_bytes()
    assert hashlib.sha256(data).hexdigest() == fixture_hashes[next(
        installed for installed, candidate in fixture_sources.items() if candidate == source)]
    fixture_blobs[source] = data


def fixture_reader(ref, path):
    assert ref == 'fixture-1.0.0' and path in fixture_blobs, (ref, path)
    return fixture_blobs[path]


identity = source_identity('fixture-1.0.0', '1.0.0', reader=fixture_reader)
assert identity == {
    'version': '1.0.0',
    'runtime_sha256': '61a99c9f30945dab1ce1c2dd5f64567f909268856544f83b279231a20ea1a23c',
    'files': fixture_hashes,
}
assert _normalise_source_policy({'format': 1, 'allowed_sources': [identity]}) == {
    'format': 1, 'allowed_sources': [identity]}
unsafe_identity = dict(identity)
unsafe_identity['files'] = dict(identity['files'], **{'config.json': '0' * 64})
try:
    catalog_builder._validate_source_policy(
        {'source_policy': {'format': 1, 'allowed_sources': [unsafe_identity]}},
        ['1.0.0'], identity['runtime_sha256'], required=True)
    raise AssertionError('User configuration was accepted as source identity')
except ValueError as error:
    assert 'invalid program hash' in str(error)
try:
    source_identity('fixture-1.0.0', '0.4.1', reader=fixture_reader)
    raise AssertionError('A frozen ref was accepted under a forged version label')
except ValueError as error:
    assert 'contains version 1.0.0' in str(error)

# A shared full/JPN runtime route carries only manifest changes. The optional
# Chinese weights stay in the complete manifest but never enter an unchanged
# delta, so a JPN source remains a JPN installation rather than gaining Qwen.
qwen_name = 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/model.onnx'
unchanged_qwen = record(qwen_name, b'unchanged qwen weights')
old_engine = record('dependencies/runtime/engine.bin', b'old')
new_engine = record('dependencies/runtime/engine.bin', b'new')
added_runtime = record('dependencies/runtime/new.bin', b'new file')
removed_runtime = record('dependencies/runtime/old-only.bin', b'old file')
source_snapshot = {
    'runtime_entries': {
        qwen_name: unchanged_qwen,
        old_engine['file']: old_engine,
        removed_runtime['file']: removed_runtime,
    }
}
target_entries = {
    qwen_name: unchanged_qwen,
    new_engine['file']: new_engine,
    added_runtime['file']: added_runtime,
}
changed_runtime, removed_records = runtime_builder._runtime_changes(
    [source_snapshot], target_entries)
assert qwen_name not in changed_runtime
assert changed_runtime == {new_engine['file'], added_runtime['file']}
assert removed_records == [removed_runtime]

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='v110-update-') as directory:
    root = Path(directory)
    # Program changes must cover the union for every source. A file changed in
    # either source is included; a file identical in both is omitted.
    target_root = root / 'target'
    target_sources = {
        'same.py': 'src/same.py', 'from-old.py': 'src/from_old.py',
        'from-new.py': 'src/from_new.py', 'added.py': 'src/added.py',
        'VWA.exe': 'VWA.exe',
    }
    write(target_root, {source: source.encode() for source in target_sources.values()})
    old_map = {
        'same.py': 'src/same.py', 'from-old.py': 'src/from_old.py',
        'from-new.py': 'src/from_new.py', 'BVWA.exe': 'BVWA.exe',
    }
    new_map = dict(old_map)
    blobs = {
        ('old', 'src/same.py'): b'src/same.py',
        ('new', 'src/same.py'): b'src/same.py',
        ('old', 'src/from_old.py'): b'old bytes',
        ('new', 'src/from_old.py'): b'src/from_old.py',
        ('old', 'src/from_new.py'): b'src/from_new.py',
        ('new', 'src/from_new.py'): b'new bytes',
    }
    def union_reader(ref, path):
        return blobs[(ref, path)]
    snapshots = [
        {'ref': 'old', 'package_sources': old_map, 'reader': union_reader},
        {'ref': 'new', 'package_sources': new_map, 'reader': union_reader},
    ]
    previous_root = runtime_builder.ROOT
    try:
        runtime_builder.ROOT = target_root
        changed = runtime_builder._program_difference_union(target_sources, snapshots)
        assert changed == {'from-old.py', 'from-new.py', 'added.py', 'VWA.exe'}
        unsupported = dict(old_map, obsolete_program_file='src/obsolete.py')
        snapshots[0]['package_sources'] = unsupported
        try:
            runtime_builder._program_difference_union(target_sources, snapshots)
            raise AssertionError('Unrepresentable program deletion was accepted')
        except ValueError as error:
            assert 'cannot safely remove' in str(error)
    finally:
        runtime_builder.ROOT = previous_root

    # A label supplied by the CLI must be present in the frozen source policy.
    forged = runtime_builder._source_snapshot(
        'fake-v041', None,
        reader=lambda ref, path: json.dumps({'version': '0.4.1'}).encode()
        if path == 'packaging/version.json' else b'{"entries":[]}')
    try:
        runtime_builder._source_policy(forged)
        raise AssertionError('Source without the four identity files was accepted')
    except ValueError as error:
        assert 'required identity file' in str(error)

    # Candidate output is explicit and cannot occupy a release-shaped path.
    candidate_root = root / 'candidate-root'
    candidate_folder = candidate_root / 'work/candidates/candidate-1'
    candidate_folder.mkdir(parents=True)
    runtime_meta = runtime_builder._candidate_metadata(candidate_folder, 'candidate-1')
    catalog_meta = catalog_builder._candidate_metadata(candidate_folder, 'candidate-1')
    assert runtime_meta == catalog_meta and runtime_meta['status'] == 'unverified'
    assert runtime_meta['user_test_confirmed'] is False
    release_candidate = candidate_root / 'outputs/releases/v1.1.0/candidates/candidate-2'
    release_candidate.mkdir(parents=True)
    assert runtime_builder._candidate_metadata(release_candidate, 'candidate-2')['published'] is False
    runtime_builder._record_candidate_runtime(
        release_candidate, runtime_builder._candidate_metadata(release_candidate, 'candidate-2'),
        'runtime-upgrade-v0.4.0.build.json')
    runtime_builder._record_candidate_runtime(
        release_candidate, runtime_builder._candidate_metadata(release_candidate, 'candidate-2'),
        'runtime-upgrade-v1.0.0.build.json')
    assert json.loads((release_candidate / 'CANDIDATE-BUILD.json').read_text(encoding='utf-8'))['reports'] == [
        'runtime-upgrade-v0.4.0.build.json', 'runtime-upgrade-v1.0.0.build.json']
    for module in (runtime_builder, catalog_builder):
        try:
            module._candidate_metadata(candidate_root / 'release', 'candidate-1')
            raise AssertionError('Release path accepted for a candidate build')
        except ValueError as error:
            assert 'candidates' in str(error)
        try:
            module._candidate_metadata(Path('relative/candidates/candidate-1'), 'candidate-1')
            raise AssertionError('Relative candidate path was accepted')
        except ValueError as error:
            assert 'absolute' in str(error)

    # Two different source bytes share one runtime route. The catalog must keep
    # one route, one source_policy and both compatibility slots byte-identical.
    assets = root / 'assets'
    assets.mkdir()
    empty_target = assets / 'dependencies/manifest.json'
    empty_target.parent.mkdir()
    empty_target.write_bytes(b'{"entries":[]}')
    empty_sha = hashlib.sha256(empty_target.read_bytes()).hexdigest()
    old_runtime = 'a' * 64
    shared_runtime = 'b' * 64
    program_report, program_target, program_target_sha = make_route(
        assets, empty_target.read_bytes(), empty_sha, [('0.4.0', old_runtime)],
        runtime_name='VWA-Runtime-Upgrade-v0.4.0.zip',
        bootstrap_name='VWA-Program-Update-v0.4.0.zip',
        legacy_name='BVWA-Program-Update.zip')
    shared_report, shared_target, shared_target_sha = make_route(
        assets, empty_target.read_bytes(), empty_sha,
        [('0.4.1', shared_runtime), ('1.0.0', shared_runtime)],
        runtime_name='VWA-Runtime-Upgrade-v1.0.0.zip',
        bootstrap_name='VWA-App-Update-v1.0.0.zip',
        legacy_name='BVWA-App-Update.zip')
    assert program_target_sha == shared_target_sha
    full_zip, jpn_zip = b'full portable archive', b'jpn portable archive'
    write(assets, {'VWA-Windows-x64.zip': full_zip,
                   'VWA-Windows-x64-JPN.zip': jpn_zip})
    def asset(name, data):
        return {'name': name, 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}
    portable_variants = {
        'full': {'variant': 'full', 'archive': asset('VWA-Windows-x64.zip', full_zip),
                 'parts': [], 'layout_root': 'VWA', 'runtime_sha256': shared_target_sha,
                 'portable_release': {'variant': 'full', 'layout_root': 'VWA'}},
        'jpn': {'variant': 'jpn', 'archive': asset('VWA-Windows-x64-JPN.zip', jpn_zip),
                'parts': [], 'layout_root': 'VWA', 'runtime_sha256': shared_target_sha,
                'portable_release': {'variant': 'jpn', 'layout_root': 'VWA'}},
    }
    catalog = build_catalog(
        assets, '1.1.0', [program_report, shared_report],
        legacy_update='0.4.1', legacy_program='0.4.0',
        target_manifest=shared_target, external_reason='synthetic v1.1 update test',
        require_source_policy=True, output='release-assets.json',
        portable_variants=portable_variants)
    assert len(catalog['updates']) == 2
    shared_route = next(route for route in catalog['updates']
                        if route['runtime_sha256'] == shared_runtime)
    assert shared_route['source_versions'] == ['0.4.1', '1.0.0']
    assert 'source_version' not in shared_route
    assert select_update_route(catalog, shared_runtime)['source_versions'] == ['0.4.1', '1.0.0']
    assert catalog['update']['name'] == 'BVWA-App-Update.zip'
    assert catalog['program_update']['name'] == 'BVWA-Program-Update.zip'
    assert catalog['runtime_upgrade'] == shared_report['runtime_upgrade']
    assert catalog['portable_downloads']['full']['variant'] == 'full'
    assert catalog['portable_downloads']['jpn']['variant'] == 'jpn'
    assert catalog['portable_downloads']['full']['archive']['name'] == 'VWA-Windows-x64.zip'
    assert catalog['portable_downloads']['jpn']['archive']['name'] == 'VWA-Windows-x64-JPN.zip'
    assert catalog['portable_downloads']['jpn']['runtime_sha256'] == shared_target_sha
    verify_catalog_assets(catalog, assets, target_manifest=shared_target,
                          external_reason='synthetic v1.1 update test', require_source_policy=True)

    # 1.0.0-only metadata remains usable without optional legacy slots. The
    # route advertises only the source that can actually reach a canonical
    # catalog; a 0.4 source needs its byte-identical legacy slot.
    one_assets = root / 'one-assets'
    one_assets.mkdir()
    one_report, one_target, _ = make_route(
        one_assets, empty_target.read_bytes(), empty_sha,
        [('1.0.0', shared_runtime)], runtime_name='VWA-Runtime-Upgrade-v1.0.0.zip',
        bootstrap_name='VWA-App-Update-v1.0.0.zip')
    one_only = build_catalog(
        one_assets, '1.1.0', [one_report], include_legacy=False,
        target_manifest=one_target, external_reason='synthetic v1.1 update test',
        require_source_policy=True, output='release-assets-1.0.json')
    assert 'update' not in one_only and 'program_update' not in one_only
    assert 'runtime_upgrade' not in one_only
    assert one_only['updates'][0]['source_versions'] == ['1.0.0']

    # Duplicate runtime routes are rejected even when the labels differ.
    duplicate = dict(shared_report)
    duplicate['source_versions'] = ['1.0.0']
    duplicate['runtime_upgrade'] = dict(shared_report['runtime_upgrade'])
    duplicate['runtime_upgrade']['source_policy'] = {
        'format': 1,
        'allowed_sources': [source_record('1.0.0', shared_runtime, '1.0.0')],
    }
    try:
        build_catalog(assets, '1.1.0', [shared_report, duplicate], include_legacy=False,
                      target_manifest=shared_target, external_reason='synthetic v1.1 update test',
                      require_source_policy=True, output='duplicate.json')
        raise AssertionError('Duplicate runtime route was accepted')
    except ValueError as error:
        assert 'Duplicate runtime' in str(error)

    # A forged catalog label and a policy mismatch in the full payload fail the
    # same closed policy check before publication metadata is written.
    forged_report = dict(shared_report)
    forged_report['source_versions'] = ['1.0.0', '9.9.9']
    try:
        build_catalog(assets, '1.1.0', [forged_report], include_legacy=False,
                      target_manifest=shared_target, external_reason='synthetic v1.1 update test',
                      require_source_policy=True, output='forged.json')
        raise AssertionError('Forged source version was accepted')
    except ValueError as error:
        assert 'allowed_sources disagree' in str(error)

    mismatch_assets = root / 'mismatch-assets'
    mismatch_assets.mkdir()
    mismatch_report, mismatch_target, _ = make_route(
        mismatch_assets, empty_target.read_bytes(), empty_sha,
        [('1.0.0', shared_runtime)], runtime_name='VWA-Runtime-Upgrade-v1.0.0.zip',
        bootstrap_name='VWA-App-Update-v1.0.0.zip',
        plan_policy_override={'format': 1, 'allowed_sources': [source_record('9.9.9', shared_runtime, '9.9.9')]})
    try:
        build_catalog(mismatch_assets, '1.1.0', [mismatch_report], include_legacy=False,
                      target_manifest=mismatch_target, external_reason='synthetic v1.1 update test',
                      require_source_policy=True, output='mismatch.json')
        raise AssertionError('Payload and route source policies were allowed to differ')
    except ValueError as error:
        assert 'different source policies' in str(error)

    helper_assets = root / 'helper-assets'
    helper_assets.mkdir()
    helper_report, helper_target, _ = make_route(
        helper_assets, empty_target.read_bytes(), empty_sha,
        [('1.0.0', shared_runtime)], runtime_name='VWA-Runtime-Upgrade-v1.0.0.zip',
        bootstrap_name='VWA-App-Update-v1.0.0.zip', helper_override=b'# wrong helper')
    try:
        build_catalog(helper_assets, '1.1.0', [helper_report], include_legacy=False,
                      target_manifest=helper_target, external_reason='synthetic v1.1 update test',
                      require_source_policy=True, output='helper.json')
        raise AssertionError('Bootstrap and payload helper hashes diverged')
    except ValueError as error:
        assert 'helper disagree' in str(error)

    candidate_assets = root / 'outputs/releases/v1.1.0/candidates/candidate-catalog'
    candidate_assets.mkdir(parents=True)
    candidate_report, candidate_target, _ = make_route(
        candidate_assets, empty_target.read_bytes(), empty_sha,
        [('1.0.0', shared_runtime)], runtime_name='VWA-Runtime-Upgrade-v1.0.0.zip',
        bootstrap_name='VWA-App-Update-v1.0.0.zip')
    candidate_metadata = catalog_builder._candidate_metadata(
        candidate_assets, 'candidate-catalog')
    candidate_report['candidate_build'] = candidate_metadata
    candidate_catalog = build_catalog(
        candidate_assets, '1.1.0', [candidate_report], include_legacy=False,
        target_manifest=candidate_target, external_reason='synthetic v1.1 update test',
        require_source_policy=True, candidate=candidate_metadata,
        output='candidate-assets.json')
    assert candidate_catalog['build_kind'] == 'candidate'
    assert candidate_catalog['release_status'] == 'candidate_unverified'
    assert candidate_catalog['candidate'] == candidate_metadata
    assert 'UNVERIFIED' in candidate_catalog['notice']
    candidate_sidecar = json.loads((candidate_assets / 'CANDIDATE-CATALOG.json').read_text(encoding='utf-8'))
    assert candidate_sidecar['status'] == 'unverified'
    assert candidate_sidecar['sha256'] == hashlib.sha256(
        (candidate_assets / 'candidate-assets.json').read_bytes()).hexdigest()
    try:
        build_catalog(candidate_assets, '1.1.0', [candidate_report], include_legacy=False,
                      target_manifest=candidate_target, external_reason='synthetic v1.1 update test',
                      require_source_policy=True, output='formal-from-candidate.json')
        raise AssertionError('Candidate report was accepted as formal metadata')
    except ValueError as error:
        assert 'Candidate migration report' in str(error)
    try:
        build_catalog(candidate_assets, '1.1.0', [candidate_report],
                      include_legacy=False, target_manifest=candidate_target,
                      external_reason='synthetic v1.1 update test', require_source_policy=True,
                      candidate=candidate_metadata, output='release-assets.json')
        raise AssertionError('Candidate catalog reused the formal release filename')
    except ValueError as error:
        assert 'candidate-specific filename' in str(error)

print('PASS: frozen source identity, source diff union, unchanged JPN omissions, separate full/JPN portable metadata, one shared-runtime route, strict policy binding, duplicate and forged-version rejection, 1.0-only catalog and explicit unverified candidate guard')
