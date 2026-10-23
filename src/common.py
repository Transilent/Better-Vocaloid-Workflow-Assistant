"""Small, dependency-free helpers for the desktop app and pipeline."""
import json
import hashlib
import re
import unicodedata
import urllib.request
import threading
import weakref
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BV_PATTERN = re.compile(r"(?<![A-Za-z0-9])BV1[A-Za-z0-9]{9}(?![A-Za-z0-9])")
_ATOMIC_LOCKS = weakref.WeakValueDictionary()
_ATOMIC_GUARD = threading.Lock()


TOOL_PATHS = ("python", "vocal2midi", "ffmpeg", "mdx_model", "mdx_metadata",
              "chorus_model", "chorus_metadata", "publish_browser_executable")


def native_path(path):
    """Use extended Windows paths only at filesystem/cache API boundaries."""
    import os
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    if value.startswith('\\\\'):
        return '\\\\?\\UNC\\' + value[2:]
    return '\\\\?\\' + value


def direct_urlopen(request, timeout=30):
    """Open public metadata/downloads without environment or system proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


@lru_cache(maxsize=1)
def dependency_manifest():
    manifest = ROOT / "dependencies/manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}


def legacy_tool_paths():
    return dependency_manifest().get("legacy_tool_paths", {})


def app_path(value):
    """Resolve saved internal paths, including pre-consolidation history."""
    text = str(value).strip().strip('"')
    if text.startswith("@/"):
        path = (ROOT / text[2:]).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("助手内部路径超出目录。")
        return path
    path = Path(text).expanduser()
    legacy = dependency_manifest().get("legacy_application_root")
    if legacy and path.is_absolute() and path.is_relative_to(Path(legacy)):
        path = ROOT / path.relative_to(Path(legacy))
    return path.resolve()


def saved_path(value):
    path = app_path(value)
    return "@/" + path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def workspace_state():
    try:
        data = json.loads((ROOT / 'cache/workspace.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def output_directory(value=None):
    """Resolve the chosen output parent; an empty field uses the bundled jobs folder."""
    if value is None:
        value = workspace_state().get('output_directory', '')
        if not isinstance(value, str):
            value = ''
    text = str(value).strip().strip('"')
    if not text:
        return ROOT / 'jobs'
    if text.startswith('@/'):
        return app_path(text)
    path = Path(text).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def save_output_directory(value):
    directory = output_directory(value)
    state = workspace_state()
    state['output_directory'] = saved_path(directory)
    roots = state.get('job_roots', [])
    roots = [r for r in roots if isinstance(r, str)] if isinstance(roots, list) else []
    state['job_roots'] = list(dict.fromkeys(roots + [saved_path(directory)]))
    atomic_json(ROOT / 'cache/workspace.json', state)


def remember_job(job):
    job = app_path(job)
    state = workspace_state()
    roots = state.get('job_roots', [])
    roots = [r for r in roots if isinstance(r, str)] if isinstance(roots, list) else []
    state['job_roots'] = list(dict.fromkeys(roots + [saved_path(job.parent)]))
    atomic_json(ROOT / 'cache/workspace.json', state)
    (ROOT / 'last_job.txt').write_text(saved_path(job), encoding='utf-8')


def job_directories():
    """Read immediate task folders from the default and remembered output parents."""
    state = workspace_state()
    roots = {ROOT / 'jobs', output_directory()}
    history = state.get('job_roots', [])
    for value in history if isinstance(history, list) else []:
        if isinstance(value, str):
            try:
                roots.add(output_directory(value))
            except (OSError, ValueError):
                continue
    try:
        last = (ROOT / 'last_job.txt').read_text(encoding='utf-8').strip()
        if last:
            roots.add(app_path(last).parent)
    except (OSError, ValueError):
        pass
    jobs = set()
    import os
    for directory in roots:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if (entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
                            and not getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & 1024
                            and (Path(entry.path) / 'request.json').is_file()):
                        jobs.add(Path(entry.path).absolute())
        except OSError:
            continue
    return sorted(jobs, key=lambda p: p.name, reverse=True)


def load_config(resolve_paths=True):
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    return resolve_tools(config) if resolve_paths else config


def resolve_tools(tools):
    """Resolve portable paths and redirect only known historical installations.

    Requests keep their original values, so migration does not change completed
    MIDI signatures. An explicitly selected custom or missing tool stays selected.
    """
    current = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    legacy = legacy_tool_paths()
    result = dict(tools)
    for key in TOOL_PATHS:
        value = result.get(key)
        if not value:
            continue
        if key in legacy and str(value).replace("\\", "/").casefold() == str(legacy[key]).replace("\\", "/").casefold():
            value = current.get(key, value)
        path = Path(value)
        result[key] = str(path if path.is_absolute() else ROOT / path)
    return result


def preserve_changed_file(job, path, expected_sha256=None, replacement_sha256=None):
    """Keep edits before regenerating an output; intact generated files need no copy."""
    import datetime as dt
    import shutil
    import uuid
    from task_store import checked_tree
    job, path = Path(job).resolve(), Path(path).absolute()
    checked_tree(path, job)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    current = file_hash(path)
    if current in (expected_sha256, replacement_sha256):
        return None
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%y%m%d-%H%M%S')
    backup = job/'backups'/(stamp+'-'+uuid.uuid4().hex[:6]+'_'+path.stem[:60]+path.suffix)
    checked_tree(backup, job)
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path, backup)
    if file_hash(backup) != current or file_hash(path) != current:
        raise OSError('备份期间文件发生变化，请重试：'+path.name)
    relative = backup.relative_to(job).as_posix()
    print('已保留原文件：'+relative,flush=True)
    return relative


def extract_bv(text):
    ids = list(dict.fromkeys(BV_PATTERN.findall(text)))
    if len(ids) != 1:
        raise ValueError("请输入一个完整 BV 号或一个 B站视频链接（BV 号区分大小写）。")
    return ids[0]


def clean_lyrics(text, *, preserve_lines=False):
    # Content selection must happen before this function: removing punctuation
    # alone cannot distinguish actual lyrics from author names or STAFF.
    if preserve_lines:
        return '\n'.join(line for raw in text.splitlines() if (line := clean_lyrics(raw)))
    return "".join(c for c in unicodedata.normalize("NFC", text)
                   if unicodedata.category(c)[0] in {"L", "N", "M"})


def atomic_json(path, data):
    path = Path(path)
    Path(native_path(path.parent)).mkdir(parents=True, exist_ok=True)
    key = str(path.absolute()).casefold()
    with _ATOMIC_GUARD:
        lock = _ATOMIC_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ATOMIC_LOCKS[key] = lock
    with lock:
        _write_json(path, data)


def _write_json(path, data):
    import os
    import tempfile
    import time
    # Concurrent progress writers must not share a temporary filename.
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=native_path(path.parent),
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
        for attempt in range(6):
            try:
                os.replace(name, native_path(path))
                break
            except OSError as error:
                if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.01 * 2**attempt)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def cancelled(job):
    if (Path(job) / "cancel.flag").exists():
        raise InterruptedError("已取消任务；已完成的步骤可以继续使用。")


def file_hash(path):
    digest = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_midi_signature(job, request, voice):
    """Bind a completed voice to its audio and extraction parameters."""
    settings = Path(resolve_tools(request["tools"])["vocal2midi"]) / "settings/vocal2midi.ini"
    data = {"adapter_version": 4, "audio": file_hash(Path(job) / "audio" / (voice + ".wav")),
            "voice": voice, "language": request["language"],
            "lyrics": request.get("backing_lyrics", "") if voice == "backing" else request.get("lyrics", ""),
            "recognize_lyrics": request.get("recognize_lyrics", True),
            "midi_steps": request.get("midi_steps"), "tools": request["tools"],
            "settings": file_hash(settings) if settings.is_file() else None}
    if request.get('midi_backend') == 'v2' or request.get('project_formats'):
        data.update(adapter_version=5, project_formats=request.get('project_formats',[]),
                    export_pitch=request.get('export_pitch',False),alignment_engine=request.get('alignment_engine','hfa'))
    if request['language'] == 'zh' and 'zh_lyric_mode' in request:
        data['zh_lyric_mode'] = request['zh_lyric_mode']
    if request.get('bpm') is not None:
        from music_timing import effective_bpm
        data['bpm'] = effective_bpm(request)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def midi_signature(job, request, voice):
    from artifact_cache import fingerprint, inference_inputs
    return fingerprint(inference_inputs(job, request, voice))


def completed_voice(job, request, voice):
    folder = Path(job) / "midi" if voice == "vocals" else Path(job) / "midi" / voice
    try:
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        if request.get('project_formats'):
            for suffix in (('ustx','vsqx') if request.get('midi_backend')=='v2' else ('ustx',)):
                artifact = folder / (voice + '.' + suffix)
                if not artifact.is_file() or artifact.stat().st_size == 0:
                    return False
                if report.get('native_sha256',{}).get(suffix) not in (None,file_hash(artifact)):
                    return False
        if report.get('pitch_source')=='absolute_rmvpe':
            artifact=folder/(voice+'.pitch.npz')
            if not artifact.is_file() or report.get('output_sha256',{}).get('pitch.npz') != file_hash(artifact):
                return False
        matches = report.get('input_signature') == midi_signature(job, request, voice)
        if not matches and 'cache_schema' not in report:
            matches = report.get('input_signature') == legacy_midi_signature(job, request, voice)
        return matches and report.get("midi_sha256") == file_hash(folder / (voice + ".mid"))
    except (OSError, ValueError, KeyError):
        return False


def environment_value(name, default=None):
    """Prefer VWA environment names and accept released BVWA callers."""
    import os
    if name in os.environ:
        return os.environ[name]
    legacy = 'B' + name if name.startswith('VWA_') else name
    return os.environ.get(legacy, default)


def canonical_kind(value):
    """Compare saved legacy record types without rewriting their signed bytes."""
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value
