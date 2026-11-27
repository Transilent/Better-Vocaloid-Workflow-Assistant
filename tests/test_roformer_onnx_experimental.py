"""Unit and integration tests for isolated experimental BS-RoFormer ONNX (10s/20s FP16)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))

import component_operations as operations
import error_recovery
import gpu_backend
import optional_components
import preparation_commands
import roformer_onnx
import roformer_onnx_component as onnx_comp
import workflow
from workspace_controller import WorkspaceController


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RoformerOnnxExperimentalTest(unittest.TestCase):
    def test_manifest_exposes_only_10s_and_20s_fp16_and_experimental_flag(self):
        spec = onnx_comp.manifest()
        self.assertTrue(spec['experimental'])
        self.assertEqual(spec['precisions'], ['fp16_10s', 'fp16'])
        self.assertEqual(set(spec['weights'].keys()), {'fp16_10s', 'fp16'})
        self.assertNotIn('fp32', spec['weights'])
        for prec in ('fp16_10s', 'fp16'):
            item = spec['weights'][prec]
            self.assertEqual(len(item['sha256']), 64)
            self.assertGreater(item['bytes'], 100_000_000)
        self.assertEqual(len(spec['runtime_wheel']['sha256']), 64)
        self.assertIn('onnxruntime_webgpu-1.27.0', spec['runtime_wheel']['name'])

    def test_amd_vram_recommendation_matrix(self):
        def _run(adapters):
            with patch.object(gpu_backend.runtime_devices, 'adapters', return_value=adapters):
                return gpu_backend.recommend_roformer_onnx()

        # 1. Pure AMD 16 GiB (e.g. RX 9070 XT) -> recommend ONNX 20s FP16
        r16 = _run([SimpleNamespace(index=0, name='AMD Radeon RX 9070 XT', vendor_id=0x1002,
                                    dedicated_vram_bytes=16 * (1 << 30), shared_vram_bytes=16 * (1 << 30),
                                    is_software=False)])
        self.assertTrue(r16['recommend_onnx'])
        self.assertEqual(r16['recommended_precision'], 'fp16')

        # 2. Pure AMD 8 GiB (e.g. RX 7600) -> recommend ONNX 10s FP16
        r8 = _run([SimpleNamespace(index=0, name='AMD Radeon RX 7600', vendor_id=0x1002,
                                   dedicated_vram_bytes=8 * (1 << 30), shared_vram_bytes=16 * (1 << 30),
                                   is_software=False)])
        self.assertTrue(r8['recommend_onnx'])
        self.assertEqual(r8['recommended_precision'], 'fp16_10s')

        # 3. AMD APU (780M 512 MiB dedicated) -> recommend ONNX 10s FP16
        rapu = _run([SimpleNamespace(index=0, name='AMD Radeon 780M Graphics', vendor_id=0x1002,
                                     dedicated_vram_bytes=512 * (1 << 20), shared_vram_bytes=16 * (1 << 30),
                                     is_software=False)])
        self.assertTrue(rapu['recommend_onnx'])
        self.assertEqual(rapu['recommended_precision'], 'fp16_10s')

        # 4. Hybrid laptop with AMD iGPU + NVIDIA RTX 5070 dGPU -> do NOT recommend ONNX (prefer CUDA)
        r_hybrid = _run([
            SimpleNamespace(index=0, name='NVIDIA GeForce RTX 5070 Laptop GPU', vendor_id=0x10DE,
                            dedicated_vram_bytes=8 * (1 << 30), shared_vram_bytes=16 * (1 << 30),
                            is_software=False),
            SimpleNamespace(index=1, name='AMD Radeon(TM) 610M', vendor_id=0x1002,
                            dedicated_vram_bytes=512 * (1 << 20), shared_vram_bytes=8 * (1 << 30),
                            is_software=False),
        ])
        self.assertFalse(r_hybrid['recommend_onnx'])
        self.assertTrue(r_hybrid['has_nvidia_discrete'])
        self.assertIn('CUDA', r_hybrid['reason'])

        # 5. Pure NVIDIA -> do NOT recommend ONNX
        r_nv = _run([SimpleNamespace(index=0, name='NVIDIA GeForce RTX 4060', vendor_id=0x10DE,
                                     dedicated_vram_bytes=8 * (1 << 30), shared_vram_bytes=16 * (1 << 30),
                                     is_software=False)])
        self.assertFalse(r_nv['recommend_onnx'])

    def test_safe_yaml_loader_accepts_tuples_and_blocks_code_execution(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            ok_yaml = Path(tmp) / 'ok.yaml'
            ok_yaml.write_text(
                'audio:\n  chunk_size: 441000\nmodel:\n  freqs_per_bands: !!python/tuple [2, 4, 8]\n'
                '  stft_n_fft: 2048\n  stft_hop_length: 512\n  stft_win_length: 2048\n  dim_freqs_in: 1025\n'
                'inference:\n  num_overlap: 4\n',
                encoding='utf-8',
            )
            cfg = roformer_onnx.load_roformer_audio_config(ok_yaml)
            self.assertEqual(cfg['chunk_size'], 441000)
            self.assertEqual(cfg['stft_hop_length'], 512)

            evil_yaml = Path(tmp) / 'evil.yaml'
            evil_yaml.write_text('x: !!python/object/apply:os.system ["echo pwned"]\n', encoding='utf-8')
            with self.assertRaises(yaml.YAMLError):
                roformer_onnx.load_roformer_audio_config(evil_yaml)

    def test_isolated_install_coexistence_strict_precision_and_verify(self):
        w10 = b'onnx-10s-fp16-bytes'
        w20 = b'onnx-20s-fp16-bytes'
        cfg_bytes = b'audio:\n  chunk_size: 882000\n'
        whl_bytes = b'fake-ort-webgpu-wheel'
        spec = {
            'format': 1,
            'component': 'bs-roformer-karaoke-onnx',
            'version': '1',
            'experimental': True,
            'precisions': ['fp16_10s', 'fp16'],
            'weights': {
                'fp16_10s': {'name': 'm.fp16_10s.onnx', 'precision': 'fp16_10s', 'url': 'fixture://10s',
                             'bytes': len(w10), 'sha256': _sha256(w10)},
                'fp16': {'name': 'm.fp16.onnx', 'precision': 'fp16', 'url': 'fixture://20s',
                         'bytes': len(w20), 'sha256': _sha256(w20)},
            },
            'config': {'name': 'config.yaml', 'url': 'fixture://cfg', 'bytes': len(cfg_bytes), 'sha256': _sha256(cfg_bytes)},
            'runtime_wheel': {'name': 'onnxruntime_webgpu-1.27.0.whl', 'url': 'fixture://whl',
                              'bytes': len(whl_bytes), 'sha256': _sha256(whl_bytes)},
            'install_bytes': {'fp16_10s': 100, 'fp16': 100},
        }
        payload_by_name = {'m.fp16_10s.onnx': w10, 'm.fp16.onnx': w20, 'config.yaml': cfg_bytes,
                           'onnxruntime_webgpu-1.27.0.whl': whl_bytes}
        downloads = []

        def fake_dl(item, folder, progress, check=None, source='auto'):
            downloads.append(item['name'])
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            p = folder / item['name']
            p.write_bytes(payload_by_name[item['name']])
            return p

        def fake_extract(archive, directory, check=None):
            d = Path(directory)
            for rel, data in (
                ('onnxruntime/__init__.py', b'__version__ = "1.27.0"\n'),
                ('onnxruntime/capi/onnxruntime_pybind11_state.pyd', b'pyd'),
                ('onnxruntime/capi/onnxruntime.dll', b'dll'),
            ):
                p = d / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)

        class _Proc:
            returncode = 0

            def communicate(self, timeout=None):
                return b'COMPONENT_PROBE={"onnxruntime":"1.27.0","webgpu_available":true}\n', None

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            root = Path(tmp) / 'bs-roformer-onnx'
            manifest_path = Path(tmp) / 'roformer-onnx.json'
            manifest_path.write_text(json.dumps(spec), encoding='utf-8')
            with patch.object(onnx_comp, 'MANIFEST', manifest_path), \
                 patch.object(onnx_comp, 'manifest', return_value=spec), \
                 patch.object(onnx_comp, 'download', side_effect=fake_dl), \
                 patch.object(onnx_comp, 'extract_wheel', side_effect=fake_extract), \
                 patch.object(onnx_comp, 'load_config', return_value={'python': sys.executable}), \
                 patch.object(onnx_comp.subprocess, 'Popen', return_value=_Proc()), \
                 patch.object(onnx_comp.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**18)):
                st1 = onnx_comp.install('fp16_10s', progress=lambda *a: None, root=root)
                self.assertTrue(st1['ready'])
                self.assertEqual(st1['installed_precisions'], ['fp16_10s'])
                # Also install fp16 (20s): must coexist and reuse config.yaml
                downloads.clear()
                st2 = onnx_comp.install('fp16', progress=lambda *a: None, root=root)
                self.assertEqual(st2['installed_precisions'], ['fp16_10s', 'fp16'])
                self.assertNotIn('config.yaml', downloads)
                # Strict precision loading from installed.json (never silently picks 10s for 20s dir)
                m20, p20, _ = roformer_onnx.resolve_installed_model(root / 'current-fp16', spec)
                self.assertEqual((m20.name, p20), ('m.fp16.onnx', 'fp16'))
                m10, p10, _ = roformer_onnx.resolve_installed_model(root / 'current-fp16_10s', spec)
                self.assertEqual((m10.name, p10), ('m.fp16_10s.onnx', 'fp16_10s'))
                # Full SHA-256 verify passes
                ver = onnx_comp.full_verify(root, precision='fp16')
                self.assertTrue(ver['verified'])
                # PyTorch optional_components directory stays 100% isolated
                self.assertNotEqual(onnx_comp.COMPONENT, optional_components.COMPONENT)

    def test_default_disabled_and_workflow_controller_integration(self):
        # Default workflow MUST remain built-in 'dual', never experimental roformer_onnx
        self.assertEqual(workflow.DEFAULT_OPTIONS['voice_mode'], 'dual')
        draft = preparation_commands.PreparationDraft()
        self.assertEqual(draft.voice_mode, 'dual')
        self.assertEqual(draft.separator_model, 'dual')

        # Explicitly selecting roformer_onnx validates cleanly
        draft_onnx = draft.edit('separator_model', 'roformer_onnx').edit('roformer_device', 'webgpu')
        prefs = draft_onnx.preferences()
        self.assertEqual(prefs['voice_mode'], 'roformer_onnx')
        self.assertEqual(prefs['roformer_device'], 'webgpu')

        # Retry with CPU resets roformer_onnx device to cpu
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            req = Path(tmp) / 'request.json'
            req.write_text(json.dumps({'voice_mode': 'roformer_onnx', 'roformer_device': 'webgpu', 'tools': {}}), encoding='utf-8')
            error_recovery.retry_with_cpu(tmp)
            updated = json.loads(req.read_text(encoding='utf-8'))
            self.assertEqual(updated['roformer_device'], 'cpu')

        # WorkspaceController tracks onnx_precision and marks roformer_onnx missing until installed
        ctrl = WorkspaceController(initial_onnx_precision='fp16_10s')
        ctrl.draft = ctrl.draft.edit('separator_model', 'roformer_onnx').edit('recognize_lyrics', False)
        rows = [
            {'id': 'builtin', 'title': 'Karaoke 2', 'summary': '', 'status_text': '已就绪',
             'status': 'ready', 'model': 'dual', 'can_use': True, 'can_install': False, 'can_verify': False},
            {'id': 'roformer_onnx', 'title': 'BS-RoFormer ONNX（实验性）', 'summary': '', 'status_text': '未安装',
             'status': 'missing', 'health': {'installed_precisions': []},
             'model': 'roformer_onnx', 'can_use': False, 'can_install': True, 'can_verify': False},
        ]
        self.assertTrue(ctrl.set_catalog('cpu', rows, onnx_precision='fp16_10s'))
        self.assertEqual(ctrl.component_plan['missingIds'], ['roformer_onnx'])
        self.assertTrue(ctrl.set_onnx_precision('fp16'))
        self.assertEqual(ctrl.onnx_precision, 'fp16')


if __name__ == '__main__':
    unittest.main()
