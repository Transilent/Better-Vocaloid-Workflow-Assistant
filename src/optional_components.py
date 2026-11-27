"""Pinned, resumable optional-component installer; the base runtime is untouched."""
import argparse
from contextlib import contextmanager
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, file_hash, load_config, direct_urlopen, native_path
from common import exclusive_sidecar, SidecarBusy
import component_health
from download_sources import component_download_urls, normalize_component_source, source_name
from operation_diagnostics import DownloadTrace, download_file, operation_event

COMPONENT = ROOT / 'dependencies/optional/bs-roformer'
MANIFEST = ROOT / 'components/roformer.json'
_NOT_READY = 'BS-RoFormer 组件元数据检查未通过；文件可能未安装、缺失或路径无效。请在“模型与组件”页面重新安装或执行完整校验。'


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


def _pinned_onnx_weights(spec):
    """Return pinned ONNX weight entries [(name, bytes, sha256), ...], variants first."""
    entries = []
    for item in spec.get('weights', ()):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '')
        if not name.lower().endswith('.onnx'):
            continue
        for variant in item.get('variants') or ():
            if not isinstance(variant, dict):
                continue
            v_name = str(variant.get('name') or '')
            v_size = variant.get('bytes')
            v_sha = str(variant.get('sha256') or '').strip().lower()
            if v_name.lower().endswith('.onnx') and type(v_size) is int and v_size > 0 and len(v_sha) == 64:
                entries.append((v_name, v_size, v_sha))
        size = item.get('bytes')
        sha = str(item.get('sha256') or '').strip().lower()
        if type(size) is int and size > 0 and len(sha) == 64:
            entries.append((name, size, sha))
    return entries


def _manual_onnx_match(root, manifest_path=MANIFEST):
    """Detect a pinned FP16/FP32 ONNX weight placed manually without installed.json."""
    root_path = Path(root)
    cur_path = current(root_path)
    if component_health._ancestor_reparse_issue(root_path):
        return None
    if cur_path.exists() and component_health._ancestor_reparse_issue(cur_path):
        return None
    if (cur_path / 'installed.json').exists():
        return None
    manifest_raw, spec, issue, _ = component_health._read_json_regular(
        manifest_path, component_health.MANIFEST_MAX_BYTES)
    if issue or not isinstance(spec, dict) or spec.get('engine') != 'onnx-webgpu':
        return None
    pinned = _pinned_onnx_weights(spec)
    if not pinned:
        return None
    by_size = {size: sha for _, size, sha in pinned}
    names = list(dict.fromkeys(name for name, _, _ in pinned))
    for folder in (cur_path, root_path):
        if not folder.is_dir() or component_health._ancestor_reparse_issue(folder):
            continue
        for name in names:
            target, info, file_issue = component_health._lstat_regular(folder / name)
            if file_issue is None and info is not None and info.st_size in by_size:
                return target, int(info.st_size), by_size[info.st_size], hashlib.sha256(manifest_raw).hexdigest()
    return None


