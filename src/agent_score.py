"""Pure MIDI score reading, review and safe patch business functions.

The module deliberately has no MCP or Qt dependency.  MIDI is the only
writeable score format: SVP projects can be read for inspection, but editing
them requires project-specific serialization that this worker does not offer.
"""

from __future__ import annotations

import bisect
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import mido
from common import native_path


def _io_path(path):
    return Path(native_path(path))


_SCHEMA_VERSION = 1
_PLAN_KIND = "vwa-score-patch-plan"
_RECEIPT_KIND = "vwa-score-patch-receipt"
_DIFF_SCHEMA = "vwa-note-diff-v1"
_DEFAULT_TEMPO = 500_000
_MAX_TICK = 0x0FFFFFFF
_SVP_UNITS_PER_QUARTER = 705_600_000
_EDIT_FIELDS = ("pitch", "onset_tick", "duration_tick")
_EDIT_KEYS = {"note_id", *_EDIT_FIELDS}
_PLAN_KEYS = {
    "schema_version",
    "kind",
    "source_sha256",
    "format",
    "midi_type",
    "ticks_per_beat",
    "edits",
    "diff",
    "expected_candidate_sha256",
    "structural_changes",
    "warnings",
    "plan_sha256",
}
_RECEIPT_KEYS = {
    "schema_version",
    "kind",
    "plan_sha256",
    "source_sha256",
    "candidate_path",
    "candidate_sha256",
    "receipt_path",
    "diff",
    "structural_changes",
    "warnings",
    "receipt_sha256",
}


@dataclass
class _EventRecord:
    event_index: int
    tick: int
    message: Any


@dataclass
class _NoteRecord:
    note_id: str
    ordinal: int
    track_index: int
    channel: int
    pitch: int
    velocity: int
    onset_tick: int
    on_event_index: int
    off_event_index: int | None = None
    end_tick: int | None = None
    pairing_ambiguous: bool = False
    lyric: str | None = None
    lyric_event_index: int | None = None

    @property
    def duration_tick(self) -> int | None:
        if self.end_tick is None:
            return None
        return self.end_tick - self.onset_tick


@dataclass
class _TrackRecord:
    index: int
    name: str | None
    instrument_name: str | None
    messages: list[Any]
    events: list[_EventRecord]
    channels: set[int] = field(default_factory=set)
    notes: list[_NoteRecord] = field(default_factory=list)
    lyrics: list[dict[str, Any]] = field(default_factory=list)
    end_tick: int = 0


@dataclass
class _ParsedMidi:
    path_text: str
    raw: bytes
    source_sha256: str
    midi: Any
    tracks: list[_TrackRecord]
    tempo_events: list[dict[str, Any]]
    integrity_issues: list[dict[str, Any]]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(payload)


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    text = json.dumps(value, **options)
    if pretty:
        text += "\n"
    return text.encode("utf-8")


def _round_seconds(value: float | None) -> float | None:
    return None if value is None else round(float(value), 9)


def _as_number(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数值。")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} 不能小于 {minimum}。")
    return number


def _integer_position(value: Any, field_name: str, *, minimum: int = 0) -> int:
    number = _as_number(value, field_name, minimum=minimum)
    if not number.is_integer():
        raise ValueError(f"{field_name} 必须是非负整数位置。")
    result = int(number)
    if result > _MAX_TICK:
        raise ValueError(f"{field_name} 超出标准 MIDI 时间事件范围。")
    return result


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".mid", ".midi"):
        return "midi"
    if suffix == ".svp":
        return "svp"
    raise ValueError("不支持的乐谱格式；请使用 .mid、.midi 或 .svp 文件。")


def _read_input(path: Any) -> tuple[Path, bytes, str]:
    try:
        source = Path(path)
    except (TypeError, ValueError) as exc:
        raise ValueError("乐谱路径无效。") from exc
    try:
        if not _io_path(source).is_file():
            raise ValueError("乐谱文件不存在或不是普通文件。")
        raw = _io_path(source).read_bytes()
        resolved = source.resolve()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"无法读取乐谱文件：{exc}") from exc
    return resolved, raw, _sha256(raw)


def _load_midi(raw: bytes, path_text: str) -> Any:
    try:
        midi = mido.MidiFile(file=io.BytesIO(raw), charset="utf8", clip=False)
    except (OSError, EOFError, ValueError, UnicodeError, IndexError) as exc:
        raise ValueError(f"无法解析 MIDI 文件：{exc}") from exc
    if midi.type not in (0, 1):
        if midi.type == 2:
            raise ValueError("不支持 MIDI format 2：各轨道使用独立时间线，无法安全计算跨轨秒数。")
        raise ValueError(f"不支持的 MIDI format：{midi.type}。")
    if type(midi.ticks_per_beat) is not int or not 0 < midi.ticks_per_beat < 32768:
        raise ValueError("MIDI ticks-per-quarter-note 超出支持范围。")
    if not midi.tracks or (midi.type == 0 and len(midi.tracks) != 1):
        raise ValueError("MIDI 轨道布局无效。")
    for track_index, track in enumerate(midi.tracks):
        if not track:
            raise ValueError(f"MIDI 轨道 {track_index} 为空。")
        eot_positions = [i for i, message in enumerate(track) if message.type == "end_of_track"]
        if eot_positions != [len(track) - 1]:
            raise ValueError(f"MIDI 轨道 {track_index} 必须以唯一 end_of_track 事件结束。")
        for message in track:
            if type(message.time) is not int or not 0 <= message.time <= _MAX_TICK:
                raise ValueError(f"MIDI 轨道 {track_index} 含无效 delta tick。")
    return midi


