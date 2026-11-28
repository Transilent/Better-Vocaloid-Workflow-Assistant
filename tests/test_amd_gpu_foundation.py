"""PR A regression tests: AMD APU/DXGI adapter selection, dual DirectML/WebGPU
routing, AMD/Vulkan error recovery, RMVPE fast resampling, Qwen >=12 GiB VRAM
session reuse gating, and PreparationPage preset selector fix.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import numpy as np
import error_recovery
import gpu_backend
import midi_bridge
import runtime_devices
import vwa_io


def _has_qt6() -> bool:
    for qt_root in (
        os.environ.get('VWA_TEST_QT6_ROOT', ''),
        str(APP / 'runtime/qt6'),
        'C:/vwa/workspace/preview/vwa/runtime/qt6',
    ):
        if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
            sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


class AmdGpuFoundationTests(unittest.TestCase):
    """Pure-Python hermetic tests (always run in minimal GitHub CI without GPU/Qt6)."""

    def test_01_amd_discrete_apu_and_explicit_adapter_selection(self):
        mod = runtime_devices.device_module()
        Adapter = mod.DxgiAdapterInfo

        # 1) AMD RX 9070 XT discrete + Intel iGPU -> prefer discrete AMD GPU
        dual = (
            Adapter(0, 'AMD Radeon RX 9070 XT', 16 << 30, False, vendor_id=0x1002, shared_vram_bytes=32 << 30),
            Adapter(1, 'Intel(R) Graphics', 128 << 20, False, vendor_id=0x8086, shared_vram_bytes=32 << 30),
            Adapter(2, 'Microsoft Basic Render Driver', 0, True, vendor_id=0x1414, shared_vram_bytes=0),
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ('VWA_GPU_ADAPTER', 'VWA_DML_ADAPTER', 'BVWA_DML_ADAPTER'):
                os.environ.pop(key, None)
            with mock.patch.object(mod, '_enumerate_dxgi_adapters', return_value=dual):
                mod.select_preferred_gpu_adapter.cache_clear()
                picked = mod.select_preferred_gpu_adapter()
                self.assertIsNotNone(picked)
                self.assertEqual(picked.index, 0)
                self.assertEqual(picked.vendor_name, 'AMD')
                self.assertTrue(picked.is_amd)

        # 2) AMD APU (Radeon 780M: 512 MiB BIOS VRAM + 8 GiB shared) -> must NOT drop to CPU
        apu_only = (
            Adapter(0, 'AMD Radeon 780M Graphics', 512 << 20, False, vendor_id=0x1002, shared_vram_bytes=8 << 30),
            Adapter(1, 'Microsoft Basic Render Driver', 0, True, vendor_id=0x1414, shared_vram_bytes=0),
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ('VWA_GPU_ADAPTER', 'VWA_DML_ADAPTER', 'BVWA_DML_ADAPTER'):
                os.environ.pop(key, None)
            with mock.patch.object(mod, '_enumerate_dxgi_adapters', return_value=apu_only):
                mod.select_preferred_gpu_adapter.cache_clear()
                picked = mod.select_preferred_gpu_adapter()
                self.assertIsNotNone(picked)
                self.assertEqual(picked.index, 0)
                self.assertTrue(picked.is_apu_eligible)

        # 3) Explicit VWA_GPU_ADAPTER / VWA_DML_ADAPTER overrides auto selection
        with mock.patch.dict(os.environ, {'VWA_GPU_ADAPTER': '1'}, clear=False):
            with mock.patch.object(mod, '_enumerate_dxgi_adapters', return_value=dual):
                mod.select_preferred_gpu_adapter.cache_clear()
                picked = mod.select_preferred_gpu_adapter()
                self.assertIsNotNone(picked)
                self.assertEqual(picked.index, 1)
                self.assertEqual(picked.vendor_name, 'Intel')
        mod.select_preferred_gpu_adapter.cache_clear()

    def test_02_dual_directml_and_webgpu_provider_resolution_backward_compat(self):
        mod = runtime_devices.device_module()
        Adapter = mod.DxgiAdapterInfo
        amd_card = (
            Adapter(0, 'AMD Radeon RX 7800 XT', 16 << 30, False, vendor_id=0x1002, shared_vram_bytes=16 << 30),
        )

        # Legacy Windows default remains 'dml' so existing config.json stays 100% compatible
        if sys.platform == 'win32':
            self.assertEqual(mod.default_runtime_device(), 'dml')
        self.assertEqual(mod.normalize_runtime_device('dml'), 'dml')
        self.assertEqual(mod.normalize_runtime_device('webgpu'), 'webgpu')
        self.assertEqual(mod.normalize_runtime_device('cpu'), 'cpu')

        # When DirectML EP is installed: 'dml' resolves to DirectML, should_cache == True
        fake_ort_dml = types.SimpleNamespace(get_available_providers=lambda: ['DmlExecutionProvider', 'CPUExecutionProvider'])
        with mock.patch.dict(sys.modules, {'onnxruntime': fake_ort_dml}), \
             mock.patch.object(mod, '_enumerate_dxgi_adapters', return_value=amd_card):
            mod.select_preferred_gpu_adapter.cache_clear()
            name, provs = mod.resolve_onnx_providers('dml', label='Test')
            self.assertEqual(name, 'dml')
            self.assertEqual(provs[0][0], 'DmlExecutionProvider')
            self.assertTrue(mod.should_cache_onnx_session('dml'))
            self.assertFalse(mod.should_cache_onnx_session('cpu'))

        # When WebGPU EP is installed instead of DirectML: 'dml' transparently uses WebGPU, should_cache == False
        fake_ort_webgpu = types.SimpleNamespace(get_available_providers=lambda: ['WebGpuExecutionProvider', 'CPUExecutionProvider'])
        with mock.patch.dict(sys.modules, {'onnxruntime': fake_ort_webgpu}), \
             mock.patch.object(mod, '_enumerate_dxgi_adapters', return_value=amd_card):
            mod.select_preferred_gpu_adapter.cache_clear()
            name, provs = mod.resolve_onnx_providers('dml', label='Test')
            self.assertEqual(name, 'webgpu')
            self.assertEqual(provs[0][0], 'WebGpuExecutionProvider')
            self.assertFalse(mod.should_cache_onnx_session('dml'))
            env = gpu_backend.probe_gpu_environment()
            self.assertTrue(env['has_amd_gpu'])
            self.assertTrue(env['webgpu_ready'])
            self.assertEqual(env['recommended_device'], 'webgpu')

            choices = gpu_backend.adapter_choices()
            self.assertEqual(choices[0]['value'], 'auto')
            self.assertIn('RX 7800 XT', choices[1]['label'])
        mod.select_preferred_gpu_adapter.cache_clear()

    def test_03_amd_vulkan_webgpu_error_recovery_classification(self):
        for msg, expected_title in (
            ('WebGpuExecutionProvider failed: Dawn error creating device', '推理设备不可用'),
            ('Vulkan error: failed to allocate memory', '推理设备不可用'),
            ('HIP error: invalid device ordinal', '推理设备不可用'),
            ('VK_ERROR_DEVICE_LOST during dispatch', '显卡推理被中断'),
        ):
            info = error_recovery.explain(msg)
            self.assertEqual(info['action'], 'cpu', msg)
            self.assertEqual(info['title'], expected_title, msg)

    def test_04_rmvpe_fast_resample_accuracy_and_fallback(self):
        v2_root = str(APP / 'dependencies/vocal2midi2')
        if v2_root not in sys.path:
            sys.path.insert(0, v2_root)
        # Ensure we import the sandbox's own rmvpe_api rather than any host-cached inference module
        for mod_name in list(sys.modules):
            if mod_name == 'inference' or mod_name.startswith('inference.'):
                sys.modules.pop(mod_name, None)
        try:
            from inference.API.rmvpe_api import RmvpeTranscriber
        except ImportError:
            self.skipTest('librosa/scipy not installed in minimal CI runner')

        sr_in, sr_out = 44100, 16000
        t = np.arange(sr_in, dtype=np.float32) / sr_in
        sine = (0.5 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
        out = RmvpeTranscriber._fast_resample(sine, sr_in, sr_out)
        self.assertEqual(out.dtype, np.float32)
        self.assertTrue(np.isfinite(out).all())
        self.assertAlmostEqual(len(out), sr_out, delta=4)
        self.assertGreater(float(np.max(np.abs(out))), 0.45)

        # Force fallback path (no soxr) -> resample_poly must also produce valid 16 kHz output
        with mock.patch.dict(sys.modules, {'soxr': None}):
            fallback = RmvpeTranscriber._fast_resample(sine, sr_in, sr_out)
            self.assertEqual(fallback.dtype, np.float32)
            self.assertTrue(np.isfinite(fallback).all())
            self.assertAlmostEqual(len(fallback), sr_out, delta=4)

    def test_05_midi_bridge_qwen_reuse_vram_gate(self):
        """GPU Qwen3-ASR session reuse triggers only when len(voices) > 1, RAM >= 16 GiB, VRAM >= 12 GiB."""
        with vwa_io.resilient_tempdir(prefix='vwa-pra-qwen-') as tmp:
            job = Path(tmp) / 'job'
            (job / 'midi').mkdir(parents=True)
            req = {
                'language': 'zh',
                'midi_backend': 'v2',
                'recognize_lyrics': True,
                'tools': {'midi_device': 'dml', 'vocal2midi': str(APP / 'dependencies/vocal2midi')},
            }
            (job / 'request.json').write_text(json.dumps(req), encoding='utf-8')

            class FakeSession:
                closed = False

                def close(self):
                    self.closed = True

            fake_app_pkg = types.ModuleType('application')
            fake_pipe = types.ModuleType('application.pipeline')
            sessions_opened: list[FakeSession] = []

            def _open_sess():
                s = FakeSession()
                sessions_opened.append(s)
                return s

            fake_pipe.open_asr_session = _open_sess
            fake_app_pkg.pipeline = fake_pipe

            for vram_gib, expect_reused in ((8, False), (16, True)):
                sessions_opened.clear()
                fake_adapter = types.SimpleNamespace(dedicated_vram_bytes=vram_gib * (1 << 30))
                with mock.patch.dict(sys.modules, {'application': fake_app_pkg, 'application.pipeline': fake_pipe}), \
                     mock.patch.object(midi_bridge, 'resolve_tools', return_value={'vocal2midi': str(tmp)}), \
                     mock.patch.object(midi_bridge, 'configure', lambda _r: None), \
                     mock.patch.object(midi_bridge, 'run', lambda _j, _v, asr_session=None: None), \
                     mock.patch.object(runtime_devices, 'prepare_environment', lambda _t: None), \
                     mock.patch.object(runtime_devices, 'available_memory', return_value=24 * (1 << 30)), \
                     mock.patch.object(runtime_devices, 'preferred_gpu_adapter', return_value=fake_adapter):
                    midi_bridge.run_many(job, ['lead', 'backing'])
                report = json.loads((job / 'midi/worker-report.json').read_text(encoding='utf-8'))
                self.assertEqual(report['asr_reused'], expect_reused, f'vram_gib={vram_gib}')
                self.assertEqual(len(sessions_opened), 1 if expect_reused else 0)
                if expect_reused:
                    self.assertTrue(sessions_opened[0].closed)

    def test_06_preparation_preset_combobox_declares_key_value_role(self):
        """Guard PreparationPage.qml preset ComboBox fix (valueRole: 'key' + valueAt(index))."""
        qml = (APP / 'src/quick_ui/PreparationPage.qml').read_text(encoding='utf-8')
        idx = qml.find('objectName: "preparationPreset"')
        self.assertGreater(idx, 0)
        snippet = qml[idx:idx + 900]
        self.assertIn('valueRole: "key"', snippet)
        self.assertIn('applyPreparationPreset(valueAt(index))', snippet)


@unittest.skipUnless(_has_qt6(), 'PySide6 required for live PreparationPage preset ComboBox test')
class AmdGpuFoundationQt6Tests(unittest.TestCase):
    def test_07_live_preparation_preset_combobox_passes_key(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QCoreApplication, QEvent, QObject
        from PySide6.QtGui import QGuiApplication
        from quick_app import create_workspace

        app = QGuiApplication.instance() or QGuiApplication([])
        app.setQuitOnLastWindowClosed(False)
        engine, window, bridge, _ = create_workspace(app, viewport=(1280, 720), scale=100)
        try:
            bridge.navigate('processing')
            for _ in range(8):
                app.processEvents()
            combo = window.findChild(QObject, 'preparationPreset')
            self.assertIsNotNone(combo, 'preparationPreset ComboBox not found')
            self.assertEqual(combo.property('valueRole'), 'key')
            self.assertEqual(combo.property('textRole'), 'name')
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            bridge.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('PASS test_amd_gpu_foundation')


if __name__ == '__main__':
    main()
