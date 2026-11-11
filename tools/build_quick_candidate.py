"""Build a reproducible, unpackable Quick engineering candidate ZIP.

The fixed CLI is:
    --source-root --python-runtime --wheel-dir --output

An optional --workflow-resources installed-root switches on a workflow candidate.
It copies only files named by the frozen dependency manifest, verifies each
copied file's declared size and SHA-256, and never walks or copies user state.
The default workflow profile covers local import through SVP preparation; --full
adds the optional language and publishing resource groups.

This builder never installs packages, uses the network, mutates the source tree,
or packages user state. It copies application files selected from
packaging/portable-files.json, a trimmed CPython runtime, only the NumPy 1.26.4
installation roots, and the two hash-locked Qt 6.10.3 wheels. Build-time child
processes verify NumPy ABI/BLAS loading and the required Qt Quick QML imports.
When workflow resources are enabled, the frozen installed runtime supplies the
processing Python packages and models instead of the component-only trim.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


QT_VERSION = "6.10.3"
NUMPY_VERSION = "1.26.4"
PYTHON_SERIES = (3, 12)
PACKAGE_ROOT = "VWA"
CANDIDATE_MANIFEST = "quick-candidate.json"
SOURCE_MANIFEST = "packaging/portable-files.json"
LOCK_FILE = "packaging/requirements-quick.lock.txt"
PYTHON_TARGET = "dependencies/vocal2midi/python"
QT_TARGET = "runtime/qt6"
ZIP_DATE = (2026, 11, 11, 0, 0, 0)
CHUNK = 4 * 1024 * 1024
WORKFLOW_FEATURES = ("core", "midi", "alignment", "chinese", "japanese",
                     "pitch", "projects", "publishing")
DEFAULT_WORKFLOW_FEATURES = frozenset({"core", "midi", "alignment", "pitch", "projects"})
RESOURCE_ROOTS = frozenset({"dependencies", "models", "vendor"})
PRIVATE_RESOURCE_PARTS = frozenset({
    ".git", "cache", "jobs", "accounts", "publish-browser", "quality-test",
    "user-presets", "work",
})
RUNTIME_SITE_PACKAGES = "dependencies/vocal2midi/python/Lib/site-packages/"
QT5_UI_PACKAGES = (
    "pyqt5", "pyqt5-", "pyqt5_", "pyqt_fluent_widgets-", "qfluentwidgets",
    "qframelesswindow", "pyqt5_frameless_window-", "darkdetect",
)
PACKAGE_MANAGER_PATHS = (
    "pip", "pip-", "setuptools", "setuptools-", "wheel", "wheel-",
    "_distutils_hack", "distutils-precedence.pth",
)
MINIMUM_WORKFLOW_FILES = (
    "dependencies/ffmpeg/ffmpeg.exe",
    "dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx",
    "dependencies/models/mdx-model-data.json",
    "dependencies/vocal2midi/settings/vocal2midi.ini",
    "dependencies/vocal2midi2/backend-version.json",
    "dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx",
    "dependencies/vocal2midi2/application/pipeline.py",
    "dependencies/vocal2midi2/inference/pipeline/auto_lyric_hybrid.py",
    "dependencies/export/libresvip/__init__.py",
    "models/UVR_MDXNET_KARA_2.onnx",
    "models/mdx-model-data.json",
)
MINIMUM_WORKFLOW_PREFIXES = (
    "dependencies/vocal2midi/experiments/1218_hfa_model_new_dict/",
    "dependencies/vocal2midi/experiments/GAME-1.0.3-medium-onnx/",
)

EXPECTED_WHEELS = {
    "pyside6-essentials": "pyside6_essentials-6.10.3-cp39-abi3-win_amd64.whl",
    "shiboken6": "shiboken6-6.10.3-cp39-abi3-win_amd64.whl",
}
REQUIRED_QT_FILES = (
    "PySide6/QtCore.pyd",
    "PySide6/QtGui.pyd",
    "PySide6/QtQml.pyd",
    "PySide6/QtQuick.pyd",
    "PySide6/QtQuickControls2.pyd",
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/qml/QtQml/qmldir",
    "PySide6/qml/QtQuick/qmldir",
    "PySide6/qml/QtQuick/Window/qmldir",
    "PySide6/qml/QtQuick/Layouts/qmldir",
    "PySide6/qml/QtQuick/Templates/qmldir",
    "PySide6/qml/QtQuick/Controls/qmldir",
    "PySide6/qml/QtQuick/Controls/Basic/qmldir",
    "PySide6/qml/QtQuick/Dialogs/qmldir",
    "PySide6/qml/QtQuick/Effects/qmldir",
    "PySide6/qml/QtQuick/Shapes/qmldir",
    "shiboken6/shiboken6.abi3.dll",
)
QUICK_APP_NAMES = {
    "应用平面.py",
    "application_plane.py",
    "application-plane.py",
    "quick_app.py",
}
FORBIDDEN_TARGET_ROOTS = {
    ".git",
    "cache",
    "jobs",
    "publish-packages",
    "quality-test",
    "user-presets",
    "work",
}



class CandidateError(RuntimeError):
    """A candidate input or invariant is not suitable for packaging."""


@dataclass(frozen=True)
class LockedWheel:
    project: str
    version: str
    sha256: str
    filename: str


@dataclass(frozen=True)
class PackagedFile:
    target: str
    bytes: int
    sha256: str
    origin: str


@dataclass(frozen=True)
class ResourceEntry:
    file: str
    bytes: int
    sha256: str
    feature: str


@dataclass(frozen=True)
class WorkflowResourcePlan:
    root: Path
    manifest: Path
    portable_manifest: Path
    profile: str
    manifest_sha256: str
    portable_manifest_sha256: str
    entries: tuple[ResourceEntry, ...]
    omitted: tuple[ResourceEntry, ...]
    blocked: tuple[ResourceEntry, ...]
    blocked_categories: tuple[tuple[str, int, int], ...]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            value.update(chunk)
    return value.hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CandidateError(f"Cannot read JSON {path}: {exc}") from exc


def resource_feature(relative: str) -> str:
    """Mirror the application's feature groups for the reviewed resource plan."""
    if relative.startswith(("dependencies/browser/", "vendor/")):
        return "publishing"
    if relative.startswith("dependencies/export/"):
        return "projects"
    if "/experiments/Qwen3-ASR-1.7B-dml/" in relative:
        return "chinese"
    if "/experiments/romajiASR/" in relative:
        return "japanese"
    if "/experiments/1218_hfa_model_new_dict/" in relative:
        return "alignment"
    if "/experiments/RMVPE/" in relative:
        return "pitch"
    if "/experiments/GAME-" in relative:
        return "midi"
    if relative.startswith(RUNTIME_SITE_PACKAGES):
        top = relative[len(RUNTIME_SITE_PACKAGES):].split("/", 1)[0].casefold()
        if top.startswith(("pyopenjtalk",)):
            return "japanese"
    return "core"


