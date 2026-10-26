"""Pure MIDI lyric reading, review, patch planning and safe publication.

The worker is intentionally independent from MCP, Qt and model inference. It
uses the score worker's current MIDI event mapper so lyric associations and
note IDs agree with the supervised score review. That dependency is versioned
below; changing the score mapping requires re-reading lyric plans rather than
silently carrying old IDs forward.  The fixed dependency is agent_score schema
1 with its current MIDI parser, note-ID mapping, tempo timeline and exclusive
publication helpers; this adapter must be revalidated if that private surface
changes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any

import mido

import agent_score as _score
from common import native_path


_SCHEMA_VERSION = 1
_READ_KIND = "vwa-lyrics-read"
_REVIEW_KIND = "vwa-lyrics-review"
_PLAN_KIND = "vwa-lyrics-patch-plan"
_RECEIPT_KIND = "vwa-lyrics-patch-receipt"
_DIFF_SCHEMA = "vwa-lyrics-diff-v1"
_SCORE_MAPPING = "agent_score-midi-v1"
_MAX_TICK = 0x0FFFFFFF
_EDIT_KEYS = {"note_id", "lyric", "operation"}
_PLAN_KEYS = {
    "schema_version", "kind", "source_path", "source_sha256", "format", "midi_type",
    "ticks_per_beat", "edits", "diff", "expected_candidate_sha256",
    "validation", "warnings", "plan_sha256",
}
_PLACEHOLDER_RE = re.compile(
    r"^(?:<[^>]*>|\[[^\]]*\]|\{[^}]*\}|tbd|todo|fixme|placeholder|lyrics?|"
    r"歌词待填|歌詞待填|待填|\?{2,}|x{3,}|_{2,}|＿{2,})$",
    re.IGNORECASE,
)
_CONTINUATION_CHARS = frozenset(
    "~～〜-‐‑‒–—―…⋯·・,，.。!！?？:：;；、'’\"“”「」『』（）()[]［］【】 \t\r\n\u3000"
)


def _io_path(path) -> Path:
    return Path(native_path(path))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(payload)


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False, "allow_nan": False, "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    text = json.dumps(value, **options)
    if pretty:
        text += "\n"
    return text.encode("utf-8")


def _normalise_sha(value: Any, field_name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdefABCDEF" for ch in value)):
        raise ValueError(f"{field_name} 必须是 64 位十六进制 SHA-256。")
    return value.lower()


def _natural_path_text(value: Any, field_name: str) -> str:
    try:
        supplied = Path(value)
        compact_text = os.path.abspath(os.fspath(supplied.expanduser()))
        resolved = supplied.expanduser().resolve()
    except (TypeError, OSError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是有效路径。") from exc

    def strip_extended(text: str) -> str:
        if text.startswith("\\\\?\\UNC\\"):
            return "\\\\" + text[8:]
        if text.startswith("\\\\?\\"):
            return text[4:]
        return text

    resolved_text = strip_extended(str(resolved))
    compact_text = strip_extended(compact_text)
    if os.name == "nt" and os.path.normcase(resolved_text) != os.path.normcase(compact_text):
        raise ValueError(f"{field_name} 必须使用普通绝对路径，不能通过别名解析到另一位置。")
    return resolved_text


def _validate_language(language: Any) -> str:
    if not isinstance(language, str) or language not in {"zh", "ja"}:
        raise ValueError("language 必须是 'zh' 或 'ja'。")
    return language


def _load_midi_source(path: Any):
    try:
        supplied = Path(path)
    except (TypeError, ValueError) as exc:
        raise ValueError("MIDI 路径无效。") from exc
    if supplied.suffix.lower() not in {".mid", ".midi"}:
        raise ValueError("Lyrics worker 只支持 .mid 或 .midi 文件。")
    source, raw, source_sha = _score._read_input(supplied)
    if source.suffix.lower() not in {".mid", ".midi"}:
        raise ValueError("Lyrics worker 只支持 .mid 或 .midi 文件。")
    parsed = _score._parse_midi(raw, str(source))
    if parsed.source_sha256 != source_sha:
        raise ValueError("MIDI 在读取期间发生变化，请重试。")
    return source, raw, source_sha, parsed


def _verify_source_unchanged(source: Path, expected_sha: str) -> None:
    _, _, current_sha = _score._read_input(source)
    if current_sha != expected_sha:
        raise ValueError("MIDI 源文件 SHA-256 已变化；请重新读取歌词。")


def _event_fingerprint(message: Any) -> str:
    payload = dict(message.dict())
    payload.pop("time", None)
    try:
        return _canonical_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MIDI 事件无法稳定序列化：{exc}") from exc


def _meta_payload(message: Any, *, tick: int, event_index: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_index": event_index, "tick": tick, "type": message.type,
    }
    for key, value in message.dict().items():
        if key != "time" and isinstance(value, (str, int, bool)):
            row[key] = value
    return row


def _alignment_by_track(parsed: Any) -> dict[int, dict[str, Any]]:
    """Classify lyric events using the same interval rule as agent_score."""
    result: dict[int, dict[str, Any]] = {}
    for track in parsed.tracks:
        note_status: dict[str, dict[str, Any]] = {}
        event_status: dict[int, dict[str, Any]] = {}
        by_onset: dict[int, list[Any]] = defaultdict(list)
        for note in track.notes:
            by_onset[note.onset_tick].append(note)
        onsets = sorted(by_onset)
        if not onsets:
            for event in track.lyrics:
                event_status[event["event_index"]] = {
                    "status": "orphan", "note_ids": [],
                }
        else:
            first_onset = onsets[0]
            for event in track.lyrics:
                if event["tick"] < first_onset:
                    event_status[event["event_index"]] = {
                        "status": "orphan", "note_ids": [],
                    }
            for index, onset in enumerate(onsets):
                following = onsets[index + 1] if index + 1 < len(onsets) else track.end_tick + 1
                events = [row for row in track.lyrics if onset <= row["tick"] < following]
                notes = by_onset[onset]
                if len(notes) == 1 and len(events) == 1:
                    note, event = notes[0], events[0]
                    note_status[note.note_id] = {
                        "status": "aligned",
                        "event_indexes": [event["event_index"]],
                    }
                    event_status[event["event_index"]] = {
                        "status": "aligned", "note_ids": [note.note_id],
                    }
                    continue
                note_ids = [note.note_id for note in notes]
                event_indexes = [event["event_index"] for event in events]
                if len(notes) > 1 or len(events) > 1:
                    for note in notes:
                        note_status[note.note_id] = {
                            "status": "ambiguous",
                            "event_indexes": event_indexes,
                        }
                    for event in events:
                        event_status[event["event_index"]] = {
                            "status": "ambiguous", "note_ids": note_ids,
                        }
                elif notes:
                    note_status[notes[0].note_id] = {
                        "status": "missing", "event_indexes": [],
                    }

        for event in track.lyrics:
            event_status.setdefault(event["event_index"], {"status": "orphan", "note_ids": []})
        for note in track.notes:
            note_status.setdefault(note.note_id, {"status": "ambiguous", "event_indexes": []})
        result[track.index] = {"notes": note_status, "events": event_status}
    return result


def _alignment_counts(statuses: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"aligned": 0, "missing": 0, "ambiguous": 0, "orphan": 0}
    for item in statuses:
        status = item.get("status", item.get("alignment", item.get("lyric_alignment")))
        if status not in counts:
            raise ValueError("歌词对齐状态无效。")
        counts[status] += 1
    return counts


def read_lyrics(path, language="zh") -> dict:
    """Read MIDI lyric evidence without changing the source file."""
    language = _validate_language(language)
    source, _raw, source_sha, parsed = _load_midi_source(path)
    score = _score._midi_score(parsed)
    alignment = _alignment_by_track(parsed)
    note_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    for row in score["notes"]:
        value = dict(row)
        evidence = alignment[row["track_index"]]["notes"][row["note_id"]]
        value["lyric_alignment"] = evidence["status"]
        value["competing_lyric_event_indexes"] = list(evidence["event_indexes"])
        note_rows.append(value)

    track_rows: list[dict[str, Any]] = []
    meta_tracks: list[dict[str, Any]] = []
    for track, score_track in zip(parsed.tracks, score["tracks"]):
        evidence = alignment[track.index]
        track_events = []
        for lyric in track.lyrics:
            status = evidence["events"][lyric["event_index"]]
            row = {
                "track_index": track.index, "track_name": track.name,
                "event_index": lyric["event_index"], "tick": lyric["tick"],
                "seconds": lyric["seconds"], "text": lyric["text"],
                "alignment": status["status"], "note_ids": list(status["note_ids"]),
            }
            track_events.append(row)
            event_rows.append(row)

        note_counts = _alignment_counts(list(evidence["notes"].values()))
        event_counts = _alignment_counts(list(evidence["events"].values()))
        enriched_track = dict(score_track)
        enriched_track["lyric_events"] = track_events
        enriched_track["alignment"] = {"notes": note_counts, "events": event_counts}
        track_rows.append(enriched_track)

        meta_rows = []
        for event in track.events:
            message = event.message
            if message.is_meta and message.type not in {"lyrics", "end_of_track"}:
                meta_rows.append(_meta_payload(
                    message, tick=event.tick, event_index=event.event_index,
                ))
        meta_tracks.append({
            "track_index": track.index, "track_name": track.name,
            "instrument_name": track.instrument_name, "meta_events": meta_rows,
        })

    unknown_notes = [dict(row) for row in note_rows if row["lyric_alignment"] == "missing"]
    ambiguous_notes = [dict(row) for row in note_rows if row["lyric_alignment"] == "ambiguous"]
    orphan_events = [dict(row) for row in event_rows if row["alignment"] == "orphan"]
    ambiguous_events = [dict(row) for row in event_rows if row["alignment"] == "ambiguous"]
    note_counts = _alignment_counts(note_rows)
    event_counts = _alignment_counts(event_rows)
    note_count = len(note_rows)
    coverage = {
        "note_count": note_count,
        "aligned_note_count": note_counts["aligned"],
        "missing_note_count": note_counts["missing"],
        "ambiguous_note_count": note_counts["ambiguous"],
        "lyric_event_count": len(event_rows),
        "aligned_event_count": event_counts["aligned"],
        "orphan_event_count": event_counts["orphan"],
        "ambiguous_event_count": event_counts["ambiguous"],
        "ratio_aligned_notes": round(note_counts["aligned"] / note_count, 9) if note_count else 1.0,
    }

    result = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _READ_KIND,
        "format": "midi",
        "path": str(source),
        "source_sha256": source_sha,
        "language": language,
        "midi_type": score["midi_type"],
        "ticks_per_beat": score["ticks_per_beat"],
        "timing": score["timing"],
        "mapping_contract": {
            "worker": "agent_score",
            "mapping": _SCORE_MAPPING,
            "schema_version": score["schema_version"],
            "note_id_basis": "note-{source_sha[:16]}-t{track_index}-n{ordinal}",
            "association": "one lyric meta event and one note onset in the same track interval",
        },
        "notes": note_rows,
        "lyric_events": event_rows,
        "tracks": track_rows,
        "meta_evidence": meta_tracks,
        "coverage": coverage,
        "unknown_notes": unknown_notes,
        "ambiguous_notes": ambiguous_notes,
        "orphan_lyric_events": orphan_events,
        "ambiguous_lyric_events": ambiguous_events,
        "integrity_issues": [dict(item) for item in score["integrity_issues"]],
        "warnings": [
            "歌词关联由确定性 MIDI tick 区间规则给出；歧义与孤立事件不会被猜测归属。",
            "本读取结果不检查发音、读音或演唱语义。",
        ],
    }
    _verify_source_unchanged(source, source_sha)
    return result


def _is_continuation_or_punctuation(text: str) -> bool:
    stripped = unicodedata.normalize("NFC", text).strip()
    if not stripped:
        return False
    return all(ch in _CONTINUATION_CHARS for ch in stripped)


def _comparison_tokens(text: str) -> list[str]:
    tokens = []
    for char in unicodedata.normalize("NFC", text):
        category = unicodedata.category(char)
        if char.isspace() or category[0] in {"P", "Z"} or char in _CONTINUATION_CHARS:
            continue
        tokens.append(char)
    return tokens


def _hint(
    code: str, severity: str, message: str, *, note_ids: list[str] | None = None,
    track_index: int | None = None, tick: int | None = None,
    seconds: float | None = None, lyric: str | None = None,
    expected: str | None = None, actual: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": code, "severity": severity, "message": message,
        "note_ids": list(note_ids or []),
    }
    if track_index is not None:
        row["track_index"] = track_index
    if tick is not None:
        row["tick"] = tick
    if seconds is not None:
        row["onset_seconds"] = round(float(seconds), 9)
    if lyric is not None:
        row["lyric"] = lyric
    if expected is not None:
        row["expected"] = expected
    if actual is not None:
        row["actual"] = actual
    return row


def _reference_mismatch_hints(read: dict[str, Any], reference_text: str) -> list[dict[str, Any]]:
    reference_tokens = _comparison_tokens(reference_text)
    units: list[dict[str, Any]] = []
    for note in sorted(
        (row for row in read["notes"] if row.get("lyric_alignment") == "aligned"),
        key=lambda row: (row["track_index"], row["onset_tick"], row["note_id"]),
    ):
        lyric = note.get("lyric")
        if not isinstance(lyric, str):
            continue
        for token in _comparison_tokens(lyric):
            units.append({
                "token": token, "note_id": note["note_id"],
                "track_index": note["track_index"], "tick": note["onset_tick"],
                "seconds": note["onset_seconds"],
            })

    hints: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(
        a=reference_tokens, b=[unit["token"] for unit in units], autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        selected_units = units[j1:j2]
        note_ids = list(dict.fromkeys(unit["note_id"] for unit in selected_units))
        if selected_units:
            anchor = selected_units[0]
            track_index, tick, seconds = anchor["track_index"], anchor["tick"], anchor["seconds"]
        else:
            previous = units[j1 - 1] if j1 > 0 else None
            following = units[j1] if j1 < len(units) else None
            anchor = previous or following
            if previous and following and previous["track_index"] != following["track_index"]:
                anchor = None
            track_index = anchor["track_index"] if anchor else None
            tick = anchor["tick"] if anchor else 0
            seconds = anchor["seconds"] if anchor else 0.0
        expected = "".join(reference_tokens[i1:i2])
        actual = "".join(unit["token"] for unit in selected_units)
        if tag == "replace":
            code = "reference_mismatch"
            message = "MIDI 歌词与调用方显式提供的参考文本不同；仅供复核，不推断读音。"
            severity = "warning"
        elif tag == "delete":
            code = "reference_missing"
            message = "参考文本中的字符未在可关联 MIDI 歌词中出现。"
            severity = "warning"
        else:
            previous_token = reference_tokens[j1 - 1] if j1 > 0 else None
            if previous_token is not None and actual and all(token == previous_token for token in actual):
                code = "ambiguous_note_span"
                message = "同一参考字符可能跨多个音符延续；此处按歧义处理，不判为错误。"
                severity = "info"
            else:
                code = "reference_extra"
                message = "MIDI 歌词含有参考文本之外的字符；可能是延音、重复或参考文本不完整。"
                severity = "warning"
        hints.append(_hint(
            code, severity, message, note_ids=note_ids, track_index=track_index,
            tick=tick, seconds=seconds, expected=expected or None, actual=actual or None,
        ))
    return hints


def review_lyrics(path, reference_text=None, language="zh") -> dict:
    """Return deterministic lyric hints; hints are evidence, not error claims."""
    read = read_lyrics(path, language=language)
    if reference_text is not None and not isinstance(reference_text, str):
        raise ValueError("reference_text 必须是字符串或 None。")
    hints: list[dict[str, Any]] = []

    for note in read["notes"]:
        lyric, status = note.get("lyric"), note["lyric_alignment"]
        if status == "missing":
            hints.append(_hint(
                "missing_lyric", "info",
                "该音符在当前窗口中没有任何唯一可关联的歌词事件。",
                note_ids=[note["note_id"]], track_index=note["track_index"],
                tick=note["onset_tick"], seconds=note["onset_seconds"],
            ))
            continue
        if status == "ambiguous":
            hints.append(_hint(
                "ambiguous_lyric_alignment", "info",
                "该音符与歌词事件存在同 onset 多音符或多事件竞争，不能自动归属。",
                note_ids=[note["note_id"]], track_index=note["track_index"],
                tick=note["onset_tick"], seconds=note["onset_seconds"],
            ))
            continue
        if not isinstance(lyric, str):
            continue
        if not lyric.strip():
            hints.append(_hint(
                "empty_lyric", "warning", "已关联歌词事件没有可见文本。",
                note_ids=[note["note_id"]], track_index=note["track_index"],
                tick=note["onset_tick"], seconds=note["onset_seconds"], lyric=lyric,
            ))
            continue
        if _PLACEHOLDER_RE.fullmatch(lyric.strip()):
            hints.append(_hint(
                "placeholder_lyric", "warning",
                "歌词文本形似占位符，请由调用方结合上下文确认。",
                note_ids=[note["note_id"]], track_index=note["track_index"],
                tick=note["onset_tick"], seconds=note["onset_seconds"], lyric=lyric,
            ))
        if _is_continuation_or_punctuation(lyric):
            hints.append(_hint(
                "continuation_mark_ambiguous", "info",
                "该事件仅含标点或延音标记，可能表示跨音符延续；不会自动判错。",
                note_ids=[note["note_id"]], track_index=note["track_index"],
                tick=note["onset_tick"], seconds=note["onset_seconds"], lyric=lyric,
            ))

    previous = None
    ordered_notes = sorted(
        read["notes"], key=lambda row: (row["track_index"], row["onset_tick"], row["note_id"]),
    )
    for note in ordered_notes:
        lyric = note.get("lyric")
        if not isinstance(lyric, str) or not lyric.strip() or _is_continuation_or_punctuation(lyric):
            previous = None
            continue
        normalized = unicodedata.normalize("NFC", lyric)
        if previous is not None and previous["normalized"] == normalized:
            hints.append(_hint(
                "duplicate_adjacent_lyric", "info",
                "相邻音符含相同歌词；可能是正常重复，也可能是同一唱字跨音符。",
                note_ids=[previous["note_id"], note["note_id"]],
                track_index=note["track_index"], tick=note["onset_tick"],
                seconds=note["onset_seconds"], lyric=lyric,
            ))
        previous = {"note_id": note["note_id"], "normalized": normalized}

    for event in read["orphan_lyric_events"]:
        hints.append(_hint(
            "orphan_lyric_event", "warning", "歌词事件没有可唯一关联的音符 onset。",
            note_ids=[], track_index=event["track_index"], tick=event["tick"],
            seconds=event["seconds"], lyric=event["text"],
        ))
    for event in read["ambiguous_lyric_events"]:
        hints.append(_hint(
            "ambiguous_lyric_event", "info",
            "歌词事件与同 onset 的多个音符竞争，未被自动归属。",
            note_ids=event["note_ids"], track_index=event["track_index"],
            tick=event["tick"], seconds=event["seconds"], lyric=event["text"],
        ))

    for track in read["tracks"]:
        missing_count = track["alignment"]["notes"]["missing"] + track["alignment"]["notes"]["ambiguous"]
        if missing_count:
            candidates = [
                note for note in read["notes"]
                if note["track_index"] == track["track_index"]
                and note["lyric_alignment"] in {"missing", "ambiguous"}
            ]
            first = sorted(candidates, key=lambda row: (row["onset_tick"], row["note_id"]))[0]
            hints.append(_hint(
                "incomplete_lyric_coverage", "info",
                f"轨道 {track['track_index']} 有 {missing_count} 个音符缺少唯一歌词关联。",
                note_ids=[note["note_id"] for note in candidates],
                track_index=track["track_index"], tick=first["onset_tick"],
                seconds=first["onset_seconds"],
            ))

    reference_evidence = {
        "provided": reference_text is not None,
        "source": "explicit_caller_input",
        "comparison": "Unicode punctuation/whitespace excluded; no provider or phonetic inference",
        "token_count": len(_comparison_tokens(reference_text)) if reference_text is not None else 0,
    }
    if reference_text is not None:
        if reference_evidence["token_count"] == 0:
            hints.append(_hint(
                "reference_has_no_lyric_tokens", "warning",
                "调用方提供了参考文本，但去除标点和空白后没有可比较字符。",
                tick=0, seconds=0.0,
            ))
        else:
            hints.extend(_reference_mismatch_hints(read, reference_text))

    hints.sort(key=lambda row: (
        row.get("track_index", -1), row.get("tick", -1), row["code"],
        tuple(row.get("note_ids", [])), row.get("message", ""),
    ))
    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": _REVIEW_KIND,
        "format": "midi",
        "source_sha256": read["source_sha256"],
        "language": read["language"],
        "note_count": len(read["notes"]),
        "lyric_event_count": len(read["lyric_events"]),
        "coverage": read["coverage"],
        "reference": reference_evidence,
        "hint_count": len(hints),
        "hints": hints,
        "disclaimer": (
            "以下仅为确定性的 MIDI 歌词对齐与文本复核提示，不代表发音、语义或演唱已经证实错误；"
            "标点、延音和一字跨多音符情形按歧义展示。"
        ),
    }


def _normalise_edits(parsed: Any, alignment: dict[int, dict[str, Any]], edits: Any) -> list[dict[str, str]]:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空数组。")
    notes = _score._notes_by_id(parsed)
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise ValueError(f"edits[{index}] 必须是对象。")
        keys = set(edit)
        if keys != _EDIT_KEYS:
            missing = sorted(_EDIT_KEYS - keys)
            extra = sorted(keys - _EDIT_KEYS)
            raise ValueError(
                f"edits[{index}] 字段必须恰为 note_id/lyric/operation；缺少={missing}，额外={extra}。"
            )
        note_id = edit.get("note_id")
        lyric = edit.get("lyric")
        operation = edit.get("operation")
        if not isinstance(note_id, str) or not note_id:
            raise ValueError(f"edits[{index}].note_id 必须是非空字符串。")
        if note_id in seen:
            raise ValueError(f"重复的 note_id：{note_id}。")
        seen.add(note_id)
        if not isinstance(lyric, str):
            raise ValueError(f"edits[{index}].lyric 必须是字符串。")
        if "\x00" in lyric:
            raise ValueError(f"edits[{index}].lyric 不能包含 NUL。")
        if operation not in {"replace", "insert"}:
            raise ValueError(f"edits[{index}].operation 必须是 replace 或 insert。")
        note = notes.get(note_id)
        if note is None:
            raise ValueError(f"未知或过期 note_id：{note_id}。")
        evidence = alignment[note.track_index]["notes"].get(note_id)
        if evidence is None:
            raise ValueError(f"音符 {note_id} 缺少歌词对齐证据。")
        if operation == "replace":
            if evidence["status"] != "aligned" or note.lyric_event_index is None:
                raise ValueError(f"音符 {note_id} 没有唯一可替换的歌词事件。")
            track = parsed.tracks[note.track_index]
            message = track.events[note.lyric_event_index].message
            if message.type != "lyrics":
                raise ValueError(f"音符 {note_id} 关联事件不是 lyrics。")
            if note.lyric == lyric:
                raise ValueError(f"音符 {note_id} 的歌词没有实际变化。")
        else:
            if evidence["status"] != "missing" or note.lyric is not None:
                raise ValueError(
                    f"音符 {note_id} 已有歌词或 onset 映射存在竞争，不能 insert；"
                    "已有歌词请使用 replace。"
                )
            same_onset = [
                candidate for candidate in notes.values()
                if candidate.track_index == note.track_index
                and candidate.onset_tick == note.onset_tick
            ]
            if len(same_onset) != 1:
                raise ValueError(f"音符 {note_id} 的 onset 有多个音符，不能安全 insert。")
        rows.append({
            "note_id": note_id,
            "lyric": lyric,
            "operation": str(operation),
        })
    rows.sort(key=lambda row: row["note_id"])
    return rows


def _build_lyrics_patch(parsed: Any, edits: Any) -> tuple[dict[str, Any], bytes]:
    if parsed.integrity_issues:
        first = parsed.integrity_issues[0]
        raise ValueError(f"MIDI 结构不安全，不能规划歌词补丁：{first['message']}")
    notes = _score._notes_by_id(parsed)
    if any(note.pairing_ambiguous for note in notes.values()):
        raise ValueError("MIDI 存在同轨、同通道、同音高的重叠音符，事件配对有歧义。")
    for note in notes.values():
        if note.end_tick is None or note.off_event_index is None:
            raise ValueError(f"音符 {note.note_id} 没有唯一关闭事件，无法安全应用歌词补丁。")

    alignment = _alignment_by_track(parsed)
    normalized = _normalise_edits(parsed, alignment, edits)
    event_updates: dict[tuple[int, int], tuple[int, Any]] = {}
    insertions: dict[int, list[tuple[int, int, Any]]] = defaultdict(list)

    for edit in normalized:
        note = notes[edit["note_id"]]
        track = parsed.tracks[note.track_index]
        if edit["operation"] == "replace":
            event_index = note.lyric_event_index
            event = track.events[event_index]
            message = event.message.copy(text=edit["lyric"])
            event_updates[(track.index, event_index)] = (event.tick, message)
        else:
            insertion_rank = (note.on_event_index * 2) - 1
            message = mido.MetaMessage("lyrics", text=edit["lyric"], time=0)
            insertions[track.index].append((note.onset_tick, insertion_rank, message))

    new_midi = mido.MidiFile(
        type=parsed.midi.type,
        ticks_per_beat=parsed.midi.ticks_per_beat,
        charset="utf8",
    )
    expected_events: list[list[tuple[int, str]]] = []
    for track in parsed.tracks:
        items: list[tuple[int, int, Any]] = []
        for event in track.events:
            tick, message = event_updates.get(
                (track.index, event.event_index),
                (event.tick, event.message),
            )
            items.append((tick, event.event_index * 2, message))
        for tick, rank, message in insertions.get(track.index, []):
            items.append((tick, rank, message))
        items.sort(key=lambda row: (row[0], row[1]))

        last_tick = 0
        new_track = mido.MidiTrack()
        expected: list[tuple[int, str]] = []
        for tick, _rank, message in items:
            delta = tick - last_tick
            if delta < 0 or delta > _MAX_TICK:
                raise ValueError(f"轨道 {track.index} 的歌词补丁产生无效 delta tick。")
            saved = message.copy(time=delta)
            new_track.append(saved)
            expected.append((tick, _event_fingerprint(saved)))
            last_tick = tick
        new_midi.tracks.append(new_track)
        expected_events.append(expected)

    buffer = io.BytesIO()
    try:
        new_midi.save(file=buffer)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"生成歌词候选 MIDI 失败：{exc}") from exc
    candidate_bytes = buffer.getvalue()
    candidate_parsed = _score._parse_midi(candidate_bytes, "<lyrics-candidate>")
    if candidate_parsed.integrity_issues:
        raise ValueError("歌词候选未通过 MIDI 结构验证，已拒绝生成计划。")
    if any(note.pairing_ambiguous for note in _score._notes_by_id(candidate_parsed).values()):
        raise ValueError("歌词候选产生音符配对歧义，已拒绝生成计划。")
    if (
        candidate_parsed.midi.type != parsed.midi.type
        or candidate_parsed.midi.ticks_per_beat != parsed.midi.ticks_per_beat
        or len(candidate_parsed.tracks) != len(parsed.tracks)
    ):
        raise ValueError("歌词候选改变 MIDI 顶层结构，已拒绝生成计划。")

    for track_index, candidate_track in enumerate(candidate_parsed.tracks):
        actual = [
            (event.tick, _event_fingerprint(event.message))
            for event in candidate_track.events
        ]
        if actual != expected_events[track_index]:
            raise ValueError(f"候选 MIDI 轨道 {track_index} 未无损保留全部事件。")

    def note_semantics(value: Any) -> list[tuple[Any, ...]]:
        return [
            (
                note.track_index, note.ordinal, note.channel, note.pitch,
                note.velocity, note.onset_tick, note.end_tick,
            )
            for track in value.tracks
            for note in track.notes
        ]

    if note_semantics(candidate_parsed) != note_semantics(parsed):
        raise ValueError("歌词候选改变音符 timing、pitch、channel 或 velocity，已拒绝生成计划。")

    candidate_notes = {
        (note.track_index, note.ordinal): note
        for track in candidate_parsed.tracks
        for note in track.notes
    }
    desired_lyrics = {note_id: note.lyric for note_id, note in notes.items()}
    for edit in normalized:
        desired_lyrics[edit["note_id"]] = edit["lyric"]
    for note in notes.values():
        candidate_note = candidate_notes[(note.track_index, note.ordinal)]
        if candidate_note.lyric != desired_lyrics[note.note_id]:
            raise ValueError(f"候选 MIDI 中音符 {note.note_id} 的确定性歌词关联不符合计划。")

    source_lyric_count = sum(len(track.lyrics) for track in parsed.tracks)
    candidate_lyric_count = sum(len(track.lyrics) for track in candidate_parsed.tracks)
    insert_count = sum(1 for edit in normalized if edit["operation"] == "insert")
    replace_count = sum(1 for edit in normalized if edit["operation"] == "replace")
    if candidate_lyric_count != source_lyric_count + insert_count:
        raise ValueError("候选 MIDI 歌词事件数量与计划不一致。")

    positions, points = _score._tempo_timeline(
        parsed.tempo_events,
        parsed.midi.ticks_per_beat,
    )
    operations: list[dict[str, Any]] = []
    for edit in normalized:
        note = notes[edit["note_id"]]
        candidate_note = candidate_notes[(note.track_index, note.ordinal)]
        note_track = parsed.tracks[note.track_index]
        candidate_track = candidate_parsed.tracks[note.track_index]
        before_event = None
        if note.lyric_event_index is not None:
            source_event = note_track.events[note.lyric_event_index]
            before_event = {
                "event_index": note.lyric_event_index,
                "tick": source_event.tick,
                "text": source_event.message.text,
            }
        after_event = None
        if candidate_note.lyric_event_index is not None:
            event = candidate_track.events[candidate_note.lyric_event_index]
            after_event = {
                "event_index": candidate_note.lyric_event_index,
                "tick": event.tick,
                "text": event.message.text,
            }
        operations.append({
            "op": edit["operation"],
            "operation": edit["operation"],
            "path": f"/tracks/{note.track_index}/notes/{note.note_id}/lyric",
            "note_id": note.note_id,
            "track_index": note.track_index,
            "tick": note.onset_tick,
            "onset_seconds": round(_score._seconds_at(
                note.onset_tick,
                positions,
                points,
                parsed.midi.ticks_per_beat,
            ), 9),
            "before": before_event,
            "after": after_event,
        })

    validation = {
        "source_sha256": parsed.source_sha256,
        "note_count": len(notes),
        "note_semantics_preserved": True,
        "midi_top_level_preserved": True,
        "all_midi_events_verified": True,
        "lyric_event_count_before": source_lyric_count,
        "lyric_event_count_after": candidate_lyric_count,
        "replace_count": replace_count,
        "insert_count": insert_count,
        "unchanged_lyric_event_count": source_lyric_count - replace_count,
    }
    warnings = [
        "补丁只修改列出的 lyrics meta 事件；音符 timing、pitch、channel、velocity、tempo、其他 meta 和控制事件均保持语义不变。",
        "源文件不会被覆盖；候选是独立文件并绑定完整计划哈希。",
        "候选源 SHA 会变化，因此 agent_score note ID 也会按当前修订规则变化；后续操作应重新读取候选。",
    ]
    plan_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _PLAN_KIND,
        "source_path": _natural_path_text(parsed.path_text, "source_path"),
        "source_sha256": parsed.source_sha256,
        "format": "midi",
        "midi_type": parsed.midi.type,
        "ticks_per_beat": parsed.midi.ticks_per_beat,
        "edits": normalized,
        "diff": {"schema": _DIFF_SCHEMA, "operations": operations},
        "expected_candidate_sha256": _sha256(candidate_bytes),
        "validation": validation,
        "warnings": warnings,
    }
    plan = dict(plan_payload)
    plan["plan_sha256"] = _canonical_digest(plan_payload)
    return plan, candidate_bytes


def plan_lyrics_patch(path, expected_sha256, edits) -> dict:
    """Plan strict lyrics meta-event replacements/insertions on a MIDI file."""
    source, _raw, source_sha, parsed = _load_midi_source(path)
    expected = _normalise_sha(expected_sha256, "expected_sha256")
    if source_sha != expected:
        raise ValueError("源文件 SHA-256 已变化；请重新 read_lyrics 后再规划补丁。")
    plan, _candidate_bytes = _build_lyrics_patch(parsed, edits)
    _verify_source_unchanged(source, source_sha)
    return plan


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ValueError("plan 必须是对象。")
    supplied = dict(plan)
    if set(supplied) != _PLAN_KEYS:
        missing = sorted(_PLAN_KEYS - set(supplied))
        extra = sorted(set(supplied) - _PLAN_KEYS)
        raise ValueError(f"plan 字段不完整或不支持；缺少={missing}，额外={extra}。")
    if (
        type(supplied.get("schema_version")) is not int
        or supplied.get("schema_version") != _SCHEMA_VERSION
        or supplied.get("kind") != _PLAN_KIND
        or supplied.get("format") != "midi"
    ):
        raise ValueError("plan schema、kind 或 format 无效。")
    plan_sha = _normalise_sha(supplied.get("plan_sha256"), "plan_sha256")
    payload = {key: value for key, value in supplied.items() if key != "plan_sha256"}
    try:
        actual = _canonical_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"plan 不是完整、有限的 JSON 对象：{exc}") from exc
    if actual != plan_sha:
        raise ValueError("plan_sha256 与完整计划内容不匹配；计划已损坏或被篡改。")
    _normalise_sha(supplied.get("source_sha256"), "source_sha256")
    _normalise_sha(supplied.get("expected_candidate_sha256"), "expected_candidate_sha256")
    if not isinstance(supplied.get("edits"), list):
        raise ValueError("plan edits 必须是数组。")
    return supplied


def _same_path(left: Path, right: Path) -> bool:
    try:
        return (
            os.path.normcase(os.path.abspath(str(left)))
            == os.path.normcase(os.path.abspath(str(right)))
        )
    except OSError:
        return False


def apply_lyrics_patch(path, plan, output_root) -> dict:
    """Apply a verified plan to an independent atomic MIDI candidate and receipt."""
    supplied = _validate_plan(plan)
    source, _raw, current_sha, parsed = _load_midi_source(path)
    if supplied.get("source_path") != _natural_path_text(source, "source_path"):
        raise ValueError("计划绑定的源文件路径与当前 path 不一致。")
    source_sha = _normalise_sha(supplied["source_sha256"], "source_sha256")
    if current_sha != source_sha:
        raise ValueError("当前源文件 SHA-256 与计划不一致；请重新 read_lyrics 和 plan_lyrics_patch。")
    regenerated, candidate_bytes = _build_lyrics_patch(parsed, supplied["edits"])
    if regenerated != supplied:
        raise ValueError("计划与当前源文件重新计算的结果不一致；请拒绝该计划。")
    candidate_sha = _sha256(candidate_bytes)
    if candidate_sha != supplied["expected_candidate_sha256"]:
        raise ValueError("重新生成的候选 SHA-256 与计划不一致。")

    if output_root is None or isinstance(output_root, bool):
        raise ValueError("output_root 必须是目录路径。")
    try:
        if isinstance(output_root, str) and not output_root:
            raise ValueError("output_root 不能为空。")
        root = Path(output_root)
        if _io_path(root).exists() and not _io_path(root).is_dir():
            raise ValueError("output_root 已存在但不是目录。")
        _io_path(root).mkdir(parents=True, exist_ok=True)
        output_root_path = root.resolve()
    except ValueError:
        raise
    except (TypeError, OSError) as exc:
        raise ValueError(f"output_root 无效：{exc}") from exc

    if not isinstance(supplied.get("source_path"), str) or not supplied["source_path"]:
        raise ValueError("plan source_path 必须是非空字符串。")
    candidate_path = (output_root_path / f"vwa-lyrics-{supplied['plan_sha256']}.mid").resolve()
    receipt_path = (
        output_root_path / f"vwa-lyrics-{supplied['plan_sha256']}.mid.receipt.json"
    ).resolve()
    if _same_path(candidate_path, source) or _same_path(receipt_path, source):
        raise ValueError("候选或 receipt 路径与源文件冲突；拒绝覆盖源文件。")

    receipt_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _RECEIPT_KIND,
        "plan_sha256": supplied["plan_sha256"],
        "source_sha256": source_sha,
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_sha,
        "receipt_path": str(receipt_path),
        "edits": supplied["edits"],
        "diff": supplied["diff"],
        "validation": supplied["validation"],
        "warnings": supplied["warnings"],
    }
    receipt = dict(receipt_payload)
    receipt["receipt_sha256"] = _canonical_digest(receipt_payload)
    receipt_bytes = _canonical_json_bytes(receipt, pretty=True)

    existing_candidate = _score._existing_file(candidate_path, "歌词候选文件")
    if existing_candidate is not None and existing_candidate != candidate_bytes:
        raise ValueError("歌词候选路径已有不同内容，拒绝覆盖或信任该碰撞。")
    existing_receipt = _score._existing_file(receipt_path, "歌词 receipt")
    if existing_receipt is not None:
        try:
            value = json.loads(existing_receipt.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"已有歌词 receipt 不是有效 UTF-8 JSON：{exc}") from exc
        if value != receipt:
            raise ValueError("已有歌词 receipt 与当前计划不一致；拒绝覆盖或信任该碰撞。")

    _verify_source_unchanged(source, source_sha)
    if existing_candidate is None:
        _score._publish_exclusive(candidate_path, candidate_bytes)
    if existing_receipt is None:
        _score._publish_exclusive(receipt_path, receipt_bytes)
    if _score._existing_file(candidate_path, "歌词候选文件") != candidate_bytes:
        raise ValueError("歌词候选发布后校验失败；文件内容不匹配。")
    if _score._existing_file(receipt_path, "歌词 receipt") != receipt_bytes:
        raise ValueError("歌词 receipt 发布后校验失败；文件内容不匹配。")
    _verify_source_unchanged(source, source_sha)
    return receipt


__all__ = [
    "apply_lyrics_patch",
    "plan_lyrics_patch",
    "read_lyrics",
    "review_lyrics",
]
