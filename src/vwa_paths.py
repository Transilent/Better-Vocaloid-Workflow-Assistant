"""Zero-cycle application path, manifest, and environment helpers."""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL_PATHS = (
    "python",
    "vocal2midi",
    "ffmpeg",
    "mdx_model",
    "mdx_metadata",
    "chorus_model",
    "chorus_metadata",
    "publish_browser_executable",
)


def native_path(path) -> str:
    """Use extended Windows paths only at filesystem/cache API boundaries."""
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


@lru_cache(maxsize=1)
def dependency_manifest() -> dict:
    manifest = ROOT / "dependencies/manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}


def legacy_tool_paths() -> dict:
    return dependency_manifest().get("legacy_tool_paths", {})


def app_path(value) -> Path:
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


def saved_path(value) -> str:
    path = app_path(value)
    return "@/" + path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def environment_value(name: str, default=None):
    """Prefer VWA environment names and accept released BVWA callers."""
    if name in os.environ:
        return os.environ[name]
    legacy = "B" + name if name.startswith("VWA_") else name
    return os.environ.get(legacy, default)


def canonical_kind(value):
    """Compare saved legacy record types without rewriting their signed bytes."""
    return "vwa." + value[5:] if isinstance(value, str) and value.startswith("bvwa.") else value
