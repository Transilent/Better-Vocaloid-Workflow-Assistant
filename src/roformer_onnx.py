"""Isolated BS-RoFormer ONNX (WebGPU / CPU) experimental karaoke separator.

Credits: ONNX export and cross-vendor WebGPU validation contributed by AU-ki.
Runs in an isolated child process so ``onnxruntime-webgpu==1.27.0`` never
collides with the main process's ``onnxruntime-directml`` installation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, cancelled, file_hash, load_config
from multi_file_commit import FileTransactionError, commit_file_pair


DEFAULT_CHUNK_SIZE = 882000
DEFAULT_NUM_OVERLAP = 4
DEFAULT_STFT_N_FFT = 2048
DEFAULT_STFT_HOP = 512
DEFAULT_STFT_WIN = 2048
DEFAULT_DIM_FREQS_IN = 1025


class _SafeTupleLoader(yaml.SafeLoader):
    """Safe YAML loader supporting only ``!!python/tuple`` used by BS-RoFormer configs."""


_SafeTupleLoader.add_constructor(
    'tag:yaml.org,2002:python/tuple',
    lambda loader, node: tuple(loader.construct_sequence(node)),
)


def load_roformer_audio_config(config_path: Path) -> dict[str, int]:
    """Read STFT and overlap settings using a restricted safe YAML loader."""
    raw = yaml.load(Path(config_path).read_text(encoding='utf-8'), Loader=_SafeTupleLoader) or {}
    if not isinstance(raw, dict):
        raise ValueError('BS-RoFormer 配置文件格式无效。')
    audio = raw.get('audio') or {}
    model = raw.get('model') or {}
    inference = raw.get('inference') or {}
    return {
        'chunk_size': int(audio.get('chunk_size', DEFAULT_CHUNK_SIZE)),
        'num_overlap': max(1, int(inference.get('num_overlap', DEFAULT_NUM_OVERLAP))),
        'stft_n_fft': int(model.get('stft_n_fft', DEFAULT_STFT_N_FFT)),
        'stft_hop_length': int(model.get('stft_hop_length', DEFAULT_STFT_HOP)),
        'stft_win_length': int(model.get('stft_win_length', DEFAULT_STFT_WIN)),
        'dim_freqs_in': int(model.get('dim_freqs_in', DEFAULT_DIM_FREQS_IN)),
    }


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
        import numpy as np
        from scipy.fft import rfft

        pad = self.n_fft // 2
        padded = np.pad(stereo_wave, ((0, 0), (pad, pad)), mode='reflect')
        frames = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft, axis=-1)[:, ::self.hop_length]
        spectra = rfft(frames * self.window, n=self.n_fft, axis=-1).transpose(0, 2, 1)[:, :self.dim_freqs_in, :]
        stacked = np.stack((spectra.real, spectra.imag), axis=1).reshape(1, 4, self.dim_freqs_in, spectra.shape[-1])
        return np.ascontiguousarray(stacked, dtype=np.float32)

    def inverse(self, spec_tensor, target_length: int):
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
        window_sq = self.window ** 2
        for i in range(count):
            start = i * self.hop_length
            out[:, start:start + self.n_fft] += frames[:, i]
            weights[start:start + self.n_fft] += window_sq
        np.divide(out, weights, out=out, where=weights > 1e-8)
        pad = self.n_fft // 2
        trimmed = out[:, pad:pad + target_length]
        if trimmed.shape[1] < target_length:
            trimmed = np.pad(trimmed, ((0, 0), (0, target_length - trimmed.shape[1])))
        return trimmed


def _overlap_window(chunk_size: int, fade_size: int):
    import numpy as np

    window = np.ones(chunk_size, dtype=np.float32)
    if fade_size > 0 and fade_size * 2 <= chunk_size:
        fade = np.linspace(0.0, 1.0, fade_size, dtype=np.float32)
        window[:fade_size] = fade
        window[-fade_size:] = fade[::-1]
    return window


def resolve_installed_model(environment: Path, spec: dict[str, Any]) -> tuple[Path, str, str]:
    """Strictly load the ONNX weight matching ``installed.json['precision']``."""
    receipt_path = Path(environment) / 'installed.json'
    if not receipt_path.is_file():
        raise ValueError('BS-RoFormer ONNX 缺少安装收据，请重新安装实验组件。')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    precision = receipt.get('precision')
    weights = spec.get('weights') if isinstance(spec, dict) else None
    if not isinstance(weights, dict) or precision not in weights:
        raise ValueError('BS-RoFormer ONNX 安装收据中的精度无效。')
    item = weights[precision]
    model_path = Path(environment) / item['name']
    if not model_path.is_file():
        raise FileNotFoundError(f"缺少 ONNX 模型文件：{item['name']}")
    expected_sha = str(item.get('sha256') or '').strip().lower()
    if expected_sha and file_hash(model_path) != expected_sha:
        raise ValueError('BS-RoFormer ONNX 权重 SHA-256 校验失败，请重新安装实验组件。')
    cfg_item = spec.get('config') or {}
    cfg_path = Path(environment) / cfg_item.get('name', 'config.yaml')
    if cfg_item.get('sha256') and (not cfg_path.is_file() or file_hash(cfg_path) != cfg_item['sha256']):
        raise ValueError('BS-RoFormer ONNX 配置文件 SHA-256 校验失败，请重新安装实验组件。')
    return model_path, precision, expected_sha


def _worker_separate(source: Path, directory: Path, job: Path, environment: Path,
                     device: str, input_kind: str) -> dict[str, Any]:
    """Run inside the isolated child process with ``environment/packages`` first on ``sys.path``."""
    sys.path.insert(0, str(environment / 'packages'))
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    from roformer_onnx_component import manifest
    from runtime_devices import create_session, session_threads

    spec = manifest()
    model_path, precision, expected_sha = resolve_installed_model(environment, spec)
    cfg = load_roformer_audio_config(environment / 'config.yaml')

    request_file = job / 'request.json'
    request = json.loads(request_file.read_text(encoding='utf-8')) if request_file.is_file() else {}
    tools = dict(request.get('tools') or {})

    requested = 'cpu' if device == 'cpu' else 'webgpu'
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session_threads(options, default=6)

    _, session = create_session(model_path, options, tools, requested, label='BS-RoFormer ONNX')
    active_providers = session.get_providers()
    active_provider = active_providers[0] if active_providers else 'CPUExecutionProvider'
    if device == 'webgpu' and active_provider != 'WebGpuExecutionProvider':
        raise RuntimeError('当前环境无法使用 WebGPU，请改选“自动”或“CPU”。')

    audio, rate = sf.read(str(source), dtype='float32', always_2d=True)
    if rate != 44100 or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError('BS-RoFormer ONNX 需要有效的 44.1 kHz 双声道音频。')

    transform = RoformerSpectrogram(
        n_fft=cfg['stft_n_fft'],
        hop_length=cfg['stft_hop_length'],
        win_length=cfg['stft_win_length'],
        dim_freqs_in=cfg['dim_freqs_in'],
    )
    input_meta = session.get_inputs()[0]
    input_name = input_meta.name
    input_dtype = np.float16 if 'float16' in str(input_meta.type) else np.float32
    output_name = session.get_outputs()[0].name

    chunk_size = cfg['chunk_size']
    hop = cfg['stft_hop_length']
    model_frames = input_meta.shape[3] if len(input_meta.shape) == 4 and isinstance(input_meta.shape[3], int) else None
    if model_frames and model_frames > 1 and (chunk_size // hop + 1) != model_frames:
        chunk_size = (model_frames - 1) * hop
    num_overlap = cfg['num_overlap']
    step = max(1, chunk_size // num_overlap)
    fade_size = chunk_size // 10
    border = chunk_size - step

    mix_t = audio.T.astype(np.float32, copy=False)
    orig_len = mix_t.shape[1]
    pad_mode = 'reflect' if orig_len > border else 'constant'
    padded = np.pad(mix_t, ((0, 0), (border, border)), mode=pad_mode) if border else mix_t
    padded_len = padded.shape[1]

    starts = list(range(0, padded_len, step))
    if starts and starts[-1] + chunk_size < padded_len:
        starts.append(padded_len - chunk_size)

    result = np.zeros((2, padded_len), dtype=np.float32)
    counter = np.zeros((2, padded_len), dtype=np.float32)
    base_window = _overlap_window(chunk_size, fade_size)

    started = time.monotonic()
    total_chunks = len(starts)
    print(f'BS-RoFormer ONNX（实验性 · {precision}）· 设备 {active_provider}', flush=True)
    for idx, start in enumerate(starts):
        cancelled(job)
        segment = padded[:, start:start + chunk_size]
        seg_len = segment.shape[1]
        if seg_len < chunk_size:
            mode = 'reflect' if seg_len > (chunk_size - seg_len) else 'constant'
            segment = np.pad(segment, ((0, 0), (0, chunk_size - seg_len)), mode=mode)
        spec_in = transform.forward(segment)
        if spec_in.dtype != input_dtype:
            spec_in = spec_in.astype(input_dtype, copy=False)
        spec_out = session.run([output_name], {input_name: spec_in})[0]
        if spec_out.dtype != np.float32:
            spec_out = spec_out.astype(np.float32, copy=False)
        wave_out = transform.inverse(spec_out, chunk_size)[:, :seg_len]
        win = base_window.copy()
        if start == 0:
            win[:fade_size] = 1.0
        if start + chunk_size >= padded_len:
            win[-fade_size:] = 1.0
        win = win[:seg_len]
        result[:, start:start + seg_len] += wave_out * win
        counter[:, start:start + seg_len] += win
        from progress_state import publish
        publish(job, 'separation', '主唱 / 和声 · BS-RoFormer ONNX（实验性）', idx + 1, total_chunks)
        print(f'主唱／和声分离：{idx + 1}/{total_chunks}', flush=True)

    np.divide(result, counter, out=result, where=counter > 1e-8)
    lead = result[:, border:border + orig_len].T.astype(np.float32, copy=False)
    cancelled(job)
    if lead.shape != audio.shape or not np.isfinite(lead).all():
        raise RuntimeError('BS-RoFormer ONNX 输出格式无效。')

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

    return {
        'model': model_path.name,
        'sha256': expected_sha,
        'engine': 'onnx-webgpu',
        'experimental': True,
        'precision': precision,
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


def separate_vocals(source, directory, job, device: str = 'auto', *, input_kind: str = 'vocals') -> dict[str, Any]:
    """Run BS-RoFormer ONNX in an isolated child process so DirectML stays unaffected."""
    if input_kind not in ('vocals', 'mix'):
        raise ValueError('分离输入类型无效。')
    if device not in ('auto', 'webgpu', 'cpu'):
        raise ValueError('BS-RoFormer ONNX 的设备请选择自动、WebGPU 或 CPU。')
    from roformer_onnx_component import use_ready
    with use_ready() as environment:
        report_file = Path(job) / 'temp/roformer-onnx-report.json'
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.unlink(missing_ok=True)
        cmd = [
            load_config()['python'], '-B', '-u', str(Path(__file__).resolve()),
            '--worker',
            '--source', str(source),
            '--directory', str(directory),
            '--job', str(job),
            '--environment', str(environment),
            '--device', str(device),
            '--input-kind', str(input_kind),
            '--report', str(report_file),
        ]
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        tail: list[str] = []
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                cancelled(job)
                line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
                if line:
                    print(line, flush=True)
                    tail.append(line)
                    if len(tail) > 40:
                        tail.pop(0)
            code = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        cancelled(job)
        if code != 0 or not report_file.is_file():
            detail = '\n'.join(tail[-12:])
            raise RuntimeError(f'BS-RoFormer ONNX（实验性）失败（退出码 {code}）：{detail}')
        return json.loads(report_file.read_text(encoding='utf-8'))


def run_pipeline_separation(mode: str, job: Path, request: dict[str, Any], report: dict[str, Any]) -> None:
    """Shared pipeline dispatch for both PyTorch ``roformer`` and experimental ``roformer_onnx``."""
    independent = request.get('separation_flow', 'cascade') == 'independent'
    target = job / 'temp/direct-lead' if independent else job / 'audio'
    source_wav = job / ('audio/source.wav' if independent else 'audio/vocals.wav')
    if mode == 'roformer_onnx':
        report['chorus'] = separate_vocals(
            source_wav, target, job, request.get('roformer_device', 'auto'),
            input_kind='mix' if independent else 'vocals',
        )
    else:
        from roformer import separate_vocals as separate_pytorch
        report['chorus'] = separate_pytorch(
            source_wav, target, job, request.get('roformer_device', 'auto'),
            input_kind='mix' if independent else 'vocals',
        )
    if independent:
        from roformer import combine_independent
        report['combination'] = combine_independent(job / 'audio/vocals.wav', target / 'lead.wav', job / 'audio', job)
        for name in ('lead.wav', 'backing.wav'):
            try:
                (target / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass
    report.update(
        method='independent_original_mix' if independent else 'cascaded_vocals',
        maximum_model_depth=1 if independent else 2,
        model_invocations=2,
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--source', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--input-kind', default='vocals')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if args.worker:
        result = _worker_separate(
            Path(args.source), Path(args.directory), Path(args.job),
            Path(args.environment), args.device, args.input_kind,
        )
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
