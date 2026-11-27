from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


RUNTIME_DEVICE_CHOICES = ("dml", "webgpu", "cpu", "cuda", "metal")
VISIBLE_RUNTIME_DEVICE_CHOICES = ("dml", "cpu") if sys.platform == "win32" else ("cpu", "metal")

ProviderSpec = str | tuple[str, dict[str, str]]
MIN_GPU_DEDICATED_VRAM_BYTES = 1 << 30
MIN_APU_DEDICATED_VRAM_BYTES = 256 << 20
MIN_APU_SHARED_VRAM_BYTES = 4 << 30

VENDOR_AMD = 0x1002
VENDOR_NVIDIA = 0x10DE
VENDOR_INTEL = 0x8086
VENDOR_NAMES = {
    VENDOR_AMD: "AMD",
    VENDOR_NVIDIA: "NVIDIA",
    VENDOR_INTEL: "Intel",
}

GPU_EXECUTION_PROVIDERS = ("DmlExecutionProvider", "WebGpuExecutionProvider")
_VALID_DAWN_BACKENDS = {"d3d12": "D3D12", "vulkan": "Vulkan", "metal": "Metal"}

_DEVICE_ALIASES = {
    "": "dml",
    "auto": "dml",
    "cuda": "dml",
    "directml": "dml",
    "dml": "dml",
    "gpu": "dml",
    "rocm": "dml",
    "webgpu": "webgpu",
    "wgpu": "webgpu",
    "metal": "metal",
    "cpu": "cpu",
}
_DXGI_ADAPTER_FLAG_SOFTWARE = 0x2
_DXGI_ERROR_NOT_FOUND = 0x887A0002


@dataclass(frozen=True)
class DxgiAdapterInfo:
    index: int
    name: str
    dedicated_vram_bytes: int
    is_software: bool
    vendor_id: int = 0
    shared_vram_bytes: int = 0
    luid: int = 0

    @property
    def vendor_name(self) -> str:
        if self.vendor_id in VENDOR_NAMES:
            return VENDOR_NAMES[self.vendor_id]
        lowered = self.name.lower()
        if "amd" in lowered or "radeon" in lowered:
            return "AMD"
        if "nvidia" in lowered or "geforce" in lowered or "rtx" in lowered:
            return "NVIDIA"
        if "intel" in lowered:
            return "Intel"
        return "Unknown"

    @property
    def is_amd(self) -> bool:
        return self.vendor_name == "AMD"

    @property
    def is_apu_eligible(self) -> bool:
        if self.is_software:
            return False
        if self.dedicated_vram_bytes >= MIN_GPU_DEDICATED_VRAM_BYTES:
            return True
        # AMD/Intel integrated GPUs (e.g. Radeon 780M/890M) often report 512 MiB
        # dedicated BIOS VRAM while sharing 8+ GiB of unified system memory.
        if self.shared_vram_bytes >= MIN_APU_SHARED_VRAM_BYTES:
            if self.dedicated_vram_bytes >= MIN_APU_DEDICATED_VRAM_BYTES:
                return True
            if self.is_amd:
                return True
        return False


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint32),
        ("DeviceId", ctypes.c_uint32),
        ("SubSysId", ctypes.c_uint32),
        ("Revision", ctypes.c_uint32),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint32),
    ]


