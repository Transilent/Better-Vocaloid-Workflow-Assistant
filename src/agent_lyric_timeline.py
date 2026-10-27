"""Pure lyric-timeline normalization and line/window review helpers.

The adapter accepts NetEase LRC/YRC text or the raw lyric JSON returned by the
existing provider layer. It never fetches, edits MIDI, calls a model, or
applies a timing patch. All review output is reference evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from common import native_path


_SCHEMA = "vwa-lyric-timeline-v1"
_KIND = "lyric-timeline"
_REVIEW_SCHEMA = "vwa-lyric-timeline-review-v1"
_REVIEW_KIND = "lyric-timeline-review"
_MAX_BYTES = 4 * 1024 * 1024
_MAX_WORDS = 20_000
_MAX_LINES = 100_000
_MAX_SECONDS = 366 * 24 * 60 * 60.0
_MAX_WINDOW_SECONDS = 30.0
_MAX_LIMIT = 1_000
_EPSILON = 1e-9

_LRC_TIME = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d{1,3})?)\]")
_LRC_META = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]*):([^\]]*)\]\s*$")
_LRC_OFFSET = re.compile(r"\[offset\s*:\s*([+-]?\d+)\]\s*$", re.I)
_YRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_YRC_WORD = re.compile(r"\((\d+),(\d+),(\d+)\)")
_YRC_SIGNATURE = re.compile(r"(?m)^\s*\[\d+\s*,\s*\d+\]\s*\(")
_CREDIT = re.compile(
    r"^\s*(?:作词|作詞|作曲|编曲|編曲|词曲|词|曲|混音|母带|母帶|录音|錄音|"
    r"制作人|製作人|制作|製作|监制|監製|发行|發行|出品|演唱|歌手|翻译|翻譯|"
    r"lyrics?|composer|arranger|vocal|producer|mixing|mastering)\s*[:：]",
    re.I,
)
_EXTENSION = re.compile(r"(?:\+|~|～|〜|-|—|–)\s*$")


def _number(value: Any, field: str, *, allow_none: bool = False,
            minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value is None and allow_none:
        return None
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限数值。")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} 必须是有限数值。")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} 不能小于 {minimum}。")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field} 超出支持的时间范围。")
    return result


def _position(value: Any, field: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} 必须是非负整数。")
    return value


def _round_seconds(value: float) -> float:
    return round(float(value), 9)


def _copy_json(value: Any, field: str) -> Any:
    if value is None:
        return None
    try:
        payload = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是可序列化且只含有限数值的数据。") from exc
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError(f"{field} 超过 4 MiB 限制。")
    return json.loads(payload)



def _input_text(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("歌词文本必须是有效 UTF-8。") from exc
        text = raw
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        encoded = bytes(raw)
        if len(encoded) > _MAX_BYTES:
            raise ValueError("歌词输入超过 4 MiB 限制。")
        try:
            text = encoded.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("歌词文本必须是有效 UTF-8。") from exc
    else:
        raise ValueError("歌词输入必须是 LRC/YRC 文本或网易云 JSON 对象。")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("歌词输入超过 4 MiB 限制。")
    if "\x00" in text:
        raise ValueError("歌词输入包含 NUL，不是可靠文本。")
    return text.lstrip("\ufeff")


def _format_hint(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("format_hint 只能是 lrc、yrc 或 auto。")
    hint = value.strip().lower().lstrip(".")
    if hint in ("", "auto"):
        return None
    if hint not in ("lrc", "yrc"):
        raise ValueError("无法识别的歌词格式；只支持 LRC、YRC 或 auto。")
    return hint


def _detect_format(text: str) -> str:
    if _YRC_SIGNATURE.search(text):
        return "yrc"
    if _LRC_TIME.search(text):
        return "lrc"
    raise ValueError("无法可靠判断歌词时间格式，请显式提供 format_hint。")


def _ms_seconds(value: int, field: str) -> float:
    if value > int(_MAX_SECONDS * 1000):
        raise ValueError(f"{field} 超出支持的时间范围。")
    return _round_seconds(value / 1000.0)


def _is_credit(text: str) -> bool:
    return bool(_CREDIT.match(text))


def _is_extension_marker(text: str) -> bool:
    return bool(text) and bool(_EXTENSION.search(text))


def _metadata_payload(source_format: str) -> dict[str, Any]:
    return {
        "source_format": source_format,
        "time_unit": "milliseconds" if source_format == "yrc" else "timestamp_tags",
        "offset_applied": False,
    }


def _parse_yrc(text: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    input_order = 0
    word_total = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{"):
            # NetEase YRC may carry a JSON signature row. It is metadata, never lyric text.
            continue
        match = _YRC_LINE.match(line)
        if not match:
            continue
        start_ms, duration_ms = int(match.group(1)), int(match.group(2))
        if duration_ms <= 0:
            raise ValueError("YRC 行时长必须是正整数毫秒。")
        line_start = _ms_seconds(start_ms, "YRC 行起点")
        line_end = _ms_seconds(start_ms + duration_ms, "YRC 行终点")
        content = match.group(3)
        tags = list(_YRC_WORD.finditer(content))
        if not tags:
            if _is_credit(content.strip()):
                continue
            raise ValueError("YRC 歌词行缺少逐字时间标签。")

        prefix = content[:tags[0].start()]
        if prefix.strip():
            if _is_credit(prefix.strip()) or prefix.strip().startswith("{"):
                continue
            raise ValueError("YRC 逐字时间前存在无法归属的文本。")

        words: list[dict[str, Any]] = []
        for index, tag in enumerate(tags):
            following = tags[index + 1].start() if index + 1 < len(tags) else len(content)
            token = content[tag.end():following]
            if not token or not token.strip():
                continue
            word_start_ms, word_duration_ms, flag = (int(part) for part in tag.groups())
            if word_duration_ms <= 0:
                raise ValueError("YRC 字时长必须是正整数毫秒。")
            word_start = _ms_seconds(word_start_ms, "YRC 字起点")
            word_end = _ms_seconds(word_start_ms + word_duration_ms, "YRC 字终点")
            if word_start < line_start - _EPSILON or word_end > line_end + _EPSILON:
                raise ValueError("YRC 字时间落在所属行时间范围之外。")
            words.append({
                "start_seconds": word_start,
                "end_seconds": word_end,
                "text": token,
                "flag": flag,
                "estimated": False,
            })
            word_total += 1
            if word_total > _MAX_WORDS:
                raise ValueError(f"歌词超过 {_MAX_WORDS} 个逐字词元限制。")

        line_text = "".join(word["text"] for word in words)
        if not line_text or _is_credit(line_text):
            continue
        records.append({
            "start_seconds": line_start,
            "end_seconds": line_end,
            "text": line_text,
            "start_estimated": False,
            "estimated": False,
            "end_estimated": False,
            "end_basis": "yrc_line_duration",
            "words": words,
            "_input_order": input_order,
        })
        input_order += 1

    if not records:
        raise ValueError("没有找到有效的 YRC 歌词行。")
    original_starts = [row["start_seconds"] for row in records]
    records.sort(key=lambda row: (row["start_seconds"], row["_input_order"]))
    if original_starts != [row["start_seconds"] for row in records]:
        warnings.append("YRC 行顺序已按时间稳定排序；同起点行保持原始顺序。")
    for line_index, row in enumerate(records):
        row.pop("_input_order", None)
        row["id"] = f"line-{line_index}"
        for word_index, word in enumerate(row["words"]):
            word["id"] = f"word-{line_index}-{word_index}"
    metadata = _metadata_payload("yrc")
    metadata["word_count"] = sum(len(row["words"]) for row in records)
    metadata["word_flags_preserved"] = True
    return records, metadata, warnings


def _parse_lrc(text: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    tags: dict[str, Any] = {}
    offset_values: list[int] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        offset_match = _LRC_OFFSET.search(stripped)
        if offset_match:
            offset_values.append(int(offset_match.group(1)))
        meta_match = _LRC_META.match(stripped)
        if meta_match and not _LRC_TIME.search(stripped):
            key, value = meta_match.group(1).strip().lower(), meta_match.group(2).strip()
            if key in tags:
                previous = tags[key]
                tags[key] = previous + [value] if isinstance(previous, list) else [previous, value]
            else:
                tags[key] = value

    offset_ms = offset_values[0] if offset_values else None
    if offset_values and any(value != offset_ms for value in offset_values[1:]):
        warnings.append("LRC 含多个不同 offset；已保留全部值，未自动选择偏移。")
    elif offset_ms not in (None, 0):
        warnings.append(
            f"LRC offset={offset_ms} ms 已保留为元数据但未应用；调用方须用显式 time_origin 决定方向。"
        )

    records: list[dict[str, Any]] = []
    sequence = 0
    for raw_line in text.splitlines():
        matches = list(_LRC_TIME.finditer(raw_line))
        if not matches:
            continue
        content = raw_line[matches[-1].end():].strip()
        if not content or _is_credit(content):
            continue
        for match in matches:
            minutes, seconds_text = int(match.group(1)), match.group(2)
            seconds = float(seconds_text)
            if not math.isfinite(seconds) or seconds < 0 or seconds >= 60:
                raise ValueError("LRC 秒字段必须是 [0, 60) 内的有限数值。")
            start = _round_seconds(minutes * 60.0 + seconds)
            if start > _MAX_SECONDS:
                raise ValueError("LRC 时间戳超出支持的时间范围。")
            records.append({
                "start_seconds": start,
                "text": content,
                "_sequence": sequence,
            })
            sequence += 1
            if len(records) > _MAX_LINES:
                raise ValueError(f"歌词超过 {_MAX_LINES} 行限制。")

    if not records:
        raise ValueError("没有找到有效的 LRC 歌词行。")
    original_starts = [row["start_seconds"] for row in records]
    records.sort(key=lambda row: (row["start_seconds"], row["_sequence"]))
    if original_starts != [row["start_seconds"] for row in records]:
        warnings.append("LRC 多标签行已按时间稳定排序；同起点与重复句保持原有顺序。")

    distinct_starts = sorted(set(row["start_seconds"] for row in records))
    next_start: dict[float, float | None] = {}
    for index, value in enumerate(distinct_starts):
        next_start[value] = distinct_starts[index + 1] if index + 1 < len(distinct_starts) else None
    for line_index, row in enumerate(records):
        row.pop("_sequence", None)
        row["id"] = f"line-{line_index}"
        following = next_start[row["start_seconds"]]
        row["end_seconds"] = following
        row["start_estimated"] = False
        row["end_estimated"] = True
        row["estimated"] = True
        row["end_basis"] = "next_distinct_lrc_line" if following is not None else "unknown_last_line"
        row["words"] = []
    warnings.append("LRC 行末由下一不同起点推得并标为 estimated；最后一行句末未知，未伪造。")
    if offset_values:
        tags["offset"] = tags.get("offset") or str(offset_ms)

    metadata = _metadata_payload("lrc")
    metadata.update({
        "offset_ms": offset_ms,
        "offset_values_ms": offset_values,
        "tags": tags,
        "has_word_timings": False,
    })
    return records, metadata, warnings


def _netease_text(raw: Mapping[str, Any]) -> str:
    yrc_block = raw.get("yrc")
    lrc_block = raw.get("lrc")

    def lyric(block: Any, name: str) -> str | None:
        if block is None:
            return None
        value = block.get("lyric") if isinstance(block, Mapping) else block
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError(f"网易云 JSON 的 {name}.lyric 必须是文本。")
        return value

    yrc = lyric(yrc_block, "yrc")
    if yrc is not None:
        return yrc
    lrc = lyric(lrc_block, "lrc")
    if lrc is not None:
        return lrc
    raise ValueError("网易云 JSON 中没有可用的 yrc.lyric 或 lrc.lyric。")


def normalize_timeline(raw: Any, format_hint: str | None = None, origin: Any = None) -> dict[str, Any]:
    """Normalize one LRC/YRC source without changing caller-owned input.

    Times remain in the reference's own seconds. In particular, an LRC
    offset is recorded but not applied. YRC word tokens retain their original
    text and explicit start/end milliseconds. A 30-second review window and
    any global positioning are chosen later by the caller.
    """
    hint = _format_hint(format_hint)
    origin_copy = _copy_json(origin, "origin")
    is_json = isinstance(raw, Mapping)
    if is_json:
        _copy_json(raw, "raw")
        text = _netease_text(raw)
        yrc_value = raw.get("yrc")
        has_yrc_lyric = bool(yrc_value.get("lyric") if isinstance(yrc_value, Mapping) else yrc_value)
        fmt = "yrc" if has_yrc_lyric else "lrc"
        if hint is not None and not has_yrc_lyric:
            fmt = hint
    else:
        text = _input_text(raw)
        fmt = hint or _detect_format(text)

    _input_text(text)
    if fmt == "yrc":
        lines, metadata, warnings = _parse_yrc(text)
        granularity = "word"
    else:
        lines, metadata, warnings = _parse_lrc(text)
        granularity = "line"
    metadata["container"] = "netease_json" if is_json else "text"
    return {
        "schema": _SCHEMA,
        "kind": _KIND,
        "origin": origin_copy,
        "granularity": granularity,
        "time_basis": "source_relative_seconds",
        "lines": lines,
        "metadata": metadata,
        "warnings": warnings,
    }


def _strict_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = Path(native_path(path)).read_bytes()
    except OSError as exc:
        raise ValueError("无法读取歌词时间轴 JSON。") from exc
    if len(raw) > _MAX_BYTES:
        raise ValueError("歌词时间轴 JSON 超过 4 MiB 限制。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("歌词时间轴 JSON 必须是有效 UTF-8。") from exc

    def reject_constant(value: str):
        raise ValueError(f"歌词时间轴 JSON 不允许非有限数值 {value}。")

    try:
        value = json.loads(text, parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise ValueError("歌词时间轴 JSON 无法解析。") from exc
    if not isinstance(value, dict):
        raise ValueError("歌词时间轴 JSON 顶层必须是对象。")
    return value, hashlib.sha256(raw).hexdigest()


def _validate_timeline(value: dict[str, Any]) -> list[dict[str, Any]]:
    if value.get("schema") != _SCHEMA or value.get("kind") != _KIND:
        raise ValueError("不是受支持的 VWA 歌词时间轴 JSON。")
    granularity = value.get("granularity")
    if granularity not in ("line", "word"):
        raise ValueError("歌词时间轴 granularity 必须是 line 或 word。")
    _copy_json(value.get("origin"), "origin")
    lines = value.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("歌词时间轴 lines 必须是非空数组。")
    if len(lines) > _MAX_LINES:
        raise ValueError(f"歌词时间轴超过 {_MAX_LINES} 行限制。")
    line_ids: set[str] = set()
    word_ids: set[str] = set()
    word_total = 0
    checked: list[dict[str, Any]] = []
    for line_index, row in enumerate(lines):
        if not isinstance(row, Mapping):
            raise ValueError(f"lines[{line_index}] 必须是对象。")
        line_id = row.get("id")
        text = row.get("text")
        if not isinstance(line_id, str) or not line_id or line_id in line_ids:
            raise ValueError(f"lines[{line_index}].id 缺失或重复。")
        if not isinstance(text, str) or not text:
            raise ValueError(f"lines[{line_index}].text 必须是非空文本。")
        line_ids.add(line_id)
        start = _number(row.get("start_seconds"), f"lines[{line_index}].start_seconds",
                        minimum=0, maximum=_MAX_SECONDS)
        end = _number(row.get("end_seconds"), f"lines[{line_index}].end_seconds",
                      allow_none=True, minimum=0, maximum=_MAX_SECONDS)
        if end is not None and end < start - _EPSILON:
            raise ValueError(f"lines[{line_index}] 的结束时间早于开始时间。")
        words = row.get("words")
        if not isinstance(words, list):
            raise ValueError(f"lines[{line_index}].words 必须是数组。")
        checked_words: list[dict[str, Any]] = []
        for word_index, word in enumerate(words):
            if not isinstance(word, Mapping):
                raise ValueError(f"lines[{line_index}].words[{word_index}] 必须是对象。")
            word_id = word.get("id")
            word_text = word.get("text")
            if not isinstance(word_id, str) or not word_id or word_id in word_ids:
                raise ValueError(f"lines[{line_index}].words[{word_index}].id 缺失或重复。")
            if not isinstance(word_text, str) or not word_text:
                raise ValueError(f"lines[{line_index}].words[{word_index}].text 必须是非空文本。")
            word_ids.add(word_id)
            word_start = _number(
                word.get("start_seconds"), f"lines[{line_index}].words[{word_index}].start_seconds",
                minimum=0, maximum=_MAX_SECONDS,
            )
            word_end = _number(
                word.get("end_seconds"), f"lines[{line_index}].words[{word_index}].end_seconds",
                minimum=0, maximum=_MAX_SECONDS,
            )
            if word_end <= word_start:
                raise ValueError(f"lines[{line_index}].words[{word_index}] 的结束必须晚于开始。")
            if word_start < start - _EPSILON or (end is not None and word_end > end + _EPSILON):
                raise ValueError(f"lines[{line_index}].words[{word_index}] 超出所属行时间范围。")
            checked_words.append(dict(word))
            word_total += 1
            if word_total > _MAX_WORDS:
                raise ValueError(f"歌词时间轴超过 {_MAX_WORDS} 个词元限制。")
        item = dict(row)
        item["start_seconds"] = start
        item["end_seconds"] = end
        item["words"] = checked_words
        checked.append(item)
    return checked


def _window_note(note: dict[str, Any], start: float, end: float) -> bool:
    onset = _number(note.get("onset_seconds"), "note.onset_seconds", minimum=0, maximum=_MAX_SECONDS)
    note_end = _number(note.get("end_seconds"), "note.end_seconds", allow_none=True,
                      minimum=0, maximum=_MAX_SECONDS)
    if onset >= end - _EPSILON:
        return False
    if note_end is None:
        return onset < end
    return note_end > start + _EPSILON or (note_end == start and onset == start)


def _reference_in_window(row: Mapping[str, Any], origin: float,
                         start: float, end: float) -> bool:
    cue_start = float(row["start_seconds"]) + origin
    cue_end = row.get("end_seconds")
    if cue_end is None:
        return start - _EPSILON <= cue_start < end - _EPSILON
    cue_end = float(cue_end) + origin
    return cue_end > start + _EPSILON and cue_start < end - _EPSILON


def _page(rows: list[dict[str, Any]], limit: int,
          offset: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page = rows[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(rows) else None
    return page, {
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }


def _overlap(first_start: float, first_end: float | None,
             second_start: float, second_end: float | None) -> bool:
    if first_end is None and second_end is None:
        return True
    if first_end is None:
        return first_start < second_end
    if second_end is None:
        return second_start < first_end
    return first_start < second_end and second_start < first_end


def _polyphonic_note_ids(notes: list[dict[str, Any]]) -> set[str]:
    by_text: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        lyric = note.get("lyric")
        if isinstance(lyric, str) and lyric:
            by_text.setdefault(lyric, []).append(note)
    ambiguous: set[str] = set()
    for rows in by_text.values():
        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1:]:
                if _overlap(
                    float(left["onset_seconds"]), left.get("end_seconds"),
                    float(right["onset_seconds"]), right.get("end_seconds"),
                ):
                    ambiguous.add(left["note_id"])
                    ambiguous.add(right["note_id"])
    return ambiguous


def _line_hint(note: dict[str, Any], lines: list[dict[str, Any]], origin: float,
               hint_index: int, *, word_level: bool) -> dict[str, Any] | None:
    lyric = note.get("lyric")
    if not isinstance(lyric, str) or not lyric:
        return None
    base = {
        "hint_id": f"lyric-reference-{note['note_id']}-{hint_index}",
        "note_id": note["note_id"],
        "note_text": lyric,
        "note_onset_seconds": _round_seconds(float(note["onset_seconds"])),
        "reference_scope": "line",
        "automatic_patch": False,
    }
    exact = [line for line in lines if line["text"] == lyric]
    if len(exact) == 1:
        line = exact[0]
        return {
            **base,
            "code": "exact_lrc_line_reference" if not word_level else "exact_yrc_line_reference",
            "severity": "info",
            "reference_line_id": line["id"],
            "reference_text": line["text"],
            "reference_start_seconds": line["start_seconds"],
            "reference_end_seconds": line["end_seconds"],
            "reference_start_seconds_midi": _round_seconds(float(line["start_seconds"]) + origin),
            "reference_end_seconds_midi": (
                None if line["end_seconds"] is None
                else _round_seconds(float(line["end_seconds"]) + origin)
            ),
            "line_start_delta_seconds": _round_seconds(
                float(note["onset_seconds"]) - float(line["start_seconds"]) - origin
            ),
            "word_timing_available": False,
            "ambiguous": False,
        }
    if len(exact) > 1:
        return {
            **base,
            "code": "ambiguous_line_reference",
            "severity": "warning",
            "candidate_line_ids": [line["id"] for line in exact],
            "ambiguous": True,
            "reason": "窗口内有多个精确同文行，不能据此给出时间偏移。",
        }
    candidates = [
        line for line in lines
        if _overlap(
            float(note["onset_seconds"]), note.get("end_seconds"),
            float(line["start_seconds"]) + origin,
            None if line["end_seconds"] is None else float(line["end_seconds"]) + origin,
        )
    ]
    if not candidates:
        return None
    return {
        **base,
        "code": "line_context_reference" if not word_level else "yrc_line_context_reference",
        "severity": "info",
        "candidate_line_ids": [line["id"] for line in candidates[:10]],
        "candidate_count": len(candidates),
        "ambiguous": len(candidates) > 1,
        "reason": "仅提供当前行作为参考，不代表该音符与整行同步。",
    }


def _word_hint(note: dict[str, Any], lines: list[dict[str, Any]], words: list[dict[str, Any]],
               origin: float, polyphonic: set[str], hint_index: int) -> dict[str, Any] | None:
    lyric = note.get("lyric")
    if not isinstance(lyric, str) or not lyric:
        return None
    base = {
        "hint_id": f"lyric-reference-{note['note_id']}-{hint_index}",
        "note_id": note["note_id"],
        "note_text": lyric,
        "note_onset_seconds": _round_seconds(float(note["onset_seconds"])),
        "reference_scope": "word",
        "automatic_patch": False,
    }
    if _is_extension_marker(lyric):
        return {
            **base,
            "code": "extension_marker_requires_review",
            "severity": "info",
            "ambiguous": True,
            "reason": "延音或 + 标记仅作待审上下文；不会自动分配音高、字时间或改写歌词。",
        }
    exact = [word for word in words if word["text"] == lyric]
    if not exact:
        return _line_hint(note, lines, origin, hint_index, word_level=True)
    if len(exact) > 1:
        return {
            **base,
            "code": "ambiguous_exact_yrc_word_reference",
            "severity": "warning",
            "candidate_word_ids": [word["id"] for word in exact],
            "candidate_reference_starts_seconds_midi": [
                _round_seconds(float(word["start_seconds"]) + origin) for word in exact
            ],
            "ambiguous": True,
            "reason": "窗口内存在多个精确同文 YRC 词元，不能给出偏移。",
        }
    if note["note_id"] in polyphonic:
        return {
            **base,
            "code": "polyphonic_exact_yrc_word_reference",
            "severity": "warning",
            "reference_word_id": exact[0]["id"],
            "reference_line_id": exact[0]["line_id"],
            "ambiguous": True,
            "reason": "同文多音符重叠，无法把唯一参考词元分配给其中一个音符。",
        }
    word = exact[0]
    delta = _round_seconds(
        float(note["onset_seconds"]) - float(word["start_seconds"]) - origin
    )
    return {
        **base,
        "code": "unique_exact_yrc_word_reference",
        "severity": "info",
        "reference_word_id": word["id"],
        "reference_line_id": word["line_id"],
        "reference_start_seconds": word["start_seconds"],
        "reference_end_seconds": word["end_seconds"],
        "reference_start_seconds_midi": _round_seconds(float(word["start_seconds"]) + origin),
        "reference_end_seconds_midi": _round_seconds(float(word["end_seconds"]) + origin),
        "delta_seconds": delta,
        "possible_offset": delta,
        "offset_semantics": "note_onset_minus_reference_start",
        "ambiguous": False,
    }


def review_timeline(score_path: Any, timeline_path: Any, *, time_origin: float = 0.0,
                    start: float | None = None, end: float | None = None,
                    track_indices: Iterable[int] | None = None, limit: int = 20,
                    offset: int = 0) -> dict[str, Any]:
    """Compare a bounded current segment with a saved normalized timeline."""
    if start is None or end is None:
        raise ValueError("必须显式提供 start 和 end，窗口最多 30 秒。")
    window_start = _number(start, "start", maximum=_MAX_SECONDS)
    window_end = _number(end, "end", maximum=_MAX_SECONDS)
    if window_end <= window_start:
        raise ValueError("end 必须晚于 start。")
    if window_end - window_start > _MAX_WINDOW_SECONDS + _EPSILON:
        raise ValueError("review_timeline 当前段窗口最多 30 秒。")
    position_origin = _number(time_origin, "time_origin")
    if abs(position_origin) > _MAX_SECONDS:
        raise ValueError("time_origin 超出支持范围。")
    page_limit = _position(limit, "limit")
    if not 1 <= page_limit <= _MAX_LIMIT:
        raise ValueError(f"limit 必须在 1..{_MAX_LIMIT}。")
    page_offset = _position(offset, "offset")

    if track_indices is None:
        wanted_tracks: set[int] | None = None
    else:
        if isinstance(track_indices, (str, bytes, Mapping)):
            raise ValueError("track_indices 必须是整数数组。")
        try:
            values = list(track_indices)
        except TypeError as exc:
            raise ValueError("track_indices 必须是整数数组。") from exc
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("track_indices 必须是非负整数数组。")
        wanted_tracks = set(values)

    try:
        timeline_file = Path(timeline_path)
    except (TypeError, ValueError) as exc:
        raise ValueError("歌词时间轴 JSON 路径无效。") from exc
    timeline, reference_sha = _strict_json(timeline_file)
    lines = _validate_timeline(timeline)

    from agent_score import read_score
    score = read_score(score_path)
    if score.get("format") != "midi":
        raise ValueError("歌词时间轴复核只接受 MIDI 乐谱。")

    note_rows: list[dict[str, Any]] = []
    for raw_note in score.get("notes", []):
        if not isinstance(raw_note, Mapping):
            raise ValueError("MIDI 乐谱音符记录不可靠。")
        track_index = raw_note.get("track_index")
        if type(track_index) is not int:
            raise ValueError("MIDI 乐谱 track_index 不可靠。")
        if wanted_tracks is not None and track_index not in wanted_tracks:
            continue
        note = dict(raw_note)
        if _window_note(note, window_start, window_end):
            note_rows.append(note)
    note_rows.sort(key=lambda item: (
        float(item["onset_seconds"]), int(item["track_index"]),
        int(item["pitch"]), str(item.get("note_id", "")),
    ))

    reference_lines: list[dict[str, Any]] = []
    reference_words: list[dict[str, Any]] = []
    for line in lines:
        if not _reference_in_window(line, position_origin, window_start, window_end):
            continue
        line_view = {key: value for key, value in line.items() if key != "words"}
        line_view["word_count"] = len(line["words"])
        line_view["midi_start_seconds"] = _round_seconds(float(line["start_seconds"]) + position_origin)
        line_view["midi_end_seconds"] = (
            None if line["end_seconds"] is None
            else _round_seconds(float(line["end_seconds"]) + position_origin)
        )
        reference_lines.append(line_view)
        for word in line["words"]:
            if not _reference_in_window(word, position_origin, window_start, window_end):
                continue
            word_view = dict(word)
            word_view["line_id"] = line["id"]
            word_view["line_text"] = line["text"]
            word_view["midi_start_seconds"] = _round_seconds(float(word["start_seconds"]) + position_origin)
            word_view["midi_end_seconds"] = _round_seconds(float(word["end_seconds"]) + position_origin)
            reference_words.append(word_view)

    note_page, note_pagination = _page(note_rows, page_limit, page_offset)
    line_page, line_pagination = _page(reference_lines, page_limit, page_offset)
    word_page, word_pagination = _page(reference_words, page_limit, page_offset)

    polyphonic = _polyphonic_note_ids(note_rows)
    word_level = timeline["granularity"] == "word"
    hints: list[dict[str, Any]] = []
    for note in note_page:
        hint = (
            _word_hint(note, reference_lines, reference_words, position_origin, polyphonic, len(hints))
            if word_level else
            _line_hint(note, reference_lines, position_origin, len(hints), word_level=False)
        )
        if hint is not None:
            hints.append(hint)

    ambiguous_hints = sum(1 for hint in hints if hint.get("ambiguous"))
    possible_offsets = sum(1 for hint in hints if "possible_offset" in hint)
    summary = {
        "notes_in_window": len(note_rows),
        "reference_lines_in_window": len(reference_lines),
        "reference_words_in_window": len(reference_words),
        "notes_returned": len(note_page),
        "reference_lines_returned": len(line_page),
        "reference_words_returned": len(word_page),
        "hints_returned": len(hints),
        "ambiguous_hints": ambiguous_hints,
        "unique_possible_offsets": possible_offsets,
    }
    return {
        "schema": _REVIEW_SCHEMA,
        "kind": _REVIEW_KIND,
        "source_sha256": score["source_sha256"],
        "reference_sha256": reference_sha,
        "timeline_granularity": timeline["granularity"],
        "reference_origin": timeline.get("origin"),
        "window": {
            "start_seconds": _round_seconds(window_start),
            "end_seconds": _round_seconds(window_end),
            "duration_seconds": _round_seconds(window_end - window_start),
            "max_seconds": _MAX_WINDOW_SECONDS,
        },
        "explicit_time_origin": _round_seconds(position_origin),
        "time_mapping": "midi_seconds = reference_seconds + explicit_time_origin",
        "track_indices": None if wanted_tracks is None else sorted(wanted_tracks),
        "pagination": {
            "notes": note_pagination,
            "lines": line_pagination,
            "words": word_pagination,
        },
        "notes": note_page,
        "reference_cues": {
            "lines": line_page,
            "words": word_page,
        },
        "hints": hints,
        "summary": summary,
        "automatic_patch": False,
        "reference_status": {
            "role": "reference_not_ground_truth",
            "explicit_time_origin_seconds": _round_seconds(position_origin),
            "original_source_and_version_verified": False,
        },
        "disclaimer": "歌词时间轴仅为外部参考，不是 MIDI 真值；未验证原始来源和版本，也不会自动改写乐谱。",
    }