def _tempo_timeline(tempo_events: list[dict[str, Any]], ticks_per_beat: int) -> tuple[list[int], list[tuple[float, int]]]:
    positions: list[int] = [0]
    points: list[tuple[float, int]] = [(0.0, _DEFAULT_TEMPO)]
    current_tick = 0
    current_seconds = 0.0
    current_tempo = _DEFAULT_TEMPO
    for row in tempo_events:
        tick = int(row["tick"])
        next_tempo = int(row["tempo_microseconds_per_quarter"])
        if tick < current_tick:
            raise ValueError("MIDI tempo 事件时间顺序无效。")
        current_seconds += (tick - current_tick) * current_tempo / (ticks_per_beat * 1_000_000.0)
        current_tick = tick
        current_tempo = next_tempo
        if positions[-1] == tick:
            points[-1] = (current_seconds, current_tempo)
        else:
            positions.append(tick)
            points.append((current_seconds, current_tempo))
        row["seconds"] = _round_seconds(current_seconds)
    return positions, points


def _seconds_at(tick: int, positions: list[int], points: list[tuple[float, int]], ticks_per_beat: int) -> float:
    point_index = max(0, bisect.bisect_right(positions, tick) - 1)
    start_seconds, tempo = points[point_index]
    start_tick = positions[point_index]
    return start_seconds + (tick - start_tick) * tempo / (ticks_per_beat * 1_000_000.0)


def _parse_midi(raw: bytes, path_text: str) -> _ParsedMidi:
    midi = _load_midi(raw, path_text)
    source_sha = _sha256(raw)
    tracks: list[_TrackRecord] = []
    all_events: list[tuple[int, _EventRecord]] = []
    for track_index, messages in enumerate(midi.tracks):
        absolute_tick = 0
        events: list[_EventRecord] = []
        name = None
        instrument_name = None
        for event_index, message in enumerate(messages):
            absolute_tick += message.time
            event = _EventRecord(event_index=event_index, tick=absolute_tick, message=message)
            events.append(event)
            all_events.append((track_index, event))
            if message.type == "track_name":
                name = message.name
            elif message.type == "instrument_name":
                instrument_name = message.name
        tracks.append(_TrackRecord(
            index=track_index,
            name=name,
            instrument_name=instrument_name,
            messages=list(messages),
            events=events,
            end_tick=absolute_tick,
        ))

    tempo_events: list[dict[str, Any]] = []
    for track_index, event in all_events:
        message = event.message
        if message.type != "set_tempo":
            continue
        tempo = message.tempo
        if type(tempo) is not int or tempo <= 0:
            raise ValueError(f"MIDI 轨道 {track_index} 含无效 tempo 事件。")
        tempo_events.append({
            "track_index": track_index,
            "event_index": event.event_index,
            "tick": event.tick,
            "tempo_microseconds_per_quarter": tempo,
            "bpm": round(60_000_000.0 / tempo, 9),
        })
    tempo_events.sort(key=lambda row: (row["tick"], row["track_index"], row["event_index"]))

    issues: list[dict[str, Any]] = []
    seen_tempos: dict[int, set[int]] = defaultdict(set)
    for row in tempo_events:
        seen_tempos[row["tick"]].add(row["tempo_microseconds_per_quarter"])
    for tick, values in sorted(seen_tempos.items()):
        if len(values) > 1:
            issues.append({
                "code": "conflicting_tempo_at_tick",
                "tick": tick,
                "tempos": sorted(values),
                "message": "同一 tick 上存在互相冲突的 tempo 事件，秒数映射具有歧义。",
            })
    lines, points = _tempo_timeline(tempo_events, midi.ticks_per_beat)

    for track in tracks:
        active: dict[tuple[int, int], deque[_NoteRecord]] = defaultdict(deque)
        ordinal = 0
        for event in track.events:
            message = event.message
            if message.type == "note_on" and message.velocity > 0:
                track.channels.add(message.channel)
                note = _NoteRecord(
                    note_id=f"note-{source_sha[:16]}-t{track.index}-n{ordinal}",
                    ordinal=ordinal,
                    track_index=track.index,
                    channel=message.channel,
                    pitch=message.note,
                    velocity=message.velocity,
                    onset_tick=event.tick,
                    on_event_index=event.event_index,
                )
                key = (message.channel, message.note)
                if active[key]:
                    note.pairing_ambiguous = True
                    for prior in active[key]:
                        prior.pairing_ambiguous = True
                active[key].append(note)
                ordinal += 1
            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                track.channels.add(message.channel)
                key = (message.channel, message.note)
                queue = active[key]
                if not queue:
                    issues.append({
                        "code": "orphan_note_off",
                        "track_index": track.index,
                        "event_index": event.event_index,
                        "tick": event.tick,
                        "channel": message.channel,
                        "pitch": message.note,
                        "message": "音符关闭事件前没有对应的开启事件。",
                    })
                    continue
                if len(queue) > 1:
                    for pending in queue:
                        pending.pairing_ambiguous = True
                note = queue.popleft()
                note.off_event_index = event.event_index
                note.end_tick = event.tick
                if note.end_tick <= note.onset_tick:
                    issues.append({
                        "code": "nonpositive_note_duration",
                        "note_id": note.note_id,
                        "track_index": track.index,
                        "onset_tick": note.onset_tick,
                        "end_tick": note.end_tick,
                        "message": "音符时长不为正，无法安全编辑。",
                    })
                track.notes.append(note)
        for queue in active.values():
            for note in queue:
                issues.append({
                    "code": "unclosed_note",
                    "note_id": note.note_id,
                    "track_index": track.index,
                    "onset_tick": note.onset_tick,
                    "channel": note.channel,
                    "pitch": note.pitch,
                    "message": "音符没有关闭事件，无法安全编辑。",
                })
                track.notes.append(note)
        track.notes.sort(key=lambda note: (note.onset_tick, note.on_event_index))
        for event in track.events:
            message = event.message
            if message.type == "lyrics":
                track.lyrics.append({
                    "event_index": event.event_index,
                    "tick": event.tick,
                    "seconds": _round_seconds(_seconds_at(event.tick, lines, points, midi.ticks_per_beat)),
                    "text": message.text,
                })
        _assign_unique_lyrics(track)

    return _ParsedMidi(
        path_text=path_text,
        raw=raw,
        source_sha256=source_sha,
        midi=midi,
        tracks=tracks,
        tempo_events=tempo_events,
        integrity_issues=issues,
    )


