"""Call the bundled Vocal2Midi application layer with portable runtime paths."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from common import atomic_json, cancelled, clean_lyrics, midi_signature, file_hash, resolve_tools, app_path, preserve_changed_file
from midi_checks import inspect_midi
from v2m_runtime import configure

# Windows spawn executes this module in each ASR/slicer worker. Apply the
# runtime paths there too, before the worker target starts loading models.
if os.environ.get("VOCALFLOW_V2M_ROOT"):
    configure(os.environ["VOCALFLOW_V2M_ROOT"])


def run(job, voice="vocals", asr_session=None):
    if voice not in ("vocals", "lead", "backing"):
        raise ValueError("声部名称无效。")
    job = app_path(job)
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    root = Path(resolve_tools(request["tools"])["vocal2midi"]).resolve()
    from runtime_devices import prepare_environment
    prepare_environment(request['tools'])
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
        candidate=path if path.is_absolute() else root/path
        if not candidate.exists() and not path.is_absolute() and (root/'backend-version.json').is_file():
            candidate=Path(__file__).resolve().parent/'dependencies/vocal2midi'/path
        return str(candidate)

    def is_cancelled():
        return (job / "cancel.flag").exists()

    language = request["language"]
    saved_mode = value("lyric_output_mode_" + language, "汉字" if language == "zh" else "罗马音")
    mode = {"汉字": "hanzi", "拼音": "pinyin", "罗马音": "romaji", "假名": "kana"}.get(saved_mode, "auto")
    if language == 'zh' and 'zh_lyric_mode' in request:
        mode = request['zh_lyric_mode']
        if mode not in ('hanzi', 'pinyin'):
            raise ValueError('任务中的中文歌词格式无效，请重新建立任务。')
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
    formats=['mid','csv','txt']
    if request.get('project_formats'):
        formats.append('ustx')
        if (root/'backend-version.json').is_file(): formats.append('vsqx')
    config_values = dict(
        audio_path=str(job / "audio" / (voice + ".wav")),
        output_filename=voice + ".wav", output_dir=pending,
        game_model_dir=model("game_model", "experiments/GAME-1.0.3-medium-onnx"),
        hfa_model_dir=model("hfa_model", "experiments/1218_hfa_model_new_dict"),
        asr_model_path=model("asr_model", "experiments/Qwen3-ASR-1.7B-dml"),
        device=request["tools"].get("midi_device", "dml"), language=language,
        ts=[t0 + i * (1 - t0) / nsteps for i in range(nsteps)],
        lyric_output_mode=mode, original_lyrics=clean_lyrics(request.get("backing_lyrics", "") if voice == "backing" else request.get("lyrics", "")),
        output_formats=formats,
        output_lyrics=request.get("recognize_lyrics", True), output_pitch_curve=bool(request.get('export_pitch') and request.get('project_formats')),
        slicing_method='smart' if (root/'backend-version.json').is_file() else '智能切片', slice_min_sec=value("slice_min_sec", 5.0),
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
    config_values.update(alignment_engine=request.get('alignment_engine','hfa'),
        chinese_asr_engine='qwen', japanese_asr_engine='romaji', japanese_g2p_engine='pyopenjtalk',
        tifa_model_path=str(root/'models/tifa-1.0-onnx'))
    if asr_session is not None:
        config_values['asr_session'] = asr_session
    import inspect
    cfg=PipelineConfig(**{key:value for key,value in config_values.items() if key in inspect.signature(PipelineConfig).parameters})
    cancelled(job)
    print(f"开始提取 {voice}：GAME 推理 {nsteps} 步。", flush=True)
    import logging,re
    warnings=[]
    class AlignmentMessages(logging.Handler):
        def emit(self,record):
            message=record.getMessage()
            if 'pitch-only GAME fallback for ' in message:
                count=re.search(r'fallback for\s+(\d+)',message)
                if count: warnings.append(int(count[1]))
    listener=AlignmentMessages()
    logging.getLogger().addHandler(listener)
    silent = silent_audio(cfg.audio_path)
    if silent:
        empty = mido.MidiFile(charset="utf8")
        empty.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000)]))
        empty.save(str(pending / (voice+'.mid')))
        for suffix in ("csv", "txt"):
            (pending / (voice+'.'+suffix)).write_text("", encoding="utf-8")
        if 'ustx' in formats:
            from inference.API.ustx_api import save_ustx
            save_ustx([],pending/(voice+'.ustx'),tempo=120)
            if 'vsqx' in formats:
                from inference.API.vsqx_api import save_vsqx
                save_vsqx([],pending/(voice+'.vsqx'),tempo=120,language=language)
        print('未检测到该声部的有效人声，已保留空轨道。',flush=True)
    else:
        try: run_auto_lyric_job(cfg)
        finally: logging.getLogger().removeHandler(listener)
    cancelled(job)
    logging.getLogger().removeHandler(listener)
    if not cfg.output_pitch_curve:
        from project_exports import remove_pitch_data
        for suffix in ('ustx','vsqx'):
            if suffix in formats:
                remove_pitch_data(pending/(voice+'.'+suffix),suffix)
    report = inspect_midi(pending / (voice + ".mid"), allow_empty=voice == "backing" or silent)
    alignment = json.loads(alignment_file.read_text(encoding="utf-8")) if alignment_file.exists() else None
    if alignment is None and warnings:
        alignment={'requested_lyrics':cfg.output_lyrics,'pitch_only_chunks':sum(warnings)}
        atomic_json(alignment_file,alignment)
    try:
        old = json.loads((out/'report.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        old = {}
    preserved = {}
    for suffix in formats:
        expected = old.get('output_sha256',{}).get(suffix) or old.get('native_sha256',{}).get(suffix)
        if suffix == 'mid': expected = expected or old.get('midi_sha256')
        saved = preserve_changed_file(job,out/(voice+'.'+suffix),expected,file_hash(pending/(voice+'.'+suffix)))
        if saved: preserved[suffix] = saved
        (pending / (voice + "." + suffix)).replace(out / (voice + "." + suffix))
    if alignment_file.exists():
        alignment_file.replace(out / alignment_file.name)
    else:
        (out / alignment_file.name).unlink(missing_ok=True)
    report.update({
        "alignment": alignment,
        "language": language, "reference_lyrics_used": bool(cfg.original_lyrics),
        "recognize_lyrics": cfg.output_lyrics,
        "lyric_output_mode": mode,
        'project_formats':formats, 'pitch_curve_requested':cfg.output_pitch_curve,
        'native_sha256':{suffix:file_hash(out/(voice+'.'+suffix)) for suffix in formats if suffix in ('ustx','vsqx')},
        'output_sha256':{suffix:file_hash(out/(voice+'.'+suffix)) for suffix in formats},
        'preserved_copies':preserved,
        "voice": voice, "inference_steps": nsteps,
        "input_signature": signature, "midi_sha256": file_hash(out / (voice + ".mid")),
        'cache_schema': 6,
        "silent_input": silent,
        "tempo": 120, "quantization": "disabled",
        "note": "120 BPM 用于换算时间，不代表已检测到歌曲 BPM；人声开头静音保留。",
    })
    atomic_json(out / "report.json", report)
    print(f"MIDI 验证通过：{report['notes']} 个音符，{report['lyrics_events']} 个歌词事件。", flush=True)
    if alignment and alignment["requested_lyrics"] and alignment["pitch_only_chunks"]:
        print(f"提示：{alignment['pitch_only_chunks']} 个片段保留音符但没有可靠歌词对齐；"
              f"详情见 {alignment_file.name}。", flush=True)


def run_many(job, voices):
    """Reuse sequential native sessions for one job; never issue concurrent DML Run calls."""
    import time
    request = json.loads((app_path(job) / 'request.json').read_text(encoding='utf-8'))
    root = Path(resolve_tools(request['tools'])['vocal2midi'])
    from runtime_devices import prepare_environment
    prepare_environment(request['tools'])
    configure(root)
    session = None
    if (request.get('midi_backend') == 'v2' and request['language'] == 'zh'
            and request['tools'].get('midi_device', 'dml') == 'cpu' and request.get('recognize_lyrics', True)):
        # The retained multi-GB text model is worthwhile only with a comfortable
        # host-memory margin. GPU Qwen sessions keep their original release policy.
        from runtime_devices import available_memory
        if available_memory() >= 16 * 1024**3:
            from application.pipeline import open_asr_session
            session = open_asr_session()
    durations = {}
    try:
        for voice in voices:
            cancelled(job)
            print('[BVWA Voice] ' + voice, flush=True)
            started = time.monotonic()
            run(job, voice, asr_session=session)
            durations[voice] = round(time.monotonic() - started, 3)
            # A completed voice is committed before starting the next one, so a
            # later error/cancel does not discard useful work.
            atomic_json(app_path(job) / 'midi/worker-report.json',
                        {'sequential_reuse': True, 'asr_reused': session is not None, 'seconds': durations})
    finally:
        if session is not None:
            session.close()


def silent_audio(path, threshold=1e-6):
    """Bounded-memory silence scan; nonfinite input is an error, not silence."""
    import numpy as np
    import soundfile as sf
    frames = 0
    silent = True
    for block in sf.blocks(str(path), blocksize=65536, dtype='float32', always_2d=True):
        frames += len(block)
        if not np.isfinite(block).all():
            raise ValueError('音频包含无效数值，请重新导入或分离音频。')
        if float(np.max(np.abs(block), initial=0)) >= threshold:
            silent = False
    if not frames:
        raise ValueError('音频没有有效采样，请重新导入。')
    return silent


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if len(sys.argv) > 2 and sys.argv[2] == '--voices':
        run_many(sys.argv[1], sys.argv[3:])
    else:
        run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "vocals")
