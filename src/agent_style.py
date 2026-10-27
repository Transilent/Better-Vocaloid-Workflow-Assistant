"""Deterministic, local style planning and independent SVP candidate rendering.

The caller supplies semantic rationale and evidence.  This module performs no
emotion inference, model/provider/network access, synthesis, or listening.  It
aligns requested seconds to complete notes, turns the result into the existing
tuning_workflow option shape, and renders through an immutable local session.
A returned candidate is always the real SVP published by that workflow, never
a synthetic path or report.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable
from common import atomic_json, native_path


def _io_path(path):
    return Path(native_path(path))

SCHEMA_VERSION = 1
CONTEXT_KIND = "vwa.style-context"
PLAN_KIND = "vwa.style-plan"
RENDER_KIND = "vwa.style-result"
PRESET_IDS = ("none", "gentle", "sad", "bold")
TECHNIQUE_IDS = ("none", "chinese_opera")
REGION_KEYS = frozenset({
    "start", "end", "preset", "strength", "style_pitch",
    "style_technique", "technique_strength", "technique_pitch",
    "rationale", "evidence_refs", "detail",
})
REQUIRED_REGION_KEYS = frozenset({
    "start", "end", "preset", "strength", "style_pitch", "rationale",
})
PROPOSAL_KEYS = frozenset({"track_indices", "brief", "regions"})
_EPSILON = 1e-9
_MIN_REGION_SECONDS = 0.025
_MAX_CONTEXT_PAGE = 500
# Public style contexts print seconds with six decimals.  A boundary may be
# rounded by at most half a microsecond before it reaches this module.  Snap
# only to real note edges inside that bounded window so [a,b) + [b,c) remains
# adjacent without weakening the complete-note ownership check below.
_SECONDS_DISPLAY_PRECISION = 6
_DISPLAY_ROUNDING_TOLERANCE = 0.5 * 10 ** (-_SECONDS_DISPLAY_PRECISION) + _EPSILON
_DEFAULT_TECHNIQUE_STRENGTH = 0.6
_DEFAULT_TECHNIQUE_PITCH = True
_SV_QUARTER = 705600000


class StyleRegionOverlap(ValueError):
    """A complete-note style range overlaps another real style range."""

    code = "style_region_overlap"
    component = "agent_style"
    retryable = False
    remediation = ["vwa_context_read", "vwa_style_plan"]

    def __init__(
        self,
        message: str,
        *,
        shared_note_ids: list[str],
        previous_interval: dict[str, float],
        current_interval: dict[str, float],
        overlap_seconds: float,
    ) -> None:
        super().__init__(message)
        self.details = {
            "shared_note_ids": list(shared_note_ids),
            "previous_interval": dict(previous_interval),
            "current_interval": dict(current_interval),
            "overlap_seconds": float(overlap_seconds),
        }


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("值必须只包含有限的 JSON 数据。") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    return prefix + "_" + _digest(value)[:24]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with _io_path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ValueError("无法读取工程：" + str(path)) from error
    return digest.hexdigest()


def _source_path(path: Any) -> Path:
    if not isinstance(path, (str, bytes, os.PathLike)):
        raise ValueError("工程路径必须是字符串或路径对象。")
    try:
        result = Path(path).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("工程路径无效。") from error
    if result.suffix.lower() != ".svp":
        raise ValueError("Style worker 仅支持 SVP 工程；其他格式请先转换并核对音符与歌词。")
    if not _io_path(result).is_file():
        raise ValueError("SVP 工程不存在或不是文件：" + str(result))
    return result


def _read_source(path: Any) -> tuple[Path, bytes]:
    source = _source_path(path)
    try:
        raw = _io_path(source).read_bytes()
    except OSError as error:
        raise ValueError("无法读取 SVP 工程：" + str(source)) from error
    if not raw:
        raise ValueError("SVP 工程为空。")
    return source, raw


def _project(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig").rstrip("\x00")
        value = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("SVP 工程不是有效的 UTF-8 JSON。") from error
    if not isinstance(value, dict):
        raise ValueError("SVP 工程根节点必须为对象。")
    if not isinstance(value.get("tracks"), list):
        raise ValueError("SVP 工程缺少有效轨道列表。")
    if not isinstance(value.get("time"), dict):
        raise ValueError("SVP 工程缺少有效时间配置。")
    return value


def _tempo(project: dict[str, Any]):
    from vocal_expression import TempoMap

    rows = project["time"].get("tempo")
    if not isinstance(rows, list) or not rows:
        raise ValueError("SVP 工程缺少从工程起点开始的速度表，无法对齐秒与音符。")
    try:
        clock = TempoMap(rows)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("SVP 速度表无效：" + str(error)) from error
    output = []
    for row in sorted(rows, key=lambda item: item["position"]):
        output.append({
            "position_tick": row["position"],
            "bpm": float(row["bpm"]),
            "start_seconds": float(clock.to_seconds(row["position"])),
        })
    return clock, output


def _finite(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(label + "必须是有限数字。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(label + "必须是有限数字。")
    return number


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "必须是非空字符串。")
    return value


def _indices(value: Any, track_count: int, *, allow_none: bool) -> list[int] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError("track_indices 必须是非空且不重复的整数列表。")
    result = []
    for index in value:
        if type(index) is not int or not 0 <= index < track_count:
            raise ValueError("track_indices 包含无效轨道索引。")
        result.append(index)
    if len(result) != len(set(result)):
        raise ValueError("track_indices 不能重复。")
    return sorted(result)


def _preset_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from style_presets import PRESETS

    presets = []
    for preset_id in PRESET_IDS:
        definition = PRESETS[preset_id]
        presets.append({
            "id": preset_id,
            "label": definition.get("label"),
            "category": definition.get("category", "singing_style"),
            "pitch_default": definition.get("pitch_default", False),
            "description": definition.get("description"),
        })
    techniques = []
    for technique_id in TECHNIQUE_IDS:
        definition = PRESETS[technique_id]
        techniques.append({
            "id": technique_id,
            "label": definition.get("label"),
            "category": definition.get("category", "singing_technique"),
            "pitch_default": definition.get("pitch_default", False),
            "description": definition.get("description"),
        })
    return presets, techniques


def _lyric(note: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    raw = note.get("lyrics", note.get("lyric"))
    if not isinstance(raw, str):
        return None, None if raw is None else str(raw), False
    text = raw.strip()
    if not text or text == "+":
        return None, raw, False
    return text, raw, True


def _note_is_singing(note: dict[str, Any], lyric: str | None) -> bool:
    marker = (lyric or "").lower()
    attributes = note.get("attributes")
    muted = isinstance(attributes, dict) and attributes.get("muted") is True
    non_singing = (
        marker in ("sil", "sp", "pau", "br")
        or (marker.startswith("br") and marker[2:].isdigit())
        or note.get("musicalType") == "rap"
        or muted
    )
    return not non_singing


def _context_unknown(
    code: str, message: str, track_index: int,
    *, note_index: int | None = None,
) -> dict[str, Any]:
    value = {
        "code": code,
        "message": message,
        "track_index": track_index,
    }
    if note_index is not None:
        value["note_index"] = note_index
    return value


def _build_context(source: Path, raw: bytes) -> dict[str, Any]:
    project = _project(raw)
    clock, tempo_rows = _tempo(project)
    source_sha = _sha256_bytes(raw)
    track_rows: list[dict[str, Any]] = []
    note_rows: list[dict[str, Any]] = []
    unknown_rows: list[dict[str, Any]] = []

    for track_index, raw_track in enumerate(project["tracks"]):
        if not isinstance(raw_track, dict):
            track_id = _stable_id("track", {
                "source_sha256": source_sha,
                "track_index": track_index,
                "invalid": True,
            })
            track_rows.append({
                "track_id": track_id,
                "track_index": track_index,
                "name": None,
                "vocal": False,
                "instrumental": None,
                "offset_tick": None,
                "offset_quarters": None,
                "main_group_present": False,
                "note_count_raw": 0,
                "note_count_parsed": 0,
                "database": None,
                "voice": None,
                "eligible_for_style": False,
                "ineligibility_reasons": ["track.invalid"],
            })
            unknown_rows.append(_context_unknown(
                "track.invalid", "轨道不是对象，无法生成风格证据。", track_index,
            ))
            continue

        name = raw_track.get("name") if isinstance(raw_track.get("name"), str) else None
        track_id = _stable_id("track", {
            "source_sha256": source_sha,
            "track_index": track_index,
            "name": name,
        })
        reasons: list[str] = []

        def reason(code: str, message: str) -> None:
            if code not in reasons:
                reasons.append(code)
                unknown_rows.append(_context_unknown(code, message, track_index))

        raw_ref = raw_track.get("mainRef", {})
        if raw_ref is None:
            raw_ref = {}
        if not isinstance(raw_ref, dict):
            reason("track.main_ref_invalid", "mainRef 不是对象，不能安全应用风格。")
            raw_ref = {}

        instrumental_value = raw_ref.get("isInstrumental", False)
        if type(instrumental_value) is not bool:
            reason("track.instrumental_unknown", "伴奏标记不是布尔值，不能确认是否为演唱轨。")
            instrumental = None
            vocal = False
        else:
            instrumental = instrumental_value
            vocal = not instrumental_value
            if instrumental_value:
                reason("track.instrumental", "该轨道是伴奏轨，不能作为风格目标。")

        offset_value = raw_ref.get("blickOffset", 0)
        offset_ok = type(offset_value) is int
        if not offset_ok:
            reason("track.offset_invalid", "轨道 blickOffset 不是整数，无法安全换算工程秒。")
            offset = None
        else:
            offset = offset_value

        groups = raw_track.get("groups")
        if groups:
            reason("track.linked_groups", "当前风格引擎不支持链接库组。")
        begin = raw_ref.get("blickAbsoluteBegin", 0)
        end = raw_ref.get("blickAbsoluteEnd", -1)
        if begin != 0 or end != -1:
            reason("track.cropped", "当前风格引擎不支持裁剪后的演唱 mainGroup。")

        group = raw_track.get("mainGroup")
        raw_notes = group.get("notes") if isinstance(group, dict) else None
        if not isinstance(group, dict):
            reason("track.main_group_missing", "轨道缺少 mainGroup。")
            raw_notes = []
        elif not isinstance(raw_notes, list):
            reason("track.notes_invalid", "mainGroup.notes 不是列表。")
            raw_notes = []

        parsed_count = 0
        for note_index, raw_note in enumerate(raw_notes):
            if not isinstance(raw_note, dict):
                reason("note.invalid", "第 " + str(note_index) + " 个音符不是对象。")
                unknown_rows.append(_context_unknown(
                    "note.invalid", "音符不是对象。", track_index, note_index=note_index,
                ))
                continue
            onset = raw_note.get("onset")
            duration = raw_note.get("duration")
            if type(onset) is not int or type(duration) is not int or duration <= 0:
                reason("note.timing_invalid", "轨道包含非整数 onset/duration 或非正时长音符。")
                unknown_rows.append(_context_unknown(
                    "note.timing_invalid", "音符时间不是引擎可安全处理的范围。",
                    track_index, note_index=note_index,
                ))
                continue
            if offset is None:
                unknown_rows.append(_context_unknown(
                    "note.time_unknown", "轨道偏移无效，音符工程秒未知。",
                    track_index, note_index=note_index,
                ))
                continue
            try:
                project_onset = onset + offset
                start_seconds = float(clock.to_seconds(project_onset))
                end_seconds = float(clock.to_seconds(project_onset + duration))
            except (TypeError, ValueError, OverflowError) as error:
                reason("note.time_invalid", "音符时间无法通过速度表换算。")
                unknown_rows.append(_context_unknown(
                    "note.time_invalid", "音符时间无法换算：" + str(error),
                    track_index, note_index=note_index,
                ))
                continue
            if not all(math.isfinite(value) for value in (start_seconds, end_seconds)) or end_seconds <= start_seconds:
                reason("note.time_invalid", "音符换算后的起止秒无效。")
                unknown_rows.append(_context_unknown(
                    "note.time_invalid", "音符换算后的起止秒无效。",
                    track_index, note_index=note_index,
                ))
                continue

            lyric, lyric_raw, lyric_known = _lyric(raw_note)
            raw_pitch = raw_note.get("pitch")
            pitch_known = (
                type(raw_pitch) in (int, float)
                and math.isfinite(float(raw_pitch))
            )
            pitch = raw_pitch if pitch_known else None
            note_payload = {
                "source_sha256": source_sha,
                "track_index": track_index,
                "track_id": track_id,
                "note_index": note_index,
                "onset_tick": onset,
                "duration_tick": duration,
                "track_offset_tick": offset,
                "pitch": pitch,
                "lyric": lyric,
                "lyric_raw": lyric_raw,
            }
            note_id = _stable_id("note", note_payload)
            if not lyric_known:
                unknown_rows.append(_context_unknown(
                    "lyric.unknown", "歌词缺失或为未知占位符。",
                    track_index, note_index=note_index,
                ))
            if not pitch_known:
                unknown_rows.append(_context_unknown(
                    "pitch.unknown", "音符音高缺失或不是有限数字。",
                    track_index, note_index=note_index,
                ))
            note_rows.append({
                "note_id": note_id,
                "track_id": track_id,
                "track_index": track_index,
                "note_index": note_index,
                "pitch": pitch,
                "pitch_known": pitch_known,
                "onset_tick": onset,
                "duration_tick": duration,
                "track_offset_tick": offset,
                "project_onset_tick": project_onset,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "lyric": lyric,
                "lyric_raw": lyric_raw,
                "lyric_known": lyric_known,
                "singing": _note_is_singing(raw_note, lyric),
            })
            parsed_count += 1

        if parsed_count == 0:
            reason("track.no_notes", "轨道没有可安全定位的音符。")
        track_rows.append({
            "track_id": track_id,
            "track_index": track_index,
            "name": name,
            "vocal": vocal,
            "instrumental": instrumental,
            "offset_tick": offset,
            "offset_quarters": None if offset is None else offset / _SV_QUARTER,
            "main_group_present": isinstance(group, dict),
            "note_count_raw": len(raw_notes),
            "note_count_parsed": parsed_count,
            "database": copy.deepcopy(raw_ref.get("database")),
            "voice": copy.deepcopy(raw_ref.get("voice")),
            "eligible_for_style": bool(vocal and parsed_count and not reasons),
            "ineligibility_reasons": reasons,
        })

    presets, techniques = _preset_catalog()
    return {
        "schema": SCHEMA_VERSION,
        "kind": CONTEXT_KIND,
        "source_path": str(source),
        "source_sha256": source_sha,
        "format": "svp",
        "tempo": tempo_rows,
        "time_basis": {
            "tick": "SVP blick",
            "quarter_ticks": _SV_QUARTER,
            "note_fields": "onset_tick/duration_tick are source-relative; start_seconds/end_seconds include mainRef.blickOffset",
            "region_fields": "project seconds",
        },
        "tracks": track_rows,
        "notes": note_rows,
        "presets": presets,
        "preset_ids": list(PRESET_IDS),
        "techniques": techniques,
        "technique_ids": list(TECHNIQUE_IDS),
        "unknowns": unknown_rows,
        "semantic_model_used": False,
        "local_only": True,
    }


def _selected_context(context: dict[str, Any], indices: list[int]) -> dict[str, Any]:
    selected = set(indices)
    result = copy.deepcopy(context)
    result["track_indices"] = list(indices)
    result["scope"] = "all_tracks" if indices == list(range(len(context["tracks"]))) else "explicit"
    result["tracks"] = [row for row in context["tracks"] if row["track_index"] in selected]
    result["notes"] = [row for row in context["notes"] if row["track_index"] in selected]
    result["unknowns"] = [row for row in context["unknowns"] if row["track_index"] in selected]
    return result


def _validate_context_projection(
    limit: Any, offset: Any, detail: Any, include_note_ids: Any,
) -> None:
    if limit is not None and (
        type(limit) is not int or not 1 <= limit <= _MAX_CONTEXT_PAGE
    ):
        raise ValueError(
            "limit 必须是 1 到 " + str(_MAX_CONTEXT_PAGE) + " 的整数或 null。"
        )
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是大于等于 0 的整数。")
    if detail not in ("summary", "notes", "full"):
        raise ValueError("detail 必须是 summary、notes 或 full。")
    if type(include_note_ids) is not bool:
        raise ValueError("include_note_ids 必须是布尔值。")


def _context_note_summary(context: dict[str, Any]) -> dict[str, Any]:
    notes = context["notes"]
    grouped = {track["track_index"]: [] for track in context["tracks"]}
    for note in notes:
        grouped[note["track_index"]].append(note)
    by_track = []
    for track in context["tracks"]:
        rows = grouped[track["track_index"]]
        starts = [float(row["start_seconds"]) for row in rows]
        ends = [float(row["end_seconds"]) for row in rows]
        by_track.append({
            "track_index": track["track_index"],
            "name": track.get("name"),
            "eligible_for_style": bool(track.get("eligible_for_style")),
            "total": len(rows),
            "singing": sum(bool(row.get("singing")) for row in rows),
            "lyric_known": sum(bool(row.get("lyric_known")) for row in rows),
            "pitch_known": sum(bool(row.get("pitch_known")) for row in rows),
            "first_start_seconds": min(starts) if starts else None,
            "last_end_seconds": max(ends) if ends else None,
        })
    return {
        "total": len(notes),
        "singing": sum(bool(row.get("singing")) for row in notes),
        "lyric_known": sum(bool(row.get("lyric_known")) for row in notes),
        "pitch_known": sum(bool(row.get("pitch_known")) for row in notes),
        "unknown_count": len(context["unknowns"]),
        "by_track": by_track,
    }


def _project_context(
    context: dict[str, Any], *, limit: int | None, offset: int,
    detail: str, include_note_ids: bool,
) -> dict[str, Any]:
    """Project an already-built context without changing its source facts."""
    all_notes = context["notes"]
    total = len(all_notes)
    page_limit = total if limit is None else limit
    page = copy.deepcopy(all_notes[offset:offset + page_limit])
    # Current-page note IDs are edit/evidence handles, not a whole-track ID list.
    next_offset = offset + len(page) if offset + len(page) < total else None
    pagination = {
        "limit": page_limit,
        "offset": offset,
        "total": total,
        "returned": len(page),
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }

    if detail == "summary":
        result = copy.deepcopy(context)
        result["detail"] = "summary"
        result["note_summary"] = _context_note_summary(context)
        result["tracks"] = [
            {
                "track_id": row.get("track_id"),
                "track_index": row["track_index"],
                "name": row.get("name"),
                "vocal": row.get("vocal"),
                "instrumental": row.get("instrumental"),
                "note_count_raw": row.get("note_count_raw"),
                "note_count_parsed": row.get("note_count_parsed"),
                "eligible_for_style": row.get("eligible_for_style"),
                "ineligibility_reasons": copy.deepcopy(row.get("ineligibility_reasons", [])),
            }
            for row in context["tracks"]
        ]
        result["unknown_summary"] = {
            "count": len(context["unknowns"]),
            "codes": sorted({row.get("code") for row in context["unknowns"] if row.get("code")}),
        }
        result["unknowns"] = []
        result["notes_included"] = False
        result["notes"] = []
        result["pagination"] = {
            **pagination,
            "returned": 0,
            "has_more": False,
            "next_offset": None,
        }
        result["notes_tool"] = {
            "detail": "notes",
            "limit": limit or 20,
            "offset": offset,
            "include_note_ids": include_note_ids,
        }
        return result

    result = copy.deepcopy(context)
    result["detail"] = detail
    result["notes"] = page
    result["notes_included"] = True
    result["pagination"] = pagination
    return result


def read_context(
    path: Any,
    track_indices: list[int] | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
    detail: str = "full",
    include_note_ids: bool = True,
) -> dict[str, Any]:
    """Read revision-bound SVP evidence, optionally with a bounded projection.

    Calls that omit the projection keywords retain the original complete
    context shape.  MCP callers explicitly request ``notes`` pages so a single
    page can never be mistaken for the whole song.
    """
    source, raw = _read_source(path)
    context = _build_context(source, raw)
    indices = _indices(track_indices, len(context["tracks"]), allow_none=True)
    if indices is None:
        indices = list(range(len(context["tracks"])))
        selected = _selected_context(context, indices)
        selected["scope"] = "all_tracks"
    else:
        selected = _selected_context(context, indices)
    _validate_context_projection(limit, offset, detail, include_note_ids)
    if limit is None and offset == 0 and detail == "full" and include_note_ids:
        return selected
    return _project_context(
        selected, limit=limit, offset=offset,
        detail=detail, include_note_ids=include_note_ids,
    )


def _track_error(row: dict[str, Any]) -> str:
    codes = row.get("ineligibility_reasons") or []
    if "track.instrumental" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 是伴奏轨，不能作为风格目标。"
    if "track.instrumental_unknown" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 的伴奏标记未知，不能安全应用风格。"
    if "track.linked_groups" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 使用了链接库组；当前风格引擎不支持。"
    if "track.cropped" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 的主音符组已裁剪；当前风格引擎不支持。"
    if "track.offset_invalid" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 的时间偏移无效。"
    if "track.no_notes" in codes:
        return "所选轨道 " + str(row["track_index"]) + " 没有可用音符。"
    return (
        "所选轨道 " + str(row["track_index"])
        + " 不满足当前风格引擎的演唱轨合同：" + ",".join(codes)
    )


def _validate_proposal(
    proposal: Any, context: dict[str, Any],
) -> tuple[list[int], str, list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(proposal, dict):
        raise ValueError("proposal 必须是对象。")
    unknown = set(proposal) - PROPOSAL_KEYS
    missing = PROPOSAL_KEYS - set(proposal)
    if unknown:
        raise ValueError("不支持的 proposal 字段：" + ",".join(sorted(map(str, unknown))))
    if missing:
        raise ValueError("proposal 缺少字段：" + ",".join(sorted(missing)))

    indices = _indices(proposal["track_indices"], len(context["tracks"]), allow_none=False)
    assert indices is not None
    track_rows = {row["track_index"]: row for row in context["tracks"]}
    for index in indices:
        row = track_rows[index]
        if not row.get("eligible_for_style"):
            raise ValueError(_track_error(row))

    brief = _text(proposal["brief"], "proposal.brief")
    rows = proposal["regions"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("proposal.regions 必须是非空列表。")
    if len(rows) > 128:
        raise ValueError("风格时间片段最多 128 个。")

    selected = _selected_context(context, indices)
    note_catalog = {row["note_id"]: row for row in selected["notes"]}
    track_catalog = {row["track_id"]: row for row in selected["tracks"]}
    normalized: list[dict[str, Any]] = []
    for region_index, raw_region in enumerate(rows):
        label = "proposal.regions[" + str(region_index) + "]"
        if not isinstance(raw_region, dict):
            raise ValueError(label + " 必须是对象。")
        extra = set(raw_region) - REGION_KEYS
        absent = REQUIRED_REGION_KEYS - set(raw_region)
        if extra:
            raise ValueError(label + " 包含不支持的字段：" + ",".join(sorted(map(str, extra))))
        if absent:
            raise ValueError(label + " 缺少字段：" + ",".join(sorted(absent)))
        start = _finite(raw_region["start"], label + ".start")
        end = _finite(raw_region["end"], label + ".end")
        if start < 0:
            raise ValueError(label + ".start 不能为负数。")
        if end <= start:
            raise ValueError(label + ".end 必须大于 start。")
        preset = raw_region["preset"]
        if not isinstance(preset, str) or preset not in PRESET_IDS:
            raise ValueError(label + ".preset 必须是 none/gentle/sad/bold。")
        strength = _finite(raw_region["strength"], label + ".strength")
        if not 0 <= strength <= 1:
            raise ValueError(label + ".strength 必须在 0 到 1 之间。")
        style_pitch = raw_region["style_pitch"]
        if type(style_pitch) is not bool:
            raise ValueError(label + ".style_pitch 必须是布尔值。")
        rationale = _text(raw_region["rationale"], label + ".rationale")

        from style_presets import technique_settings,detail_settings

        try:
            technique = technique_settings({
                "style_technique": raw_region.get("style_technique", "none"),
                "technique_strength": raw_region.get(
                    "technique_strength", _DEFAULT_TECHNIQUE_STRENGTH,
                ),
                "technique_pitch": raw_region.get(
                    "technique_pitch", _DEFAULT_TECHNIQUE_PITCH,
                ),
            })
            detail=detail_settings(raw_region.get('detail'))
        except ValueError as error:
            raise ValueError(label + " 的唱腔设置无效：" + str(error)) from error
        if preset == "none" and technique["style_technique"] == "none":
            raise ValueError(label + " 未选择风格或唱腔，不能生成风格片段。")

        refs = raw_region.get("evidence_refs", [])
        if not isinstance(refs, list):
            raise ValueError(label + ".evidence_refs 必须是字符串列表。")
        if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(label + ".evidence_refs 只能包含非空字符串。")
        if len(refs) != len(set(refs)):
            raise ValueError(label + ".evidence_refs 不能重复。")
        for ref in refs:
            if ref not in note_catalog and ref not in track_catalog:
                raise ValueError(label + " 引用了当前源和所选轨道中不存在的证据：" + ref)

        normalized.append({
            "start": start,
            "end": end,
            "preset": preset,
            "strength": strength,
            "style_pitch": style_pitch,
            "detail": detail,
            "style_technique": technique["style_technique"],
            "technique_strength": technique["technique_strength"],
            "technique_pitch": technique["technique_pitch"],
            "rationale": rationale,
            "evidence_refs": sorted(refs),
        })

    normalized.sort(key=lambda row: (row["start"], row["end"]))
    return indices, brief, normalized, selected


def _components(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for note in sorted(
        notes,
        key=lambda row: (
            row["start_seconds"], row["end_seconds"],
            row["track_index"], row["note_index"],
        ),
    ):
        start = float(note["start_seconds"])
        end = float(note["end_seconds"])
        if components and start < components[-1]["end"] - _EPSILON:
            current = components[-1]
            current["end"] = max(current["end"], end)
            current["note_ids"].append(note["note_id"])
        else:
            components.append({
                "start": start,
                "end": end,
                "note_ids": [note["note_id"]],
            })
    return components


def _snap_display_boundary(value: float, boundaries: list[float]) -> float:
    """Map a six-decimal display value back to a nearby real note edge."""
    if not boundaries:
        return value
    nearest = min(boundaries, key=lambda candidate: (abs(candidate - value), candidate))
    if abs(nearest - value) <= _DISPLAY_ROUNDING_TOLERANCE:
        return nearest
    return value


def _align_regions(
    regions: list[dict[str, Any]], selected_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    components = _components(selected_context["notes"])
    boundaries = sorted({
        float(component[edge])
        for component in components
        for edge in ("start", "end")
    })
    aligned: list[dict[str, Any]] = []
    for row in regions:
        # Public seconds are rounded to six decimals.  Restore an exact edge
        # only when the displayed value is within half a display unit; this
        # preserves [a,b) adjacency without widening arbitrary overlaps.
        first = _snap_display_boundary(float(row["start"]), boundaries)
        last = _snap_display_boundary(float(row["end"]), boundaries)
        if last <= first:
            raise ValueError("六位秒数回映到整音符边界后不再是有效时间片段。")
        touched = [
            component for component in components
            if component["start"] < last and component["end"] > first
        ]
        if not touched:
            raise ValueError(
                "片段 " + f"{first:.6f}-{last:.6f}s 位于静音区，没有可对齐的音符。"
            )
        start = min(first, touched[0]["start"])
        end = max(last, touched[-1]["end"])
        if end - start < _MIN_REGION_SECONDS - _EPSILON:
            raise ValueError("整音符对齐后的时间片段过短，当前调音引擎无法生成有效选区。")
        note_ids = sorted({
            note_id for component in touched for note_id in component["note_ids"]
        })
        aligned.append({
            "start": start,
            "end": end,
            "requested_start": first,
            "requested_end": last,
            "aligned_note_ids": note_ids,
            "preset": row["preset"],
            "strength": row["strength"],
            "style_pitch": row["style_pitch"],
            "detail": copy.deepcopy(row['detail']),
            "style_technique": row["style_technique"],
            "technique_strength": row["technique_strength"],
            "technique_pitch": row["technique_pitch"],
            "rationale": row["rationale"],
            "evidence_refs": list(row["evidence_refs"]),
        })

    aligned.sort(key=lambda row: (row["start"], row["end"]))
    for previous, current in zip(aligned, aligned[1:]):
        shared_note_ids = sorted(
            set(previous["aligned_note_ids"]) & set(current["aligned_note_ids"])
        )
        previous_interval = {
            "start": float(previous["start"]),
            "end": float(previous["end"]),
        }
        current_interval = {
            "start": float(current["start"]),
            "end": float(current["end"]),
        }
        overlap_seconds = max(0.0, previous_interval["end"] - current_interval["start"])
        if shared_note_ids:
            raise StyleRegionOverlap(
                "整音符对齐后风格片段重叠并共用完整音符："
                + f"{previous['start']:.6f}-{previous['end']:.6f}s 与 "
                + f"{current['start']:.6f}-{current['end']:.6f}s；"
                + "共有 " + str(len(shared_note_ids)) + " 个音符。",
                shared_note_ids=shared_note_ids,
                previous_interval=previous_interval,
                current_interval=current_interval,
                overlap_seconds=overlap_seconds,
            )
        if current["start"] < previous["end"]:
            raise StyleRegionOverlap(
                "整音符对齐后风格片段发生重叠："
                + f"{previous['start']:.6f}-{previous['end']:.6f}s 与 "
                + f"{current['start']:.6f}-{current['end']:.6f}s。",
                shared_note_ids=[],
                previous_interval=previous_interval,
                current_interval=current_interval,
                overlap_seconds=overlap_seconds,
            )

    semantic = []
    engine_regions = []
    for index, row in enumerate(aligned):
        semantic.append({
            "region_index": index,
            "start": row["start"],
            "end": row["end"],
            "requested_start": row["requested_start"],
            "requested_end": row["requested_end"],
            "preset": row["preset"],
            "strength": row["strength"],
            "style_pitch": row["style_pitch"],
            "detail": copy.deepcopy(row['detail']),
            "style_technique": row["style_technique"],
            "technique_strength": row["technique_strength"],
            "technique_pitch": row["technique_pitch"],
            "rationale": row["rationale"],
            "evidence_refs": row["evidence_refs"],
            "aligned_note_ids": row["aligned_note_ids"],
        })
        engine = {
            "start": row["start"],
            "end": row["end"],
            "preset": row["preset"],
            "strength": row["strength"],
            "detail": copy.deepcopy(row['detail']),
            "pitch": row["style_pitch"],
        }
        # The public semantic region always carries the complete technique
        # triple.  The legacy engine row receives it only when enabled.
        if row["style_technique"] != "none":
            engine.update({
                "technique": row["style_technique"],
                "technique_strength": row["technique_strength"],
                "technique_pitch": row["technique_pitch"],
            })
        engine_regions.append(engine)

    return semantic, engine_regions, [
        {
            "region_index": row["region_index"],
            "start": row["start"],
            "end": row["end"],
            "requested_start": row["requested_start"],
            "requested_end": row["requested_end"],
            "note_ids": row["aligned_note_ids"],
        }
        for row in semantic
    ]


def _resolved_evidence(
    semantic_regions: list[dict[str, Any]], selected_context: dict[str, Any],
) -> list[dict[str, Any]]:
    note_catalog = {row["note_id"]: row for row in selected_context["notes"]}
    track_catalog = {row["track_id"]: row for row in selected_context["tracks"]}
    refs = sorted({
        ref for region in semantic_regions for ref in region["evidence_refs"]
    })
    evidence = []
    for ref in refs:
        if ref in note_catalog:
            note = note_catalog[ref]
            evidence.append({
                "ref": ref,
                "type": "note",
                "track_index": note["track_index"],
                "note_index": note["note_index"],
                "lyric": note["lyric"],
                "start_seconds": note["start_seconds"],
                "end_seconds": note["end_seconds"],
            })
        else:
            track = track_catalog[ref]
            evidence.append({
                "ref": ref,
                "type": "track",
                "track_index": track["track_index"],
                "name": track["name"],
                "database": copy.deepcopy(track["database"]),
            })
    return evidence


def _engine_options(
    indices: list[int], engine_regions: list[dict[str, Any]],
) -> dict[str, Any]:
    from tuning_engine import settings

    options = {
        "expression_mode": "preserve",
        "track_indices": list(indices),
        "performance_style": "none",
        "style_strength": 0.0,
        "style_pitch": False,
        "style_detail": {
            "onset": 1.0,
            "sustain": 1.0,
            "connection": 1.0,
            "release": 1.0,
        },
        "style_regions": copy.deepcopy(engine_regions),
        "expression_density": "original",
        "style_technique": "none",
        "technique_strength": 0.0,
        "technique_pitch": False,
    }
    try:
        settings(options)
    except ValueError as error:
        raise ValueError("生成的调音选项未通过现有引擎校验：" + str(error)) from error
    return options


def plan_style(path: Any, proposal: dict[str, Any]) -> dict[str, Any]:
    """Validate caller semantics and align them to complete notes in SVP seconds."""
    source, raw = _read_source(path)
    context = _build_context(source, raw)
    indices, brief, normal_regions, selected = _validate_proposal(proposal, context)
    semantic, engine_regions, aligned_ranges = _align_regions(normal_regions, selected)

    from style_presets import region_settings

    try:
        checked_regions = region_settings(engine_regions)
    except ValueError as error:
        raise ValueError("对齐后的引擎片段未通过现有风格校验：" + str(error)) from error
    if checked_regions != engine_regions:
        raise ValueError("对齐后的引擎片段被现有风格规范化意外改写，已停止。")

    engine_options = _engine_options(indices, checked_regions)
    evidence = _resolved_evidence(semantic, selected)
    normalized_proposal = {
        "track_indices": list(indices),
        "brief": brief,
        "regions": [
            {
                "start": row["requested_start"],
                "end": row["requested_end"],
                "preset": row["preset"],
                "strength": row["strength"],
                "style_pitch": row["style_pitch"],
                "detail": copy.deepcopy(row['detail']),
                "style_technique": row["style_technique"],
                "technique_strength": row["technique_strength"],
                "technique_pitch": row["technique_pitch"],
                "rationale": row["rationale"],
                "evidence_refs": list(row["evidence_refs"]),
            }
            for row in semantic
        ],
    }
    result = {
        "schema": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "source_path": str(source),
        "source_sha256": context["source_sha256"],
        "track_indices": list(indices),
        "brief": brief,
        "proposal": normalized_proposal,
        "regions": semantic,
        "aligned_ranges": aligned_ranges,
        "rationale": [
            {
                "region_index": row["region_index"],
                "text": row["rationale"],
                "evidence_refs": list(row["evidence_refs"]),
            }
            for row in semantic
        ],
        "evidence": evidence,
        "engine_options": engine_options,
        "style_regions": copy.deepcopy(engine_options["style_regions"]),
        "settings": {
            "expression_mode": "preserve",
            "expression_density": "original",
            "note_boundary_alignment": "complete_note_range",
            "target_scope": "global_track_indices",
        },
        "context_unknowns": copy.deepcopy(selected["unknowns"]),
        "limitations": [
            "语义理由和 evidence_refs 由调用方提供；本模块不推断情绪或调用语义模型。",
            "仅支持未裁剪、未链接库组的 SVP 演唱 mainGroup。",
            "只生成真实调音工程候选；不进行声库合成、试听或网络验证。",
            "风格目标由全局 track_indices 决定，region 不接受单独 target 覆盖。",
        ],
        "semantic_model_used": False,
        "local_only": True,
    }
    result["plan_sha256"] = _digest(result)
    return result


def _verify_plan_digest(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("plan 必须是对象。")
    stored = plan.get("plan_sha256")
    if (
        not isinstance(stored, str) or len(stored) != 64
        or any(char not in "0123456789abcdef" for char in stored)
    ):
        raise ValueError("plan_sha256 必须是小写 SHA-256。")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    try:
        actual = _digest(unsigned)
    except ValueError as error:
        raise ValueError("plan 含有无效 JSON 数据。") from error
    if actual != stored:
        raise ValueError("计划摘要与当前 plan 内容不一致，计划可能已被篡改。")
    return copy.deepcopy(plan)


def _cancel_checker(cancel: Callable[[], Any] | None) -> Callable[[], None]:
    if cancel is not None and not callable(cancel):
        raise ValueError("cancel 必须可调用或为 None。")

    def check() -> None:
        if cancel is not None:
            value = cancel()
            if value is True:
                raise InterruptedError("已取消风格候选生成。")

    return check


def _progress_callback(progress: Callable[[str], Any] | None):
    if progress is not None and not callable(progress):
        raise ValueError("progress 必须可调用或为 None。")
    if progress is None:
        return lambda text: None
    return progress


_PARTIAL_REASON_ADVICE = {
    "short_note_preserved": "短音符已按原值保留；可缩短选区边界，或避免对其强制应用音高与包络控制。",
    "protected_note_preserved": "受保护或校准的音符未被改写；如确需调整，请先核对保护规则，再显式允许变化。",
    "overlapping_notes": "音高或包络目标存在重叠；请检查同一轨道的单声部冲突后再生成风格候选。",
    "non_singing_or_rap": "气口、静音或说唱音符未套用演唱风格；请在需要表现的目标音符上重新划定片段。",
    "unsupported_nonlinear_controls": "部分原有非线性控制无法自动加密；已保留原曲线，试听后再决定是否重建控制点。",
    "unsupported_fractional_time_controls": "部分控制点使用非整数时间，未自动加密；请保留原曲线或在 GUI 中显式重采样。",
}


def _partial_evidence(report: Any) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    """Count actionable partial reasons from the immutable tuning report."""
    counts: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            reason = value.get("reason")
            if isinstance(reason, str) and reason and reason not in ("disabled", "zero_strength"):
                counts[reason] = counts.get(reason, 0) + 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(report)
    reason_counts = dict(sorted(counts.items()))
    rows = []
    advice = []
    for reason, count in reason_counts.items():
        action = _PARTIAL_REASON_ADVICE.get(
            reason,
            "请核对 report 中该原因对应的跳过明细；修复输入条件后再生成候选。",
        )
        rows.append({"reason": reason, "count": count, "action": action})
        advice.append(reason + "（" + str(count) + "）：" + action)
    return reason_counts, rows, advice


def _relocate_candidate_media(
    source: Path, candidate: Path, *, allowed_roots: list[Path],
) -> dict[str, Any]:
    """Copy supported source media and rewrite candidate paths in place."""
    from agent_media import relocate_svp_media

    evidence = relocate_svp_media(
        source,
        candidate,
        allowed_roots=[str(path) for path in allowed_roots],
    )
    if not isinstance(evidence, dict):
        raise ValueError("agent_media.relocate_svp_media 必须返回证据对象。")
    return copy.deepcopy(evidence)


def _allowed_media_roots(
    values: Any, source: Path, output_root: Path,
) -> list[Path]:
    """Combine caller-authorized media roots with the two intrinsic roots."""
    if values is None:
        explicit: list[Any] = []
    elif isinstance(values, (list, tuple)):
        explicit = list(values)
    else:
        raise ValueError("allowed_media_roots 必须是路径列表或 null。")
    roots: list[Path] = [source.parent.resolve(), output_root.resolve()]
    for value in explicit:
        if not isinstance(value, (str, bytes, os.PathLike)):
            raise ValueError("allowed_media_roots 只能包含路径。")
        try:
            roots.append(Path(value).expanduser().resolve())
        except (OSError, TypeError, ValueError) as error:
            raise ValueError("allowed_media_roots 包含无效路径。") from error
    return sorted(set(roots))


def render_style(
    path: Any,
    plan: dict[str, Any],
    output_root: Any,
    progress: Callable[[str], Any] | None = None,
    cancel: Callable[[], Any] | None = None,
    *,
    allowed_media_roots: list[Any] | None = None,
) -> dict[str, Any]:
    """Render a real independent SVP candidate through tuning_workflow."""
    check_cancel = _cancel_checker(cancel)
    progress_callback = _progress_callback(progress)
    check_cancel()
    saved = _verify_plan_digest(plan)

    source, initial_raw = _read_source(path)
    if saved.get("source_path") != str(source):
        raise ValueError("计划绑定的源工程路径与当前 path 不一致。")
    current_sha = _sha256_bytes(initial_raw)
    if saved.get("source_sha256") != current_sha:
        raise ValueError("源 SVP 已改变；请基于当前文件重新生成风格计划。")

    proposal = saved.get("proposal")
    expected = plan_style(source, proposal)
    if expected.get("source_sha256") != current_sha:
        raise ValueError("源 SVP 在读取计划期间发生变化；请重新生成风格计划。")
    if expected != saved:
        raise ValueError("计划内容与当前源、证据或整音符对齐结果不一致，计划无效。")

    check_cancel()
    try:
        root = Path(output_root).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("output_root 无效。") from error
    if _io_path(root).exists() and not _io_path(root).is_dir():
        raise ValueError("output_root 必须是目录，不是文件。")
    try:
        _io_path(root).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("无法创建输出目录：" + str(root)) from error
    media_roots = _allowed_media_roots(allowed_media_roots, source, root)

    import tuning_workflow
    from tuning_engine import settings as engine_settings

    session_dir = root / ("agent-style-" + saved["plan_sha256"][:24])
    if _io_path(session_dir).exists():
        if not _io_path(session_dir).is_dir():
            raise ValueError("确定性会话路径已存在但不是目录：" + str(session_dir))
        session = session_dir / "session.json"
        if not _io_path(session).is_file():
            raise ValueError("确定性会话目录缺少 session.json，拒绝复用。")
        spec = tuning_workflow.read_session(session)
        if (
            spec.get("source_sha256") != current_sha
            or Path(spec.get("source", "")).resolve() != source
        ):
            raise ValueError("确定性会话绑定了不同源工程，拒绝复用。")
        last_options = session_dir / "last-options.json"
        if _io_path(last_options).is_file():
            try:
                previous_options = json.loads(_io_path(last_options).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError("现有会话选项记录无效，拒绝复用。") from error
            expected_options = engine_settings(saved["engine_options"])
            if previous_options != expected_options:
                raise ValueError("确定性会话已使用不同选项，拒绝复用或叠加。")
    else:
        session = tuning_workflow.create_session(source, session_dir)
        spec = tuning_workflow.read_session(session)
        if spec.get("source_sha256") != current_sha:
            raise ValueError("创建会话期间源 SVP 已改变；请重新生成风格计划。")

    check_cancel()
    result = tuning_workflow.run_session(
        session,
        copy.deepcopy(saved["engine_options"]),
        cancel=check_cancel,
        progress=progress_callback,
    )

    candidate_value = result.get("output")
    if not isinstance(candidate_value, str) or not candidate_value:
        raise ValueError("调音工作流未返回候选工程路径。")
    candidate = Path(candidate_value).resolve()
    if not _io_path(candidate).is_file():
        raise ValueError("调音工作流返回的候选工程不存在：" + str(candidate))
    tuning_candidate_sha = _file_sha256(candidate)
    if tuning_candidate_sha != result.get("output_sha256"):
        raise ValueError("候选 SVP 哈希与调音回执不一致。")
    if candidate == source:
        raise ValueError("候选 SVP 不得覆盖源工程。")
    if _file_sha256(source) != current_sha:
        raise ValueError("生成候选期间源 SVP 发生变化；候选已保留，请核对后重试。")

    receipt_value = result.get("receipt")
    receipt = Path(receipt_value).resolve() if isinstance(receipt_value, str) and receipt_value else None
    if receipt is None or not _io_path(receipt).is_file():
        raise ValueError("调音工作流未返回实际存在的候选回执。")
    if result.get("receipt_verified") is not True:
        raise ValueError("调音候选回执未通过完整性验证。")
    receipt_sha = _file_sha256(receipt)
    try:
        receipt_document = json.loads(_io_path(receipt).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("调音回执无法读取。") from error
    if (
        not isinstance(receipt_document, dict)
        or receipt_document.get("output_sha256") != tuning_candidate_sha
        or receipt_document.get("source_sha256") != current_sha
    ):
        raise ValueError("调音回执与候选或源工程身份不一致。")

    check_cancel()
    progress_callback("保留源工程伴奏并改写候选中的相对媒体引用")
    media_relocation = _relocate_candidate_media(
        source, candidate, allowed_roots=media_roots,
    )
    if not _io_path(candidate).is_file():
        raise ValueError("媒体迁移后候选工程不存在：" + str(candidate))
    if candidate == source:
        raise ValueError("媒体迁移不得把候选覆盖为源工程。")
    if _file_sha256(source) != current_sha:
        raise ValueError("媒体迁移期间源 SVP 发生变化；候选已保留，请核对后重试。")
    candidate_raw = _io_path(candidate).read_bytes()
    candidate_project = _project(candidate_raw)
    # The final identity must cover the rewritten media paths and copied
    # accompaniment, not merely the pre-relocation tuning output.
    candidate_sha = _sha256_bytes(candidate_raw)
    candidate_bytes = len(candidate_raw)
    serialized_content_sha = _digest(candidate_project)
    original_tuning_sha = receipt_document.get("tuning_output_sha256", tuning_candidate_sha)
    receipt_document.update({
        "tuning_output_sha256": original_tuning_sha,
        "output_sha256": candidate_sha,
        "output_bytes": candidate_bytes,
        "serialized_content_sha256": serialized_content_sha,
        "media_references": copy.deepcopy(media_relocation.get("tracks", [])),
        "media_relocation": copy.deepcopy(media_relocation),
    })
    try:
        atomic_json(receipt, receipt_document)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("媒体迁移后无法更新候选调音回执；候选已保留。") from error
    receipt_sha = _file_sha256(receipt)
    partial_reason_counts, partial_remediation, partial_advice = _partial_evidence(result.get("report"))
    report = copy.deepcopy(result.get("report"))
    if isinstance(report, dict):
        report["partial_reason_counts"] = copy.deepcopy(partial_reason_counts)
        report["partial_remediation"] = copy.deepcopy(partial_remediation)
    tuning_result = copy.deepcopy(result)
    tuning_result.update({
        "tuning_output_sha256": original_tuning_sha,
        "output_sha256": candidate_sha,
        "output_bytes": candidate_bytes,
        "serialized_content_sha256": serialized_content_sha,
        "media_references": copy.deepcopy(media_relocation.get("tracks", [])),
        "media_relocation": copy.deepcopy(media_relocation),
    })

    return {
        "schema": SCHEMA_VERSION,
        "kind": RENDER_KIND,
        "plan_sha256": saved["plan_sha256"],
        "source_sha256": current_sha,
        "source_path": str(source),
        "source_unchanged": True,
        "session": str(Path(session).resolve()),
        "session_id": spec.get("id"),
        "options_sha256": _digest(saved["engine_options"]),
        "candidate_path": str(candidate),
        "candidate_sha256": candidate_sha,
        "candidate_bytes": candidate_bytes,
        "tuning_candidate_sha256": original_tuning_sha,
        "receipt_matches_final_candidate": True,
        "media_relocation": media_relocation,
        "partial_reason_counts": partial_reason_counts,
        "partial_remediation": partial_remediation,
        "partial_advice": partial_advice,
        # Preserve existing tuning-result field names as aliases.
        "output": str(candidate),
        "output_sha256": candidate_sha,
        "receipt": str(receipt) if receipt is not None else None,
        "receipt_sha256": receipt_sha,
        "signature": result.get("signature"),
        "status": result.get("status"),
        "cached": bool(result.get("cached")),
        "receipt_verified": result.get("receipt_verified"),
        "report": report,
        "semantic_model_used": False,
        "synthesized": False,
        "listened": False,
        "tuning_result": tuning_result,
    }
