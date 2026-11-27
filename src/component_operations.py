"""Qt-free operations shared by the desktop and Qt Quick component pages.

This module projects the existing component services into one small UI
contract.  It owns status projection and dispatch only; installation,
transaction, receipt and SHA-256 verification algorithms remain in the
respective services.
"""
from __future__ import annotations

from pathlib import Path
import shutil
from stat import S_ISDIR
from typing import Callable

import check_dependencies
import dependency_profiles
import download_sources
import expression_component
import feature_components
import gpu_backend
import optional_components
import roformer_onnx_component
from preparation_languages import QWEN_FEATURE, QWEN_TITLE


COMPONENT_IDS = ('builtin', 'roformer', 'chinese', 'expression', 'roformer_onnx')
ROW_KEYS = (
    'id', 'title', 'summary', 'status', 'status_text', 'detail',
    'download_bytes', 'installed_bytes', 'required_bytes',
    'can_install', 'can_verify', 'can_use', 'model', 'health', 'download_sources',
)
STATUSES = ('ready', 'missing', 'partial', 'damaged', 'error')
_STATUS_TEXT = {
    'ready': '已就绪',
    'missing': '未安装',
    'partial': '部分就绪',
    'damaged': '需要修复',
    'error': '检查失败',
}
_FEATURE_STATUS = {'ready', 'missing', 'partial', 'damaged', 'error'}


def _runtime(value):
    if value not in ('cpu', 'cuda'):
        raise ValueError('请选择 CPU 或 NVIDIA CUDA 运行库。')
    return value


def _nonnegative_int(value):
    if type(value) is int and value >= 0:
        return value
    return 0


def _message(exc, limit=500):
    text = ' '.join(str(exc).splitlines()).strip() or exc.__class__.__name__
    return text[:limit]


def _progress(callback):
    return callback if callable(callback) else (lambda done, total, text: None)


def _row(component_id, status, *, detail, download=0, installed=0,
         required=0, can_install=False, can_verify=False, can_use=False,
         model=None, status_text=None, health=None, download_items=()):
    if status not in STATUSES:
        raise ValueError('无效的组件状态。')
    return {
        'id': component_id,
        'title': {
            'builtin': 'Karaoke 2',
            'roformer': 'BS-RoFormer',
            'roformer_onnx': 'BS-RoFormer ONNX（实验性）',
            QWEN_FEATURE: QWEN_TITLE,
            'expression': '参考演唱音色',
        }[component_id],
        'summary': {
            'builtin': '主唱／和声分离 · 内置基础方案 · 无需下载',
            'roformer': '主唱／和声分离 · 可选 CPU / CUDA 运行库',
            'roformer_onnx': '主唱／和声分离 · 实验性 WebGPU/ONNX · 10s/20s FP16 · 默认不启用',
            QWEN_FEATURE: 'Qwen3-ASR · 中文和英文共用',
            'expression': 'WORLD · 张力、气声与发声强度',
        }[component_id],
        'status': status,
        'status_text': status_text or _STATUS_TEXT[status],
        'detail': detail,
        'download_bytes': _nonnegative_int(download),
        'installed_bytes': _nonnegative_int(installed),
        'required_bytes': _nonnegative_int(required),
        'can_install': bool(can_install),
        'can_verify': bool(can_verify),
        'can_use': bool(can_use),
        'model': model,
        'health': health or {},
        'download_sources': download_sources.component_source_options(download_items) if can_install else (),
    }


def _error_row(component_id, exc, model=None):
    return _row(
        component_id, 'error',
        detail='状态读取失败：' + _message(exc),
        model=model,
    )


def _path_has_entries(path):
    try:
        path = Path(path)
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _install_volume(root):
    """Read the volume used by the installer's disk preflight, without creating it."""
    root = Path(root).absolute()
    for path in (root, *root.parents):
        try:
            if not S_ISDIR(path.stat().st_mode):
                break
        except FileNotFoundError:
            continue
        except OSError:
            break
        try:
            return {'path': str(root), 'free_bytes': shutil.disk_usage(path).free}
        except OSError:
            break
    return {'path': str(root), 'free_bytes': None}