def blocked_runtime_category(relative: str):
    """Return the exclusion class for UI bindings and package-manager files."""
    if not relative.startswith(RUNTIME_SITE_PACKAGES):
        return None
    top = relative[len(RUNTIME_SITE_PACKAGES):].split("/", 1)[0].casefold()
    if top.startswith(QT5_UI_PACKAGES):
        return "qt5-ui"
    if any(top == item or (item.endswith("-") and top.startswith(item))
           for item in PACKAGE_MANAGER_PATHS):
        return "installer-tooling"
    return None


def entry_summary(entries: Iterable[ResourceEntry]):
    rows = {
        feature: {"files": 0, "bytes": 0}
        for feature in WORKFLOW_FEATURES
    }
    total = {"files": 0, "bytes": 0}
    for entry in entries:
        row = rows[entry.feature]
        row["files"] += 1
        row["bytes"] += entry.bytes
        total["files"] += 1
        total["bytes"] += entry.bytes
    return {"total": total, "features": rows}


def load_workflow_resources(resource_root: Path, source_root: Path, runtime: Path,
                            *, full: bool) -> WorkflowResourcePlan:
    """Validate a frozen installed dependency root without walking user state."""
    try:
        root = resource_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise CandidateError(f"--workflow-resources is unavailable: {exc}") from exc
    if not root.is_dir():
        raise CandidateError("--workflow-resources must be an installed application directory")

    frozen_path = checked_source(source_root, "dependencies/manifest.json")
    manifest_path = checked_source(root, "dependencies/manifest.json")
    manifest_sha256 = digest(manifest_path)
    if manifest_sha256 != digest(frozen_path):
        raise CandidateError(
            "--workflow-resources dependencies/manifest.json does not match the frozen source manifest"
        )

    portable_relative = next(
        (name for name in ("portable-files.json", "packaging/portable-files.json")
         if (root / name).is_file()),
        None,
    )
    if portable_relative is None:
        raise CandidateError("--workflow-resources must contain portable-files.json")
    portable_path = checked_source(root, portable_relative)
    portable = read_json(portable_path)
    if portable.get("format") != 1 or not isinstance(portable.get("entries"), list):
        raise CandidateError("Installed portable-files.json must use format 1 and an entries array")
    portable_mapping = {}
    for index, item in enumerate(portable["entries"]):
        if not isinstance(item, dict):
            raise CandidateError(f"Installed portable-files entry {index} is not an object")
        target = checked_target(item.get("file"), f"installed portable-files[{index}].file")
        source = clean_relative(item.get("source"), f"installed portable-files[{index}].source")
        folded = target.casefold()
        if folded in portable_mapping:
            raise CandidateError(f"Duplicate installed portable destination: {target}")
        portable_mapping[folded] = source
    if portable_mapping.get("dependencies/manifest.json") != "dependencies/manifest.json":
        raise CandidateError("Installed portable-files.json must preserve dependencies/manifest.json")

    expected_runtime = checked_source(root, PYTHON_TARGET, directory=True)
    runtime = resolve_python_runtime(runtime)
    if runtime != expected_runtime:
        raise CandidateError(
            "--python-runtime must be the exact Python runtime under --workflow-resources"
        )

    manifest = read_json(manifest_path)
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CandidateError("Frozen dependency manifest must contain a non-empty entries array")
    if "files" in manifest and manifest["files"] != len(raw_entries):
        raise CandidateError("Frozen dependency manifest file count is inconsistent")

    selected = []
    omitted = []
    blocked = []
    blocked_counts = {}
    seen = set()
    declared_total = 0
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise CandidateError(f"dependency manifest entry {index} is not an object")
        relative = clean_relative(item.get("file"), f"dependency manifest[{index}].file")
        folded = relative.casefold()
        if folded in seen:
            raise CandidateError(f"Duplicate dependency manifest path: {relative}")
        seen.add(folded)
        root_name = relative.split("/", 1)[0].casefold()
        if root_name not in RESOURCE_ROOTS:
            raise CandidateError(f"Resource path is outside the reviewed roots: {relative}")
        if any(part.casefold() in PRIVATE_RESOURCE_PARTS for part in relative.split("/")):
            raise CandidateError(f"Resource manifest points at private/runtime state: {relative}")
        size = item.get("bytes")
        sha256 = item.get("sha256")
        if type(size) is not int or size < 0:
            raise CandidateError(f"Invalid dependency size: {relative}")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise CandidateError(f"Invalid dependency SHA-256: {relative}")
        declared_total += size
        feature = resource_feature(relative)
        entry = ResourceEntry(relative, size, sha256.lower(), feature)
        category = blocked_runtime_category(relative)
        if category:
            blocked.append(entry)
            row = blocked_counts.setdefault(category, [0, 0])
            row[0] += 1
            row[1] += size
        elif full or feature in DEFAULT_WORKFLOW_FEATURES:
            selected.append(entry)
        else:
            omitted.append(entry)
    if "bytes" in manifest and manifest["bytes"] != declared_total:
        raise CandidateError("Frozen dependency manifest byte total is inconsistent")
    if not selected:
        raise CandidateError("Workflow resource selection is empty")

    selected_names = {entry.file for entry in selected}
    missing = [name for name in MINIMUM_WORKFLOW_FILES if name not in selected_names]
    missing.extend(
        prefix for prefix in MINIMUM_WORKFLOW_PREFIXES
        if not any(name.startswith(prefix) for name in selected_names)
    )
    if missing:
        raise CandidateError("Workflow resource plan is missing required files: " + ", ".join(missing))

    for entry in selected:
        path = checked_source(root, entry.file)
        if path.stat().st_size != entry.bytes:
            raise CandidateError(f"Workflow resource size mismatch: {entry.file}")

    blocked_categories = tuple(
        (name, values[0], values[1])
        for name, values in sorted(blocked_counts.items())
    )
    return WorkflowResourcePlan(
        root=root,
        manifest=manifest_path,
        portable_manifest=portable_path,
        profile="full" if full else "workflow-core",
        manifest_sha256=manifest_sha256,
        portable_manifest_sha256=digest(portable_path),
        entries=tuple(selected),
        omitted=tuple(omitted),
        blocked=tuple(blocked),
        blocked_categories=blocked_categories,
    )


