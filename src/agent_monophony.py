"""Deterministic monophonic-export preflight over a VWA score view.

``monophonic_conflicts(score, limit=100)`` accepts the JSON-compatible mapping
returned by ``agent_score.read_score`` and reads ``score["notes"]``. Each note
must provide these score fields:

* ``note_id`` (unique non-empty string)
* ``track_index`` (non-negative integer)
* ``channel`` (integer 0..15, or ``None`` for an SVP track)
* ``pitch`` (finite number in 0..127)
* ``onset_tick`` and ``end_tick`` (finite values, end greater than onset)

Optional ``onset_seconds`` and ``end_seconds`` fields are used when present so
conflicts can report an exact playback-time duration. Tick intervals are
half-open, ``[onset_tick, end_tick)``; touching endpoints do not overlap.

MIDI voices are scoped by ``(track_index, channel)``. This matches the
exporter's current rule that multiple channels may become separate target
voices. Notes on different tracks or different channels are not conflicts,
even when they overlap in time. Every positive overlap inside one scope is a
``code == "monophonic_overlap"`` row.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import math
from typing import Any


CONFLICT_CODE = "monophonic_overlap"
REQUIRED_NOTE_FIELDS = (
    "note_id",
    "track_index",
    "channel",
    "pitch",
    "onset_tick",
    "end_tick",
)
MONOPHONIC_SCOPE_FIELDS = ("track_index", "channel")


def _finite_number(value: Any, field_name: str) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} 必须是有限数值；布尔值不算数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数值。")
    return int(value) if type(value) is int else number


def _integer(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} 必须是整数；布尔值不算整数。")
    if value < minimum or (maximum is not None and value > maximum):
        upper = "" if maximum is None else f"..{maximum}"
        raise ValueError(f"{field_name} 必须是 {minimum}{upper} 范围内的整数。")
    return value


def _optional_seconds(note: Mapping[str, Any], field_name: str, index: int) -> float | None:
    if field_name not in note or note.get(field_name) is None:
        return None
    value = note.get(field_name)
    if type(value) not in (int, float):
        raise ValueError(f"score.notes[{index}].{field_name} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"score.notes[{index}].{field_name} 必须是有限数值。")
    return number


def _normalise_notes(score: Mapping[str, Any]) -> list[dict[str, Any]]:
    notes = score.get("notes")
    if not isinstance(notes, (list, tuple)):
        raise ValueError("score.notes 必须是数组。")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, note in enumerate(notes):
        if not isinstance(note, Mapping):
            raise ValueError(f"score.notes[{index}] 必须是对象。")
        missing = [field for field in REQUIRED_NOTE_FIELDS if field not in note]
        if missing:
            raise ValueError(
                f"score.notes[{index}] 缺少字段：{', '.join(missing)}。"
            )
        note_id = note.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise ValueError(f"score.notes[{index}].note_id 必须是非空字符串。")
        if note_id in seen_ids:
            raise ValueError(f"score.notes 中 note_id 重复：{note_id}。")
        seen_ids.add(note_id)
        track_index = _integer(
            note.get("track_index"),
            f"score.notes[{index}].track_index",
            minimum=0,
        )
        channel = note.get("channel")
        if channel is not None:
            channel = _integer(
                channel,
                f"score.notes[{index}].channel",
                minimum=0,
                maximum=15,
            )
        pitch = _finite_number(note.get("pitch"), f"score.notes[{index}].pitch")
        if not 0 <= float(pitch) <= 127:
            raise ValueError(f"score.notes[{index}].pitch 必须在 0..127 范围内。")
        onset_tick = _finite_number(
            note.get("onset_tick"),
            f"score.notes[{index}].onset_tick",
        )
        end_tick = _finite_number(
            note.get("end_tick"),
            f"score.notes[{index}].end_tick",
        )
        if onset_tick < 0:
            raise ValueError(f"score.notes[{index}].onset_tick 必须是非负数。")
        if end_tick <= onset_tick:
            raise ValueError(
                f"score.notes[{index}] 的 end_tick 必须大于 onset_tick。"
            )
        result.append({
            "note_id": note_id,
            "track_index": track_index,
            "channel": channel,
            "pitch": pitch,
            "onset_tick": onset_tick,
            "end_tick": end_tick,
            "onset_seconds": _optional_seconds(note, "onset_seconds", index),
            "end_seconds": _optional_seconds(note, "end_seconds", index),
        })
    return result


def _scope_sort_key(key: tuple[int, int | None]) -> tuple[int, int]:
    return key[0], -1 if key[1] is None else key[1]


def _note_sort_key(note: dict[str, Any]) -> tuple[Any, ...]:
    return (
        note["onset_tick"],
        note["end_tick"],
        float(note["pitch"]),
        note["note_id"],
    )


def _overlap_seconds(
    first: dict[str, Any],
    second: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    values = (
        first["onset_seconds"],
        first["end_seconds"],
        second["onset_seconds"],
        second["end_seconds"],
    )
    if any(value is None for value in values):
        return None, None, None
    start = float(second["onset_seconds"])
    end = min(float(first["end_seconds"]), float(second["end_seconds"]))
    if end <= start:
        return None, None, None
    return round(start, 9), round(end, 9), round(end - start, 9)


def monophonic_conflicts(
    score: Mapping[str, Any],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return deterministic positive overlaps within monophonic voices.

    ``limit`` bounds returned rows. ``limit=0`` is valid and returns an empty
    list. The function does not mutate ``score`` or its nested mappings.
    """
    if not isinstance(score, Mapping):
        raise ValueError("score 必须是 read_score 返回的对象。")
    if type(limit) is not int:
        raise ValueError("limit 必须是整数；布尔值不算整数。")
    if limit < 0:
        raise ValueError("limit 必须是非负整数。")
    if limit == 0:
        return []

    grouped: dict[tuple[int, int | None], list[dict[str, Any]]] = defaultdict(list)
    for note in _normalise_notes(score):
        grouped[(note["track_index"], note["channel"])].append(note)

    conflicts: list[dict[str, Any]] = []
    for (track_index, channel), group in sorted(
        grouped.items(),
        key=lambda item: _scope_sort_key(item[0]),
    ):
        ordered = sorted(group, key=_note_sort_key)
        for left_index, first in enumerate(ordered):
            for second in ordered[left_index + 1:]:
                overlap_start = second["onset_tick"]
                if overlap_start >= first["end_tick"]:
                    break
                overlap_end = min(first["end_tick"], second["end_tick"])
                if overlap_end <= overlap_start:
                    continue
                overlap_ticks = overlap_end - overlap_start
                start_seconds, end_seconds, overlap_seconds = _overlap_seconds(
                    first,
                    second,
                )
                left_pitch = float(first["pitch"])
                right_pitch = float(second["pitch"])
                conflict: dict[str, Any] = {
                    "code": CONFLICT_CODE,
                    "severity": "error",
                    "track_index": track_index,
                    "channel": channel,
                    "voice_key": {
                        "track_index": track_index,
                        "channel": channel,
                    },
                    "note_ids": [first["note_id"], second["note_id"]],
                    "pitches": [first["pitch"], second["pitch"]],
                    "cross_pitch": left_pitch != right_pitch,
                    "start_tick": overlap_start,
                    "end_tick": overlap_end,
                    "overlap_ticks": overlap_ticks,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "overlap_seconds": overlap_seconds,
                    "overlap_milliseconds": (
                        None
                        if overlap_seconds is None
                        else round(overlap_seconds * 1000.0, 6)
                    ),
                }
                conflict["message"] = (
                    f"轨道 {track_index}、通道 {channel} 的目标单声部在 "
                    f"{overlap_start}..{overlap_end} tick 发生音符重叠；"
                    "转换前请保留合法分声部或调整其中一个音符时间。"
                )
                conflicts.append(conflict)
                if len(conflicts) >= limit:
                    return conflicts
    return conflicts


__all__ = [
    "CONFLICT_CODE",
    "MONOPHONIC_SCOPE_FIELDS",
    "REQUIRED_NOTE_FIELDS",
    "monophonic_conflicts",
]