def _tree_presence(root, *, damaged_names=('current',), partial_names=('downloads', 'staging', 'previous')):
    root = Path(root)
    if any((root / name).exists() for name in damaged_names):
        return 'damaged'
    if any(_path_has_entries(root / name) for name in partial_names):
        return 'partial'
    return 'missing'


_SEPARATION_TOOL_PATHS = ('python', 'ffmpeg', 'mdx_model', 'mdx_metadata', 'chorus_model', 'chorus_metadata')
_SEPARATION_PAYLOAD = frozenset((
    'dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx', 'dependencies/models/mdx-model-data.json',
    'models/UVR_MDXNET_KARA_2.onnx', 'models/mdx-model-data.json',
    *('dependencies/vocal2midi/python/Lib/site-packages/' + name for name in (
        'numpy/__init__.py', 'scipy/__init__.py', 'soundfile.py', '_soundfile_data/libsndfile_x64.dll',
        'onnxruntime/capi/onnxruntime_pybind11_state.pyd',
    )),
))


def _builtin_row(check=None):
    model = 'dual'
    try:
        # Check the actual audio feature. The legacy UI and installer files
        # in the core manifest are not requirements of the separation engine.
        profile = dependency_profiles.status(['core'], file_filter=_SEPARATION_PAYLOAD.__contains__, check=check)['core']
        report = check_dependencies.check(features={'core'}, tool_paths=_SEPARATION_TOOL_PATHS)
    except InterruptedError:
        raise
    except Exception as exc:
        return _error_row('builtin', exc, model=model)

    required = _nonnegative_int(profile.get('bytes'))
    installed = _nonnegative_int(profile.get('installed_bytes'))
    profile_state = profile.get('state')
    profile_ready = bool(profile.get('ready', profile_state == 'ready' and not profile.get('missing')))
    check_passed = report.get('state') == 'passed'
    if check_passed and profile_ready:
        status = 'ready'
        detail = '内置分离的工具、模型和关键运行文件已通过文件检查。'
    elif profile_state == 'error':
        status = 'error'
        detail = profile.get('error') or '基础依赖清单无法读取。'
    elif profile_state == 'partial':
        status = 'partial'
        detail = f'基础依赖仅部分存在（{installed} / {required} 字节）；当前不能声明内置方案就绪。'
    elif profile_state == 'damaged' or installed:
        status = 'damaged'
        detail = '基础依赖文件存在，但工具或内置依赖检查未通过；内置方案暂不可用。'
    else:
        errors = report.get('errors') or ()
        first = errors[0] if errors else {}
        problem = first.get('file') or first.get('reason') if isinstance(first, dict) else str(first) if first else ''
        detail = '基础依赖缺失' + (f'：{problem}' if problem else '；内置方案暂不可用。')
        status = 'missing'
    return _row(
        'builtin', status, detail=detail,
        download=0, installed=installed, required=required,
        can_install=False, can_verify=False, can_use=status == 'ready',
        model=model,
    )


def _roformer_manifest_items(spec, runtime):
    rows = []
    for key in ('weights', 'shared_wheels'):
        value = spec.get(key)
        if not isinstance(value, list):
            raise ValueError(f'BS-RoFormer 清单缺少 {key}。')
        rows.extend(value)
    torch_wheels = spec.get('torch_wheels')
    if not isinstance(torch_wheels, dict) or not isinstance(torch_wheels.get(runtime), dict):
        raise ValueError(f'BS-RoFormer 清单缺少 {runtime} 运行库。')
    rows.append(torch_wheels[runtime])
    for item in rows:
        if not isinstance(item, dict) or type(item.get('bytes')) is not int or item['bytes'] < 0:
            raise ValueError('BS-RoFormer 清单文件大小无效。')
    return rows


