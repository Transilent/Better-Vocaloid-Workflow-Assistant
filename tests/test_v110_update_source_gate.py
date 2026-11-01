"""VWA 1.1.0 source release identity gates for runtime update preparation/apply."""
import hashlib
import importlib.util
import io
import json
import shutil
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from tools import update_apply as apply


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_files(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def source_installation(root, version='1.0.0'):
    base_runtime = b'old runtime'
    manifest = json.dumps({'entries': [record('dependencies/runtime/engine.bin', base_runtime)]}).encode()
    program = {
        'app.py': b'# old application\n',
        'updater.py': b'# source updater\n',
        'updates_ui.py': b'# source update UI\n',
        'tools/update_apply.py': b'# source installer\n',
        'version.json': json.dumps({'version': version, 'channel': 'stable'}).encode(),
        'dependencies/manifest.json': manifest,
        'dependencies/runtime/engine.bin': base_runtime,
    }
    write_files(root, program)
    sentinels = {
        'config.json': b'PRIVATE SETTINGS',
        'jobs/song/tuned.mid': b'USER TASK',
        'cache/publish-browser/session': b'ACCOUNT STATE',
        'dependencies/optional/model.bin': b'OPTIONAL MODEL',
    }
    write_files(root, sentinels)
    return program, manifest, sentinels


def source_policy(root, version='1.0.0', runtime_sha=None):
    files = {
        name: digest((root / name).read_bytes())
        for name in ('app.py', 'updater.py', 'updates_ui.py', 'tools/update_apply.py')
    }
    return {
        'format': 1,
        'allowed_sources': [{
            'version': version,
            'runtime_sha256': runtime_sha or digest((root / 'dependencies/manifest.json').read_bytes()),
            'files': files,
        }],
    }


def runtime_bundle(root, *, target_version='1.1.0', policy=True, embedded_policy=True):
    old_runtime = (root / 'dependencies/runtime/engine.bin').read_bytes()
    new_files = {
        'app.py': b'# target application\n',
        'tools/update_apply.py': Path(apply.__file__).read_bytes(),
        'dependencies/runtime/engine.bin': b'new runtime',
        'dependencies/runtime/new.bin': b'new dependency',
        'version.json': json.dumps({'version': target_version, 'channel': 'stable'}).encode(),
    }
    target_manifest = json.dumps({'entries': [
        record('dependencies/runtime/engine.bin', new_files['dependencies/runtime/engine.bin']),
        record('dependencies/runtime/new.bin', new_files['dependencies/runtime/new.bin']),
    ]}).encode()
    files = {**new_files, 'dependencies/manifest.json': target_manifest}
    plan = {
        'version': target_version,
        'source_runtime_sha256': digest((root / 'dependencies/manifest.json').read_bytes()),
        'target_runtime_sha256': digest(target_manifest),
        'entries': [record(name, data) for name, data in files.items()],
        'removed': [],
    }
    selected_policy = source_policy(root) if policy is True else policy
    if selected_policy is not None and embedded_policy:
        plan['source_policy'] = selected_policy
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
        zipped.writestr('runtime-upgrade.json', json.dumps(plan))
    raw = buffer.getvalue()
    name = 'VWA-Runtime-Upgrade-v' + ('1.0.0' if target_version == '1.1.0' else '9.9.9') + '.zip'
    item = {
        'name': name,
        'version': target_version,
        'bytes': len(raw),
        'sha256': digest(raw),
        'source_runtime_sha256': plan['source_runtime_sha256'],
        'target_runtime_sha256': plan['target_runtime_sha256'],
    }
    if selected_policy is not None:
        item['source_policy'] = selected_policy

    def download(selected, folder, *args, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / selected['name']
        path.write_bytes(raw)
        return path

    return item, download, files, sentinels_for(root), old_runtime


QWEN_PREFIX = 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/'
QWEN_NAMES = (
    QWEN_PREFIX + 'qwen3_asr_encoder_backend.fp16.onnx',
    QWEN_PREFIX + 'qwen3_asr_encoder_frontend.fp16.onnx',
    QWEN_PREFIX + 'qwen3_asr_llm.f16.gguf',
)


def portable_runtime_installation(root, *, variant, installed=0, corrupt=False,
                                  real_metadata=False, forge=None):
    root.mkdir()
    source_files, _, sentinels = source_installation(root)
    shared_name = 'dependencies/runtime/engine.bin'
    shared_data = b'original shared runtime'
    if real_metadata:
        assert installed == 0
        feature_raw = (APP / 'components/runtime-features.json').read_bytes()
        feature_data = json.loads(feature_raw)
        chinese = feature_data['features']['chinese']
        qwen_entries = [
            {'file': entry['file'], 'bytes': entry['bytes'], 'sha256': entry['sha256']}
            for entry in chinese['files']
        ]
        qwen_data = {}
    else:
        qwen_data = {
            name: (b'qwen-' + str(index).encode() + b'-' + b'x' * (24 + index))
            for index, name in enumerate(QWEN_NAMES)
        }
        qwen_entries = [record(name, data) for name, data in qwen_data.items()]
        features_payload = {
            'format': 1,
            'features': {'chinese': {
                'name': 'Chinese lyrics · Qwen3-ASR',
                'files': qwen_entries,
                'bytes': sum(entry['bytes'] for entry in qwen_entries),
            }},
        }
        feature_raw = json.dumps(features_payload, ensure_ascii=False).encode('utf-8')
    feature_path = root / 'components/runtime-features.json'
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    feature_path.write_bytes(feature_raw)
    manifest = json.dumps({'format': 1, 'entries': [
        record(shared_name, shared_data), *qwen_entries,
    ]}, ensure_ascii=False).encode('utf-8')
    (root / 'dependencies/manifest.json').write_bytes(manifest)
    (root / shared_name).parent.mkdir(parents=True, exist_ok=True)
    (root / shared_name).write_bytes(shared_data)
    for name in QWEN_NAMES[:installed]:
        write_files(root, {name: qwen_data[name]})
    if corrupt:
        assert installed
        expected = qwen_data[QWEN_NAMES[0]]
        write_files(root, {QWEN_NAMES[0]: b'X' * len(expected)})
    inventory_entries = [
        record(shared_name, shared_data),
        record('components/runtime-features.json', feature_raw),
    ]
    omitted = []
    if variant == 'full':
        inventory_entries.extend(qwen_entries)
        inventory = {'format': 1, 'layout_root': 'VWA', 'entries': inventory_entries}
    else:
        omitted = [dict(entry) for entry in qwen_entries]
        if forge == 'sha':
            omitted[0]['sha256'] = '0' * 64
        elif forge == 'count':
            omitted = omitted[:-1]
        elif forge == 'name':
            omitted[0]['file'] = QWEN_PREFIX + 'unreviewed.onnx'
        inventory = {
            'format': 1, 'layout_root': 'VWA', 'variant': 'jpn',
            'entries': inventory_entries, 'omitted': omitted,
        }
    (root / 'release-inventory.json').write_text(
        json.dumps(inventory, ensure_ascii=False), encoding='utf-8')
    return qwen_data, qwen_entries, feature_raw, manifest, sentinels


def stable_runtime_bundle(root, target_version='1.1.0'):
    manifest = (root / 'dependencies/manifest.json').read_bytes()
    files = {
        'app.py': b'# target application\n',
        'tools/update_apply.py': Path(apply.__file__).read_bytes(),
        'version.json': json.dumps({'version': target_version, 'channel': 'stable'}).encode(),
        'dependencies/manifest.json': manifest,
    }
    policy = source_policy(root)
    target_sha = digest(manifest)
    plan = {
        'version': target_version,
        'source_runtime_sha256': target_sha,
        'target_runtime_sha256': target_sha,
        'entries': [record(name, data) for name, data in files.items()],
        'removed': [],
        'source_policy': policy,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
        zipped.writestr('runtime-upgrade.json', json.dumps(plan))
    raw = buffer.getvalue()
    item = {
        'name': 'VWA-Runtime-Upgrade-v1.0.0.zip', 'version': target_version,
        'bytes': len(raw), 'sha256': digest(raw),
        'source_runtime_sha256': target_sha, 'target_runtime_sha256': target_sha,
        'source_policy': policy,
    }
    def download(selected, folder, *args, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / selected['name']
        path.write_bytes(raw)
        return path
    return item, download


def sentinels_for(root):
    return {name: (root / name).read_bytes() for name in (
        'config.json', 'jobs/song/tuned.mid', 'cache/publish-browser/session',
        'dependencies/optional/model.bin')}


def not_called(*args, **kwargs):
    raise AssertionError('source gate allowed the runtime archive to be downloaded')


def expect_rejected(call, text):
    try:
        call()
        raise AssertionError('unsafe source policy or plan was accepted')
    except ValueError as error:
        assert text in str(error), str(error)


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='v110-source-gate-') as directory:
    scratch = Path(directory)

    # A legitimate 1.0.0 source prepares without touching installed files and
    # applies only after repeating the same source identity check.
    root = scratch / 'valid-1.0'
    root.mkdir()
    source_files, source_manifest, sentinels = source_installation(root)
    item, download, target_files, _, _ = runtime_bundle(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    assert (root / 'app.py').read_bytes() == source_files['app.py']
    assert (root / 'version.json').read_bytes() == source_files['version.json']
    assert json.loads((root / 'dependencies/manifest.json').read_text())['entries']
    planned = json.loads(plan.read_text(encoding='utf-8'))
    assert planned['source_identity']['version'] == '1.0.0'
    assert planned['source_evidence']['kind'] == 'installed_release'
    assert planned['source_policy_sha256'] == apply._canonical_json_sha256(planned['source_policy'])
    pending = json.loads((root / 'cache/updates/pending.json').read_text(encoding='utf-8'))
    assert pending['plan_sha256'] == digest(plan.read_bytes())
    assert apply.validate_prepared_runtime_upgrade(plan)

    # A changed plan is rejected by the stage binding before installed
    # program files are touched. Restoring the exact prepared bytes permits the
    # already staged, still-valid retry.
    prepared_bytes = plan.read_bytes()
    tampered = json.loads(prepared_bytes)
    tampered['source_identity']['files']['updater.py'] = '0' * 64
    plan.write_text(json.dumps(tampered), encoding='utf-8')
    expect_rejected(lambda: apply.apply_update(plan), '计划绑定已改变')
    assert (root / 'version.json').read_bytes() == source_files['version.json']
    plan.write_bytes(prepared_bytes)

    # Independently editing only pending.json is also rejected.
    pending_path = root / 'cache/updates/pending.json'
    original_pending = pending_path.read_bytes()
    pending_data = json.loads(original_pending)
    pending_data['plan_sha256'] = 'f' * 64
    pending_path.write_text(json.dumps(pending_data), encoding='utf-8')
    expect_rejected(lambda: apply.apply_update(plan), '计划或待安装回执已改变')
    assert (root / 'app.py').read_bytes() == source_files['app.py']
    pending_path.write_bytes(original_pending)

    # Frozen 1.0 helper code rewrites pending.json in its historical two-field
    # shape after calling the new helper. The stage sidecar must keep the plan
    # bound, while the old-shaped pending record remains sufficient.
    legacy_pending = {'plan': pending_data['plan'], 'version': pending_data['version']}
    pending_path.write_text(json.dumps(legacy_pending), encoding='utf-8')
    assert apply.validate_prepared_runtime_upgrade(plan)
    sidecar_tamper = json.loads(prepared_bytes)
    sidecar_tamper['state'] = 'tampered'
    plan.write_text(json.dumps(sidecar_tamper), encoding='utf-8')
    expect_rejected(lambda: apply.apply_update(plan), '计划绑定已改变')
    plan.write_bytes(prepared_bytes)

    result = apply.apply_update(plan)
    assert result['state'] == 'passed' and result['runtime_upgraded']
    assert all((root / name).read_bytes() == data for name, data in {**target_files, **sentinels}.items())
    assert not pending_path.exists()

    # The item is authoritative for binding; the builder may keep the same
    # runtime route for 0.4.1 and 1.0.0 as long as each source has its own exact
    # version/files record. The current 1.0.0 installation must select 1.0.0.
    root = scratch / 'shared-runtime-item-policy'
    root.mkdir()
    source_installation(root)
    shared_policy = source_policy(root, version='1.0.0')
    older = json.loads(json.dumps(shared_policy['allowed_sources'][0]))
    older['version'] = '0.4.1'
    shared_policy['allowed_sources'].append(older)
    item, download, _, _, _ = runtime_bundle(
        root, policy=shared_policy, embedded_policy=False)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    planned = json.loads(plan.read_text(encoding='utf-8'))
    assert planned['source_identity']['version'] == '1.0.0'
    assert planned['source_policy']['allowed_sources'][1]['version'] == '0.4.1'
    assert planned['source_policy']['allowed_sources'][0]['runtime_sha256'] == planned['source_policy']['allowed_sources'][1]['runtime_sha256']
    assert apply.apply_update(plan)['state'] == 'passed'

    # A source program file changing after preparation fails the second gate and
    # leaves the old installed version and user state intact.
    root = scratch / 'changed-after-prepare'
    root.mkdir()
    source_files, source_manifest, sentinels = source_installation(root)
    item, download, _, _, _ = runtime_bundle(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    (root / 'updater.py').write_bytes(b'# manually changed source\n')
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('_v110_source_gate_bootstrap', APP / 'src/upgrade_bootstrap.py')
    upgrade_bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade_bootstrap)
    messages = []
    bridge = SimpleNamespace(close_requested=False, root=root, failed=messages.append)
    with patch.object(upgrade_bootstrap.subprocess, 'Popen') as spawn:
        upgrade_bootstrap.UpgradeDialog.install(bridge, plan)
    spawn.assert_not_called()
    assert messages and '安装前源身份复核失败' in messages[-1]
    expect_rejected(lambda: apply.apply_update(plan), '准备更新后源版本')
    assert (root / 'app.py').read_bytes() == source_files['app.py']
    assert (root / 'version.json').read_bytes() == source_files['version.json']
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    # A source change during the staged download is noticed before the plan is
    # published; the incomplete runtime stage is removed and old files remain.
    root = scratch / 'changed-during-staging'
    root.mkdir()
    source_files, source_manifest, sentinels = source_installation(root)
    item, download, _, _, _ = runtime_bundle(root)
    def mutate_while_staging(selected, folder, *args, **kwargs):
        fetched = download(selected, folder, *args, **kwargs)
        (root / 'app.py').write_bytes(b'# source changed during staging\n')
        return fetched
    expect_rejected(
        lambda: apply.prepare_runtime_upgrade(item, root, downloader=mutate_while_staging),
        '准备更新期间源版本')
    assert not list((root / 'cache/updates').glob('runtime-stage-*'))
    assert (root / 'version.json').read_bytes() == source_files['version.json']
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    # Even if the source changes while staged payload validation runs, the
    # immediate pre-write recheck catches it.
    root = scratch / 'changed-during-validation'
    root.mkdir()
    source_files, source_manifest, sentinels = source_installation(root)
    item, download, _, _, _ = runtime_bundle(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    original_validate = apply._validate_runtime_stage
    def validate_then_change(*args, **kwargs):
        validated = original_validate(*args, **kwargs)
        (root / 'updates_ui.py').write_bytes(b'# changed during validation\n')
        return validated
    with patch.object(apply, '_validate_runtime_stage', side_effect=validate_then_change):
        expect_rejected(lambda: apply.apply_update(plan), '准备更新后源版本')
    assert (root / 'app.py').read_bytes() == source_files['app.py']

    # Frozen 1.0.0 end-to-end: its own prepare_catalog_update loads the new
    # helper, then rewrites pending.json with only plan/version. The stage
    # sidecar must carry the binding so the real compatibility path still works.
    root = scratch / 'frozen-1.0-catalog-e2e'
    root.mkdir()
    source_files, source_manifest, sentinels = source_installation(root)
    frozen_fixture = APP / 'tests/fixtures/update-client-1.0.0'
    frozen_files = {
        'app.py': ('app.py', 'fac7084b6621ae5ec0dfd119164dca76d8e77548f33cdb2a28e4b68d8c2be64e'),
        'updater.py': ('updater.py', '5772f3cb6b51310a0bb71731a6c9b139a295ac59c8e2f670130a81b1f74b2c4f'),
        'updates_ui.py': ('updates_ui.py', '57dbf54031a17f6ba0866d823bf224d1a37c01d21825e6d5c8597727047638e2'),
        'tools/update_apply.py': ('update_apply.py', '84a9550707bf65c2d77e7fb56911d03a7e560b71cebc08a34a2c2dbba6dfdd1d'),
    }
    frozen_program = {}
    for installed_name, (fixture_name, expected) in frozen_files.items():
        raw = (frozen_fixture / fixture_name).read_bytes()
        assert digest(raw) == expected, fixture_name
        frozen_program[installed_name] = raw
    write_files(root, frozen_program)
    source_files.update(frozen_program)

    assets = scratch / 'frozen-1.0-assets'
    item, download, target_files, _, _ = runtime_bundle(root)
    runtime_path = download(item, assets)
    from tools.build_runtime_upgrade import build_bootstrap
    bootstrap = build_bootstrap(
        assets, item, filename='VWA-Program-Update-v1.0.0.zip')
    route = {
        'source_versions': ['1.0.0'], 'runtime_sha256': item['source_runtime_sha256'],
        'update': bootstrap, 'runtime_upgrade': item,
    }
    release = {
        'version': '1.1.0',
        'update': dict(bootstrap, catalog_route=route,
                       download_bytes=bootstrap['bytes'] + item['bytes']),
    }
    asset_paths = {item['name']: runtime_path, bootstrap['name']: assets / bootstrap['name']}
    def frozen_download(selected, folder, *args, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        copied = folder / selected['name']
        shutil.copy2(asset_paths[selected['name']], copied)
        return copied

    frozen_folder = scratch / 'frozen-1.0-code'
    frozen_folder.mkdir()
    frozen_helper_path = frozen_folder / 'update_apply.py'
    frozen_updater_path = frozen_folder / 'updater.py'
    frozen_helper_path.write_bytes(frozen_program['tools/update_apply.py'])
    frozen_updater_path.write_bytes(frozen_program['updater.py'])
    helper_spec = importlib.util.spec_from_file_location('_frozen_100_apply', frozen_helper_path)
    frozen_helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(frozen_helper)
    updater_spec = importlib.util.spec_from_file_location('_frozen_100_updater', frozen_updater_path)
    frozen_updater = importlib.util.module_from_spec(updater_spec)
    updater_spec.loader.exec_module(frozen_updater)
    previous_helper = sys.modules.get('tools.update_apply')
    sys.modules['tools.update_apply'] = frozen_helper
    try:
        plan = frozen_updater.prepare_update(
            release, source='direct', root=root, downloader=frozen_download)
    finally:
        if previous_helper is None:
            sys.modules.pop('tools.update_apply', None)
        else:
            sys.modules['tools.update_apply'] = previous_helper

    old_pending_path = root / 'cache/updates/pending.json'
    old_pending = json.loads(old_pending_path.read_text(encoding='utf-8'))
    assert set(old_pending) == {'plan', 'version'}, 'Frozen 1.0 must retain its two-field receipt'
    assert old_pending == {'plan': str(plan.resolve()), 'version': '1.1.0'}
    assert (plan.parent / 'source-binding.json').is_file()
    assert all((root / name).read_bytes() == data for name, data in source_files.items())
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    staged_spec = importlib.util.spec_from_file_location(
        '_frozen_100_staged_new_helper', plan.parent / 'apply_helper.py')
    staged = importlib.util.module_from_spec(staged_spec)
    staged_spec.loader.exec_module(staged)
    frozen_plan_bytes = plan.read_bytes()
    frozen_tamper = json.loads(frozen_plan_bytes)
    frozen_tamper['source_identity']['files']['app.py'] = '0' * 64
    plan.write_text(json.dumps(frozen_tamper), encoding='utf-8')
    expect_rejected(lambda: staged.apply_update(plan), '计划绑定已改变')
    plan.write_bytes(frozen_plan_bytes)
    assert json.loads(old_pending_path.read_text(encoding='utf-8')) == old_pending
    assert staged.apply_update(plan)['state'] == 'passed'
    assert all((root / name).read_bytes() == data for name, data in {**target_files, **sentinels}.items())
    assert json.loads((root / 'version.json').read_text()) == {'version': '1.1.0', 'channel': 'stable'}
    assert not old_pending_path.exists()

    # full -> full: the complete manifest remains mandatory. Every Chinese
    # payload must exist and hash, and the update receipt identifies the variant.
    root = scratch / 'full-complete'
    qwen_data, _, _, _, sentinels = portable_runtime_installation(
        root, variant='full', installed=3)
    before_features = (root / 'components/runtime-features.json').read_bytes()
    item, download = stable_runtime_bundle(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    result = apply.apply_update(plan)
    assert result['release_variant'] == 'full'
    assert result['omitted_runtime_files'] == []
    assert result['installed_omitted_runtime_files'] == []
    assert result['preserved_omitted_runtime_files'] == []
    assert all((root / name).read_bytes() == data for name, data in qwen_data.items())
    assert (root / 'components/runtime-features.json').read_bytes() == before_features
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    root = scratch / 'full-missing'
    portable_runtime_installation(root, variant='full', installed=0)
    item, download = stable_runtime_bundle(root)
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=download), '运行库文件缺失')
    assert not any((root / name).exists() for name in QWEN_NAMES)
    assert not list((root / 'cache/updates').glob('runtime-stage-*'))

    # Real 1.0 JPN inventory metadata: format/layout/variant and the exact three
    # released omitted records. Updating leaves those default omissions absent.
    root = scratch / 'jpn-real-inventory-omitted'
    _, real_records, real_features, real_manifest, sentinels = portable_runtime_installation(
        root, variant='jpn', installed=0, real_metadata=True)
    before_inventory = (root / 'release-inventory.json').read_bytes()
    item, download = stable_runtime_bundle(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    result = apply.apply_update(plan)
    real_names = {entry['file'] for entry in real_records}
    assert result['release_variant'] == 'jpn'
    assert set(result['omitted_runtime_files']) == real_names
    assert set(result['preserved_omitted_runtime_files']) == real_names
    assert result['installed_omitted_runtime_files'] == []
    assert not any((root / name).exists() for name in real_names)
    assert (root / 'components/runtime-features.json').read_bytes() == real_features
    assert (root / 'dependencies/manifest.json').read_bytes() == real_manifest
    assert (root / 'release-inventory.json').read_bytes() == before_inventory
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    # JPN -> JPN with a partial or complete later installation: present weights
    # are verified and preserved byte-for-byte; missing verified omissions stay
    # missing and are never added by the update payload.
    for installed_count in (1, 3):
        root = scratch / f'jpn-earlier-models-{installed_count}'
        qwen_data, qwen_records, before_features, _, sentinels = portable_runtime_installation(
            root, variant='jpn', installed=installed_count)
        item, download = stable_runtime_bundle(root)
        result = apply.apply_update(
            apply.prepare_runtime_upgrade(item, root, downloader=download))
        expected_present = set(QWEN_NAMES[:installed_count])
        expected_missing = set(QWEN_NAMES) - expected_present
        assert result['release_variant'] == 'jpn'
        assert set(result['omitted_runtime_files']) == set(QWEN_NAMES)
        assert set(result['installed_omitted_runtime_files']) == expected_present
        assert set(result['preserved_omitted_runtime_files']) == expected_missing
        for name in expected_present:
            assert (root / name).read_bytes() == qwen_data[name]
        for name in expected_missing:
            assert not (root / name).exists()
        assert (root / 'components/runtime-features.json').read_bytes() == before_features
        assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    # A present but damaged omitted weight is never forgiven as if absent.
    root = scratch / 'jpn-present-corrupt'
    qwen_data, _, _, _, sentinels = portable_runtime_installation(
        root, variant='jpn', installed=2, corrupt=True)
    item, download = stable_runtime_bundle(root)
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=download), '校验不符')
    assert (root / QWEN_NAMES[0]).read_bytes() != qwen_data[QWEN_NAMES[0]]
    assert all((root / name).read_bytes() == data for name, data in sentinels.items())

    # A forged or incomplete JPN omitted list cannot authorize missing files.
    for forge in ('sha', 'count', 'name'):
        root = scratch / ('jpn-forged-' + forge)
        portable_runtime_installation(root, variant='jpn', installed=0, forge=forge)
        item, download = stable_runtime_bundle(root)
        expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=download), 'JPN')
        assert not any((root / name).exists() for name in QWEN_NAMES)

    # The JPN exception is exact: a different manifest member remaining missing
    # still fails even when all three reviewed Qwen omissions are valid.
    root = scratch / 'jpn-other-missing'
    portable_runtime_installation(root, variant='jpn', installed=0)
    (root / 'dependencies/runtime/engine.bin').unlink()
    item, download = stable_runtime_bundle(root)
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=download),
                    '运行库文件缺失')

    # The ordinary program-update entry cannot install 1.1.0 without the
    # runtime route and its source policy. Only the narrow catalog bootstrap
    # staging path opts into the temporary first-hop exception.
    root = scratch / 'ordinary-program-entry'
    root.mkdir()
    source_installation(root)
    expect_rejected(
        lambda: apply.prepare_program_update('1.1.0', {}, root, downloader=not_called),
        '不能通过普通程序更新入口')

    # A target-version or newer source label cannot be used to replay/install the
    # same release.
    root = scratch / 'target-version-source'
    root.mkdir()
    source_installation(root, version='1.1.0')
    item, download, _, _, _ = runtime_bundle(root, policy=source_policy(root, version='1.1.0'))
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '源版本不低于')

    # Any future target that carries a policy receives the same binding checks;
    # 1.1.0 is only the version where the policy remains mandatory.
    root = scratch / 'policy-future-target'
    root.mkdir()
    source_installation(root)
    item, download = stable_runtime_bundle(root, target_version='1.1.1')
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    assert json.loads(plan.read_text(encoding='utf-8'))['version'] == '1.1.1'
    assert apply.apply_update(plan)['state'] == 'passed'
    assert json.loads((root / 'version.json').read_text())['version'] == '1.1.1'

    # Missing or malformed policies fail before download.
    root = scratch / 'missing-policy'
    root.mkdir()
    source_installation(root)
    item, download, _, _, _ = runtime_bundle(root, policy=None)
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '缺少 source_policy')

    root = scratch / 'unsafe-policy-path'
    root.mkdir()
    source_installation(root)
    bad_policy = source_policy(root)
    bad_policy['allowed_sources'][0]['files']['config.json'] = digest((root / 'config.json').read_bytes())
    item, download, _, _, _ = runtime_bundle(root, policy=bad_policy)
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '不安全')

    # Same runtime does not authorize a different source version, and a changed
    # 1.0.0 release program is not accepted merely because version.json says so.
    root = scratch / 'same-runtime-wrong-source'
    root.mkdir()
    source_installation(root, version='0.4.1')
    item, download, _, _, _ = runtime_bundle(root, policy=source_policy(root, version='1.0.0'))
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '允许的源版本')

    root = scratch / 'wrong-release-file'
    root.mkdir()
    source_installation(root)
    item, download, _, _, _ = runtime_bundle(root)
    (root / 'updates_ui.py').write_bytes(b'# modified release file\n')
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '允许的源版本')

    # Optional legacy-first-hop route: after the old 0.4.1 client has already
    # replaced three files, the second hop can proceed only from one matching
    # old plan and a complete, still-present rollback backup.
    def first_hop_installation(root, *, with_backup=True):
        root.mkdir()
        source_files, source_manifest, kept = source_installation(root, version='0.4.1')
        policy = source_policy(root, version='0.4.1')
        item, download, target_files, _, _ = runtime_bundle(root, policy=policy)
        first_target = {
            'app.py': b'# first hop bootstrap\n',
            'tools/update_apply.py': Path(apply.__file__).read_bytes(),
            'version.json': json.dumps({
                'version': '1.1.0', 'channel': 'stable', 'upgrade_pending': True,
                'runtime_upgrade': item,
            }, ensure_ascii=False).encode(),
        }
        clients = json.loads((APP / 'packaging/update-clients.json').read_text(encoding='utf-8'))['clients']
        client = next(entry for entry in clients if entry['version'] == '0.4.1')
        fixture = APP / client['fixtures']['tools/update_apply.py']['fixture']
        spec = importlib.util.spec_from_file_location('_frozen_041_apply_' + root.name, fixture)
        old_helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old_helper)
        stage = root / 'cache/updates/stage-frozen-first-hop'
        write_files(stage / 'files', first_target)
        first_plan = stage / 'plan.json'
        first_plan.write_text(json.dumps({
            'root': str(root.resolve()), 'version': '1.1.0',
            'runtime_sha256': item['source_runtime_sha256'],
            'entries': [record(name, data) for name, data in first_target.items()],
            'state': 'ready',
        }), encoding='utf-8')
        assert old_helper.apply_update(first_plan)['state'] == 'passed'
        install_metadata = json.loads((root / 'version.json').read_text(encoding='utf-8'))
        assert install_metadata['version'] == '1.1.0' and install_metadata['upgrade_pending']
        assert all((root / name).read_bytes() == data for name, data in first_target.items())
        backup = stage / 'backup'
        assert backup.is_dir()
        if not with_backup:
            shutil.rmtree(backup)
        return item, download, target_files, kept, stage


    root = scratch / 'first-hop-proven'
    item, download, target_files, kept, first_stage = first_hop_installation(root)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    planned = json.loads(plan.read_text(encoding='utf-8'))
    evidence = planned['source_evidence']
    assert evidence['kind'] == 'first_hop_backup'
    backup = root / evidence['backup']
    assert backup == first_stage / 'backup' and (backup / 'version.json').is_file()
    assert json.loads((backup / 'version.json').read_text())['version'] == '0.4.1'
    assert apply.apply_update(plan)['state'] == 'passed'
    assert json.loads((root / 'version.json').read_text()) == {'version': '1.1.0', 'channel': 'stable'}
    assert all((root / name).read_bytes() == data for name, data in {**target_files, **kept}.items())
    assert (backup / 'version.json').is_file(), 'first-hop rollback evidence must be preserved'

    # Once version.json says 1.1.0 and the bootstrap is installed, a missing old
    # backup cannot be papered over by the matching runtime SHA or version label.
    root = scratch / 'first-hop-no-evidence'
    item, download, _, kept, _ = first_hop_installation(root, with_backup=False)
    before = (root / 'version.json').read_bytes()
    expect_rejected(lambda: apply.prepare_runtime_upgrade(item, root, downloader=not_called), '第一跳后无法证明')
    assert (root / 'version.json').read_bytes() == before
    assert all((root / name).read_bytes() == data for name, data in kept.items())

    # Existing non-1.1.0 tests remain compatible when no policy is supplied.
    root = scratch / 'legacy-no-policy'
    root.mkdir()
    source_installation(root)
    item, download, _, _, _ = runtime_bundle(root, target_version='9.9.9', policy=None)
    plan = apply.prepare_runtime_upgrade(item, root, downloader=download)
    assert apply.apply_update(plan)['state'] == 'passed'
    assert json.loads((root / 'version.json').read_text())['version'] == '9.9.9'

print('PASS: 1.0.0 source/runtime/release identity, pre-download gate, install/apply rechecks, plan/pending tamper, frozen 1.0 catalog/pending compatibility, fail-closed policy, full/JPN variants, first-hop evidence and legacy compatibility')
