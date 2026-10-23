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
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from common import ROOT, BV_PATTERN, atomic_json, cancelled, clean_lyrics, extract_bv, load_config, resolve_tools, completed_voice, file_hash, app_path, saved_path, output_directory, remember_job
from common import direct_urlopen, native_path
from check_dependencies import validate as validate_dependencies

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
               local_music=None, roformer_device='auto', zh_lyric_mode='hanzi',
               workflow_graph=None, separation_device=None, midi_device=None,
               midi_backend=None, alignment_engine='hfa', project_formats=None,
               audio_formats=None, export_pitch=False, include_instrumental=True, lyric_reference=None,
               reference_mode='local', subtitles_mode='midi', dml_adapter=None, cpu_threads=None,
               reference_dynamics=False, dynamics_strength=.6,
               reference_tension=False, reference_breathiness=False, reference_voicing=False,
               separation_flow='cascade', reference_calibration=None, bpm=None, expression_density='dense',
               project_export_enabled=None, audio_export_enabled=None, export_preferences=None,
               performance_style='none', style_strength=.6, style_scope='lead',style_pitch=False,prepare_only=False):
    if type(prepare_only) is not bool:raise ValueError('准备工作流开关必须为布尔值。')
    if prepare_only:
        reference_dynamics=reference_tension=reference_breathiness=reference_voicing=False
        reference_calibration=None;performance_style='none';style_pitch=False;expression_density='original'
    from music_timing import validate_bpm
    bpm = validate_bpm(bpm)
    from calibration_refinement import density_settings
    density_settings({'expression_density':expression_density})
    from style_presets import settings as style_settings
    performance_style,style_strength,style_scope=style_settings({
        'performance_style':performance_style,'style_strength':style_strength,'style_scope':style_scope,'style_pitch':style_pitch})
    from project_exports import validate_options
    projects=list(project_formats or [])
    audios=list(['wav'] if audio_formats is None else audio_formats)
    validate_options(projects,audios)
    for value in (project_export_enabled, audio_export_enabled):
        if value is not None and type(value) is not bool:
            raise ValueError('导出开关必须为开启或关闭。')
    if project_export_enabled is False: projects=[]
    if audio_export_enabled is False: audios=[]
    if export_preferences is not None:
        if not isinstance(export_preferences, dict):
            raise ValueError('导出偏好格式无效。')
        from workflow import export_preferences as checked_export_preferences
        export_preferences=checked_export_preferences(export_preferences)
    if separation_flow not in ('cascade', 'independent') or separation_flow == 'independent' and voice_mode != 'roformer':
        raise ValueError('原曲独立提取目前需要选择 BS-RoFormer 分离组件。')
    if reference_calibration:
        if 'svp' not in projects: raise ValueError('声库校准拟合需要启用 SVP 工程导出。')
        from expression_calibration import read_profile
        read_profile(app_path(reference_calibration))
        from expression_component import status as calibration_component_status
        if not calibration_component_status()['ready']:
            raise FileNotFoundError('声库校准拟合需要 WORLD 组件，请在“模型与组件”中下载。')
    from vocal_expression import expression_settings
    expression = {'reference_dynamics':reference_dynamics, 'dynamics_strength':dynamics_strength,
                  'reference_tension':reference_tension, 'reference_breathiness':reference_breathiness,
                  'reference_voicing':reference_voicing}
    _, _, timbre = expression_settings(expression)
    if (reference_dynamics or timbre) and 'svp' not in projects:
        raise ValueError('参考演唱参数需要选择 Synthesizer V · SVP 工程。')
    if timbre and dynamics_strength > 0:
        from expression_component import status as expression_status
        if not expression_status()['ready']:
            raise FileNotFoundError('参考音色组件尚未安装，请在“模型与组件”下载 WORLD 分析器。')
    if reference_mode not in ('local','netease') or subtitles_mode not in ('midi','netease'):
        raise ValueError('歌词或字幕匹配方式无效。')
    if midi_backend not in (None,'v1','v2') or alignment_engine not in ('hfa','tifa'):
        raise ValueError('MIDI 引擎或歌词对齐引擎无效。')
    if alignment_engine=='tifa' and midi_backend!='v2':
        raise ValueError('TiFA 需要 Vocal2Midi 2.0 后端。')
    from workflow import validate_graph
    if workflow_graph is not None:
        end_stage = validate_graph(workflow_graph)
        if end_stage=='separation': projects=[]
        if end_stage=='separation' and (reference_dynamics or timbre or reference_calibration):
            raise ValueError('参考演唱参数需要启用 MIDI 与工程导出。')
        if end_stage == 'subtitles' and not recognize_lyrics:
            raise ValueError('字幕节点需要歌词事件，请开启歌词识别或移除字幕节点。')
        if end_stage=='subtitles' and subtitles_mode!='midi':
            raise ValueError('自动字幕使用 MIDI 匹配；网易云时间轴请在字幕卡片中编辑并导出。')
    tools = load_config(resolve_paths=False)
    if midi_backend is not None:
        tools['vocal2midi']='dependencies/vocal2midi2' if midi_backend=='v2' else 'dependencies/vocal2midi'
    if alignment_engine=='tifa' and not (ROOT/'dependencies/vocal2midi2/models/tifa-1.0-onnx').is_dir():
        raise FileNotFoundError('TiFA 模型尚未安装，请先安装模型或选择 HubertFA。')
    for key, value in [('separation_device', separation_device), ('midi_device', midi_device)]:
        if value is not None:
            if value not in ('cpu', 'dml'):
                raise ValueError('推理设备应为 CPU 或 DirectML。')
            tools[key] = value
    from runtime_devices import validate
    validate(tools.get('dml_adapter', 'auto') if dml_adapter is None else dml_adapter,
             tools.get('cpu_threads', 0) if cpu_threads is None else cpu_threads)
    if dml_adapter is not None: tools['dml_adapter'] = dml_adapter
    if cpu_threads is not None: tools['cpu_threads'] = cpu_threads
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
    if zh_lyric_mode not in ('hanzi', 'pinyin'):
        raise ValueError('中文歌词格式请选择汉字或拼音。')
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
    destination = output_directory(parent or ROOT / 'jobs')
    if destination.exists() and not destination.is_dir():
        raise ValueError('保存位置是文件，请选择用于存放任务的文件夹。')
    job = destination / f"{bv or 'Local'}_{timestamp}"
    try:
        job.mkdir(parents=True)
    except OSError as exc:
        raise ValueError('无法在所选目录创建任务文件夹，请检查目录权限和磁盘状态。') from exc
    atomic_json(job / "request.json", {
        "version": 3, "bv": bv, "url": f"https://www.bilibili.com/video/{bv}/" if bv else None,
        "source_kind": "local" if local is not None else "bilibili",
        "output_directory": saved_path(destination),
        **({"source_audio": saved_path(local), "source_title": local.stem,
            "input_file": "input/source" + local.suffix.lower()} if local is not None else {}),
        "language": language, "lyrics": clean_lyrics(lyrics),
        'lyric_lines': clean_lyrics(lyrics, preserve_lines=True).splitlines(),
        'backing_lyric_lines': clean_lyrics(backing_lyrics, preserve_lines=True).splitlines(),
        "zh_lyric_mode": zh_lyric_mode,
        "recognize_lyrics": bool(recognize_lyrics), "tools": tools,
        'created_at': dt.datetime.now().astimezone().isoformat(),
        **({'workflow_graph': workflow_graph} if workflow_graph is not None else {}),
        "voice_mode": voice_mode, "imported_stems": imported_stems or {},
        'separation_flow': separation_flow,
        **({'reference_calibration':saved_path(reference_calibration)} if reference_calibration else {}),
        "midi_steps": int(midi_steps), "bpm": bpm, "backing_lyrics": clean_lyrics(backing_lyrics),
        'project_formats':projects,'audio_formats':audios,'export_pitch':bool(export_pitch),
        'project_export_enabled':bool(projects),'audio_export_enabled':bool(audios),
        **({'export_preferences':export_preferences} if export_preferences is not None else {}),
        'include_instrumental':bool(include_instrumental),'alignment_engine':alignment_engine,
        'reference_dynamics':reference_dynamics,'dynamics_strength':float(dynamics_strength),'expression_density':expression_density,
        'performance_style':performance_style,'style_strength':style_strength,'style_scope':style_scope,'style_pitch':style_pitch,
        'prepare_only':prepare_only,
        'reference_tension':reference_tension,'reference_breathiness':reference_breathiness,'reference_voicing':reference_voicing,
        'reference_mode':reference_mode,'subtitles_mode':subtitles_mode,
        **({'midi_backend':midi_backend} if midi_backend else {}),
        **({'lyric_reference':lyric_reference} if lyric_reference else {}),
        **({'roformer_device': roformer_device} if voice_mode == 'roformer' else {}),
    })
    if remember:
        remember_job(job)
    return job.resolve()


