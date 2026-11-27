"""Multi-monitor-safe window geometry & last-workspace persistence (zero Qt)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from vwa_io import atomic_json
from vwa_paths import ROOT

UI_STATE_REL = Path("cache/ui-state.json")
VALID_RESTORE_ROUTES = frozenset(
    (
        "processing",
        "results",
        "tuning",
        "tuning_workshop",
        "chorus",
        "project_convert",
        "audio_mixdown",
        "video_mixdown",
        "publishing",
        "components",
        "updates",
    )
)


def load_ui_state(root=ROOT) -> dict[str, Any]:
    path = Path(root).resolve() / UI_STATE_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def save_ui_state(changes: dict[str, Any], root=ROOT) -> dict[str, Any]:
    root_path = Path(root).resolve()
    state = load_ui_state(root_path)
    state.update(changes)
    try:
        atomic_json(root_path / UI_STATE_REL, state)
    except OSError:
        pass
    return state


def save_last_route(route: str, root=ROOT) -> None:
    key = str(route or "").strip()
    if key in VALID_RESTORE_ROUTES:
        save_ui_state({"last_route": key}, root=root)


def load_last_route(root=ROOT, *, default: str = "processing") -> str:
    route = load_ui_state(root).get("last_route")
    return route if isinstance(route, str) and route in VALID_RESTORE_ROUTES else default


def clamp_window_geometry(
    saved: dict[str, Any] | None,
    screens: Sequence[tuple[int, int, int, int]],
    *,
    min_width: int = 960,
    min_height: int = 640,
) -> dict[str, Any] | None:
    """Validate saved window bounds against currently connected monitor work areas.

    Returns a safe ``{x, y, width, height, maximized}`` dict if the window is
    visible on at least one connected screen; returns ``None`` if no saved state
    or if the previous monitor was unplugged (caller falls back to primary screen).
    """
    if not isinstance(saved, dict) or not screens:
        return None
    try:
        x = int(saved["x"])
        y = int(saved["y"])
        w = int(saved["width"])
        h = int(saved["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w < min_width or h < min_height or w > 16384 or h > 16384:
        return None

    # Ensure at least 160x80 of the top window area intersects a live screen
    for sx, sy, sw, sh in screens:
        ix1 = max(x, sx)
        iy1 = max(y, sy)
        ix2 = min(x + w, sx + sw)
        iy2 = min(y + min(h, 120), sy + sh)
        if (ix2 - ix1) >= 160 and (iy2 - iy1) >= 48:
            clamped_w = min(w, sw)
            clamped_h = min(h, sh)
            clamped_x = max(sx, min(x, sx + sw - 160))
            clamped_y = max(sy, min(y, sy + sh - 80))
            return {
                "x": clamped_x,
                "y": clamped_y,
                "width": clamped_w,
                "height": clamped_h,
                "maximized": bool(saved.get("maximized", False)),
            }
    return None
