"""Tier 2 Milestone 2 (M2) infrastructure regression suite (L0).

Verifies:
  1. vwa_config: schema v1 -> v2 migration with backup, unknown-key preservation,
     self-healing of corrupt enum/range/type fields, and redacted snapshot.
  2. task_index: SQLite3 WAL read-cache index creation, incremental sync, and
     transparent self-healing rebuild after database corruption.
  3. crash_dump: faulthandler enablement, Windows MiniDumpNormal ('MDMP') write
     via built-in dbghelp.dll, rotation to MAX_DUMPS, and inclusion in redacted
     diagnostic bundles.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get("BVWA_TESTING") == "1"

import crash_dump
import operation_diagnostics
import task_index
import vwa_config


with tempfile.TemporaryDirectory(dir=APP / "work", prefix="tier2-m2-") as tmp:
    root = Path(tmp)

    # --- 1. vwa_config migration + self-healing + unknown field retention ---
    legacy_cfg = {
        "theme": "neon-broken",
        "download_workers": 999,
        "mdx_overlap": 1.5,
        "check_for_updates": "yes",
        "custom_user_flag": "keep-me-on-downgrade",
    }
    (root / "config.json").write_text(json.dumps(legacy_cfg, indent=2), encoding="utf-8")
    clean, warnings = vwa_config.load_validated_config(root, persist_migration=True)
    assert clean["schema_version"] == vwa_config.CONFIG_SCHEMA_VERSION
    assert clean["theme"] == "dark"
    assert clean["download_workers"] == 4
    assert clean["mdx_overlap"] == 0.5
    assert clean["check_for_updates"] is True
    assert clean["network_proxy_mode"] == "direct"
    assert clean["custom_user_flag"] == "keep-me-on-downgrade", "Unknown user key lost during migration"
    assert len(warnings) >= 4
    assert (root / "config.json.v1.bak").is_file(), "v1 backup must be written before persisting migration"
    persisted = json.loads((root / "config.json").read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 2 and persisted["custom_user_flag"] == "keep-me-on-downgrade"

    # Corrupt JSON config heals cleanly instead of crashing startup
    corrupt_root = root / "corrupt-cfg"
    corrupt_root.mkdir()
    (corrupt_root / "config.json").write_text("{not valid json", encoding="utf-8")
    healed, heal_warnings = vwa_config.load_validated_config(corrupt_root, persist_migration=False)
    assert healed["schema_version"] == 2 and healed["theme"] == "dark"
    assert any("config_parse_error" in w for w in heal_warnings)

    snap = vwa_config.unified_config_snapshot(root)
    assert snap["config"]["schema_version"] == 2
    assert "python" not in snap["config"], "Local executable paths must not leak into snapshot"

    # --- 2. task_index SQLite3 sync + corruption self-heal ---
    sample_entries = {
        str(root / "jobs/song-a"): {
            "stamps": {"request.json": [100, 1000], "status.json": [50, 2000]},
            "row": {
                "title": "Song A",
                "request": {"language": "zh"},
                "status": {"state": "done"},
                "created": "2026-11-27",
                "modified": 1700000100.0,
            },
        },
        str(root / "jobs/song-b"): {
            "stamps": {"request.json": [120, 1100], "status.json": [60, 2100]},
            "row": {
                "title": "Song B",
                "request": {"language": "ja"},
                "status": {"state": "ready"},
                "created": "2026-11-27",
                "modified": 1700000200.0,
            },
        },
    }
    assert task_index.sync_entries(root, sample_entries) is True
    loaded, healthy = task_index.load_cached_entries(root)
    assert healthy is True
    assert set(loaded.keys()) == set(sample_entries.keys())
    assert loaded[str(root / "jobs/song-b")]["row"]["title"] == "Song B"

    # Prune deleted task on next sync
    del sample_entries[str(root / "jobs/song-a")]
    assert task_index.sync_entries(root, sample_entries) is True
    loaded2, healthy2 = task_index.load_cached_entries(root)
    assert healthy2 is True and list(loaded2.keys()) == [str(root / "jobs/song-b")]

    # Corrupt database auto-purges and recovers cleanly
    db_file = task_index.index_path(root)
    db_file.write_bytes(b"CORRUPT-SQLITE-HEADER-BYTES-1234567890")
    loaded3, healthy3 = task_index.load_cached_entries(root)
    assert healthy3 is False and loaded3 == {}
    assert not db_file.exists(), "Corrupt SQLite index must be unlinked for transparent rebuild"
    assert task_index.sync_entries(root, sample_entries) is True
    loaded4, healthy4 = task_index.load_cached_entries(root)
    assert healthy4 is True and len(loaded4) == 1

    # --- 3. crash_dump: faulthandler + MiniDumpNormal + diagnostic bundle inclusion ---
    dump_dir = root / crash_dump.DUMP_DIR_REL
    dump_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(5):
        p = dump_dir / f"vwa-crash-20261127-00000{idx}.dmp"
        p.write_bytes(b"MDMP" + bytes([idx]) * 32)
        os.utime(p, (1700000000 + idx, 1700000000 + idx))
    removed = crash_dump.prune_old_dumps(dump_dir, keep=3)
    assert removed == 2
    assert len(list(dump_dir.glob("vwa-crash-*.dmp"))) == 3

    if os.name == "nt":
        real_dmp = dump_dir / "vwa-crash-live-check.dmp"
        assert crash_dump.write_minidump(real_dmp) is True
        header = real_dmp.read_bytes()[:4]
        assert header == b"MDMP", f"Expected valid Windows MiniDump header 'MDMP', got {header!r}"

    tb_file = root / crash_dump.TRACEBACK_FILE_REL
    tb_file.parent.mkdir(parents=True, exist_ok=True)
    tb_file.write_text(f"Fatal Python error at {root}\\src\\launch.py\n", encoding="utf-8")

    bundle_zip = root / "diag-m2.zip"
    receipt = operation_diagnostics.export_diagnostic_bundle(bundle_zip, "summary ok", root=root)
    assert "config-snapshot.json" in receipt["files"]
    assert "crash-traceback.txt" in receipt["files"]
    assert "crash-dumps.json" in receipt["files"]
    with zipfile.ZipFile(bundle_zip, "r") as zf:
        tb_redacted = zf.read("crash-traceback.txt").decode("utf-8")
        assert "<VWA_ROOT>" in tb_redacted and str(root) not in tb_redacted
        cfg_json = json.loads(zf.read("config-snapshot.json").decode("utf-8"))
        assert cfg_json["config"]["schema_version"] == 2
        dumps_json = json.loads(zf.read("crash-dumps.json").decode("utf-8"))
        assert len(dumps_json["dumps"]) >= 1

# --- 4. M3: list_diff incremental engine, pure ViewModels, & QML split size verification ---
import chorus_viewmodel
import list_diff
import style_catalog
import tuning_viewmodel

# Incremental update only (no reset)
old_rows = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]
new_rows = [{"id": "a", "v": 1}, {"id": "b", "v": 20}, {"id": "c", "v": 3}]
ops = list_diff.compute_list_diff(old_rows, new_rows, lambda r: r["id"])
assert ops == [list_diff.DiffOp("update", 1, 1)], ops

# Remove + insert without reordering emits targeted remove + insert
ops2 = list_diff.compute_list_diff(
    [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}],
    [{"id": "a", "v": 1}, {"id": "c", "v": 3}, {"id": "d", "v": 4}],
    lambda r: r["id"],
)
assert [o.kind for o in ops2] == ["remove", "insert"], ops2

# Reordering falls back cleanly to reset
ops3 = list_diff.compute_list_diff(
    [{"id": "a"}, {"id": "b"}],
    [{"id": "b"}, {"id": "a"}],
    lambda r: r["id"],
)
assert ops3 == [list_diff.DiffOp("reset", 0, 1)], ops3

# Pure-Python ViewModel helpers (zero Qt)
assert chorus_viewmodel.resolve_timing_ref_mode("midi", has_score=False) == "lead"
assert chorus_viewmodel.resolve_timing_ref_mode("midi", has_score=True) == "midi"
assert chorus_viewmodel.resolve_timing_ref_mode("", has_score=True) == "midi"
styles = tuning_viewmodel.build_style_catalog_rows(style_catalog, lambda s: s)
assert len(styles) == len(style_catalog.VISIBLE_STYLES) and all("id" in r and "color" in r for r in styles)

# Verify split SettingsPage.qml and TuningPage.qml stay compact
sp_lines = len((APP / "src/quick_ui/SettingsPage.qml").read_text(encoding="utf-8").splitlines())
tp_lines = len((APP / "src/quick_ui/TuningPage.qml").read_text(encoding="utf-8").splitlines())
assert sp_lines <= 650, f"SettingsPage.qml regressed: {sp_lines}"
assert tp_lines <= 1900, f"TuningPage.qml regressed: {tp_lines}"

print("PASS: Tier 2 M2+M3 (vwa_config, SQLite task_index, MiniDump, list_diff, ViewModels, QML split)")
