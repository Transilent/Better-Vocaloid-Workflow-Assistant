"""Unified configuration schema, validation, self-healing, and idempotent migration.

Zero upward dependencies (imports only stdlib + vwa_paths + vwa_io).
Unknown fields are preserved verbatim so version rollbacks never discard user settings.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from vwa_io import atomic_json
from vwa_paths import ROOT

CONFIG_SCHEMA_VERSION = 2

CONFIG_DEFAULTS: dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "python": "dependencies/vocal2midi/python/python.exe",
    "vocal2midi": "dependencies/vocal2midi",
    "ffmpeg": "dependencies/ffmpeg/ffmpeg.exe",
    "mdx_model": "dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx",
    "mdx_metadata": "dependencies/models/mdx-model-data.json",
    "default_source": "",
    "attribution": "source_and_upstream",
    "separation_device": "dml",
    "midi_device": "dml",
    "chorus_model": "models/UVR_MDXNET_KARA_2.onnx",
    "chorus_metadata": "models/mdx-model-data.json",
    "mdx_overlap": 0.5,
    "mdx_denoise": True,
    "publish_browser_executable": "dependencies/browser/chrome/chrome.exe",
    "download_source": "auto",
    "network_proxy_mode": "direct",
    "check_for_updates": True,
    "auto_restart_updates": True,
    "theme": "dark",
    "download_workers": 4,
    "audio_output_device": "",
}

CONFIG_CHOICES: dict[str, tuple[Any, ...]] = {
    "theme": ("light", "dark", "system"),
    "network_proxy_mode": ("direct", "system"),
    "separation_device": ("cpu", "dml"),
    "midi_device": ("cpu", "dml"),
    "attribution": ("source_and_upstream", "source_only", "none"),
}

_BOOL_KEYS = ("mdx_denoise", "check_for_updates", "auto_restart_updates")
_STRING_KEYS = (
    "python",
    "vocal2midi",
    "ffmpeg",
    "mdx_model",
    "mdx_metadata",
    "default_source",
    "chorus_model",
    "chorus_metadata",
    "publish_browser_executable",
    "download_source",
    "audio_output_device",
)


def validate_and_migrate(raw: Any) -> tuple[dict[str, Any], list[str], bool]:
    """Validate and migrate a raw config dict to CONFIG_SCHEMA_VERSION.

    Returns ``(clean_config, warnings, migrated)``. Unknown keys are preserved
    so downgrades never lose user fields; malformed known keys heal to safe defaults.
    """
    warnings: list[str] = []
    if not isinstance(raw, dict):
        warnings.append("config_root_invalid: reset to defaults")
        return copy.deepcopy(CONFIG_DEFAULTS), warnings, True

    result = copy.deepcopy(raw)
    migrated = False

    raw_ver = result.get("schema_version")
    if type(raw_ver) is not int or raw_ver < CONFIG_SCHEMA_VERSION:
        result["schema_version"] = CONFIG_SCHEMA_VERSION
        migrated = True

    # Ensure defaults exist without clobbering unknown keys
    for key, default in CONFIG_DEFAULTS.items():
        if key not in result:
            result[key] = copy.deepcopy(default)
            if key in ("schema_version", "network_proxy_mode"):
                migrated = True

    # Validate enum choices
    for key, allowed in CONFIG_CHOICES.items():
        val = result.get(key)
        if val not in allowed:
            warnings.append(f"config_invalid_{key}: {val!r} -> {CONFIG_DEFAULTS[key]!r}")
            result[key] = CONFIG_DEFAULTS[key]

    # Validate booleans
    for key in _BOOL_KEYS:
        val = result.get(key)
        if type(val) is not bool:
            warnings.append(f"config_invalid_{key}: {val!r} -> {CONFIG_DEFAULTS[key]!r}")
            result[key] = CONFIG_DEFAULTS[key]

    # Validate string fields
    for key in _STRING_KEYS:
        val = result.get(key)
        if not isinstance(val, str):
            warnings.append(f"config_invalid_{key}: reset to default")
            result[key] = CONFIG_DEFAULTS[key]

    # Validate download_workers (1..8)
    workers = result.get("download_workers")
    if type(workers) is not int or not (1 <= workers <= 8):
        warnings.append(f"config_invalid_download_workers: {workers!r} -> 4")
        result["download_workers"] = 4

    # Validate mdx_overlap (0.0 <= x < 1.0)
    overlap = result.get("mdx_overlap")
    if type(overlap) not in (int, float) or not math.isfinite(overlap) or not (0.0 <= float(overlap) < 1.0):
        warnings.append(f"config_invalid_mdx_overlap: {overlap!r} -> 0.5")
        result["mdx_overlap"] = 0.5
    else:
        result["mdx_overlap"] = float(overlap)

    return result, warnings, migrated


def load_validated_config(
    root=ROOT,
    *,
    persist_migration: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Read config.json safely, migrate v1 -> v2 with backup when requested, and heal bad fields."""
    root = Path(root).resolve()
    cfg_path = root / "config.json"
    raw: Any = None
    warnings: list[str] = []

    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            warnings.append(f"config_parse_error: {type(exc).__name__}")
            raw = None
    else:
        example = root / "packaging/config.example.json"
        if not example.is_file():
            example = root / "config.example.json"
        if example.is_file():
            try:
                raw = json.loads(example.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError):
                raw = None

    clean, val_warnings, migrated = validate_and_migrate(raw)
    warnings.extend(val_warnings)

    if persist_migration and migrated and cfg_path.is_file():
        backup = root / "config.json.v1.bak"
        try:
            if not backup.exists():
                backup.write_bytes(cfg_path.read_bytes())
            atomic_json(cfg_path, clean)
        except OSError as exc:
            warnings.append(f"config_migration_persist_failed: {type(exc).__name__}")

    return clean, warnings


def unified_config_snapshot(root=ROOT) -> dict[str, Any]:
    """Return a redacted, non-path configuration snapshot for diagnostics."""
    root = Path(root).resolve()
    cfg, warnings = load_validated_config(root, persist_migration=False)
    safe_keys = (
        "schema_version",
        "theme",
        "download_source",
        "download_workers",
        "network_proxy_mode",
        "check_for_updates",
        "auto_restart_updates",
        "separation_device",
        "midi_device",
        "attribution",
    )
    snapshot: dict[str, Any] = {
        "config": {k: cfg.get(k) for k in safe_keys},
        "warnings": warnings[:20],
    }
    appearance_path = root / "cache/appearance.json"
    if appearance_path.is_file():
        try:
            app_data = json.loads(appearance_path.read_text(encoding="utf-8-sig"))
            if isinstance(app_data, dict):
                snapshot["appearance"] = {
                    k: app_data.get(k)
                    for k in ("material", "ui_scale", "ui_language", "reduce_motion", "render_backend")
                    if k in app_data
                }
        except (OSError, UnicodeError, ValueError):
            pass
    return snapshot