def _roformer_row(runtime):
    model = 'roformer'
    try:
        state = optional_components.status()
        spec = optional_components.manifest()
        items = _roformer_manifest_items(spec, runtime)
        download = sum(item['bytes'] for item in items)
        install_sizes = spec.get('install_bytes')
        expanded = install_sizes.get(runtime) if isinstance(install_sizes, dict) else None
        expanded = _nonnegative_int(expanded)
        required = download + expanded + 512 * 1024**2
    except Exception as exc:
        return _error_row('roformer', exc, model=model)

    ready = bool(state.get('ready'))
    installed_runtimes = state.get('installed_runtimes')
    if isinstance(installed_runtimes, list):
        installed_runtimes = [rt for rt in ('cuda', 'cpu') if rt in installed_runtimes]
    else:
        rt_single = state.get('runtime') if state.get('runtime') in ('cpu', 'cuda') else None
        installed_runtimes = [rt_single] if (ready and rt_single) else []
    target_ready = runtime in installed_runtimes
    installed_runtime = runtime if target_ready else (installed_runtimes[0] if installed_runtimes else None)
    runtime_states = {rt: ('ready' if rt in installed_runtimes else 'missing') for rt in ('cuda', 'cpu')}
    verification = state.get('verification') if isinstance(state.get('verification'), dict) else {}
    cached = 0
    for item in items:
        name = item.get('name')
        if not isinstance(name, str) or not name or Path(name).name != name:
            continue
        try:
            cached += feature_components.resumable_cache_bytes(optional_components.COMPONENT / 'downloads', item)
        except OSError:
            cached = None
            break
    health = {'runtime': installed_runtime or '', 'ready_runtime': (installed_runtime or '') if installed_runtimes else '',
              'installed_runtimes': installed_runtimes,
              'runtimes': runtime_states,
              'target_runtime': runtime,
              'cache_bytes': cached,
              'installed_estimate': bool(target_ready),
              'scope': '安装收据、文件路径与大小；完整校验另行执行',
              'source': '当前固定组件清单'}
    if target_ready:
        installed = expanded
        if verification.get('content_verified') or verification.get('sha256_checked'):
            verify_note = '元数据与完整 SHA-256 校验通过；不会加载模型。'
        else:
            verify_note = '元数据检查通过；尚未执行完整 SHA-256 校验。'
        if len(installed_runtimes) == 2:
            detail = f'GPU (CUDA) 与 CPU 运行库均已独立安装，可自由切换无需重新下载。{verify_note}'
        elif runtime == 'cuda':
            detail = f'GPU (CUDA) 运行库已安装（同时支持 CPU 推理，无需另装 CPU 版）；CPU 独立版未安装。{verify_note}'
        else:
            detail = f'CPU 运行库已安装；GPU (CUDA) 运行库未安装（后续加装 CUDA 不会覆盖现有 CPU 版）。{verify_note}'
        return _row(
            'roformer', 'ready', detail=detail,
            download=download, installed=installed, required=required,
            can_install=True, can_verify=True, can_use=True, model=model, health=health, download_items=items,
        )

    if installed_runtimes:
        other_runtime = installed_runtimes[0]
        if other_runtime == 'cuda' and runtime == 'cpu':
            detail = (
                '已安装 GPU (CUDA) 运行库（已自带 CPU 推理能力，在准备工作流中可直接选择 CPU，无需另行下载 CPU 版）；'
                '当前查看的 CPU 独立运行库未安装，若加装也会独立保存，不会覆盖 CUDA 版。'
            )
        else:
            detail = (
                '已安装 CPU 运行库；当前选择的 GPU (CUDA) 运行库尚未安装。'
                '下载安装 CUDA 版会独立保存，不会覆盖已安装的 CPU 版。'
            )
        return _row(
            'roformer', 'missing',
            status_text='当前目标未安装',
            detail=detail,
            download=download, installed=0, required=required,
            can_install=True, can_verify=False, can_use=False, model=model, health=health, download_items=items,
        )

    state_value = state.get('state') or verification.get('state')
    if state_value in _FEATURE_STATUS:
        status = state_value
    elif state_value == 'corrupt':
        status = 'damaged'
    else:
        status = _tree_presence(
            optional_components.COMPONENT,
            damaged_names=(f'current-{runtime}', 'current'),
            partial_names=('downloads', f'staging-{runtime}', f'previous-{runtime}', 'staging', 'previous'),
        )
    if status == 'ready':
        status = 'damaged'
    if status == 'damaged':
        detail = 'BS-RoFormer 安装目录存在，但元数据或收据检查未通过；可修复或完整校验。'
        can_verify = True
    elif status == 'partial':
        detail = '检测到未完成的 BS-RoFormer 下载或暂存；当前目标尚未就绪。'
        can_verify = False
    elif status == 'error':
        detail = 'BS-RoFormer 状态报告为错误；请检查清单或安装目录。'
        can_verify = False
    else:
        detail = '当前运行库尚未安装；可直接开始下载并安装。'
        can_verify = False
    return _row(
        'roformer', status, detail=detail,
        download=download, installed=0, required=required,
        can_install=status != 'error', can_verify=can_verify, can_use=False,
        model=model, health=health, download_items=items,
    )