def reuse_download(previous, **options):
    """New immutable job, reusing the old download; old outputs stay available."""
    import shutil
    previous = app_path(previous)
    old = json.loads((previous / "request.json").read_text(encoding="utf-8"))
    prior_status = json.loads((previous / "status.json").read_text(encoding="utf-8"))
    if old.get("source_kind") == "local":
        snapshot = previous / old['input_file']
        path = previous/'import-report.json'
        saved = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        expected = saved.get('sha256')
        if not expected:
            snapshot = app_path(old['source_audio'])
        elif not snapshot.is_file() or file_hash(snapshot) != expected:
            original = app_path(old['source_audio'])
            if not original.is_file() or file_hash(original) != expected:
                raise ValueError('原音乐副本已改变，无法复用。请重新选择要处理的音乐文件。')
            snapshot = original
        options["local_music"] = snapshot
    job = create_job(old.get("bv") or "", remember=False, **options)
    if old.get("source_kind") == "local":
        new_request = json.loads((job / "request.json").read_text(encoding="utf-8"))
        new_request["source_title"] = old["source_title"]
        atomic_json(job / "request.json", new_request)
    new_request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    from artifact_cache import valid_receipt, receipt
    copied = {"steps": {}}
    for stage in ("metadata", "download", "audio"):
        if prior_status["steps"].get(stage, {}).get("state") != "done":
            break
        files = list(requirements(stage, old))
        if not all((previous / path).is_file() and (previous / path).stat().st_size > 0 for path in files):
            break
        if not valid_receipt(previous,old,stage,files,prior_status['steps'][stage].get('receipt')):
            break
        if stage == 'download' and old.get('source_kind') == 'local' and file_hash(previous/old['input_file']) != saved.get('sha256'):
            break
        if stage == "metadata":
            files += [p.name for p in previous.glob("*.txt") if p.name != "last_job.txt"]
        for name in dict.fromkeys(files):
            destination = job / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            shutil.copy2(previous / name, destination)
        copied["steps"][stage] = {"state": "done", "reused_from": str(previous)}
        if stage == 'metadata':
            (job/'参考歌词.txt').write_text(new_request.get('lyrics',''),encoding='utf-8')
        copied['steps'][stage]['receipt'] = receipt(job,new_request,stage,requirements(stage,new_request))
    if 'metadata' in copied['steps']:
        from cover_art import reuse_cached_cover
        reuse_cached_cover(previous, job)
    from calibration_workflow import inherit_expression
    inherit_expression(previous,job,new_request)
    atomic_json(job / "status.json", copied)
    remember_job(job)
    return job


