"""Synthetic layered health, installer receipt and ComponentsPage verification tests."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['BVWA_TESTING'] = '1'

from PyQt5.QtWidgets import QApplication
import component_health
import components_ui
import optional_components as components


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def build_fixture(base, *, weight=b'weight-v1', critical=b'runtime-v1',
                  legacy_critical_sha=False, weight_name='weights/model.bin',
                  critical_name='packages/runtime.bin'):
    current = Path(base) / 'component/current'
    (current / Path(weight_name).parent).mkdir(parents=True, exist_ok=True) if '..' not in Path(weight_name).parts else None
    (current / Path(critical_name).parent).mkdir(parents=True, exist_ok=True) if '..' not in Path(critical_name).parts else None
    if '..' not in Path(weight_name).parts:
        (current / weight_name).write_bytes(weight)
    if '..' not in Path(critical_name).parts:
        (current / critical_name).write_bytes(critical)
    manifest = Path(base) / 'roformer.json'
    spec = {
        'component': 'bs-roformer-test',
        'weights': [{'name': weight_name, 'bytes': len(weight), 'sha256': digest(weight)}],
        'shared_wheels': [],
        'torch_wheels': {'cpu': {'bytes': 0}, 'cuda': {'bytes': 0}},
        'install_bytes': {'cpu': 0, 'cuda': 0},
    }
    write_json(manifest, spec)
    critical_row = {'file': critical_name, 'bytes': len(critical)}
    if not legacy_critical_sha:
        critical_row['sha256'] = digest(critical)
    write_json(current / 'installed.json', {
        'runtime': 'cpu',
        'probe': {'torch': 'fixture', 'cuda_available': False},
        'critical_files': [critical_row],
        'manifest_sha256': digest(manifest.read_bytes()),
        'source_sha256': {},
    })
    return current, manifest, spec


def replace_json_same_size(path, transform):
    path = Path(path)
    before = path.stat().st_size
    value = json.loads(path.read_text(encoding='utf-8'))
    transform(value)
    temporary = path.with_name(path.name + '.same-size-replacement')
    write_json(temporary, value)
    assert temporary.stat().st_size == before
    os.replace(temporary, path)


def parent_reparse_patch(target):
    target = Path(target).absolute()
    real_lstat = Path.lstat

    def patched(self):
        if self.absolute() == target:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o777,
                                   st_file_attributes=0x400, st_size=0)
        return real_lstat(self)

    return patch.object(Path, 'lstat', patched)


def make_directory_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        if os.name != 'nt':
            return False
        result = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(link), str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        return result.returncode == 0 and link.exists()


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    application.processEvents()
    return predicate()


application = QApplication.instance() or QApplication([])
passed = []
with tempfile.TemporaryDirectory(prefix='component-health-', dir=APP / 'work') as temporary:
    base = Path(temporary)

    root, manifest, spec = build_fixture(base / 'normal')
    with patch.object(components, 'MANIFEST', manifest):
        with patch.object(component_health, '_hash_file', side_effect=AssertionError('fast status hashed content')):
            fast = components.status(root.parent)
        assert fast['ready'] and fast['runtime'] == 'cpu' and fast['probe']['torch'] == 'fixture'
        assert fast['verification'] == {'level': 'metadata', 'content_verified': False,
                                        'sha256_checked': False, 'state': 'metadata_ready'}
        progress = []
        report = components.full_verify(
            root.parent, progress=lambda done, total, text: progress.append((done, total, text)))
    assert report['state'] == 'verified' and report['verified'] is True, report
    assert len(report['expected']) == 2 and len(report['actual']) == 2
    assert not report['corrupted'] and not report['missing'] and not report['unverified']
    assert report['progress']['sha256_checked'] == 2 and progress
    passed.append('normal-full-verified-and-fast-metadata')

    model = root / 'weights/model.bin'
    model.write_bytes(b'weight-v2')
    assert model.stat().st_size == 9
    with patch.object(components, 'MANIFEST', manifest):
        assert components.status(root.parent)['ready']
        corrupted = components.full_verify(root.parent)
    assert not corrupted['verified'] and corrupted['state'] == 'corrupt'
    mismatch = next(row for row in corrupted['corrupted'] if row['reason'] == 'sha256_mismatch')
    assert mismatch['path'] == 'weights/model.bin'
    assert mismatch['expected_sha256'] != mismatch['actual_sha256']
    passed.append('same-size-corruption-distinguished-from-metadata')

    root, manifest, spec = build_fixture(base / 'missing')
    (root / 'packages/runtime.bin').unlink()
    with patch.object(components, 'MANIFEST', manifest):
        assert not components.status(root.parent)['ready']
        missing = components.full_verify(root.parent)
    assert not missing['verified'] and 'packages/runtime.bin' in missing['missing']
    assert any(row['reason'] == 'missing' for row in missing['corrupted'])
    passed.append('missing-critical-file')

    root, manifest, spec = build_fixture(base / 'legacy', legacy_critical_sha=True)
    with patch.object(components, 'MANIFEST', manifest):
        assert components.status(root.parent)['ready']
        legacy = components.full_verify(root.parent)
    assert not legacy['verified'] and legacy['state'] == 'unverified'
    assert any(row['reason'] == 'expected_sha256_missing' for row in legacy['unverified'])
    passed.append('legacy-receipt-missing-sha-not-verified')

    large = b'X' * (2 * 1024 * 1024 + 17)
    root, manifest, spec = build_fixture(base / 'cancel', weight=large)
    cancelled = [False]
    cancel_progress = []

    def progress(done, total, text):
        cancel_progress.append((done, total, text))
        if 'weights/model.bin' in text and done:
            cancelled[0] = True

    with patch.object(components, 'MANIFEST', manifest):
        report = components.full_verify(root.parent, progress=progress, check=lambda: cancelled[0])
    assert report['state'] == 'cancelled' and not report['verified']
    assert report['progress']['hash_bytes'] > 0
    passed.append('cancellable-progress')

    root, manifest, spec = build_fixture(base / 'traversal', weight_name='../outside.bin')
    outside = Path(base) / 'outside.bin'
    outside.write_bytes(b'weight-v1')
    with patch.object(components, 'MANIFEST', manifest):
        traversal = components.full_verify(root.parent)
    assert not traversal['verified']
    assert any(row['path'] == '../outside.bin' and row['reason'] == 'path_traversal'
               for row in traversal['corrupted'])
    passed.append('manifest-traversal-rejected')

    root, manifest, spec = build_fixture(
        base / 'reparse', critical_name='packages/linked/runtime.bin')
    linked_parent = root / 'packages/linked'
    linked_parent.parent.mkdir(parents=True, exist_ok=True)
    if linked_parent.is_dir():
        shutil.rmtree(linked_parent)
    outside_dir = Path(base) / 'reparse-outside'
    outside_dir.mkdir()
    (outside_dir / 'runtime.bin').write_bytes(b'runtime-v1')
    actual_link = make_directory_link(linked_parent, outside_dir)
    if actual_link:
        with patch.object(components, 'MANIFEST', manifest):
            reparse = components.full_verify(root.parent)
    else:
        real_lstat = Path.lstat
        def simulated_reparse(self):
            if self.absolute() == linked_parent.absolute():
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o777,
                                       st_file_attributes=0x400, st_size=0)
            return real_lstat(self)
        with patch.object(components, 'MANIFEST', manifest):
            with patch.object(Path, 'lstat', simulated_reparse):
                reparse = components.full_verify(root.parent)
    assert not reparse['verified']
    assert any(row['path'] == 'packages/linked/runtime.bin' and row['reason'] == 'reparse_point'
               for row in reparse['corrupted'])
    passed.append('reparse-point-rejected' if actual_link else 'reparse-point-rejected-simulated')

    # A junction above current must be rejected even when current itself is ordinary.
    root, manifest, spec = build_fixture(base / 'root-ancestor')
    redirect_parent = root.parent
    with patch.object(components, 'MANIFEST', manifest), parent_reparse_patch(redirect_parent):
        root_ancestor = components.full_verify(root.parent)
        assert not components.status(root.parent)['ready']
    assert root_ancestor['errors'][0]['code'] == 'reparse_point'
    assert root_ancestor['errors'][0]['path'] == str(redirect_parent.absolute())
    passed.append('component-root-parent-reparse-rejected')

    # A junction above the manifest must be rejected before opening the file.
    root, original_manifest, spec = build_fixture(base / 'manifest-ancestor')
    manifest_parent = base / 'manifest-parent'
    manifest_parent.mkdir()
    manifest = manifest_parent / 'roformer.json'
    shutil.copy2(original_manifest, manifest)
    with patch.object(components, 'MANIFEST', manifest), parent_reparse_patch(manifest_parent):
        manifest_ancestor = components.full_verify(root.parent)
        assert not components.status(root.parent)['ready']
    assert manifest_ancestor['errors'][0]['code'] == 'reparse_point'
    assert manifest_ancestor['errors'][0]['path'] == str(manifest_parent.absolute())
    passed.append('manifest-parent-reparse-rejected')

    root, manifest, spec = build_fixture(base / 'manifest-budget')
    oversized_manifest = base / 'oversized-roformer.json'
    oversized_manifest.write_bytes(b'{' + b' ' * component_health.MANIFEST_MAX_BYTES)
    with patch.object(components, 'MANIFEST', oversized_manifest):
        large_manifest = components.full_verify(root.parent)
    assert large_manifest['errors'][0]['code'] == 'metadata_too_large'
    assert large_manifest['errors'][0]['limit_bytes'] == component_health.MANIFEST_MAX_BYTES
    assert large_manifest['errors'][0]['actual_bytes'] > component_health.MANIFEST_MAX_BYTES

    root, manifest, spec = build_fixture(base / 'receipt-budget')
    (root / 'installed.json').write_bytes(b'{' + b' ' * component_health.RECEIPT_MAX_BYTES)
    with patch.object(components, 'MANIFEST', manifest):
        assert not components.status(root.parent)['ready']
        large_receipt = components.full_verify(root.parent)
    assert large_receipt['errors'][0]['code'] == 'metadata_too_large'
    assert large_receipt['errors'][0]['limit_bytes'] == component_health.RECEIPT_MAX_BYTES
    passed.append('metadata-byte-budgets')

    for index, bad_size in enumerate((1.5, float('inf'))):
        root, manifest, spec = build_fixture(base / f'strict-size-{index}')
        spec['weights'][0]['bytes'] = bad_size
        write_json(manifest, spec)
        receipt_path = root / 'installed.json'
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['manifest_sha256'] = digest(manifest.read_bytes())
        write_json(receipt_path, receipt)
        with patch.object(components, 'MANIFEST', manifest):
            assert not components.status(root.parent)['ready']
            strict = components.full_verify(root.parent)
        assert strict['expected'][0]['bytes'] is None
        assert any(row['code'] == 'expected_bytes_missing' for row in strict['errors'])
        assert not strict['verified']
    passed.append('strict-nonnegative-integer-sizes')

    large = b'A' * (2 * 1024 * 1024 + 4096)
    root, manifest, spec = build_fixture(base / 'changed-during-verification', weight=large)
    receipt_path = root / 'installed.json'
    changed_once = [False]

    def replace_after_hash(done, total, message):
        if changed_once[0] or '已读取并哈希' not in message:
            return
        changed_once[0] = True
        model = root / 'weights/model.bin'
        temporary = model.with_name(model.name + '.replacement')
        temporary.write_bytes(b'B' * len(large))
        os.replace(temporary, model)
        replace_json_same_size(
            manifest, lambda value: value.__setitem__('component', 'bs-roformer-tesu'))
        replace_json_same_size(
            receipt_path, lambda value: value['probe'].__setitem__('torch', 'fixturf'))

    with patch.object(components, 'MANIFEST', manifest):
        changed = components.full_verify(root.parent, progress=replace_after_hash)
    assert changed['state'] == 'changed_during_verification' and not changed['verified']
    assert any(row.get('kind') == 'manifest' for row in changed['changed'])
    assert any(row.get('kind') == 'receipt' for row in changed['changed'])
    assert any(row['path'] == 'weights/model.bin' and row['reason'] == 'changed_during_verification'
               for row in changed['corrupted'])
    passed.append('manifest-receipt-file-identity-change-detected')

    # Exercise the real installer receipt path with tiny wheels and a fake probe.
    install_base = base / 'install'
    install_root = install_base / 'component-root'
    weight_bytes = b'installer-weight'
    wheel_bytes = b'fake-wheel'
    install_manifest = install_base / 'roformer.json'
    install_spec = {
        'component': 'bs-roformer-test',
        'weights': [{'name': 'model.ckpt', 'url': 'fixture://weight',
                     'bytes': len(weight_bytes), 'sha256': digest(weight_bytes)}],
        'shared_wheels': [],
        'torch_wheels': {
            'cpu': {'name': 'torch-cpu.whl', 'url': 'fixture://torch', 'bytes': len(wheel_bytes),
                    'sha256': digest(wheel_bytes)},
            'cuda': {'name': 'torch-cuda.whl', 'url': 'fixture://torch', 'bytes': len(wheel_bytes),
                     'sha256': digest(wheel_bytes)},
        },
        'install_bytes': {'cpu': 0, 'cuda': 0},
    }
    write_json(install_manifest, install_spec)

    def fake_download(item, folder, progress, check=None, source='auto'):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / item['name']
        path.write_bytes(weight_bytes if item['name'].endswith('.ckpt') else wheel_bytes)
        return path

    def fake_extract(archive, directory, check=None):
        directory = Path(directory)
        for name, data in (
            ('torch/__init__.py', b'# torch fixture\n'),
            ('torch/lib/runtime.dll', b'runtime-dll'),
            ('pymss/__init__.py', b'# pymss fixture\n'),
            ('pymss_core/__init__.py', b'# pymss core fixture\n'),
        ):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    class ProbeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            payload = b'COMPONENT_PROBE={"torch":"2.7.1+cpu","cuda_available":false,"device":"cpu","gpu":null}\n'
            return payload, None

        def poll(self):
            return 0

    installer_progress = []
    with patch.object(components, 'MANIFEST', install_manifest), \
         patch.object(components, 'manifest', return_value=install_spec), \
         patch.object(components, 'download_urls'), \
         patch.object(components, 'download', side_effect=fake_download), \
         patch.object(components, 'extract_wheel', side_effect=fake_extract), \
         patch.object(components, 'load_config', return_value={'python': sys.executable}), \
         patch.object(components.subprocess, 'Popen', return_value=ProbeProcess()), \
         patch.object(components.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**18)), \
         patch('pipeline.job_lock', return_value=nullcontext()):
        installed = components.install(
            'cpu', progress=lambda done, total, text: installer_progress.append(text), root=install_root)
        receipt = json.loads((components.current(install_root) / 'installed.json').read_text(encoding='utf-8'))
        assert installed['ready'] and installed['verification']['level'] == 'metadata'
        assert all(len(row.get('sha256', '')) == 64 for row in receipt['critical_files'])
        full = components.full_verify(install_root)
        installer_progress.clear()
        no_op = components.install(
            'cpu', progress=lambda done, total, text: installer_progress.append(text), root=install_root)
    assert full['verified'] and full['progress']['sha256_checked'] == 5
    assert no_op['ready'] and any('元数据检查' in text for text in installer_progress)
    passed.append('installer-writes-critical-sha-and-status-compatible')

# UI must discover verification, remain busy while it runs, and defer closing.
owner = SimpleNamespace(
    runner=None,
    processing_active=False,
    storage=SimpleNamespace(busy=lambda: False),
    update_component_state=lambda: None,
)
ui_manifest = {
    'weights': [{'bytes': 1}],
    'shared_wheels': [],
    'torch_wheels': {'cuda': {'bytes': 1}, 'cpu': {'bytes': 1}},
    'install_bytes': {'cuda': 1, 'cpu': 1},
}
ready_state = {
    'ready': True, 'runtime': 'cpu', 'probe': {'torch': 'fixture'},
    'verification': {'level': 'metadata', 'content_verified': False,
                     'sha256_checked': False, 'state': 'metadata_ready'},
}


def cancelled_verification(progress=None, check=None):
    progress(0, 1, '正在完整校验 · fixture')
    while not check():
        time.sleep(0.005)
    return {'state': 'cancelled', 'verified': False, 'errors': [], 'missing': [],
            'unverified': [], 'corrupted': [], 'progress': {'sha256_checked': 0,
                                                             'total_hash_bytes': 1,
                                                             'hash_bytes': 0}}


with patch.object(components_ui.components, 'manifest', return_value=ui_manifest), \
     patch.object(components_ui.components, 'status', return_value=ready_state), \
     patch.object(components_ui.components, 'full_verify', side_effect=cancelled_verification), \
     patch.object(components_ui, 'load_config', return_value={}), \
     patch('dependency_profiles.status', return_value={'chinese': {'ready': False, 'bytes': 1}}), \
     patch('expression_component.status', return_value={'ready': False}), \
     patch('expression_component.SPEC', {'bytes': 1}):
    page = components_ui.ComponentsPage(owner)
    page.show()
    application.processEvents()
    assert page.verify.isEnabled()
    assert '未做 SHA-256' in page.badge.text()
    page.start_verify()
    assert wait_until(lambda: page.busy())
    assert not page.verify.isEnabled() and page.cancel.isVisible()
    assert page.cancel.text() == '取消校验'
    page.request_cancel()
    assert wait_until(lambda: not page.busy() and '已取消' in page.message.text())
    assert not page.cancel.isVisible()
    page.verification_completed({'state': 'changed_during_verification', 'verified': False,
                                 'unverified': [], 'corrupted': [], 'changed': [{}],
                                 'errors': [], 'progress': {}})
    assert '发生变化' in page.message.text() and '未确认完整性' in page.message.text()
    application.processEvents()

    page.verify.setEnabled(True)
    page.start_verify()
    assert wait_until(lambda: page.busy())
    closed_immediately = page.close()
    assert closed_immediately is False and page.close_pending and page.busy()
    assert '完成后关闭页面' in page.message.text()
    assert wait_until(lambda: not page.busy())
    assert wait_until(lambda: not page.isVisible())
    assert not page.worker.isRunning()
    passed.append('ui-discoverable-progress-cancel-and-close-deferred')

print(json.dumps({'state': 'passed', 'checks': passed}, ensure_ascii=False))
print('PASS: component metadata/full verification, corruption, cancellation, path boundaries and UI lifecycle')