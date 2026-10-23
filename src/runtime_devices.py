"""One adapter and CPU-thread policy for the assistant's inference engines."""

from common import environment_value
import importlib.util
import os
from functools import lru_cache
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent


def available_memory():
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ('total_phys','avail_phys','total_page','avail_page','total_virtual','avail_virtual','extended')]]
    info = MemoryStatus(); info.length = ctypes.sizeof(info)
    if os.name != 'nt' or not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)):
        return 0  # Keep the original release policy when no reliable budget exists.
    return info.avail_phys


def validate(adapter='auto', threads=0):
    if adapter != 'auto' and (type(adapter) is not int or adapter < 0):
        raise ValueError('DirectML 显卡选择无效。')
    if type(threads) is not int or not 0 <= threads <= 64:
        raise ValueError('CPU 线程数应为自动或 1–64。')


def prepare_environment(tools):
    adapter, threads = tools.get('dml_adapter', 'auto'), tools.get('cpu_threads', 0)
    validate(adapter, threads)
    for key, value in [('VWA_DML_ADAPTER', None if adapter == 'auto' else str(adapter)),
                       ('VWA_CPU_THREADS', str(threads) if threads else None)]:
        for current in (key, 'B' + key):
            if value is None: os.environ.pop(current, None)
            else: os.environ[current] = value


@lru_cache(maxsize=1)
def device_module():
    path = ROOT / 'dependencies/vocal2midi2/inference/device_utils.py'
    spec = importlib.util.spec_from_file_location('_vwa_device_probe', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def adapters():
    return device_module()._enumerate_dxgi_adapters()


def providers(tools, device, label='MDX'):
    prepare_environment(tools)
    module = device_module()
    module.select_preferred_gpu_adapter.cache_clear()
    return module.resolve_onnx_providers(device, label=label)


def session_threads(options, default=0):
    threads = int(environment_value('VWA_CPU_THREADS', default))
    if threads:
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
    return options
