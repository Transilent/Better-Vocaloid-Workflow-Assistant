"""Small, dependency-free facade over vwa_paths, vwa_io, and vwa_net plus job/signature helpers."""
import json
import hashlib
import os
import re
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path

from vwa_paths import (
    ROOT,
    TOOL_PATHS,
    app_path,
    canonical_kind,
    dependency_manifest,
    environment_value,
    legacy_tool_paths,
    native_path,
    saved_path,
)
from vwa_io import (
    SidecarBusy,
    _write_json,
    atomic_json,
    bundled_export_module,
    cancellation_requested,
    cancelled,
    checked_tree,
    exclusive_sidecar,
    file_hash,
    fingerprint,
    locked,
    preserve_changed_file,
)
import vwa_net

BV_PATTERN = re.compile(r"(?<![A-Za-z0-9])BV1[A-Za-z0-9]{9}(?![A-Za-z0-9])")


def network_proxy_mode():
    """Return 'system' only when explicitly enabled in config.json; default stays 'direct'."""
    try:
        mode = load_config(False).get('network_proxy_mode', 'direct')
        return 'system' if mode == 'system' else 'direct'
    except (OSError, ValueError, TypeError):
        return 'direct'


def direct_urlopen(request, timeout=30, *, allow_system_proxy=False):
    """Open public metadata/downloads; honor system proxy only when explicitly opted in."""
    if allow_system_proxy and network_proxy_mode() == 'system':
        return urllib.request.urlopen(request, timeout=timeout)
    return vwa_net.direct_urlopen(request, timeout=timeout, allow_system_proxy=False)


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


def extract_bv(text):
    ids = list(dict.fromkeys(BV_PATTERN.findall(text)))
    if len(ids) != 1:
        raise ValueError("请输入一个完整 BV 号或一个 B站视频链接（BV 号区分大小写）。")
    return ids[0]


def clean_lyrics(text, *, preserve_lines=False, language='zh'):
    # Content selection must happen before this function: removing punctuation
    # alone cannot distinguish actual lyrics from author names or STAFF.
    if preserve_lines:
        return '\n'.join(line for raw in text.splitlines() if (line := clean_lyrics(raw, language=language)))
    if language == 'en':
        from preparation_languages import english_words
        return ' '.join(english_words(text))
    return "".join(c for c in unicodedata.normalize("NFC", text)
                   if unicodedata.category(c)[0] in {"L", "N", "M"})


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


@lru_cache(maxsize=1)
def pinned_files():
    return {item['file']: item for item in dependency_manifest().get('entries', [])}


@lru_cache(maxsize=128)
def _custom_hash(path, size, modified):
    return file_hash(path)


def model_identity(value):
    """Use the verified distribution catalog for immutable bundled weights.

    Custom models are hashed once per size/mtime within the worker. The separate
    dependency check remains responsible for verifying the installed catalog.
    """
    path = Path(value).resolve()
    catalog = pinned_files()
    if path.is_relative_to(ROOT):
        name = path.relative_to(ROOT).as_posix()
        entries = [item for key, item in catalog.items()
                   if key == name or key.startswith(name + '/')]
        if entries:
            return [(item['file'].removeprefix(name), item['sha256'],
                     (ROOT / item['file']).stat().st_size) for item in sorted(entries, key=lambda x: x['file'])]
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    return [(p.relative_to(path).as_posix() if path.is_dir() else p.name,
             _custom_hash(str(p), p.stat().st_size, p.stat().st_mtime_ns))
            for p in files if p.is_file()]


def inference_inputs(job, request, voice):
    import configparser
    tools = resolve_tools(request['tools'])
    backend = Path(tools['vocal2midi'])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(backend / 'settings/vocal2midi.ini', encoding='utf-8-sig')
    settings = dict(parser['General']) if parser.has_section('General') else {}
    # These are the values actually consumed by midi_bridge. GUI, export-list,
    # download and publishing preferences do not affect recognition.
    defaults = {'t0': 0., 'slice_min_sec': 5., 'slice_max_sec': 10.,
                'round_pitch': True, 'seg_thresh': .2, 'seg_rad': .02,
                'est_thresh': .2, 'batch_size': 1, 'asr_batch': 2}
    effective = {}
    for key, default in defaults.items():
        raw = settings.get(key, str(default))
        effective[key] = raw.lower() in ('true', '1') if isinstance(default, bool) else type(default)(raw)
    effective['nsteps'] = request.get('midi_steps', int(settings.get('nsteps', 8)))
    from music_timing import effective_bpm
    effective['tempo'] = effective_bpm(request)
    recognize = request.get('recognize_lyrics', True)
    pitch = bool(request.get('export_pitch') and request.get('project_formats'))
    analyze_pitch = pitch or (backend / 'backend-version.json').is_file()
    from preparation_languages import HUBERTFA_MODEL, PROFILES, lyric_output_mode
    language = request['language']
    models = {'game_model': 'experiments/GAME-1.0.3-medium-onnx'}
    if recognize:
        profile = PROFILES[language]
        models['hfa_model'] = HUBERTFA_MODEL
        models[profile.asr_settings_key] = profile.asr_model
    if analyze_pitch:
        models['rmvpe_model'] = 'experiments/RMVPE/rmvpe.onnx'
    identities = {}
    for key, default in models.items():
        path = Path(settings.get(key, default))
        candidate = path if path.is_absolute() else backend / path
        if not candidate.exists() and not path.is_absolute() and (backend / 'backend-version.json').is_file():
            candidate = ROOT / 'dependencies/vocal2midi' / path
        identities[key] = model_identity(candidate)
    code = [(str(p.relative_to(backend)), file_hash(p))
            for directory in ('application', 'inference') for p in sorted((backend / directory).rglob('*.py'))]
    cross_voice = {}
    if voice == 'backing' and analyze_pitch and (backend / 'backend-version.json').is_file():
        peer = Path(job) / 'midi/lead/lead.pitch.npz'
        partition = Path(job) / 'separation-report.json'
        cross_voice = {'lead_signature': fingerprint(inference_inputs(job, request, 'lead')),
                       'lead_pitch': file_hash(peer) if peer.is_file() else None,
                       'partition_report': file_hash(partition) if partition.is_file() else None,
                       'adapter': file_hash(ROOT / 'vocal_partition.py')}
    return {'schema': 8, 'audio': file_hash(Path(job) / 'audio' / (voice + '.wav')),
            **({'cross_voice': cross_voice} if cross_voice else {}),
            'worker_code': {name: file_hash(ROOT / name) for name in ('midi_bridge.py', 'v2m_runtime.py', 'music_timing.py')},
            **({'pitch_adapter': file_hash(ROOT / 'pitch_accuracy.py')} if analyze_pitch and (backend / 'backend-version.json').is_file() else {}),
            **({'transcription_adapter': file_hash(ROOT / 'transcription_quality.py')}
               if (backend / 'backend-version.json').is_file() else {}),
            'voice': voice, 'language': language, 'recognize_lyrics': recognize,
            'lyrics': request.get('backing_lyrics', '') if voice == 'backing' else request.get('lyrics', ''),
            'lyric_mode': lyric_output_mode(request, settings.get),
            'native_project': bool(request.get('project_formats')), 'pitch': pitch,
            'alignment': request.get('alignment_engine', 'hfa'), 'models': identities, 'code': code,
            'parameters': effective, 'device': tools.get('midi_device', 'dml'), 'adapter': tools.get('dml_adapter', 'auto')}


def midi_signature(job, request, voice):
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