def _assign_unique_lyrics(track: _TrackRecord) -> None:
    """Attach a lyric only when one text event uniquely follows one note onset."""
    by_onset: dict[int, list[_NoteRecord]] = defaultdict(list)
    for note in track.notes:
        by_onset[note.onset_tick].append(note)
    onsets = sorted(by_onset)
    for index, onset in enumerate(onsets):
        following = onsets[index + 1] if index + 1 < len(onsets) else track.end_tick + 1
        events = [row for row in track.lyrics if onset <= row["tick"] < following]
        notes = by_onset[onset]
        if len(notes) == 1 and len(events) == 1:
            notes[0].lyric = events[0]["text"]
            notes[0].lyric_event_index = events[0]["event_index"]

def _note_view(note: _NoteRecord, positions: list[int], points: list[tuple[float, int]], ticks_per_beat: int) -> dict[str, Any]:
    onset_seconds = _seconds_at(note.onset_tick, positions, points, ticks_per_beat)
    if note.end_tick is None:
        duration_seconds = None
        end_seconds = None
    else:
        end_seconds = _seconds_at(note.end_tick, positions, points, ticks_per_beat)
        duration_seconds = end_seconds - onset_seconds
    return {
        "note_id": note.note_id,
        "track_index": note.track_index,
        "channel": note.channel,
        "pitch": note.pitch,
        "velocity": note.velocity,
        "onset_tick": note.onset_tick,
        "duration_tick": note.duration_tick,
        "end_tick": note.end_tick,
        "onset_seconds": _round_seconds(onset_seconds),
        "duration_seconds": _round_seconds(duration_seconds),
        "end_seconds": _round_seconds(end_seconds),
        "lyric": note.lyric,
        "lyric_event_index": note.lyric_event_index,
        "pairing_ambiguous": note.pairing_ambiguous,
    }


def _midi_score(parsed: _ParsedMidi) -> dict[str, Any]:
    midi = parsed.midi
    positions, points = _tempo_timeline(parsed.tempo_events, midi.ticks_per_beat)
    notes = [
        _note_view(note, positions, points, midi.ticks_per_beat)
        for track in parsed.tracks
        for note in track.notes
    ]
    tracks = []
    for track in parsed.tracks:
        tracks.append({
            "track_index": track.index,
            "name": track.name,
            "instrument_name": track.instrument_name,
            "channels": sorted(track.channels),
            "note_count": len(track.notes),
            "note_ids": [note.note_id for note in track.notes],
            "lyric_count": len(track.lyrics),
            "lyric_events": [dict(row) for row in track.lyrics],
            "end_tick": track.end_tick,
        })
    warnings = []
    if any(note.pairing_ambiguous for track in parsed.tracks for note in track.notes):
        warnings.append("存在同轨、同通道、同音高的重叠音符；可读取，但补丁规划会拒绝这种无法唯一配对的情形。")
    if any(issue["code"] == "conflicting_tempo_at_tick" for issue in parsed.integrity_issues):
        warnings.append("同一 tick 存在冲突 tempo；秒数按确定性事件顺序展示，补丁规划会拒绝该文件。")
    return {
        "schema_version": _SCHEMA_VERSION,
        "format": "midi",
        "source_sha256": parsed.source_sha256,
        "path": parsed.path_text,
        "midi_type": midi.type,
        "ticks_per_beat": midi.ticks_per_beat,
        "timing": {
            "tick_unit": "MIDI ticks (PPQ)",
            "ticks_per_beat": midi.ticks_per_beat,
            "seconds_basis": "all set_tempo meta events in MIDI playback order",
            "tempo_events": [dict(row) for row in parsed.tempo_events],
        },
        "tracks": tracks,
        "notes": notes,
        "integrity_issues": [dict(issue) for issue in parsed.integrity_issues],
        "write_supported": True,
        "write_unsupported_reason": None,
        "warnings": warnings,
    }


def _strip_svp_nuls(raw: bytes) -> bytes:
    end = len(raw)
    while end and raw[end - 1] == 0:
        end -= 1
    return raw[:end]


def _svp_position(value: Any, field_name: str) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"SVP {field_name} 必须是有限非负数。")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"SVP {field_name} 必须是有限非负数。")
    return int(number) if number.is_integer() else number


def _svp_timeline(entries: list[dict[str, Any]]) -> tuple[list[int | float], list[tuple[float, float]]]:
    ordered: list[tuple[float, float]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"SVP tempo[{index}] 必须是对象。")
        position = _svp_position(entry.get("position"), f"tempo[{index}].position")
        bpm = _as_number(entry.get("bpm"), f"SVP tempo[{index}].bpm", minimum=0.000001)
        ordered.append((float(position), bpm))
    ordered.sort(key=lambda row: row[0])
    positions: list[int | float] = [0]
    points: list[tuple[float, float]] = [(0.0, 120.0)]
    current_position = 0.0
    current_seconds = 0.0
    current_bpm = 120.0
    for position, bpm in ordered:
        current_seconds += (position - current_position) * 60.0 / (current_bpm * _SVP_UNITS_PER_QUARTER)
        current_position = position
        current_bpm = bpm
        normalized_position = int(position) if position.is_integer() else position
        if positions[-1] == normalized_position:
            points[-1] = (current_seconds, bpm)
        else:
            positions.append(normalized_position)
            points.append((current_seconds, bpm))
    return positions, points


