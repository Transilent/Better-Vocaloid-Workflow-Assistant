"""Resumable BV -> video/WAV -> vocals/instrumental -> MIDI workflow."""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from common import ROOT, BV_PATTERN, atomic_json, cancelled, clean_lyrics, extract_bv, load_config, resolve_tools, completed_voice, file_hash, app_path, saved_path

sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "vendor"))
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}


def singer_candidate(description, title=""):
    lead = re.search(r"(?im)^\s*(?:歌|演唱|主唱|歌姬|vocal)\s*[:：]\s*(.+)$", description)
    if not lead:
        lead = re.search(r"(?m)^\s*原唱\s*[:：]\s*(.+)$", description)
    if not lead:
        lead = re.search(r"【([^】]+?)(?:原创|翻唱|翻调|[Cc]over)[^】]*】", title)
    harmony = re.search(r"(?m)^\s*和声\s*[:：]\s*(.+)$", description)
    return ((lead.group(1).strip() if lead else "[歌姬待核对]") +
            (f"（和声：{harmony.group(1).strip()}）" if harmony else ""))


def create_job(source="", lyrics="", language="zh", recognize_lyrics=True, parent=None,
               voice_mode="dual", imported_stems=None, midi_steps=16, backing_lyrics="", remember=True,
               local_music=None, roformer_device='auto'):
    if local_music is not None and not str(local_music).strip().strip('"'):
        raise ValueError("请选择本地音乐文件。")
    local = app_path(local_music) if local_music is not None else None
    if local is not None and (not local.is_file() or local.stat().st_size == 0):
        raise ValueError("请选择存在且非空的本地音乐文件。")
    bv = None if local is not None else extract_bv(source)
    if voice_mode not in ("single", "dual", "import", "roformer"):
        raise ValueError("未知的分轨模式。")
    if language not in ("zh", "ja"):
        raise ValueError("歌词语言请选择中文或日语。")
    if midi_steps not in (8, 16, 32):
        raise ValueError("MIDI 推理步数应为 8、16 或 32。")
    if roformer_device not in ('auto', 'cpu', 'cuda'):
        raise ValueError('新模型的设备请选择自动、CPU 或 CUDA。')
    if voice_mode == 'roformer':
        from optional_components import require_ready
        require_ready()
    if voice_mode == "import":
        for key in ("lead", "backing", "instrumental"):
            path = (imported_stems or {}).get(key)
            if not path or not app_path(path).is_file():
                raise ValueError("请分别选择主唱、和声、伴奏的完整 WAV 文件。")
        imported_stems = {key: saved_path(imported_stems[key]) for key in ("lead", "backing", "instrumental")}
    timestamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    job = Path(parent or ROOT / "jobs") / f"{bv or 'Local'}_{timestamp}"
    job.mkdir(parents=True)
    atomic_json(job / "request.json", {
        "version": 2, "bv": bv, "url": f"https://www.bilibili.com/video/{bv}/" if bv else None,
        "source_kind": "local" if local is not None else "bilibili",
        **({"source_audio": saved_path(local), "source_title": local.stem,
            "input_file": "input/source" + local.suffix.lower()} if local is not None else {}),
        "language": language, "lyrics": clean_lyrics(lyrics),
        "recognize_lyrics": bool(recognize_lyrics), "tools": load_config(resolve_paths=False),
        "voice_mode": voice_mode, "imported_stems": imported_stems or {},
        "midi_steps": int(midi_steps), "backing_lyrics": clean_lyrics(backing_lyrics),
        **({'roformer_device': roformer_device} if voice_mode == 'roformer' else {}),
    })
    if remember:
        (ROOT / "last_job.txt").write_text(saved_path(job), encoding="utf-8")
    return job.resolve()


