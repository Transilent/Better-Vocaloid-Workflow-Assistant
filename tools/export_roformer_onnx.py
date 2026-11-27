"""Offline BS-RoFormer PyTorch -> ONNX exporter optimized for onnxruntime-webgpu.

Key design choices for AMD/NVIDIA/Intel WebGPU compatibility:
1. Strips complex ``torch.stft`` / ``torch.istft`` outside the ONNX graph
   (handled by ``src/roformer_onnx.py`` using NumPy + SciPy).
2. Disables ``flash_attn``, replaces ``einsum`` with pure ``matmul``, disables
   in-place grouped caches in ``BandSplit`` / ``MaskEstimator``, and chunks the
   62 independent frequency bands inside each Time Transformer into small groups
   (default ``--band-chunk 8``) so peak attention memory drops from ~11 GiB to
   <1.5 GiB with identical math.
3. Exports static shape ``[1, 4, 1025, 1723]`` (20s @ 44.1 kHz, hop=512) for
   optimal shader compilation on WebGPU.
4. Verifies numerical error (RMS, max abs diff, SNR) against PyTorch and can
   benchmark ``WebGpuExecutionProvider``, update ``components/roformer.json``,
   and install directly into ``dependencies/optional/bs-roformer/current``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_rotary_emb_real(cos, sin, t):
    """Pure real-valued RoPE matching pymss_core apply_rotary_emb_fast without aten::complex."""
    import torch

    t_pair = t.reshape(*t.shape[:-1], -1, 2)
    t_even = t_pair[..., 0]
    t_odd = t_pair[..., 1]
    out_even = t_even * cos - t_odd * sin
    out_odd = t_odd * cos + t_even * sin
    return torch.stack((out_even, out_odd), dim=-1).reshape_as(t)


def _tree_cat(tensors, *, dim: int, max_inputs: int = 6):
    """Hierarchical concatenation keeping each Concat node <= max_inputs+1 WebGPU storage buffers."""
    import torch

    items = list(tensors)
    while len(items) > max_inputs:
        items = [
            torch.cat(items[i:i + max_inputs], dim=dim)
            for i in range(0, len(items), max_inputs)
        ]
    return items[0] if len(items) == 1 else torch.cat(items, dim=dim)


def _patch_for_clean_onnx(model, *, fp16: bool = False) -> None:
    """Switch pymss_core BSRoformer to pure exportable WebGPU-safe ops without altering math."""
    import torch
    import torch.nn.functional as F

    try:
        from pymss_core.modules.bs_roformer import transformer as pymss_transformer
        pymss_transformer.apply_rotary_emb_fast = _apply_rotary_emb_real
    except ImportError:
        pass

    def _clean_band_split_forward(self, x):
        outs = []
        offset = 0
        for dim_in, to_feature in zip(self.dim_inputs, self.to_features):
            part = x[..., offset:offset + dim_in]
            offset += dim_in
            outs.append(to_feature(part).unsqueeze(-2))
        return _tree_cat(outs, dim=-2, max_inputs=6)

    def _clean_mask_estimator_forward(self, x):
        outs = [
            mlp(x[:, :, i, :])
            for i, mlp in enumerate(self.to_freqs)
        ]
        return _tree_cat(outs, dim=-1, max_inputs=6)

    if hasattr(model, 'band_split'):
        model.band_split.use_grouped_forward = False
        if hasattr(model.band_split, 'clear_runtime_cache'):
            model.band_split.clear_runtime_cache()
        model.band_split.forward = types.MethodType(_clean_band_split_forward, model.band_split)
    for est in getattr(model, 'mask_estimators', ()):
        est.use_grouped_forward = False
        if hasattr(est, 'clear_runtime_cache'):
            est.clear_runtime_cache()
        est.forward = types.MethodType(_clean_mask_estimator_forward, est)

    def _clean_attend_forward(self, q, k, v):
        scale = self.scale if self.scale is not None else (q.shape[-1] ** -0.5)
        sim = torch.matmul(q, k.transpose(-2, -1)) * scale
        if sim.dtype == torch.float16:
            attn = torch.softmax(sim.float(), dim=-1).to(q.dtype)
        else:
            attn = torch.softmax(sim, dim=-1)
        return torch.matmul(attn, v)

    def _clean_rmsnorm_forward(self, x):
        out = F.normalize(x.float(), dim=-1) * self.scale * self.gamma.float()
        return out.to(x.dtype)

    def _clean_glu_forward(self, x):
        dim = self.dim
        half = int(x.shape[dim]) // 2
        a, b = torch.split(x, half, dim=dim)
        return a * torch.sigmoid(b)

    for module in model.modules():
        if hasattr(module, 'flash'):
            module.flash = False
        if hasattr(module, 'use_flash'):
            module.use_flash = False
        if type(module).__name__ == 'Attend':
            module.forward = types.MethodType(_clean_attend_forward, module)
        if type(module).__name__ == 'RMSNorm':
            module.forward = types.MethodType(_clean_rmsnorm_forward, module)
        if isinstance(module, torch.nn.GLU):
            module.forward = types.MethodType(_clean_glu_forward, module)
        if type(module).__name__ == 'RotaryEmbedding':
            if hasattr(module, 'cache'):
                module.cache.clear()
            if hasattr(module, '_pymss_cos_sin_cache'):
                module._pymss_cos_sin_cache.clear()
    if fp16:
        model.half()
        # Keep RotaryEmbedding base frequencies in fp32 for accurate sin/cos before casting.
        for module in model.modules():
            if type(module).__name__ == 'RotaryEmbedding' and hasattr(module, 'freqs'):
                module.freqs.data = module.freqs.data.float()


def _wrap_bs_roformer_core(model, *, band_chunk: int = 8, fp16: bool = False):
    """Build a real-valued spectrogram wrapper matching pymss_core's exact stft_repr layout."""
    import torch
    import torch.nn as nn

    _patch_for_clean_onnx(model, fp16=fp16)

    class _BSRoFormerSpectrogramCore(nn.Module):
        def __init__(self, inner, group_size: int, use_fp16: bool):
            super().__init__()
            self.inner = inner
            self.group_size = max(1, int(group_size))
            self.use_fp16 = bool(use_fp16)

        def _run_time_transformer(self, time_transformer, x):
            # x: (B, T, K, D). The K=62 frequency bands are independent during time attention.
            b, t, k, d = x.shape
            k_int = int(k)
            if self.group_size >= k_int:
                flat = x.permute(0, 2, 1, 3).reshape(b * k_int, t, d)
                out = time_transformer(flat)
                return out.reshape(b, k_int, t, d).permute(0, 2, 1, 3)
            chunks = []
            for start in range(0, k_int, self.group_size):
                end = min(k_int, start + self.group_size)
                sub = x[:, :, start:end, :].permute(0, 2, 1, 3).reshape(b * (end - start), t, d)
                sub = time_transformer(sub)
                chunks.append(sub.reshape(b, end - start, t, d).permute(0, 2, 1, 3))
            return _tree_cat(chunks, dim=2, max_inputs=6)

        def forward(self, spec_4ch):
            # spec_4ch: (B, 4, F, T) float32 with channels [L_re, L_im, R_re, R_im]
            b, _, f, t = spec_4ch.shape
            # Match pymss_core stft_roformer layout: stft_repr = (B, F*2, T, 2)
            x_scft = spec_4ch.reshape(b, 2, 2, f, t)  # (B, s=2, c=2, F, T)
            stft_repr = x_scft.permute(0, 3, 1, 4, 2).reshape(b, f * 2, t, 2)
            # BandSplit input: (B, T, F*2*2)
            band_in = stft_repr.permute(0, 2, 1, 3).reshape(b, t, f * 4)
            if self.use_fp16:
                band_in = band_in.half()
            x = self.inner.band_split(band_in)

            residual_store = [] if getattr(self.inner, 'skip_connection', False) else None
            for time_transformer, freq_transformer in self.inner.layers:
                if residual_store is not None:
                    for residual in residual_store:
                        x = x + residual
                x = self._run_time_transformer(time_transformer, x)
                bf = x.reshape(b * t, x.shape[2], x.shape[3])
                bf = freq_transformer(bf)
                x = bf.reshape(b, t, x.shape[2], x.shape[3])
                if residual_store is not None:
                    residual_store.append(x)

            x = self.inner.final_norm(x)
            # MaskEstimator output: (B, T, F*4) -> (B, F*2, T, 2)
            mask_flat = self.inner.mask_estimators[0](x)
            if self.use_fp16:
                mask_flat = mask_flat.float()
            mask = mask_flat.reshape(b, t, f * 2, 2).permute(0, 2, 1, 3)

            # Complex multiply in float32: (xr + i*xi) * (mr + i*mi)
            xr, xi = stft_repr[..., 0], stft_repr[..., 1]
            mr, mi = mask[..., 0], mask[..., 1]
            yr = xr * mr - xi * mi
            yi = xr * mi + xi * mr
            out_stft = torch.stack((yr, yi), dim=-1)  # (B, F*2, T, 2)
            # Convert back to (B, 4, F, T) -> [L_re, L_im, R_re, R_im]
            out_4ch = out_stft.reshape(b, f, 2, t, 2).permute(0, 2, 4, 1, 3).reshape(b, 4, f, t)
            if getattr(self.inner, 'zero_dc', False):
                dc_mask = torch.ones((1, 1, f, 1), dtype=out_4ch.dtype, device=out_4ch.device)
                dc_mask[:, :, 0, :] = 0.0
                out_4ch = out_4ch * dc_mask
            return out_4ch

    return _BSRoFormerSpectrogramCore(model, band_chunk, fp16).eval()


