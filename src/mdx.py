"""ONNX MDX runner using NumPy/SciPy, preserving the input sample timeline.

Supports the local UVR MDX stereo models with four real/imaginary channels.
No torch installation is required. Model parameters come from UVR's registry.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
from scipy.fft import rfft, irfft

from common import cancelled


class Transform:
    def __init__(self, n_fft, hop=1024):
        self.n_fft, self.hop = n_fft, hop
        self.window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)

    def forward(self, wave, bins):
        padded = np.pad(wave, ((0, 0), (self.n_fft // 2, self.n_fft // 2)), mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft, axis=-1)[:, ::self.hop]
        spectra = rfft(frames * self.window, axis=-1).transpose(0, 2, 1)[:, :bins]
        # [left real, left imaginary, right real, right imaginary]
        return np.stack((spectra.real, spectra.imag), axis=1).reshape(1, 4, bins, spectra.shape[-1]).astype(np.float32)

    def inverse(self, tensor):
        _, channels, bins, count = tensor.shape
        if channels != 4:
            raise ValueError("MDX 模型必须输出四个频谱通道。")
        parts = tensor.reshape(2, 2, bins, count)
        spectrum = parts[:, 0] + 1j * parts[:, 1]
        spectrum = np.pad(spectrum, ((0, 0), (0, self.n_fft // 2 + 1 - bins), (0, 0)))
        frames = irfft(spectrum.transpose(0, 2, 1), n=self.n_fft, axis=-1) * self.window
        total = (count - 1) * self.hop + self.n_fft
        out = np.zeros((2, total), dtype=np.float32)
        weights = np.zeros(total, dtype=np.float32)
        for i in range(count):
            start = i * self.hop
            out[:, start:start + self.n_fft] += frames[:, i]
            weights[start:start + self.n_fft] += self.window ** 2
        np.divide(out, weights, out=out, where=weights > 1e-8)
        trim = self.n_fft // 2
        return out[:, trim:-trim]


def model_parameters(model_path, metadata_path):
    p = Path(model_path)
    with p.open("rb") as f:
        f.seek(-min(p.stat().st_size, 10000 * 1024), 2)
        fingerprint = hashlib.md5(f.read()).hexdigest()
    registry = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if fingerprint not in registry:
        raise ValueError(f"UVR 参数库中没有这个模型：{p.name}。请配置匹配的 MDX 模型和参数库。")
    params = registry[fingerprint]
    if params["primary_stem"] not in ("Instrumental", "Vocals"):
        raise ValueError("第一版只支持人声/伴奏二轨 MDX 模型。")
    return params, fingerprint


def separate(source, directory, config, job, log=print,
             output_names=("vocals.wav", "instrumental.wav")):
    audio, sr = sf.read(str(source), dtype="float32", always_2d=True)
    if sr != 44100 or audio.shape[1] != 2:
        raise ValueError("分离输入需要 44100Hz 双声道 WAV。")
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("输入音频为空或包含无效采样。")
    params, fingerprint = model_parameters(config["mdx_model"], config["mdx_metadata"])
    n_fft = int(params["mdx_n_fft_scale_set"])
    bins = int(params["mdx_dim_f_set"])
    frames = 2 ** int(params["mdx_dim_t_set"])
    transform = Transform(n_fft)
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 4
    providers = ["CPUExecutionProvider"]
    if config.get("separation_device") == "dml" and "DmlExecutionProvider" in ort.get_available_providers():
        providers.insert(0, "DmlExecutionProvider")
    session = ort.InferenceSession(config["mdx_model"], sess_options=options, providers=providers)
    shape = session.get_inputs()[0].shape
    if shape[1:] != [4, bins, frames]:
        raise ValueError(f"模型输入尺寸与 UVR 参数不匹配：{shape}")
    log(f"分离模型：{Path(config['mdx_model']).name}，设备：{session.get_providers()[0]}", flush=True)
    # Reflect-centered STFT matches MDX preprocessing. Pad the song once,
    # blend overlapping predictions, then undo that padding exactly.
    chunk = transform.hop * (frames - 1)
    trim = n_fft // 2
    generation = chunk - 2 * trim
    pad_end = generation + trim - (len(audio) % generation)
    peak = float(np.abs(audio).max())
    scale = max(peak, 1e-8)
    mix = audio.T / scale
    padded = np.pad(mix, ((0, 0), (trim, pad_end)))
    result = np.zeros_like(padded)
    divider = np.zeros(padded.shape[-1], dtype=np.float32)
    overlap = float(config.get("mdx_overlap", 0.5))
    if not 0 <= overlap < 1:
        raise ValueError("MDX overlap 必须在 0 到 1 之间。")
    denoise = bool(config.get("mdx_denoise", True))
    step = max(1, int(chunk * (1 - overlap)))
    positions = list(range(0, padded.shape[-1], step))
    for index, start in enumerate(positions):
        cancelled(job)
        size = min(chunk, padded.shape[-1] - start)
        wave = np.pad(padded[:, start:start + size], ((0, 0), (0, chunk - size)))
        tensor = transform.forward(wave, bins)
        tensor[:, :, :3, :] = 0
        predicted = session.run(None, {session.get_inputs()[0].name: tensor})[0]
        if denoise:
            # UVR's polarity averaging suppresses model bias/artifacts.
            negative = session.run(None, {session.get_inputs()[0].name: -tensor})[0]
            predicted = (predicted - negative) * 0.5
        estimate = transform.inverse(predicted)[:, :size]
        window = np.hanning(size).astype(np.float32)
        result[:, start:start + size] += estimate * window
        divider[start:start + size] += window
        log(f"分离进度：{index + 1}/{len(positions)}", flush=True)
        from progress_state import publish
        publish(job, 'separation', '主唱 / 和声' if output_names[0] == 'lead.wav' else '人声 / 伴奏', index+1, len(positions))
    target = slice(trim, trim + len(audio))
    if not np.all(divider[target] > 0):
        raise RuntimeError("分离窗口没有完整覆盖歌曲。")
    primary = (result[:, target] / divider[target] * scale * float(params["compensate"])).T
    secondary = audio - primary
    if params["primary_stem"] == "Instrumental":
        instrumental, vocals = primary, secondary
    else:
        vocals, instrumental = primary, secondary
    if not np.isfinite(vocals).all() or not np.isfinite(instrumental).all():
        raise RuntimeError("分离模型产生了无效采样。")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Float WAV avoids clipping and independent volume changes to stems.
    sf.write(str(directory / output_names[0]), vocals, sr, subtype="FLOAT")
    sf.write(str(directory / output_names[1]), instrumental, sr, subtype="FLOAT")
    return {
        "model": config["mdx_model"], "model_hash": fingerprint,
        "provider": session.get_providers()[0], "sample_rate": sr,
        "samples": len(audio), "duration": len(audio) / sr,
        "timeline_offset_seconds": 0,
        "overlap": overlap, "polarity_averaging": denoise,
        "sum_max_error": float(np.max(np.abs(vocals + instrumental - audio))),
    }
