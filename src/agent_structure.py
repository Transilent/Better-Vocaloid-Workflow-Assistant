"""Safe MIDI structure patches for note deletion and insertion.

This module is independent of MCP, Qt, models and user task state. A plan is a
pure, content-addressed description. Applying it rereads the source,
regenerates the candidate bytes, and rejects any plan that no longer matches.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import io
import json
from pathlib import Path
from typing import Any

import mido

import agent_score as _score
from common import native_path


_SCHEMA_VERSION = 1
_PLAN_KIND = "vwa-structure-patch-plan"
_RECEIPT_KIND = "vwa-structure-patch-receipt"
_DIFF_SCHEMA = "vwa-structure-diff-v1"
_MAX_EDITS = 1_024
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
_MAX_LYRIC_BYTES = 4_096
_MAX_TICK = _score._MAX_TICK

_DELETE_KEYS = {"operation", "note_id", "lyric_policy"}
_INSERT_REQUIRED_KEYS = {
    "operation",
    "track_index",
    "channel",
    "pitch",
    "onset_tick",
    "duration_tick",
    "velocity",
}
_INSERT_KEYS = _INSERT_REQUIRED_KEYS | {"lyric"}

_PLAN_KEYS = {
    "schema_version",
    "kind",
    "source_sha256",
    "format",
    "midi_type",
    "ticks_per_beat",
    "track_count",
    "edits",
    "diff",
    "expected_candidate_sha256",
    "structural_changes",
    "warnings",
    "inference_performed",
    "plan_sha256",
}

def _io_path(path: Any) -> Path:
    """Apply extended Windows path syntax only at filesystem boundaries."""
    return Path(native_path(path))


# These helpers are version-coupled to agent_score in the same source snapshot:
# its note IDs, event map, canonical digest, and exclusive publication are the
# fixed source of truth for plans produced here.
_publish_exclusive = _score._publish_exclusive
_existing_file = _score._existing_file
_canonical_digest = _score._canonical_digest
_canonical_json_bytes = _score._canonical_json_bytes
_normalise_sha = _score._normalise_sha
_sha256 = _score._sha256
_event_fingerprint = _score._event_fingerprint


@dataclass(frozen=True)
class _Insertion:
    edit_index: int
    track_index: int
    channel: int
    pitch: int
    onset_tick: int
    duration_tick: int
    velocity: int
    lyric: str | None

    @property
    def end_tick(self) -> int:
        return self.onset_tick + self.duration_tick

    @property
    def insert_id(self) -> str:
        return f"insert-{self.edit_index:04d}"

    def note_key(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.track_index,
            self.channel,
            self.pitch,
            self.onset_tick,
            self.end_tick,
            self.velocity,
        )

    def public_edit(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "operation": "insert",
            "track_index": self.track_index,
            "channel": self.channel,
            "pitch": self.pitch,
            "onset_tick": self.onset_tick,
            "duration_tick": self.duration_tick,
            "velocity": self.velocity,
        }
        if self.lyric is not None:
            row["lyric"] = self.lyric
        return row


def _strict_int(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} 必须是整数；布尔值不算整数。")
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 范围内的整数。")
    return value


def _strict_text(value: Any, field_name: str, *, maximum_bytes: int | None = None) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} 必须是非空字符串。")
    if "\x00" in value:
        raise ValueError(f"{field_name} 不能包含 NUL。")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{field_name} 不是有效 UTF-8 文本。") from exc
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise ValueError(f"{field_name} 超过 {maximum_bytes} 字节上限。")
    return value


def _read_source(path: Any) -> tuple[Path, bytes, str, Any]:
    source, raw, source_sha = _score._read_input(path)
    if _score._file_kind(source) != "midi":
        raise ValueError("Structure worker 只支持标准 MIDI；SVP 写入不受支持。")
    if len(raw) > _MAX_SOURCE_BYTES:
        raise ValueError(f"源 MIDI 超过 {_MAX_SOURCE_BYTES} 字节安全上限。")
    parsed = _score._parse_midi(raw, str(source))
    return source, raw, source_sha, parsed


def _safe_notes(parsed: Any) -> dict[str, Any]:
    if parsed.integrity_issues:
        first = parsed.integrity_issues[0]
        raise ValueError(f"MIDI 结构不安全，不能规划结构补丁：{first['message']}")
    notes = _score._notes_by_id(parsed)
    if any(note.pairing_ambiguous for note in notes.values()):
        raise ValueError("MIDI 存在同轨、同通道、同音高重叠，音符关闭事件配对有歧义。")
    for note in notes.values():
        if note.end_tick is None or note.off_event_index is None:
            raise ValueError(f"音符 {note.note_id} 没有唯一关闭事件。")
        if note.end_tick <= note.onset_tick:
            raise ValueError(f"音符 {note.note_id} 时长不为正。")
    return notes


def _lyrics_near_onset(track: Any, onset_tick: int) -> list[dict[str, Any]]:
    later = sorted({note.onset_tick for note in track.notes if note.onset_tick > onset_tick})
    following = later[0] if later else track.end_tick + 1
    return [
        row for row in track.lyrics
        if onset_tick <= int(row["tick"]) < following
    ]


def _normalise_edits(
    parsed: Any,
    edits: Any,
) -> tuple[list[dict[str, Any]], list[Any], list[_Insertion]]:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits 必须是非空数组。")
    if len(edits) > _MAX_EDITS:
        raise ValueError(f"edits 最多允许 {_MAX_EDITS} 项。")
    notes = _safe_notes(parsed)
    seen_deletes: set[str] = set()
    seen_owned_lyrics: set[tuple[int, int]] = set()
    public_edits: list[dict[str, Any]] = []
    deletions: list[Any] = []
    insertions: list[_Insertion] = []

    for edit_index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise ValueError(f"edits[{edit_index}] 必须是对象。")
        operation = edit.get("operation")
        if operation == "delete":
            keys = set(edit)
            if keys != _DELETE_KEYS:
                missing = sorted(_DELETE_KEYS - keys)
                extra = sorted(keys - _DELETE_KEYS, key=str)
                raise ValueError(
                    f"edits[{edit_index}] delete 字段不完整或不支持；缺少={missing}，额外={extra}。"
                )
            note_id = _strict_text(
                edit.get("note_id"),
                f"edits[{edit_index}].note_id",
                maximum_bytes=512,
            )
            if note_id in seen_deletes:
                raise ValueError(f"重复删除 note_id：{note_id}。")
            seen_deletes.add(note_id)
            note = notes.get(note_id)
            if note is None:
                raise ValueError(f"未知或过期 note_id：{note_id}。")
            lyric_policy = edit.get("lyric_policy")
            if type(lyric_policy) is not str or lyric_policy not in {"preserve", "delete_owned"}:
                raise ValueError(
                    f"edits[{edit_index}].lyric_policy 只允许 preserve 或 delete_owned。"
                )
            if lyric_policy == "delete_owned" and note.lyric_event_index is None:
                if _lyrics_near_onset(parsed.tracks[note.track_index], note.onset_tick):
                    raise ValueError(
                        f"音符 {note_id} 的歌词归属不唯一，拒绝 delete_owned；"
                        "请改用 preserve 或人工处理。"
                    )
            if note.lyric_event_index is not None:
                owned_key = (note.track_index, note.lyric_event_index)
                if lyric_policy == "delete_owned" and owned_key in seen_owned_lyrics:
                    raise ValueError("同一歌词事件被多个删除操作声明为独占；拒绝不安全归属。")
                if lyric_policy == "delete_owned":
                    seen_owned_lyrics.add(owned_key)
            public_edits.append({
                "operation": "delete",
                "note_id": note_id,
                "lyric_policy": lyric_policy,
            })
            deletions.append(note)
            continue

        if operation == "insert":
            keys = set(edit)
            if not _INSERT_REQUIRED_KEYS.issubset(keys) or not keys.issubset(_INSERT_KEYS):
                missing = sorted(_INSERT_REQUIRED_KEYS - keys)
                extra = sorted(keys - _INSERT_KEYS, key=str)
                raise ValueError(
                    f"edits[{edit_index}] insert 字段不完整或不支持；缺少={missing}，额外={extra}。"
                )
            track_index = _strict_int(
                edit.get("track_index"),
                f"edits[{edit_index}].track_index",
                minimum=0,
                maximum=len(parsed.tracks) - 1,
            )
            channel = _strict_int(
                edit.get("channel"),
                f"edits[{edit_index}].channel",
                minimum=0,
                maximum=15,
            )
            pitch = _strict_int(
                edit.get("pitch"),
                f"edits[{edit_index}].pitch",
                minimum=0,
                maximum=127,
            )
            onset_tick = _strict_int(
                edit.get("onset_tick"),
                f"edits[{edit_index}].onset_tick",
                minimum=0,
                maximum=_MAX_TICK - 1,
            )
            duration_tick = _strict_int(
                edit.get("duration_tick"),
                f"edits[{edit_index}].duration_tick",
                minimum=1,
                maximum=_MAX_TICK,
            )
            if onset_tick + duration_tick > _MAX_TICK:
                raise ValueError(f"edits[{edit_index}] 插入音符结束位置超出 MIDI tick 范围。")
            velocity = _strict_int(
                edit.get("velocity"),
                f"edits[{edit_index}].velocity",
                minimum=1,
                maximum=127,
            )
            lyric = None
            if "lyric" in edit:
                lyric = _strict_text(
                    edit.get("lyric"),
                    f"edits[{edit_index}].lyric",
                    maximum_bytes=_MAX_LYRIC_BYTES,
                )
            insertion = _Insertion(
                edit_index=edit_index,
                track_index=track_index,
                channel=channel,
                pitch=pitch,
                onset_tick=onset_tick,
                duration_tick=duration_tick,
                velocity=velocity,
                lyric=lyric,
            )
            public_edits.append(insertion.public_edit())
            insertions.append(insertion)
            continue

        raise ValueError(f"edits[{edit_index}].operation 只允许 delete 或 insert。")

    if len(public_edits) != len(edits):
        raise AssertionError("内部编辑规范化丢失操作")
    return public_edits, deletions, insertions


def _note_key(note: Any) -> tuple[int, int, int, int, int, int]:
    return (
        int(note.track_index),
        int(note.channel),
        int(note.pitch),
        int(note.onset_tick),
        int(note.end_tick),
        int(note.velocity),
    )


def _deleted_lyric_events(
    deletions: list[Any],
    policies: dict[str, str],
) -> set[tuple[int, int]]:
    removed: set[tuple[int, int]] = set()
    for note in deletions:
        if policies.get(note.note_id) == "delete_owned" and note.lyric_event_index is not None:
            key = (note.track_index, note.lyric_event_index)
            if key in removed:
                raise ValueError("同一歌词事件被重复删除；拒绝不安全归属。")
            removed.add(key)
    return removed


def _validate_final_state(
    parsed: Any,
    notes: dict[str, Any],
    deletions: list[Any],
    insertions: list[_Insertion],
    policies: dict[str, str],
) -> tuple[list[Any], set[tuple[int, int]]]:
    deleted_ids = {note.note_id for note in deletions}
    kept_notes = [note for note_id, note in notes.items() if note_id not in deleted_ids]
    removed_events: set[tuple[int, int]] = set()
    for note in deletions:
        removed_events.add((note.track_index, note.on_event_index))
        removed_events.add((note.track_index, note.off_event_index))
    removed_events.update(_deleted_lyric_events(deletions, policies))

    interval_groups: dict[tuple[int, int, int], list[tuple[int, int, str]]] = defaultdict(list)
    for note in kept_notes:
        interval_groups[(note.track_index, note.channel, note.pitch)].append((
            note.onset_tick,
            note.end_tick,
            note.note_id,
        ))
    for insertion in insertions:
        interval_groups[(insertion.track_index, insertion.channel, insertion.pitch)].append((
            insertion.onset_tick,
            insertion.end_tick,
            insertion.insert_id,
        ))
    for key, intervals in interval_groups.items():
        intervals.sort()
        previous_end = None
        for onset, end, _item_id in intervals:
            if previous_end is not None and onset < previous_end:
                raise ValueError(
                    f"最终状态在轨道 {key[0]}、通道 {key[1]}、音高 {key[2]} 发生重叠；"
                    "事件配对会歧义。"
                )
            previous_end = end

    final_onsets: dict[int, set[int]] = defaultdict(set)
    for note in kept_notes:
        final_onsets[note.track_index].add(note.onset_tick)
    for insertion in insertions:
        final_onsets[insertion.track_index].add(insertion.onset_tick)

    for insertion in insertions:
        track = parsed.tracks[insertion.track_index]
        later = sorted(
            onset for onset in final_onsets[insertion.track_index]
            if onset > insertion.onset_tick
        )
        following = later[0] if later else max(track.end_tick, insertion.end_tick) + 1
        remaining_lyrics = [
            row for row in track.lyrics
            if (track.index, int(row["event_index"])) not in removed_events
            and insertion.onset_tick <= int(row["tick"]) < following
        ]
        other_notes_at_onset = 0
        for note in kept_notes:
            if note.track_index == insertion.track_index and note.onset_tick == insertion.onset_tick:
                other_notes_at_onset += 1
        for other in insertions:
            if other.edit_index != insertion.edit_index and (
                other.track_index == insertion.track_index
                and other.onset_tick == insertion.onset_tick
            ):
                other_notes_at_onset += 1

        if insertion.lyric is not None:
            if other_notes_at_onset:
                raise ValueError(
                    f"插入 {insertion.insert_id} 请求创建歌词，"
                    "但目标轨道同 onset 还有其他音符；歌词归属会歧义。"
                )
            if remaining_lyrics:
                raise ValueError(
                    f"插入 {insertion.insert_id} 请求创建歌词，"
                    "但其归属窗口已有歌词；拒绝覆盖或猜测归属。"
                )
        elif remaining_lyrics:
            raise ValueError(
                f"插入 {insertion.insert_id} 会改变既有或孤立歌词的唯一归属；"
                "拒绝不安全插入。"
            )

    return kept_notes, removed_events


def _build_track_items(
    track: Any,
    insertions: list[_Insertion],
    removed_events: set[tuple[int, int]],
) -> tuple[list[tuple[int, int, int, Any]], list[int]]:
    items: list[tuple[int, int, int, Any]] = []
    original_order: list[int] = []
    for event in track.events[:-1]:  # end_of_track is rebuilt last
        if (track.index, event.event_index) in removed_events:
            continue
        items.append((event.tick, 20, event.event_index, event.message))
        original_order.append(event.event_index)
    for insertion in insertions:
        if insertion.track_index != track.index:
            continue
        # At one tick, every note-off precedes every inserted note-on. This
        # keeps an adjacent same-pitch replacement unambiguously paired.
        items.append((
            insertion.end_tick,
            10,
            insertion.edit_index,
            mido.Message(
                "note_off",
                channel=insertion.channel,
                note=insertion.pitch,
                velocity=0,
            ),
        ))
        items.append((
            insertion.onset_tick,
            30,
            insertion.edit_index,
            mido.Message(
                "note_on",
                channel=insertion.channel,
                note=insertion.pitch,
                velocity=insertion.velocity,
            ),
        ))
        if insertion.lyric is not None:
            items.append((
                insertion.onset_tick,
                31,
                insertion.edit_index,
                mido.MetaMessage("lyrics", text=insertion.lyric),
            ))
    items.sort(key=lambda row: (row[0], row[1], row[2]))
    surviving_existing_order = [
        row[2] for row in items
        if row[1] == 20
    ]
    reordered = 0 if surviving_existing_order == original_order else 1
    return items, [track.index] if reordered else []


def _build_plan(parsed: Any, edits: Any) -> tuple[dict[str, Any], bytes]:
    public_edits, deletions, insertions = _normalise_edits(parsed, edits)
    policies = {
        row["note_id"]: row["lyric_policy"]
        for row in public_edits
        if row["operation"] == "delete"
    }
    notes = _safe_notes(parsed)
    kept_notes, removed_events = _validate_final_state(
        parsed,
        notes,
        deletions,
        insertions,
        policies,
    )

    new_midi = mido.MidiFile(
        type=parsed.midi.type,
        ticks_per_beat=parsed.midi.ticks_per_beat,
        charset="utf8",
    )
    expected_events: list[list[tuple[int, str]]] = []
    reordered_tracks: list[int] = []
    eot_changes: list[dict[str, int]] = []
    for track in parsed.tracks:
        items, reordered = _build_track_items(track, insertions, removed_events)
        reordered_tracks.extend(reordered)
        new_track = mido.MidiTrack()
        last_tick = 0
        expected: list[tuple[int, str]] = []
        for tick, _phase, _order, message in items:
            delta = tick - last_tick
            if delta < 0 or delta > _MAX_TICK:
                raise ValueError(f"轨道 {track.index} 生成无效 delta tick。")
            saved = message.copy(time=delta)
            new_track.append(saved)
            expected.append((tick, _event_fingerprint(saved)))
            last_tick = tick
        eot_tick = max(track.end_tick, last_tick)
        eot_delta = eot_tick - last_tick
        if eot_delta < 0 or eot_delta > _MAX_TICK:
            raise ValueError(f"轨道 {track.index} 的 end_of_track 时间无效。")
        eot = track.messages[-1].copy(time=eot_delta)
        new_track.append(eot)
        expected.append((eot_tick, _event_fingerprint(eot)))
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
        raise ValueError(f"生成结构补丁候选 MIDI 失败：{exc}") from exc
    candidate_bytes = buffer.getvalue()
    if len(candidate_bytes) > _MAX_CANDIDATE_BYTES:
        raise ValueError(f"候选 MIDI 超过 {_MAX_CANDIDATE_BYTES} 字节安全上限。")

    candidate_parsed = _score._parse_midi(candidate_bytes, "<structure-candidate>")
    if candidate_parsed.integrity_issues:
        raise ValueError("候选 MIDI 未通过结构验证。")
    candidate_notes = list(_score._notes_by_id(candidate_parsed).values())
    if any(note.pairing_ambiguous for note in candidate_notes):
        raise ValueError("候选 MIDI 产生音符配对歧义。")
    if len(candidate_parsed.tracks) != len(parsed.tracks):
        raise ValueError("候选 MIDI 丢失或新增了轨道。")
    for track_index, candidate_track in enumerate(candidate_parsed.tracks):
        actual = [(event.tick, _event_fingerprint(event.message)) for event in candidate_track.events]
        if actual != expected_events[track_index]:
            raise ValueError(f"候选 MIDI 轨道 {track_index} 未保留预期事件。")

    expected_note_keys = sorted(
        [_note_key(note) for note in kept_notes]
        + [insertion.note_key() for insertion in insertions]
    )
    actual_note_keys = sorted([_note_key(note) for note in candidate_notes])
    if actual_note_keys != expected_note_keys:
        raise ValueError("候选 MIDI 的实际音符集合与计划不一致，拒绝静默损失或增音。")

    actual_by_key: dict[tuple[int, int, int, int, int, int], Any] = {}
    for note in candidate_notes:
        key = _note_key(note)
        if key in actual_by_key:
            raise ValueError("候选 MIDI 含有无法唯一关联的音符。")
        actual_by_key[key] = note

    for track_index, (before_track, after_track) in enumerate(
        zip(parsed.tracks, candidate_parsed.tracks)
    ):
        before_tempos = [
            (event.tick, event.message.tempo)
            for event in before_track.events
            if event.message.type == "set_tempo"
        ]
        after_tempos = [
            (event.tick, event.message.tempo)
            for event in after_track.events
            if event.message.type == "set_tempo"
        ]
        if after_tempos != before_tempos:
            raise ValueError(f"候选 MIDI 轨道 {track_index} 的 tempo 映射发生变化。")
        before_signatures = [
            (event.tick, event.message.numerator, event.message.denominator)
            for event in before_track.events
            if event.message.type == "time_signature"
        ]
        after_signatures = [
            (event.tick, event.message.numerator, event.message.denominator)
            for event in after_track.events
            if event.message.type == "time_signature"
        ]
        if after_signatures != before_signatures:
            raise ValueError(f"候选 MIDI 轨道 {track_index} 的拍号发生变化。")

    lyric_rows: dict[tuple[int, int], dict[str, Any]] = {}
    for track in parsed.tracks:
        for row in track.lyrics:
            lyric_rows[(track.index, int(row["event_index"]))] = row

    deleted_evidence: list[dict[str, Any]] = []
    for note in deletions:
        policy = policies[note.note_id]
        lyric_row = (
            lyric_rows.get((note.track_index, note.lyric_event_index))
            if note.lyric_event_index is not None
            else None
        )
        nearby_lyrics = _lyrics_near_onset(
            parsed.tracks[note.track_index],
            note.onset_tick,
        )
        deleted_evidence.append({
            "note_id": note.note_id,
            "track_index": note.track_index,
            "channel": note.channel,
            "pitch": note.pitch,
            "onset_tick": note.onset_tick,
            "end_tick": note.end_tick,
            "duration_tick": note.duration_tick,
            "velocity": note.velocity,
            "lyric_policy": policy,
            "lyric_association": (
                "unique" if note.lyric_event_index is not None else "unknown"
            ),
            "owned_lyric_event_index": note.lyric_event_index,
            "owned_lyric": lyric_row["text"] if lyric_row else None,
            "nearby_lyric_event_indices": sorted(
                int(row["event_index"]) for row in nearby_lyrics
            ),
            "lyric_removed": policy == "delete_owned" and note.lyric_event_index is not None,
        })

    inserted_evidence: list[dict[str, Any]] = []
    for insertion in insertions:
        candidate_note = actual_by_key.get(insertion.note_key())
        if candidate_note is None:
            raise ValueError(f"候选 MIDI 找不到插入操作 {insertion.insert_id} 的实际音符。")
        if insertion.lyric is not None:
            if candidate_note.lyric != insertion.lyric or candidate_note.lyric_event_index is None:
                raise ValueError(
                    f"插入操作 {insertion.insert_id} 的歌词没有被唯一关联，拒绝验证成功。"
                )
        inserted_evidence.append({
            "insert_id": insertion.insert_id,
            "candidate_note_id": candidate_note.note_id,
            "track_index": insertion.track_index,
            "channel": insertion.channel,
            "pitch": insertion.pitch,
            "onset_tick": insertion.onset_tick,
            "end_tick": insertion.end_tick,
            "duration_tick": insertion.duration_tick,
            "velocity": insertion.velocity,
            "lyric": insertion.lyric,
            "lyric_created": insertion.lyric is not None,
        })

    operations: list[dict[str, Any]] = []
    for edit_index, edit in enumerate(public_edits):
        if edit["operation"] == "delete":
            evidence = next(
                row for row in deleted_evidence
                if row["note_id"] == edit["note_id"]
            )
            operations.append({"op": "delete_note", **evidence})
        else:
            insertion = next(row for row in insertions if row.edit_index == edit_index)
            evidence = next(
                row for row in inserted_evidence
                if row["insert_id"] == insertion.insert_id
            )
            operations.append({"op": "insert_note", **evidence})

    warnings = [
        "候选 MIDI 的轨道数、tempo、time signature、通道、力度及所有未删除事件"
        "均按完整事件指纹复核；本结果不推断音乐意义。",
    ]
    if any(
        row["lyric_policy"] == "preserve" and row["nearby_lyric_event_indices"]
        for row in deleted_evidence
    ):
        warnings.append(
            "preserve 策略保留了删除音符邻域的歌词事件；这些歌词可能成为孤立歌词，"
            "既有归属不明确时仍保持 unknown，而不是被重新归属。"
        )
    if any(row["operation"] == "insert" and "lyric" not in row for row in public_edits):
        warnings.append("插入音符未提供歌词；未创建或推断任何歌词。")
    structural_changes = {
        "end_of_track": eot_changes,
        "reordered_tracks": sorted(set(reordered_tracks)),
    }
    plan_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _PLAN_KIND,
        "source_sha256": parsed.source_sha256,
        "format": "midi",
        "midi_type": parsed.midi.type,
        "ticks_per_beat": parsed.midi.ticks_per_beat,
        "track_count": len(parsed.tracks),
        "edits": public_edits,
        "diff": {
            "schema": _DIFF_SCHEMA,
            "operations": operations,
            "deleted_notes": deleted_evidence,
            "inserted_notes": inserted_evidence,
        },
        "expected_candidate_sha256": _sha256(candidate_bytes),
        "structural_changes": structural_changes,
        "warnings": warnings,
        "inference_performed": False,
    }
    plan = dict(plan_payload)
    plan["plan_sha256"] = _canonical_digest(plan_payload)
    return plan, candidate_bytes


def plan_structure_patch(path, expected_sha256, edits) -> dict:
    """Validate a strict delete/insert plan without modifying the source."""
    source, _raw, source_sha, parsed = _read_source(path)
    expected = _normalise_sha(expected_sha256, "expected_sha256")
    if source_sha != expected:
        raise ValueError("源文件 SHA-256 已变化；请重新读取并规划结构补丁。")
    plan, _candidate_bytes = _build_plan(parsed, edits)
    _source_after, _raw_after, sha_after, _parsed_after = _read_source(source)
    if sha_after != source_sha:
        raise ValueError("源文件在规划期间发生变化；已拒绝返回计划。")
    return plan


def _validate_plan(plan: Any) -> tuple[dict[str, Any], str, str, str]:
    if not isinstance(plan, Mapping):
        raise ValueError("plan 必须是对象。")
    supplied = dict(plan)
    if set(supplied) != _PLAN_KEYS:
        missing = sorted(_PLAN_KEYS - set(supplied))
        extra = sorted(set(supplied) - _PLAN_KEYS, key=str)
        raise ValueError(f"plan 字段不完整或不支持；缺少={missing}，额外={extra}。")
    if (
        type(supplied.get("schema_version")) is not int
        or supplied.get("schema_version") != _SCHEMA_VERSION
        or supplied.get("kind") != _PLAN_KIND
        or supplied.get("format") != "midi"
        or supplied.get("inference_performed") is not False
    ):
        raise ValueError("plan schema、kind、format 或 inference_performed 无效。")
    plan_sha = _normalise_sha(supplied.get("plan_sha256"), "plan_sha256")
    payload = {key: value for key, value in supplied.items() if key != "plan_sha256"}
    try:
        actual_sha = _canonical_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"plan 不是完整、有限的 JSON 对象：{exc}") from exc
    if actual_sha != plan_sha:
        raise ValueError("plan_sha256 与完整计划内容不匹配；计划已损坏或被篡改。")
    edits = supplied.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("plan.edits 必须是非空数组。")
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise ValueError(f"plan.edits[{index}] 必须是对象。")
        operation = edit.get("operation")
        if operation == "delete":
            keys = set(edit)
            if not _DELETE_KEYS.issubset(keys):
                raise ValueError(f"plan.edits[{index}] delete 字段不完整。")
            extra = sorted(keys - _DELETE_KEYS, key=str)
            if extra:
                raise ValueError(f"plan.edits[{index}] 含不支持字段：{extra}。")
            if type(edit.get("note_id")) is not str or not edit["note_id"]:
                raise ValueError(f"plan.edits[{index}].note_id 无效。")
            if type(edit.get("lyric_policy")) is not str:
                raise ValueError(f"plan.edits[{index}].lyric_policy 必须是字符串。")
            if edit["lyric_policy"] not in {"preserve", "delete_owned"}:
                raise ValueError(f"plan.edits[{index}].lyric_policy 无效。")
        elif operation == "insert":
            keys = set(edit)
            if not _INSERT_REQUIRED_KEYS.issubset(keys):
                raise ValueError(f"plan.edits[{index}] insert 字段不完整。")
            extra = sorted(keys - _INSERT_KEYS, key=str)
            if extra:
                raise ValueError(f"plan.edits[{index}] 含不支持字段：{extra}。")
        else:
            raise ValueError(f"plan.edits[{index}].operation 无效。")
    source_sha = _normalise_sha(supplied.get("source_sha256"), "source_sha256")
    candidate_sha = _normalise_sha(
        supplied.get("expected_candidate_sha256"),
        "expected_candidate_sha256",
    )
    return supplied, plan_sha, source_sha, candidate_sha


def _normalise_output_root(output_root: Any) -> Path:
    if output_root is None or isinstance(output_root, bool):
        raise ValueError("output_root 必须是目录路径。")
    try:
        if isinstance(output_root, str) and not output_root:
            raise ValueError("output_root 不能为空。")
        root = Path(output_root)
        if _io_path(root).exists() and not _io_path(root).is_dir():
            raise ValueError("output_root 已存在但不是目录。")
        _io_path(root).mkdir(parents=True, exist_ok=True)
        return root.resolve()
    except ValueError:
        raise
    except (TypeError, OSError) as exc:
        raise ValueError(f"output_root 无效：{exc}") from exc


def apply_structure_patch(path, plan, output_root) -> dict:
    """Apply a verified plan to a new MIDI candidate and JSON receipt."""
    supplied, plan_sha, source_sha, expected_candidate_sha = _validate_plan(plan)
    source, _raw, current_sha, parsed = _read_source(path)
    if current_sha != source_sha:
        raise ValueError("当前源文件 SHA-256 与计划不一致；请重新读取和规划。")
    regenerated, candidate_bytes = _build_plan(parsed, supplied["edits"])
    if regenerated != supplied:
        raise ValueError("当前源文件重新生成的计划与提交计划完全不一致；拒绝该计划。")
    candidate_sha = _sha256(candidate_bytes)
    if candidate_sha != expected_candidate_sha:
        raise ValueError("重新生成的候选 SHA-256 与计划不一致。")

    root = _normalise_output_root(output_root)
    candidate_path = (root / f"vwa-structure-{plan_sha}.mid").resolve()
    receipt_path = (root / f"vwa-structure-{plan_sha}.mid.receipt.json").resolve()
    if candidate_path == source or receipt_path == source:
        raise ValueError("输出路径与源文件冲突；拒绝覆盖源文件。")

    receipt_payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _RECEIPT_KIND,
        "plan_sha256": plan_sha,
        "source_sha256": source_sha,
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_sha,
        "receipt_path": str(receipt_path),
        "format": "midi",
        "diff": supplied["diff"],
        "structural_changes": supplied["structural_changes"],
        "warnings": supplied["warnings"],
        "inference_performed": False,
    }
    receipt = dict(receipt_payload)
    receipt["receipt_sha256"] = _canonical_digest(receipt_payload)
    receipt_bytes = _canonical_json_bytes(receipt, pretty=True)

    existing_candidate = _existing_file(candidate_path, "候选文件")
    if existing_candidate is not None and existing_candidate != candidate_bytes:
        raise ValueError("候选路径已有不同内容；拒绝覆盖或信任碰撞。")
    existing_receipt = _existing_file(receipt_path, "receipt")
    if existing_receipt is not None:
        try:
            existing_value = json.loads(existing_receipt.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"已有 receipt 不是有效 UTF-8 JSON：{exc}") from exc
        if existing_value != receipt:
            raise ValueError("已有 receipt 与当前计划不一致；拒绝覆盖或信任碰撞。")

    _source_before, _raw_before, before_sha, _parsed_before = _read_source(source)
    if before_sha != source_sha:
        raise ValueError("源文件在发布前发生变化；未发布候选。")
    if existing_candidate is None:
        _publish_exclusive(candidate_path, candidate_bytes)
    if existing_receipt is None:
        _publish_exclusive(receipt_path, receipt_bytes)

    published_candidate = _existing_file(candidate_path, "候选文件")
    published_receipt = _existing_file(receipt_path, "receipt")
    if published_candidate != candidate_bytes:
        raise ValueError("候选发布后字节校验失败。")
    if published_receipt != receipt_bytes:
        raise ValueError("receipt 发布后字节校验失败。")
    _source_after, _raw_after, after_sha, _parsed_after = _read_source(source)
    if after_sha != source_sha:
        raise ValueError("源文件在发布期间发生变化；拒绝返回不可再验证的 receipt。")
    return receipt