def status(root=COMPONENT):
    """Fast metadata status; this intentionally does not hash model/runtime bytes."""
    try:
        return _status_tree(current(root))
    except (OSError, ValueError, KeyError, TypeError):
        match = _manual_onnx_match(root, MANIFEST)
        if match is not None:
            return {
                'ready': True,
                'runtime': 'webgpu',
                'probe': {
                    'onnxruntime': 'bundled',
                    'webgpu_available': True,
                    'device': 'webgpu',
                    'manual_weight': match[0].name,
                },
                'verification': {
                    'level': 'metadata',
                    'content_verified': False,
                    'sha256_checked': False,
                    'state': 'metadata_ready',
                },
            }
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
    match = _manual_onnx_match(root, MANIFEST)
    if match is not None:
        target, expected_bytes, expected_sha, manifest_sha = match
        if progress:
            progress(0, expected_bytes, f'正在完整校验 · {target.name}')
        try:
            actual_sha = file_hash(target, check=check)
        except InterruptedError:
            return {'component': 'bs-roformer-karaoke', 'level': 'full', 'state': 'cancelled', 'verified': False}
        ok = actual_sha == expected_sha
        if progress:
            progress(expected_bytes, expected_bytes, '完整校验通过' if ok else '完整校验结束')
        return {
            'component': 'bs-roformer-karaoke',
            'level': 'full',
            'state': 'verified' if ok else 'corrupt',
            'verified': ok,
            'metadata_ready': True,
            'runtime': 'webgpu',
            'probe': {'onnxruntime': 'bundled', 'webgpu_available': True, 'device': 'webgpu',
                      'manual_weight': target.name},
            'manifest_sha256': manifest_sha,
            'expected': [{'path': target.name, 'kind': 'weight', 'bytes': expected_bytes, 'sha256': expected_sha}],
            'actual': [{'path': target.name, 'kind': 'weight', 'exists': True, 'bytes': expected_bytes,
                        'sha256': actual_sha, 'sha256_checked': True}],
            'corrupted': [] if ok else [{'path': target.name, 'kind': 'weight', 'reason': 'sha256_mismatch',
                                         'message': '文件内容 SHA-256 与清单不一致。',
                                         'expected_bytes': expected_bytes, 'actual_bytes': expected_bytes,
                                         'expected_sha256': expected_sha, 'actual_sha256': actual_sha}],
            'changed': [],
            'missing': [],
            'unverified': [],
            'errors': [],
            'progress': {'hash_bytes': expected_bytes, 'total_hash_bytes': expected_bytes,
                         'sha256_checked': 1, 'files_expected': 1},
        }
    return component_health.verify_component(current(root), MANIFEST,
                                             progress=progress, check=check)


def require_ready(root=None):
    root = COMPONENT if root is None else root
    try:
        if _status_tree(current(root))['ready']:
            return current(root)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    match = _manual_onnx_match(root, MANIFEST)
    if match is not None:
        return match[0].parent
    raise FileNotFoundError(_NOT_READY)


@contextmanager
def _component_lock(root, *, install=False):
    path = component_health.component_lock_path(root)
    if install:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():
        raise FileNotFoundError(_NOT_READY)
    try:
        with exclusive_sidecar(path):
            yield path.parent
    except SidecarBusy:
        raise RuntimeError('BS-RoFormer 组件正在安装或使用，请稍后重试。') from None


