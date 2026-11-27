"""Isolated installer and verifier for experimental BS-RoFormer ONNX (WebGPU/CPU).

Installs into ``dependencies/optional/bs-roformer-onnx/`` with an isolated
``onnxruntime-webgpu==1.27.0`` wheel inside each precision directory so the
base DirectML runtime and the original PyTorch CUDA BS-RoFormer stay untouched.
Only two FP16 precision tiers are supported: ``fp16_10s`` (<=8 GiB / APU) and
``fp16`` (20s, >=12 GiB VRAM).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, SidecarBusy, atomic_json, exclusive_sidecar, file_hash, load_config
import component_health
from download_sources import normalize_component_source
from optional_components import (
    cancelled,
    download,
    extract_wheel,
    remove_owned_tree,
    replace_verified_tree,
)

COMPONENT = ROOT / 'dependencies/optional/bs-roformer-onnx'
MANIFEST = ROOT / 'components/roformer-onnx.json'
if not MANIFEST.is_file() and (ROOT.parent / 'components/roformer-onnx.json').is_file():
    MANIFEST = ROOT.parent / 'components/roformer-onnx.json'
PRECISIONS = ('fp16_10s', 'fp16')
_NOT_READY = (
    'BS-RoFormer ONNX（实验性）尚未就绪；文件可能未安装或校验未通过。'
    '请在“模型与组件”中单独下载，或改用内置分离 / 原版 PyTorch BS-RoFormer。'
)
_REQUIRED_RUNTIME_FILES = (
    'packages/onnxruntime/__init__.py',
    'packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd',
)


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def normalize_precision(value: str | None = None) -> str:
    raw = str(value or '').strip().lower()
    if not raw or raw == 'auto':
        import gpu_backend
        return gpu_backend.recommend_roformer_onnx()['recommended_precision']
    if raw in PRECISIONS:
        return raw
    raise ValueError('BS-RoFormer ONNX（实验性）仅支持 10s FP16（fp16_10s）或 20s FP16（fp16）。')


def manifest_items(spec: dict, precision: str) -> list[dict]:
    prec = normalize_precision(precision)
    weights = spec.get('weights')
    if not isinstance(weights, dict) or not isinstance(weights.get(prec), dict):
        raise ValueError(f'BS-RoFormer ONNX 清单缺少 {prec} 权重。')
    config_item = spec.get('config')
    runtime_item = spec.get('runtime_wheel')
    if not isinstance(config_item, dict) or not isinstance(runtime_item, dict):
        raise ValueError('BS-RoFormer ONNX 清单缺少配置或 WebGPU 运行库条目。')
    items = [weights[prec], config_item, runtime_item]
    for item in items:
        if type(item.get('bytes')) is not int or item['bytes'] < 0:
            raise ValueError('BS-RoFormer ONNX 清单文件大小无效。')
    return items


def precision_directory(precision: str, root=None) -> Path:
    prec = normalize_precision(precision)
    return Path(COMPONENT if root is None else root) / f'current-{prec}'


def _not_ready_status() -> dict:
    return {
        'ready': False,
        'precision': None,
        'verification': {
            'level': 'metadata',
            'content_verified': False,
            'sha256_checked': False,
            'state': 'not_ready',
        },
    }


def _metadata_check_tree(directory: Path, expected_precision: str | None = None) -> dict:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError('组件目录不存在。')
    receipt_path = directory / 'installed.json'
    if not receipt_path.is_file():
        raise ValueError('缺少 installed.json 收据。')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict):
        raise ValueError('installed.json 格式无效。')
    prec = receipt.get('precision')
    if prec not in PRECISIONS:
        raise ValueError('installed.json 精度标识无效。')
    if expected_precision is not None and prec != expected_precision:
        raise ValueError('installed.json 与目标精度不匹配。')
    spec = manifest()
    if receipt.get('manifest_sha256') != file_hash(MANIFEST):
        raise ValueError('组件清单已更新，需重新验证或安装。')
    weight_item = spec['weights'][prec]
    config_item = spec['config']
    for item in (weight_item, config_item):
        target = directory / item['name']
        if not target.is_file() or target.stat().st_size != item['bytes']:
            raise ValueError(f"缺少或大小不匹配：{item['name']}")
    critical = receipt.get('critical_files')
    if not isinstance(critical, list) or not critical:
        raise ValueError('缺少关键运行库校验列表。')
    seen = set()
    for row in critical:
        rel = row.get('file') if isinstance(row, dict) else None
        size = row.get('bytes') if isinstance(row, dict) else None
        if not isinstance(rel, str) or type(size) is not int or size < 0 or '..' in rel or '\\' in rel:
            raise ValueError('关键文件收据格式无效。')
        target = directory / rel
        if not target.is_file() or target.stat().st_size != size:
            raise ValueError(f'运行库文件异常：{rel}')
        seen.add(rel)
    if not all(req in seen for req in _REQUIRED_RUNTIME_FILES):
        raise ValueError('onnxruntime-webgpu 运行库不完整。')
    return {
        'ready': True,
        'precision': prec,
        'probe': receipt.get('probe') if isinstance(receipt.get('probe'), dict) else {},
        'verification': {
            'level': 'metadata',
            'content_verified': False,
            'sha256_checked': False,
            'state': 'metadata_ready',
        },
    }


def _directory_ready(directory: Path, expected_precision: str | None = None) -> bool:
    try:
        return _metadata_check_tree(directory, expected_precision)['ready']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _status_for_precision(precision: str, root=None) -> dict:
    try:
        return _metadata_check_tree(precision_directory(precision, root), precision)
    except (OSError, ValueError, KeyError, TypeError):
        return _not_ready_status()


def status(root=None, precision: str | None = None) -> dict:
    root = COMPONENT if root is None else root
    if precision is not None:
        return _status_for_precision(normalize_precision(precision), root)
    per_prec = {prec: _status_for_precision(prec, root) for prec in PRECISIONS}
    installed = [prec for prec in PRECISIONS if per_prec[prec]['ready']]
    states = {prec: ('ready' if per_prec[prec]['ready'] else 'missing') for prec in PRECISIONS}
    if installed:
        import gpu_backend
        rec = gpu_backend.recommend_roformer_onnx()['recommended_precision']
        preferred = rec if rec in installed else installed[0]
        return {**per_prec[preferred], 'installed_precisions': installed, 'precisions': states}
    return {**_not_ready_status(), 'installed_precisions': [], 'precisions': states}


def current(root=None, precision: str | None = None) -> Path:
    root = Path(COMPONENT if root is None else root)
    if precision is not None:
        return precision_directory(normalize_precision(precision), root)
    st = status(root)
    if st['ready'] and st['precision'] in PRECISIONS:
        return precision_directory(st['precision'], root)
    for prec in PRECISIONS:
        cand = precision_directory(prec, root)
        if cand.exists():
            return cand
    return precision_directory('fp16_10s', root)


def full_verify(root=None, progress=None, check=None, precision: str | None = None) -> dict:
    root = COMPONENT if root is None else root
    directory = current(root, precision=precision)
    callback = progress if callable(progress) else (lambda _d, _t, _m: None)
    errors = []
    corrupted = []
    try:
        meta = _metadata_check_tree(directory, precision)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {
            'component': 'bs-roformer-onnx',
            'level': 'full',
            'scope': 'ONNX 权重、配置与隔离 WebGPU 运行库 SHA-256 校验；不加载模型。',
            'verified': False,
            'state': 'corrupt',
            'errors': [{'path': str(directory), 'message': str(exc)}],
            'corrupted': [],
            'progress': {'sha256_checked': 0, 'files_expected': 0},
        }
    spec = manifest()
    prec = meta['precision']
    receipt = json.loads((directory / 'installed.json').read_text(encoding='utf-8'))
    targets = [
        (spec['weights'][prec]['name'], spec['weights'][prec]['sha256'], spec['weights'][prec]['bytes']),
        (spec['config']['name'], spec['config']['sha256'], spec['config']['bytes']),
    ]
    for row in receipt.get('critical_files', ()):
        targets.append((row['file'], row.get('sha256', ''), row.get('bytes', 0)))
    total_bytes = sum(max(0, int(size)) for _, _, size in targets)
    done_bytes = 0
    checked = 0
    for rel, expected_sha, size in targets:
        if check and check():
            return {
                'component': 'bs-roformer-onnx',
                'level': 'full',
                'verified': False,
                'state': 'cancelled',
                'errors': [],
                'corrupted': corrupted,
                'progress': {'sha256_checked': checked, 'files_expected': len(targets)},
            }
        path = directory / rel
        callback(done_bytes, total_bytes, f'正在校验 {Path(rel).name}')
        try:
            actual = file_hash(path, check=check)
        except InterruptedError:
            return {
                'component': 'bs-roformer-onnx',
                'level': 'full',
                'verified': False,
                'state': 'cancelled',
                'errors': [],
                'corrupted': corrupted,
                'progress': {'sha256_checked': checked, 'files_expected': len(targets)},
            }
        except OSError as exc:
            errors.append({'path': rel, 'message': str(exc)})
            continue
        checked += 1
        done_bytes += max(0, int(size))
        if not expected_sha or actual != expected_sha:
            corrupted.append({'path': rel, 'message': 'SHA-256 校验不匹配。'})
    callback(total_bytes, total_bytes, 'BS-RoFormer ONNX 完整校验结束')
    ok = not errors and not corrupted and checked == len(targets)
    return {
        'component': 'bs-roformer-onnx',
        'level': 'full',
        'scope': 'ONNX 权重、配置与隔离 WebGPU 运行库 SHA-256 校验；不加载模型。',
        'verified': ok,
        'state': 'verified' if ok else 'corrupt',
        'errors': errors,
        'corrupted': corrupted,
        'progress': {'sha256_checked': checked, 'files_expected': len(targets)},
    }


def require_ready(root=None, precision: str | None = None) -> Path:
    root = COMPONENT if root is None else root
    st = status(root, precision=precision)
    if not st['ready']:
        raise FileNotFoundError(_NOT_READY)
    return current(root, precision=st['precision'])


@contextmanager
def _component_lock(root, *, install: bool = False):
    path = component_health.component_lock_path(root)
    if install:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():
        raise FileNotFoundError(_NOT_READY)
    try:
        with exclusive_sidecar(path):
            yield path.parent
    except SidecarBusy:
        raise RuntimeError('BS-RoFormer ONNX（实验性）正在安装或使用，请稍后重试。') from None


@contextmanager
def use_ready(root=None, precision: str | None = None):
    root = COMPONENT if root is None else root
    with _component_lock(root) as owned:
        yield require_ready(owned, precision=precision)


def _recover_previous(root: Path, precision: str) -> bool:
    destination = precision_directory(precision, root)
    backup = root / f'previous-{precision}'
    if destination.exists() or not backup.exists() or not _directory_ready(backup, precision):
        return False
    backup.replace(destination)
    return True


def _reusable_config(root: Path, item: dict, check=None) -> Path | None:
    for prec in PRECISIONS:
        candidate = precision_directory(prec, root) / item['name']
        try:
            if candidate.is_file() and candidate.stat().st_size == item['bytes']:
                if file_hash(candidate, check=check) == item['sha256']:
                    return candidate
        except OSError:
            continue
    return None


def probe(directory: str) -> None:
    """Subprocess-only probe loading isolated onnxruntime-webgpu==1.27.0."""
    target = Path(directory)
    sys.path.insert(0, str(target / 'packages'))
    import onnxruntime as ort
    providers = list(ort.get_available_providers())
    if 'WebGpuExecutionProvider' not in providers:
        raise RuntimeError(f'隔离运行库缺少 WebGpuExecutionProvider：{providers}')
    onnx_files = sorted(target.glob('*.onnx'))
    if not onnx_files:
        raise FileNotFoundError('暂存目录缺少 ONNX 模型文件。')
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(str(onnx_files[0]), sess_options=opts, providers=['CPUExecutionProvider'])
    shape = list(sess.get_inputs()[0].shape)
    result = {
        'onnxruntime': str(getattr(ort, '__version__', '')),
        'providers': providers,
        'webgpu_available': 'WebGpuExecutionProvider' in providers,
        'model': onnx_files[0].name,
        'input_shape': shape,
    }
    print('COMPONENT_PROBE=' + json.dumps(result, ensure_ascii=False), flush=True)


def install(precision: str = 'auto', progress=print, check=None, root=None,
            force: bool = False, source: str = 'auto') -> dict:
    root = Path(COMPONENT if root is None else root)
    source = normalize_component_source(source)
    prec = normalize_precision(precision)
    if sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32 or sys.platform != 'win32':
        raise RuntimeError('此实验组件需要助手自带的 Windows x64 Python 3.12。')
    with _component_lock(root, install=True) as owned:
        _recover_previous(owned, prec)
        existing = status(owned, precision=prec)
        if not force and existing['ready'] and existing['precision'] == prec:
            progress(1, 1, '元数据检查已通过；未重新哈希大文件，可在组件页执行完整校验')
            return status(owned)
        spec = manifest()
        items = manifest_items(spec, prec)
        total = sum(item['bytes'] for item in items)
        needed = int(spec['install_bytes'][prec]) + total + 512 * 1024**2
        if shutil.disk_usage(owned).free < needed:
            raise OSError(f'安装空间不足，请至少预留 {needed / 10**9:.1f} GB。')
        stage = owned / f'staging-{prec}'
        backup = owned / f'previous-{prec}'
        destination = precision_directory(prec, owned)
        remove_owned_tree(stage, owned)
        stage.mkdir(parents=True, exist_ok=True)
        completed = 0
        downloads = []
        for index, item in enumerate(items):
            cancelled(check)
            if item['name'] == spec['config']['name']:
                reused = _reusable_config(owned, item, check=check)
                if reused is not None:
                    shutil.copyfile(reused, stage / item['name'])
                    completed += item['bytes']
                    progress(completed, total, f'复用已校验配置 · 文件 {index + 1}/{len(items)}')
                    continue
            def update(done, count, message, base=completed, number=index + 1):
                progress(base + done, total, f'{message} · 文件 {number}/{len(items)}')
            download_path = download(item, owned / 'downloads', update, check, source=source)
            downloads.append(download_path)
            if item['name'].endswith('.whl'):
                progress(completed + item['bytes'], total, '正在安装隔离 WebGPU 运行库')
                extract_wheel(download_path, stage / 'packages', check)
            else:
                shutil.copyfile(download_path, stage / item['name'])
            completed += item['bytes']
        cancelled(check)
        progress(total, total, '正在验证隔离 ONNX / WebGPU 环境')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen(
            [load_config()['python'], '-B', '-u', str(Path(__file__).resolve()), '--probe', str(stage)],
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
                        raise TimeoutError('实验组件验证超时，请重试。')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        text = output.decode('utf-8', errors='replace')
        if process.returncode or 'COMPONENT_PROBE=' not in text:
            raise RuntimeError('隔离 WebGPU 环境验证失败：\n' + text[-2500:])
        probed = json.loads(next(line.split('=', 1)[1] for line in text.splitlines()
                                 if line.startswith('COMPONENT_PROBE=')))
        if probed.get('onnxruntime') != '1.27.0':
            raise ValueError('隔离 onnxruntime-webgpu 版本与清单不匹配。')
        critical = []
        for rel in _REQUIRED_RUNTIME_FILES:
            path = stage / rel
            critical.append({'file': rel, 'bytes': path.stat().st_size, 'sha256': file_hash(path)})
        for dll in sorted((stage / 'packages/onnxruntime/capi').glob('*.dll')):
            rel = dll.relative_to(stage).as_posix()
            critical.append({'file': rel, 'bytes': dll.stat().st_size, 'sha256': file_hash(dll)})
        atomic_json(stage / 'installed.json', {
            'precision': prec,
            'probe': probed,
            'critical_files': critical,
            'manifest_sha256': file_hash(MANIFEST),
            'source_sha256': {item['name']: item['sha256'] for item in items},
        })
        if not _directory_ready(stage, prec):
            raise ValueError('暂存的 BS-RoFormer ONNX 组件未通过完整性校验。')
        cancelled(check)
        replace_verified_tree(stage, destination, backup, owned, lambda d: _directory_ready(d, prec))
        for path in downloads:
            path.unlink(missing_ok=True)
        remove_owned_tree(owned / 'downloads', owned)
        progress(total, total, '安装完成，可使用 BS-RoFormer ONNX（实验性）')
        return status(owned)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe')
    parser.add_argument('--install', choices=PRECISIONS)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.probe:
        probe(args.probe)
    elif args.verify:
        report = full_verify()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report['verified'] else 2)
    elif args.install:
        install(args.install, lambda done, total, text: print(f'{done}/{total}: {text}', flush=True))
