"""Small, dependency-free helpers for the desktop app and pipeline."""
import json
import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BV_PATTERN = re.compile(r"(?<![A-Za-z0-9])BV1[A-Za-z0-9]{9}(?![A-Za-z0-9])")


TOOL_PATHS = ("python", "vocal2midi", "ffmpeg", "mdx_model", "mdx_metadata",
              "chorus_model", "chorus_metadata", "publish_browser_executable")


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


def clean_lyrics(text):
    # Content selection must happen before this function: removing punctuation
    # alone cannot distinguish actual lyrics from author names or STAFF.
    return "".join(c for c in unicodedata.normalize("NFC", text)
                   if unicodedata.category(c)[0] in {"L", "N", "M"})


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def cancelled(job):
    if (Path(job) / "cancel.flag").exists():
        raise InterruptedError("已取消任务；已完成的步骤可以继续使用。")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def midi_signature(job, request, voice):
    """Bind a completed voice to its audio and extraction parameters."""
    settings = Path(resolve_tools(request["tools"])["vocal2midi"]) / "settings/vocal2midi.ini"
    data = {"adapter_version": 4, "audio": file_hash(Path(job) / "audio" / (voice + ".wav")),
            "voice": voice, "language": request["language"],
            "lyrics": request.get("backing_lyrics", "") if voice == "backing" else request.get("lyrics", ""),
            "recognize_lyrics": request.get("recognize_lyrics", True),
            "midi_steps": request.get("midi_steps"), "tools": request["tools"],
            "settings": file_hash(settings)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def completed_voice(job, request, voice):
    folder = Path(job) / "midi" if voice == "vocals" else Path(job) / "midi" / voice
    try:
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        return (report.get("input_signature") == midi_signature(job, request, voice)
                and report.get("midi_sha256") == file_hash(folder / (voice + ".mid")))
    except (OSError, ValueError, KeyError):
        return False