def _chinese_row():
    try:
        state = feature_components.status('chinese')
        download_items = () if state.get('state') == 'error' else feature_components.specification('chinese')['files']
    except Exception as exc:
        return _error_row('chinese', exc)
    status = state.get('state')
    if status == 'corrupt':
        status = 'damaged'
    if status not in _FEATURE_STATUS:
        status = 'ready' if state.get('ready') else 'missing'
    if status == 'ready' and not state.get('ready'):
        status = 'damaged'

    total = _nonnegative_int(state.get('bytes'))
    installed = _nonnegative_int(state.get('complete_bytes'))
    complete = _nonnegative_int(state.get('complete_files'))
    files = _nonnegative_int(state.get('files'))
    cached = _nonnegative_int(state.get('partial_bytes'))
    can_verify = status in {'ready', 'partial', 'damaged'} and (installed > 0 or cached > 0)
    if status == 'ready':
        detail = 'Qwen3-ASR 模型文件已就绪；完整校验会逐文件核对固定 SHA-256。'
    elif status == 'error':
        detail = state.get('error') or 'Qwen3-ASR 模型清单或路径状态无法读取。'
    elif status == 'damaged':
        detail = f'Qwen3-ASR 模型文件需要修复（{complete} / {files} 个文件完整）；可继续下载并重新校验。'
    elif status == 'partial':
        detail = f'Qwen3-ASR 模型已准备 {complete} / {files} 个文件；下载缓存可断点续传。'
    else:
        detail = 'Qwen3-ASR 模型尚未安装；下载后可离线使用，完整校验会逐文件核对 SHA-256。'
    return _row(
        'chinese', status, detail=detail,
        download=total, installed=installed, required=total,
        can_install=status != 'error', can_verify=can_verify,
        can_use=status == 'ready', model=None, download_items=download_items,
        health={'version': str(state.get('revision') or '')[:16], 'cache_bytes': cached,
                'complete_files': complete, 'expected_files': files,
                'scope': '清单与本地文件检查；完整校验另行执行', 'source': '固定模型版本'},
    )


def _expression_row():
    try:
        state = expression_component.status()
        spec = expression_component.SPEC
        download = _nonnegative_int(spec.get('bytes')) if isinstance(spec, dict) else 0
    except Exception as exc:
        return _error_row('expression', exc)
    ready = bool(state.get('ready'))
    if ready:
        status = 'ready'
        detail = 'WORLD 安装收据与列出的文件摘要检查通过；校验不会加载扩展。'
    else:
        status_value = state.get('state')
        if status_value in _FEATURE_STATUS and status_value != 'ready':
            status = status_value
        elif status_value == 'corrupt':
            status = 'damaged'
        else:
            status = _tree_presence(expression_component.COMPONENT)
        detail = {
            'damaged': 'WORLD 组件目录存在，但收据或文件摘要检查未通过；可重新安装修复。',
            'partial': '检测到未完成的 WORLD 下载或暂存；组件尚未就绪。',
            'error': 'WORLD 组件状态报告为错误；请检查安装目录。',
            'missing': 'WORLD 参考音色组件尚未安装；下载后可用于张力、气声与发声强度分析。',
        }.get(status, 'WORLD 参考音色组件尚未就绪。')
    installed = _nonnegative_int(state.get('bytes')) if ready else 0
    can_verify = ready or status == 'damaged'
    return _row(
        'expression', status, detail=detail,
        download=download, installed=installed, required=max(16 * 1024**2, download, installed),
        can_install=status != 'error', can_verify=can_verify,
        can_use=status == 'ready', model=None,
        download_items=(spec,),
        health={'version': str(state.get('version') or ''), 'scope': '安装收据和列出的文件摘要',
                'source': '固定 PyPI 组件'},
    )