def workflow_resource_summary(plan: WorkflowResourcePlan):
    selected = entry_summary(plan.entries)
    omitted = entry_summary(plan.omitted)
    names = {entry.file for entry in plan.entries}
    omitted_models = [
        {"file": entry.file, "bytes": entry.bytes, "feature": entry.feature}
        for entry in plan.omitted
        if ("/experiments/" in entry.file
            and entry.feature in {"chinese", "japanese"})
    ]

    def has_file(name):
        return name in names

    def has_prefix(prefix):
        return any(name.startswith(prefix) for name in names)

    stages = {
        "local_audio_import": has_file("dependencies/ffmpeg/ffmpeg.exe"),
        "default_separation": (
            has_file("dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx")
            and has_file("dependencies/models/mdx-model-data.json")
        ),
        "chorus_separation": (
            has_file("models/UVR_MDXNET_KARA_2.onnx")
            and has_file("models/mdx-model-data.json")
        ),
        "midi_notes": (
            has_prefix("dependencies/vocal2midi/experiments/GAME-1.0.3-medium-onnx/")
            and has_file("dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx")
            and has_file("dependencies/vocal2midi2/application/pipeline.py")
        ),
        "hfa_alignment": has_prefix(
            "dependencies/vocal2midi/experiments/1218_hfa_model_new_dict/"
        ),
        "chinese_qwen_lyrics": any(entry.feature == "chinese" for entry in plan.entries),
        "japanese_romaji_lyrics": any(entry.feature == "japanese" for entry in plan.entries),
        "svp_project_export": (
            any(entry.feature == "projects" for entry in plan.entries)
            and has_file("dependencies/export/libresvip/__init__.py")
        ),
        "publishing": any(entry.feature == "publishing" for entry in plan.entries),
    }
    return {
        "profile": plan.profile,
        "selection": (
            "local import, separation, MIDI, HFA alignment, RMVPE pitch, and SVP conversion"
            if plan.profile == "workflow-core"
            else "all manifest resources except Qt5 UI and installer tooling"
        ),
        "source_identity": {
            "manifest_matches_frozen": True,
            "manifest_sha256": plan.manifest_sha256,
            "portable_files_sha256": plan.portable_manifest_sha256,
        },
        "selected": selected,
        "omitted_optional": omitted,
        "omitted_optional_models": omitted_models,
        "blocked_runtime": {
            "categories": [
                {"name": name, "files": files, "bytes": size}
                for name, files, size in plan.blocked_categories
            ],
            "total": {
                "files": sum(files for _, files, _ in plan.blocked_categories),
                "bytes": sum(size for _, _, size in plan.blocked_categories),
            },
        },
        "stages": {
            name: {"bundled": bundled, "validation": "not_run_by_builder"}
            for name, bundled in stages.items()
        },
    }


