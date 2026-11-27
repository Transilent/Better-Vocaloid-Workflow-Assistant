"""Unit and hardware tests for the unified ONNX Runtime WebGPU & AMD GPU backend."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))
os.environ['VWA_TESTING'] = '1'
os.environ['BVWA_TESTING'] = '1'

import error_recovery
import gpu_backend
import runtime_devices
import vwa_config
import workflow


def test_legacy_device_normalization_and_config_migration():
    assert gpu_backend.normalize_execution_device('dml') == 'webgpu'
    assert gpu_backend.normalize_execution_device('directml') == 'webgpu'
    assert gpu_backend.normalize_execution_device('cuda') == 'webgpu'
    assert gpu_backend.normalize_execution_device('webgpu') == 'webgpu'
    assert gpu_backend.normalize_execution_device('cpu') == 'cpu'

    assert gpu_backend.normalize_roformer_device('cuda') == 'webgpu'
    assert gpu_backend.normalize_roformer_device('auto') == 'auto'
    assert gpu_backend.normalize_roformer_runtime('cuda') == 'webgpu'
    assert gpu_backend.normalize_roformer_runtime('webgpu') == 'webgpu'
    assert gpu_backend.normalize_roformer_runtime('cpu') == 'cpu'

    clean, warnings, migrated = vwa_config.validate_and_migrate({
        'schema_version': 2,
        'separation_device': 'dml',
        'midi_device': 'dml',
    })
    assert migrated is True
    assert not warnings
    assert clean['separation_device'] == 'webgpu'
    assert clean['midi_device'] == 'webgpu'

    opts = workflow.validate_options({
        'separation_device': 'dml',
        'midi_device': 'dml',
        'roformer_device': 'cuda',
    })
    assert opts['separation_device'] == 'webgpu'
    assert opts['midi_device'] == 'webgpu'
    assert opts['roformer_device'] == 'webgpu'


def test_amd_discrete_and_apu_adapter_selection():
    mod = runtime_devices.device_module()
    Adapter = mod.DxgiAdapterInfo

    # Case 1: AMD RX 9070 XT + Intel iGPU -> must prefer discrete AMD GPU.
    dual = (
        Adapter(0, 'AMD Radeon RX 9070 XT', 16 << 30, False, vendor_id=0x1002, shared_vram_bytes=32 << 30),
        Adapter(1, 'Intel(R) Graphics', 128 << 20, False, vendor_id=0x8086, shared_vram_bytes=32 << 30),
        Adapter(2, 'Microsoft Basic Render Driver', 0, True, vendor_id=0x1414, shared_vram_bytes=0),
    )
    with patch.dict(os.environ, {}, clear=False):
        for key in ('VWA_GPU_ADAPTER', 'VWA_DML_ADAPTER', 'BVWA_DML_ADAPTER'):
            os.environ.pop(key, None)
        with patch.object(mod, '_enumerate_dxgi_adapters', return_value=dual):
            mod.select_preferred_gpu_adapter.cache_clear()
            picked = mod.select_preferred_gpu_adapter()
            assert picked is not None and picked.index == 0 and picked.vendor_name == 'AMD'

    # Case 2: AMD APU (e.g. Radeon 780M with 512 MiB BIOS VRAM + 8 GiB shared) -> must NOT drop to CPU.
    apu_only = (
        Adapter(0, 'AMD Radeon 780M Graphics', 512 << 20, False, vendor_id=0x1002, shared_vram_bytes=8 << 30),
        Adapter(1, 'Microsoft Basic Render Driver', 0, True, vendor_id=0x1414, shared_vram_bytes=0),
    )
    with patch.dict(os.environ, {}, clear=False):
        for key in ('VWA_GPU_ADAPTER', 'VWA_DML_ADAPTER', 'BVWA_DML_ADAPTER'):
            os.environ.pop(key, None)
        with patch.object(mod, '_enumerate_dxgi_adapters', return_value=apu_only):
            mod.select_preferred_gpu_adapter.cache_clear()
            picked = mod.select_preferred_gpu_adapter()
            assert picked is not None and picked.index == 0 and picked.is_apu_eligible

    # Case 3: Explicit VWA_GPU_ADAPTER selects specific index.
    with patch.dict(os.environ, {'VWA_GPU_ADAPTER': '1'}, clear=False):
        with patch.object(mod, '_enumerate_dxgi_adapters', return_value=dual):
            mod.select_preferred_gpu_adapter.cache_clear()
            picked = mod.select_preferred_gpu_adapter()
            assert picked is not None and picked.index == 1 and picked.vendor_name == 'Intel'
    mod.select_preferred_gpu_adapter.cache_clear()


def test_webgpu_error_recovery_classification():
    info = error_recovery.explain('WebGpuExecutionProvider failed: Dawn error creating device')
    assert info['action'] == 'cpu' and info['title'] == '推理设备不可用'

    lost = error_recovery.explain('Vulkan VK_ERROR_DEVICE_LOST during dispatch')
    assert lost['action'] == 'cpu' and lost['title'] == '显卡推理被中断'


def test_roformer_spectrogram_roundtrip():
    import numpy as np
    try:
        import scipy.fft  # noqa: F401
    except ImportError:
        return
    from roformer_onnx import RoformerSpectrogram

    rng = np.random.default_rng(42)
    wave = (rng.standard_normal((2, 44100)) * 0.25).astype(np.float32)
    transform = RoformerSpectrogram(n_fft=2048, hop_length=512, win_length=2048, dim_freqs_in=1025)
    spec = transform.forward(wave)
    assert spec.shape[0] == 1 and spec.shape[1] == 4 and spec.shape[2] == 1025
    rebuilt = transform.inverse(spec, wave.shape[1])
    assert rebuilt.shape == wave.shape
    rms_err = float(np.sqrt(np.mean((rebuilt - wave) ** 2)))
    assert rms_err < 1e-5, f'STFT/iSTFT round-trip RMS error too high: {rms_err}'


def test_live_webgpu_session_if_installed():
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError:
        return
    if 'WebGpuExecutionProvider' not in ort.get_available_providers():
        return
    env = gpu_backend.probe_gpu_environment()
    assert env['onnxruntime']['available'] is True
    sample = Path(ort.__file__).parent / 'datasets' / 'mul_1.onnx'
    if not sample.is_file():
        return
    opts = ort.SessionOptions()
    opts.enable_mem_pattern = False
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    effective, session = runtime_devices.create_session(sample, opts, {}, 'webgpu', label='WebGPU Test')
    assert effective == 'webgpu'
    assert 'WebGpuExecutionProvider' in session.get_providers()
    inp = session.get_inputs()[0]
    shape = [d if isinstance(d, int) else 2 for d in inp.shape]
    x = np.ones(shape, dtype=np.float32) * 2.0
    y = session.run(None, {inp.name: x})[0]
    assert np.isfinite(y).all()
    print(f'LIVE WebGPU OK: preferred={env["preferred_adapter"]} providers={session.get_providers()}')


def main():
    test_legacy_device_normalization_and_config_migration()
    test_amd_discrete_and_apu_adapter_selection()
    test_webgpu_error_recovery_classification()
    test_roformer_spectrogram_roundtrip()
    test_live_webgpu_session_if_installed()
    print('PASS test_webgpu_backend')


if __name__ == '__main__':
    main()
