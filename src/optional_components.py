"""Pinned, resumable optional-component installer; the base runtime is untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, file_hash, load_config, direct_urlopen, native_path
import component_health
from download_sources import download_urls, source_name

COMPONENT = ROOT / 'dependencies/optional/bs-roformer'
MANIFEST = ROOT / 'components/roformer.json'


def manifest():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def current(root=COMPONENT):
    return Path(root) / 'current'


def _status_tree(directory):
    report = component_health.metadata_status(Path(directory), MANIFEST)
    if not report['metadata_ready']:
        detail = report['errors'][0]['message'] if report['errors'] else '文件缺失或元数据不一致'
        raise ValueError(detail)
    return {
        'ready': True,
        'runtime': report['runtime'],
        'probe': report['probe'],
        'verification': {
            'level': 'metadata',
            'content_verified': False,
            'sha256_checked': False,
            'state': 'metadata_ready',
        },
    }


def status(root=COMPONENT):
    """Fast metadata status; this intentionally does not hash model/runtime bytes."""
    try:
        return _status_tree(current(root))
    except (OSError, ValueError, KeyError, TypeError):
        return {
            'ready': False,
            'runtime': None,
            'verification': {
                'level': 'metadata',
                'content_verified': False,
                'sha256_checked': False,
                'state': 'not_ready',
            },
        }


def _directory_ready(directory):
    """Receipt/path/size check used by installation transactions."""
    try:
        return _status_tree(directory)['ready']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def full_verify(root=COMPONENT, progress=None, check=None):
    """Explicitly hash manifest weights and receipt-critical files.

    This is cancellable through ``check``, reports byte progress through
    ``progress(done, total, message)``, and never loads the model or uses the
    network. Missing expected SHAs are reported as unverified rather than ready.
    """
    return component_health.verify_component(current(root), MANIFEST,
                                             progress=progress, check=check)


def require_ready():
    if not status()['ready']:
        raise FileNotFoundError('BS-RoFormer 组件元数据检查未通过；文件可能未安装、缺失或路径无效。请在“模型与组件”页面重新安装或执行完整校验。')
    return current()


def cancelled(check):
    if check and check():
        raise InterruptedError('已取消安装；已下载的数据会保留，重试可继续下载。')


def download(item, folder, progress, check=None, opener=direct_urlopen, source='auto', workers=None):
    """Honor Range only when the server confirms the exact offset and total."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = item['name']
    if name in ('', '.', '..') or Path(name).name != name or any(c in name for c in ('/', '\\', ':')):
        raise ValueError('Invalid download filename')
    target = folder / name
    partial = folder / (name + '.partial')
    routes = download_urls(item['url'], source)
    cancelled(check)
    if target.is_file() and target.stat().st_size == item['bytes'] and file_hash(target) == item['sha256']:
        progress(item['bytes'], item['bytes'], '已校验下载文件')
        return target
    if workers is None:
        from common import load_config
        workers = int(load_config(False).get('download_workers', 4))
    if not 1 <= workers <= 8:
        raise ValueError('下载连接数应为 1–8。')
    if workers > 1 and item['bytes'] >= 8 * 1024**2 and not partial.exists():
        from parallel_download import download as parallel, RangeUnsupported
        try:
            return parallel(item, folder, progress, check, source=source, workers=workers, opener=opener)
        except RangeUnsupported:
            progress(0, item['bytes'], '当前下载源未支持分段，切换为单连接续传')
            from parallel_download import sequential_partial
            sequential_partial(item, folder)
    elif workers == 1 and not partial.exists():
        from parallel_download import sequential_partial
        sequential_partial(item, folder)
    for attempt in range(3):
        cancelled(check)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == item['bytes']:
            if file_hash(partial) == item['sha256']:
                partial.replace(target)
                return target
            partial.unlink()
            offset = 0
        elif offset > item['bytes']:
            partial.unlink()
            offset = 0
        headers = {'User-Agent': 'pip/25.0 (Vocaloid-Workflow-Assistant)', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        route = routes[min(attempt, len(routes) - 1)]
        request = urllib.request.Request(route, headers=headers)
        progress(offset, item['bytes'], '连接' + source_name(route))
        try:
            with opener(request, timeout=30) as response:
                code = response.status
                if code == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-') or not content_range.endswith('/' + str(item['bytes'])):
                        raise ValueError('服务器返回了错误的下载续传范围。')
                elif code == 200:
                    offset = 0  # Server ignored Range: replace the partial, never append.
                else:
                    raise ValueError(f'下载服务器返回状态 {code}')
                with partial.open('ab' if offset else 'wb') as writer:
                    progress(offset, item['bytes'], '正在下载 · ' + source_name(route))
                    while block := response.read(1024 * 1024):
                        cancelled(check)
                        writer.write(block)
                        offset += len(block)
                        if offset > item['bytes']:
                            raise ValueError('下载文件超出清单大小。')
                        progress(offset, item['bytes'], '正在下载 · ' + source_name(route))
            cancelled(check)
            if offset != item['bytes']:
                raise OSError('下载未完成，正在重试。')
            if file_hash(partial) != item['sha256']:
                partial.unlink()
                from parallel_download import ChecksumMismatch
                raise ChecksumMismatch('下载文件 SHA-256 校验失败，请重试。')
            partial.replace(target)
            segments=folder/(name+'.segments')
            if segments.exists():
                from task_store import checked_tree
                checked_tree(segments,folder)
                shutil.rmtree(segments)
            return target
        except InterruptedError:
            raise
        except ValueError as exc:
            from parallel_download import ChecksumMismatch
            if not isinstance(exc, ChecksumMismatch) or attempt == 2:
                raise
            progress(0, item['bytes'], '文件校验失败，尝试其他下载源')
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise
            progress(offset, item['bytes'], '连接中断，正在重连')
            time.sleep(attempt + 1)
    raise RuntimeError('Download failed')


def extract_wheel(archive, directory, check=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as wheel:
        for info in wheel.infolist():
            cancelled(check)
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename:
                raise ValueError('Wheel contains an escaping path')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Wheel contains a symbolic link')
            # This is an inference runtime; C++ development files are unnecessary.
            if info.filename.startswith(('torch/include/', 'torch/share/cmake/')):
                continue
            parts = path.parts
            if parts and parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue  # We use the Python API, not command-line entry points.
                parts = parts[2:]
            target = directory.joinpath(*parts).resolve()
            if not target.is_relative_to(directory):
                raise ValueError('Wheel destination escaped the component')
            if os.name == 'nt':
                target = Path('\\\\?\\' + str(target))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(info) as src, target.open('wb') as dst:
                while block := src.read(1024 * 1024):
                    cancelled(check)
                    dst.write(block)


def remove_owned_tree(path, root):
    """Only remove this component's disposable stage/backup, rejecting reparse points."""
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError('Cleanup target escaped component directory')
    if not path.exists():
        return
    walk_root = '\\\\?\\' + str(resolved) if os.name == 'nt' else str(resolved)
    for folder, dirs, files in os.walk(walk_root, followlinks=False):
        for candidate in [Path(folder), *(Path(folder) / name for name in dirs + files)]:
            attributes = candidate.lstat()
            if candidate.is_symlink() or getattr(attributes, 'st_file_attributes', 0) & 1024:
                raise ValueError('Cleanup target contains a reparse point')
    shutil.rmtree(walk_root)


def recover_verified_previous(root, ready):
    """Restore only a metadata-valid backup when an interrupted swap left no current tree."""
    root = Path(root).resolve()
    destination, backup = current(root), root / 'previous'
    if destination.exists() or not backup.exists() or not ready(backup):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup.replace(destination)
    return True


def replace_verified_tree(stage, destination, backup, root, ready):
    """Promote a metadata-valid stage while retaining the last valid tree until commit."""
    root = Path(root).resolve()
    stage, destination, backup = Path(stage), Path(destination), Path(backup)
    paths = (stage, destination, backup)
    resolved = [path.resolve() for path in paths]
    if any(path == root or not path.is_relative_to(root) for path in resolved) or len(set(resolved)) != 3:
        raise ValueError('Component transaction path escaped its root')
    if not ready(stage):
        raise ValueError('Staged component failed metadata verification')

    quarantine = root / 'rejected-current'
    previous_ready = backup.exists() and ready(backup)
    restore_source = None
    if destination.exists():
        if ready(destination):
            # The current tree itself is last-known-good, so it becomes the rollback copy.
            remove_owned_tree(backup, root)
            destination.replace(backup)
            restore_source = backup
        else:
            remove_owned_tree(quarantine, root)
            destination.replace(quarantine)
            if previous_ready:
                restore_source = backup
            elif ready(quarantine):
                restore_source = quarantine
    elif previous_ready:
        # Prior crash left current absent. Keep this verified backup in place until commit.
        restore_source = backup

    try:
        stage.replace(destination)
        if not ready(destination):
            raise ValueError('Installed component failed metadata verification')
    except BaseException:
        if destination.exists():
            remove_owned_tree(destination, root)
        if restore_source is not None and restore_source.exists():
            restore_source.replace(destination)
        raise

    # Cleanup follows receipt/path/size checks; content hashes remain an explicit full verification.
    remove_owned_tree(backup, root)
    remove_owned_tree(quarantine, root)


def probe(directory):
    sys.path.insert(0, str(Path(directory) / 'packages'))
    os.environ['NUMBA_CACHE_DIR'] = native_path(ROOT / 'cache/numba')
    os.environ['PYMSS_PLUGINS_DIR'] = str(ROOT / 'cache/pymss-plugins')
    import torch
    from pymss import MSSeparator
    torch.set_num_threads(6)
    test = torch.eye(2)
    assert torch.equal(test @ test, test)
    cuda = torch.cuda.is_available()
    if cuda:
        assert torch.isfinite(torch.ones(32, 32, device='cuda') @ torch.ones(32, 32, device='cuda')).all().item()
    spec = manifest()
    with MSSeparator(model_type='bs_roformer', model_path=str(Path(directory) / spec['weights'][0]['name']),
                     config_path=str(Path(directory) / 'config.yaml'), device='auto', use_tta=False,
                     store_dirs=str(ROOT / 'cache/component-probe'),
                     inference_params={'batch_size': 1, 'normalize': False}) as separator:
        device = str(separator.device)
    result = {'torch': torch.__version__, 'cuda_available': cuda, 'device': device,
              'gpu': torch.cuda.get_device_name() if cuda else None}
    print('COMPONENT_PROBE=' + json.dumps(result), flush=True)


def install(runtime, progress=print, check=None, root=COMPONENT, force=False, source='auto'):
    download_urls('https://github.com/', source)  # Validate before modifying the install stage.
    if runtime not in ('cpu', 'cuda'):
        raise ValueError('请选择 CPU 或 NVIDIA GPU 运行库。')
    if sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32 or sys.platform != 'win32':
        raise RuntimeError('此组件需要助手自带的 Windows x64 Python 3.12。')
    from pipeline import job_lock
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with job_lock(root):
        recover_verified_previous(root, _directory_ready)
        existing = status(root)
        if not force and existing['ready'] and existing['runtime'] == runtime:
            progress(1, 1, '元数据检查已通过；未重新哈希大文件，可在组件页执行完整校验')
            return existing
        spec = manifest()
        items = spec['weights'] + spec['shared_wheels'] + [spec['torch_wheels'][runtime]]
        total = sum(item['bytes'] for item in items)
        needed = spec['install_bytes'][runtime] + total + 512 * 1024**2
        if shutil.disk_usage(root).free < needed:
            raise OSError(f'安装空间不足，请至少预留 {needed / 10**9:.1f} GB。')
        stage, backup, destination = root / 'staging', root / 'previous', current(root)
        remove_owned_tree(stage, root)
        stage.mkdir()
        completed = 0
        downloads = []
        for index, item in enumerate(items):
            cancelled(check)
            def update(done, count, message, base=completed, number=index + 1):
                progress(base + done, total, f'{message} · 文件 {number}/{len(items)}')
            download_path = download(item, root / 'downloads', update, check, source=source)
            downloads.append(download_path)
            if item['name'].endswith('.whl'):
                progress(completed + item['bytes'], total, '正在安装运行库')
                extract_wheel(download_path, stage / 'packages', check)
            else:
                shutil.copyfile(download_path, stage / item['name'])
            completed += item['bytes']
        cancelled(check)
        progress(total, total, '正在验证模型与运行环境')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen([load_config()['python'], '-B', '-u', str(ROOT / 'optional_components.py'), '--probe', str(stage)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = b''
        started = time.monotonic()
        try:
            while True:
                try:
                    output, _ = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    cancelled(check)
                    if time.monotonic() - started > 180:
                        raise TimeoutError('组件验证超时，请重试。')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        text = output.decode('utf-8', errors='replace')
        if process.returncode or 'COMPONENT_PROBE=' not in text:
            raise RuntimeError('运行环境验证失败：\n' + text[-2500:])
        probed = json.loads(next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith('COMPONENT_PROBE=')))
        if probed['torch'] != '2.7.1+' + ('cu128' if runtime == 'cuda' else 'cpu'):
            raise ValueError('Installed runtime version does not match the pinned component')
        critical = []
        for file in (stage / 'packages/torch/lib').glob('*.dll'):
            critical.append({'file': file.relative_to(stage).as_posix(), 'bytes': file.stat().st_size,
                             'sha256': file_hash(file)})
        for name in ('packages/torch/__init__.py', 'packages/pymss/__init__.py', 'packages/pymss_core/__init__.py'):
            path = stage / name
            critical.append({'file': name, 'bytes': path.stat().st_size, 'sha256': file_hash(path)})
        atomic_json(stage / 'installed.json', {'runtime': runtime, 'probe': probed, 'critical_files': critical,
                    'manifest_sha256': file_hash(MANIFEST),
                    'source_sha256': {item['name']: item['sha256'] for item in items}})
        if not _directory_ready(stage):
            if check and check():
                cancelled(check)
            raise ValueError('暂存的 BS-RoFormer 组件未通过完整性校验')
        cancelled(check)
        replace_verified_tree(stage, destination, backup, root, _directory_ready)
        for path in downloads:
            path.unlink(missing_ok=True)
        progress(total, total, '安装完成，可以使用 BS-RoFormer')
        return status(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', choices=('cpu', 'cuda'))
    parser.add_argument('--probe')
    parser.add_argument('--verify', action='store_true',
                        help='explicitly hash manifest weights and receipt-critical files')
    from download_sources import SOURCES
    parser.add_argument('--download-source', choices=SOURCES, default='auto')
    args = parser.parse_args()
    if args.probe:
        probe(args.probe)
    elif args.verify:
        try:
            report = full_verify(progress=lambda done, total, text: print(
                f'{done}/{total}: {text}', file=sys.stderr, flush=True))
        except KeyboardInterrupt:
            print(json.dumps({'state': 'cancelled', 'verified': False}, ensure_ascii=False))
            raise SystemExit(130)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report['verified'] else 2)
    elif args.install:
        install(args.install, lambda done, total, text: print(f'{done}/{total}: {text}', flush=True), source=args.download_source)
