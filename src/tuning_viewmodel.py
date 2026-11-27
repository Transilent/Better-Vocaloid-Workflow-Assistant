"""Qt-free pure-Python ViewModel projection helpers for the Tuning workspace."""
from __future__ import annotations

from typing import Any, Callable


def build_track_rows(project: Any, translate: Callable[[str], str]) -> list[dict[str, Any]]:
    if not project:
        return []
    return [
        {**row, "reason": translate(row["reason"]) if row.get("reason") else ""}
        for row in project.tracks.values()
    ]


def build_style_catalog_rows(style_catalog_mod: Any, translate: Callable[[str], str]) -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": translate(style_catalog_mod.style_label(key)),
            "short": translate("不叠加" if key == "none" else style_catalog_mod.style_region_label(key)),
            "color": style_catalog_mod.style_color(key),
        }
        for key in style_catalog_mod.VISIBLE_STYLES
    ]


def build_technique_catalog_rows(style_catalog_mod: Any, translate: Callable[[str], str]) -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": translate(style_catalog_mod.technique_label(key)),
            "short": translate(style_catalog_mod.technique_region_label(key) or style_catalog_mod.technique_label(key)),
            "color": style_catalog_mod.technique_color(key),
        }
        for key in style_catalog_mod.TECHNIQUE_IDS
    ]


def build_binding_rows(project: Any, options: dict[str, Any], active_indices: set[int] | list[int]) -> list[dict[str, Any]]:
    if not project:
        return []
    reference = {row["track_index"]: row for row in options.get("reference_sources", ())}
    retained = {row["track_index"]: row for row in options.get("retained_sources", ())}
    rows = []
    for key in project.editable:
        index = project.tracks[key]["index"]
        if index not in active_indices:
            continue
        rows.append(
            {
                "trackId": key,
                "trackName": project.tracks[key]["name"],
                "reference": reference.get(index, {}).get("path", ""),
                "referenceVoice": reference.get(index, {}).get("voice", ""),
                "retained": retained.get(index, {}).get("project", ""),
                "retainedIndex": retained.get(index, {}).get("source_track_index", 0),
            }
        )
    return rows