def normalized_project(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def clean_relative(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateError(f"{label} must be a non-empty relative path")
    if "\\" in value or ":" in value:
        raise CandidateError(f"{label} must use a canonical POSIX path without a drive or ADS: {value}")
    parts = value.split("/")
    if value.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise CandidateError(f"{label} escapes or is not canonical: {value}")
    if any(ord(character) < 32 for character in value):
        raise CandidateError(f"{label} contains a control character")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != value:
        raise CandidateError(f"{label} is not canonical: {value}")
    return value


def checked_target(value, label="destination"):
    target = clean_relative(value, label)
    if target.split("/", 1)[0].casefold() in FORBIDDEN_TARGET_ROOTS:
        raise CandidateError(f"{label} points at private/runtime state: {target}")
    return target


def reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def checked_source(root: Path, relative: str, *, directory: bool = False) -> Path:
    current = root
    parts = PurePosixPath(clean_relative(relative, "source path")).parts
    for part in parts:
        current = current / part
        if reparse_point(current):
            raise CandidateError(f"Source path contains a link or reparse point: {relative}")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise CandidateError(f"Source does not exist: {relative} ({exc})") from exc
    if not resolved.is_relative_to(resolved_root):
        raise CandidateError(f"Source escapes the input root: {relative}")
    if directory and not resolved.is_dir():
        raise CandidateError(f"Expected a directory: {relative}")
    if not directory and not resolved.is_file():
        raise CandidateError(f"Expected a regular file: {relative}")
    return resolved


class Payload:
    def __init__(self, stage: Path):
        self.stage = stage
        self._files: dict[str, PackagedFile] = {}
        self._folded: dict[str, str] = {}

    @property
    def files(self):
        return dict(sorted(self._files.items()))

    def _claim(self, target: str) -> Path:
        target = checked_target(target)
        folded = target.casefold()
        if folded in self._folded:
            raise CandidateError(
                f"Duplicate destination (case-insensitive): {target} and {self._folded[folded]}"
            )
        self._folded[folded] = target
        destination = self.stage.joinpath(*PurePosixPath(target).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def copy_path(self, source: Path, target: str, origin: str) -> None:
        destination = self._claim(target)
        value = hashlib.sha256()
        size = 0
        with source.open("rb") as source_stream, destination.open("xb") as output:
            for chunk in iter(lambda: source_stream.read(CHUNK), b""):
                output.write(chunk)
                value.update(chunk)
                size += len(chunk)
        self._files[target] = PackagedFile(target, size, value.hexdigest(), origin)

    def copy_verified(self, source: Path, target: str, origin: str, *,
                      expected_bytes: int, expected_sha256: str) -> None:
        """Copy one frozen file into the stage and verify the copied bytes."""
        if source.stat().st_size != expected_bytes:
            raise CandidateError(f"Resource size changed before copy: {source}")
        destination = self._claim(target)
        value = hashlib.sha256()
        size = 0
        with source.open("rb") as source_stream, destination.open("xb") as output:
            for chunk in iter(lambda: source_stream.read(CHUNK), b""):
                output.write(chunk)
                value.update(chunk)
                size += len(chunk)
        actual_sha256 = value.hexdigest()
        if size != expected_bytes or actual_sha256 != expected_sha256:
            raise CandidateError(f"Resource SHA-256/size mismatch: {source}")
        self._files[target] = PackagedFile(target, size, actual_sha256, origin)

    def copy_stream(self, source: BinaryIO, target: str, origin: str) -> None:
        destination = self._claim(target)
        value = hashlib.sha256()
        size = 0
        with destination.open("xb") as output:
            for chunk in iter(lambda: source.read(CHUNK), b""):
                output.write(chunk)
                value.update(chunk)
                size += len(chunk)
        self._files[target] = PackagedFile(target, size, value.hexdigest(), origin)

    def write_bytes(self, target: str, data: bytes, origin: str) -> None:
        self.copy_stream(io_bytes(data), target, origin)


class io_bytes:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk
def load_source_inventory(source_root: Path):
    path = source_root / SOURCE_MANIFEST
    data = read_json(path)
    if data.get("format") != 1 or not isinstance(data.get("entries"), list):
        raise CandidateError("packaging/portable-files.json must use format 1 and an entries array")
    entries = []
    destinations = {}
    for index, entry in enumerate(data["entries"]):
        if not isinstance(entry, dict):
            raise CandidateError(f"portable-files entry {index} is not an object")
        target = checked_target(entry.get("file"), f"portable-files[{index}].file")
        source = clean_relative(entry.get("source"), f"portable-files[{index}].source")
        folded = target.casefold()
        if folded in destinations:
            raise CandidateError(f"Duplicate portable destination: {target}")
        destinations[folded] = target
        entries.append((target, source))
    return path, entries


def select_application_entries(entries: Iterable[tuple[str, str]]):
    selected = {}
    for target, source in entries:
        source_path = PurePosixPath(source)
        target_path = PurePosixPath(target)
        suffix = source_path.suffix.casefold()
        wanted = False
        if source.startswith("src/"):
            if suffix == ".py":
                wanted = True
            elif source.startswith("src/quick_ui/") or target.startswith("quick_ui/"):
                wanted = True
        if source.startswith("assets/") or source.startswith("components/"):
            wanted = True
        if target in {"config.json", "dependencies/manifest.json", "version.json"}:
            wanted = True
        if target in {"packaging/requirements-pedalboard.lock.txt", "tools/install_pedalboard_component.py"}:
            wanted = True
        upper_name = target_path.name.upper()
        if upper_name.startswith(("LICENSE", "NOTICE", "COPYING", "THIRD_PARTY")):
            wanted = True
        if wanted:
            selected[target] = source
    if "launch.py" not in selected:
        raise CandidateError("portable-files.json must map src/launch.py to launch.py")
    if selected["launch.py"].casefold() != "src/launch.py":
        raise CandidateError("launch.py must map the real src/launch.py")
    if "config.json" not in selected:
        raise CandidateError("portable-files.json must provide the default config.json mapping")
    if "dependencies/manifest.json" not in selected:
        raise CandidateError("portable-files.json must preserve dependencies/manifest.json")
    if not any(source.startswith("assets/") for source in selected.values()):
        raise CandidateError("portable-files.json selected no assets")
    if not any(source.startswith("components/") for source in selected.values()):
        raise CandidateError("portable-files.json selected no components")
    quick_apps = [
        (target, source) for target, source in selected.items()
        if source.startswith("src/")
        and (PurePosixPath(target).name.casefold() in {name.casefold() for name in QUICK_APP_NAMES}
             or PurePosixPath(source).name.casefold() in {name.casefold() for name in QUICK_APP_NAMES})
    ]
    if not quick_apps:
        raise CandidateError(
            "portable-files.json must map the Quick application plane "
            "(应用平面.py, application_plane.py, or quick_app.py) from src/"
        )
    qml = [
        (target, source) for target, source in selected.items()
        if PurePosixPath(target).suffix.casefold() == ".qml"
        and (target.startswith("quick_ui/") or source.startswith("src/quick_ui/"))
    ]
    if not qml:
        raise CandidateError("portable-files.json must map at least one src/quick_ui/*.qml file")
    return selected, quick_apps, qml


def load_lock(source_root: Path):
    path = source_root / LOCK_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CandidateError(f"Cannot read Qt lock {path}: {exc}") from exc
    requirements = {}
    flags = set()
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)\s+--hash=sha256:([0-9a-fA-F]{64})$")
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            flags.add(line.split()[0])
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise CandidateError(f"Unsupported requirements-quick.lock.txt line: {line}")
        project, version, sha256 = match.groups()
        normalized = normalized_project(project)
        if normalized in requirements:
            raise CandidateError(f"Duplicate locked project: {project}")
        requirements[normalized] = LockedWheel(
            normalized, version, sha256.lower(), EXPECTED_WHEELS.get(normalized, "")
        )
    if "--require-hashes" not in flags or "--only-binary=:all:" not in flags:
        raise CandidateError("Quick lock must require hashes and binary-only inputs")
    if set(requirements) != set(EXPECTED_WHEELS):
        raise CandidateError("Quick lock must contain exactly PySide6 Essentials and shiboken6")
    for item in requirements.values():
        if item.version != QT_VERSION or not item.filename:
            raise CandidateError(f"Quick lock is not the reviewed Qt {QT_VERSION} wheel set")
    return path, requirements


def wheel_metadata(archive: zipfile.ZipFile):
    metadata = [name for name in archive.namelist() if name.count("/") == 1 and name.endswith(".dist-info/METADATA")]
    wheel_info = [name for name in archive.namelist() if name.count("/") == 1 and name.endswith(".dist-info/WHEEL")]
    if len(metadata) != 1 or len(wheel_info) != 1:
        raise CandidateError("Wheel must contain exactly one METADATA and WHEEL file")
    parsed = BytesParser().parsebytes(archive.read(metadata[0]))
    project = normalized_project(parsed.get("Name", ""))
    version = (parsed.get("Version") or "").strip()
    wheel = BytesParser().parsebytes(archive.read(wheel_info[0]))
    tags = wheel.get_all("Tag", [])
    return project, version, metadata[0], wheel_info[0], tags


def verify_wheels(wheel_dir: Path, lock: dict[str, LockedWheel]):
    locked_by_filename = {item.filename: item for item in lock.values()}
    result = []
    for filename, item in sorted(locked_by_filename.items()):
        path = checked_source(wheel_dir, filename)
        actual = digest(path)
        if actual != item.sha256:
            raise CandidateError(f"Wheel SHA-256 mismatch: {filename}")
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len({name.casefold() for name in names}) != len(names):
                    raise CandidateError(f"Wheel contains duplicate paths: {filename}")
                for info in archive.infolist():
                    clean_relative(info.filename.rstrip("/"), f"wheel path in {filename}")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise CandidateError(f"Wheel contains a symbolic link: {info.filename}")
                project, version, metadata_name, wheel_name, tags = wheel_metadata(archive)
                if project != item.project or version != QT_VERSION:
                    raise CandidateError(f"Wheel identity mismatch: {filename}")
                if "win_amd64" not in " ".join(tags):
                    raise CandidateError(f"Wheel is not Windows x64: {filename}")
        except zipfile.BadZipFile as exc:
            raise CandidateError(f"Invalid wheel ZIP: {filename}") from exc
        result.append({
            "project": project,
            "version": version,
            "file": filename,
            "bytes": path.stat().st_size,
            "sha256": actual,
            "metadata": metadata_name,
            "wheel_metadata": wheel_name,
        })
    return result

def _wheel_file_selected(name: str, project: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    if not parts:
        return False
    if any(part == "__pycache__" for part in parts):
        return False
    if path.suffix.casefold() in {".pyc", ".pyo", ".pyi", ".h", ".lib", ".cmake", ".cpp", ".prl", ".rst"}:
        return False
    top = parts[0]
    if top.endswith(".dist-info"):
        return True
    if project == "shiboken6":
        if top != "shiboken6" or len(parts) > 2:
            return False
        return path.suffix.casefold() in {".py", ".pyd", ".dll", ".typed", ".txt"} or path.name == "py.typed"
    if top != "PySide6":
        return False
    if len(parts) == 2:
        return path.suffix.casefold() in {".py", ".pyd", ".dll", ".json", ".typed", ".txt"}
    subdir = parts[1]
    if subdir in {"include", "lib", "typesystems", "scripts", "glue", "doc"}:
        return False
    if subdir == "plugins":
        if len(parts) < 3:
            return False
        return parts[2] in {
            "generic", "iconengines", "imageformats", "networkinformation",
            "platforminputcontexts", "platforms", "styles", "tls",
        }
    if subdir == "qml":
        joined = "/".join(parts)
        if joined.startswith((
            "PySide6/qml/QtQuick/VirtualKeyboard/",
            "PySide6/qml/QtQuick/Controls/FluentWinUI3/",
            "PySide6/qml/QtQuick/Controls/designer/",
            "PySide6/qml/QtTest/",
        )):
            return False
        return True
    if subdir in {"translations", "metatypes", "support"}:
        return True
    return False


def extract_qt_runtime(payload: Payload, wheel_dir: Path, wheels):
    licenses = []
    for wheel in wheels:
        path = checked_source(wheel_dir, wheel["file"])
        license_names = []
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
                if info.is_dir() or not _wheel_file_selected(info.filename, wheel["project"]):
                    continue
                target = f"{QT_TARGET}/{info.filename}"
                with archive.open(info, "r") as stream:
                    payload.copy_stream(stream, target, f"wheel:{wheel['file']}:{info.filename}")
                lowered = info.filename.casefold()
                if "/licenses/" in lowered or PurePosixPath(info.filename).name.casefold() in {
                    "license", "license.txt", "copying", "notice", "notice.txt",
                }:
                    license_names.append(target)
        licenses.extend(license_names)
    missing = [name for name in REQUIRED_QT_FILES if not (payload.stage / QT_TARGET / name).is_file()]
    if missing:
        raise CandidateError("Trimmed Qt runtime is missing required files: " + ", ".join(missing))
    if not licenses:
        raise CandidateError("Qt wheels supplied no license files")
    return sorted(licenses)


def resolve_python_runtime(path: Path):
    path = path.expanduser().resolve()
    for candidate in (path, path / "dependencies/vocal2midi/python"):
        if (candidate / "python.exe").is_file() and (candidate / "Lib/site-packages").is_dir():
            return candidate
    raise CandidateError(
        "--python-runtime must be the embedded Python directory or an application root containing it"
    )


def python_version(python: Path):
    code = (
        "import json,platform,sys;"
        "print(json.dumps({'version':sys.version,'major':sys.version_info.major,"
        "'minor':sys.version_info.minor,'micro':sys.version_info.micro,"
        "'machine':platform.machine(),'implementation':sys.implementation.name}))"
    )
    clean_env = os.environ.copy()
    clean_env.pop("PYTHONHOME", None)
    clean_env.pop("PYTHONPATH", None)
    try:
        process = subprocess.run(
            [str(python), "-I", "-B", "-c", code], cwd=python.parent,
            env=clean_env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateError(f"Cannot execute source Python: {exc}") from exc
    if process.returncode != 0:
        raise CandidateError("Source Python failed: " + process.stderr[-2000:])
    value = json.loads(process.stdout.strip().splitlines()[-1])
    if value.get("implementation") != "cpython":
        raise CandidateError("Candidate Python must be CPython")
    if (value.get("major"), value.get("minor")) != PYTHON_SERIES:
        raise CandidateError(f"Candidate Python must be {PYTHON_SERIES[0]}.{PYTHON_SERIES[1]}")
    if value.get("machine", "").lower() not in {"amd64", "x86_64"}:
        raise CandidateError("Candidate Python must be 64-bit Windows")
    return value


def parse_record(path: Path):
    records = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if not row:
                continue
            relative = row[0]
            hash_value = row[1] if len(row) > 1 else ""
            size = row[2] if len(row) > 2 else ""
            records[relative] = (hash_value, size)
    return records

def validate_numpy(runtime: Path):
    site = runtime / "Lib/site-packages"
    package = site / "numpy"
    libraries = site / "numpy.libs"
    dist_info = site / f"numpy-{NUMPY_VERSION}.dist-info"
    for directory in (package, libraries, dist_info):
        if not directory.is_dir() or reparse_point(directory):
            raise CandidateError(f"Required NumPy directory is missing or linked: {directory.name}")
    metadata_path = dist_info / "METADATA"
    wheel_path = dist_info / "WHEEL"
    record_path = dist_info / "RECORD"
    if not all(path.is_file() for path in (metadata_path, wheel_path, record_path)):
        raise CandidateError("NumPy 1.26.4 installation lacks METADATA, WHEEL, or RECORD")
    metadata = BytesParser().parsebytes(metadata_path.read_bytes())
    if normalized_project(metadata.get("Name", "")) != "numpy" or (metadata.get("Version") or "").strip() != NUMPY_VERSION:
        raise CandidateError(f"Source runtime does not contain NumPy {NUMPY_VERSION}")
    wheel = BytesParser().parsebytes(wheel_path.read_bytes())
    tags = " ".join(wheel.get_all("Tag", []))
    if "cp312-cp312-win_amd64" not in tags:
        raise CandidateError("NumPy installation is not the cp312 Windows x64 build")

    actual = []
    for directory in (package, libraries, dist_info):
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.casefold() in {".pyc", ".pyo"}:
                continue
            if "__pycache__" in path.parts or reparse_point(path):
                continue
            actual.append(path)
    records = parse_record(record_path)
    verified = 0
    total = 0
    for path in actual:
        relative = path.relative_to(site).as_posix()
        if relative not in records:
            raise CandidateError(f"NumPy file is absent from RECORD: {relative}")
        expected_hash, expected_size = records[relative]
        size = path.stat().st_size
        total += size
        if expected_size and str(size) != expected_size:
            raise CandidateError(f"NumPy RECORD size mismatch: {relative}")
        if expected_hash:
            algorithm, separator, expected = expected_hash.partition("=")
            if not separator:
                raise CandidateError(f"Malformed NumPy RECORD hash: {relative}")
            try:
                value = hashlib.new(algorithm)
            except ValueError as exc:
                raise CandidateError(f"Unsupported NumPy RECORD hash: {relative}") from exc
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(CHUNK), b""):
                    value.update(chunk)
            encoded = base64.urlsafe_b64encode(value.digest()).rstrip(b"=").decode("ascii")
            if encoded != expected:
                raise CandidateError(f"NumPy RECORD hash mismatch: {relative}")
            verified += 1
    if not actual:
        raise CandidateError("NumPy installation contains no package files")
    return {
        "version": NUMPY_VERSION,
        "roots": [directory.name for directory in (package, libraries, dist_info)],
        "files": [path.relative_to(site).as_posix() for path in actual],
        "total_bytes": total,
        "record_verified_files": verified,
        "record_file": record_path.relative_to(site).as_posix(),
    }


def add_python_runtime(payload: Payload, runtime: Path):
    version = python_version(runtime / "python.exe")
    numpy = validate_numpy(runtime)
    major_minor = f"python{version['major']}{version['minor']}"
    zip_name = f"{major_minor}.zip"
    zip_path = checked_source(runtime, zip_name)
    if zip_path.stat().st_size == 0:
        raise CandidateError("Embedded Python standard-library ZIP is empty")

    top_level = []
    for path in sorted(runtime.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or reparse_point(path):
            continue
        name = path.name
        if name in {"python.exe", "pythonw.exe", "python.cat", "LICENSE.txt", "python3.dll"}:
            top_level.append(path)
        elif name.casefold().endswith((".dll", ".pyd")):
            top_level.append(path)
        elif re.fullmatch(rf"{major_minor}\.(zip|dll)", name, re.IGNORECASE):
            top_level.append(path)
    top_names = {path.name.casefold() for path in top_level}
    for required in ("python.exe", "pythonw.exe", "python3.dll", f"{major_minor}.dll", zip_name):
        if required.casefold() not in top_names:
            raise CandidateError(f"Embedded Python is missing {required}")

    for path in top_level:
        payload.copy_path(path, f"{PYTHON_TARGET}/{path.name}", f"python-runtime:{path.name}")
    pth = "\n".join((
        zip_name,
        ".",
        "Lib\\site-packages",
        "..\\..\\..\\runtime\\qt6",
        "import site",
        "",
    )).encode("ascii")
    payload.write_bytes(f"{PYTHON_TARGET}/{major_minor}._pth", pth, "generated:embedded-python-path")
    site = runtime / "Lib/site-packages"
    for relative in numpy["files"]:
        source = checked_source(site, relative)
        payload.copy_path(
            source, f"{PYTHON_TARGET}/Lib/site-packages/{relative}",
            f"numpy-1.26.4:{relative}",
        )
    numpy_summary = {key: value for key, value in numpy.items() if key != "files"}
    numpy_summary["files_count"] = len(numpy["files"])
    return version, numpy_summary


def add_workflow_resources(payload: Payload, plan: WorkflowResourcePlan):
    """Copy the manifest-selected processing runtime and models into the stage."""
    verified_files = 0
    verified_bytes = 0
    generated_files = 0
    generated_pth = f"{PYTHON_TARGET}/python312._pth"
    for entry in sorted(plan.entries, key=lambda item: item.file.casefold()):
        if entry.file == generated_pth:
            continue
        source = checked_source(plan.root, entry.file)
        payload.copy_verified(
            source,
            entry.file,
            f"workflow-resource:{entry.feature}",
            expected_bytes=entry.bytes,
            expected_sha256=entry.sha256,
        )
        verified_files += 1
        verified_bytes += entry.bytes

    pth = "\n".join((
        "python312.zip",
        ".",
        "Lib\\site-packages",
        "..",
        "..\\..\\..\\runtime\\qt6",
        "import site",
        "",
    )).encode("ascii")
    payload.write_bytes(generated_pth, pth, "generated:workflow-embedded-python-path")
    generated_files += 1

    python_root = payload.stage / PYTHON_TARGET
    version = python_version(python_root / "python.exe")
    numpy = validate_numpy(python_root)
    numpy_summary = {key: value for key, value in numpy.items() if key != "files"}
    numpy_summary["files_count"] = len(numpy["files"])
    version["copy_mode"] = "frozen-workflow-resources"
    verification = {
        "selected_files": len(plan.entries),
        "sha256_verified_files": verified_files,
        "generated_files": generated_files,
        "verified_bytes": verified_bytes,
        "mode": "manifest-size-and-sha256-during-isolated-copy",
        "source_files_opened_read_only": True,
        "generated_files_list": [generated_pth],
    }
    return version, numpy_summary, verification

def clean_subprocess_env(stage: Path, *, qt: bool):
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    python = stage / PYTHON_TARGET
    qt_root = stage / QT_TARGET
    env["PATH"] = os.pathsep.join((str(python), str(qt_root / "PySide6"), env.get("PATH", "")))
    if qt:
        env["QT_PLUGIN_PATH"] = str(qt_root / "PySide6/plugins")
        env["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(qt_root / "PySide6/plugins/platforms")
        env["QML2_IMPORT_PATH"] = str(qt_root / "PySide6/qml")
        env["QML_IMPORT_PATH"] = str(qt_root / "PySide6/qml")
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_QUICK_BACKEND"] = "software"
    return env


def run_candidate_python(stage: Path, code: str, *, qt=False, timeout=90):
    python = stage / PYTHON_TARGET / "python.exe"
    process = subprocess.run(
        [str(python), "-I", "-B", "-c", code], cwd=stage,
        env=clean_subprocess_env(stage, qt=qt), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if process.returncode != 0:
        detail = (process.stdout + "\n" + process.stderr).strip()
        raise CandidateError("Candidate subprocess check failed:\n" + detail[-5000:])
    return process.stdout.strip()


def verify_numpy_abi(stage: Path):
    code = (
        "import json,numpy;"
        "a=numpy.arange(6,dtype=numpy.float64).reshape(2,3);"
        "b=numpy.asarray([[1.0],[2.0],[3.0]]);"
        "print(json.dumps({'version':numpy.__version__,'file':numpy.__file__,"
        "'result':(a@b).reshape(-1).tolist()}))"
    )
    value = json.loads(run_candidate_python(stage, code).splitlines()[-1])
    if value.get("version") != NUMPY_VERSION:
        raise CandidateError(f"Candidate NumPy ABI imported {value.get('version')!r}")
    if value.get("result") != [8.0, 26.0]:
        raise CandidateError("Candidate NumPy BLAS calculation returned an unexpected result")
    return value


def verify_qt_qml(stage: Path):
    code = r'''
import json
from PySide6.QtCore import QUrl, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
app = QGuiApplication([])
engine = QQmlEngine()
component = QQmlComponent(engine)
component.setData(b"""import QtQml
import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Templates
import QtQuick.Controls
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects
import QtQuick.Shapes
Item { width: 1; height: 1 }
""", QUrl("inline:quick-candidate-check.qml"))
import time
deadline = time.monotonic() + 10
while component.status() == QQmlComponent.Loading and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
errors = [error.toString() for error in component.errors()]
if component.status() != QQmlComponent.Ready or errors:
    raise RuntimeError("\n".join(errors) or f"QML status: {component.status().name}")
print(json.dumps({"qt": qVersion(), "status": component.status().name}))
'''
    value = json.loads(run_candidate_python(stage, code, qt=True).splitlines()[-1])
    if value.get("qt") != QT_VERSION:
        raise CandidateError(f"Candidate Qt runtime reports {value.get('qt')!r}")
    return value


def add_start_launcher(payload: Payload, source_root: Path, entries):
    mapping = {target: source for target, source in entries}
    target = "Start-Quick.bat"
    source = mapping.get(target, "tools/portable/Start-Quick.bat")
    path = checked_source(source_root, source)
    text = path.read_text(encoding="utf-8-sig")
    if "launch.py" not in text or "--quick" not in text:
        raise CandidateError("Start-Quick.bat must call the real launch.py --quick")
    payload.copy_path(path, target, f"portable-files:{source}" if target in mapping else source)



def add_shared_lock_helper(payload: Payload, source_root: Path, entries):
    """Freeze the exact updater lock helper and check its top-level imports."""
    mapping = {target: source for target, source in entries}
    target = "tools/update_apply.py"
    source = mapping.get(target, target)
    path = checked_source(source_root, source)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        raise CandidateError(f"Invalid shared lock helper: {exc}") from exc

    roots = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise CandidateError("Top-level relative imports in tools/update_apply.py are unsupported")
            if node.module:
                roots.add(node.module.split(".", 1)[0])

    standard = set(sys.stdlib_module_names)
    local_imports = []
    for root in sorted(roots - standard):
        local_target = f"{root}.py"
        if local_target in payload.files:
            continue
        local_source = mapping.get(local_target)
        if not local_source:
            raise CandidateError(
                f"tools/update_apply.py requires unmapped local module {root!r} ({local_target})"
            )
        payload.copy_path(
            checked_source(source_root, local_source), local_target,
            f"portable-files:{local_source}",
        )
        local_imports.append(local_target)

    payload.copy_path(path, target, f"portable-files:{source}" if target in mapping else source)
    return {
        "target": target,
        "source": source,
        "sha256": digest(path),
        "top_level_imports": sorted(roots),
        "added_local_imports": local_imports,
    }

def payload_fingerprint(files):
    value = hashlib.sha256()
    for item in files:
        value.update(f"{item['target']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8"))
    return value.hexdigest()


def scan_stage(stage: Path):
    result = []
    for path in sorted(stage.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file():
            if reparse_point(path):
                raise CandidateError(f"Staged file became a link: {path}")
            result.append(path.relative_to(stage).as_posix())
    return result


def file_records_for_stage(stage: Path):
    records = []
    for relative in scan_stage(stage):
        if relative == CANDIDATE_MANIFEST:
            continue
        path = stage.joinpath(*PurePosixPath(relative).parts)
        records.append({"target": relative, "bytes": path.stat().st_size, "sha256": digest(path)})
    return records


def write_manifest(stage: Path, source_manifest: Path, lock_path: Path, selected, quick_apps, qml,
                   python_info, numpy_summary, wheels, licenses, shared_lock, workflow=None):
    files = file_records_for_stage(stage)
    candidate_kind = "quick-workflow-engineering" if workflow else "quick-component-engineering"
    notice = (
        "Quick workflow-engineering candidate. Validate extraction, staged startup, and each "
        "music stage before use; this is not a media release, production installer, upgrade "
        "route, or product acceptance claim."
        if workflow else
        "Quick component-engineering candidate. Validate extraction and startup before use; "
        "this is not a media release, production installer, upgrade route, or acceptance claim."
    )
    value = {
        "format": 1,
        "candidate_kind": candidate_kind,
        "accepted_release": False,
        "notice": notice,
        "entrypoint": {"launcher": "Start-Quick.bat", "python": f"{PYTHON_TARGET}/python.exe", "command": "launch.py --quick"},
        "source_manifest": {"path": SOURCE_MANIFEST, "sha256": digest(source_manifest)},
        "requirements_lock": {"path": LOCK_FILE, "sha256": digest(lock_path)},
        "application": {
            "selected_files": len(selected),
            "quick_app": [{"target": target, "source": source} for target, source in quick_apps],
            "quick_qml": [{"target": target, "source": source} for target, source in qml],
            "shared_lock": shared_lock,
        },
        "python": python_info,
        "numpy": numpy_summary,
        "qt": {"version": QT_VERSION, "runtime": QT_TARGET, "wheels": wheels, "license_files": licenses},
        "files": files,
        "file_count": len(files),
        "payload_sha256": payload_fingerprint(files),
        "manifest_self_excluded": True,
    }
    if workflow is not None:
        value["workflow_resources"] = workflow
    with (stage / CANDIDATE_MANIFEST).open("xb") as stream:
        stream.write(json_bytes(value))
    return value

def write_zip(stage: Path, temporary: Path):
    names = scan_stage(stage)
    folded = set()
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=3, allowZip64=True) as archive:
        for relative in names:
            key = relative.casefold()
            if key in folded:
                raise CandidateError(f"Staged case-insensitive duplicate: {relative}")
            folded.add(key)
            path = stage.joinpath(*PurePosixPath(relative).parts)
            info = zipfile.ZipInfo(f"{PACKAGE_ROOT}/{relative}", date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info._compresslevel = 3
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, CHUNK)
    expected = {f"{PACKAGE_ROOT}/{name}" for name in names}
    with zipfile.ZipFile(temporary, "r") as archive:
        actual_names = archive.namelist()
        if len(actual_names) != len(set(actual_names)) or set(actual_names) != expected:
            raise CandidateError("Candidate ZIP central directory does not match the staged payload")
        bad = archive.testzip()
        if bad is not None:
            raise CandidateError(f"Candidate ZIP CRC failure: {bad}")
    return len(names)


def verify_stage_policy(stage: Path):
    """Reject private state and Qt5/installer runtimes from the isolated stage."""
    for relative in scan_stage(stage):
        parts = relative.split("/")
        if parts[0].casefold() in FORBIDDEN_TARGET_ROOTS:
            raise CandidateError(f"Staged candidate contains private state: {relative}")
        if any(part.casefold() in PRIVATE_RESOURCE_PARTS for part in parts):
            raise CandidateError(f"Staged candidate contains private state: {relative}")
        category = blocked_runtime_category(relative)
        if category:
            raise CandidateError(f"Staged candidate contains blocked {category}: {relative}")


def build(args):
    source_root = args.source_root.expanduser().resolve(strict=True)
    runtime = resolve_python_runtime(args.python_runtime)
    wheel_dir = args.wheel_dir.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if output.suffix.casefold() != ".zip":
        raise CandidateError("--output must name a .zip file")
    if output.exists():
        raise CandidateError(f"Refusing to overwrite existing output: {output}")
    if not wheel_dir.is_dir():
        raise CandidateError("--wheel-dir must be a directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_root = (args.staging_root or source_root / "work").expanduser().resolve()
    staging_root.mkdir(parents=True, exist_ok=True)
    workflow_plan = (
        load_workflow_resources(
            args.workflow_resources, source_root, runtime, full=args.full,
        )
        if args.workflow_resources else None
    )

    source_manifest, entries = load_source_inventory(source_root)
    selected, quick_apps, qml = select_application_entries(entries)
    launch_source = checked_source(source_root, selected["launch.py"])
    if "--quick" not in launch_source.read_text(encoding="utf-8-sig"):
        raise CandidateError("The real launch.py does not dispatch --quick")
    lock_path, lock = load_lock(source_root)
    wheels = verify_wheels(wheel_dir, lock)

    # Keep the DLL/QML tree out of a potentially long output directory. The
    # ZIP itself stays on the destination volume for its final atomic move.
    with tempfile.TemporaryDirectory(prefix="q-", dir=staging_root) as temporary, \
            tempfile.TemporaryDirectory(prefix=".vq-", dir=output.parent) as archive_folder:
        stage = Path(temporary)
        payload = Payload(stage)

        for target, source in sorted(selected.items(), key=lambda item: item[0].casefold()):
            path = checked_source(source_root, source)
            payload.copy_path(path, target, f"portable-files:{source}")
        add_start_launcher(payload, source_root, entries)
        shared_lock = add_shared_lock_helper(payload, source_root, entries)
        payload.copy_path(lock_path, LOCK_FILE, LOCK_FILE)

        workflow = None
        if workflow_plan:
            python_info, numpy_summary, resource_verification = add_workflow_resources(
                payload, workflow_plan,
            )
            workflow = workflow_resource_summary(workflow_plan)
            workflow["copy_verification"] = resource_verification
        else:
            python_info, numpy_summary = add_python_runtime(payload, runtime)
        licenses = extract_qt_runtime(payload, wheel_dir, wheels)
        qt_inventory = {
            "version": QT_VERSION,
            "wheels": wheels,
            "license_files": licenses,
            "selection": "runtime files only; headers, import libraries, CMake, docs, tools, PySide6 and VirtualKeyboard samples omitted",
        }
        payload.write_bytes(f"{QT_TARGET}/WHEELS.json", json_bytes(qt_inventory), "generated:qt-wheel-lock")

        numpy_abi = verify_numpy_abi(stage)
        qt_qml = verify_qt_qml(stage)
        verify_stage_policy(stage)
        candidate = write_manifest(
            stage, source_manifest, lock_path, selected, quick_apps, qml,
            python_info, numpy_summary, wheels, licenses, shared_lock, workflow,
        )
        candidate["verification"] = {"numpy_abi": numpy_abi, "qt_qml": qt_qml}
        (stage / CANDIDATE_MANIFEST).write_bytes(json_bytes(candidate))

        temporary_zip = Path(archive_folder) / "candidate.zip"
        file_count = write_zip(stage, temporary_zip)
        try:
            os.rename(temporary_zip, output)
        except OSError as exc:
            raise CandidateError(f"Cannot finalize candidate ZIP: {exc}") from exc

    result = {
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest(output),
        "files": file_count,
        "candidate_kind": candidate["candidate_kind"],
        "accepted_release": False,
        "numpy_version": NUMPY_VERSION,
        "numpy_record_verified_files": numpy_summary["record_verified_files"],
        "qt_version": QT_VERSION,
        "quick_app": candidate["application"]["quick_app"],
        "quick_qml_count": len(qml),
        "shared_lock": shared_lock,
    }
    if workflow:
        result.update(
            workflow_profile=workflow["profile"],
            workflow_resources=workflow["selected"]["total"],
            workflow_stages=workflow["stages"],
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="repository/source snapshot containing src/, assets/, components/, packaging/")
    parser.add_argument("--python-runtime", type=Path,
                        help="controlled embedded Python directory, or application root containing dependencies/vocal2midi/python")
    parser.add_argument("--wheel-dir", type=Path,
                        help="offline directory containing the two exact Qt 6.10.3 wheels")
    parser.add_argument("--output", type=Path,
                        help="new candidate ZIP path; existing files are never overwritten")
    parser.add_argument("--staging-root", type=Path,
                        help="writable, short path for temporary DLL/QML files (default: source-root/work)")
    parser.add_argument("--workflow-resources", type=Path,
                        help="installed read-only application root whose frozen dependency manifest supplies processing resources")
    parser.add_argument("--full", action="store_true",
                        help="with --workflow-resources, include optional language and publishing groups; Qt5/UI and installer tooling stay excluded")
    parser.add_argument("--resource-plan", action="store_true",
                        help="print the explicit workflow resource groups and sizes without creating an archive")
    args = parser.parse_args(argv)
    try:
        if args.full and args.workflow_resources is None:
            parser.error("--full requires --workflow-resources")
        if args.resource_plan:
            if args.workflow_resources is None:
                parser.error("--resource-plan requires --workflow-resources")
            source_root = args.source_root.expanduser().resolve(strict=True)
            resource_root = args.workflow_resources.expanduser().resolve(strict=True)
            runtime = args.python_runtime or resource_root / PYTHON_TARGET
            plan = load_workflow_resources(resource_root, source_root, runtime, full=args.full)
            summary = workflow_resource_summary(plan)
            summary["plan_verification"] = {
                "manifest_content_verified": True,
                "selected_file_existence_and_size_verified": True,
                "selected_file_sha256_verified": False,
                "note": "SHA-256 is verified while selected files are copied into build isolation.",
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        missing = [
            name for name, value in (
                ("--python-runtime", args.python_runtime),
                ("--wheel-dir", args.wheel_dir),
                ("--output", args.output),
            ) if value is None
        ]
        if missing:
            parser.error("build mode requires " + ", ".join(missing))
        build(args)
    except (CandidateError, OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        print(f"quick candidate build failed: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