def _svp_seconds_at(position: int | float, positions: list[int | float], points: list[tuple[float, float]]) -> float:
    point_index = max(0, bisect.bisect_right(positions, position) - 1)
    start_seconds, bpm = points[point_index]
    return start_seconds + (position - positions[point_index]) * 60.0 / (bpm * _SVP_UNITS_PER_QUARTER)


def _svp_score(raw: bytes, path_text: str) -> dict[str, Any]:
    source_sha = _sha256(raw)
    payload = _strip_svp_nuls(raw)
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法解析 SVP JSON：{exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("SVP 顶层必须是对象。")
    raw_tracks = data.get("tracks")
    if not isinstance(raw_tracks, list):
        raise ValueError("SVP 缺少 tracks 数组。")
    time_section = data.get("time") or {}
    if not isinstance(time_section, Mapping):
        raise ValueError("SVP time 字段必须是对象。")
    tempo_entries = time_section.get("tempo") or []
    if not isinstance(tempo_entries, list):
        raise ValueError("SVP time.tempo 必须是数组。")
    positions, points = _svp_timeline(tempo_entries)
    tempo_events = []
    for index, entry in enumerate(tempo_entries):
        position = _svp_position(entry["position"], f"tempo[{index}].position")
        tempo_events.append({
            "position": position,
            "bpm": float(entry["bpm"]),
            "seconds": _round_seconds(_svp_seconds_at(position, positions, points)),
        })
    tempo_events.sort(key=lambda row: row["position"])

    output_tracks = []
    output_notes = []
    for track_index, track in enumerate(raw_tracks):
        if not isinstance(track, Mapping):
            raise ValueError(f"SVP tracks[{track_index}] 必须是对象。")
        main_ref = track.get("mainRef") or {}
        main_group = track.get("mainGroup") or {}
        if not isinstance(main_ref, Mapping) or not isinstance(main_group, Mapping):
            raise ValueError(f"SVP tracks[{track_index}] 的 mainRef/mainGroup 无效。")
        raw_notes = main_group.get("notes") or []
        if not isinstance(raw_notes, list):
            raise ValueError(f"SVP tracks[{track_index}].mainGroup.notes 必须是数组。")
        track_note_ids = []
        for note_index, note in enumerate(raw_notes):
            if not isinstance(note, Mapping):
                raise ValueError(f"SVP tracks[{track_index}] 音符 {note_index} 必须是对象。")
            onset = _svp_position(note.get("onset"), f"track[{track_index}].note[{note_index}].onset")
            duration = _svp_position(note.get("duration"), f"track[{track_index}].note[{note_index}].duration")
            if duration <= 0:
                raise ValueError(f"SVP tracks[{track_index}] 音符 {note_index} 时长必须为正。")
            pitch = _as_number(note.get("pitch"), f"SVP track[{track_index}].note[{note_index}].pitch")
            if not 0 <= pitch <= 127:
                raise ValueError(f"SVP tracks[{track_index}] 音符 {note_index} 的 pitch 超出 0..127。")
            normalized_pitch: int | float = int(pitch) if pitch.is_integer() else pitch
            end = onset + duration
            onset_seconds = _svp_seconds_at(onset, positions, points)
            end_seconds = _svp_seconds_at(end, positions, points)
            note_id = f"note-{source_sha[:16]}-t{track_index}-n{note_index}"
            lyric = note.get("lyrics")
            if lyric is not None and not isinstance(lyric, str):
                raise ValueError(f"SVP tracks[{track_index}] 音符 {note_index} 的歌词必须是字符串或 null。")
            row = {
                "note_id": note_id,
                "track_index": track_index,
                "channel": None,
                "pitch": normalized_pitch,
                "velocity": None,
                "onset_tick": onset,
                "duration_tick": duration,
                "end_tick": end,
                "onset_seconds": _round_seconds(onset_seconds),
                "duration_seconds": _round_seconds(end_seconds - onset_seconds),
                "end_seconds": _round_seconds(end_seconds),
                "lyric": lyric,
                "lyric_event_index": None,
                "pairing_ambiguous": False,
            }
            output_notes.append(row)
            track_note_ids.append(note_id)
        output_tracks.append({
            "track_index": track_index,
            "name": track.get("name") if isinstance(track.get("name"), str) else None,
            "instrument_name": None,
            "instrumental": main_ref.get("isInstrumental") is True,
            "channels": [],
            "note_count": len(raw_notes),
            "note_ids": track_note_ids,
            "lyric_count": sum(1 for note in raw_notes if isinstance(note.get("lyrics"), str) and note.get("lyrics")),
            "lyric_events": [
                {"note_index": index, "text": note.get("lyrics")}
                for index, note in enumerate(raw_notes)
                if isinstance(note.get("lyrics"), str) and note.get("lyrics")
            ],
            "end_tick": max((row["end_tick"] for row in output_notes if row["track_index"] == track_index), default=0),
        })

    return {
        "schema_version": _SCHEMA_VERSION,
        "format": "svp",
        "source_sha256": source_sha,
        "path": path_text,
        "midi_type": None,
        "ticks_per_beat": _SVP_UNITS_PER_QUARTER,
        "timing": {
            "tick_unit": "SVP positions (705600000 per quarter note)",
            "ticks_per_beat": _SVP_UNITS_PER_QUARTER,
            "seconds_basis": "SVP time.tempo positions",
            "tempo_events": tempo_events,
        },
        "tracks": output_tracks,
        "notes": output_notes,
        "integrity_issues": [],
        "write_supported": False,
        "write_unsupported_reason": "Score worker 不写 SVP：SVP 项目还包含声库、参数曲线和编辑器专用字段，通用 MIDI 补丁无法保持这些不变量。",
        "warnings": ["SVP 读取仅展开各轨 mainGroup.notes；其他分组和编辑器专用字段作为原项目内容保留，但不在本视图展开。"],
    }


def read_score(path) -> dict:
    """Read a MIDI or SVP score into JSON-serializable track and note evidence."""
    source, raw, source_sha = _read_input(path)
    kind = _file_kind(source)
    if kind == "midi":
        parsed = _parse_midi(raw, str(source))
        if parsed.source_sha256 != source_sha:
            raise ValueError("乐谱文件在读取期间发生变化，请重试。")
        return _midi_score(parsed)
    return _svp_score(raw, str(source))


def review_score(path) -> dict:
    """Return deterministic review suggestions; hints are never proof of errors."""
    score = read_score(path)
    hints: list[dict[str, Any]] = []
    grouped: dict[tuple[int, Any], list[dict[str, Any]]] = defaultdict(list)
    for note in score["notes"]:
        grouped[(note["track_index"], note["channel"])].append(note)
    for (track_index, channel), notes in sorted(grouped.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        ordered = sorted(notes, key=lambda note: (note["onset_tick"], note["pitch"], note["note_id"]))
        previous = None
        for note in ordered:
            note_ids = [note["note_id"]]
            if note.get("pairing_ambiguous"):
                hints.append({
                    "code": "ambiguous_same_key_overlap",
                    "severity": "warning",
                    "note_ids": note_ids,
                    "track_index": track_index,
                    "channel": channel,
                    "reason": "同轨同通道同音高音符发生重叠，事件配对无法唯一确定；请先手动拆分或关闭重复音符。",
                })
            duration_seconds = note.get("duration_seconds")
            if duration_seconds is not None and duration_seconds < 0.05:
                hints.append({
                    "code": "very_short_duration",
                    "severity": "info",
                    "note_ids": note_ids,
                    "track_index": track_index,
                    "channel": channel,
                    "reason": "该音符短于 50 ms，可能是装饰音、连奏残留或误检；请结合试听核对。",
                    "observed_seconds": duration_seconds,
                })
            if previous is not None and abs(int(note["pitch"]) - int(previous["pitch"])) >= 12:
                hints.append({
                    "code": "large_pitch_leap",
                    "severity": "info",
                    "note_ids": [previous["note_id"], note["note_id"]],
                    "track_index": track_index,
                    "channel": channel,
                    "reason": "相邻音符跨度至少一个八度，可能是正常跳进或八度误识别；请试听确认。",
                    "pitch_delta": int(note["pitch"]) - int(previous["pitch"]),
                })
            previous = note

    unique: dict[tuple, dict[str, Any]] = {}
    for hint in hints:
        key = (hint["code"], tuple(hint["note_ids"]))
        unique.setdefault(key, hint)
    ordered_hints = [unique[key] for key in sorted(unique, key=lambda row: (row[0], row[1]))]
    for index, hint in enumerate(ordered_hints):
        hint["hint_id"] = f"review-{score['source_sha256'][:16]}-{index}"
    return {
        "schema_version": _SCHEMA_VERSION,
        "format": score["format"],
        "source_sha256": score["source_sha256"],
        "track_count": len(score["tracks"]),
        "note_count": len(score["notes"]),
        "hints": ordered_hints,
        "disclaimer": "以下仅为确定性的复核提示，不代表已经证实音符错误；最终判断应结合试听、原谱和业务上下文。",
    }

def _notes_by_id(parsed: _ParsedMidi) -> dict[str, _NoteRecord]:
    return {note.note_id: note for track in parsed.tracks for note in track.notes}


def _strict_edit_int(value: Any, field_name: str, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"编辑字段 {field_name} 必须是整数；布尔值不算整数。")
    if value < minimum or (maximum is not None and value > maximum):
        upper = "" if maximum is None else f"..{maximum}"
        raise ValueError(f"编辑字段 {field_name} 必须是 {minimum}{upper} 范围内的整数。")
    return value


def _normalise_edits(edits: Any, notes: dict[str, _NoteRecord]) -> list[dict[str, Any]]:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空数组。")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise ValueError(f"edits[{index}] 必须是对象。")
        keys = set(edit)
        unsupported = keys - _EDIT_KEYS
        if unsupported:
            raise ValueError(f"edits[{index}] 含不支持字段：{', '.join(sorted(map(str, unsupported)))}。")
        if "note_id" not in keys:
            raise ValueError(f"edits[{index}] 缺少 note_id。")
        note_id = edit.get("note_id")
        if not isinstance(note_id, str) or not note_id:
            raise ValueError(f"edits[{index}].note_id 必须是非空字符串。")
        if note_id in seen:
            raise ValueError(f"重复的 note_id：{note_id}。")
        seen.add(note_id)
        note = notes.get(note_id)
        if note is None:
            raise ValueError(f"未知 note_id：{note_id}。")
        if note.end_tick is None or note.off_event_index is None:
            raise ValueError(f"音符 {note_id} 没有可唯一关闭的事件，无法编辑。")
        changes = keys - {"note_id"}
        if not changes:
            raise ValueError(f"音符 {note_id} 没有提供任何编辑字段。")
        row: dict[str, Any] = {"note_id": note_id}
        if "pitch" in changes:
            pitch = _strict_edit_int(edit["pitch"], "pitch", minimum=0, maximum=127)
            if pitch == note.pitch:
                raise ValueError(f"音符 {note_id} 的 pitch 没有实际变化。")
            row["pitch"] = pitch
        if "onset_tick" in changes:
            onset = _strict_edit_int(edit["onset_tick"], "onset_tick", minimum=0, maximum=_MAX_TICK)
            if onset == note.onset_tick:
                raise ValueError(f"音符 {note_id} 的 onset_tick 没有实际变化。")
            row["onset_tick"] = onset
        if "duration_tick" in changes:
            duration = _strict_edit_int(edit["duration_tick"], "duration_tick", minimum=1, maximum=_MAX_TICK)
            if duration == note.duration_tick:
                raise ValueError(f"音符 {note_id} 的 duration_tick 没有实际变化。")
            row["duration_tick"] = duration
        new_onset = row.get("onset_tick", note.onset_tick)
        new_duration = row.get("duration_tick", note.duration_tick)
        if new_onset + new_duration > _MAX_TICK:
            raise ValueError(f"音符 {note_id} 的结束位置超出标准 MIDI 时间事件范围。")
        rows.append(row)
    rows.sort(key=lambda row: row["note_id"])
    return rows


def _event_fingerprint(message: Any) -> str:
    payload = dict(message.dict())
    payload.pop("time", None)
    try:
        return _canonical_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MIDI 事件无法稳定序列化：{exc}") from exc


def _build_patch(parsed: _ParsedMidi, edits: Any) -> tuple[dict[str, Any], bytes]:
    if parsed.integrity_issues:
        first = parsed.integrity_issues[0]
        raise ValueError(f"MIDI 结构不安全，不能规划补丁：{first['message']}")
    notes = _notes_by_id(parsed)
    if any(note.pairing_ambiguous for note in notes.values()):
        raise ValueError("MIDI 存在同轨、同通道、同音高的重叠音符，事件配对有歧义；请先手动拆分或关闭重复音符。")
    for note in notes.values():
        if note.end_tick is not None and note.end_tick <= note.onset_tick:
            raise ValueError(f"音符 {note.note_id} 时长不为正，无法规划补丁。")
    normalized = _normalise_edits(edits, notes)

    new_values: dict[str, dict[str, Any]] = {}
    for edit in normalized:
        note = notes[edit["note_id"]]
        new_values[note.note_id] = {
            "pitch": edit.get("pitch", note.pitch),
            "onset_tick": edit.get("onset_tick", note.onset_tick),
            "duration_tick": edit.get("duration_tick", note.duration_tick),
            "end_tick": edit.get("onset_tick", note.onset_tick) + edit.get("duration_tick", note.duration_tick),
            "channel": note.channel,
        }

    interval_groups: dict[tuple[int, int, int], list[tuple[int, int, str]]] = defaultdict(list)
    for note in notes.values():
        values = new_values.get(note.note_id)
        if values is None:
            pitch = note.pitch
            onset = note.onset_tick
            duration = note.duration_tick
            end = note.end_tick
        else:
            pitch = values["pitch"]
            onset = values["onset_tick"]
            duration = values["duration_tick"]
            end = values["end_tick"]
        if duration is None or end is None:
            raise ValueError(f"音符 {note.note_id} 缺少完整时长，无法规划补丁。")
        interval_groups[(note.track_index, note.channel, pitch)].append((onset, end, note.note_id))
    for key, intervals in interval_groups.items():
        intervals.sort()
        previous_end = -1
        previous_id = None
        for onset, end, note_id in intervals:
            if previous_id is not None and onset < previous_end:
                raise ValueError(
                    f"补丁会造成轨道 {key[0]}、通道 {key[1]}、音高 {key[2]} 的音符重叠，"
                    "事件配对将不再唯一。"
                )
            previous_end = end
            previous_id = note_id

    event_updates: dict[tuple[int, int], tuple[int, Any]] = {}
    for edit in normalized:
        note = notes[edit["note_id"]]
        track = parsed.tracks[note.track_index]
        values = new_values[note.note_id]
        on_event = track.events[note.on_event_index]
        off_event = track.events[note.off_event_index]
        on_message = on_event.message
        off_message = off_event.message
        if "pitch" in edit:
            on_message = on_message.copy(note=values["pitch"])
            off_message = off_message.copy(note=values["pitch"])
        event_updates[(note.track_index, note.on_event_index)] = (values["onset_tick"], on_message)
        event_updates[(note.track_index, note.off_event_index)] = (values["end_tick"], off_message)

    new_midi = mido.MidiFile(
        type=parsed.midi.type,
        ticks_per_beat=parsed.midi.ticks_per_beat,
        charset="utf8",
    )
    expected_events: list[list[tuple[int, str]]] = []
    reordered_tracks: list[int] = []
    eot_changes: list[dict[str, int]] = []
    for track in parsed.tracks:
        eot_event_index = len(track.messages) - 1
        items = []
        original_order = []
        for event in track.events[:-1]:
            tick, message = event_updates.get((track.index, event.event_index), (event.tick, event.message))
            items.append((tick, event.event_index, message))
            original_order.append(event.event_index)
        items.sort(key=lambda row: (row[0], row[1]))
        if [row[1] for row in items] != original_order:
            reordered_tracks.append(track.index)
        last_tick = 0
        new_track = mido.MidiTrack()
        expected = []
        for tick, event_index, message in items:
            delta = tick - last_tick
            if delta < 0 or delta > _MAX_TICK:
                raise ValueError(f"轨道 {track.index} 的补丁产生无效 delta tick。")
            saved_message = message.copy(time=delta)
            new_track.append(saved_message)
            expected.append((tick, _event_fingerprint(saved_message)))
            last_tick = tick
        eot_tick = max(track.end_tick, last_tick)
        eot_delta = eot_tick - last_tick
        if eot_delta < 0 or eot_delta > _MAX_TICK:
            raise ValueError(f"轨道 {track.index} 的结束事件时间无效。")
        eot_message = track.messages[eot_event_index].copy(time=eot_delta)
        new_track.append(eot_message)
        expected.append((eot_tick, _event_fingerprint(eot_message)))
        expected_events.append(expected)
        if eot_tick != track.end_tick:
            eot_changes.append({
                "track_index": track.index,
                "before_tick": track.end_tick,
                "after_tick": eot_tick,
            })
        new_midi.tracks.append(new_track)

    buffer = io.BytesIO()
    try:
        new_midi.save(file=buffer)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"生成候选 MIDI 失败：{exc}") from exc
    candidate_bytes = buffer.getvalue()
    candidate_parsed = _parse_midi(candidate_bytes, "<candidate>")
    if candidate_parsed.integrity_issues:
        raise ValueError("候选 MIDI 未通过结构验证，已拒绝生成计划。")
    if any(note.pairing_ambiguous for note in _notes_by_id(candidate_parsed).values()):
        raise ValueError("候选 MIDI 产生音符配对歧义，已拒绝生成计划。")
    if len(candidate_parsed.tracks) != len(parsed.tracks):
        raise ValueError("候选 MIDI 丢失轨道，已拒绝生成计划。")
    for track_index, candidate_track in enumerate(candidate_parsed.tracks):
        actual = [(event.tick, _event_fingerprint(event.message)) for event in candidate_track.events]
        if actual != expected_events[track_index]:
            raise ValueError(f"候选 MIDI 轨道 {track_index} 未无损保留全部事件，已拒绝生成计划。")

    expected_notes = []
    actual_notes = []
    for note in notes.values():
        values = new_values.get(note.note_id)
        if values is None:
            expected_notes.append((note.track_index, note.channel, note.pitch, note.onset_tick, note.end_tick))
        else:
            expected_notes.append((
                note.track_index,
                note.channel,
                values["pitch"],
                values["onset_tick"],
                values["end_tick"],
            ))
    for note in _notes_by_id(candidate_parsed).values():
        actual_notes.append((note.track_index, note.channel, note.pitch, note.onset_tick, note.end_tick))
    if sorted(expected_notes) != sorted(actual_notes):
        raise ValueError("候选 MIDI 的音符语义与计划不一致，已拒绝生成计划。")

    operations = []
    computed_changes = []
    for edit in normalized:
        note = notes[edit["note_id"]]
        before = {
            "pitch": note.pitch,
            "onset_tick": note.onset_tick,
            "duration_tick": note.duration_tick,
            "end_tick": note.end_tick,
        }
        after = dict(before, **{key: value for key, value in edit.items() if key != "note_id"})
        for field_name in _EDIT_FIELDS:
            if field_name in edit:
                operations.append({
                    "op": "replace",
                    "path": f"/notes/{note.note_id}/{field_name}",
                    "note_id": note.note_id,
                    "field": field_name,
                    "before": before[field_name],
                    "after": edit[field_name],
                })
        if "onset_tick" in edit or "duration_tick" in edit:
            computed_changes.append({
                "note_id": note.note_id,
                "before_end_tick": note.end_tick,
                "after_end_tick": new_values[note.note_id]["end_tick"],
            })

    warnings = [
        "本计划只修改列出的音符事件；tempo、轨道、通道、力度、歌词和其余 meta/事件保持原值。",
    ]
    if any("onset_tick" in edit or "duration_tick" in edit for edit in normalized):
        warnings.append("时间编辑不会移动歌词或其他 meta 事件；读取结果中的歌词关联提示可能随音符重排而变化。")
    if eot_changes:
        warnings.append("为容纳延长的音符，end_of_track 的绝对位置随之顺延；其 meta 内容不变。")
    structural_changes = {
        "end_of_track": eot_changes,
        "reordered_tracks": reordered_tracks,
        "computed_note_changes": computed_changes,
    }
    plan_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _PLAN_KIND,
        "source_sha256": parsed.source_sha256,
        "format": "midi",
        "midi_type": parsed.midi.type,
        "ticks_per_beat": parsed.midi.ticks_per_beat,
        "edits": normalized,
        "diff": {
            "schema": _DIFF_SCHEMA,
            "operations": operations,
            "computed_changes": computed_changes,
        },
        "expected_candidate_sha256": _sha256(candidate_bytes),
        "structural_changes": structural_changes,
        "warnings": warnings,
    }
    plan = dict(plan_payload)
    plan["plan_sha256"] = _canonical_digest(plan_payload)
    return plan, candidate_bytes


