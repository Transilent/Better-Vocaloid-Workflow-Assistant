"""Component integrity and interrupted-install recovery, using synthetic local fixtures."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import expression_component as world
import optional_components as components


def make_tree(root, payload):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'ready.txt').write_text(payload, encoding='utf-8')


def tree_ready(root):
    path = Path(root) / 'ready.txt'
    return path.is_file() and path.read_text(encoding='utf-8') != 'invalid'


def injected_replace_failure():
    original = Path.replace
    active = [True]

    def replace(self, target):
        target = Path(target)
        if active[0] and self.name == 'staging' and target.name == 'current':
            active[0] = False
            raise OSError('injected stage->current failure')
        return original(self, target)

    return replace


def wheel_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in (
            ('testpackage/__init__.py', b'VALUE=1\n'),
            ('testpackage-1.dist-info/LICENSE', b'Test fixture\n'),
        ):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


passed = []
with tempfile.TemporaryDirectory(prefix='component-recovery-', dir=APP / 'work') as temporary:
    base = Path(temporary)

    root = base / 'normal-rollback'
    make_tree(root / 'current', 'old')
    make_tree(root / 'staging', 'new')
    try:
        with patch.object(Path, 'replace', injected_replace_failure()):
            components.replace_verified_tree(
                root / 'staging', root / 'current', root / 'previous', root, tree_ready)
        raise AssertionError('Normal transaction failure was not raised')
    except OSError:
        pass
    assert (root / 'current/ready.txt').read_text(encoding='utf-8') == 'old'
    assert not (root / 'previous').exists()
    passed.append('normal-rollback')

    root = base / 'prior-crash-second-failure'
    make_tree(root / 'previous', 'old')
    make_tree(root / 'staging', 'new')
    try:
        with patch.object(Path, 'replace', injected_replace_failure()):
            components.replace_verified_tree(
                root / 'staging', root / 'current', root / 'previous', root, tree_ready)
        raise AssertionError('Second interrupted-install failure was not raised')
    except OSError:
        pass
    assert (root / 'current/ready.txt').read_text(encoding='utf-8') == 'old'
    assert not (root / 'previous').exists()
    passed.append('prior-crash-second-failure')

    root = base / 'success-cleanup'
    make_tree(root / 'current', 'old')
    make_tree(root / 'previous', 'stale')
    make_tree(root / 'staging', 'new')
    components.replace_verified_tree(
        root / 'staging', root / 'current', root / 'previous', root, tree_ready)
    assert (root / 'current/ready.txt').read_text(encoding='utf-8') == 'new'
    assert not (root / 'staging').exists() and not (root / 'previous').exists()
    passed.append('success-cleanup')

    root = base / 'post-verify-failure'
    make_tree(root / 'current', 'old')
    make_tree(root / 'staging', 'new')
    reject_current = [True]

    def verify_postcommit(candidate):
        marker = Path(candidate) / 'ready.txt'
        if reject_current[0] and marker.is_file() and marker.read_text(encoding='utf-8') == 'new':
            return False
        return tree_ready(candidate)

    try:
        components.replace_verified_tree(
            root / 'staging', root / 'current', root / 'previous', root, verify_postcommit)
        raise AssertionError('Post-commit verification failure was not raised')
    except ValueError:
        pass
    assert (root / 'current/ready.txt').read_text(encoding='utf-8') == 'old'
    passed.append('post-verify-rollback')

    root = base / 'previous-recovery'
    make_tree(root / 'previous', 'old')
    assert components.recover_verified_previous(root, tree_ready)
    assert (root / 'current/ready.txt').read_text(encoding='utf-8') == 'old'
    assert not (root / 'previous').exists()
    root = base / 'invalid-previous'
    make_tree(root / 'previous', 'invalid')
    assert not components.recover_verified_previous(root, tree_ready)
    assert (root / 'previous').exists() and not (root / 'current').exists()
    passed.append('previous-recovery-boundary')

    wheel = wheel_bytes()
    spec = dict(world.SPEC, sha256=hashlib.sha256(wheel).hexdigest(), bytes=len(wheel))
    download_calls = [0]

    def fake_download(item, folder, progress, check=None, source='auto'):
        download_calls[0] += 1
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / item['name']
        target.write_bytes(wheel)
        return target

    world_root = base / 'world'
    probe = SimpleNamespace(returncode=0, stderr=b'')
    with (patch.object(world, 'SPEC', spec),
          patch.object(components, 'download', side_effect=fake_download),
          patch.object(world.subprocess, 'run', return_value=probe)):
        installed = world.install(root=world_root)
        assert installed['ready'] and world.verify(world_root)
        calls_after_install = download_calls[0]
        assert world.install(root=world_root)['ready']
        assert download_calls[0] == calls_after_install
        passed.append('healthy-install-noop')

        payload = world_root / 'current/packages/testpackage/__init__.py'
        original = payload.read_bytes()
        payload.write_bytes(b'VALUE=2\n')
        assert len(payload.read_bytes()) == len(original)
        assert not world.status(world_root)['ready']
        with patch.object(world, 'COMPONENT', world_root):
            try:
                world.load()
                raise AssertionError('Corrupt WORLD payload was accepted')
            except FileNotFoundError as error:
                assert '重新安装' in str(error)
        passed.append('world-same-size-corruption')

        assert world.install(root=world_root)['ready']
        assert payload.read_bytes() == original
        assert (world_root / 'current/WORLD-LICENSE.txt').is_file()
        passed.append('world-ordinary-repair')

        calls_after_repair = download_calls[0]
        assert world.install(root=world_root)['ready']
        assert download_calls[0] == calls_after_repair
        (world_root / 'current').replace(world_root / 'previous')
        with patch.object(components, 'download', side_effect=AssertionError('download must not run')):
            assert world.install(root=world_root)['ready']
        assert (world_root / 'current').is_dir() and not (world_root / 'previous').exists()
        passed.append('verified-previous-startup-recovery')

print(json.dumps({'state': 'passed', 'checks': passed}, ensure_ascii=False))
print('PASS: component integrity, no-op, rollback and interrupted recovery')
