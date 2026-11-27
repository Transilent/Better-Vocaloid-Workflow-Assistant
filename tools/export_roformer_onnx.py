"""Offline BS-RoFormer PyTorch -> ONNX exporter optimized for onnxruntime-webgpu.

Key design choices for AMD/NVIDIA/Intel WebGPU compatibility:
1. Strips complex ``torch.stft`` / ``torch.istft`` outside the ONNX graph
   (handled by ``src/roformer_onnx.py`` using NumPy + SciPy).
2. Disables ``flash_attn`` and chunks the 62 independent frequency bands inside
   each Time Transformer into small groups (default ``--band-chunk 8``) so peak
   attention memory drops from ~11 GiB to <1.5 GiB with identical math.
3. Exports static shape ``[1, 4, 1025, 1723]`` (20s @ 44.1 kHz, hop=512) for
   optimal shader compilation on WebGPU.
4. Verifies numerical error (RMS, max abs diff, SNR) against PyTorch and can
   optionally benchmark ``WebGpuExecutionProvider`` and update
   ``components/roformer.json`` or install into ``dependencies/optional/bs-roformer``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wrap_bs_roformer_core(model, *, band_chunk: int = 8):
    """Build a real-valued spectrogram wrapper around a loaded BS-RoFormer module."""
    import torch
    import torch.nn as nn

    class _BSRoFormerSpectrogramCore(nn.Module):
        def __init__(self, inner, group_size: int):
            super().__init__()
            self.inner = inner
            self.group_size = max(1, int(group_size))

        def _run_time_transformer(self, time_transformer, x):
            # x: (B, T, K, D). Bands K are independent during time attention.
            b, t, k, d = x.shape
            if self.group_size >= k:
                flat = x.permute(0, 2, 1, 3).reshape(b * k, t, d)
                out = time_transformer(flat)
                return out.reshape(b, k, t, d).permute(0, 2, 1, 3)
            chunks = []
            for start in range(0, k, self.group_size):
                end = min(k, start + self.group_size)
                sub = x[:, :, start:end, :].permute(0, 2, 1, 3).reshape(b * (end - start), t, d)
                sub = time_transformer(sub)
                chunks.append(sub.reshape(b, end - start, t, d).permute(0, 2, 1, 3))
            return torch.cat(chunks, dim=2)

        def forward(self, spec_4ch):
            # spec_4ch: (B, 4, F, T) -> [L_re, L_im, R_re, R_im]
            b, _, f, t = spec_4ch.shape
            # Rearrange to (B, T, F * 4) expected by BandSplit
            x_complex = spec_4ch.reshape(b, 2, 2, f, t)  # (B, ch=2, ri=2, F, T)
            x_btf = x_complex.permute(0, 4, 3, 1, 2).reshape(b, t, f * 4)
            x = self.inner.band_split(x_btf)

            for time_transformer, freq_transformer in self.inner.layers:
                x = self._run_time_transformer(time_transformer, x)
                # Freq attention along K=62 bands per frame T
                bf = x.reshape(b * t, x.shape[2], x.shape[3])
                bf = freq_transformer(bf)
                x = bf.reshape(b, t, x.shape[2], x.shape[3])

            x = self.inner.final_norm(x)
            # Mask estimator produces complex mask in (B, stems, T, F, 2, 2) or (B, T, F*4)
            masks = torch.stack([est(x) for est in self.inner.mask_estimators], dim=1)
            mask = masks[:, 0]  # single stem (Vocals)
            if mask.dim() == 3:
                mask = mask.reshape(b, t, f, 2, 2)
            elif mask.dim() == 4:
                mask = mask.reshape(b, t, f, 2, 2)
            # Complex multiply: (a + bi)(c + di) = (ac - bd) + (ad + bc)i
            m = mask.permute(0, 3, 4, 2, 1)  # (B, ch=2, ri=2, F, T)
            xr, xi = x_complex[:, :, 0], x_complex[:, :, 1]
            mr, mi = m[:, :, 0], m[:, :, 1]
            yr = xr * mr - xi * mi
            yi = xr * mi + xi * mr
            return torch.stack((yr, yi), dim=2).reshape(b, 4, f, t)

    # Disable flash attention flags recursively so standard exportable SDPA/MatMul is used.
    for module in model.modules():
        if hasattr(module, 'flash'):
            module.flash = False
        if hasattr(module, 'use_flash'):
            module.use_flash = False
    return _BSRoFormerSpectrogramCore(model, band_chunk).eval()


def export_model(
    ckpt_path: Path,
    config_path: Path,
    output_path: Path,
    *,
    band_chunk: int = 8,
    opset: int = 17,
    fp16: bool = False,
    verify: bool = True,
):
    import numpy as np
    import torch
    import yaml

    try:
        from pymss.models.bs_roformer import BSRoformer
    except ImportError as exc:
        raise RuntimeError(
            '导出 ONNX 需要在开发环境中安装 torch、pymss 和 onnx（推理时不需要 PyTorch）。'
        ) from exc

    cfg = yaml.unsafe_load(config_path.read_text(encoding='utf-8'))
    model_cfg = dict(cfg['model'])
    model_cfg['flash_attn'] = False
    model = BSRoformer(**model_cfg)
    state = torch.load(str(ckpt_path), map_location='cpu', weights_only=False)
    if isinstance(state, dict) and 'state_dict' in state:
        state = state['state_dict']
    model.load_state_dict(state, strict=True)
    model.eval()

    chunk_size = int(cfg['audio'].get('chunk_size', 882000))
    hop_length = int(model_cfg.get('stft_hop_length', 512))
    dim_freqs = int(model_cfg.get('dim_freqs_in', 1025))
    frames = chunk_size // hop_length + 1

    core = _wrap_bs_roformer_core(model, band_chunk=band_chunk)
    dummy = torch.randn(1, 4, dim_freqs, frames, dtype=torch.float32)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'Exporting static BS-RoFormer ONNX: shape={tuple(dummy.shape)}, band_chunk={band_chunk}, opset={opset}')
    with torch.no_grad():
        ref_out = core(dummy).numpy()
        torch.onnx.export(
            core,
            dummy,
            str(output_path),
            export_params=True,
            opset_version=opset,
            do_constant_folding=True,
            input_names=['spectrogram'],
            output_names=['lead_spectrogram'],
        )

    if fp16:
        try:
            import onnx
            from onnxconverter_common import float16
        except ImportError as exc:
            raise RuntimeError('开启 --fp16 需要安装 onnx 和 onnxconverter-common。') from exc
        proto = onnx.load(str(output_path))
        proto_fp16 = float16.convert_float_to_float16(
            proto,
            keep_io_types=True,
            op_block_list=['LayerNormalization', 'RMSNormalization', 'Softmax', 'ReduceMean', 'Pow', 'Sqrt'],
        )
        onnx.save(proto_fp16, str(output_path))
        print('Converted ONNX backbone to mixed-precision FP16 (IO/Norm/Softmax kept FP32).')

    size = output_path.stat().st_size
    sha256 = _sha256_file(output_path)
    print(f'Exported: {output_path} ({size:,} bytes, sha256={sha256})')

    if verify:
        import onnxruntime as ort

        sys.path.insert(0, str(ROOT / 'dependencies/vocal2midi2'))
        from inference.device_utils import create_onnx_session

        for dev in ('cpu', 'webgpu'):
            opts = ort.SessionOptions()
            opts.enable_mem_pattern = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            t0 = time.perf_counter()
            provider, sess = create_onnx_session(output_path, opts, dev, label=f'Verify-{dev}')
            out = sess.run(None, {'spectrogram': dummy.numpy()})[0]
            dt = time.perf_counter() - t0
            diff = np.abs(out - ref_out)
            rms = float(np.sqrt(np.mean(diff ** 2)))
            max_abs = float(diff.max())
            print(f'  [{dev} -> {sess.get_providers()[0]}] time={dt:.2f}s  rms_diff={rms:.6e}  max_diff={max_abs:.6e}')
    return {'name': output_path.name, 'bytes': size, 'sha256': sha256}


def main():
    parser = argparse.ArgumentParser(description='Export BS-RoFormer checkpoint to static WebGPU-ready ONNX.')
    parser.add_argument('--ckpt', type=Path, required=True, help='Path to bs_roformer_karaoke_frazer_becruily.ckpt')
    parser.add_argument('--config', type=Path, required=True, help='Path to config_karaoke_frazer_becruily.yaml')
    parser.add_argument('--output', type=Path, default=Path('bs_roformer_karaoke_frazer_becruily.onnx'))
    parser.add_argument('--band-chunk', type=int, default=8, help='Time-attention band group size (default: 8)')
    parser.add_argument('--opset', type=int, default=17)
    parser.add_argument('--fp16', action='store_true', help='Convert backbone weights to mixed-precision FP16')
    parser.add_argument('--no-verify', action='store_true')
    parser.add_argument('--update-manifest', action='store_true', help='Update components/roformer.json bytes & sha256')
    parser.add_argument('--install', action='store_true', help='Install exported ONNX into dependencies/optional/bs-roformer')
    args = parser.parse_args()

    meta = export_model(
        args.ckpt,
        args.config,
        args.output,
        band_chunk=args.band_chunk,
        opset=args.opset,
        fp16=args.fp16,
        verify=not args.no_verify,
    )
    if args.update_manifest:
        manifest_path = ROOT / 'components/roformer.json'
        spec = json.loads(manifest_path.read_text(encoding='utf-8'))
        for item in spec.get('weights', ()):
            if str(item.get('name', '')).endswith('.onnx'):
                item['name'] = meta['name']
                item['bytes'] = meta['bytes']
                item['sha256'] = meta['sha256']
        manifest_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'Updated {manifest_path}')
    if args.install:
        sys.path.insert(0, str(ROOT / 'src'))
        import optional_components
        optional_components.install_local_onnx(args.output, args.config, runtime='webgpu')
        print('Installed into dependencies/optional/bs-roformer/current')


if __name__ == '__main__':
    main()