@contextmanager
def use_ready(root=None):
    """Select and use the current installation under its existing run.lock."""
    root = COMPONENT if root is None else root
    with _component_lock(root) as owned:
        yield require_ready(owned)


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
    routes = component_download_urls(item, source)
    cancelled(check)
    if target.is_file() and target.stat().st_size == item['bytes'] and file_hash(target) == item['sha256']:
        progress(item['bytes'], item['bytes'], '已校验下载文件')
        return target
    if workers is None:
        from common import load_config
        workers = int(load_config(False).get('download_workers', 4))
    if not 1 <= workers <= 8:
        raise ValueError('下载连接数应为 1–8。')
    from parallel_download import ChecksumMismatch, RangeUnsupported, sequential_partial
    if workers > 1 and item['bytes'] >= 8 * 1024**2 and not partial.exists():
        from parallel_download import download as parallel
        try:
            return parallel(item, folder, progress, check, workers=workers, opener=opener, routes=routes)
        except (RangeUnsupported, ChecksumMismatch):
            progress(0, item['bytes'], '分段下载未完成，切换为单连接续传')
            sequential_partial(item, folder)
            operation_event('download_fallback', phase='download', file=download_file(item), mode='single',
                            cached_bytes=partial.stat().st_size if partial.is_file() else 0)
    elif workers == 1 and not partial.exists():
        sequential_partial(item, folder)
    attempts = max(3, len(routes))
    failures = {}
    for attempt in range(attempts):
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
        endpoint = urlsplit(route).hostname
        trace = DownloadTrace(opener, route, item, 'single')
        try:
            with trace.open(request, timeout=30) as response:
                endpoint = urlsplit(response.geturl()).hostname or endpoint
                code = response.status
                if code == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if content_range != f"bytes {offset}-{item['bytes'] - 1}/{item['bytes']}":
                        raise ValueError('服务器返回了错误的下载续传范围。')
                elif code == 200:
                    offset = 0  # Server ignored Range: replace the partial, never append.
                else:
                    raise ValueError(f'下载服务器返回状态 {code}')
                with partial.open('ab' if offset else 'wb') as writer:
                    progress(offset, item['bytes'], '正在下载 · ' + source_name(route))
                    while block := response.read(1024 * 1024):
                        cancelled(check)
                        if offset + len(block) > item['bytes']:
                            raise ValueError('下载文件超出清单大小。')
                        writer.write(block)
                        offset += len(block)
                        progress(offset, item['bytes'], '正在下载 · ' + source_name(route))
            cancelled(check)
            if offset != item['bytes']:
                raise OSError('下载未完成，正在重试。')
            progress(item['bytes'], item['bytes'], '正在校验下载文件')
            if file_hash(partial, check=check) != item['sha256']:
                partial.unlink()
                raise ChecksumMismatch('下载文件 SHA-256 校验失败，请重试。')
            partial.replace(target)
            segments=folder/(name+'.segments')
            if segments.exists():
                from vwa_io import checked_tree
                checked_tree(segments,folder)
                shutil.rmtree(segments)
            trace.record('succeeded', item['bytes'])
            return target
        except InterruptedError:
            trace.record('cancelled', offset)
            raise
        except ValueError as exc:
            trace.record('failed', 0 if isinstance(exc, ChecksumMismatch) else offset, exc)
            failures[endpoint] = str(exc)
            if attempt + 1 == attempts:
                raise type(exc)(_download_failure(name, failures)) from exc
            progress(offset, item['bytes'], '服务器响应或文件校验有误，尝试其他下载源')
        except (OSError, IncompleteRead) as exc:
            trace.record('failed', offset, exc)
            if isinstance(exc, urllib.error.HTTPError):
                endpoint = urlsplit(exc.geturl()).hostname or endpoint
            reason = getattr(exc, 'reason', exc)
            code = getattr(exc, 'code', None)
            winerror = getattr(reason, 'winerror', None)
            error = f'HTTP {code}' if code else f'WinError {winerror}' if winerror else type(reason).__name__
            failures[endpoint] = error
            if attempt + 1 == attempts:
                raise OSError(_download_failure(name, failures)) from exc
            progress(offset, item['bytes'], '连接中断，正在重连')
            if attempt + 1 >= len(routes):
                time.sleep(attempt + 1)
    raise RuntimeError('Download failed')


def _download_failure(name, failures):
    # Hostnames and error codes are shareable; redirect URLs may be signed.
    return f'{name} · ' + '；'.join(f'{host}：{reason}' for host, reason in failures.items())


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


def _manifest_runtime_wheel(spec, runtime):
    wheels = spec.get('runtime_wheels') or spec.get('torch_wheels') or {}
    wheel = wheels.get(runtime)
    if wheel is None and runtime == 'webgpu':
        wheel = wheels.get('cuda')
    if not isinstance(wheel, dict):
        raise ValueError(f'BS-RoFormer 清单缺少 {runtime} 运行库。')
    return wheel


