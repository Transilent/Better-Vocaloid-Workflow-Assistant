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

print("PASS: Tier 3 M1+M2 (11-step tutorial, scenarios, What's New, shortcuts, LRU stage, window/route state)")