def reuse_download(previous, **options):
    """New immutable job, reusing the old download; old outputs stay available."""
    import shutil
    previous = app_path(previous)
    old = json.loads((previous / "request.json").read_text(encoding="utf-8"))
    prior_status = json.loads((previous / "status.json").read_text(encoding="utf-8"))
    if old.get("source_kind") == "local":
        options["local_music"] = previous / old["input_file"]
    job = create_job(old.get("bv") or "", remember=False, **options)
    if old.get("source_kind") == "local":
        new_request = json.loads((job / "request.json").read_text(encoding="utf-8"))
        new_request["source_title"] = old["source_title"]
        atomic_json(job / "request.json", new_request)
    copied = {"steps": {}}
    for stage in ("metadata", "download", "audio"):
        if prior_status["steps"].get(stage, {}).get("state") != "done":
            break
        if not all((previous / path).is_file() for path in requirements(stage, old)):
            break
        files = list(requirements(stage, old))
        if stage == "metadata":
            files += [p.name for p in previous.glob("*.txt") if p.name != "last_job.txt"]
        for name in dict.fromkeys(files):
            destination = job / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            shutil.copy2(previous / name, destination)
        copied["steps"][stage] = {"state": "done", "reused_from": str(previous)}
    atomic_json(job / "status.json", copied)
    (ROOT / "last_job.txt").write_text(saved_path(job), encoding="utf-8")
    return job


def get_view(bv):
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bv})
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)
    if data.get("code") != 0:
        raise RuntimeError(f"读取 B站简介失败：{data.get('message', data.get('code'))}")
    return data["data"]


