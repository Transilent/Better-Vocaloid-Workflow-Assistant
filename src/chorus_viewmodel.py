"""Qt-free pure-Python ViewModel projection helpers for the Chorus Workbench."""
from __future__ import annotations

from typing import Any


def resolve_timing_ref_mode(requested_mode: str, *, has_score: bool) -> str:
    """Resolve effective timing reference mode given whether a score lane is loaded."""
    if requested_mode in ("midi", "lead", "ensemble"):
        if requested_mode == "midi" and not has_score:
            return "lead"
        return requested_mode
    return "midi" if has_score else "lead"


def build_lyric_row(line: Any) -> dict[str, Any]:
    if not line:
        return {}
    return {"text": line.text, "language": line.language, "confirmed": line.confirmed}


def build_track_row(project: Any, track: Any, feat: Any = None) -> dict[str, Any]:
    return {
        "id": track.id,
        "name": track.name,
        "role": track.role,
        "part": track.part,
        "offset": track.offset,
        "duration": track.duration,
        "muted": track.muted,
        "scoreLane": track.score_lane,
        "scoreOctave": track.score_octave,
        "timeRef": track.timing_ref,
        "lead": project.guide == track.id,
        "wetWarning": bool(feat.wet_warning) if feat is not None else False,
        "sideMidDb": float(feat.side_mid_db) if feat is not None else -120.0,
        "edits": sum(edit.track == track.id for edit in project.edits),
        "timing": [
            {
                "start": edit.start,
                "end": edit.end,
                "shift": edit.value,
                "points": [list(point) for point in edit.points],
            }
            for edit in project.edits_for(track.id, "timing")
        ],
        "takes": [
            {"start": edit.start, "end": edit.end, "id": edit.source}
            for edit in project.edits_for(track.id, "take")
        ],
    }


def build_phrase_rows(project: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, phrase in enumerate(project.phrases, 1):
        line = phrase.lyric() or next(iter(phrase.lyrics), None)
        rows.append(
            {
                "start": phrase.start,
                "end": phrase.end,
                "name": line.text if line else phrase.label or f"唱段 {index}",
                "confirmed": bool(line and line.confirmed),
            }
        )
    return rows