def _normalise_sha(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError(f"{field_name} 必须是 64 位十六进制 SHA-256。")
    return value.lower()


def plan_patch(path, expected_sha256, edits) -> dict:
    """Plan strict note pitch/timing edits without changing the source file."""
    source, raw, source_sha = _read_input(path)
    if _file_kind(source) != "midi":
        raise ValueError("Score worker 只支持写入标准 MIDI；SVP 写入不受支持。")
    expected = _normalise_sha(expected_sha256, "expected_sha256")
    if source_sha != expected:
        raise ValueError("源文件 SHA-256 已变化；请重新 read_score 后再规划补丁。")
    parsed = _parse_midi(raw, str(source))
    if parsed.source_sha256 != source_sha:
        raise ValueError("源文件在规划期间发生变化，请重试。")
    plan, _candidate_bytes = _build_patch(parsed, edits)
    return plan


def _publish_exclusive(path: Path, data: bytes) -> None:
    """Publish complete bytes without replacing an existing target.

    Hard links provide atomic no-overwrite creation on both supported
    platforms.  Some Windows managed sandboxes reject hard links, so Windows
    falls back to os.rename, whose Win32 destination-exists behavior rejects
    replacement.  POSIX never uses rename here because it can replace a file.
    """
    path = _io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(str(temporary), str(path))
            return
        except FileExistsError:
            return
        except OSError as link_error:
            if os.name != "nt":
                raise ValueError(f"无法原子发布候选文件：{link_error}") from link_error
            # Check immediately before rename.  The caller still performs the
            # full-byte verification after publication.
            try:
                if os.path.lexists(path):
                    return
            except (AttributeError, OSError) as lexists_error:
                raise ValueError(f"无法安全检查发布目标：{lexists_error}") from lexists_error
        try:
            os.rename(str(temporary), str(path))
        except FileExistsError:
            return
        except OSError as rename_error:
            raise ValueError(f"无法通过 Windows 原子重命名发布候选文件：{rename_error}") from rename_error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _existing_file(path: Path, label: str) -> bytes | None:
    path = _io_path(path)
    if path.is_symlink():
        raise ValueError(f"{label} 是符号链接，拒绝信任可能指向别处的文件。")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{label} 已存在但不是普通文件。")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"无法校验已有 {label}：{exc}") from exc


def apply_patch(path, plan, output_directory) -> dict:
    """Apply a verified plan to an independently named candidate and receipt."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan 必须是对象。")
    supplied_plan = dict(plan)
    if set(supplied_plan) != _PLAN_KEYS:
        missing = sorted(_PLAN_KEYS - set(supplied_plan))
        extra = sorted(set(supplied_plan) - _PLAN_KEYS)
        raise ValueError(f"plan 字段不完整或不支持；缺少={missing}，额外={extra}。")
    if (type(supplied_plan.get("schema_version")) is not int
            or supplied_plan.get("schema_version") != _SCHEMA_VERSION
            or supplied_plan.get("kind") != _PLAN_KIND):
        raise ValueError("plan schema 或 kind 无效。")
    plan_sha = _normalise_sha(supplied_plan.get("plan_sha256"), "plan_sha256")
    plan_payload = {key: value for key, value in supplied_plan.items() if key != "plan_sha256"}
    try:
        actual_plan_sha = _canonical_digest(plan_payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"plan 不是完整、有限的 JSON 对象：{exc}") from exc
    if plan_sha != actual_plan_sha:
        raise ValueError("plan_sha256 与完整计划内容不匹配；计划已损坏或被篡改。")
    source_sha = _normalise_sha(supplied_plan.get("source_sha256"), "source_sha256")
    _normalise_sha(supplied_plan.get("expected_candidate_sha256"), "expected_candidate_sha256")

    source, raw, current_sha = _read_input(path)
    if _file_kind(source) != "midi":
        raise ValueError("Score worker 只支持写入标准 MIDI；SVP 写入不受支持。")
    if current_sha != source_sha:
        raise ValueError("当前源文件 SHA-256 与计划不一致；请重新 read_score 和 plan_patch。")
    parsed = _parse_midi(raw, str(source))
    regenerated, candidate_bytes = _build_patch(parsed, supplied_plan["edits"])
    if regenerated != supplied_plan:
        raise ValueError("计划与当前源文件重新计算的结果不一致；请拒绝该计划。")

    if output_directory is None or isinstance(output_directory, bool):
        raise ValueError("output_directory 必须是目录路径。")
    try:
        if isinstance(output_directory, str) and not output_directory:
            raise ValueError("output_directory 不能为空。")
        output_root = Path(output_directory)
        if _io_path(output_root).exists() and not _io_path(output_root).is_dir():
            raise ValueError("output_directory 已存在但不是目录。")
        _io_path(output_root).mkdir(parents=True, exist_ok=True)
        output_root = output_root.resolve()
    except ValueError:
        raise
    except (TypeError, OSError) as exc:
        raise ValueError(f"output_directory 无效：{exc}") from exc

    candidate_path = (output_root / f"vwa-score-{plan_sha}.mid").resolve()
    receipt_path = (output_root / f"vwa-score-{plan_sha}.mid.receipt.json").resolve()
    if candidate_path == source or receipt_path == source:
        raise ValueError("候选路径与源文件冲突；拒绝覆盖源文件。")
    candidate_sha = _sha256(candidate_bytes)
    if candidate_sha != supplied_plan["expected_candidate_sha256"]:
        raise ValueError("重新生成的候选 SHA-256 与计划不一致。")

    receipt_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _RECEIPT_KIND,
        "plan_sha256": plan_sha,
        "source_sha256": source_sha,
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_sha,
        "receipt_path": str(receipt_path),
        "diff": supplied_plan["diff"],
        "structural_changes": supplied_plan["structural_changes"],
        "warnings": supplied_plan["warnings"],
    }
    receipt = dict(receipt_payload)
    receipt["receipt_sha256"] = _canonical_digest(receipt_payload)
    receipt_bytes = _canonical_json_bytes(receipt, pretty=True)

    existing_candidate = _existing_file(candidate_path, "候选文件")
    if existing_candidate is not None and existing_candidate != candidate_bytes:
        raise ValueError("候选路径已有不同内容，拒绝覆盖或信任该碰撞。")
    existing_receipt = _existing_file(receipt_path, "receipt")
    if existing_receipt is not None:
        try:
            existing_receipt_value = json.loads(existing_receipt.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"已有 receipt 不是有效 UTF-8 JSON：{exc}") from exc
        if existing_receipt_value != receipt:
            raise ValueError("已有 receipt 与当前计划不一致；拒绝覆盖或信任该碰撞。")

    _, _, before_publish_sha = _read_input(source)
    if before_publish_sha != source_sha:
        raise ValueError("源文件在应用前发生变化；未发布候选。")
    if existing_candidate is None:
        _publish_exclusive(candidate_path, candidate_bytes)
    if existing_receipt is None:
        _publish_exclusive(receipt_path, receipt_bytes)
    published_candidate = _existing_file(candidate_path, "候选文件")
    published_receipt = _existing_file(receipt_path, "receipt")
    if published_candidate != candidate_bytes:
        raise ValueError("候选发布后校验失败；文件中内容不匹配。")
    if published_receipt != receipt_bytes:
        raise ValueError("receipt 发布后校验失败；文件中内容不匹配。")
    _, _, after_publish_sha = _read_input(source)
    if after_publish_sha != source_sha:
        raise ValueError("源文件在应用期间发生变化；拒绝返回不可再验证的 receipt。")
    return receipt
