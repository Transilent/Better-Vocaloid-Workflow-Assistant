"""Tier 3 user-facing UX infrastructure regression suite (L0).

Covers T3-M1 (and grows with M2-M5):
  1. tutorial_catalog single source of truth covers all 11 workspaces (incl. chorus,
     project_convert, tuning_workshop, results) + 4 scenario cards + What's New + shortcuts.
  2. startup_preferences What's New & page-tip persistence without disturbing legacy tour state.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get("BVWA_TESTING") == "1"

import startup_preferences as sp
import tutorial_catalog as tc

# --- T3-M1: Tutorial catalog & What's New preferences ---
step_ids = [row["pageId"] for row in tc.TOUR_STEPS]
assert len(step_ids) == 11 and len(set(step_ids)) == 11
for must_have in ("processing", "results", "tuning", "tuning_workshop", "chorus", "project_convert", "audio_mixdown", "video_mixdown", "publishing", "components", "updates"):
    assert must_have in step_ids, f"Missing workspace in tour catalog: {must_have}"

assert len(tc.SCENARIO_CARDS) == 4
assert {c["pageId"] for c in tc.SCENARIO_CARDS} == {"processing", "chorus", "tuning", "project_convert"}
assert len(tc.WHATS_NEW_HIGHLIGHTS) >= 3
assert len(tc.SHORTCUT_GROUPS) >= 4

with tempfile.TemporaryDirectory(dir=APP / "work", prefix="tier3-m1-") as tmp:
    root = Path(tmp)
    (root / "config.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    prefs = sp.load_preferences(root)
    # New users see onboarding tour first, NOT What's New simultaneously
    assert sp.tour_due(prefs) is True
    assert sp.whats_new_due(tc.FEATURE_HIGHLIGHTS_VERSION, prefs) is False

    # Existing users who already completed tour see What's New once per feature version
    sp.finish_tour(root, "completed")
    prefs2 = sp.load_preferences(root)
    assert sp.tour_due(prefs2) is False
    assert sp.whats_new_due(tc.FEATURE_HIGHLIGHTS_VERSION, prefs2) is True
    sp.dismiss_whats_new(root, tc.FEATURE_HIGHLIGHTS_VERSION)
    prefs3 = sp.load_preferences(root)
    assert sp.whats_new_due(tc.FEATURE_HIGHLIGHTS_VERSION, prefs3) is False
    assert sp.whats_new_due("9.9.9", prefs3) is True

    # Per-page micro-tips persistence & reset
    assert sp.page_tip_seen(prefs3, "chorus") is False
    sp.mark_page_tip_seen(root, "chorus")
    assert sp.page_tip_seen(sp.load_preferences(root), "chorus") is True
    sp.reset_page_tips(root)
    assert sp.page_tip_seen(sp.load_preferences(root), "chorus") is False

# --- T3-M2: Multi-monitor-safe window geometry & route persistence + WorkspaceStage LRU ---
import workspace_ui_state as ui_state

with tempfile.TemporaryDirectory(dir=APP / "work", prefix="tier3-m2-") as tmp:
    root = Path(tmp)
    ui_state.save_last_route("chorus", root=root)
    assert ui_state.load_last_route(root) == "chorus"
    ui_state.save_last_route("nonexistent_route", root=root)
    assert ui_state.load_last_route(root) == "chorus", "Invalid routes must not overwrite last_route"

    # Valid window on primary screen clamps cleanly
    screens = [(0, 0, 1920, 1080)]
    geo = ui_state.clamp_window_geometry({"x": 100, "y": 80, "width": 1440, "height": 900, "maximized": True}, screens)
    assert geo == {"x": 100, "y": 80, "width": 1440, "height": 900, "maximized": True}

    # Unplugged second monitor (saved at x=2400) safely falls back to None (primary centered)
    offscreen = ui_state.clamp_window_geometry({"x": 2400, "y": 100, "width": 1280, "height": 800}, screens)
    assert offscreen is None, "Offscreen unplugged-monitor geometry must not be restored blindly"

    # Oversized window clamps to screen bounds
    clamped = ui_state.clamp_window_geometry({"x": 50, "y": 50, "width": 2560, "height": 1440}, screens)
    assert clamped is not None and clamped["width"] == 1920 and clamped["height"] == 1080

ws_stage = (APP / "src/quick_ui/WorkspaceStage.qml").read_text(encoding="utf-8")
assert 'objectName: "pageLoader"' in ws_stage and "maxCachedPages" in ws_stage and "lruRoutes" in ws_stage

# --- T3-M3: Crash-safe draft recovery, Windows taskbar progress & Toast stack ---
import draft_recovery as dr
import win_taskbar as wt
from chorus_project import ChorusProject
from mixdown_workspace_state import MixdownWorkspaceState

with tempfile.TemporaryDirectory(dir=APP / "work", prefix="tier3-m3-") as tmp:
    root = Path(tmp)
    # 1. Chorus draft write / read / clear
    proj = ChorusProject.fresh("测试合唱草稿")
    assert dr.has_draft("chorus", root) is False
    dr.write_chorus_draft(proj, source_path="demo.vwachorus", root=root)
    assert dr.has_draft("chorus", root) is True
    meta = dr.read_draft_meta("chorus", root)
    assert meta is not None and "测试合唱草稿" in meta["label"]
    assert dr.chorus_draft_project_path(root).is_file()
    assert dr.clear_draft("chorus", root) is True
    assert dr.has_draft("chorus", root) is False

    # 2. Mixdown draft save / apply roundtrip
    fake_wav = root / "vocal.wav"
    fake_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    st = MixdownWorkspaceState()
    st.set_source("vocal-1", str(fake_wav))
    st.set_track("vocal-1", "gain", -3.5)
    st.set_option("reverb_enabled", True)
    st.set_option("reverb_mix", 0.22)
    payload = dr.mixdown_state_to_draft(st)
    dr.save_draft("mixdown", payload, label="vocal", root=root)
    assert dr.has_draft("mixdown", root) is True

    restored_st = MixdownWorkspaceState()
    loaded = dr.load_draft("mixdown", root)
    assert loaded is not None
    restored_count = dr.apply_mixdown_draft(restored_st, loaded["payload"])
    assert restored_count == 1
    assert restored_st.reverb_enabled is True
    assert abs(restored_st.reverb_mix - 0.22) < 1e-6
    v1 = next(t for t in restored_st.tracks if t["id"] == "vocal-1")
    assert abs(v1["gain"] - (-3.5)) < 1e-6
    assert dr.clear_draft("mixdown", root) is True

# 3. Windows taskbar pure state reducer & safe disabled no-op
assert wt.compute_taskbar_state([]) == ("none", 0, 0)
assert wt.compute_taskbar_state([], has_recent_error=True) == ("error", 0, 0)
assert wt.compute_taskbar_state([{"kind": "preparation"}]) == ("indeterminate", 0, 0)
assert wt.compute_taskbar_state([{"fraction": 0.425}]) == ("progress", 425, 1000)
assert wt.compute_taskbar_state([{"done": 3, "total": 10}]) == ("progress", 3, 10)
tb = wt.TaskbarProgress(hwnd=0, enabled=False)
tb.sync_operations([{"fraction": 0.5}])
tb.sync_operations([])
tb.close()

# 4. Toast stack & RecoveryBanner QML contracts
toast_qml = (APP / "src/quick_ui/UiToastStack.qml").read_text(encoding="utf-8")
assert 'objectName: "uiToastStack"' in toast_qml and "onToastRequested" in toast_qml
banner_qml = (APP / "src/quick_ui/RecoveryBanner.qml").read_text(encoding="utf-8")
assert 'objectName: "recoveryBanner"' in banner_qml and "restoreChorusDraft" in banner_qml and "restoreMixdownDraft" in banner_qml

# --- T3-M4: Global MRU, window-level smart drop & full Ctrl+K palette ---
import recent_items as ri

with tempfile.TemporaryDirectory(dir=APP / "work", prefix="tier3-m4-") as tmp:
    root = Path(tmp)
    f1 = root / "song_a.vwachorus"
    f2 = root / "song_b.svp"
    f3 = root / "missing.ustx"
    f1.write_text("{}", encoding="utf-8")
    f2.write_text("{}", encoding="utf-8")
    f3.write_text("{}", encoding="utf-8")

    ri.record_recent(f1, "chorus", title="Song A", root=root)
    ri.record_recent(f2, "tuning", title="Song B", root=root)
    ri.record_recent(f3, "project_convert", title="Song C", root=root)
    ri.record_recent(f1, "chorus", title="Song A Updated", root=root)
    rows = ri.list_recent(root=root)
    assert len(rows) == 3 and rows[0]["title"] == "Song A Updated"

    # Missing file auto-prunes cleanly
    f3.unlink()
    rows2 = ri.list_recent(root=root)
    assert len(rows2) == 2 and all(r["fileName"] != "missing.ustx" for r in rows2)
    ri.clear_recent(root=root)
    assert ri.list_recent(root=root) == []

# Smart drop classifier
d_chorus = ri.classify_dropped_files(["/tmp/test.vwachorus"])
assert d_chorus["category"] == "chorus_project" and len(d_chorus["targets"]) == 1
d_svp = ri.classify_dropped_files(["/tmp/song.svp"])
assert d_svp["category"] == "vocal_project" and {t["id"] for t in d_svp["targets"]} == {"tuning_project", "convert_project", "chorus_score"}
d_wav = ri.classify_dropped_files(["/tmp/vocal.wav"])
assert d_wav["category"] == "audio" and {t["id"] for t in d_wav["targets"]} == {"prep_audio", "chorus_audio", "mixdown_vocal"}
d_mp4 = ri.classify_dropped_files(["/tmp/pv.mp4"])
assert d_mp4["category"] == "video" and {t["id"] for t in d_mp4["targets"]} == {"video_source", "prep_audio"}

pal_qml = (APP / "src/quick_ui/UiCommandPalette.qml").read_text(encoding="utf-8")
assert "project_convert:" in pal_qml and "recentItems" in pal_qml and "mixdown:toggle-ab" in pal_qml
drop_qml = (APP / "src/quick_ui/GlobalDropOverlay.qml").read_text(encoding="utf-8")
assert 'objectName: "globalDropOverlay"' in drop_qml and "inspectDropUrls" in drop_qml

print("PASS: Tier 3 M1-M4 (tutorial, LRU stage, recovery/taskbar/toast, MRU/smart-drop/Ctrl+K)")