def get_view(bv, tools=None):
    from bilibili_session import task_options, download_cookies, cookie_header
    settings = task_options(tools)
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bv})
    headers = dict(HEADERS)
    req = urllib.request.Request(url, headers=headers)
    if cookie := cookie_header(download_cookies(settings), url):
        req.add_unredirected_header('Cookie', cookie)
    for attempt in range(settings['retries'] + 1):
        try:
            with direct_urlopen(req, timeout=settings['timeout']) as response:
                data = json.load(response)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if (attempt == settings['retries'] or
                    isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504)):
                raise
            time.sleep(min(4, .5 * 2 ** attempt))
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
    env["NUMBA_CACHE_DIR"] = native_path(cache)
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
            active_voice = {'midi_lead': '主唱', 'midi_backing': '和声'}.get(label, '人声')
            stop_started = None
            while True:
                more = reader.read()
                pending += more
                if b"\n" in pending or b"\r" in pending:
                    # tqdm updates use carriage returns instead of newlines.
                    # Consume them while the recognizer is still running.
                    lines = re.split(br"[\r\n]", pending)
                    pending = lines.pop()
                    for line in lines:
                        text = line.decode("utf-8", errors="replace").strip()
                        if text:
                            if label.startswith('midi'):
                                if text.startswith(('[VWA Voice] ', '[BVWA Voice] ')):
                                    active_voice = {'lead': '主唱', 'backing': '和声'}.get(text.rsplit(' ', 1)[-1], '人声')
                                from progress_state import from_log
                                from_log(job, text, active_voice)
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
                            terminate_tree(process)
                        process.wait(timeout=10)
                        cancelled(job)
                time.sleep(0.2)
            cancelled(job)
            if code:
                raise RuntimeError(f"{label} 失败（退出码 {code}）；详情见 {output.name}。")
    finally:
        if process is not None and process.poll() is None:
            terminate_tree(process)
            process.wait(timeout=10)
        if handle is not None:
            handle.Close()