def probe(directory):
    directory = Path(directory)
    packages_dir = directory / 'packages'
    if packages_dir.is_dir():
        sys.path.insert(0, str(packages_dir))
    import numpy as np
    import onnxruntime as ort
    from runtime_devices import create_session, device_module

    providers = list(ort.get_available_providers())
    webgpu = 'WebGpuExecutionProvider' in providers
    gpu_name = None
    active_device = 'cpu'
    if webgpu:
        module = device_module()
        module.select_preferred_gpu_adapter.cache_clear()
        adapter = module.select_preferred_gpu_adapter()
        if adapter is not None:
            gpu_name = adapter.name
        sample_model = Path(ort.__file__).parent / 'datasets' / 'mul_1.onnx'
        if sample_model.is_file():
            opts = ort.SessionOptions()
            opts.enable_mem_pattern = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            active_device, sess = create_session(sample_model, opts, {}, 'webgpu', label='Component Probe')
            inp = sess.get_inputs()[0]
            shape = [d if isinstance(d, int) else 2 for d in inp.shape]
            out = sess.run(None, {inp.name: np.ones(shape, dtype=np.float32)})[0]
            assert np.isfinite(out).all()
            if 'WebGpuExecutionProvider' in sess.get_providers():
                active_device = 'webgpu'
    spec = manifest()
    for item in spec.get('weights', ()):
        if str(item.get('name', '')).lower().endswith('.onnx'):
            model_path = directory / item['name']
            if model_path.is_file():
                opts = ort.SessionOptions()
                opts.enable_mem_pattern = False
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                active_device, sess = create_session(
                    model_path, opts, {}, 'webgpu' if webgpu else 'cpu', label='BS-RoFormer Probe'
                )
                assert sess.get_inputs() and sess.get_outputs()
    result = {
        'onnxruntime': ort.__version__,
        'webgpu_available': webgpu,
        'device': active_device,
        'gpu': gpu_name,
    }
    print('COMPONENT_PROBE=' + json.dumps(result), flush=True)


def install_local_onnx(onnx_path, config_path, runtime='webgpu', root=COMPONENT):
    """Install a locally exported BS-RoFormer ONNX model + config into current/."""
    from gpu_backend import normalize_roformer_runtime

    runtime = normalize_roformer_runtime(runtime)
    onnx_src = Path(onnx_path).resolve()
    cfg_src = Path(config_path).resolve()
    if not onnx_src.is_file() or not cfg_src.is_file():
        raise FileNotFoundError('本地 ONNX 模型或 config.yaml 不存在。')
    with _component_lock(root, install=True) as owned:
        spec = manifest()
        stage, backup, destination = owned / 'staging', owned / 'previous', current(owned)
        remove_owned_tree(stage, owned)
        stage.mkdir()
        onnx_name = next(
            (item['name'] for item in spec.get('weights', ()) if str(item.get('name', '')).lower().endswith('.onnx')),
            onnx_src.name,
        )
        shutil.copyfile(onnx_src, stage / onnx_name)
        shutil.copyfile(cfg_src, stage / 'config.yaml')
        critical = [
            {'file': onnx_name, 'bytes': (stage / onnx_name).stat().st_size, 'sha256': file_hash(stage / onnx_name)},
            {'file': 'config.yaml', 'bytes': (stage / 'config.yaml').stat().st_size, 'sha256': file_hash(stage / 'config.yaml')},
        ]
        atomic_json(stage / 'installed.json', {
            'runtime': runtime,
            'probe': {'onnxruntime': 'local', 'webgpu_available': runtime == 'webgpu', 'device': runtime, 'gpu': None},
            'critical_files': critical,
            'manifest_sha256': file_hash(MANIFEST),
            'source_sha256': {onnx_name: critical[0]['sha256'], 'config.yaml': critical[1]['sha256']},
        })
        replace_verified_tree(stage, destination, backup, owned, _directory_ready)
        return status(owned)


