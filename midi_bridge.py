"""Call the bundled Vocal2Midi application layer with portable runtime paths."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from common import atomic_json, cancelled, clean_lyrics, midi_signature, file_hash, resolve_tools, app_path
from midi_checks import inspect_midi
from v2m_runtime import configure

# Windows spawn executes this module in each ASR/slicer worker. Apply the
# runtime paths there too, before the worker target starts loading models.
if os.environ.get("VOCALFLOW_V2M_ROOT"):
    configure(os.environ["VOCALFLOW_V2M_ROOT"])


def run(job, voice="vocals"):
    if voice not in ("vocals", "lead", "backing"):
        raise ValueError("声部名称无效。")
    job = app_path(job)
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    root = Path(resolve_tools(request["tools"])["vocal2midi"]).resolve()
    configure(root)
    # ONNX and llama native libraries use the same locations as run.bat.
    os.environ["PATH"] = os.pathsep.join([str(root / "python"), str(root / "python/DLLs"),
                                         str(root / "inference/qwen3asr_dml/bin"), os.environ.get("PATH", "")])
    from application.config import PipelineConfig
    from application.pipeline import run_auto_lyric_job
    # Load ONNX before Qt: these portable builds ship conflicting native
    # dependencies when Qt initializes first.
    from PyQt5.QtCore import QSettings
    import mido

    settings = QSettings(str(root / "settings/vocal2midi.ini"), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)

    def value(key, default):
        return settings.value(key, default, type=type(default))

    def model(key, default):
        path = Path(value(key, default))
        return str(path if path.is_absolute() else root / path)

    def is_cancelled():
        return (job / "cancel.flag").exists()

    language = request["language"]
    saved_mode = value("lyric_output_mode_" + language, "汉字" if language == "zh" else "罗马音")
    mode = {"汉字": "hanzi", "拼音": "pinyin", "罗马音": "romaji", "假名": "kana"}.get(saved_mode, "auto")
    t0, nsteps = value("t0", 0.0), request.get("midi_steps", value("nsteps", 8))
    if not 0 <= t0 < 1 or nsteps < 1:
        raise ValueError("Vocal2Midi 的 t0/nsteps 设置无效。")
    out = job / "midi" if voice == "vocals" else job / "midi" / voice
    out.mkdir(parents=True, exist_ok=True)
    pending = job / "temp/midi-output" / voice
    pending.mkdir(parents=True, exist_ok=True)
    signature = midi_signature(job, request, voice)
    alignment_file = pending / (voice + "_alignment.json")
    alignment_file.unlink(missing_ok=True)
    cfg = PipelineConfig(
        audio_path=str(job / "audio" / (voice + ".wav")),
        output_filename=voice + ".wav", output_dir=pending,
        game_model_dir=model("game_model", "experiments/GAME-1.0.3-medium-onnx"),
        hfa_model_dir=model("hfa_model", "experiments/1218_hfa_model_new_dict"),
        asr_model_path=model("asr_model", "experiments/Qwen3-ASR-1.7B-dml"),
        device=request["tools"].get("midi_device", "dml"), language=language,
        ts=[t0 + i * (1 - t0) / nsteps for i in range(nsteps)],
        lyric_output_mode=mode, original_lyrics=clean_lyrics(request.get("backing_lyrics", "") if voice == "backing" else request.get("lyrics", "")),
        output_formats=["mid", "csv", "txt"],
        output_lyrics=request.get("recognize_lyrics", True), output_pitch_curve=False,
        slicing_method="智能切片", slice_min_sec=value("slice_min_sec", 5.0),
        slice_max_sec=value("slice_max_sec", 10.0), tempo=120.0,
        quantization_step=0, quantization_mode="simple", pitch_format="name",
        round_pitch=value("round_pitch", True),
        seg_threshold=value("seg_thresh", 0.2), seg_radius=value("seg_rad", 0.02),
        est_threshold=value("est_thresh", 0.2), batch_size=value("batch_size", 1),
        asr_batch_size=value("asr_batch", 2),
        rmvpe_model_path=model("rmvpe_model", "experiments/RMVPE/rmvpe.onnx"),
        phoneme_asr_model_path=model("phoneme_asr_model", "experiments/romajiASR"),
        cancel_checker=is_cancelled,
    )
    cancelled(job)
    print(f"开始提取 {voice}：GAME 推理 {nsteps} 步。", flush=True)
    import numpy as np
    import soundfile as sf
    waveform, _ = sf.read(cfg.audio_path, dtype="float32", always_2d=True)
    silent = float(np.max(np.abs(waveform), initial=0)) < 1e-6
    if silent and voice == "backing":
        empty = mido.MidiFile(charset="utf8")
        empty.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000)]))
        empty.save(str(pending / "backing.mid"))
        for suffix in ("csv", "txt"):
            (pending / ("backing." + suffix)).write_text("", encoding="utf-8")
        print("和声为静音，输出空和声轨。", flush=True)
    else:
        run_auto_lyric_job(cfg)
    cancelled(job)
    report = inspect_midi(pending / (voice + ".mid"), allow_empty=voice == "backing")
    alignment = json.loads(alignment_file.read_text(encoding="utf-8")) if alignment_file.exists() else None
    for suffix in ("mid", "csv", "txt"):
        (pending / (voice + "." + suffix)).replace(out / (voice + "." + suffix))
    if alignment_file.exists():
        alignment_file.replace(out / alignment_file.name)
    else:
        (out / alignment_file.name).unlink(missing_ok=True)
    report.update({
        "alignment": alignment,
        "language": language, "reference_lyrics_used": bool(cfg.original_lyrics),
        "recognize_lyrics": cfg.output_lyrics,
        "voice": voice, "inference_steps": nsteps,
        "input_signature": signature, "midi_sha256": file_hash(out / (voice + ".mid")),
        "silent_input": silent,
        "tempo": 120, "quantization": "disabled",
        "note": "120 BPM 用于换算时间，不代表已检测到歌曲 BPM；人声开头静音保留。",
    })
    atomic_json(out / "report.json", report)
    print(f"MIDI 验证通过：{report['notes']} 个音符，{report['lyrics_events']} 个歌词事件。", flush=True)
    if alignment and alignment["requested_lyrics"] and alignment["pitch_only_chunks"]:
        print(f"提示：{alignment['pitch_only_chunks']} 个片段保留音符但没有可靠歌词对齐；"
              f"详情见 {alignment_file.name}。", flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "vocals")
