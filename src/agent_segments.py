"""Pure, bounded segmentation of MIDI or SVP note evidence.

This module deliberately depends only on the Python standard library.  It
accepts the complete internal JSON returned by ``agent_score.read_score`` or
``agent_style.read_context`` and returns note identifiers for one reviewable
segment at a time.  It performs no model call, media access, emotion
inference, or musical-semantic classification.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any


DEFAULT_MAX_NOTES = 16
DEFAULT_MAX_SECONDS = 10.0
GAP_SECONDS = 0.65
LONG_NOTE_REASON = "unbreakable_long_note_exceeds_max_seconds"

_EPSILON = 1e-9
_STYLE_CONTEXT_KIND = "vwa.style-context"


class SegmentError(ValueError):
    """Stable, actionable error raised by the pure segmentation helpers."""

    component = "agent_segments"
    retryable = False
    remediation = ["vwa_score_read", "vwa_context_read"]

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        remediation: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = {} if details is None else dict(details)
        self.retryable = bool(retryable)
        self.remediation = list(
            self.__class__.remediation if remediation is None else remediation
        )


@dataclass(frozen=True)
class _VoiceNote:
    """A validated note with its original input position."""

    ordinal: int
    note_id: str
    track_index: int
    channel: int | None
    start_seconds: float
    end_seconds: float
    onset_tick: int | float | None
    end_tick: int | float | None
    pitch: int | float | None
    lyric: str | None

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class _DraftSegment:
    notes: tuple[_VoiceNote, ...]
    reason: str | None = None

    @property
    def track_index(self) -> int:
        return self.notes[0].track_index

    @property
    def channel(self) -> int | None:
        return self.notes[0].channel

    @property
    def start_seconds(self) -> float:
        return min(note.start_seconds for note in self.notes)

    @property
    def end_seconds(self) -> float:
        return max(note.end_seconds for note in self.notes)

    @property
    def start_tick(self) -> int | float | None:
        values = [note.onset_tick for note in self.notes]
        if any(value is None for value in values):
            return None
        return min(values)

    @property
    def end_tick(self) -> int | float | None:
        values = [note.end_tick for note in self.notes]
        if any(value is None for value in values):
            return None
        return max(values)


def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise SegmentError(
            "invalid_segment_data",
            "段指纹需要有限的 JSON 数据。",
            details={"reason": str(error)},
        ) from error
    return text.encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_sha256(score: Any) -> str:
    if not isinstance(score, Mapping):
        raise SegmentError(
            "invalid_score",
            "score 必须是 agent_score 或 agent_style 返回的 JSON 对象。",
            details={"field": "score", "actual_type": type(score).__name__},
        )
    value = score.get("source_sha256")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise SegmentError(
            "invalid_source_sha256",
            "score.source_sha256 必须是 64 位十六进制 SHA-256。",
            details={"field": "source_sha256"},
        )
    return value.lower()


def _validate_limits(max_notes: Any, max_seconds: Any) -> tuple[int, float]:
    if type(max_notes) is not int or max_notes < 1:
        raise SegmentError(
            "invalid_max_notes",
            "max_notes 必须是大于等于 1 的整数。",
            details={"field": "max_notes", "value": max_notes},
        )
    if type(max_seconds) not in (int, float):
        raise SegmentError(
            "invalid_max_seconds",
            "max_seconds 必须是有限正数。",
            details={"field": "max_seconds", "value": max_seconds},
        )
    seconds = float(max_seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise SegmentError(
            "invalid_max_seconds",
            "max_seconds 必须是有限正数。",
            details={"field": "max_seconds", "value": max_seconds},
        )
    return max_notes, seconds


def _finite_seconds(value: Any, field: str, note_index: int, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float):
        raise SegmentError(
            "invalid_note_timing",
            "音符起止秒必须是有限数值。",
            details={"field": field, "note_index": note_index},
        )
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise SegmentError(
            "invalid_note_timing",
            "音符起止秒必须是有限数值。",
            details={
                "field": field,
                "note_index": note_index,
                "value": value,
                "minimum": minimum,
            },
        )
    return number


def _tick_value(
    value: Any,
    field: str,
    note_index: int,
    *,
    required: bool,
) -> int | float | None:
    if value is None:
        if required:
            raise SegmentError(
                "invalid_note_timing",
                "MIDI 音符必须具有 onset_tick 和 end_tick。",
                details={"field": field, "note_index": note_index, "required": True},
            )
        return None
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise SegmentError(
            "invalid_note_timing",
            "音符 tick 必须是有限数值。",
            details={"field": field, "note_index": note_index, "value": value},
        )
    number = float(value)
    if number < 0:
        raise SegmentError(
            "invalid_note_timing",
            "音符 tick 不能为负数。",
            details={"field": field, "note_index": note_index, "value": value},
        )
    return int(number) if number.is_integer() else number


def _note_start(note: Mapping[str, Any], score_kind: Any, note_index: int) -> float:
    has_onset = "onset_seconds" in note
    has_start = "start_seconds" in note
    if has_onset and has_start:
        field = "start_seconds" if score_kind == _STYLE_CONTEXT_KIND else "onset_seconds"
    elif has_onset:
        field = "onset_seconds"
    elif has_start:
        field = "start_seconds"
    else:
        raise SegmentError(
            "invalid_note_timing",
            "音符缺少 onset_seconds 或 start_seconds。",
            details={"field": "start_seconds", "note_index": note_index},
        )
    return _finite_seconds(note.get(field), field, note_index, minimum=0.0)


def _track_flags(score: Mapping[str, Any]) -> dict[int, dict[str, bool | None]]:
    raw_tracks = score.get("tracks")
    if raw_tracks is None:
        return {}
    if not isinstance(raw_tracks, list):
        raise SegmentError(
            "invalid_tracks",
            "score.tracks 必须是数组。",
            details={"field": "tracks"},
        )
    result: dict[int, dict[str, bool | None]] = {}
    for row_index, row in enumerate(raw_tracks):
        if not isinstance(row, Mapping):
            raise SegmentError(
                "invalid_track",
                "score.tracks 中每一项必须是对象。",
                details={"field": "tracks", "row_index": row_index},
            )
        track_index = row.get("track_index")
        if type(track_index) is not int or track_index < 0:
            raise SegmentError(
                "invalid_track",
                "轨道 track_index 必须是非负整数。",
                details={"field": "tracks[].track_index", "row_index": row_index},
            )
        if track_index in result:
            raise SegmentError(
                "duplicate_track_index",
                "score.tracks 中存在重复的 track_index。",
                details={"track_index": track_index},
            )
        flags: dict[str, bool | None] = {}
        for field in ("instrumental", "vocal"):
            if field not in row:
                continue
            value = row.get(field)
            if value is not None and type(value) is not bool:
                raise SegmentError(
                    "invalid_track_metadata",
                    "轨道的 instrumental/vocal 标记必须是布尔值或 null。",
                    details={"track_index": track_index, "field": field},
                )
            flags[field] = value
        result[track_index] = flags
    return result


def _normalise_score(score: Any) -> tuple[str, list[Mapping[str, Any]], list[_VoiceNote], dict[int, dict[str, bool | None]]]:
    source_sha = _source_sha256(score)
    raw_notes = score.get("notes")
    if not isinstance(raw_notes, list):
        raise SegmentError(
            "invalid_notes",
            "score.notes 必须是数组。",
            details={"field": "notes"},
        )
    track_flags = _track_flags(score)
    score_kind = score.get("kind")
    score_format = score.get("format")
    midi_ticks_required = score_format == "midi"
    seen_ids: set[str] = set()
    normalised: list[_VoiceNote] = []
    for note_index, note in enumerate(raw_notes):
        if not isinstance(note, Mapping):
            raise SegmentError(
                "invalid_note",
                "score.notes 中每一项必须是对象。",
                details={"note_index": note_index},
            )
        note_id = note.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise SegmentError(
                "invalid_note_id",
                "音符必须具有非空字符串 note_id。",
                details={"note_index": note_index},
            )
        if note_id in seen_ids:
            raise SegmentError(
                "duplicate_note_id",
                "score.notes 中存在重复的 note_id。",
                details={"note_index": note_index, "note_id": note_id},
            )
        seen_ids.add(note_id)

        track_index = note.get("track_index")
        if type(track_index) is not int or track_index < 0:
            raise SegmentError(
                "invalid_note",
                "音符 track_index 必须是非负整数。",
                details={"note_index": note_index, "field": "track_index"},
            )
        channel = note.get("channel")
        if channel is not None and (type(channel) is not int or channel < 0):
            raise SegmentError(
                "invalid_note",
                "音符 channel 必须是非负整数或 null。",
                details={"note_index": note_index, "field": "channel"},
            )

        start = _note_start(note, score_kind, note_index)
        end = _finite_seconds(
            note.get("end_seconds"), "end_seconds", note_index, minimum=0.0
        )
        if end <= start:
            raise SegmentError(
                "invalid_note_timing",
                "音符必须满足 start_seconds < end_seconds。",
                details={
                    "note_index": note_index,
                    "note_id": note_id,
                    "start_seconds": start,
                    "end_seconds": end,
                },
            )

        if "pitch" not in note:
            raise SegmentError(
                "invalid_note",
                "音符缺少 pitch。",
                details={"note_index": note_index, "note_id": note_id, "field": "pitch"},
            )
        pitch = note.get("pitch")
        if pitch is not None:
            if type(pitch) not in (int, float) or not math.isfinite(float(pitch)):
                raise SegmentError(
                    "invalid_note",
                    "音符 pitch 必须是有限数值或 null。",
                    details={"note_index": note_index, "note_id": note_id, "field": "pitch"},
                )
            pitch = int(pitch) if float(pitch).is_integer() else float(pitch)

        lyric = note.get("lyric")
        if lyric is not None and not isinstance(lyric, str):
            raise SegmentError(
                "invalid_note",
                "音符 lyric 必须是字符串或 null。",
                details={"note_index": note_index, "note_id": note_id, "field": "lyric"},
            )

        onset_tick = _tick_value(
            note.get("onset_tick"),
            "onset_tick",
            note_index,
            required=midi_ticks_required,
        )
        end_tick = _tick_value(
            note.get("end_tick"),
            "end_tick",
            note_index,
            required=midi_ticks_required,
        )
        duration_tick = _tick_value(
            note.get("duration_tick"),
            "duration_tick",
            note_index,
            required=False,
        )
        if end_tick is None and onset_tick is not None and duration_tick is not None:
            end_tick = onset_tick + duration_tick
        if (onset_tick is None) != (end_tick is None):
            raise SegmentError(
                "invalid_note_timing",
                "音符 onset_tick 与 end_tick 必须同时提供或同时省略。",
                details={
                    "note_index": note_index,
                    "note_id": note_id,
                    "onset_tick": onset_tick,
                    "end_tick": end_tick,
                },
            )
        if (
            onset_tick is not None
            and end_tick is not None
            and float(end_tick) <= float(onset_tick)
        ):
            raise SegmentError(
                "invalid_note_timing",
                "音符必须满足 onset_tick < end_tick。",
                details={
                    "note_index": note_index,
                    "note_id": note_id,
                    "onset_tick": onset_tick,
                    "end_tick": end_tick,
                },
            )

        normalised.append(_VoiceNote(
            ordinal=note_index,
            note_id=note_id,
            track_index=track_index,
            channel=channel,
            start_seconds=start,
            end_seconds=end,
            onset_tick=onset_tick,
            end_tick=end_tick,
            pitch=pitch,
            lyric=lyric,
        ))
    return source_sha, raw_notes, normalised, track_flags



def _pitch_order(pitch: int | float | None) -> tuple[int, float]:
    return (1, 0.0) if pitch is None else (0, float(pitch))


def _note_order(note: _VoiceNote) -> tuple[Any, ...]:
    return (
        note.start_seconds,
        note.end_seconds,
        *_pitch_order(note.pitch),
        note.lyric or "",
        note.ordinal,
    )


def _canonical_note(note: _VoiceNote) -> dict[str, Any]:
    return {
        "start_seconds": note.start_seconds,
        "end_seconds": note.end_seconds,
        "onset_tick": note.onset_tick,
        "end_tick": note.end_tick,
        "pitch": note.pitch,
        "lyric": note.lyric,
    }


def _fingerprint(draft: _DraftSegment) -> str:
    notes = sorted(
        (_canonical_note(note) for note in draft.notes),
        key=lambda row: _canonical(row),
    )
    payload = {
        "track_index": draft.track_index,
        "channel": draft.channel,
        "start_seconds": draft.start_seconds,
        "end_seconds": draft.end_seconds,
        "start_tick": draft.start_tick,
        "end_tick": draft.end_tick,
        "notes": notes,
    }
    return _sha256(_canonical(payload))


def _segment_id(source_sha: str, fingerprint: str) -> str:
    identity = _sha256(_canonical({
        "source_sha256": source_sha,
        "fingerprint": fingerprint,
    }))
    return "segment-" + source_sha[:16] + "-" + identity[:20]


def _window_index(note: _VoiceNote, max_seconds: float) -> int:
    try:
        value = math.floor(note.start_seconds / max_seconds)
    except (OverflowError, ValueError) as error:
        raise SegmentError(
            "invalid_max_seconds",
            "max_seconds 太小，无法为音符建立有限窗口。",
            details={"note_id": note.note_id, "max_seconds": max_seconds},
        ) from error
    if value < 0:
        raise SegmentError(
            "invalid_note_timing",
            "音符起始秒不能为负数。",
            details={"note_id": note.note_id, "start_seconds": note.start_seconds},
        )
    return int(value)


def _split_voice(
    notes: list[_VoiceNote], max_notes: int, max_seconds: float
) -> list[_DraftSegment]:
    windows: dict[int, list[_VoiceNote]] = defaultdict(list)
    for note in notes:
        windows[_window_index(note, max_seconds)].append(note)

    result: list[_DraftSegment] = []
    for window in sorted(windows):
        current: list[_VoiceNote] = []
        running_end: float | None = None

        def flush() -> None:
            nonlocal current, running_end
            if current:
                result.append(_DraftSegment(tuple(current)))
                current = []
                running_end = None

        for note in sorted(windows[window], key=_note_order):
            if note.duration_seconds > max_seconds + _EPSILON:
                flush()
                result.append(_DraftSegment((note,), LONG_NOTE_REASON))
                continue
            if current:
                gap = note.start_seconds - float(running_end)
                candidate_end = max(float(running_end), note.end_seconds)
                exceeds_span = (
                    candidate_end - current[0].start_seconds
                    > max_seconds + _EPSILON
                )
                if (
                    gap >= GAP_SECONDS - _EPSILON
                    or len(current) >= max_notes
                    or exceeds_span
                ):
                    flush()
            current.append(note)
            running_end = (
                note.end_seconds
                if running_end is None
                else max(running_end, note.end_seconds)
            )
        flush()
    return result


def _public_segment(
    draft: _DraftSegment,
    *,
    source_sha: str,
    index: int,
) -> dict[str, Any]:
    fingerprint = _fingerprint(draft)
    ordered = sorted(draft.notes, key=_note_order)
    segment: dict[str, Any] = {
        "id": _segment_id(source_sha, fingerprint),
        "fingerprint": fingerprint,
        "index": index,
        "track_index": draft.track_index,
        "channel": draft.channel,
        "start_seconds": draft.start_seconds,
        "end_seconds": draft.end_seconds,
        "note_count": len(ordered),
        "note_ids": [note.note_id for note in ordered],
    }
    if draft.start_tick is not None and draft.end_tick is not None:
        segment["start_tick"] = draft.start_tick
        segment["end_tick"] = draft.end_tick
    if draft.reason is not None:
        segment["reason"] = draft.reason
    return segment


def plan_segments(
    score: Any,
    max_notes: int = DEFAULT_MAX_NOTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> dict[str, Any]:
    """Plan bounded, deterministic review segments from complete score JSON.

    Notes are grouped by ``(track_index, channel)``.  The fixed windows are
    anchored at project second zero (``[0,max), [max,2*max), ...``); within a
    window a silence of at least 0.65 seconds starts a new phrase.  A complete
    note is never split.  A single note longer than ``max_seconds`` is kept as
    one segment and carries ``reason=LONG_NOTE_REASON``.
    """
    max_notes, max_seconds = _validate_limits(max_notes, max_seconds)
    source_sha, _raw_notes, notes, track_flags = _normalise_score(score)

    grouped: dict[tuple[int, int | None], list[_VoiceNote]] = defaultdict(list)
    for note in notes:
        grouped[(note.track_index, note.channel)].append(note)

    drafts: list[_DraftSegment] = []
    for (track_index, channel), voice_notes in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], -1 if item[0][1] is None else item[0][1]),
    ):
        flags = track_flags.get(track_index, {})
        if flags.get("instrumental") is True:
            continue
        # ``vocal`` exists only in the SVP style context.  Honor an explicit
        # non-vocal result without inventing a conductor flag from lyrics.
        if flags.get("vocal") is False:
            continue
        drafts.extend(_split_voice(voice_notes, max_notes, max_seconds))

    drafts.sort(key=lambda draft: (
        draft.track_index,
        -1 if draft.channel is None else draft.channel,
        draft.start_seconds,
        draft.end_seconds,
        _fingerprint(draft),
    ))
    segments = [
        _public_segment(draft, source_sha=source_sha, index=index)
        for index, draft in enumerate(drafts)
    ]
    return {
        "source_sha256": source_sha,
        "segments": segments,
        "total_segments": len(segments),
        "total_notes": sum(segment["note_count"] for segment in segments),
    }


def _validated_segment(
    score: Any, segment: Any
) -> tuple[str, list[Mapping[str, Any]], list[_VoiceNote], Mapping[str, Any]]:
    source_sha, raw_notes, notes, _track_flags = _normalise_score(score)
    if not isinstance(segment, Mapping):
        raise SegmentError(
            "invalid_segment",
            "segment 必须是 plan_segments 返回的对象。",
            details={"field": "segment", "actual_type": type(segment).__name__},
        )
    note_ids = segment.get("note_ids")
    if not isinstance(note_ids, list) or not note_ids:
        raise SegmentError(
            "invalid_segment",
            "segment.note_ids 必须是非空数组。",
            details={"field": "note_ids"},
        )
    if any(not isinstance(note_id, str) or not note_id for note_id in note_ids):
        raise SegmentError(
            "invalid_segment",
            "segment.note_ids 中每一项必须是非空字符串。",
            details={"field": "note_ids"},
        )
    if len(set(note_ids)) != len(note_ids):
        raise SegmentError(
            "invalid_segment",
            "segment.note_ids 不能包含重复值。",
            details={"field": "note_ids"},
        )

    track_index = segment.get("track_index")
    if type(track_index) is not int or track_index < 0:
        raise SegmentError(
            "invalid_segment",
            "segment.track_index 必须是非负整数。",
            details={"field": "track_index"},
        )
    channel = segment.get("channel")
    if channel is not None and (type(channel) is not int or channel < 0):
        raise SegmentError(
            "invalid_segment",
            "segment.channel 必须是非负整数或 null。",
            details={"field": "channel"},
        )

    by_id = {note.note_id: note for note in notes}
    missing = [note_id for note_id in note_ids if note_id not in by_id]
    if missing:
        raise SegmentError(
            "unknown_note_id",
            "段引用的音符不在当前 score 中；该段可能已过期。",
            details={"missing_note_ids": missing[:20], "missing_count": len(missing)},
        )
    selected = [by_id[note_id] for note_id in note_ids]
    if any(
        note.track_index != track_index or note.channel != channel
        for note in selected
    ):
        raise SegmentError(
            "stale_segment",
            "段中的音符已不在声明的轨道或声道。",
            details={"track_index": track_index, "channel": channel},
        )

    draft = _DraftSegment(tuple(selected))
    expected_fingerprint = _fingerprint(draft)
    expected_id = _segment_id(source_sha, expected_fingerprint)
    checks = {
        "id": expected_id,
        "fingerprint": expected_fingerprint,
        "start_seconds": draft.start_seconds,
        "end_seconds": draft.end_seconds,
        "note_count": len(selected),
    }
    if draft.start_tick is not None and draft.end_tick is not None:
        checks["start_tick"] = draft.start_tick
        checks["end_tick"] = draft.end_tick
    mismatches = {
        field: {"expected": expected, "actual": segment.get(field)}
        for field, expected in checks.items()
        if segment.get(field) != expected
    }
    if mismatches:
        raise SegmentError(
            "stale_segment",
            "段与当前 score 的身份、几何、歌词或声道不一致，请重新 plan_segments。",
            details={"mismatches": mismatches},
        )
    return source_sha, raw_notes, selected, segment


def segment_notes(score: Any, segment: Any) -> list[dict[str, Any]]:
    """Return the current score's note rows for exactly one validated segment."""

    _source_sha, raw_notes, selected, _segment = _validated_segment(score, segment)
    return [copy.deepcopy(dict(raw_notes[note.ordinal])) for note in selected]


