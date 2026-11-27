"""Unified GPU capability probe and WebGPU backend normalization.

Pure Python, zero Qt dependencies. Centralizes AMD/NVIDIA/Intel adapter
discovery (DXGI + ONNX Runtime WebGPU EP devices) and legacy device token
migration (``dml`` / ``cuda`` -> ``webgpu``).
"""
from __future__ import annotations

import sys
from typing import Any

import runtime_devices

VENDOR_AMD = 0x1002
VENDOR_NVIDIA = 0x10DE
VENDOR_INTEL = 0x8086
VENDOR_LABELS = {
    VENDOR_AMD: 'AMD',
    VENDOR_NVIDIA: 'NVIDIA',
    VENDOR_INTEL: 'Intel',
}

EXECUTION_DEVICE_CHOICES = ('cpu', 'webgpu')
ROFORMER_DEVICE_CHOICES = ('auto', 'cpu', 'webgpu')
ROFORMER_RUNTIME_CHOICES = ('cpu', 'webgpu')

_LEGACY_EXECUTION_MAP = {
    'cpu': 'cpu',
    'webgpu': 'webgpu',
    'wgpu': 'webgpu',
    'gpu': 'webgpu',
    'dml': 'webgpu',
    'directml': 'webgpu',
    'cuda': 'webgpu',
    'rocm': 'webgpu',
}


def normalize_execution_device(value: Any, *, default: str = 'webgpu') -> str:
    """Normalize a separation/MIDI device choice, migrating legacy ``dml`` to ``webgpu``."""
    if isinstance(value, bool) or value is None:
        return default
    key = str(value).strip().casefold()
    if key in _LEGACY_EXECUTION_MAP:
        return _LEGACY_EXECUTION_MAP[key]
    raise ValueError('推理设备应为 CPU 或 WebGPU。')


def normalize_roformer_device(value: Any, *, default: str = 'auto') -> str:
    """Normalize a RoFormer execution device choice, migrating legacy ``cuda`` to ``webgpu``."""
    if isinstance(value, bool) or value is None:
        return default
    key = str(value).strip().casefold()
    if key == 'auto':
        return 'auto'
    if key in _LEGACY_EXECUTION_MAP:
        return _LEGACY_EXECUTION_MAP[key]
    raise ValueError('新模型的设备请选择自动、CPU 或 WebGPU。')


def normalize_roformer_runtime(value: Any) -> str:
    """Normalize a RoFormer install runtime, migrating legacy ``cuda`` to ``webgpu``."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError('请选择 CPU 或 WebGPU 运行库。')
    key = value.strip().casefold()
    if key in ('cpu', 'webgpu'):
        return key
    if key in ('cuda', 'dml', 'gpu'):
        return 'webgpu'
    raise ValueError('请选择 CPU 或 WebGPU 运行库。')


def vendor_name(vendor_id: int, adapter_name: str = '') -> str:
    if vendor_id in VENDOR_LABELS:
        return VENDOR_LABELS[vendor_id]
    lowered = str(adapter_name or '').casefold()
    if 'amd' in lowered or 'radeon' in lowered:
        return 'AMD'
    if 'nvidia' in lowered or 'geforce' in lowered or 'rtx' in lowered:
        return 'NVIDIA'
    if 'intel' in lowered:
        return 'Intel'
    return 'Unknown'


def _serialize_adapter(adapter: Any) -> dict[str, Any]:
    vid = int(getattr(adapter, 'vendor_id', 0) or 0)
    name = str(getattr(adapter, 'name', '') or '')
    dedicated = int(getattr(adapter, 'dedicated_vram_bytes', 0) or 0)
    shared = int(getattr(adapter, 'shared_vram_bytes', 0) or 0)
    return {
        'index': int(getattr(adapter, 'index', 0)),
        'name': name,
        'vendor_id': vid,
        'vendor': vendor_name(vid, name),
        'dedicated_vram_bytes': dedicated,
        'shared_vram_bytes': shared,
        'is_software': bool(getattr(adapter, 'is_software', False)),
        'is_apu_eligible': bool(getattr(adapter, 'is_apu_eligible', dedicated >= (1 << 30))),
    }


def _probe_ort_webgpu() -> dict[str, Any]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        return {
            'available': False,
            'version': '',
            'providers': [],
            'ep_devices': [],
            'error': str(exc),
        }
    providers = list(ort.get_available_providers())
    ep_devices: list[dict[str, Any]] = []
    get_ep_devices = getattr(ort, 'get_ep_devices', None)
    if callable(get_ep_devices):
        try:
            for item in get_ep_devices():
                if getattr(item, 'ep_name', '') != 'WebGpuExecutionProvider':
                    continue
                device = getattr(item, 'device', None)
                meta = dict(getattr(device, 'metadata', None) or {})
                ep_devices.append({
                    'vendor': str(getattr(device, 'vendor', '') or ''),
                    'description': str(meta.get('Description', '') or ''),
                    'discrete': str(meta.get('Discrete', '0')) == '1',
                    'dxgi_adapter_number': meta.get('DxgiAdapterNumber'),
                    'video_memory': str(meta.get('DxgiVideoMemory', '') or ''),
                })
        except RuntimeError:
            ep_devices = []
    return {
        'available': 'WebGpuExecutionProvider' in providers,
        'legacy_dml_available': 'DmlExecutionProvider' in providers,
        'version': str(getattr(ort, '__version__', '')),
        'providers': providers,
        'ep_devices': ep_devices,
        'error': '',
    }


def probe_gpu_environment() -> dict[str, Any]:
    """Inspect local GPUs and ONNX Runtime WebGPU readiness for UI and diagnostics."""
    dxgi_adapters = [_serialize_adapter(a) for a in runtime_devices.adapters() if not a.is_software]
    ort_info = _probe_ort_webgpu()
    module = runtime_devices.device_module()
    preferred_raw = None
    try:
        module.select_preferred_gpu_adapter.cache_clear()
        preferred_raw = module.select_preferred_gpu_adapter()
    except ValueError:
        preferred_raw = None
    preferred = _serialize_adapter(preferred_raw) if preferred_raw is not None else None
    webgpu_ready = bool(
        ort_info['available']
        and (preferred is not None or bool(ort_info['ep_devices']) or sys.platform != 'win32')
    )
    has_amd = any(a['vendor'] == 'AMD' for a in dxgi_adapters) or any(
        'amd' in (d['vendor'] + ' ' + d['description']).casefold() for d in ort_info['ep_devices']
    )
    recommended = 'webgpu' if webgpu_ready else 'cpu'
    return {
        'platform': sys.platform,
        'recommended_device': recommended,
        'recommended_roformer_runtime': recommended,
        'webgpu_ready': webgpu_ready,
        'has_amd_gpu': has_amd,
        'preferred_adapter': preferred,
        'adapters': dxgi_adapters,
        'onnxruntime': ort_info,
    }


def adapter_choices() -> list[dict[str, Any]]:
    """Return UI-friendly GPU adapter choices with real hardware names when available."""
    rows: list[dict[str, Any]] = [{'label': '自动选择', 'value': 'auto'}]
    hardware = [a for a in runtime_devices.adapters() if not a.is_software]
    if hardware:
        for adapter in hardware:
            vram_gib = adapter.dedicated_vram_bytes / float(1 << 30)
            label = f"{adapter.index}: {adapter.name} ({vram_gib:.1f} GiB)"
            rows.append({'label': label, 'value': int(adapter.index)})
        return rows
    for index in range(8):
        rows.append({'label': f'适配器 {index}', 'value': index})
    return rows
