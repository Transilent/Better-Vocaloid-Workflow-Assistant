"""Regression tests for SV2 v203+, RMVPE DirectML->CPU fallback, RoFormer dual-runtime coexistence, and GPU adapter labels."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))

import component_operations as operations
import gpu_backend
import optional_components as components
import svp_tuning
from workspace_controller import WorkspaceController


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_module(name: str, path: Path):
    import types

    added_stubs: list[str] = []
    if 'librosa' not in sys.modules:
        try:
            __import__('librosa')
        except ImportError:
            sys.modules['librosa'] = types.ModuleType('librosa')
            added_stubs.append('librosa')
    if 'scipy.signal' not in sys.modules:
        try:
            __import__('scipy.signal')
        except ImportError:
            scipy_mod = sys.modules.get('scipy')
            if scipy_mod is None:
                scipy_mod = types.ModuleType('scipy')
                sys.modules['scipy'] = scipy_mod
                added_stubs.append('scipy')
            sig_mod = types.ModuleType('scipy.signal')
            sig_mod.get_window = lambda *a, **kw: None
            sig_mod.resample_poly = lambda *a, **kw: None
            sys.modules['scipy.signal'] = sig_mod
            scipy_mod.signal = sig_mod
            added_stubs.append('scipy.signal')
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for mod_name in reversed(added_stubs):
            sys.modules.pop(mod_name, None)


class HotfixRegressionsTest(unittest.TestCase):
    def test_sv2_version_203_and_higher_are_writable_by_structure(self):
        project = {
            'version': 203,
            'time': {'tempo': [{'position': 0, 'bpm': 120.0}]},
            'library': [{
                'uuid': 'grp-1',
                'name': 'Vocals 1',
                'parameters': {'pitchDelta': {'mode': 'linear', 'points': []}},
                'notes': [{
                    'onset': 0,
                    'duration': 705600000,
                    'pitch': 60,
                    'lyrics': '啊',
                    'musicalType': 'singing',
                }],
            }],
            'tracks': [{
                'name': 'Lead',
                'mainRef': {'isInstrumental': False, 'groupID': 'main-1', 'blickOffset': 0},
                'mainGroup': {'uuid': 'main-1', 'notes': [], 'parameters': {}},
                'groups': [{'groupID': 'grp-1', 'blickOffset': 0, 'pitchOffset': 0,
                            'blickAbsoluteBegin': 0, 'blickAbsoluteEnd': -1, 'mute': False}],
            }],
        }
        layout = svp_tuning.Layout(project)
        self.assertTrue(layout.sv2)
        self.assertTrue(layout.tracks[0].writable)
        self.assertEqual(layout.tracks[0].issues, {})

        # Even newer minor SV2 versions (e.g. 215) remain writable if schema is valid.
        layout_future = svp_tuning.Layout({**project, 'version': 215})
        self.assertTrue(layout_future.tracks[0].writable)

    def test_rmvpe_v2_and_v1_fallback_to_cpu_on_dml_device_removed_or_unicode_error(self):
        for tag, rel in (
            ('_test_rmvpe_v2', 'dependencies/vocal2midi2/inference/API/rmvpe_api.py'),
            ('_test_rmvpe_v1', 'dependencies/vocal2midi/inference/API/rmvpe_api.py'),
        ):
            with self.subTest(module=tag):
                dep_root = str((APP / rel).parents[2])
                if dep_root not in sys.path:
                    sys.path.insert(0, dep_root)
                try:
                    mod = _load_module(tag, APP / rel)
                finally:
                    if dep_root in sys.path:
                        sys.path.remove(dep_root)
                cls = mod.RmvpeTranscriber

                # Simulate ORT pybind11 GBK UnicodeDecodeError on DML GRU 0x887A0005
                self.assertTrue(cls._is_gpu_device_fault(UnicodeDecodeError('utf-8', b'\xb6', 0, 1, 'invalid start byte')))
                self.assertTrue(cls._is_gpu_device_fault(RuntimeError('Non-zero status 887A0005 DmlExecutionProvider')))
                self.assertFalse(cls._is_gpu_device_fault(ValueError('unrelated shape mismatch')))

                calls = []

                class FakeSession:
                    def __init__(self, provider: str):
                        self.provider = provider

                    def get_inputs(self):
                        return [SimpleNamespace(name='input', shape=[1, 128, 512])]

                    def run(self, _out, feed):
                        calls.append(self.provider)
                        if self.provider == 'dml':
                            raise UnicodeDecodeError('utf-8', b'\xb6', 0, 1, 'invalid start byte')
                        return [np.zeros((1, 512, 360), dtype=np.float32)]

                inst = cls.__new__(cls)
                inst.provider_name = 'dml'
                inst.session = FakeSession('dml')
                inst.input_name = 'input'
                inst.fixed_batch_size = 1
                if tag == '_test_rmvpe_v2':
                    inst._create_session = lambda dev: ('cpu', FakeSession('cpu'))
                else:
                    inst._resolve_providers = lambda dev: ('cpu', ['CPUExecutionProvider'])
                    inst._create_session = lambda providers: FakeSession('cpu')

                out = inst._run_session(np.zeros((1, 128, 512), dtype=np.float32))
                self.assertEqual(calls, ['dml', 'cpu'])
                self.assertEqual(inst.provider_name, 'cpu')
                self.assertEqual(out.shape, (1, 512, 360))

                # Already on CPU: must not loop or swallow genuine CPU failure.
                inst.session.run = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('887A0005'))
                with self.assertRaises(RuntimeError):
                    inst._run_session(np.zeros((1, 128, 512), dtype=np.float32))

    def test_roformer_cuda_and_cpu_coexist_without_overwriting_or_redownloading(self):
        weight_bytes = b'roformer-weight-fixture'
        wheel_bytes = b'fake-torch-wheel'
        spec = {
            'component': 'bs-roformer-test',
            'weights': [{'name': 'model.ckpt', 'url': 'fixture://weight',
                         'bytes': len(weight_bytes), 'sha256': _digest(weight_bytes)}],
            'shared_wheels': [],
            'torch_wheels': {
                'cpu': {'name': 'torch-cpu.whl', 'url': 'fixture://cpu', 'bytes': len(wheel_bytes),
                        'sha256': _digest(wheel_bytes)},
                'cuda': {'name': 'torch-cuda.whl', 'url': 'fixture://cuda', 'bytes': len(wheel_bytes),
                         'sha256': _digest(wheel_bytes)},
            },
            'install_bytes': {'cpu': 100, 'cuda': 200},
        }
        downloaded_names = []

        def fake_download(item, folder, progress, check=None, source='auto'):
            downloaded_names.append(item['name'])
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / item['name']
            path.write_bytes(weight_bytes if item['name'].endswith('.ckpt') else wheel_bytes)
            return path

        def fake_extract(archive, directory, check=None):
            directory = Path(directory)
            for name, data in (
                ('torch/__init__.py', b'# torch\n'),
                ('torch/lib/runtime.dll', b'dll'),
                ('pymss/__init__.py', b'# pymss\n'),
                ('pymss_core/__init__.py', b'# pymss_core\n'),
            ):
                p = directory / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)

        def make_probe(cmd, **_kwargs):
            stage_name = Path(cmd[-1]).name
            is_cuda = 'cuda' in stage_name
            torch_ver = '2.7.1+cu128' if is_cuda else '2.7.1+cpu'

            class _Proc:
                returncode = 0

                def communicate(self, timeout=None):
                    payload = json.dumps({
                        'torch': torch_ver,
                        'cuda_available': is_cuda,
                        'device': 'cuda:0' if is_cuda else 'cpu',
                        'gpu': 'RTX Fixture' if is_cuda else None,
                    }).encode('utf-8')
                    return b'COMPONENT_PROBE=' + payload + b'\n', None

                def poll(self):
                    return 0

            return _Proc()

        with tempfile.TemporaryDirectory(dir=APP / 'work') as tmp:
            root = Path(tmp) / 'bs-roformer'
            manifest_path = Path(tmp) / 'roformer.json'
            manifest_path.write_text(json.dumps(spec), encoding='utf-8')

            with patch.object(components, 'MANIFEST', manifest_path), \
                 patch.object(components, 'manifest', return_value=spec), \
                 patch.object(components, 'download', side_effect=fake_download), \
                 patch.object(components, 'extract_wheel', side_effect=fake_extract), \
                 patch.object(components, 'load_config', return_value={'python': sys.executable}), \
                 patch.object(components.subprocess, 'Popen', side_effect=make_probe), \
                 patch.object(components.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**18)):
                # 1. Install CUDA
                st_cuda = components.install('cuda', progress=lambda *a: None, root=root)
                self.assertTrue(st_cuda['ready'])
                self.assertEqual(st_cuda['installed_runtimes'], ['cuda'])
                self.assertEqual(st_cuda['runtimes'], {'cuda': 'ready', 'cpu': 'missing'})
                self.assertEqual(downloaded_names, ['model.ckpt', 'torch-cuda.whl'])

                # 2. Also install CPU: must NOT overwrite CUDA and must reuse model.ckpt without redownloading!
                downloaded_names.clear()
                st_both = components.install('cpu', progress=lambda *a: None, root=root)
                self.assertTrue(st_both['ready'])
                self.assertEqual(st_both['installed_runtimes'], ['cuda', 'cpu'])
                self.assertEqual(st_both['runtimes'], {'cuda': 'ready', 'cpu': 'ready'})
                self.assertEqual(downloaded_names, ['torch-cpu.whl'])

                # 3. Switching back to CUDA must be an instant no-op (zero downloads!)
                downloaded_names.clear()
                st_again = components.install('cuda', progress=lambda *a: None, root=root)
                self.assertEqual(downloaded_names, [])
                self.assertEqual(st_again['installed_runtimes'], ['cuda', 'cpu'])

                # 4. Runtime directory resolution routes cuda->current-cuda, cpu->current-cpu, auto->current-cuda
                self.assertEqual(components.resolve_runtime_directory(root, 'cuda'), root / 'current-cuda')
                self.assertEqual(components.resolve_runtime_directory(root, 'cpu'), root / 'current-cpu')
                self.assertEqual(components.resolve_runtime_directory(root, 'auto'), root / 'current-cuda')

                # 5. Catalog rows report BOTH runtimes ready independently
                with patch.object(operations.optional_components, 'COMPONENT', root), \
                     patch.object(operations.optional_components, 'MANIFEST', manifest_path), \
                     patch.object(operations.optional_components, 'manifest', return_value=spec):
                    cuda_row = operations._roformer_row('cuda')
                    cpu_row = operations._roformer_row('cpu')
                    self.assertEqual(cuda_row['status'], 'ready')
                    self.assertEqual(cpu_row['status'], 'ready')
                    self.assertEqual(cuda_row['health']['runtimes'], {'cuda': 'ready', 'cpu': 'ready'})
                    self.assertEqual(cpu_row['health']['installed_runtimes'], ['cuda', 'cpu'])

    def test_workspace_controller_respects_initial_runtime_and_dual_installed_runtimes(self):
        ctrl = WorkspaceController(initial_runtime='cuda')
        self.assertEqual(ctrl.runtime, 'cuda')
        ctrl.draft = ctrl.draft.edit('separator_model', 'roformer').edit('roformer_device', 'cuda').edit('recognize_lyrics', False)
        rows = [
            {'id': 'builtin', 'title': 'Karaoke 2', 'summary': '', 'status_text': '已就绪',
             'status': 'ready', 'model': 'dual', 'can_use': True, 'can_install': False, 'can_verify': False},
            {'id': 'roformer', 'title': 'BS-RoFormer', 'summary': '', 'status_text': '已就绪',
             'status': 'ready', 'health': {'ready_runtime': 'cuda', 'installed_runtimes': ['cuda', 'cpu']},
             'model': 'roformer', 'can_use': True, 'can_install': True, 'can_verify': True},
        ]
        self.assertTrue(ctrl.set_catalog('cuda', rows))
        self.assertEqual(ctrl.component_plan['missingIds'], [])

    def test_gpu_adapter_choices_show_real_names_and_igpu_shared_memory(self):
        fake_adapters = [
            SimpleNamespace(index=0, name='Intel(R) Arc(TM) Graphics', vendor_id=0x8086,
                            dedicated_vram_bytes=128 * (1 << 20), shared_vram_bytes=16 * (1 << 30),
                            is_software=False),
            SimpleNamespace(index=1, name='NVIDIA GeForce RTX 4060 Laptop GPU', vendor_id=0x10DE,
                            dedicated_vram_bytes=8 * (1 << 30), shared_vram_bytes=16 * (1 << 30),
                            is_software=False),
            SimpleNamespace(index=2, name='Microsoft Basic Render Driver', vendor_id=0x1414,
                            dedicated_vram_bytes=0, shared_vram_bytes=8 * (1 << 30),
                            is_software=True),
        ]
        with patch.object(gpu_backend.runtime_devices, 'adapters', return_value=fake_adapters):
            choices = gpu_backend.adapter_choices()
        self.assertEqual(len(choices), 3)
        self.assertEqual(choices[0], {'label': '自动选择', 'value': 'auto'})
        self.assertIn('Intel(R) Arc(TM) Graphics', choices[1]['label'])
        self.assertIn('共享', choices[1]['label'])
        self.assertEqual(choices[1]['value'], 0)
        self.assertIn('RTX 4060 Laptop GPU', choices[2]['label'])
        self.assertIn('8.0 GiB', choices[2]['label'])
        self.assertEqual(choices[2]['value'], 1)


if __name__ == '__main__':
    unittest.main()