def adjacent_context(
    score: Any,
    segment: Any,
    limit: int = 2,
) -> dict[str, Any]:
    """Return at most ``limit`` notes immediately before and after a segment.

    Only the same ``(track_index, channel)`` voice is considered.  Adjacent
    half-open intervals are allowed: a note ending exactly at the segment start
    is "before", and one starting exactly at the segment end is "after".  The
    result never includes the full score.
    """
    if type(limit) is not int or limit < 0:
        raise SegmentError(
            "invalid_limit",
            "limit 必须是大于等于 0 的整数。",
            details={"field": "limit", "value": limit},
        )
    source_sha, raw_notes, notes, _track_flags = _normalise_score(score)
    _source, _raw, selected, validated = _validated_segment(score, segment)
    segment_start = min(note.start_seconds for note in selected)
    segment_end = max(note.end_seconds for note in selected)
    selected_ids = {note.note_id for note in selected}
    voice = [
        note for note in notes
        if note.track_index == validated["track_index"]
        and note.channel == validated["channel"]
        and note.note_id not in selected_ids
    ]
    before_candidates = sorted(
        (note for note in voice if note.end_seconds <= segment_start + _EPSILON),
        key=lambda note: (note.end_seconds, note.start_seconds, note.ordinal),
    )
    after_candidates = sorted(
        (note for note in voice if note.start_seconds >= segment_end - _EPSILON),
        key=lambda note: (note.start_seconds, note.end_seconds, note.ordinal),
    )
    before = before_candidates[-limit:] if limit else []
    after = after_candidates[:limit]
    return {
        "id": validated["id"],
        "track_index": validated["track_index"],
        "channel": validated["channel"],
        "limit": limit,
        "before": [
            copy.deepcopy(dict(raw_notes[note.ordinal])) for note in before
        ],
        "after": [
            copy.deepcopy(dict(raw_notes[note.ordinal])) for note in after
        ],
        "has_more_before": len(before_candidates) > limit,
        "has_more_after": len(after_candidates) > limit,
        "source_sha256": source_sha,
    }