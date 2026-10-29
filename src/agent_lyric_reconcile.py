"""Pure, preview-only reconciliation of timed lyric lines to MIDI notes.

The caller supplies a raw ``agent_control.lyrics_read`` result and timed lyric
lines.  This module never reads or writes files, invokes model inference, or
changes a score.  Its ``edits`` list has the shape accepted by
``agent_lyrics.plan_lyrics_patch``; producing that list is only a suggestion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from numbers import Real
import unicodedata
from typing import Any


_SCHEMA_VERSION = 1
_PREVIEW_KIND = "vwa-lyrics-reconcile-preview"
_DIFF_SCHEMA = "vwa-lyrics-reconcile-diff-v1"
_CONTINUATION_CHARS = frozenset(
    "~～〜-‐‑‒–—―…⋯·・,，.。!！?？:：;；、'’\"“”「」『』（）()[]［］【】 \t\r\n\u3000"
)
_SAFE_ALIGNMENTS = frozenset({"aligned", "missing"})

__all__ = ["reconcile_lyrics"]


def _finite_seconds(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数值。")
    return number


def _optional_seconds(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _finite_seconds(value, field_name)


def _comparison_tokens(text: str) -> list[str]:
    """Use the same singable-character rule as agent_lyrics.

    Whitespace, punctuation, and explicit continuation markers do not consume a
    character position.  Unicode is normalized to NFC before counting.
    """

    tokens: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        category = unicodedata.category(char)
        if char.isspace() or category[0] in {"P", "Z"} or char in _CONTINUATION_CHARS:
            continue
        tokens.append(char)
    return tokens


def _normalise_line(line: Any, line_index: int) -> dict[str, Any]:
    if not isinstance(line, Mapping):
        raise ValueError(f"lines[{line_index}] 必须是对象。")
    for key in ("start", "end", "text"):
        if key not in line:
            raise ValueError(f"lines[{line_index}] 缺少 {key}。")
    if not isinstance(line["text"], str):
        raise ValueError(f"lines[{line_index}].text 必须是字符串。")
    start = _finite_seconds(line["start"], f"lines[{line_index}].start")
    end = _finite_seconds(line["end"], f"lines[{line_index}].end")
    if end <= start:
        raise ValueError(f"lines[{line_index}] 的 end 必须大于 start。")
    text = line["text"]
    tokens = _comparison_tokens(text)
    return {
        "line_index": line_index,
        "start": start,
        "end": end,
        "text": text,
        "tokens": tokens,
        "raw_char_count": len(unicodedata.normalize("NFC", text)),
    }


def _track_name(read: Mapping[str, Any], track_index: int) -> str | None:
    tracks = read.get("tracks")
    if not isinstance(tracks, Sequence) or isinstance(tracks, (str, bytes)):
        return None
    for track in tracks:
        if isinstance(track, Mapping) and track.get("track_index") == track_index:
            value = track.get("track_name")
            return value if isinstance(value, str) else None
    return None


def _normalise_notes(read: Mapping[str, Any], track_index: int) -> list[dict[str, Any]]:
    rows = read.get("notes")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("read.notes 必须是数组。")
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"read.notes[{index}] 必须是对象。")
        if raw.get("track_index") != track_index:
            continue
        note_id = raw.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise ValueError(f"read.notes[{index}].note_id 必须是非空字符串。")
        if note_id in seen_ids:
            raise ValueError(f"轨道内出现重复 note_id：{note_id}。")
        seen_ids.add(note_id)
        onset = _finite_seconds(
            raw.get("onset_seconds"), f"read.notes[{index}].onset_seconds",
        )
        end = _optional_seconds(
            raw.get("end_seconds"), f"read.notes[{index}].end_seconds",
        )
        lyric = raw.get("lyric")
        if lyric is not None and not isinstance(lyric, str):
            raise ValueError(f"read.notes[{index}].lyric 必须是字符串或 null。")
        competing = raw.get("competing_lyric_event_ids")
        if competing is None:
            competing_ids: list[str] = []
        elif isinstance(competing, Sequence) and not isinstance(competing, (str, bytes)):
            competing_ids = [item for item in competing if isinstance(item, str)]
        else:
            competing_ids = []
        selected.append({
            "note_id": note_id,
            "track_index": track_index,
            "channel": raw.get("channel"),
            "pitch": raw.get("pitch"),
            "onset_tick": raw.get("onset_tick"),
            "end_tick": raw.get("end_tick"),
            "onset_seconds": onset,
            "end_seconds": end,
            "duration_seconds": (
                round(end - onset, 9) if end is not None and end > onset else None
            ),
            "lyric": lyric,
            "lyric_event_id": raw.get("lyric_event_id"),
            "lyric_alignment": raw.get("lyric_alignment"),
            "pairing_ambiguous": bool(raw.get("pairing_ambiguous")),
            "competing_lyric_event_ids": competing_ids,
        })
    selected.sort(key=lambda row: (
        row["onset_seconds"],
        row["onset_tick"] if isinstance(row["onset_tick"], int) else -1,
        row["pitch"] if isinstance(row["pitch"], int) else -1,
        row["note_id"],
    ))
    return selected


def _overlapping_lines(lines: list[dict[str, Any]]) -> dict[int, list[int]]:
    overlaps: dict[int, list[int]] = {}
    for left_index, left in enumerate(lines):
        for right_index in range(left_index + 1, len(lines)):
            right = lines[right_index]
            if max(left["start"], right["start"]) < min(left["end"], right["end"]):
                overlaps.setdefault(left_index, []).append(right_index)
                overlaps.setdefault(right_index, []).append(left_index)
    for indexes in overlaps.values():
        indexes.sort()
    return overlaps


def _decision(
    code: str,
    line_index: int,
    message: str,
    *,
    note_ids: Sequence[str] = (),
    required_action: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "blocking",
        "line_index": line_index,
        "note_ids": list(note_ids),
        "message": message,
        "required_action": required_action,
        "requires_confirmation": True,
        "auto_apply": False,
    }


def _same_onset_groups(notes: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    groups: dict[tuple[Any, Any], list[str]] = {}
    for note in notes:
        key = (
            note.get("onset_tick"),
            round(float(note["onset_seconds"]), 9),
        )
        groups.setdefault(key, []).append(str(note["note_id"]))
    return [ids for ids in groups.values() if len(ids) > 1]


def _note_evidence_problem(note: Mapping[str, Any]) -> str | None:
    if note["pairing_ambiguous"]:
        return "note_pairing_ambiguous"
    # lyrics_read includes the uniquely associated event in this evidence list;
    # only extra or conflicting identities represent competition.
    competing = note["competing_lyric_event_ids"]
    if competing and not (note["lyric_alignment"] == "aligned"
                          and len(competing) == 1
                          and competing[0] == note["lyric_event_id"]):
        return "competing_lyric_events"
    alignment = note["lyric_alignment"]
    if alignment not in _SAFE_ALIGNMENTS:
        return "unsafe_lyric_alignment"
    if alignment == "aligned":
        if not isinstance(note["lyric"], str) or not note["lyric_event_id"]:
            return "incomplete_aligned_evidence"
    elif note["lyric"] is not None:
        return "incomplete_missing_evidence"
    return None


def _hold_candidate(
    line: Mapping[str, Any], notes: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if len(line["tokens"]) != 1 or len(notes) < 2:
        return None
    if any(_note_evidence_problem(note) is not None for note in notes):
        return None
    character = line["tokens"][0]
    conditional_edits = []
    for index, note in enumerate(notes):
        conditional_edits.append({
            "note_id": note["note_id"],
            "lyric": character if index == 0 else "-",
            "operation": "replace" if note["lyric_alignment"] == "aligned" else "insert",
        })
    return {
        "kind": "conditional_hold_sequence",
        "conditional_edits": conditional_edits,
        "requires_confirmation": True,
        "applied": False,
        "included_in_edits": False,
        "rationale": "首音符承载文字、后续音符使用延音标记只是候选；无法证明该字确实跨越全部音符。",
    }


def _existing_hold_blockers(
    line: Mapping[str, Any], notes: Sequence[Mapping[str, Any]],
    duplicate_event_ids: set[str],
) -> list[str]:
    blockers: set[str] = set()
    if not notes or not any(note["lyric"] == "-" for note in notes):
        blockers.add("no_explicit_hold")
    if notes and notes[0]["lyric"] == "-":
        blockers.add("leading_hold")
    if any("\x00" in token for token in line["tokens"]):
        blockers.add("invalid_patch_character")
    for note in notes:
        event_id = note["lyric_event_id"]
        if (_note_evidence_problem(note) is not None or note["lyric_alignment"] != "aligned"
                or not isinstance(event_id, str) or not event_id):
            blockers.add("non_unique_or_missing_event")
        if isinstance(event_id, str) and event_id in duplicate_event_ids:
            blockers.add("shared_event")
        if (note["end_seconds"] is None or note["end_seconds"] <= note["onset_seconds"]
                or note["end_seconds"] > line["end"]):
            blockers.add("note_crosses_or_has_unknown_window_end")
        if (isinstance(note["lyric"], str) and note["lyric"] != "-"
                and not _comparison_tokens(note["lyric"])):
            blockers.add("non_singable_existing_lyric")
    if sum(note["lyric"] != "-" for note in notes) != len(line["tokens"]):
        blockers.add("non_hold_count_mismatch")
    return sorted(blockers)


def _existing_holds_candidate(
    line: Mapping[str, Any], notes: Sequence[Mapping[str, Any]],
    duplicate_event_ids: set[str],
) -> dict[str, Any] | None:
    """Preview a mapping only when the existing explicit holds are usable."""
    if _existing_hold_blockers(line, notes, duplicate_event_ids):
        return None

    characters = iter(line["tokens"])
    rows = []
    edits = []
    for note in notes:
        after = "-" if note["lyric"] == "-" else next(characters)
        operation = "keep" if note["lyric"] == after else "replace"
        rows.append({
            "note_id": note["note_id"],
            "onset_seconds": note["onset_seconds"],
            "lyric_event_id": note["lyric_event_id"],
            "before_lyric": note["lyric"],
            "proposed_lyric": after,
            "operation": operation,
        })
        if operation == "replace":
            edits.append({"note_id": note["note_id"], "lyric": after, "operation": "replace"})
    return {
        "kind": "conditional_preserve_existing_holds",
        "uncertain": True,
        "requires_confirmation": True,
        "applied": False,
        "included_in_edits": False,
        "compatible_with_plan_lyrics_patch": True,
        "conditional_edits": edits,
        "proposals": rows,
        "before_lyrics": [row["before_lyric"] for row in rows],
        "after_lyrics": [row["proposed_lyric"] for row in rows],
        "rationale": (
            "按现有明确 '-' 位置保留延音，其余音符依次对应歌词字符；"
            "原延音可能掩盖漏词，须逐音符人工确认后才能复制 conditional_edits 到歌词补丁计划。"
        ),
    }


def _split_capacity_candidate(
    line: Mapping[str, Any], notes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    needed = max(0, len(line["tokens"]) - len(notes))
    eligible = [
        note for note in notes
        if note["end_seconds"] is not None
        and note["end_seconds"] > note["onset_seconds"]
    ]
    eligible.sort(key=lambda row: (
        -float(row["duration_seconds"] or 0.0),
        row["onset_seconds"],
        row["note_id"],
    ))
    extra_parts = {note["note_id"]: 0 for note in eligible}
    if eligible and needed:
        for index in range(needed):
            extra_parts[eligible[index % len(eligible)]["note_id"]] += 1
    suggestions = []
    for note in eligible:
        extra = extra_parts[note["note_id"]]
        if not extra:
            continue
        split_into = extra + 1
        start = float(note["onset_seconds"])
        end = float(note["end_seconds"])
        span = end - start
        boundaries = [
            round(start + span * part / split_into, 9)
            for part in range(split_into + 1)
        ]
        suggestions.append({
            "note_id": note["note_id"],
            "pitch": note["pitch"],
            "channel": note["channel"],
            "split_into": split_into,
            "pieces": [
                {
                    "start_seconds": boundaries[part],
                    "end_seconds": boundaries[part + 1],
                }
                for part in range(split_into)
            ],
            "preserves_original_span": True,
        })
    return {
        "kind": "equal_duration_capacity_suggestion",
        "additional_note_count_needed": needed,
        "suggested_additional_note_count": sum(extra_parts.values()),
        "suggestions": suggestions,
        "requires_confirmation": True,
        "applied": False,
        "included_in_edits": False,
        "compatible_with_plan_lyrics_patch": False,
        "rationale": "仅按音符时长补足容量并等分区间；不推断字与音节边界，不生成可套用的文本补丁。",
    }


def _manual_note_view(note: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "note_id": note["note_id"],
        "track_index": note["track_index"],
        "channel": note["channel"],
        "pitch": note["pitch"],
        "onset_tick": note["onset_tick"],
        "end_tick": note["end_tick"],
        "onset_seconds": note["onset_seconds"],
        "end_seconds": note["end_seconds"],
        "current_lyric": note["lyric"],
        "lyric_event_id": note["lyric_event_id"],
        "lyric_alignment": note["lyric_alignment"],
        "pairing_ambiguous": note["pairing_ambiguous"],
        "competing_lyric_event_ids": list(note["competing_lyric_event_ids"]),
    }


def _evaluate_line(
    line: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    overlap_indexes: Sequence[int],
    duplicate_event_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    relationship = "empty"
    character_count = len(line["tokens"])
    note_count = len(notes)
    note_ids = [note["note_id"] for note in notes]
    before_sequence = [note["lyric"] for note in notes]
    before_text = "".join(
        value for value in before_sequence if isinstance(value, str)
    )

    if overlap_indexes:
        issues.append(_decision(
            "overlapping_line_windows",
            line["line_index"],
            "该行时间窗与其他行重叠，交集内的音符可能被跨行复用。",
            note_ids=note_ids,
            required_action="先修正 start/end，使除端点相接外的各行时间窗互不重叠。",
        ))
    groups = _same_onset_groups(notes)
    if groups:
        issues.append(_decision(
            "same_onset_notes",
            line["line_index"],
            "行内存在同一 onset 的多个音符，歌词事件无法唯一归属。",
            note_ids=[note_id for group in groups for note_id in group],
            required_action="人工选择承载该歌词事件的音符，或先解决同时起音的音符结构。",
        ))
    pairing_ambiguous = [
        note["note_id"] for note in notes if note["pairing_ambiguous"]
    ]
    if pairing_ambiguous:
        issues.append(_decision(
            "ambiguous_note_pairing",
            line["line_index"],
            "行内音符的 note_on/note_off 配对不唯一，不能生成安全歌词编辑。",
            note_ids=pairing_ambiguous,
            required_action="先修复重叠或未闭合的音符事件。",
        ))
    evidence_problems: dict[str, list[str]] = {}
    for note in notes:
        problem = _note_evidence_problem(note)
        if problem is not None:
            evidence_problems.setdefault(problem, []).append(note["note_id"])
    if evidence_problems:
        issue_ids = [note_id for ids in evidence_problems.values() for note_id in ids]
        issues.append(_decision(
            "unsafe_lyric_evidence",
            line["line_index"],
            "行内音符没有唯一、可安全 replace/insert 的歌词归属证据。",
            note_ids=issue_ids,
            required_action="逐音符核对当前 lyrics 事件；存在竞争事件时先单独清理或移动事件。",
        ))

    if notes and notes[0]["lyric"] == "-":
        issues.append(_decision(
            "leading_hold",
            line["line_index"],
            "行首已有 '-'，其所属文字可能位于窗口之外，不能推断归属。",
            note_ids=[notes[0]["note_id"]],
            required_action="人工确认该延音和行边界，再决定歌词映射。",
        ))
    elif (character_count >= note_count and note_count
          and any(note["lyric"] == "-" for note in notes)):
        issues.append(_decision(
            "existing_hold_requires_review",
            line["line_index"],
            "现有延音位置与逐音符字数无法同时满足，不能自动覆盖延音。",
            note_ids=[note["note_id"] for note in notes if note["lyric"] == "-"],
            required_action="人工核对延音是否漏词及音符是否需要拆分。",
        ))

    if character_count == 0 and note_count:
        relationship = "no_singable_characters"
        issues.append(_decision(
            "empty_text_with_notes",
            line["line_index"],
            "该行没有可唱字符但时间窗内有音符；不应自行清空或借用其他行文字。",
            note_ids=note_ids,
            required_action="确认该行是否纯器乐、漏填歌词，或时间窗是否错误。",
        ))
    elif character_count and not note_count:
        relationship = "more_characters_than_notes"
        issues.append(_decision(
            "no_notes_in_line_window",
            line["line_index"],
            "该行有歌词字符但严格时间窗内没有音符。",
            required_action="补充或修正音符/行时间；不得借用相邻行音符补位。",
        ))
    elif character_count == note_count:
        relationship = "one_to_one"
    elif character_count == 1 and note_count > 1:
        relationship = "one_character_many_notes"
        issues.append(_decision(
            "one_character_many_notes",
            line["line_index"],
            "一个字符对应多个命中音符，无法证明是延音还是歌词缺失。",
            note_ids=note_ids,
            required_action="人工确认该字覆盖范围；若确为延音，再决定首音符文字与后续 '-' 标记。",
        ))
    elif character_count > note_count:
        relationship = "more_characters_than_notes"
        issues.append(_decision(
            "more_characters_than_notes",
            line["line_index"],
            f"行内 {character_count} 个可唱字符多于 {note_count} 个音符，需要人工拆分音节/音符。",
            note_ids=note_ids,
            required_action="人工选择要拆分的音符与字音节边界；容量建议不等于已确认的音乐结构。",
        ))
    else:
        relationship = "fewer_characters_than_notes"
        issues.append(_decision(
            "fewer_characters_than_notes",
            line["line_index"],
            f"行内 {character_count} 个可唱字符少于 {note_count} 个音符，无法判断哪些音符属于一字多音。",
            note_ids=note_ids,
            required_action="人工标明延音归属；不得按顺序自行填充字符或延音标记。",
        ))

    safe_to_map = not issues and character_count == note_count
    proposals: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    after_sequence: list[str] | None = None
    if safe_to_map:
        after_sequence = list(line["tokens"])
        for note, character in zip(notes, after_sequence):
            if note["lyric_alignment"] == "aligned":
                operation = "keep" if note["lyric"] == character else "replace"
            else:
                operation = "insert"
            proposal = {
                "note_id": note["note_id"],
                "line_index": line["line_index"],
                "onset_seconds": note["onset_seconds"],
                "before_lyric": note["lyric"],
                "proposed_lyric": character,
                "operation": operation,
                "lyric_alignment": note["lyric_alignment"],
                "lyric_event_id": note["lyric_event_id"],
            }
            proposals.append(proposal)
            if operation != "keep":
                edit = {
                    "note_id": note["note_id"],
                    "lyric": character,
                    "operation": operation,
                }
                edits.append(edit)

    conditional: dict[str, Any] | None = None
    conditional_hold_blockers: list[str] = []
    if relationship in {"one_character_many_notes", "fewer_characters_than_notes"}:
        # Count mismatch is expected; every other blocking issue excludes the
        # candidate.  Do not reinterpret an existing '-' as a guessed hold.
        mismatch_codes = {"one_character_many_notes", "fewer_characters_than_notes"}
        conditional_hold_blockers = _existing_hold_blockers(line, notes, duplicate_event_ids)
        conditional_hold_blockers.extend(
            issue["code"] for issue in issues if issue["code"] not in mismatch_codes
        )
        conditional_hold_blockers = sorted(set(conditional_hold_blockers))
        if all(issue["code"] in mismatch_codes for issue in issues):
            conditional = _existing_holds_candidate(line, notes, duplicate_event_ids)
            if (conditional is None and relationship == "one_character_many_notes"
                    and not any(note["lyric"] == "-" for note in notes)
                    and all(note["end_seconds"] is not None
                            and note["end_seconds"] <= line["end"] for note in notes)):
                conditional = _hold_candidate(line, notes)
    elif relationship == "more_characters_than_notes" and note_count:
        conditional = _split_capacity_candidate(line, notes)

    covered_char_count = character_count if safe_to_map else 0
    operation_counts = {
        "keep": sum(row["operation"] == "keep" for row in proposals),
        "replace": sum(row["operation"] == "replace" for row in proposals),
        "insert": sum(row["operation"] == "insert" for row in proposals),
    }
    line_result = {
        "line_index": line["line_index"],
        "start": line["start"],
        "end": line["end"],
        "text": line["text"],
        "characters": list(line["tokens"]),
        "char_count": character_count,
        "raw_char_count": line["raw_char_count"],
        "note_count": note_count,
        "note_ids": list(note_ids),
        "relationship": relationship,
        "status": (
            "ready" if safe_to_map
            else "empty" if not character_count and not note_count
            else "manual_required"
        ),
        "safe_to_map": safe_to_map,
        "before_lyrics": before_sequence,
        "after_lyrics": after_sequence,
        "before_text": before_text,
        "after_text": "".join(after_sequence) if after_sequence is not None else None,
        "proposals": proposals,
        "diff": {
            "before_lyrics": before_sequence,
            "after_lyrics": after_sequence,
            "changed_note_count": operation_counts["replace"] + operation_counts["insert"],
            "replace_count": operation_counts["replace"],
            "insert_count": operation_counts["insert"],
            "keep_count": operation_counts["keep"],
        },
        "coverage": {
            "covered_char_count": covered_char_count,
            "uncovered_char_count": character_count - covered_char_count,
            "unresolved_note_count": 0 if safe_to_map else note_count,
        },
        "manual_decision_codes": [item["code"] for item in issues],
        "conditional_suggestion": conditional,
        "conditional_hold_blockers": conditional_hold_blockers,
    }
    return line_result, edits, issues


def reconcile_lyrics(
    read: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    track_index: int,
) -> dict[str, Any]:
    """Build a complete, read-only lyric line reconciliation preview.

    Parameters match the MCP read surface: ``read`` is the original dictionary
    from ``lyrics_read``, ``lines`` contains ``start``/``end``/``text`` rows, and
    ``track_index`` selects exactly one note track.  A note belongs to a line
    only when ``line.start <= onset_seconds < line.end``.  No fallback or
    cross-track matching is performed.

    The return value is always preview data.  ``edits`` contains only safe
    note-level ``replace``/``insert`` candidates and is never applied here.
    Split and conditional hold proposals live outside ``edits`` and require
    explicit human confirmation. Existing-hold conditional edits are available
    in each line's ``conditional_suggestion.conditional_edits``.
    """

    if not isinstance(read, Mapping):
        raise ValueError("read 必须是 lyrics_read 返回的对象。")
    if isinstance(track_index, bool) or not isinstance(track_index, int) or track_index < 0:
        raise ValueError("track_index 必须是非负整数。")
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
        raise ValueError("lines 必须是对象数组。")
    if not lines:
        raise ValueError("lines 不能为空。")

    normalised_lines = [
        _normalise_line(line, index) for index, line in enumerate(lines)
    ]
    notes = _normalise_notes(read, track_index)
    event_counts: dict[str, int] = {}
    for note in notes:
        event_id = note["lyric_event_id"]
        if isinstance(event_id, str) and event_id:
            event_counts[event_id] = event_counts.get(event_id, 0) + 1
    duplicate_event_ids = {event_id for event_id, count in event_counts.items() if count > 1}
    overlaps = _overlapping_lines(normalised_lines)

    line_results: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    manual_decisions: list[dict[str, Any]] = []
    matched_note_ids: set[str] = set()
    unresolved: dict[str, dict[str, Any]] = {}

    for line in normalised_lines:
        line_notes = [
            note for note in notes
            if line["start"] <= note["onset_seconds"] < line["end"]
        ]
        matched_note_ids.update(note["note_id"] for note in line_notes)
        result, line_edits, issues = _evaluate_line(
            line, line_notes, overlaps.get(line["line_index"], []), duplicate_event_ids,
        )
        line_results.append(result)
        edits.extend(line_edits)
        manual_decisions.extend(issues)
        if not result["safe_to_map"]:
            for note in line_notes:
                row = unresolved.setdefault(note["note_id"], {
                    "note": _manual_note_view(note),
                    "line_indexes": [],
                })
                row["line_indexes"].append(line["line_index"])

    outside_notes = [
        note for note in notes if note["note_id"] not in matched_note_ids
    ]
    uncovered_characters = []
    uncovered_lines = []
    for result in line_results:
        if result["coverage"]["uncovered_char_count"]:
            rows = [
                {
                    "line_index": result["line_index"],
                    "character_index": index,
                    "character": character,
                }
                for index, character in enumerate(result["characters"])
            ]
            uncovered_characters.extend(rows)
            uncovered_lines.append({
                "line_index": result["line_index"],
                "characters": list(result["characters"]),
                "char_count": len(result["characters"]),
            })

    operation_rows = []
    for line_result in line_results:
        for proposal in line_result["proposals"]:
            if proposal["operation"] == "keep":
                continue
            operation_rows.append({
                "line_index": line_result["line_index"],
                "note_id": proposal["note_id"],
                "onset_seconds": proposal["onset_seconds"],
                "operation": proposal["operation"],
                "before": proposal["before_lyric"],
                "after": proposal["proposed_lyric"],
            })

    total_char_count = sum(row["char_count"] for row in line_results)
    total_window_note_count = sum(row["note_count"] for row in line_results)
    unresolved_note_count = len(unresolved)
    has_manual = bool(manual_decisions)
    warnings = [
        "本结果是纯预览：dry_run=true、applied=false，不读取文件、不写入 MIDI、不调用模型。",
        "edits 只含时间窗内唯一且可安全 replace/insert 的逐音符建议；任何人工决策都会令 ready_for_patch_plan=false。",
        "时间窗严格采用 start <= onset_seconds < end；行外音符保持未覆盖，开头缺词不会因本预览而被视为已修复。",
        "一字多音只有条件性延音候选；字多音符少只有等时长容量建议，二者均不进入 edits。",
        "保留现有 '-' 的条件候选仍可能掩盖漏词；必须先人工确认，不计入安全覆盖或 edits。",
    ]

    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": _PREVIEW_KIND,
        "format": "midi",
        "source_sha256": read.get("source_sha256"),
        "language": read.get("language"),
        "track_index": track_index,
        "track_name": _track_name(read, track_index),
        "dry_run": True,
        "preview_only": True,
        "applied": False,
        "apply_supported": False,
        "status": "manual_required" if has_manual else "ready",
        "ready_for_patch_plan": not has_manual and bool(edits),
        "no_changes_required": not has_manual and not edits,
        "edits": edits,
        "lines": line_results,
        "diff": {
            "schema": _DIFF_SCHEMA,
            "lines": [
                {
                    "line_index": row["line_index"],
                    "before_text": row["before_text"],
                    "after_text": row["after_text"],
                    **row["diff"],
                }
                for row in line_results
            ],
            "operations": operation_rows,
        },
        "coverage": {
            "line_count": len(line_results),
            "track_note_count": len(notes),
            "line_window_note_count": total_window_note_count,
            "unique_matched_note_count": len(matched_note_ids),
            "outside_line_note_count": len(outside_notes),
            "unresolved_line_note_count": unresolved_note_count,
            "total_char_count": total_char_count,
            "covered_char_count": sum(
                row["coverage"]["covered_char_count"] for row in line_results
            ),
            "uncovered_char_count": len(uncovered_characters),
            "proposed_edit_count": len(edits),
            "proposed_replace_count": sum(
                row["operation"] == "replace" for row in edits
            ),
            "proposed_insert_count": sum(
                row["operation"] == "insert" for row in edits
            ),
            "manual_decision_count": len(manual_decisions),
        },
        "uncovered": {
            "characters": uncovered_characters,
            "lines": uncovered_lines,
            "outside_line_notes": [_manual_note_view(note) for note in outside_notes],
            "unresolved_line_notes": list(unresolved.values()),
        },
        "manual_decisions": manual_decisions,
        "warnings": warnings,
    }