def terminate_tree(process):
    """Fallback when an outer Windows job prevents nested JobObject assignment."""
    from process_control import terminate_tree as stop
    stop(process)


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
    data = get_view(request["bv"], request.get('tools', {}))
    atomic_json(job / "source-info.json", data)
    (job / "原视频简介.txt").write_text(data.get("desc", ""), encoding="utf-8")
    linked = [x for x in dict.fromkeys(BV_PATTERN.findall(data.get("desc", ""))) if x != request["bv"]]
    upstream = []
    # Keep one-hop citations as citations: do not silently change the input.
    for bv in linked[:5]:
        try:
            detail = get_view(bv, request.get('tools', {}))
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
    from bilibili_session import task_options as download_options, download_cookies, configure_downloader, verify_login
    settings = download_options(request.get('tools', {}))
    cookies = download_cookies(settings)
    if cookies:
        verify_login(cookies, settings['timeout'])
    source = job / 'video/source.mp4'
    report_path = job / 'download-report.json'
    if source.is_file():
        try:
            old = json.loads(report_path.read_text(encoding='utf-8'))
            if (old.get('sha256') == file_hash(source)
                    and bool(old.get('authenticated', False)) == settings['use_login']):
                print('原视频已校验，可以继续使用。',flush=True)
                return
        except (OSError,ValueError): pass
        # Called only for a new/interrupted/invalidated download stage. Detach a
        # stale final snapshot so yt-dlp cannot silently accept it with no-overwrite.
        source.unlink()
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
            from progress_state import publish
            speed = info.get('speed') or 0
            publish(job, 'download', '下载媒体轨', current, total, speed=speed,
                    downloaded=current, bytes_total=total, eta=info.get('eta'))
            print(f"下载进度：{current / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else ""), flush=True)

    options = {
        "noplaylist": True, "cachedir": False, "quiet": True, "noprogress": True,
        "format": "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
        "outtmpl": str(job / "video/source.%(ext)s"),
        "merge_output_format": "mp4", "ffmpeg_location": resolve_tools(request["tools"])["ffmpeg"],
        "progress_hooks": [progress], "socket_timeout": settings['timeout'], "retries": settings['retries'],
        "fragment_retries": settings['retries'], "overwrites": False,
        # Bilibili metadata, covers and media share the direct connection policy.
        "proxy": "",
    }
    (job / "video").mkdir(exist_ok=True)
    with yt_dlp.YoutubeDL(options) as downloader:
        if cookies:
            configure_downloader(downloader, cookies)
        info = downloader.extract_info(request["url"], download=True)
    selected = [{k: item.get(k) for k in ("format_id", "width", "height", "vcodec", "acodec", "abr")}
                for item in info.get("requested_formats", [info])]
    if not source.is_file() or source.stat().st_size < 1024:
        raise RuntimeError("没有得到完整的 source.mp4。")
    atomic_json(report_path, {"title": info.get("title"), "duration": info.get("duration"),
                             "formats": selected, 'sha256':file_hash(source),
                             'authenticated': bool(cookies)})
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
        for name in ("lead", "backing", "instrumental"):
            path = app_path(request["imported_stems"][name])
            info = sf.info(str(path))
            if info.frames <= 0 or info.channels not in (1, 2):
                raise ValueError(f'导入音频格式无效：{path.name}')
            frames = round(info.frames * 44100 / info.samplerate)
            if abs(frames - original.frames) > 88:
                raise ValueError(f"{name} 与输入音乐长度不一致。请从 0 秒导出完整图层，保留开头静音。")
            if info.samplerate == 44100:
                blocks = sf.blocks(str(path), blocksize=65536, dtype='float32', always_2d=True)
            else:
                audio, sr = sf.read(str(path), dtype='float32', always_2d=True)
                if not np.isfinite(audio).all(): raise ValueError('导入音频含有无效采样：'+path.name)
                divisor = gcd(sr, 44100)
                audio = resample_poly(audio, 44100 // divisor, sr // divisor, axis=0)
                blocks = iter((audio,))
            target = job / 'audio' / (name + '.wav')
            pending = target.with_name(name + '.pending.wav')
            try:
                with sf.SoundFile(str(pending), 'w', samplerate=44100, channels=2, subtype='FLOAT') as output:
                    written = 0
                    for block in blocks:
                        cancelled(job)
                        if not np.isfinite(block).all(): raise ValueError('导入音频含有无效采样：'+path.name)
                        if info.channels == 1: block = np.repeat(block, 2, axis=1)
                        usable = max(0, min(len(block), original.frames - written))
                        output.write(block[:usable]); written += usable
                    if written < original.frames: output.write(np.zeros((original.frames - written, 2), dtype='float32'))
                pending.replace(target)
            finally:
                pending.unlink(missing_ok=True)
                if info.samplerate != 44100: del audio, blocks
        pending = job / 'audio/vocals.pending.wav'
        try:
            with sf.SoundFile(str(pending), 'w', samplerate=44100, channels=2, subtype='FLOAT') as output, \
                 sf.SoundFile(str(job / 'audio/lead.wav')) as lead, sf.SoundFile(str(job / 'audio/backing.wav')) as backing:
                while True:
                    a=lead.read(65536,dtype='float32',always_2d=True)
                    if not len(a): break
                    b=backing.read(len(a),dtype='float32',always_2d=True)
                    cancelled(job); output.write(a + b)
            pending.replace(job / 'audio/vocals.wav')
        finally:
            pending.unlink(missing_ok=True)
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
        independent = request.get('separation_flow', 'cascade') == 'independent'
        target = job/'temp/direct-lead' if independent else job/'audio'
        report['chorus'] = separate_vocals(job/('audio/source.wav' if independent else 'audio/vocals.wav'),
            target, job, request.get('roformer_device', 'auto'), input_kind='mix' if independent else 'vocals')
        if independent:
            from roformer import combine_independent
            report['combination'] = combine_independent(job/'audio/vocals.wav', target/'lead.wav', job/'audio', job)
            # These original-mix predictions are intermediate copies. Keep the
            # validated final stems, not an extra full-length pair per task.
            for name in ('lead.wav','backing.wav'):
                try: (target/name).unlink(missing_ok=True)
                except OSError: pass  # Task-temp cleanup can remove an in-use file later.
            try: target.rmdir()
            except OSError: pass
        report.update(method='independent_original_mix' if independent else 'cascaded_vocals',
                      maximum_model_depth=1 if independent else 2, model_invocations=2)
    if mode in ('dual','roformer'):
        from vocal_partition import repair
        from progress_state import publish
        print('检查主唱与和声中的重复成分……',flush=True)
        report['partition'] = repair(job/'audio',job,
            progress=lambda done,total:publish(job,'separation','检查声部串音',done,total))
        print('声部检查完成：已保留时间轴和总人声。',flush=True)
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
    pending = []
    for voice in ("lead", "backing"):
        cancelled(job)
        output = job / "midi" / voice
        if completed_voice(job, request, voice):
            from task_contract import index_voice
            index_voice(job, voice, replace=True)
            print(f"已完成，跳过 {voice} 的提取。", flush=True)
            continue
        pending.append(voice)
    # Chinese GPU ASR must not coexist with the previous voice's resident GAME,
    # HFA and RMVPE sessions on a small GPU. Preserve isolated workers there.
    reuse = request.get('midi_backend') == 'v2' and (request['language'] == 'ja'
        or config.get('midi_device') == 'cpu' or not request.get('recognize_lyrics', True))
    if pending and reuse:
        run_process([config['python'], '-B', '-u', str(ROOT / 'midi_bridge.py'), str(job), '--voices', *pending],
                    job, 'midi_voices', cooperative=True)
    for voice in ([] if reuse else pending):
        from progress_state import publish
        publish(job, 'midi', ('主唱' if voice == 'lead' else '和声')+' · 准备模型')
        run_process([config["python"], "-B", "-u", str(ROOT / "midi_bridge.py"), str(job), voice],
                    job, "midi_" + voice, cooperative=True)
    from midi_merge import merge_voices
    try:
        previous_merge = json.loads((job/'midi/report.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        previous_merge = {}
    report = merge_voices([("Lead Vocal", job / "midi/lead/lead.mid"),
                           ("Backing Vocal", job / "midi/backing/backing.mid")], job / "midi/voices.mid",
                          job=job, expected_sha256=previous_merge.get('merged_sha256'),
                          bpm=request.get('bpm'))
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


def subtitles(job, request):
    from subtitles import export_job
    export_job(job, request)


def exports(job, request):
    run_process([resolve_tools(request['tools'])['python'],'-B','-u',str(ROOT/'project_exports.py'),'--job',str(job)],job,'exports')


STEPS = [("metadata", "抓取简介与来源", prepare_metadata), ("download", "下载原视频", download),
         ("audio", "提取原音频和封面", audio_and_cover), ("separation", "分离人声和伴奏", separate),
         ("midi", "生成 MIDI", midi), ('subtitles', '导出字幕', subtitles), ('exports','导出工程与音频',exports)]
REQUIRED = {"metadata": ["source-info.json", "upstream-info.json", "发布简介草稿.txt"],
            "download": ["video/source.mp4", "download-report.json"],
            "audio": ["audio/source.wav", "cover.jpg", "audio-info.json"],
            "separation": ["audio/vocals.wav", "audio/instrumental.wav", "separation-report.json"],
            "midi": ["midi/vocals.mid", "midi/report.json"], 'subtitles': ['subtitles/report.json'], 'exports':['exports/report.json']}


def requirements(key, request):
    if key == 'subtitles':
        return REQUIRED[key]
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


def run(job, until=None):
    job = app_path(job)
    with job_lock(job):
        # Cancel removal happens only after obtaining the lock, so a second
        # launch cannot clear the active job's stop request.
        (Path(job) / "cancel.flag").unlink(missing_ok=True)
        _run(job, until)


def ensure_source_video(job):
    """Fetch media on demand for a metadata-only publication task."""
    job = app_path(job)
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    if request.get('source_kind') == 'local':
        raise ValueError('本地音乐任务没有原视频，请选择自选封面图片。')
    source=job/'video/source.mp4'
    from artifact_cache import valid_receipt
    try:
        state=json.loads((job/'status.json').read_text(encoding='utf-8'))
        step=state.get('steps',{}).get('download',{})
        if (step.get('state')=='done' and source.is_file() and source.stat().st_size>=1024
                and valid_receipt(job,request,'download',requirements('download',request),step.get('receipt'))):
            return source
    except (OSError,ValueError):pass
    run(job, until='download')
    return source


def planned_steps(request, until=None):
    """Resolve one ordered stage plan for CLI, resume and publication calls."""
    from workflow import validate_graph
    if until is not None and until not in {item[0] for item in STEPS}:
        raise ValueError('未知的结束阶段：'+str(until)+'。可选阶段：'+', '.join(item[0] for item in STEPS))
    target = validate_graph(request['workflow_graph']) if 'workflow_graph' in request else 'midi'
    wants_exports=(bool(request.get('project_formats')) and target!='separation') or any(fmt!='wav' for fmt in request.get('audio_formats',['wav']))
    enabled={'metadata','download','audio','separation'}
    if target!='separation' or until in ('midi','subtitles'):
        enabled.add('midi')
    if target=='subtitles' or until=='subtitles':
        enabled.add('subtitles')
    if wants_exports:
        enabled.add('exports')
    steps=[item for item in STEPS if item[0] in enabled]
    if wants_exports:
        target='exports'
    until = until or target
    if until not in enabled:
        raise ValueError('当前工作流未启用 '+until+' 阶段，请检查处理步骤和导出格式。')
    steps = steps[:[item[0] for item in steps].index(until) + 1]
    return steps, target, until


def _run(job, until):
    job = Path(job).resolve()
    from task_contract import read as read_contract, begin_stage, index_stage, fail_stage, reconcile
    read_contract(job)
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    from progress_state import publish
    steps, target, until = planned_steps(request, until)
    enabled = {item[0] for item in steps}
    config = resolve_tools(request["tools"])
    keys = (['python', 'ffmpeg'] if enabled - {'metadata'} else []) + (['vocal2midi'] if 'midi' in enabled else [])
    if 'separation' in enabled and request.get("voice_mode") != "import":
        keys += ["mdx_model", "mdx_metadata"]
    status_file = job / "status.json"
    status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {"steps": {}}
    status["state"] = "running"
    status['schema'] = 1
    status.pop('current_step', None)
    status['run_started'] = time.time()
    status['planned_steps'] = [key for key, _, _ in steps]
    status.pop("error", None)
    status.pop('artifact_error', None)
    atomic_json(status_file, status)
    try:
        from dependency_profiles import task_features
        validate_dependencies(task_features(request, enabled))
        if 'separation' in enabled and request.get('voice_mode') == 'roformer':
            from optional_components import require_ready
            require_ready()
        for key in keys:
            if not config.get(key) or not Path(config[key]).exists():
                raise FileNotFoundError(f"工具路径不存在：{config.get(key, key)}（可编辑 config.json 后重做任务）")
        if 'separation' in enabled and request.get("voice_mode") == "dual":
            for key in ("chorus_model", "chorus_metadata"):
                if not config.get(key) or not (ROOT / config[key]).is_file():
                    raise FileNotFoundError(f"主唱/和声分离模型路径不存在：{config.get(key, key)}")
        for key, title, function in steps:
            if request.get("source_kind") == "local":
                title = {"metadata": "准备本地音乐资料", "download": "导入本地音乐",
                         "audio": "解码本地音乐"}.get(key, title)
            cancelled(job)
            completed = status["steps"].get(key, {}).get("state") == "done"
            intact = all((job / x).is_file() and (job / x).stat().st_size > 0 for x in requirements(key, request))
            from artifact_cache import valid_receipt, receipt
            cached_stages = {'metadata', 'download', 'audio', 'separation', 'subtitles'}
            if key in cached_stages and intact:
                intact = valid_receipt(job, request, key, requirements(key, request),
                                       status['steps'].get(key, {}).get('receipt'))
            if key=='exports' and intact:
                from project_exports import outputs_intact
                intact=outputs_intact(job, request)
            if key == 'subtitles' and intact:
                try:
                    output = json.loads((job/'subtitles/report.json').read_text(encoding='utf-8'))
                    from pathlib import PurePosixPath
                    intact = bool(output.get('outputs')) and all(
                        PurePosixPath(name).parts[:1] == ('subtitles',) and '..' not in PurePosixPath(name).parts
                        and (job/name).is_file() and (job/name).stat().st_size > 0 for name in output['outputs'])
                    if intact and output.get('sha256'):
                        intact = all(output['sha256'].get(name) == file_hash(job / name) for name in output['outputs'])
                except (OSError, ValueError, TypeError):
                    intact = False
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
                if key in cached_stages and not status['steps'][key].get('receipt'):
                    status['steps'][key]['receipt'] = receipt(job, request, key, requirements(key, request))
                index_stage(job, key, requirements(key, request), receipt=status['steps'][key].get('receipt'))
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
                status["steps"][key] = {"state": "running", 'started': time.time()}
                atomic_json(status_file, status)
                begin_stage(job, key)
                publish(job, key, title)
                print(f"\n正在{title}……", flush=True)
                started = time.monotonic()
                function(job, request)
                status["steps"][key] = {"state": "done", "seconds": round(time.monotonic() - started, 2)}
                if key in cached_stages:
                    status['steps'][key]['receipt'] = receipt(job, request, key, requirements(key, request))
                index_stage(job, key, requirements(key, request), receipt=status['steps'][key].get('receipt'), replace=True)
                atomic_json(status_file, status)
            if key == until:
                break
        status["state"] = "done" if until == target else "partial"
        status.pop("error", None)
        status.pop("current_step", None)
        atomic_json(status_file, status)
        reconcile(job)
        print(f"\n本轮处理完成：{job}", flush=True)
    except BaseException as exc:
        status["state"] = "cancelled" if isinstance(exc, InterruptedError) else "failed"
        status["error"] = str(exc)
        current = status.get('current_step')
        if current:
            status['steps'][current].update(state=status['state'], error=str(exc))
        atomic_json(status_file, status)
        if current:
            try:
                fail_stage(job, current, status['state'], exc)
            except Exception as record_error:
                status['artifact_error'] = str(record_error)
                atomic_json(status_file, status)
        raise
    finally:
        status['elapsed_seconds'] = status.get('elapsed_seconds', 0) + max(0, time.time() - status.pop('run_started', time.time()))
        atomic_json(status_file, status)


def main():
    parser = argparse.ArgumentParser(description="BV / 本地音乐 → 分离 → Vocal2Midi")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", help="BV 号或 B站链接")
    inputs.add_argument("--audio-file", help="直接导入本地音乐")
    inputs.add_argument("--job", help="继续已有任务")
    parser.add_argument("--language", choices=("zh", "ja"), default="zh")
    parser.add_argument("--lyrics-file")
    parser.add_argument("--notes-only", action="store_true")
    parser.add_argument('--output-dir', help='Parent directory for a new task; defaults to the application jobs folder')
    parser.add_argument('--zh-lyric-mode', choices=('hanzi', 'pinyin'), default='hanzi')
    parser.add_argument("--voice-mode", choices=("single", "dual", "import", "roformer"), default="dual")
    parser.add_argument('--roformer-device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument('--separation-flow', choices=('cascade','independent'), default='cascade')
    parser.add_argument('--calibration-profile', type=Path, help='可选的声库试音校准配置')
    parser.add_argument("--lead-wav")
    parser.add_argument("--backing-wav")
    parser.add_argument("--instrumental-wav")
    parser.add_argument("--midi-steps", type=int, choices=(8, 16, 32), default=16)
    parser.add_argument('--bpm', type=float, help='Constant export tempo, 20–400 BPM; omitted: 120 BPM time reference')
    parser.add_argument('--midi-backend',choices=('v1','v2'),default='v2')
    parser.add_argument('--project-formats',nargs='+',choices=('ustx','vsqx','svp'),default=[])
    audio_export = parser.add_mutually_exclusive_group()
    audio_export.add_argument('--audio-formats',nargs='+',choices=('wav','flac','mp3','ogg'),default=['wav'])
    audio_export.add_argument('--no-audio-export',action='store_true',help='Keep internal WAV stems without selecting audio exports')
    parser.add_argument('--no-pitch-curve',action='store_true')
    parser.add_argument('--reference-dynamics', action='store_true', help='Transfer reference dynamics to SVP loudness (experimental)')
    parser.add_argument('--dynamics-strength', type=float, default=.6, help='Reference dynamics amount, from 0 to 1')
    parser.add_argument('--expression-density', choices=('original','dense'), default='dense', help='SVP expression sampling; dense preserves the original linear contour at 5 ms')
    parser.add_argument('--performance-style',choices=('none','soft_ballad','intimate_narration','power_pop'),default='none',help='Independent experimental SVP style candidate')
    parser.add_argument('--style-strength',type=float,default=.6)
    parser.add_argument('--style-scope',choices=('lead','all_vocals'),default='lead')
    parser.add_argument('--style-pitch',action='store_true',help='Optional structural pitch style gestures')
    parser.add_argument('--prepare-only',action='store_true',help='Prepare score/projects; tune in the independent tuning workflow')
    parser.add_argument('--reference-tension', action='store_true', help='Add relative SVP tension (experimental; WORLD component required)')
    parser.add_argument('--reference-breathiness', action='store_true', help='Add relative SVP breathiness (experimental)')
    parser.add_argument('--reference-voicing', action='store_true', help='Add relative SVP voicing (experimental)')
    parser.add_argument('--netease-song',help='NetEase song ID or URL for reference lyrics')
    parser.add_argument("--until", choices=[x[0] for x in STEPS])
    args = parser.parse_args()
    try:
        if args.job:
            job = Path(args.job)
        else:
            reference=None
            if args.netease_song:
                from lyrics_sources import fetch
                reference=fetch(args.netease_song)
            elif args.lyrics_file:
                from lyrics_sources import from_file
                reference=from_file(args.lyrics_file)
            lyrics=reference.text if reference else ''
            job = create_job(args.source or "", lyrics, args.language, not args.notes_only,
                             voice_mode=args.voice_mode, midi_steps=args.midi_steps, bpm=args.bpm,
                             roformer_device=args.roformer_device,
                             separation_flow=args.separation_flow,reference_calibration=args.calibration_profile,
                             zh_lyric_mode=args.zh_lyric_mode,
                             parent=args.output_dir,
                             local_music=args.audio_file,
                             midi_backend=args.midi_backend,project_formats=args.project_formats,
                             audio_formats=[] if args.no_audio_export else args.audio_formats,
                             export_pitch=bool(args.project_formats) and not args.no_pitch_curve,
                             reference_dynamics=args.reference_dynamics,dynamics_strength=args.dynamics_strength,expression_density=args.expression_density,
                             performance_style=args.performance_style,style_strength=args.style_strength,style_scope=args.style_scope,style_pitch=args.style_pitch,prepare_only=args.prepare_only,
                             reference_tension=args.reference_tension,reference_breathiness=args.reference_breathiness,reference_voicing=args.reference_voicing,
                             lyric_reference={'origin':reference.origin} if reference else None,
                             reference_mode='netease' if args.netease_song else 'local',
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
