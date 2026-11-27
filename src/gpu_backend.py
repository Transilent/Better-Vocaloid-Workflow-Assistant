"""Unified GPU capability probe and cross-vendor (AMD/NVIDIA/Intel) backend helpers.

Pure Python, zero Qt dependencies. Centralizes DXGI adapter discovery (including
AMD APU 780M/890M shared-memory eligibility) and ONNX Runtime DirectML / WebGPU
provider probing without breaking legacy ``dml`` / ``cuda`` configurations.
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


def _probe_onnxruntime() -> dict[str, Any]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        return {
            'available': False,
            'webgpu_available': False,
            'dml_available': False,
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
    webgpu = 'WebGpuExecutionProvider' in providers
    dml = 'DmlExecutionProvider' in providers
    return {
        'available': webgpu or dml,
        'webgpu_available': webgpu,
        'dml_available': dml,
        'version': str(getattr(ort, '__version__', '')),
        'providers': providers,
        'ep_devices': ep_devices,
        'error': '',
    }


def probe_gpu_environment() -> dict[str, Any]:
    """Inspect local GPUs and ONNX Runtime DirectML/WebGPU readiness for diagnostics & UI."""
    dxgi_adapters = [_serialize_adapter(a) for a in runtime_devices.adapters() if not a.is_software]
    ort_info = _probe_onnxruntime()
    preferred_raw = None
    try:
        preferred_raw = runtime_devices.preferred_gpu_adapter()
    except ValueError:
        preferred_raw = None
    preferred = _serialize_adapter(preferred_raw) if preferred_raw is not None else None
    webgpu_ready = bool(
        ort_info['webgpu_available']
        and (preferred is not None or bool(ort_info['ep_devices']) or sys.platform != 'win32')
    )
    dml_ready = bool(ort_info['dml_available'] and preferred is not None)
    has_amd = any(a['vendor'] == 'AMD' for a in dxgi_adapters) or any(
        'amd' in (d['vendor'] + ' ' + d['description']).casefold() for d in ort_info['ep_devices']
    )
    if webgpu_ready:
        recommended = 'webgpu'
    elif dml_ready:
        recommended = 'dml'
    else:
        recommended = 'cpu'
    return {
        'platform': sys.platform,
        'recommended_device': recommended,
        'webgpu_ready': webgpu_ready,
        'dml_ready': dml_ready,
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