def _roformer_onnx_row(precision=None):
    model = 'roformer_onnx'
    try:
        recommendation = gpu_backend.recommend_roformer_onnx()
        target_prec = roformer_onnx_component.normalize_precision(
            precision or recommendation['recommended_precision']
        )
        state = roformer_onnx_component.status()
        spec = roformer_onnx_component.manifest()
        items = roformer_onnx_component.manifest_items(spec, target_prec)
        download = sum(item['bytes'] for item in items)
        install_sizes = spec.get('install_bytes')
        expanded = _nonnegative_int(install_sizes.get(target_prec) if isinstance(install_sizes, dict) else 0)
        required = download + expanded + 512 * 1024**2
    except Exception as exc:
        return _error_row('roformer_onnx', exc, model=model)

    installed_precisions = [
        p for p in roformer_onnx_component.PRECISIONS
        if p in (state.get('installed_precisions') or ())
    ]
    prec_states = {p: ('ready' if p in installed_precisions else 'missing') for p in roformer_onnx_component.PRECISIONS}
    target_ready = target_prec in installed_precisions
    cached = 0
    for item in items:
        name = item.get('name')
        if not isinstance(name, str) or not name or Path(name).name != name:
            continue
        try:
            cached += feature_components.resumable_cache_bytes(roformer_onnx_component.COMPONENT / 'downloads', item)
        except OSError:
            cached = None
            break
    health = {
        'experimental': True,
        'target_precision': target_prec,
        'installed_precisions': installed_precisions,
        'precisions': prec_states,
        'recommendation': recommendation,
        'cache_bytes': cached,
        'installed_estimate': bool(target_ready),
        'scope': 'ONNX 权重、配置与隔离 onnxruntime-webgpu 1.27.0 校验；不影响原版 PyTorch CUDA 与 DirectML',
        'source': '实验性固定组件清单（致谢 AU-ki 导出）',
    }
    label_map = {'fp16_10s': '10s FP16', 'fp16': '20s FP16'}
    target_label = label_map[target_prec]
    if target_ready:
        detail = (
            f'实验性 {target_label} 与隔离 WebGPU 运行库已就绪（默认不启用，可在准备工作流中手动选用）。'
            f'{recommendation["reason"]}'
        )
        return _row(
            'roformer_onnx', 'ready', detail=detail,
            download=download, installed=expanded, required=required,
            can_install=True, can_verify=True, can_use=True, model=model,
            health=health, download_items=items,
        )
    if installed_precisions:
        other = label_map[installed_precisions[0]]
        detail = (
            f'已安装实验性 {other}；当前选中的 {target_label} 尚未安装（两档独立存储互不覆盖）。'
            f'{recommendation["reason"]}'
        )
        return _row(
            'roformer_onnx', 'missing', status_text='当前档位未安装', detail=detail,
            download=download, installed=0, required=required,
            can_install=True, can_verify=False, can_use=False, model=model,
            health=health, download_items=items,
        )
    status = _tree_presence(
        roformer_onnx_component.COMPONENT,
        damaged_names=(f'current-{target_prec}',),
        partial_names=('downloads', f'staging-{target_prec}', f'previous-{target_prec}'),
    )
    if status == 'damaged':
        detail = 'BS-RoFormer ONNX（实验性）目录存在但校验未通过；可重新安装或校验。'
        can_verify = True
    elif status == 'partial':
        detail = '检测到未完成的 BS-RoFormer ONNX（实验性）下载或暂存；可继续下载。'
        can_verify = False
    else:
        detail = (
            f'实验性跨厂商 WebGPU / ONNX 后端，默认不启用。{recommendation["reason"]}'
            ' 8 GB 及以下显存请仅用 10s FP16；20s FP16 需 12 GB+ 独立显存。'
        )
        can_verify = False
    return _row(
        'roformer_onnx', status, detail=detail,
        download=download, installed=0, required=required,
        can_install=True, can_verify=can_verify, can_use=False, model=model,
        health=health, download_items=items,
    )