def run_process(argv, job, label, cooperative=False):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    temp = Path(job) / "temp"
    temp.mkdir(exist_ok=True)
    env.update(TMP=str(temp), TEMP=str(temp))
    cache = ROOT / "cache/numba"
    cache.mkdir(parents=True, exist_ok=True)
    env["NUMBA_CACHE_DIR"] = str(cache)
    output = Path(job) / f"{label}.log"
    # A Windows job contains child processes, including ASR workers. Closing
    # it on cancellation prevents an inference process surviving the job.
    handle = None
    try:
        import win32job
        handle = win32job.CreateJobObject(None, "VocalFlow-" + uuid.uuid4().hex)
        limits = win32job.QueryInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation, limits)
    except ImportError:
        pass
    process = None
    try:
        with output.open("wb") as writer, output.open("rb") as reader:
            process = subprocess.Popen(argv, stdout=writer, stderr=subprocess.STDOUT, env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if handle is not None:
                try:
                    win32job.AssignProcessToJobObject(handle, process._handle)
                except Exception:
                    handle.Close()
                    handle = None
            pending = b""
            stop_started = None
            while True:
                more = reader.read()
                pending += more
                if b"\n" in pending:
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    for line in lines:
                        text = line.decode("utf-8", errors="replace").strip()
                        if text:
                            print(text, flush=True)
                code = process.poll()
                if code is not None:
                    pending += reader.read()
                    if pending:
                        print(pending.decode("utf-8", errors="replace"), flush=True)
                    break
                if (Path(job) / "cancel.flag").exists():
                    stop_started = stop_started or time.monotonic()
                    if not cooperative or time.monotonic() - stop_started > 30:
                        if handle is not None:
                            win32job.TerminateJobObject(handle, 1)
                        else:
                            process.terminate()
                        process.wait(timeout=10)
                        cancelled(job)
                time.sleep(0.2)
            cancelled(job)
            if code:
                raise RuntimeError(f"{label} 失败（退出码 {code}）；详情见 {output.name}。")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if handle is not None:
            handle.Close()


def check_audio(path, stereo=True):
    import soundfile as sf
    info = sf.info(str(path))
    if info.frames <= 0 or info.samplerate != 44100 or (stereo and info.channels != 2):
        raise RuntimeError(f"音频验证失败：{Path(path).name}")
    return info


def prepare_metadata(job, request):
    if request.get("source_kind") == "local":
        title = request["source_title"]
        atomic_json(job / "source-info.json", {"kind": "local", "bvid": None,
                    "title": title, "desc": "", "owner": {"name": ""}})
        atomic_json(job / "upstream-info.json", [])
        (job / "发布简介草稿.txt").write_text(
            f"本家：待填写（本地音乐导入）\n歌曲：{title}\n原作者 / 歌姬：待填写\nSTAFF：待填写", encoding="utf-8")
        (job / "参考歌词.txt").write_text(request["lyrics"], encoding="utf-8")
        (job / "歌词候选_需核对.txt").write_text("", encoding="utf-8")
        print(f"已准备本地音乐：{title}；本家与 STAFF 可在发布页手填。", flush=True)
        return
    data = get_view(request["bv"])
    atomic_json(job / "source-info.json", data)
    (job / "原视频简介.txt").write_text(data.get("desc", ""), encoding="utf-8")
    linked = [x for x in dict.fromkeys(BV_PATTERN.findall(data.get("desc", ""))) if x != request["bv"]]
    upstream = []
    # Keep one-hop citations as citations: do not silently change the input.
    for bv in linked[:5]:
        try:
            detail = get_view(bv)
            upstream.append(detail)
        except Exception as exc:
            upstream.append({"bvid": bv, "desc": "", "retrieval_error": str(exc)})
            print(f"来源 {bv} 的简介暂时读取失败，草稿中保留 BV 号。", flush=True)
    atomic_json(job / "upstream-info.json", upstream)
    description = [
        f"本家：{request['bv']}/@{data.get('owner', {}).get('name', '待填写')}/{singer_candidate(data.get('desc', ''), data.get('title', ''))}",
        f"本家视频：{data.get('title', '')}", request["url"],
        "", "本家简介及 STAFF（原文，发布前可编辑）：", data.get("desc", ""),
    ]
    for detail in upstream:
        description += ["", f"本家引用来源：{detail['bvid']}/@{detail.get('owner', {}).get('name', '待核对')}/{singer_candidate(detail.get('desc', ''), detail.get('title', ''))}",
                        f"https://www.bilibili.com/video/{detail['bvid']}/", detail.get("desc", "")]
    (job / "发布简介草稿.txt").write_text("\n".join(description), encoding="utf-8")
    # Only labeled lyric sections qualify as candidates. They are never
    # automatically submitted without review, and empty input means ASR.
    candidate = ""
    marker = re.search(r"(?m)^\s*(?:歌词|Lyrics)\s*[:：]\s*", data.get("desc", ""), re.I)
    if marker:
        remaining = data["desc"][marker.end():]
        candidate = re.split(r"\n\s*\n|\n\s*(?:STAFF|作词|作曲|编曲|调教|混音|曲绘|PV)\s*[:：]", remaining, maxsplit=1, flags=re.I)[0]
    (job / "歌词候选_需核对.txt").write_text(clean_lyrics(candidate), encoding="utf-8")
    (job / "参考歌词.txt").write_text(request["lyrics"], encoding="utf-8")
    print(f"已读取：{data['title']}，作者：{data['owner']['name']}。", flush=True)
    print("简介未标出歌词；使用本地自动识别。" if not request["lyrics"] and not candidate else "已保存参考歌词或歌词候选。", flush=True)


def download(job, request):
    if request.get("source_kind") == "local":
        import hashlib
        source = app_path(request["source_audio"])
        if not source.is_file():
            raise FileNotFoundError("本地音乐文件已移动，请重新选择文件。")
        target = job / request["input_file"]
        target.parent.mkdir(exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        before = source.stat()
        digest = hashlib.sha256()
        try:
            with source.open("rb") as reader, temporary.open("wb") as writer:
                for block in iter(lambda: reader.read(4 * 1024**2), b""):
                    cancelled(job)
                    digest.update(block)
                    writer.write(block)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or temporary.stat().st_size != before.st_size:
                raise ValueError("音乐文件在导入时发生变化，请重新导入。")
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        atomic_json(job / "import-report.json", {"kind": "local", "file": request["input_file"],
                    "bytes": before.st_size, "sha256": digest.hexdigest()})
        print("已保存本地音乐副本；原文件保持不变。", flush=True)
        return
    import yt_dlp
    last = [0.0]

    def progress(info):
        cancelled(job)
        now = time.monotonic()
        if info["status"] == "finished":
            print("一个媒体轨下载完成。", flush=True)
        elif now - last[0] > 5:
            last[0] = now
            total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
            current = info.get("downloaded_bytes", 0)
            print(f"下载进度：{current / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else ""), flush=True)

    options = {
        "noplaylist": True, "cachedir": False, "quiet": True, "noprogress": True,
        "format": "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
        "outtmpl": str(job / "video/source.%(ext)s"),
        "merge_output_format": "mp4", "ffmpeg_location": resolve_tools(request["tools"])["ffmpeg"],
        "progress_hooks": [progress], "socket_timeout": 30, "retries": 3,
        "fragment_retries": 3, "overwrites": False,
    }
    (job / "video").mkdir(exist_ok=True)
    # No cookie extraction. If login is needed, expose the error and allow
    # the user to choose a separate login integration in a later iteration.
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(request["url"], download=True)
    selected = [{k: item.get(k) for k in ("format_id", "width", "height", "vcodec", "acodec", "abr")}
                for item in info.get("requested_formats", [info])]
    atomic_json(job / "download-report.json", {"title": info.get("title"), "duration": info.get("duration"), "formats": selected})
    source = job / "video/source.mp4"
    if not source.is_file() or source.stat().st_size < 1024:
        raise RuntimeError("没有得到完整的 source.mp4。")
    print("原视频下载完成。", flush=True)


def audio_and_cover(job, request):
    (job / "audio").mkdir(exist_ok=True)
    ffmpeg = resolve_tools(request["tools"])["ffmpeg"]
    local = request.get("source_kind") == "local"
    source = str(job / (request["input_file"] if local else "video/source.mp4"))
    # Earlier development jobs shared source WAVs via hard links. Detach
    # this job's old output before ffmpeg opens it for rewriting.
    (job / "audio/source.wav").unlink(missing_ok=True)
    run_process([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                 "-map", "0:a:0", "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le",
                 str(job / "audio/source.wav")], job, "extract_audio")
    info = check_audio(job / "audio/source.wav")
    if local:
        atomic_json(job / "audio-info.json", {"sample_rate": info.samplerate, "samples": info.frames,
                    "duration": info.duration, "source_kind": "local", "cover_kind": None})
        print(f"已解码完整本地音乐：{info.duration:.3f} 秒，44100Hz 双声道浮点 WAV。", flush=True)
        return
    from cover_art import job_cover
    image, art = job_cover(job)
    if image.read_bytes().startswith(b"\xff\xd8\xff"):
        import shutil
        shutil.copy2(image, job / "cover.jpg")
    else:
        run_process([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(image),
                     "-frames:v", "1", "-q:v", "2", str(job / "cover.jpg")], job, "cover")
    atomic_json(job / "audio-info.json", {"sample_rate": info.samplerate, "samples": info.frames,
                                         "duration": info.duration, "cover_kind": "original", "cover_url": art["url"]})
    print(f"已提取完整音频：{info.duration:.3f} 秒；已保存本家原始封面。", flush=True)


def separate(job, request):
    mode = request.get("voice_mode", "single")
    if mode == "import":
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
        from math import gcd
        original = check_audio(job / "audio/source.wav")
        stems = {}
        for name in ("lead", "backing", "instrumental"):
            path = app_path(request["imported_stems"][name])
            audio, sr = sf.read(path, dtype="float32", always_2d=True)
            if audio.shape[1] == 1:
                audio = np.repeat(audio, 2, axis=1)
            if sr != 44100:
                divisor = gcd(sr, 44100)
                audio = resample_poly(audio, 44100 // divisor, sr // divisor, axis=0)
            if audio.shape[1] != 2 or not np.isfinite(audio).all():
                raise ValueError(f"导入音频格式无效：{path}")
            if abs(len(audio) - original.frames) > 88:
                raise ValueError(f"{name} 与输入音乐长度不一致。请从 0 秒导出完整图层，保留开头静音。")
            audio = np.pad(audio[:original.frames], ((0, max(0, original.frames - len(audio))), (0, 0)))
            sf.write(str(job / "audio" / (name + ".wav")), audio, 44100, subtype="FLOAT")
            stems[name] = audio
        sf.write(str(job / "audio/vocals.wav"), stems["lead"] + stems["backing"], 44100, subtype="FLOAT")
        report = {"method": "imported_stems", "sources": request["imported_stems"],
                  "samples": original.frames, "sample_rate": 44100, "timeline_offset_seconds": 0}
        atomic_json(job / "separation-report.json", report)
        print("已导入主唱、和声、伴奏；时间长度验证通过。", flush=True)
        return
    from mdx import separate as separate_mdx
    config = resolve_tools(request["tools"])
    report = separate_mdx(job / "audio/source.wav", job / "audio", config, job)
    if mode == "dual":
        config["mdx_model"] = str(ROOT / config["chorus_model"])
        config["mdx_metadata"] = str(ROOT / config["chorus_metadata"])
        print("进一步拆分主唱与和声（Karaoke 2）……", flush=True)
        report["chorus"] = separate_mdx(job / "audio/vocals.wav", job / "audio", config, job,
                                         output_names=("lead.wav", "backing.wav"))
    elif mode == 'roformer':
        from roformer import separate_vocals
        report['chorus'] = separate_vocals(job / 'audio/vocals.wav', job / 'audio', job,
                                          request.get('roformer_device', 'auto'))
    original = check_audio(job / "audio/source.wav")
    for name in (("vocals.wav", "instrumental.wav", "lead.wav", "backing.wav") if mode in ("dual", "roformer") else ("vocals.wav", "instrumental.wav")):
        if check_audio(job / "audio" / name).frames != original.frames:
            raise RuntimeError("分离输出长度与原音频不一致。")
    atomic_json(job / "separation-report.json", report)
    print("人声、伴奏验证通过，采样数与原音频一致。", flush=True)


def midi(job, request):
    config = resolve_tools(request["tools"])
    if request.get("voice_mode", "single") == "single":
        run_process([config["python"], "-B", "-u", str(ROOT / "midi_bridge.py"), str(job)],
                    job, "midi", cooperative=True)
        return
    for voice in ("lead", "backing"):
        cancelled(job)
        output = job / "midi" / voice
        if completed_voice(job, request, voice):
            print(f"已完成，跳过 {voice} 的提取。", flush=True)
            continue
        run_process([config["python"], "-B", "-u", str(ROOT / "midi_bridge.py"), str(job), voice],
                    job, "midi_" + voice, cooperative=True)
    from midi_merge import merge_voices
    report = merge_voices([("Lead Vocal", job / "midi/lead/lead.mid"),
                           ("Backing Vocal", job / "midi/backing/backing.mid")], job / "midi/voices.mid")
    report["voices"] = {voice: json.loads((job / "midi" / voice / "report.json").read_text(encoding="utf-8"))
                         for voice in ("lead", "backing")}
    report["merged_sha256"] = file_hash(job / "midi/voices.mid")
    import csv
    with (job / "midi/需要核对的音符.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["声部", "开始秒数", "结束秒数", "MIDI音高", "原因"])
        for voice, details in report["voices"].items():
            for note in details.get("review_hints", []):
                writer.writerow([voice, round(note["start"], 4), round(note["end"], 4), note["pitch"], note["reason"]])
    atomic_json(job / "midi/report.json", report)
    print("已合并双轨 MIDI：voices.mid（Lead Vocal / Backing Vocal）。", flush=True)


STEPS = [("metadata", "抓取简介与来源", prepare_metadata), ("download", "下载原视频", download),
         ("audio", "提取原音频和封面", audio_and_cover), ("separation", "分离人声和伴奏", separate),
         ("midi", "生成 MIDI", midi)]
REQUIRED = {"metadata": ["source-info.json", "upstream-info.json", "发布简介草稿.txt"],
            "download": ["video/source.mp4", "download-report.json"],
            "audio": ["audio/source.wav", "cover.jpg", "audio-info.json"],
            "separation": ["audio/vocals.wav", "audio/instrumental.wav", "separation-report.json"],
            "midi": ["midi/vocals.mid", "midi/report.json"]}


def requirements(key, request):
    if request.get("source_kind") == "local":
        if key == "download":
            return [request["input_file"], "import-report.json"]
        if key == "audio":
            return ["audio/source.wav", "audio-info.json"]
    if request.get("voice_mode", "single") != "single":
        if key == "separation":
            return REQUIRED[key] + ["audio/lead.wav", "audio/backing.wav"]
        if key == "midi":
            return ["midi/voices.mid", "midi/report.json", "midi/lead/lead.mid", "midi/backing/backing.mid", "midi/需要核对的音符.csv"]
    return REQUIRED[key]


@contextmanager
def job_lock(job):
    import msvcrt
    with (Path(job) / "run.lock").open("a+b") as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("这个任务已经在运行，请等待它完成或在原窗口取消。") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def run(job, until="midi"):
    job = app_path(job)
    with job_lock(job):
        # Cancel removal happens only after obtaining the lock, so a second
        # launch cannot clear the active job's stop request.
        (Path(job) / "cancel.flag").unlink(missing_ok=True)
        _run(job, until)


def _run(job, until):
    job = Path(job).resolve()
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    config = resolve_tools(request["tools"])
    keys = ["python", "vocal2midi", "ffmpeg"]
    if request.get("voice_mode") != "import":
        keys += ["mdx_model", "mdx_metadata"]
    status_file = job / "status.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {"steps": {}}
    status["state"] = "running"
    status.pop("error", None)
    atomic_json(status_file, status)
    try:
        if request.get('voice_mode') == 'roformer':
            from optional_components import require_ready
            require_ready()
        for key in keys:
            if not config.get(key) or not Path(config[key]).exists():
                raise FileNotFoundError(f"工具路径不存在：{config.get(key, key)}（可编辑 config.json 后重做任务）")
        if request.get("voice_mode") == "dual":
            for key in ("chorus_model", "chorus_metadata"):
                if not config.get(key) or not (ROOT / config[key]).is_file():
                    raise FileNotFoundError(f"主唱/和声分离模型路径不存在：{config.get(key, key)}")
        for key, title, function in STEPS:
            if request.get("source_kind") == "local":
                title = {"metadata": "准备本地音乐资料", "download": "导入本地音乐",
                         "audio": "解码本地音乐"}.get(key, title)
            cancelled(job)
            completed = status["steps"].get(key, {}).get("state") == "done"
            intact = all((job / x).is_file() and (job / x).stat().st_size > 0 for x in requirements(key, request))
            if key == "download" and intact and request.get("source_kind") == "local":
                try:
                    saved = json.loads((job / "import-report.json").read_text(encoding="utf-8"))
                    intact = saved["sha256"] == file_hash(job / request["input_file"])
                except (OSError, KeyError, ValueError):
                    intact = False
            if key == "midi" and intact and request.get("version", 1) >= 2:
                voices = ("vocals",) if request.get("voice_mode") == "single" else ("lead", "backing")
                intact = all(completed_voice(job, request, voice) for voice in voices)
                if intact and len(voices) == 2:
                    try:
                        merged_report = json.loads((job / "midi/report.json").read_text(encoding="utf-8"))
                        intact = merged_report.get("merged_sha256") == file_hash(job / "midi/voices.mid")
                    except (OSError, ValueError):
                        intact = False
            if completed and intact:
                print(f"已完成，跳过：{title}。", flush=True)
            else:
                # An upstream step being regenerated invalidates downstream
                # completion, even if its old output files still exist.
                later = False
                for next_key, _, _ in STEPS:
                    if later:
                        status["steps"].pop(next_key, None)
                    later = later or next_key == key
                status["current_step"] = key
                status["steps"][key] = {"state": "running"}
                atomic_json(status_file, status)
                print(f"\n正在{title}……", flush=True)
                started = time.monotonic()
                function(job, request)
                status["steps"][key] = {"state": "done", "seconds": round(time.monotonic() - started, 2)}
                atomic_json(status_file, status)
            if key == until:
                break
        status["state"] = "done" if until == "midi" else "partial"
        status.pop("error", None)
        status.pop("current_step", None)
        atomic_json(status_file, status)
        print(f"\n本轮处理完成：{job}", flush=True)
    except BaseException as exc:
        status["state"] = "cancelled" if isinstance(exc, InterruptedError) else "failed"
        status["error"] = str(exc)
        atomic_json(status_file, status)
        raise


def main():
    parser = argparse.ArgumentParser(description="BV / 本地音乐 → 分离 → Vocal2Midi")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", help="BV 号或 B站链接")
    inputs.add_argument("--audio-file", help="直接导入本地音乐")
    inputs.add_argument("--job", help="继续已有任务")
    parser.add_argument("--language", choices=("zh", "ja"), default="zh")
    parser.add_argument("--lyrics-file")
    parser.add_argument("--notes-only", action="store_true")
    parser.add_argument("--voice-mode", choices=("single", "dual", "import", "roformer"), default="dual")
    parser.add_argument('--roformer-device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument("--lead-wav")
    parser.add_argument("--backing-wav")
    parser.add_argument("--instrumental-wav")
    parser.add_argument("--midi-steps", type=int, choices=(8, 16, 32), default=16)
    parser.add_argument("--until", choices=[x[0] for x in STEPS], default="midi")
    args = parser.parse_args()
    try:
        if args.job:
            job = Path(args.job)
        else:
            lyrics = Path(args.lyrics_file).read_text(encoding="utf-8-sig") if args.lyrics_file else ""
            job = create_job(args.source or "", lyrics, args.language, not args.notes_only,
                             voice_mode=args.voice_mode, midi_steps=args.midi_steps,
                             roformer_device=args.roformer_device,
                             local_music=args.audio_file,
                             imported_stems={"lead": args.lead_wav, "backing": args.backing_wav, "instrumental": args.instrumental_wav})
        print(f"任务目录：{job}", flush=True)
        run(job, args.until)
    except (ValueError, FileNotFoundError) as exc:
        print(f"处理失败：{exc}", file=sys.stderr, flush=True)
        return 1
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
