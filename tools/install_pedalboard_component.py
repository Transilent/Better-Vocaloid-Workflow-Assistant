#!/usr/bin/env python3
"""Install the pinned Pedalboard wheel into an isolated VWA component tree.

This tool never installs into the interpreter's site-packages and never runs
pip. It verifies the manifest, wheel size/hash and wheel archive entries before
atomically promoting a tested component tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_MANIFEST = 'components/pedalboard.json'


class ComponentError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _manifest(app_root: Path) -> dict:
    path = app_root / COMPONENT_MANIFEST
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('format') != 1 or data.get('component') != 'pedalboard':
        raise ComponentError('Unsupported Pedalboard component manifest')
    wheel = data.get('wheel') or {}
    install = data.get('install') or {}
    if wheel.get('name') != 'pedalboard-0.9.26-cp312-cp312-win_amd64.whl':
        raise ComponentError('Unexpected Pedalboard wheel name')
    if wheel.get('sha256') != '816b7063d3befb00263bfee7656932ee5f51788c586d5184d4d61f7a85f61aea':
        raise ComponentError('Unexpected Pedalboard wheel hash')
    if wheel.get('bytes') != 3691266 or wheel.get('installed_bytes') != 9582994:
        raise ComponentError('Unexpected Pedalboard wheel size')
    for key in ('root', 'current', 'packages', 'receipt'):
        if not isinstance(install.get(key), str) or not install[key]:
            raise ComponentError('Incomplete Pedalboard install paths')
    license_data = data.get('license') or {}
    for key in ('license_file', 'notice_file', 'review_file'):
        relative = license_data.get(key)
        if not relative or not (app_root / relative).is_file():
            raise ComponentError('Missing component notice: ' + str(relative))
        expected = (license_data.get('hashes') or {}).get(key)
        if not isinstance(expected, str) or digest(app_root / relative) != expected:
            raise ComponentError('Component notice hash mismatch: ' + str(relative))
    return data


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or '\\' in value or ':' in value
            or any(part in ('', '.', '..') for part in path.parts)):
        raise ComponentError('Unsafe relative component path: ' + value)
    return path


def _under(root: Path, relative: str) -> Path:
    path = root.joinpath(*_safe_relative(relative).parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ComponentError('Component path escapes its root')
    return path


def _is_reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def _remove_tree(path: Path, root: Path) -> None:
    path, root = path.resolve(), root.resolve()
    if path == root or not path.is_relative_to(root):
        raise ComponentError('Refusing to remove an unsafe component path')
    if not path.exists():
        return
    if _is_reparse(path):
        raise ComponentError('Refusing to remove a reparse point')
    for folder, dirs, files in os.walk('\\\\?\\' + str(path) if os.name == 'nt' else path):
        for name in dirs + files:
            if _is_reparse(Path(folder) / name):
                raise ComponentError('Component tree contains a reparse point')
    shutil.rmtree('\\\\?\\' + str(path) if os.name == 'nt' else path)


def _verify_wheel(path: Path, spec: dict) -> None:
    wheel = spec['wheel']
    if not path.is_file() or path.name != wheel['name']:
        raise ComponentError('Expected wheel is absent or has the wrong filename')
    if path.stat().st_size != wheel['bytes'] or digest(path).lower() != wheel['sha256']:
        raise ComponentError('Pedalboard wheel size or SHA-256 mismatch')
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ComponentError('Corrupt wheel member: ' + bad)
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise ComponentError('Pedalboard wheel is not a valid ZIP file') from exc
    required = {'pedalboard/__init__.py', 'pedalboard_native.cp312-win_amd64.pyd'}
    if not required.issubset(names):
        raise ComponentError('Pedalboard wheel is missing required runtime modules')


def _download(spec: dict, target: Path) -> Path:
    wheel = spec['wheel']
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(wheel['url'], headers={'User-Agent': 'VWA-Pedalboard-Component/1'})
    with urlopen(request, timeout=60) as response, target.open('xb') as stream:
        if getattr(response, 'status', 200) != 200:
            raise ComponentError('Pedalboard download did not return HTTP 200')
        shutil.copyfileobj(response, stream, length=1024 * 1024)
    _verify_wheel(target, spec)
    return target


def _extract_wheel(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as wheel:
        for info in wheel.infolist():
            relative = PurePosixPath(info.filename)
            if (relative.is_absolute() or '\\' in info.filename or ':' in info.filename
                    or any(part in ('', '.', '..') for part in relative.parts)):
                raise ComponentError('Unsafe path inside Pedalboard wheel')
            if relative.parts and relative.parts[0].endswith('.data'):
                if len(relative.parts) < 3 or relative.parts[1] not in ('purelib', 'platlib'):
                    continue
                relative = PurePosixPath(*relative.parts[2:])
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ComponentError('Pedalboard wheel contains a symbolic link')
            target = destination.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(destination):
                raise ComponentError('Wheel destination escaped the component')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(info) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    total = sum(path.stat().st_size for path in destination.rglob('*') if path.is_file())
    if total != 9582994:
        raise ComponentError('Extracted Pedalboard size does not match the pin')


def _file_records(directory: Path) -> list[dict]:
    records = []
    for path in sorted(directory.rglob('*')):
        if path.is_file() and not _is_reparse(path) and '__pycache__' not in path.parts:
            records.append({'file': path.relative_to(directory).as_posix(),
                            'bytes': path.stat().st_size, 'sha256': digest(path)})
    return records


def _probe(python: Path, packages: Path, manifest_sha: str) -> dict:
    if not python.is_file():
        raise ComponentError('Trial Python interpreter is missing: ' + str(python))
    code = r'''
import json
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import pedalboard
from pedalboard import Compressor, HighpassFilter, Pedalboard, Reverb
required = ('Pedalboard', 'HighpassFilter', 'PeakFilter', 'Compressor', 'Reverb')
missing = [name for name in required if not hasattr(pedalboard, name)]
assert not missing, 'missing plugins: ' + ', '.join(missing)
sr = 44100
t = np.arange(sr * 3, dtype=np.float32) / sr
source = (0.15 * np.sin(2 * np.pi * 220 * t) + 0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
source[sr:] = 0
def render(chunked):
    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=70),
        Compressor(threshold_db=-22, ratio=2, attack_ms=20, release_ms=100),
        Reverb(room_size=0.5, damping=0.5, wet_level=0.25, dry_level=0.75, width=0.8),
    ])
    if not chunked:
        return board(source, sr, reset=True)
    return np.concatenate([
        board(source[i:i + 4096], sr, reset=(i == 0))
        for i in range(0, len(source), 4096)
    ])
whole = render(False)
chunked = render(True)
assert whole.shape == source.shape and np.isfinite(whole).all()
assert float(np.max(np.abs(whole - chunked))) == 0.0
assert float(np.sqrt(np.mean(whole[sr:] ** 2))) > 0.0
assert pedalboard.__version__ == '0.9.26'
print('PEDALBOARD_PROBE=' + json.dumps({
    'version': pedalboard.__version__,
    'numpy': np.__version__,
    'samples': len(whole),
    'finite': True,
    'reverb_tail_rms': float(np.sqrt(np.mean(whole[sr:] ** 2))),
    'max_chunk_difference': float(np.max(np.abs(whole - chunked))),
}))
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    completed = subprocess.run([str(python), '-B', '-c', code, str(packages)],
                               capture_output=True, text=True, timeout=60, env=env,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode:
        detail = (completed.stdout + completed.stderr)[-2500:]
        raise ComponentError('Pedalboard probe failed:\n' + detail)
    line = next((value for value in completed.stdout.splitlines()
                 if value.startswith('PEDALBOARD_PROBE=')), None)
    if not line:
        raise ComponentError('Pedalboard probe returned no receipt')
    result = json.loads(line.split('=', 1)[1])
    if result.get('version') != '0.9.26' or result.get('numpy') != '1.26.4':
        raise ComponentError('Pedalboard or NumPy version does not match the pin')
    # Manifest hash is recorded by the caller so the probe itself stays import-only.
    result['manifest_sha256'] = manifest_sha
    return result


def _status(app_root: Path, spec: dict) -> dict:
    root = _under(app_root, spec['install']['root'])
    current = _under(root, spec['install']['current'])
    receipt = _under(root, spec['install']['receipt'])
    try:
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if (saved.get('manifest_sha256') != digest(app_root / COMPONENT_MANIFEST)
                or saved.get('wheel_sha256') != spec['wheel']['sha256']):
            raise ComponentError('Component receipt does not match the current pin')
        for item in saved.get('files', []):
            path = current.joinpath(*_safe_relative(item['file']).parts)
            if (not path.is_file() or _is_reparse(path) or path.stat().st_size != item['bytes']
                    or digest(path) != item['sha256']):
                raise ComponentError('Installed Pedalboard file failed verification: ' + item['file'])
        return {'ready': True, 'version': spec['version'], 'root': str(current),
                'packages': str(_under(root, spec['install']['packages'])),
                'probe': saved.get('probe')}
    except (ComponentError, OSError, ValueError, KeyError, TypeError) as exc:
        return {'ready': False, 'version': spec['version'], 'error': str(exc)}


def _activate_import(app_root: Path, python: Path, packages: Path) -> Path:
    """Add only the extracted component to this disposable trial's Python."""
    code = "import site; print(next(iter(site.getsitepackages())))"
    completed = subprocess.run([str(python), '-B', '-c', code], capture_output=True,
                               text=True, timeout=30,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode:
        raise ComponentError('Cannot locate the trial Python site-packages: ' + completed.stderr[-1000:])
    site_packages = Path(completed.stdout.strip()).resolve()
    if not site_packages.is_relative_to(app_root.resolve()):
        raise ComponentError('Refusing to modify Python outside the selected trial app root')
    if not packages.resolve().is_relative_to(app_root.resolve()):
        raise ComponentError('Refusing to activate a package outside the selected trial app root')
    site_packages.mkdir(parents=True, exist_ok=True)
    marker = site_packages / 'vwa-pedalboard.pth'
    temporary = marker.with_name(marker.name + '.tmp')
    temporary.write_text(str(packages.resolve()) + '\n', encoding='utf-8')
    temporary.replace(marker)
    return marker


def _install(app_root: Path, spec: dict, wheel: Path, python: Path, force: bool,
             activate_import: bool = False) -> dict:
    current = _status(app_root, spec)
    if current['ready'] and not force:
        if activate_import:
            root = _under(app_root, spec['install']['root'])
            packages = _under(root, spec['install']['packages'])
            current['activation'] = str(_activate_import(app_root, python, packages))
        return current
    root = _under(app_root, spec['install']['root'])
    root.mkdir(parents=True, exist_ok=True)
    manifest_sha = digest(app_root / COMPONENT_MANIFEST)
    stage = root / ('.staging-' + uuid.uuid4().hex)
    destination = _under(root, spec['install']['current'])
    backup = root / 'previous'
    moved_previous = False
    try:
        current_relative = _safe_relative(spec['install']['current'])
        packages_relative = _safe_relative(spec['install']['packages'])
        receipt_relative = _safe_relative(spec['install']['receipt'])
        if (packages_relative.parts[:len(current_relative.parts)] != current_relative.parts
                or receipt_relative.parts[:len(current_relative.parts)] != current_relative.parts):
            raise ComponentError('Component paths must be inside the current tree')
        packages = stage.joinpath(*packages_relative.parts[len(current_relative.parts):])
        packages.parent.mkdir(parents=True, exist_ok=True)
        _extract_wheel(wheel, packages)
        license_dir = app_root / 'components'
        shutil.copy2(license_dir / 'pedalboard-LICENSE.txt', stage / 'LICENSE.txt')
        shutil.copy2(license_dir / 'pedalboard-NOTICE.txt', stage / 'NOTICE.txt')
        shutil.copy2(license_dir / 'pedalboard-THIRD_PARTY_NOTICES.md', stage / 'THIRD_PARTY_NOTICES.md')
        probe = _probe(python, packages, manifest_sha)
        receipt = {'format': 1, 'component': 'pedalboard', 'version': spec['version'],
                   'manifest_sha256': manifest_sha, 'wheel_sha256': spec['wheel']['sha256'],
                   'wheel_bytes': spec['wheel']['bytes'], 'probe': probe,
                   'files': _file_records(stage)}
        receipt_path = stage.joinpath(*receipt_relative.parts[len(current_relative.parts):])
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if destination.exists():
            _remove_tree(backup, root)
            destination.replace(backup)
            moved_previous = True
        stage.replace(destination)
    except Exception:
        if moved_previous and not destination.exists() and backup.exists():
            backup.replace(destination)
        _remove_tree(stage, root)
        raise
    result = _status(app_root, spec)
    if not result['ready']:
        raise ComponentError('Installed Pedalboard component failed final verification')
    if activate_import:
        packages = _under(root, spec['install']['packages'])
        result['activation'] = str(_activate_import(app_root, python, packages))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'status'))
    parser.add_argument('--app-root', type=Path, default=ROOT,
                        help='Root of an isolated VWA copy (default: this checkout)')
    parser.add_argument('--wheel', type=Path,
                        help='Previously downloaded cp312 win_amd64 wheel')
    parser.add_argument('--python', type=Path,
                        help='Trial Python 3.12 x64 interpreter')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--activate-import', action='store_true',
                        help='Add a .pth file to this trial copy so its app Python imports the component')
    args = parser.parse_args(argv)
    app_root = args.app_root.resolve()
    spec = _manifest(app_root)
    if args.action == 'status':
        result = _status(app_root, spec)
    else:
        if args.wheel:
            wheel = args.wheel.resolve()
            _verify_wheel(wheel, spec)
            temporary = None
        elif args.action == 'install':
            temporary = tempfile.TemporaryDirectory(prefix='vwa-pedalboard-')
            wheel = _download(spec, Path(temporary.name) / spec['wheel']['name'])
        else:
            raise ComponentError('check requires --wheel; pass install to download the pinned wheel')
        try:
            result = ({'ready': True, 'version': spec['version'],
                       'wheel': str(wheel), 'wheel_sha256': spec['wheel']['sha256']}
                      if args.action == 'check'
                      else _install(app_root, spec, wheel,
                                    (args.python or app_root / 'dependencies/vocal2midi/python/python.exe').resolve(),
                                    args.force, args.activate_import))
        finally:
            if temporary is not None:
                temporary.cleanup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ready') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ComponentError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        raise SystemExit(1)