def catalog(runtime, *, check=None, onnx_precision=None) -> list[dict]:
    """Return the stable component rows for the selected RoFormer runtime and ONNX precision."""
    runtime = _runtime(runtime)
    rows = []
    readers = (
        lambda: _builtin_row(check),
        lambda: _roformer_row(runtime),
        _chinese_row,
        _expression_row,
        lambda: _roformer_onnx_row(onnx_precision),
    )
    for read in readers:
        if check is not None and check():
            raise InterruptedError('已取消组件检查。')
        row = read()
        root = {
            'roformer': optional_components.COMPONENT,
            'roformer_onnx': roformer_onnx_component.COMPONENT,
            'chinese': feature_components.ROOT,
            'expression': expression_component.COMPONENT,
        }.get(row['id'])
        if root is not None:
            row['health']['volume'] = _install_volume(root)
            reserve = row['required_bytes']
            if row['id'] == 'chinese' and reserve:
                reserve += 64 * 1024**2
            row['health']['reserve_bytes'] = reserve
        rows.append(row)
    return [{key: row[key] for key in ROW_KEYS} for row in rows]


def install(component_id, *, runtime=None, precision=None, source='auto', progress=None,
            check=None, force=False):
    """Dispatch installation to the existing service without reimplementing it."""
    callback = _progress(progress)
    if component_id == 'roformer':
        runtime = _runtime(runtime)
        return optional_components.install(
            runtime, callback, check, force=bool(force), source=source,
        )
    if component_id == 'roformer_onnx':
        prec = roformer_onnx_component.normalize_precision(precision)
        return roformer_onnx_component.install(
            prec, callback, check, force=bool(force), source=source,
        )
    if component_id == 'chinese':
        return feature_components.install(
            'chinese', callback, check, source=source,
        )
    if component_id == 'expression':
        return expression_component.install(
            callback, check, source=source,
        )
    if component_id == 'builtin':
        raise ValueError('内置基础方案随安装包提供，不能单独在线安装。')
    raise ValueError('未知的组件标识。')


def verify(component_id, *, runtime=None, precision=None, progress=None, check=None):
    """Reuse the service-owned verification report or wrap its boolean result."""
    callback = _progress(progress)
    if component_id == 'builtin':
        report = check_dependencies.check(full=True, features={'core'})
        callback(1, 1, '基础依赖 SHA-256 校验结束')
        return {**report, 'verified': report.get('state') == 'passed'}
    if component_id == 'roformer':
        if runtime is None:
            return optional_components.full_verify(progress=progress, check=check)
        return optional_components.full_verify(progress=progress, check=check, runtime=_runtime(runtime))
    if component_id == 'roformer_onnx':
        if precision is None:
            return roformer_onnx_component.full_verify(progress=progress, check=check)
        return roformer_onnx_component.full_verify(
            progress=progress, check=check, precision=roformer_onnx_component.normalize_precision(precision),
        )
    if component_id == 'chinese':
        return feature_components.verify('chinese', progress=progress, check=check)
    if component_id == 'expression':
        result = expression_component.verify()
        if isinstance(result, dict):
            callback(1, 1, 'WORLD 组件校验结束')
            return result
        verified = bool(result)
        callback(1, 1, 'WORLD 组件校验结束')
        return {
            'component': 'expression',
            'level': 'receipt-files',
            'scope': 'WORLD 安装收据及其中列出的全部文件 SHA-256；不加载扩展。',
            'verified': verified,
            'state': 'verified' if verified else 'corrupt',
        }
    raise ValueError('未知的组件标识。')


def verification_summary(report):
    """Small user-facing snapshot from the most recent explicit check."""
    if not isinstance(report, dict):
        return {}
    rows = []
    for key in ('corrupted', 'errors', 'unverified'):
        for row in report.get(key) or ():
            if isinstance(row, dict):
                path = row.get('path') or row.get('file') or ''
                reason = row.get('message') or row.get('reason') or row.get('code') or ''
                if path or reason:
                    label = ' · '.join(str(part) for part in (path, reason) if part)[:480]
                    if label not in rows:
                        rows.append(label)
    progress = report.get('progress') or {}
    count = progress.get('sha256_checked', report.get('checked_files'))
    expected = progress.get('files_expected')
    if report.get('feature') == 'chinese':
        expected = report.get('files')
        if count is None and report.get('verified'):
            count = report.get('complete_files')
    return {'state': str(report.get('state') or ''), 'verified': report.get('verified') is True,
            'checked': count if type(count) is int else None,
            'expected': expected if type(expected) is int and expected > 0 else None,
            'issues': rows[:8], 'issue_count': len(rows)}


__all__ = [
    'COMPONENT_IDS', 'ROW_KEYS', 'STATUSES',
    'catalog', 'install', 'verify',
]
