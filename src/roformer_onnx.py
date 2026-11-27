"""Pure ONNX Runtime (WebGPU / CPU) inference engine for BS-RoFormer karaoke separation.

Strips complex STFT/iSTFT outside the ONNX graph using NumPy + SciPy so the
neural backbone runs directly on ``onnxruntime-webgpu`` across AMD, NVIDIA,
and Intel GPUs without requiring PyTorch or CUDA.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from common import cancelled
from multi_file_commit import FileTransactionError, commit_file_pair


DEFAULT_CHUNK_SIZE = 882000
DEFAULT_NUM_OVERLAP = 4
DEFAULT_STFT_N_FFT = 2048
DEFAULT_STFT_HOP = 512
DEFAULT_STFT_WIN = 2048
DEFAULT_DIM_FREQS_IN = 1025


class RoformerSpectrogram:
    """Periodic-Hann centered STFT/iSTFT matching PyTorch BS-RoFormer settings."""

    def __init__(
        self,
        n_fft: int = DEFAULT_STFT_N_FFT,
        hop_length: int = DEFAULT_STFT_HOP,
        win_length: int = DEFAULT_STFT_WIN,
        dim_freqs_in: int = DEFAULT_DIM_FREQS_IN,
    ):
        import numpy as np

        self.n_fft = int(n_fft)
        self.hop_length = int(hop_length)
        self.win_length = int(win_length)
        self.dim_freqs_in = int(dim_freqs_in)
        self.window = (
            0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(self.win_length, dtype=np.float64) / self.win_length)
        ).astype(np.float32)

    def forward(self, stereo_wave):
        """Convert ``(2, L)`` float32 waveform to ``(1, 4, F, T)`` real/imag tensor."""
        import numpy as np
        from scipy.fft import rfft

        pad = self.n_fft // 2
        padded = np.pad(stereo_wave, ((0, 0), (pad, pad)), mode='reflect')
        frames = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft, axis=-1)[:, ::self.hop_length]
        spectra = rfft(frames * self.window, n=self.n_fft, axis=-1).transpose(0, 2, 1)[:, :self.dim_freqs_in, :]
        # Channel order: [L_re, L_im, R_re, R_im]
        stacked = np.stack((spectra.real, spectra.imag), axis=1).reshape(1, 4, self.dim_freqs_in, spectra.shape[-1])
        return np.ascontiguousarray(stacked, dtype=np.float32)

    def inverse(self, spec_tensor, target_length: int):
        """Convert ``(1, 4, F, T)`` real/imag tensor back to ``(2, target_length)`` float32 waveform."""
        import numpy as np
        from scipy.fft import irfft

        _, channels, bins, count = spec_tensor.shape
        if channels != 4:
            raise ValueError('BS-RoFormer ONNX 模型必须输出 4 个实部/虚部频谱通道。')
        parts = spec_tensor.reshape(2, 2, bins, count)
        spectrum = parts[:, 0] + 1j * parts[:, 1]
        full_bins = self.n_fft // 2 + 1
        if bins < full_bins:
            spectrum = np.pad(spectrum, ((0, 0), (0, full_bins - bins), (0, 0)))
        frames = irfft(spectrum.transpose(0, 2, 1), n=self.n_fft, axis=-1) * self.window
        total = (count - 1) * self.hop_length + self.n_fft
        out = np.zeros((2, total), dtype=np.float32)
        weights = np.zeros(total, dtype=np.float32)
        win_sq = self.window ** 2
        for index in range(count):
            start = index * self.hop_length
            out[:, start:start + self.n_fft] += frames[:, index]
            weights[start:start + self.n_fft] += win_sq
        np.divide(out, weights, out=out, where=weights > 1e-8)
        trim = self.n_fft // 2
        trimmed = out[:, trim:trim + target_length]
        if trimmed.shape[1] < target_length:
            trimmed = np.pad(trimmed, ((0, 0), (0, target_length - trimmed.shape[1])))
        return np.ascontiguousarray(trimmed, dtype=np.float32)


def load_roformer_audio_config(config_path: Path | None) -> dict[str, int]:
    """Read chunk & STFT parameters from config.yaml (falling back to pinned defaults)."""
    params = {
        'chunk_size': DEFAULT_CHUNK_SIZE,
        'num_overlap': DEFAULT_NUM_OVERLAP,
        'stft_n_fft': DEFAULT_STFT_N_FFT,
        'stft_hop_length': DEFAULT_STFT_HOP,
        'stft_win_length': DEFAULT_STFT_WIN,
        'dim_freqs_in': DEFAULT_DIM_FREQS_IN,
    }
    if config_path is None or not Path(config_path).is_file():
        return params
    try:
        import yaml
    except ImportError:
        return params
    raw = yaml.unsafe_load(Path(config_path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        return params
    audio_cfg = raw.get('audio') or {}
    model_cfg = raw.get('model') or {}
    infer_cfg = raw.get('inference') or {}
    if isinstance(audio_cfg.get('chunk_size'), int) and audio_cfg['chunk_size'] > 0:
        params['chunk_size'] = audio_cfg['chunk_size']
    if isinstance(infer_cfg.get('num_overlap'), int) and infer_cfg['num_overlap'] >= 1:
        params['num_overlap'] = infer_cfg['num_overlap']
    for key in ('stft_n_fft', 'stft_hop_length', 'stft_win_length', 'dim_freqs_in'):
        value = model_cfg.get(key)
        if isinstance(value, int) and value > 0:
            params[key] = value
    return params


def _overlap_window(chunk_size: int, fade_size: int):
    import numpy as np

    fade_size = max(1, min(fade_size, chunk_size // 2))
    window = np.ones(chunk_size, dtype=np.float32)
    fade_in = np.linspace(0.0, 1.0, fade_size, dtype=np.float32)
    window[:fade_size] = fade_in
    window[-fade_size:] = fade_in[::-1]
    return window


def _resolve_onnx_model(environment: Path, spec: dict[str, Any]) -> Path:
    for item in spec.get('weights', ()):
        name = str(item.get('name') or '')
        if name.lower().endswith('.onnx'):
            candidate = environment / name
            if candidate.is_file():
                return candidate
    found = sorted(environment.glob('*.onnx'))
    if found:
        return found[0]
    raise FileNotFoundError(
        '未找到 BS-RoFormer ONNX 模型文件。请重新安装组件，或使用 tools/export_roformer_onnx.py 导出并导入。'
    )


def separate_vocals_onnx(
    source,
    directory,
    job,
    environment: Path,
    spec: dict[str, Any],
    device: str = 'auto',
    *,
    input_kind: str = 'vocals',
):
    """Run BS-RoFormer ONNX inference on WebGPU or CPU and commit lead/backing WAVs."""
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf

    from runtime_devices import create_session, session_threads

    model_path = _resolve_onnx_model(Path(environment), spec)
    config_path = Path(environment) / 'config.yaml'
    cfg = load_roformer_audio_config(config_path)

    request_file = Path(job) / 'request.json'
    request = json.loads(request_file.read_text(encoding='utf-8')) if request_file.is_file() else {}
    tools = dict(request.get('tools') or {})

    requested = 'cpu' if device == 'cpu' else 'webgpu'
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session_threads(options, default=6)

    effective_device, session = create_session(
        model_path, options, tools, requested, label='BS-RoFormer ONNX'
    )
    active_providers = session.get_providers()
    active_provider = active_providers[0] if active_providers else 'CPUExecutionProvider'
    if device == 'webgpu' and active_provider != 'WebGpuExecutionProvider':
        raise RuntimeError('当前环境无法使用 WebGPU，请在处理设置中选择“自动”或“CPU”。')

    audio, rate = sf.read(str(source), dtype='float32', always_2d=True)
    if rate != 44100 or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError('BS-RoFormer 需要有效的 44.1 kHz 双声道音频。')

    transform = RoformerSpectrogram(
        n_fft=cfg['stft_n_fft'],
        hop_length=cfg['stft_hop_length'],
        win_length=cfg['stft_win_length'],
        dim_freqs_in=cfg['dim_freqs_in'],
    )
    chunk_size = cfg['chunk_size']
    num_overlap = max(1, cfg['num_overlap'])
    step = max(1, chunk_size // num_overlap)
    fade_size = chunk_size // 10
    border = max(0, chunk_size - step)

    mix_t = audio.T  # (2, samples)
    total_samples = mix_t.shape[1]
    pad_mode = 'reflect' if total_samples > border > 0 else 'constant'
    padded = np.pad(mix_t, ((0, 0), (border, border)), mode=pad_mode) if border else mix_t
    padded_len = padded.shape[1]

    input_meta = session.get_inputs()[0]
    input_name = input_meta.name
    input_dtype = np.float16 if 'float16' in str(input_meta.type) else np.float32
    output_name = session.get_outputs()[0].name

    starts = list(range(0, padded_len, step))
    if starts and starts[-1] + chunk_size < padded_len:
        starts.append(padded_len - chunk_size)

    accum = np.zeros_like(padded, dtype=np.float32)
    counter = np.zeros(padded_len, dtype=np.float32)
    base_window = _overlap_window(chunk_size, fade_size)

    cancelled(job)
    started = time.monotonic()
    print(f'BS-RoFormer ONNX · 设备 {active_provider} ({effective_device})', flush=True)

    for index, start in enumerate(starts):
        cancelled(job)
        end = min(start + chunk_size, padded_len)
        piece = padded[:, start:end]
        valid = piece.shape[1]
        if valid < chunk_size:
            piece = np.pad(piece, ((0, 0), (0, chunk_size - valid)), mode='constant')
        spec_in = transform.forward(piece).astype(input_dtype, copy=False)
        spec_out = np.asarray(session.run([output_name], {input_name: spec_in})[0], dtype=np.float32)
        wave_out = transform.inverse(spec_out, chunk_size)[:, :valid]

        window = base_window[:valid].copy()
        if index == 0:
            window[:min(fade_size, valid)] = 1.0
        if end >= padded_len:
            window[max(0, valid - fade_size):] = 1.0

        accum[:, start:end] += wave_out * window
        counter[start:end] += window

        from progress_state import publish
        publish(job, 'separation', '主唱 / 和声 · BS-RoFormer', index + 1, len(starts))
        print(f'主唱／和声分离：{index + 1}/{len(starts)}', flush=True)

    del session
    cancelled(job)
    np.divide(accum, counter, out=accum, where=counter > 1e-8)
    lead = accum[:, border:border + total_samples].T if border else accum[:, :total_samples].T
    if lead.shape != audio.shape or not np.isfinite(lead).all():
        raise RuntimeError('BS-RoFormer 输出格式无效。')

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    pending = [directory / 'lead.pending.wav', directory / 'backing.pending.wav']
    max_error = 0.0
    preserve_pending = False
    try:
        with sf.SoundFile(str(pending[0]), 'w', samplerate=rate, channels=2, subtype='FLOAT') as a, \
             sf.SoundFile(str(pending[1]), 'w', samplerate=rate, channels=2, subtype='FLOAT') as b:
            for pos in range(0, len(audio), 65536):
                cancelled(job)
                primary = lead[pos:pos + 65536]
                mix = audio[pos:pos + 65536]
                backing = mix - primary
                a.write(primary)
                b.write(backing)
                max_error = max(max_error, float(np.abs(primary + backing - mix).max()))
        transaction = commit_file_pair(pending, [directory / 'lead.wav', directory / 'backing.wav'])
    except FileTransactionError as error:
        preserve_pending = error.preserve_staging
        raise
    finally:
        if not preserve_pending:
            for path in pending:
                path.unlink(missing_ok=True)

    model_entry = next(
        (item for item in spec.get('weights', ()) if str(item.get('name', '')).lower().endswith('.onnx')),
        {'name': model_path.name, 'sha256': ''},
    )
    return {
        'model': model_entry['name'],
        'sha256': model_entry.get('sha256', ''),
        'provider': active_provider,
        'method': 'original_mix_to_lead_and_remainder' if input_kind == 'mix' else 'total_vocals_then_karaoke',
        'input_kind': input_kind,
        'sample_rate': rate,
        'samples': len(audio),
        'timeline_offset_seconds': 0,
        'batch_size': 1,
        'chunk_size': chunk_size,
        'overlap_size': chunk_size - step,
        'tta': False,
        'output_normalization': False,
        'processing_seconds': round(time.monotonic() - started, 2),
        'sum_max_error': max_error,
        'transaction': transaction,
    }