_IID_IDXGIFactory1 = _GUID(
    0x770AAE78,
    0xF26F,
    0x4DBA,
    (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
)


def default_runtime_device() -> str:
    """Keep DirectML as default on Windows for 100% legacy runtime compatibility."""
    return "dml" if sys.platform == "win32" else "cpu"


def normalize_runtime_device(device: str | None, default: str | None = None) -> str:
    fallback = default if default is not None else default_runtime_device()
    value = str(fallback if device is None else device).strip().lower()
    if not value:
        value = fallback
    return _DEVICE_ALIASES.get(value, value)


def is_gpu_provider(provider_name: str | None) -> bool:
    return str(provider_name or "") in GPU_EXECUTION_PROVIDERS


def _hresult_code(value: int) -> int:
    return ctypes.c_uint32(value).value


def _call_com_method(obj: ctypes.c_void_p, method_index: int, restype, argtypes, *args):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    func_ptr = int(vtable[method_index])
    func = ctypes.WINFUNCTYPE(restype, *argtypes)(func_ptr)
    return func(*args)


def _release_com(obj: ctypes.c_void_p | None) -> None:
    if obj is None or not getattr(obj, "value", None):
        return
    _call_com_method(obj, 2, ctypes.c_ulong, [ctypes.c_void_p], obj)


@lru_cache(maxsize=1)
def _enumerate_dxgi_adapters() -> tuple[DxgiAdapterInfo, ...]:
    if not hasattr(ctypes, "WinDLL"):
        return ()
    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError:
        return ()

    create_factory = dxgi.CreateDXGIFactory1
    create_factory.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    create_factory.restype = ctypes.c_long

    factory = ctypes.c_void_p()
    adapters: list[DxgiAdapterInfo] = []
    try:
        hr = create_factory(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory))
        if hr < 0 or not factory.value:
            return ()

        adapter_index = 0
        while True:
            adapter = ctypes.c_void_p()
            hr = _call_com_method(
                factory,
                12,
                ctypes.c_long,
                [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                factory,
                adapter_index,
                ctypes.byref(adapter),
            )
            if _hresult_code(hr) == _DXGI_ERROR_NOT_FOUND:
                break
            if hr < 0 or not adapter.value:
                break
            try:
                desc = _DXGI_ADAPTER_DESC1()
                hr_desc = _call_com_method(
                    adapter,
                    10,
                    ctypes.c_long,
                    [ctypes.c_void_p, ctypes.POINTER(_DXGI_ADAPTER_DESC1)],
                    adapter,
                    ctypes.byref(desc),
                )
                if hr_desc >= 0:
                    luid = (int(desc.AdapterLuid.HighPart) << 32) | int(desc.AdapterLuid.LowPart)
                    adapters.append(
                        DxgiAdapterInfo(
                            index=adapter_index,
                            name=str(desc.Description).rstrip("\x00").strip() or f"Adapter {adapter_index}",
                            dedicated_vram_bytes=int(desc.DedicatedVideoMemory),
                            is_software=bool(desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE),
                            vendor_id=int(desc.VendorId),
                            shared_vram_bytes=int(desc.SharedSystemMemory),
                            luid=luid,
                        )
                    )
            finally:
                _release_com(adapter)
            adapter_index += 1
    except Exception:
        return ()
    finally:
        _release_com(factory)

    return tuple(adapters)


def format_gib(num_bytes: int) -> str:
    return f"{num_bytes / float(1 << 30):.1f} GiB"


def describe_gpu_adapter(adapter: DxgiAdapterInfo) -> str:
    detail = f"{format_gib(adapter.dedicated_vram_bytes)} dedicated VRAM"
    if adapter.shared_vram_bytes > 0 and adapter.dedicated_vram_bytes < MIN_GPU_DEDICATED_VRAM_BYTES:
        detail += f", {format_gib(adapter.shared_vram_bytes)} shared"
    return f"adapter {adapter.index}: {adapter.name} [{adapter.vendor_name}] ({detail})"


def _selected_adapter_env() -> str | None:
    for key in ("VWA_GPU_ADAPTER", "VWA_DML_ADAPTER", "BVWA_DML_ADAPTER"):
        value = os.environ.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _dawn_backend_option() -> str | None:
    raw = str(os.environ.get("VWA_WEBGPU_BACKEND", "")).strip().lower()
    return _VALID_DAWN_BACKENDS.get(raw)


@lru_cache(maxsize=8)
def select_preferred_gpu_adapter(
    min_dedicated_vram_bytes: int = MIN_GPU_DEDICATED_VRAM_BYTES,
) -> DxgiAdapterInfo | None:
    selected = _selected_adapter_env()
    if selected is not None:
        matches = [a for a in _enumerate_dxgi_adapters() if a.index == int(selected) and not a.is_software]
        if not matches:
            raise ValueError("The selected GPU adapter is unavailable; choose Auto or another adapter.")
        return matches[0]
    eligible = [
        adapter
        for adapter in _enumerate_dxgi_adapters()
        if not adapter.is_software
        and (adapter.dedicated_vram_bytes >= int(min_dedicated_vram_bytes) or adapter.is_apu_eligible)
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda adapter: (
            adapter.dedicated_vram_bytes >= int(min_dedicated_vram_bytes),
            adapter.dedicated_vram_bytes,
            adapter.shared_vram_bytes,
            -adapter.index,
        ),
    )


def _select_preferred_dml_adapter() -> DxgiAdapterInfo | None:
    return select_preferred_gpu_adapter(MIN_GPU_DEDICATED_VRAM_BYTES)


def _webgpu_provider_options(adapter: DxgiAdapterInfo | None) -> dict[str, str]:
    options: dict[str, str] = {}
    if adapter is not None:
        options["deviceId"] = str(adapter.index)
    backend = _dawn_backend_option()
    if backend is not None:
        options["dawnBackendType"] = backend
    return options


def _match_webgpu_ep_device(ort, adapter: DxgiAdapterInfo | None):
    """Find the OrtEpDevice matching the selected DXGI adapter (or best discrete GPU)."""
    get_ep_devices = getattr(ort, "get_ep_devices", None)
    if not callable(get_ep_devices):
        return None
    try:
        ep_devices = [d for d in get_ep_devices() if getattr(d, "ep_name", "") == "WebGpuExecutionProvider"]
    except Exception:
        return None
    if not ep_devices:
        return None
    if adapter is not None:
        for dev in ep_devices:
            meta = getattr(getattr(dev, "device", None), "metadata", None) or {}
            if meta.get("DxgiAdapterNumber") == str(adapter.index):
                return dev
            if adapter.luid and meta.get("LUID") == str(adapter.luid):
                return dev
        for dev in ep_devices:
            meta = getattr(getattr(dev, "device", None), "metadata", None) or {}
            if str(meta.get("Description", "")).strip().lower() == adapter.name.strip().lower():
                return dev

    def _rank(dev):
        meta = getattr(getattr(dev, "device", None), "metadata", None) or {}
        discrete = 1 if str(meta.get("Discrete", "0")) == "1" else 0
        mem_text = str(meta.get("DxgiVideoMemory", "0")).split()[0]
        try:
            mem_mb = int(mem_text)
        except ValueError:
            mem_mb = 0
        return (discrete, mem_mb)

    return max(ep_devices, key=_rank)


def _resolve_webgpu(ort, *, label: str) -> tuple[str, list[ProviderSpec]] | None:
    adapter = select_preferred_gpu_adapter(MIN_GPU_DEDICATED_VRAM_BYTES) if sys.platform == "win32" else None
    if sys.platform != "win32" or adapter is not None:
        opts = _webgpu_provider_options(adapter)
        if adapter is not None:
            logger.info(f"[{label}] Using WebGPU {describe_gpu_adapter(adapter)}.")
        else:
            logger.info(f"[{label}] Using WebGPU execution provider.")
        return "webgpu", [("WebGpuExecutionProvider", opts), "CPUExecutionProvider"]
    if _match_webgpu_ep_device(ort, None) is not None:
        opts = _webgpu_provider_options(None)
        logger.info(f"[{label}] Using WebGPU execution provider (EP device auto-select).")
        return "webgpu", [("WebGpuExecutionProvider", opts), "CPUExecutionProvider"]
    return None


def _resolve_dml(*, label: str) -> tuple[str, list[ProviderSpec]] | None:
    adapter = _select_preferred_dml_adapter()
    if adapter is not None:
        logger.info(f"[{label}] Using DirectML {describe_gpu_adapter(adapter)}.")
        return "dml", [("DmlExecutionProvider", {"device_id": str(adapter.index)}), "CPUExecutionProvider"]
    return None


def resolve_onnx_providers(device: str | None, *, label: str = "ONNX") -> tuple[str, list[ProviderSpec]]:
    import onnxruntime as ort

    normalized = normalize_runtime_device(device)
    available = set(ort.get_available_providers())
    if normalized == "cpu":
        return "cpu", ["CPUExecutionProvider"]
    if normalized == "metal":
        if "WebGpuExecutionProvider" in available:
            opts = _webgpu_provider_options(None)
            logger.info(f"[{label}] Using WebGPU (Metal backend).")
            return "webgpu", [("WebGpuExecutionProvider", opts), "CPUExecutionProvider"]
        logger.info(f"[{label}] Metal/CoreML is not enabled for this ONNX stage; using CPUExecutionProvider.")
        return "cpu", ["CPUExecutionProvider"]

    # Prefer explicitly requested provider; transparently fall back to the other GPU EP if installed.
    prefer_webgpu = normalized == "webgpu"
    order = ("WebGpuExecutionProvider", "DmlExecutionProvider") if prefer_webgpu else (
        "DmlExecutionProvider", "WebGpuExecutionProvider"
    )
    for ep_name in order:
        if ep_name not in available:
            continue
        resolved = _resolve_webgpu(ort, label=label) if ep_name == "WebGpuExecutionProvider" else _resolve_dml(label=label)
        if resolved is not None:
            return resolved

    logger.info(f"[{label}] No eligible GPU execution provider/adapter found; falling back to CPUExecutionProvider.")
    return "cpu", ["CPUExecutionProvider"]


def create_onnx_session(
    model_path: str | Path,
    sess_options=None,
    device: str | None = None,
    *,
    label: str = "ONNX",
):
    """Create an ONNX Runtime InferenceSession bound to the selected DirectML or WebGPU adapter."""
    import onnxruntime as ort

    options = sess_options if sess_options is not None else ort.SessionOptions()
    provider_name, providers = resolve_onnx_providers(device, label=label)
    if provider_name == "webgpu" and hasattr(options, "add_provider_for_devices"):
        adapter = select_preferred_gpu_adapter(MIN_GPU_DEDICATED_VRAM_BYTES) if sys.platform == "win32" else None
        ep_dev = _match_webgpu_ep_device(ort, adapter)
        if ep_dev is not None:
            webgpu_opts: dict[str, str] = {}
            backend = _dawn_backend_option()
            if backend is not None:
                webgpu_opts["dawnBackendType"] = backend
            try:
                options.add_provider_for_devices([ep_dev], webgpu_opts)
                return provider_name, ort.InferenceSession(str(model_path), sess_options=options)
            except Exception as exc:
                logger.warning(f"[{label}] Explicit WebGPU device binding failed ({exc}); retrying with provider list.")
    return provider_name, ort.InferenceSession(str(model_path), sess_options=options, providers=providers)


def should_cache_onnx_session(device: str | None = None) -> bool:
    """Keep per-process session cache ONLY under DirectML to avoid DML teardown AVs."""
    try:
        provider_name, _ = resolve_onnx_providers(device, label="CacheCheck")
    except Exception:
        return False
    return provider_name == "dml"


def use_gpu(device: str | None) -> bool:
    import onnxruntime as ort

    if normalize_runtime_device(device) == "cpu":
        return False
    available = set(ort.get_available_providers())
    if "DmlExecutionProvider" in available and _select_preferred_dml_adapter() is not None:
        return True
    if "WebGpuExecutionProvider" in available:
        if sys.platform != "win32":
            return True
        return select_preferred_gpu_adapter(MIN_GPU_DEDICATED_VRAM_BYTES) is not None or (
            _match_webgpu_ep_device(ort, None) is not None
        )
    return False


# Backwards-compatible alias used by existing Qwen3-ASR call sites.
use_dml = use_gpu


def configure_onnx_threads(options):
    selected = os.environ.get("VWA_CPU_THREADS") or os.environ.get("BVWA_CPU_THREADS")
    if selected:
        threads = int(selected)
        if not 1 <= threads <= 64:
            raise ValueError("CPU thread count must be between 1 and 64.")
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
    return options
