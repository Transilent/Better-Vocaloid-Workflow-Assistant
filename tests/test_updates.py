"""Update discovery, hash validation, state preservation and transaction rollback."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import subprocess
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import updater
from download_sources import accelerated_url, download_urls
from tools.update_apply import apply_update

assert updater.is_newer('0.3.0', '0.3.0-preview.1')
assert not updater.is_newer('0.2.0', '0.3.0-preview.1')
assert updater.is_newer('0.3.0-preview.10', '0.3.0-preview.2')
assert accelerated_url('https://github.com/a/b/releases/download/v1/x.zip') == 'https://api.gitproxy.dev/github.com/a/b/releases/download/v1/x.zip'
assert accelerated_url('https://huggingface.co/a/b/resolve/commit/x.bin?download=true') == 'https://hf-mirror.com/a/b/resolve/commit/x.bin?download=true'
for url in ('https://github.com.evil.test/file', 'https://user:password@github.com/file', 'http://github.com/file', 'https://download.pytorch.org/a.whl'):
    assert accelerated_url(url) == url
assert len(download_urls('https://github.com/a/b', 'auto')) == 2
assert len(download_urls('https://github.com/a/b', 'direct')) == 1

# Validate the future archive inventory without creating a distributable package.
inventory = json.loads((APP / 'portable-files.json').read_text(encoding='utf-8'))['entries']
from common import file_hash
from tools.update_apply import validate_file
for entry in inventory:
    assert (APP / entry['source']).is_file(), entry
    if entry['file'] == 'config.json' or entry['file'].startswith(('dependencies/', 'models/', 'components/')):
        continue
    source = APP / entry['source']
    validate_file({'file': entry['file'], 'bytes': source.stat().st_size, 'sha256': file_hash(source)})
for script, name in (('build_app_update.py', 'BVWA-App-Update.zip'), ('build_portable.py', 'gate-test.zip')):
    target = APP / 'work' / name
    assert not target.exists()
    result = subprocess.run([sys.executable, '-B', str(APP / 'tools' / script), '--output', str(target)],
                            capture_output=True, timeout=20)
    assert result.returncode == 2 and b'Local user testing must be confirmed' in result.stderr
    assert not target.exists(), 'Archive creation must wait for user testing confirmation'

with tempfile.TemporaryDirectory(prefix='app-update-', dir=APP / 'work') as folder:
    root = Path(folder)
    (root / 'dependencies').mkdir()
    (root / 'dependencies/manifest.json').write_bytes(b'fixed-runtime')
    runtime = hashlib.sha256(b'fixed-runtime').hexdigest()
    kept = {'config.json': b'user settings', 'jobs/song.mid': b'user song',
            'cache/browser-profile/session': b'private login',
            'dependencies/optional/model.bin': b'installed optional model'}
    for name, data in kept.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / 'app.py').write_bytes(b'OLD APP')
    (root / 'version.json').write_bytes(b'{"version":"0.2.0"}')

    def package(files):
        buffer = io.BytesIO()
        entries = [{'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                   for name, data in files.items()]
        with zipfile.ZipFile(buffer, 'w') as zipped:
            for name, data in files.items():
                zipped.writestr(name, data)
            zipped.writestr('update-inventory.json', json.dumps({'version': '9.9.9',
                              'runtime_sha256': runtime, 'entries': entries}))
        data = buffer.getvalue()
        release = {'version': '9.9.9', 'available': True, 'update': {'name': updater.UPDATE_NAME,
                   'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'runtime_sha256': runtime}}
        def download(item, target, progress, check, **kwargs):
            assert item['sha256'] == hashlib.sha256(data).hexdigest()
            target.mkdir(parents=True, exist_ok=True)
            archive = target / item['name']
            archive.write_bytes(data)
            return archive
        return release, download

    files = {'app.py': b'NEW APP', 'version.json': b'{"version":"9.9.9"}', 'assets/check.svg': b'<svg/>'}
    release, download = package(files)
    plan = updater.prepare_update(release, root=root, downloader=download)
    assert (root / 'app.py').read_bytes() == b'OLD APP', 'Downloading must not install or overwrite the running app'
    assert updater.pending_update(root)['version'] == '9.9.9'
    assert apply_update(plan)['state'] == 'passed'
    assert all((root / n).read_bytes() == data for n, data in files.items())
    assert all((root / n).read_bytes() == data for n, data in kept.items())
    assert updater.pending_update(root) is None

    for forbidden in ('config.json', 'jobs/song.mid', '../outside.py', 'dependencies/optional/model.bin'):
        bad_release, bad_download = package(dict(files, **{forbidden: b'BAD'}))
        try:
            updater.prepare_update(bad_release, root=root, downloader=bad_download)
            raise AssertionError('Disallowed update destination accepted')
        except ValueError:
            pass
        assert all((root / n).read_bytes() == data for n, data in kept.items())

    release, download = package({'app.py': b'ANOTHER APP', 'version.json': b'{"version":"9.9.9"}'})
    plan = updater.prepare_update(release, root=root, downloader=download)
    old = {name: (root / name).read_bytes() for name in ('app.py', 'version.json')}
    replace = Path.replace
    def fail_second(path, destination):
        if path.name == 'version.json.bvwa-update':
            raise PermissionError('simulated locked file')
        return replace(path, destination)
    with patch.object(Path, 'replace', fail_second):
        try:
            apply_update(plan)
            raise AssertionError('Failed update was accepted')
        except PermissionError:
            pass
    assert all((root / n).read_bytes() == data for n, data in old.items()), 'Failed update must restore all previous files'
    assert all((root / n).read_bytes() == data for n, data in kept.items())
    assert updater.pending_update(root) is None, 'Failed updates must not be offered repeatedly'

    release, download = package(files)
    release['update']['sha256'] = '0' * 64
    def corrupted(item, target, progress, check, **kwargs):
        target.mkdir(parents=True, exist_ok=True)
        archive = target / item['name']
        archive.write_bytes(b'x' * item['bytes'])
        return archive
    try:
        updater.prepare_update(release, root=root, downloader=corrupted)
        raise AssertionError('Invalid update hash accepted')
    except ValueError:
        pass

def reader(url, source):
    if url == updater.API:
        raise OSError('simulated unavailable GitHub API')
    assert url.endswith('/latest/download/release-assets.json') and source == 'mirror'
    return json.dumps({'version': '9.9.9', 'update': {'name': updater.UPDATE_NAME}}).encode()
assert updater.check_release('mirror', reader)['version'] == '9.9.9'

def legacy_reader(url, source):
    if url == updater.API:
        raise OSError('simulated unavailable GitHub API')
    return json.dumps({'archive': {'name': 'BVWA-Windows-x64.zip'}, 'parts': [{'name': 'part'}]}).encode()
legacy = updater.check_release('mirror', legacy_reader)
assert legacy['legacy'] and not legacy['available'] and legacy['version'] is None
print('PASS: update source routing, API fallback, staged verification, protected data, rollback and hash rejection')