def _import_bs_roformer():
    try:
        from pymss_core.modules.bs_roformer.bs_roformer import BSRoformer
        return BSRoformer
    except ImportError:
        from pymss.models.bs_roformer import BSRoformer
        return BSRoformer


def export_model(
    ckpt_path: Path,
    config_path: Path,
    output_path: Path,
    *,
    band_chunk: int = 8,
    chunk_size: int | None = None,
    opset: int = 18,
    fp16: bool = False,
    verify: bool = True,
):
    import numpy as np
    import torch
    import yaml

    BSRoformer = _import_bs_roformer()

    cfg = yaml.unsafe_load(config_path.read_text(encoding='utf-8'))
    model_cfg = dict(cfg['model'])
    model_cfg['flash_attn'] = False
    model = BSRoformer(**model_cfg)
    state = torch.load(str(ckpt_path), map_location='cpu', weights_only=False)
    if isinstance(state, dict) and 'state_dict' in state:
        state = state['state_dict']
    model.load_state_dict(state, strict=True)
    model.eval()

    effective_chunk_size = int(chunk_size) if chunk_size is not None else int(cfg['audio'].get('chunk_size', 882000))
    hop_length = int(model_cfg.get('stft_hop_length', 512))
    dim_freqs = int(model_cfg.get('dim_freqs_in', 1025))
    frames = effective_chunk_size // hop_length + 1

    core = _wrap_bs_roformer_core(model, band_chunk=band_chunk, fp16=fp16)
    dummy = torch.randn(1, 4, dim_freqs, frames, dtype=torch.float32) * 0.1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove any stale sidecar data file from previous exports
    sidecar = Path(f'{output_path}.data')
    if sidecar.exists():
        sidecar.unlink()

    precision = 'fp16 (mixed)' if fp16 else 'fp32'
    print(f'Exporting static BS-RoFormer ONNX ({precision}): shape={tuple(dummy.shape)}, band_chunk={band_chunk}, opset={opset}', flush=True)
    t_exp = time.perf_counter()
    with torch.no_grad():
        onnx_program = torch.onnx.export(
            core,
            (dummy,),
            opset_version=opset,
            input_names=['spectrogram'],
            output_names=['lead_spectrogram'],
            dynamo=True,
            optimize=True,
        )
        onnx_program.save(str(output_path), external_data=False)
    print(f'ONNX export finished in {time.perf_counter() - t_exp:.1f}s', flush=True)

    size = output_path.stat().st_size
    sha256 = _sha256_file(output_path)
    print(f'Exported: {output_path} ({size:,} bytes, {size / 1048576:.1f} MiB, sha256={sha256})', flush=True)

    if verify:
        import onnxruntime as ort

        sys.path.insert(0, str(ROOT / 'dependencies/vocal2midi2'))
        from inference.device_utils import create_onnx_session

        print('Computing PyTorch CPU reference...', flush=True)
        _patch_for_clean_onnx(model, fp16=fp16)
        with torch.no_grad():
            ref_out = core(dummy).detach().cpu().numpy()

        for dev in ('webgpu', 'cpu'):
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
            ref_rms = float(np.sqrt(np.mean(ref_out ** 2)))
            snr = 20.0 * np.log10(max(ref_rms, 1e-12) / max(rms, 1e-12))
            print(
                f'  [{dev} -> {sess.get_providers()[0]}] time={dt:.2f}s  '
                f'rms_diff={rms:.6e}  max_diff={max_abs:.6e}  SNR={snr:.1f} dB',
                flush=True,
            )
    return {'name': output_path.name, 'bytes': size, 'sha256': sha256}


def main():
    parser = argparse.ArgumentParser(description='Export BS-RoFormer checkpoint to static WebGPU-ready ONNX.')
    parser.add_argument('--ckpt', type=Path, required=True, help='Path to bs_roformer_karaoke_frazer_becruily.ckpt')
    parser.add_argument('--config', type=Path, required=True, help='Path to config_karaoke_frazer_becruily.yaml')
    parser.add_argument('--output', type=Path, default=Path('bs_roformer_karaoke_frazer_becruily.onnx'))
    parser.add_argument('--band-chunk', type=int, default=8, help='Time-attention band group size (default: 8)')
    parser.add_argument('--chunk-size', type=int, default=None, help='Override static waveform chunk_size in samples (e.g. 441000 for 10s low-VRAM)')
    parser.add_argument('--opset', type=int, default=18)
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
        chunk_size=args.chunk_size,
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
        print(f'Updated {manifest_path}', flush=True)
    if args.install:
        sys.path.insert(0, str(ROOT))
        import optional_components
        optional_components.install_local_onnx(args.output, args.config, runtime='webgpu')
        print('Installed into dependencies/optional/bs-roformer/current', flush=True)


if __name__ == '__main__':
    main()
