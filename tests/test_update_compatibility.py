"""Unmodified 0.4.1 client -> narrow first hop -> new modules -> real GUI startup."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import load_config
from tools.build_app_update import require_compatible_clients
from tools.build_runtime_upgrade import build_bootstrap
from tools.build_portable import package_sources


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


client = json.loads((APP / 'packaging/update-clients.json').read_text(encoding='utf-8'))['clients'][0]
for item in client['fixtures'].values():
    assert hashlib.sha256((APP / item['fixture']).read_bytes()).hexdigest() == item['sha256']
old = module('bvwa_released_041_updater', APP / client['fixtures']['src/updater.py']['fixture'])
old_helper = module('bvwa_released_041_installer', APP / client['fixtures']['tools/update_apply.py']['fixture'])
assert set(client['ROOT_FILES']) == old_helper.ROOT_FILES
assert set(client['TOOL_FILES']) == old_helper.TOOL_FILES
new_names = ('pitch_accuracy.py', 'transcription_quality.py', 'vocal_timbre.py', 'components/world-LICENSE.txt')
for name in new_names:
    try:
        require_compatible_clients([record(name, b'fixture')])
        raise AssertionError('Direct-update build silently bypassed the old client allowlist')
    except ValueError as error:
        assert 'first hop' in str(error)

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='legacy-client-') as folder:
    root = Path(folder) / 'assistant'; root.mkdir()
    assets = Path(folder) / 'fixtures'; assets.mkdir()
    marker = b'fixed dependency sentinel'
    device_name = 'dependencies/vocal2midi2/inference/device_utils.py'
    device_source = (APP / device_name).read_bytes()
    manifest = json.dumps({'entries': [record('dependencies/runtime/keep.bin', marker),
                                       record(device_name, device_source)]}).encode('utf-8')
    for name, data in {'dependencies/manifest.json': manifest, 'dependencies/runtime/keep.bin': marker,
                       device_name: device_source,
                       'version.json': b'{"version":"0.4.1"}', 'app.py': b'# old application',
                       'tools/update_apply.py': (APP / client['fixtures']['tools/update_apply.py']['fixture']).read_bytes()}.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    config = dict(load_config(), check_for_updates=False, startup_auto_update=False, private_sentinel='preserved')
    kept = {'config.json': json.dumps(config).encode(), 'last_job.txt': b'@/jobs/private-task',
            'jobs/private-task/tuned.mid': b'USER MIDI', 'cache/publish-browser/bilibili/session.txt': b'ACCOUNT',
            'dependencies/optional/user-model.bin': b'OPTIONAL MODEL'}
    for name, data in kept.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    runtime_sha = hashlib.sha256(manifest).hexdigest()
    version = '9.9.9'
    files = {name: (APP / source).read_bytes() for name, source in package_sources().items()
             if name != 'config.json' and not name.startswith(('dependencies/', 'models/', 'vendor/'))}
    files.update({'version.json': json.dumps({'version': version, 'channel': 'stable'}).encode(),
                  'dependencies/manifest.json': manifest})
    runtime_name = 'BVWA-Runtime-Upgrade-v0.4.1.zip'
    runtime = assets / runtime_name
    records = [record(name, data) for name, data in files.items()]
    with zipfile.ZipFile(runtime, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items(): zipped.writestr(name, data)
        zipped.writestr('runtime-upgrade.json', json.dumps({'version': version,
            'source_runtime_sha256': runtime_sha, 'target_runtime_sha256': runtime_sha,
            'entries': records, 'removed': [],
            'launcher_migration': {'from':'BVWA.exe','to':'VWA.exe','legacy_alias':True}}))
    item = {'name': runtime_name, 'version': version, 'bytes': runtime.stat().st_size,
            'sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
            'source_runtime_sha256': runtime_sha, 'target_runtime_sha256': runtime_sha}
    first_hop = build_bootstrap(assets, item, 'BVWA-Program-Update.zip')
    with zipfile.ZipFile(assets / first_hop['name']) as zipped:
        first_records = json.loads(zipped.read('update-inventory.json'))['entries']
    assert {r['file'] for r in first_records} == {'app.py', 'version.json', 'tools/update_apply.py'}
    require_compatible_clients(first_records)
    for entry in first_records: old_helper.validate_file(entry)
    inventory = {'version': version, 'program_update': first_hop, 'update': first_hop}
    def reader(url, source):
        if url == old.API:
            return json.dumps({'tag_name': 'v' + version, 'draft': False, 'prerelease': False, 'body': 'fixture'}).encode()
        return json.dumps(inventory).encode()
    def download(selected, target, *args, **kwargs):
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets / selected['name'], target / selected['name'])
        return target / selected['name']
    with patch.object(old, 'ROOT', root), patch.dict(sys.modules, {'tools.update_apply': old_helper}):
        for source in ('direct', 'mirror', 'auto'):
            release = old.check_release(source, reader=reader)
            assert release['available'] and release['update'] == first_hop
        plan = old.prepare_update(release, root=root, downloader=download)
        assert old_helper.apply_update(plan)['state'] == 'passed'
    installed_metadata = json.loads((root / 'version.json').read_text())
    assert installed_metadata['version'] == version and installed_metadata['upgrade_pending']
    assert not (root / 'pitch_accuracy.py').exists(), 'New modules must follow installation of the new helper'
    assert all((root / name).read_bytes() == data for name, data in kept.items())
    # The narrow first hop must start before the new display modules arrive.
    first_hop_probe = '''
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication
import sys
sys.path.insert(0, str(Path.cwd()))
import app
assert Path(app.__file__).resolve().parent == Path.cwd()
assert not Path('i18n.py').exists() and not Path('ui_input.py').exists()
application=QApplication([])
with patch.object(app.QTimer,'singleShot'):
    window=app.UpgradeDialog(Path.cwd())
assert window.source.findData('gh-proxy')>=0
assert window.source.findData('pypi-tuna')>=0
assert window.worker is None
window.close()
print('Narrow first-hop GUI ready; no network requested')
'''
    first_result = subprocess.run([sys.executable, '-B', '-c', first_hop_probe], cwd=root,
                                  env=dict(os.environ, PYTHONPATH='', BVWA_TESTING='1', QT_QPA_PLATFORM='offscreen'),
                                  capture_output=True, timeout=30)
    assert first_result.returncode == 0, (first_result.stdout + first_result.stderr).decode('utf-8', errors='replace')
    installed = module('bvwa_newly_installed_helper', root / 'tools/update_apply.py')
    second = installed.prepare_runtime_upgrade(item, root, downloader=download)
    assert installed.apply_update(second)['state'] == 'passed'
    assert all((root / name).read_bytes() == data for name, data in kept.items())
    assert all((root / name).read_bytes() == files[name] for name in new_names)
    assert (root / 'dependencies/runtime/keep.bin').read_bytes() == marker
    result = subprocess.run([sys.executable, '-B', '-u', str(root / 'launch.py'), '--smoke-test'],
                            cwd=root, env=dict(os.environ, BVWA_TESTING='1', QT_QPA_PLATFORM='offscreen'),
                            capture_output=True, timeout=60)
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    assert result.returncode == 0 and 'GUI ready; startup smoke test passed' in output, output[-4000:]
    assert all((root / name).read_bytes() == data for name, data in kept.items())
print('PASS: pinned unmodified 0.4.1 client, direct/mirror/auto discovery, allowlisted first hop, new-module second hop, unchanged runtime/private state and real startup')