def install(runtime, progress=print, check=None, root=COMPONENT, force=False, source='auto'):
    from gpu_backend import normalize_roformer_runtime

    source = normalize_component_source(source)
    runtime = normalize_roformer_runtime(runtime)
    if (
        not (os.environ.get('VWA_TESTING') == '1' or os.environ.get('BVWA_TESTING') == '1')
        and (sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32 or sys.platform != 'win32')
    ):
        raise RuntimeError('此组件需要助手自带的 Windows x64 Python 3.12。')
    with _component_lock(root, install=True) as root:
        recover_verified_previous(root, _directory_ready)
        existing = status(root)
        if not force and existing['ready'] and existing['runtime'] == runtime:
            progress(1, 1, '元数据检查已通过；未重新哈希大文件，可在组件页执行完整校验')
            return existing
        spec = manifest()
        items = spec['weights'] + spec['shared_wheels'] + [_manifest_runtime_wheel(spec, runtime)]
        total = sum(item['bytes'] for item in items)
        install_sizes = spec['install_bytes']
        expanded = install_sizes.get(runtime, install_sizes.get('cuda', 0))
        needed = expanded + total + 512 * 1024**2
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
        process = subprocess.Popen(
            [load_config()['python'], '-B', '-u', str(ROOT / 'optional_components.py'), '--probe', str(stage)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
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
        if not probed.get('onnxruntime'):
            raise ValueError('Installed runtime version does not match the pinned component')
        critical = []
        for pattern in ('packages/onnxruntime/capi/*.dll', 'packages/onnxruntime/capi/*.pyd'):
            for file in stage.glob(pattern):
                critical.append({'file': file.relative_to(stage).as_posix(), 'bytes': file.stat().st_size,
                                 'sha256': file_hash(file)})
        ort_init = stage / 'packages/onnxruntime/__init__.py'
        if ort_init.is_file():
            critical.append({'file': 'packages/onnxruntime/__init__.py', 'bytes': ort_init.stat().st_size,
                             'sha256': file_hash(ort_init)})
        atomic_json(stage / 'installed.json', {
            'runtime': runtime, 'probe': probed, 'critical_files': critical,
            'manifest_sha256': file_hash(MANIFEST),
            'source_sha256': {item['name']: item['sha256'] for item in items},
        })
        if not _directory_ready(stage):
            if check and check():
                cancelled(check)
            raise ValueError('暂存的 BS-RoFormer 组件未通过完整性校验')
        cancelled(check)
        replace_verified_tree(stage, destination, backup, root, _directory_ready)
        for path in downloads:
            path.unlink(missing_ok=True)
        # Also remove stale archives and parallel-download .segments dirs left by earlier runs.
        remove_owned_tree(root / 'downloads', root)
        progress(total, total, '安装完成，可以使用 BS-RoFormer')
        return status(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', choices=('cpu', 'webgpu', 'cuda'))
    parser.add_argument('--install-local-onnx', type=Path, help='Install a locally exported BS-RoFormer .onnx file')
    parser.add_argument('--config', type=Path, help='config.yaml paired with --install-local-onnx')
    parser.add_argument('--runtime', choices=('cpu', 'webgpu'), default='webgpu')
    parser.add_argument('--probe')
    parser.add_argument('--verify', action='store_true',
                        help='explicitly hash manifest weights and receipt-critical files')
    from download_sources import component_source_options
    spec = manifest()
    wheel_map = spec.get('runtime_wheels') or spec.get('torch_wheels') or {}
    items = spec['weights'] + spec['shared_wheels'] + list(wheel_map.values())
    choices = [value for _, value in component_source_options(items)]
    parser.add_argument('--download-source', choices=[*choices, 'mirror'], default='auto')
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
    elif args.install_local_onnx:
        if not args.config:
            raise SystemExit('--install-local-onnx requires --config <config.yaml>')
        print(json.dumps(install_local_onnx(args.install_local_onnx, args.config, runtime=args.runtime), ensure_ascii=False, indent=2))
    elif args.install:
        install(args.install, lambda done, total, text: print(f'{done}/{total}: {text}', flush=True), source=args.download_source)
