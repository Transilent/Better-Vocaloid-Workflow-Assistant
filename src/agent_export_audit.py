"""Independent, read-only audit of VWA export receipts.

The caller-facing contract is :func:`audit_export`.  This module deliberately
does not trust ``verified_*`` booleans in an export receipt: it hashes the
source and candidates again, reloads each project through the locally supplied
LibreSVIP runtime, and compares musical data on both sides.

Version coupling is intentional.  ``agent_exports`` remains the owner of the
calibration that maps MIDI ticks to LibreSVIP's 480 PPQN project clock, the
candidate naming rule, and the receipt canonical digest.  The private helpers
used here are limited to those stable boundaries; the audit does not modify the
export worker or any candidate.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from contextlib import contextmanager
import copy
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import unicodedata

import mido

import agent_exports as _exports
import agent_media
from common import native_path


_SCHEMA_VERSION = 1
_KIND = "vwa-export-audit"
_INTERNAL_PPQN = 480
_DEFAULT_BPM = 120.0
_BASE_TIMING_TOLERANCE_MS = 2.0
_MAX_ISSUES = 64
_MAX_DEVIATIONS = 32
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 128 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORMATS = frozenset({"svp", "ustx", "vsqx"})


def _io_path(path: Any) -> Path:
    return Path(native_path(path))


def _normalise(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value))


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("审计结果必须只包含有限的 JSON 数据。") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _issue(code: str, message: str, fmt: str | None = None, **fields: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if fmt is not None:
        result["format"] = fmt
    result.update(fields)
    return result


def _checker(cancel: Callable[[], Any] | None) -> Callable[[], None]:
    if cancel is not None and not callable(cancel):
        raise ValueError("cancel 必须可调用或为 None。")

    def check() -> None:
        if cancel is not None and bool(cancel()):
            raise InterruptedError("已取消导出审计。")

    return check


def _read_stable_bytes(path: Path, *, label: str, maximum: int) -> tuple[bytes, str]:
    try:
        size = _io_path(path).stat().st_size
        if size < 0 or size > maximum:
            raise ValueError(f"{label}大小超出安全审计上限。")
        with _io_path(path).open("rb") as stream:
            raw = stream.read(maximum + 1)
        with _io_path(path).open("rb") as stream:
            after = stream.read(maximum + 1)
    except OSError as error:
        raise ValueError(f"无法读取{label}：{error}") from error
    if len(raw) > maximum:
        raise ValueError(f"{label}大小超出安全审计上限。")
    if not raw:
        raise ValueError(f"{label}为空。")
    if raw != after:
        raise ValueError(f"{label}在审计读取期间发生变化。")
    return raw, hashlib.sha256(raw).hexdigest()


def _stable_sha(path: Path, *, label: str, maximum: int = _MAX_CANDIDATE_BYTES) -> str:
    try:
        size = _io_path(path).stat().st_size
        if size < 0 or size > maximum:
            raise ValueError(f"{label}大小超出安全审计上限。")
        first = hashlib.sha256()
        with _io_path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                first.update(block)
        second = hashlib.sha256()
        with _io_path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                second.update(block)
    except OSError as error:
        raise ValueError(f"无法读取{label}：{error}") from error
    if first.digest() != second.digest():
        raise ValueError(f"{label}在审计读取期间发生变化。")
    return first.hexdigest()


def _extract_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("receipt 必须是对象。")
    outer = copy.deepcopy(dict(value))
    nested = outer.get("receipt")
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise ValueError("receipt.receipt 必须是对象。")
        result = copy.deepcopy(dict(nested))
    else:
        result = outer
    if result.get("schema_version") != 1 or result.get("kind") != "vwa-export-receipt":
        raise ValueError("不支持的 export receipt schema/kind。")
    return result


def _receipt_digest_status(receipt: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    stored = receipt.get("receipt_sha256")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    computed = _exports._digest(unsigned)
    if not isinstance(stored, str) or not _SHA256_RE.fullmatch(stored):
        return False, stored if isinstance(stored, str) else None, computed
    return stored == computed, stored, computed


def _resolved_path(value: Any, label: str) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, bytes, os.PathLike)):
        raise ValueError(label + "必须是路径。")
    try:
        return Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(label + "无效。") from error


def _resolve_runtime(value: Any) -> Path:
    if value is not None:
        return _exports._runtime_path(value)
    try:
        return _exports._runtime_path(None)
    except FileNotFoundError:
        paths_file = Path(__file__).resolve().parents[2] / "paths.json"
        try:
            with _io_path(paths_file).open("r", encoding="utf-8") as stream:
                paths = json.load(stream)
        except (OSError, ValueError, TypeError) as error:
            raise FileNotFoundError("无法从 workspace/paths.json 定位只读导出运行库。") from error
        legacy = paths.get("legacy_installation") if isinstance(paths, Mapping) else None
        if not isinstance(legacy, str) or not legacy:
            raise FileNotFoundError("workspace/paths.json 缺少 legacy_installation。")
        return _exports._runtime_path(Path(legacy) / "dependencies" / "export")


@contextmanager
def _audit_scratch(value: Any):
    if value is None:
        with tempfile.TemporaryDirectory(prefix="vwa-export-audit-") as temporary:
            yield Path(temporary)
        return
    root = _resolved_path(value, "output_root")
    if _io_path(root).exists() and not _io_path(root).is_dir():
        raise ValueError("output_root 已存在但不是目录。")
    try:
        _io_path(root).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("无法创建审计 output_root：" + str(error)) from error
    yield root


@dataclass(frozen=True)
class _TempoRow:
    position: int
    bpm: float
    order: int = 0


@dataclass(frozen=True)
class _SignatureRow:
    bar_index: int
    numerator: int
    denominator: int


@dataclass
class _SourceNote:
    note_id: str
    track_index: int
    channel: int
    track_title: str
    pitch: int
    onset_tick: int
    duration_tick: int
    velocity: int
    lyric: str | None
    lyric_ambiguous: bool
    overlap_ambiguous: bool
    quant_onset: int = 0
    quant_length: int = 0
    onset_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class _CandidateNote:
    note_id: str
    track_index: int
    track_title: str
    pitch: int
    onset_tick: int
    duration_tick: int
    lyric: str | None
    onset_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0


def _validate_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须是 {minimum}..{maximum} 的整数。")
    return value


def _validate_float(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(label + "必须是数值。")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(label + "必须是有限正数。")
    return number


def _normalise_tempo_rows(rows: Any) -> list[_TempoRow]:
    if not isinstance(rows, (list, tuple)):
        raise ValueError("tempo 元数据必须是列表。")
    result: list[_TempoRow] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError("tempo 元数据项必须是对象。")
        position = _validate_int(row.get("position"), "tempo.position", minimum=0, maximum=2**63 - 1)
        bpm = _validate_float(row.get("bpm"), "tempo.bpm")
        if bpm > 100000.0:
            raise ValueError("tempo.bpm 超出支持范围。")
        result.append(_TempoRow(position, bpm, index))
    result.sort(key=lambda item: (item.position, item.order))
    return result


def _effective_tempos(rows: list[_TempoRow]) -> list[_TempoRow]:
    by_position: dict[int, _TempoRow] = {}
    for row in rows:
        by_position[row.position] = row
    ordered = [by_position[position] for position in sorted(by_position)]
    if not ordered or ordered[0].position > 0:
        ordered.insert(0, _TempoRow(0, _DEFAULT_BPM, -1))
    result: list[_TempoRow] = []
    for row in ordered:
        if not result or round(result[-1].bpm, 3) != round(row.bpm, 3):
            result.append(row)
    return result


def _tempo_at(rows: list[_TempoRow], tick: int) -> float:
    effective = _effective_tempos(rows)
    positions = [row.position for row in effective]
    index = bisect.bisect_right(positions, tick) - 1
    index = max(index, 0)
    return effective[index].bpm


def _seconds_at_tick(rows: list[_TempoRow], tick: int, ppqn: int = _INTERNAL_PPQN) -> float:
    effective = _effective_tempos(rows)
    seconds = 0.0
    previous_tick = effective[0].position
    previous_bpm = effective[0].bpm
    if tick <= previous_tick:
        return (tick - previous_tick) * 60.0 / previous_bpm / ppqn
    for row in effective[1:]:
        if tick <= row.position:
            return seconds + (tick - previous_tick) * 60.0 / previous_bpm / ppqn
        seconds += (row.position - previous_tick) * 60.0 / previous_bpm / ppqn
        previous_tick, previous_bpm = row.position, row.bpm
    return seconds + (tick - previous_tick) * 60.0 / previous_bpm / ppqn


def _source_signatures(midi: Any) -> list[_SignatureRow]:
    master = midi.tracks[0]
    tick = 0
    previous_tick = 0
    measure = 0.0
    result: list[_SignatureRow] = []
    for message in master:
        tick += int(message.time)
        if not (message.is_meta and message.type == "time_signature"):
            continue
        numerator = _validate_int(message.numerator, "time_signature.numerator", minimum=1, maximum=64)
        denominator = _validate_int(message.denominator, "time_signature.denominator", minimum=1, maximum=64)
        if denominator & (denominator - 1):
            raise ValueError("MIDI 拍号分母必须是 2 的幂。")
        if not result:
            bar_ticks = 4 * midi.ticks_per_beat
        else:
            previous = result[-1]
            bar_ticks = (_INTERNAL_PPQN * 4) * previous.numerator / previous.denominator
            bar_ticks *= midi.ticks_per_beat / _INTERNAL_PPQN
        measure += (tick - previous_tick) / bar_ticks
        result.append(_SignatureRow(math.floor(measure), numerator, denominator))
        previous_tick = tick
    if not result or result[0].bar_index > 0:
        result.insert(0, _SignatureRow(0, 4, 4))
    return result


def _parse_source(raw: bytes) -> dict[str, Any]:
    try:
        midi = mido.MidiFile(file=io.BytesIO(raw), charset="utf8", clip=False)
    except (OSError, EOFError, ValueError, UnicodeError, IndexError) as error:
        raise ValueError("源 MIDI 无法解析：" + str(error)) from error
    if midi.type not in (0, 1):
        raise ValueError("源 MIDI format 2 无法建立单一 tempo 时间线。")
    if type(midi.ticks_per_beat) is not int or not 0 < midi.ticks_per_beat < 32768:
        raise ValueError("源 MIDI 的 PPQN 无效。")
    if not midi.tracks:
        raise ValueError("源 MIDI 不含轨道。")

    raw_tempo_events: list[tuple[int, int, int, int]] = []
    raw_notes: list[_SourceNote] = []
    order = 0
    tracks_with_notes = 0
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        track_title = ""
        lyric_rows: dict[int, list[str]] = defaultdict(list)
        active: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        has_notes = False
        for message in track:
            if type(message.time) is not int or not 0 <= message.time <= 0x0FFFFFFF:
                raise ValueError(f"源 MIDI 轨道 {track_index} 含无效 delta tick。")
            tick += message.time
            if message.is_meta:
                if message.type == "track_name":
                    track_title = _normalise(message.name)
                elif message.type == "lyrics":
                    lyric_rows[tick].append(_normalise(message.text))
                elif message.type == "set_tempo":
                    tempo = _validate_int(message.tempo, "set_tempo.tempo", minimum=1, maximum=0xFFFFFF)
                    raw_tempo_events.append((tick, track_index, order, tempo))
                    order += 1
                continue
            if message.type == "note_on" and message.velocity > 0:
                key = (_validate_int(message.channel, "MIDI channel", minimum=0, maximum=15),
                       _validate_int(message.note, "MIDI pitch", minimum=0, maximum=127))
                active[key].append((tick, _validate_int(message.velocity, "MIDI velocity", minimum=1, maximum=127)))
                has_notes = True
                continue
            is_off = message.type == "note_off" or (message.type == "note_on" and message.velocity == 0)
            if not is_off:
                continue
            key = (_validate_int(message.channel, "MIDI channel", minimum=0, maximum=15),
                   _validate_int(message.note, "MIDI pitch", minimum=0, maximum=127))
            opens = active.get(key, [])
            closing = [row for row in opens if row[0] != tick]
            keeping = [row for row in opens if row[0] == tick]
            for onset, velocity in closing:
                length = tick - onset
                if length <= 0:
                    raise ValueError(f"源 MIDI 轨道 {track_index} 含零时值音符。")
                lyric_values = lyric_rows.get(onset, [])
                unique = list(dict.fromkeys(lyric_values))
                lyric = unique[0] if len(unique) == 1 else None
                raw_notes.append(_SourceNote(
                    note_id="pending",
                    track_index=track_index,
                    channel=key[0],
                    track_title=track_title,
                    pitch=key[1],
                    onset_tick=onset,
                    duration_tick=length,
                    velocity=velocity,
                    lyric=lyric,
                    lyric_ambiguous=len(unique) > 1,
                    overlap_ambiguous=len(closing) > 1,
                ))
            if keeping:
                active[key] = keeping
            else:
                active.pop(key, None)
        if any(active.values()):
            raise ValueError(f"源 MIDI 轨道 {track_index} 含未闭合音符。")
        if has_notes:
            tracks_with_notes += 1
    if not raw_notes:
        raise ValueError("源 MIDI 不含可审计音符。")

    raw_notes.sort(key=lambda note: (
        note.track_index, note.onset_tick, note.channel, note.pitch,
        note.duration_tick, note.lyric or "",
    ))
    for index, note in enumerate(raw_notes):
        note.note_id = f"source:{note.track_index}:{note.channel}:{index}"
        note.quant_onset = int(round(note.onset_tick * _INTERNAL_PPQN / midi.ticks_per_beat))
        note.quant_length = int(round(note.duration_tick * _INTERNAL_PPQN / midi.ticks_per_beat))
        if note.quant_length <= 0:
            raise ValueError("源 MIDI 音符量化到 480 PPQN 后时值为零。")

    source_tempo_rows = [
        _TempoRow(
            int(round(tick * _INTERNAL_PPQN / midi.ticks_per_beat)),
            round(60_000_000.0 / tempo, 3),
            item_order,
        )
        for tick, _track_index, item_order, tempo in sorted(raw_tempo_events)
    ]
    if not source_tempo_rows:
        source_tempo_rows = [_TempoRow(0, _DEFAULT_BPM, 0)]
    # Seconds use original source ticks with a separate exact tempo map; the
    # 480 PPQN rows above are only for structural comparison.
    exact_seconds_rows: list[_TempoRow] = []
    for tick, _track_index, event_order, tempo in sorted(raw_tempo_events):
        exact_seconds_rows.append(_TempoRow(tick, 60_000_000.0 / tempo, event_order))
    if not exact_seconds_rows:
        exact_seconds_rows = [_TempoRow(0, _DEFAULT_BPM, 0)]
    for note in raw_notes:
        note.onset_seconds = _seconds_at_tick(exact_seconds_rows, note.onset_tick, midi.ticks_per_beat)
        note.end_seconds = _seconds_at_tick(exact_seconds_rows, note.onset_tick + note.duration_tick, midi.ticks_per_beat)
        note.duration_seconds = note.end_seconds - note.onset_seconds

    signatures = _source_signatures(midi)
    return {
        "ppqn": midi.ticks_per_beat,
        "midi_type": midi.type,
        "notes": raw_notes,
        "tempos": _normalise_tempo_rows([
            {"position": row.position, "bpm": row.bpm} for row in source_tempo_rows
        ]),
        "signatures": signatures,
        "tracks_with_notes": tracks_with_notes,
        "channels": sorted({(note.track_index, note.channel) for note in raw_notes}),
    }


def _parse_project(project: Any, format_name: str) -> dict[str, Any]:
    tempo_source = getattr(project, "song_tempo_list", None)
    signature_source = getattr(project, "time_signature_list", None)
    track_source = getattr(project, "track_list", None)
    if tempo_source is None or signature_source is None or track_source is None:
        raise ValueError("候选工程缺少 tempo、拍号或轨道元数据。")
    # Reloaded SVP/USTX plugin tempo positions include an internal first bar;
    # VSQX parser has already removed its on-disk preMeasure. Notes are on the
    # playback clock in all three plugins. Resolve this parser convention here
    # independently of the export adapter, before computing actual seconds.
    prefix = (1920 if format_name=='ustx' else
              round(signature_source[0].bar_length()) if format_name=='svp' else 0)
    tempo_rows=[{'position':row.position,'bpm':row.bpm} for row in tempo_source]
    if prefix:
        preceding=[row for row in tempo_rows if row['position']<=prefix]
        shifted=[{'position':row['position']-prefix,'bpm':row['bpm']}
                 for row in tempo_rows if row['position']>=prefix]
        if not shifted or shifted[0]['position']>0:
            last_position=max((row['position'] for row in preceding),default=None)
            anchors=[{'position':0,'bpm':row['bpm']} for row in preceding
                     if row['position']==last_position]
            shifted=(anchors or [{'position':0,'bpm':120.0}])+shifted
        tempo_rows=shifted
    tempos = _normalise_tempo_rows(tempo_rows)
    signatures: list[_SignatureRow] = []
    for row in signature_source:
        signatures.append(_SignatureRow(
            _validate_int(max(0,row.bar_index-1) if format_name=='svp' else row.bar_index,
                          "time_signature.bar_index", minimum=0, maximum=2**31 - 1),
            _validate_int(row.numerator, "time_signature.numerator", minimum=1, maximum=64),
            _validate_int(row.denominator, "time_signature.denominator", minimum=1, maximum=64),
        ))
        if signatures[-1].denominator & (signatures[-1].denominator - 1):
            raise ValueError("候选工程拍号分母不是 2 的幂。")
    notes: list[_CandidateNote] = []
    tracks: list[dict[str, Any]] = []
    for track_index, track in enumerate(track_source):
        title = _normalise(getattr(track, "title", "") or "")
        note_list = getattr(track, "note_list", None)
        tracks.append({
            "track_index": track_index,
            "title": title,
            "singing": note_list is not None,
            "note_count": len(note_list) if note_list is not None else 0,
        })
        if note_list is None:
            continue
        for note in note_list:
            pitch = _validate_int(note.key_number, "candidate note pitch", minimum=0, maximum=127)
            onset = _validate_int(note.start_pos, "candidate note onset", minimum=0, maximum=2**63 - 1)
            duration = _validate_int(note.length, "candidate note duration", minimum=1, maximum=2**63 - 1)
            lyric_text = getattr(note, "lyric", "")
            lyric = _normalise(lyric_text) if lyric_text not in (None, "") else None
            notes.append(_CandidateNote(
                note_id="pending",
                track_index=track_index,
                track_title=title,
                pitch=pitch,
                onset_tick=onset,
                duration_tick=duration,
                lyric=lyric,
            ))
    notes.sort(key=lambda note: (
        note.track_index, note.onset_tick, note.pitch, note.duration_tick, note.lyric or "",
    ))
    for index, note in enumerate(notes):
        note.note_id = f"candidate:{note.track_index}:{index}"
        note.onset_seconds = _seconds_at_tick(tempos, note.onset_tick)
        note.end_seconds = _seconds_at_tick(tempos, note.onset_tick + note.duration_tick)
        note.duration_seconds = note.end_seconds - note.onset_seconds
    return {
        "notes": notes,
        "tempos": tempos,
        "signatures": signatures,
        "tracks": tracks,
    }


def _tempo_comparison(source_rows: list[_TempoRow], candidate_rows: list[_TempoRow],
                      source_notes: list[_SourceNote]) -> dict[str, Any]:
    source_effective = _effective_tempos(source_rows)
    candidate_effective = _effective_tempos(candidate_rows)
    positions = sorted({
        0,
        *(row.position for row in source_rows),
        *(row.position for row in candidate_rows),
        *(note.quant_onset for note in source_notes),
        *(note.quant_onset + note.quant_length for note in source_notes),
    })
    mismatches = []
    for position in positions:
        source_bpm = _tempo_at(source_effective, position)
        candidate_bpm = _tempo_at(candidate_effective, position)
        if round(source_bpm, 3) != round(candidate_bpm, 3):
            mismatches.append({
                "position_480ppqn": position,
                "source_bpm": round(source_bpm, 3),
                "candidate_bpm": round(candidate_bpm, 3),
            })
            if len(mismatches) >= _MAX_DEVIATIONS:
                break
    source_raw = [[row.position, round(row.bpm, 3)] for row in source_rows]
    candidate_raw = [[row.position, round(row.bpm, 3)] for row in candidate_rows]
    source_effective = _effective_tempos(source_rows)
    candidate_effective = _effective_tempos(candidate_rows)
    raw_structure_equal = source_raw == candidate_raw
    structure_equal = [
        [row.position, round(row.bpm, 3)] for row in source_effective
    ] == [
        [row.position, round(row.bpm, 3)] for row in candidate_effective
    ]
    duplicate_positions = sorted({
        row.position for row in candidate_rows
        if sum(1 for other in candidate_rows if other.position == row.position) > 1
    })
    return {
        "status": "differences" if mismatches else "verified",
        "raw_structure_equivalent": raw_structure_equal,
        "structure_equivalent": structure_equal,
        "effective_map_equivalent": not mismatches,
        "effective_mismatch_count": len(mismatches),
        "effective_mismatches": mismatches,
        "source": [{"position_480ppqn": row.position, "bpm": round(row.bpm, 3)} for row in source_rows],
        "candidate": [{"position_480ppqn": row.position, "bpm": round(row.bpm, 3)} for row in candidate_rows],
        "duplicate_candidate_positions": duplicate_positions,
    }


def _signature_comparison(source_rows: list[_SignatureRow], candidate_rows: list[_SignatureRow]) -> dict[str, Any]:
    source_raw = [[row.bar_index, row.numerator, row.denominator] for row in source_rows]
    candidate_raw = [[row.bar_index, row.numerator, row.denominator] for row in candidate_rows]
    return {
        "status": "verified" if source_raw == candidate_raw else "differences",
        "source": source_raw,
        "candidate": candidate_raw,
        "equal": source_raw == candidate_raw,
    }


def _quant_key(note: _SourceNote | _CandidateNote) -> tuple[int, int, int]:
    onset = note.quant_onset if isinstance(note, _SourceNote) else note.onset_tick
    length = note.quant_length if isinstance(note, _SourceNote) else note.duration_tick
    return note.pitch, onset, length


def _track_mapping(source_notes: list[_SourceNote], candidate_notes: list[_CandidateNote]) -> dict[int, dict[str, Any]]:
    source_groups: dict[tuple[int, int], list[_SourceNote]] = defaultdict(list)
    candidate_groups: dict[int, list[_CandidateNote]] = defaultdict(list)
    for note in source_notes:
        source_groups[(note.track_index, note.channel)].append(note)
    for note in candidate_notes:
        candidate_groups[note.track_index].append(note)
    source_counters = {
        key: Counter(_quant_key(note) for note in notes)
        for key, notes in source_groups.items()
    }
    result: dict[int, dict[str, Any]] = {}
    for track_index, notes in candidate_groups.items():
        candidate_counter = Counter(_quant_key(note) for note in notes)
        candidate_title = notes[0].track_title if notes else ""
        scores = []
        for source_key, source_counter in source_counters.items():
            content_score = sum((candidate_counter & source_counter).values())
            source_title = source_groups[source_key][0].track_title
            title_match = bool(candidate_title and source_title and candidate_title == source_title)
            scores.append((content_score, int(title_match), source_key))
        best_content = max((score for score, _title, _key in scores), default=0)
        content_winners = [
            (score, title, key) for score, title, key in scores if score == best_content
        ]
        best_title = max((title for _score, title, _key in content_winners), default=0)
        winners = [key for score, title, key in content_winners if title == best_title and (score > 0 or title)]
        if len(winners) == 1:
            source_key = winners[0]
            selected_title = next(title for score, title, key in scores if key == source_key)
            result[track_index] = {
                "association": "content_or_title",
                "source_track_index": source_key[0],
                "source_channel": source_key[1],
                "matched_quantized_notes": best_content,
                "title_match": bool(selected_title),
                "ambiguous": False,
            }
        elif winners:
            result[track_index] = {
                "association": "ambiguous",
                "candidates": [[key[0], key[1]] for key in winners],
                "matched_quantized_notes": best_content,
                "ambiguous": True,
            }
        else:
            result[track_index] = {
                "association": "unsupported",
                "ambiguous": False,
                "matched_quantized_notes": 0,
            }
    return result


def _lyric_equal(source: _SourceNote, candidate: _CandidateNote) -> bool:
    if source.lyric is None:
        return True
    return source.lyric == candidate.lyric


def _bounded_append(target: list[dict[str, Any]], value: dict[str, Any]) -> None:
    if len(target) < _MAX_DEVIATIONS:
        target.append(value)


def _match_notes(source_notes: list[_SourceNote], candidate_notes: list[_CandidateNote],
                 track_map: dict[int, dict[str, Any]]) -> dict[str, Any]:
    unused = {note.note_id: note for note in source_notes}
    pairs: list[tuple[_SourceNote, _CandidateNote, str]] = []
    unmatched_candidates: list[_CandidateNote] = []

    def unique_primary(note: _CandidateNote, allowed: list[_SourceNote]) -> list[_SourceNote]:
        return [
            source for source in allowed
            if source.note_id in unused and _quant_key(source) == _quant_key(note)
        ]

    for candidate in candidate_notes:
        association = track_map.get(candidate.track_index, {})
        if association.get("ambiguous") or association.get("association") != "content_or_title":
            allowed = list(unused.values())
            association_kind = "global"
        else:
            allowed = [
                source for source in unused.values()
                if source.track_index == association["source_track_index"]
                and source.channel == association["source_channel"]
            ]
            association_kind = "track"
        primary = unique_primary(candidate, allowed)
        if len(primary) > 1:
            exact_lyric = [note for note in primary if _lyric_equal(note, candidate)]
            if len(exact_lyric) == 1:
                primary = exact_lyric
        selected = None
        if len(primary) == 1:
            selected = primary[0]
        if selected is not None:
            unused.pop(selected.note_id, None)
            pairs.append((selected, candidate, association_kind))
        else:
            unmatched_candidates.append(candidate)

    # Pair remaining notes only when a single source/candidate candidate share
    # the same start and duration.  This distinguishes a pitch mutation while
    # refusing to invent a mapping for duplicate or unison notes.
    missing = list(unused.values())
    still_extra: list[_CandidateNote] = []
    for candidate in unmatched_candidates:
        same_time = [
            note for note in missing
            if note.quant_onset == candidate.onset_tick
            and note.quant_length == candidate.duration_tick
            and track_map.get(candidate.track_index, {}).get("source_track_index") == note.track_index
        ]
        global_same_time = [
            note for note in missing
            if note.quant_onset == candidate.onset_tick
            and note.quant_length == candidate.duration_tick
        ]
        choices = same_time if same_time else global_same_time
        same_slot = [
            extra for extra in unmatched_candidates
            if extra.onset_tick == candidate.onset_tick
            and extra.duration_tick == candidate.duration_tick
        ]
        if len(choices) == 1 and len(same_slot) == 1:
            selected = choices[0]
            missing.remove(selected)
            pairs.append((selected, candidate, "same_time_pitch_mutation"))
        else:
            still_extra.append(candidate)

    deviations: list[dict[str, Any]] = []
    missing_devs: list[dict[str, Any]] = []
    extra_devs: list[dict[str, Any]] = []
    pitch_devs: list[dict[str, Any]] = []
    lyric_devs: list[dict[str, Any]] = []
    for note in sorted(missing, key=lambda row: row.note_id):
        value = {
            "note_id": note.note_id,
            "track_index": note.track_index,
            "channel": note.channel,
            "pitch": note.pitch,
            "onset_tick_480ppqn": note.quant_onset,
            "duration_tick_480ppqn": note.quant_length,
            "lyric": note.lyric,
        }
        _bounded_append(missing_devs, value)
        _bounded_append(deviations, {"type": "missing_source_note", **value})
    for note in sorted(still_extra, key=lambda row: row.note_id):
        value = {
            "note_id": note.note_id,
            "track_index": note.track_index,
            "pitch": note.pitch,
            "onset_tick_480ppqn": note.onset_tick,
            "duration_tick_480ppqn": note.duration_tick,
            "lyric": note.lyric,
        }
        _bounded_append(extra_devs, value)
        _bounded_append(deviations, {"type": "extra_candidate_note", **value})
    for source, candidate, association in pairs:
        if source.pitch != candidate.pitch:
            value = {
                "source_note_id": source.note_id,
                "candidate_note_id": candidate.note_id,
                "source_pitch": source.pitch,
                "candidate_pitch": candidate.pitch,
                "onset_tick_480ppqn": candidate.onset_tick,
                "duration_tick_480ppqn": candidate.duration_tick,
            }
            _bounded_append(pitch_devs, value)
            _bounded_append(deviations, {"type": "pitch_mismatch", **value})
        if not _lyric_equal(source, candidate):
            value = {
                "source_note_id": source.note_id,
                "candidate_note_id": candidate.note_id,
                "source_lyric": source.lyric,
                "candidate_lyric": candidate.lyric,
                "onset_tick_480ppqn": candidate.onset_tick,
                "duration_tick_480ppqn": candidate.duration_tick,
            }
            _bounded_append(lyric_devs, value)
            _bounded_append(deviations, {"type": "lyric_mismatch", **value})

    unknown_lyrics = sum(1 for note in source_notes if note.lyric is None)
    filled_lyrics = sum(
        1 for source, candidate, _association in pairs
        if source.lyric is None and candidate.lyric is not None
    )
    return {
        "pairs": pairs,
        "unmatched_source": missing,
        "unmatched_candidate": still_extra,
        "missing_deviation_count": len(missing),
        "extra_deviation_count": len(still_extra),
        "pitch_mismatch_count": len(pitch_devs),
        "lyric_mismatch_count": len(lyric_devs),
        "missing_deviations": missing_devs,
        "extra_deviations": extra_devs,
        "pitch_deviations": pitch_devs,
        "lyric_deviations": lyric_devs,
        "note_deviations": deviations,
        "unknown_source_lyric_count": unknown_lyrics,
        "filled_unknown_lyric_count": filled_lyrics,
    }


def _timing_comparison(source: dict[str, Any], pairs: list[tuple[_SourceNote, _CandidateNote, str]],
                       source_count: int) -> dict[str, Any]:
    ppqn = source["ppqn"]
    deviations: list[dict[str, Any]] = []
    max_onset = 0.0
    max_end = 0.0
    max_duration = 0.0
    max_allowed = 0.0
    over = 0
    source_effective = _effective_tempos(source["tempos"])
    for source_note, candidate, _association in pairs:
        source_bpm_near = _tempo_at(source_effective, source_note.quant_onset)
        source_bpm_end = _tempo_at(
            source_effective, source_note.quant_onset + source_note.quant_length
        )
        onset_quant_ms = abs(
            source_note.onset_tick * _INTERNAL_PPQN / ppqn - source_note.quant_onset
        ) * 60_000.0 / source_bpm_near / _INTERNAL_PPQN
        end_quant_ms = abs(
            (source_note.onset_tick + source_note.duration_tick) * _INTERNAL_PPQN / ppqn
            - (source_note.quant_onset + source_note.quant_length)
        ) * 60_000.0 / source_bpm_end / _INTERNAL_PPQN
        onset_error = abs(source_note.onset_seconds - candidate.onset_seconds) * 1000.0
        end_error = abs(source_note.end_seconds - candidate.end_seconds) * 1000.0
        duration_error = abs(source_note.duration_seconds - candidate.duration_seconds) * 1000.0
        onset_allowed = _BASE_TIMING_TOLERANCE_MS + onset_quant_ms
        end_allowed = _BASE_TIMING_TOLERANCE_MS + end_quant_ms
        duration_allowed = _BASE_TIMING_TOLERANCE_MS + onset_quant_ms + end_quant_ms
        max_onset = max(max_onset, onset_error)
        max_end = max(max_end, end_error)
        max_duration = max(max_duration, duration_error)
        max_allowed = max(max_allowed, onset_allowed, end_allowed, duration_allowed)
        exceeds = onset_error > onset_allowed or end_error > end_allowed or duration_error > duration_allowed
        if exceeds:
            over += 1
            _bounded_append(deviations, {
                "source_note_id": source_note.note_id,
                "candidate_note_id": candidate.note_id,
                "onset_tick_480ppqn": candidate.onset_tick,
                "duration_tick_480ppqn": candidate.duration_tick,
                "source_onset_seconds": round(source_note.onset_seconds, 9),
                "candidate_onset_seconds": round(candidate.onset_seconds, 9),
                "onset_error_ms": round(onset_error, 6),
                "end_error_ms": round(end_error, 6),
                "duration_error_ms": round(duration_error, 6),
                "onset_allowed_ms": round(onset_allowed, 6),
                "end_allowed_ms": round(end_allowed, 6),
                "duration_allowed_ms": round(duration_allowed, 6),
            })
    return {
        "status": "differences" if over else "verified",
        "comparison": "seconds_from_each_side_actual_tempo_map",
        "base_tolerance_ms": _BASE_TIMING_TOLERANCE_MS,
        "quantization_included": True,
        "quantization_rule": "base tolerance plus source MIDI to 480 PPQN onset/end rounding, evaluated with the source-local tempo",
        "paired_note_count": len(pairs),
        "source_note_count": source_count,
        "max_onset_error_ms": round(max_onset, 6),
        "max_end_error_ms": round(max_end, 6),
        "max_duration_error_ms": round(max_duration, 6),
        "max_timing_error_ms": round(max(max_onset, max_end, max_duration), 6),
        "max_allowed_error_ms": round(max_allowed, 6),
        "over_tolerance_count": over,
        "within_tolerance": over == 0,
        "deviations": deviations,
    }


def _skeleton_candidate(format_name: str, row: Any, source_sha: str | None) -> dict[str, Any]:
    declared_path = row.get("candidate_path") if isinstance(row, Mapping) else None
    declared_sha = row.get("candidate_sha256") if isinstance(row, Mapping) else None
    return {
        "format": format_name,
        "status": "unknown",
        "candidate_path": declared_path if isinstance(declared_path, str) else None,
        "candidate_sha256": None,
        "declared_candidate_sha256": declared_sha if isinstance(declared_sha, str) else None,
        "source_sha256": source_sha,
        "parsed": False,
        "blockers": [],
        "warnings": [],
    }


def _claim_issues(row: Any, actual: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(row, Mapping):
        return []
    issues = []
    claims = {
        "verified_notes": actual["candidate_note_count"],
        "verified_lyrics": actual["candidate_explicit_lyric_count"],
        "tempo_verified": actual["tempo"]["effective_map_equivalent"],
        "timing_verified": actual["timing"]["within_tolerance"],
        "time_signature_verified": actual["time_signatures"]["equal"],
    }
    for key, observed in claims.items():
        if key not in row:
            continue
        declared = row.get(key)
        if key in {"verified_notes", "verified_lyrics"}:
            inconsistent = type(declared) is not int or declared != observed
        else:
            inconsistent = type(declared) is not bool or (declared is True and observed is False)
        if inconsistent:
            issues.append(_issue(
                "receipt_claim_mismatch",
                f"回执中的 {key} 与独立审计结果不一致。",
                field=key,
                declared=declared,
                observed=observed,
            ))
    if "bytes" in row and row.get("bytes") != actual["candidate_bytes"]:
        issues.append(_issue(
            "receipt_claim_mismatch",
            "回执中的候选字节数与实际文件不一致。",
            field="bytes",
            declared=row.get("bytes"),
            observed=actual["candidate_bytes"],
        ))
    return issues[:_MAX_ISSUES]


def _merge_issues(candidates: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        for issue in candidate.get(key, []):
            identity = _canonical(issue)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(copy.deepcopy(issue))
            if len(result) >= _MAX_ISSUES:
                return result
    return result


def _audit_candidate(format_name: str, row: Any, source: dict[str, Any],
                     source_sha: str, receipt_root: Path, plan_sha: str,
                     converters: Mapping[str, Any]) -> dict[str, Any]:
    result = _skeleton_candidate(format_name, row, source_sha)
    warnings: list[dict[str, Any]] = result["warnings"]
    blockers: list[dict[str, Any]] = result["blockers"]
    if not isinstance(row, Mapping):
        blockers.append(_issue("candidate_record_invalid", "候选记录不是对象。", format_name))
        return result
    declared_sha = row.get("candidate_sha256")
    if not isinstance(declared_sha, str) or not _SHA256_RE.fullmatch(declared_sha):
        blockers.append(_issue("candidate_sha256_invalid", "候选记录中的 SHA-256 无效。", format_name))
        return result
    declared_path_value = row.get("candidate_path")
    if not isinstance(declared_path_value, str) or not declared_path_value:
        blockers.append(_issue("candidate_path_invalid", "候选记录中的路径无效。", format_name))
        return result
    declared_path = _resolved_path(declared_path_value, "candidate_path")
    canonical_path = _exports.candidate_path_for(receipt_root, plan_sha, format_name, declared_sha)
    result["declared_candidate_path"] = declared_path_value
    result["candidate_path"] = str(canonical_path)
    path_matches = declared_path == canonical_path and canonical_path.is_relative_to(receipt_root)
    result["candidate_path_matches"] = path_matches
    if not path_matches:
        blockers.append(_issue(
            "candidate_path_mismatch",
            "候选路径不符合回执计划、候选哈希和输出根目录共同确定的路径。",
            format_name,
            declared_path=str(declared_path),
            expected_path=str(canonical_path),
        ))
        return result
    if not _io_path(canonical_path).is_file():
        blockers.append(_issue("candidate_missing", "候选文件不存在或不是普通文件。", format_name))
        return result
    try:
        actual_sha = _stable_sha(canonical_path, label=format_name.upper() + " 候选")
    except ValueError as error:
        blockers.append(_issue("candidate_unreadable", str(error), format_name))
        return result
    result["candidate_sha256"] = actual_sha
    result["candidate_sha256_matches"] = actual_sha == declared_sha
    if actual_sha != declared_sha:
        blockers.append(_issue(
            "candidate_sha256_mismatch",
            "候选文件的 SHA-256 与回执声明不一致。",
            format_name,
            declared_sha256=declared_sha,
            actual_sha256=actual_sha,
        ))
        return result

    declared_media = row.get("media")
    instrumental_declared = row.get("instrumental_included")
    if format_name == "svp":
        expected_media = None
        if isinstance(declared_media, Mapping):
            expected_media = dict(declared_media)
        elif instrumental_declared is True:
            expected_media = {"included": True}
        elif instrumental_declared is False:
            expected_media = {"included": False}
        try:
            media_result = agent_media.inspect_svp_media(
                canonical_path, allowed_roots=[receipt_root], require_relative=True,
                expected=expected_media,
            )
            result["instrumental"] = media_result
            if instrumental_declared is True and media_result.get("included") is not True:
                blockers.append(_issue(
                    "instrumental_missing", "候选声明含伴奏，但独立回读未发现伴奏轨。",
                    format_name,
                ))
            elif instrumental_declared is False and media_result.get("included") is True:
                blockers.append(_issue(
                    "instrumental_unexpected", "候选声明未含伴奏，但独立回读发现了伴奏轨。",
                    format_name,
                ))
        except (ValueError, OSError) as error:
            blockers.append(_issue(
                getattr(error, "code", "instrumental_audit_failed"),
                "SVP 伴奏独立回读失败：" + str(error), format_name,
                details=getattr(error, "details", None),
            ))
    else:
        result["instrumental"] = {"included": False, "reason": row.get("instrumental_reason")}
        if instrumental_declared is True:
            blockers.append(_issue(
                "instrumental_unsupported", "非 SVP 候选不能包含伴奏。", format_name,
            ))
        elif row.get("instrumental_reason") == "format_not_supported":
            warnings.append(_issue(
                "instrumental_not_included_in_format",
                "该工程格式不包含伴奏；SVP 可生成可移动试听包。",
                format_name,
            ))

    converter = converters.get(format_name)
    if converter is None:
        blockers.append(_issue("candidate_parser_unavailable", "运行库未提供格式解析器。", format_name))
        return result
    try:
        project = converter.load(
            _io_path(canonical_path),
            {"import_pitch": False, "import_instrumental_track": False},
        )
        parsed = _parse_project(project, format_name)
        after_sha = _stable_sha(canonical_path, label=format_name.upper() + " 候选")
    except Exception as error:
        blockers.append(_issue("candidate_parse_failed", "候选工程实际解析失败：" + str(error), format_name))
        return result
    if after_sha != actual_sha:
        blockers.append(_issue(
            "candidate_changed_during_audit",
            "候选文件在解析期间发生变化。",
            format_name,
            before_sha256=actual_sha,
            after_sha256=after_sha,
        ))
        return result

    result["parsed"] = True
    result["engine"] = {
        "name": "LibreSVIP",
        "converter": type(converter).__name__,
        "version": getattr(converter, "_version_", None),
    }
    result["candidate_bytes"] = _io_path(canonical_path).stat().st_size
    candidate_notes = parsed["notes"]
    track_map = _track_mapping(source["notes"], candidate_notes)
    result["tracks"] = {
        "source": [
            {"track_index": track_index, "channel": channel,
             "note_count": sum(1 for note in source["notes"]
                               if note.track_index == track_index and note.channel == channel)}
            for track_index, channel in source["channels"]
        ],
        "candidate": parsed["tracks"],
        "association": {str(key): value for key, value in track_map.items()},
    }
    matched = _match_notes(source["notes"], candidate_notes, track_map)
    timing = _timing_comparison(source, matched["pairs"], len(source["notes"]))
    tempo = _tempo_comparison(source["tempos"], parsed["tempos"], source["notes"])
    signatures = _signature_comparison(source["signatures"], parsed["signatures"])
    result["counts"] = {
        "source_note_count": len(source["notes"]),
        "candidate_note_count": len(candidate_notes),
        "matched_note_count": len(matched["pairs"]),
        "source_explicit_lyric_count": sum(1 for note in source["notes"] if note.lyric is not None),
        "candidate_explicit_lyric_count": sum(1 for note in candidate_notes if note.lyric is not None),
        "unknown_source_lyric_count": matched["unknown_source_lyric_count"],
    }
    result["note_audit"] = {
        "matched_count": len(matched["pairs"]),
        "missing_count": matched["missing_deviation_count"],
        "extra_count": matched["extra_deviation_count"],
        "pitch_mismatch_count": matched["pitch_mismatch_count"],
        "lyric_mismatch_count": matched["lyric_mismatch_count"],
        "missing_deviations": matched["missing_deviations"],
        "extra_deviations": matched["extra_deviations"],
        "pitch_deviations": matched["pitch_deviations"],
        "lyric_deviations": matched["lyric_deviations"],
        "bounded_note_deviations": matched["note_deviations"],
    }
    result["timing"] = timing
    result["tempo"] = tempo
    result["time_signatures"] = signatures
    result["acoustic_or_phonetic_verified"] = False

    if matched["missing_deviation_count"]:
        blockers.append(_issue(
            "missing_note", "候选工程缺少源 MIDI 音符。", format_name,
            count=matched["missing_deviation_count"],
            sample=matched["missing_deviations"][:8],
        ))
    if matched["extra_deviation_count"]:
        blockers.append(_issue(
            "extra_note", "候选工程含源 MIDI 中不存在的音符。", format_name,
            count=matched["extra_deviation_count"],
            sample=matched["extra_deviations"][:8],
        ))
    if matched["pitch_mismatch_count"]:
        blockers.append(_issue(
            "pitch_mismatch", "候选工程音高与源 MIDI 不一致。", format_name,
            count=matched["pitch_mismatch_count"],
            sample=matched["pitch_deviations"][:8],
        ))
    if matched["lyric_mismatch_count"]:
        blockers.append(_issue(
            "lyric_mismatch", "候选工程歌词文本与源 MIDI 明确歌词不一致。", format_name,
            count=matched["lyric_mismatch_count"],
            sample=matched["lyric_deviations"][:8],
        ))
    if not timing["within_tolerance"]:
        blockers.append(_issue(
            "timing_seconds_mismatch",
            "按双方实际 tempo map 换算后，候选音符秒级时序超出容差。",
            format_name,
            over_tolerance_count=timing["over_tolerance_count"],
            max_timing_error_ms=timing["max_timing_error_ms"],
            max_allowed_error_ms=timing["max_allowed_error_ms"],
            sample=timing["deviations"][:8],
        ))
    if not tempo["effective_map_equivalent"]:
        blockers.append(_issue(
            "tempo_content_mismatch",
            "候选 tempo 表的有效 BPM 与源 MIDI 不一致。",
            format_name,
            effective_mismatch_count=tempo["effective_mismatch_count"],
            sample=tempo["effective_mismatches"][:8],
        ))
    elif not tempo["raw_structure_equivalent"]:
        warnings.append(_issue(
            "tempo_table_structure_normalized",
            "候选 tempo 表的锚点结构被归一化，但有效 BPM 时间线与源一致。",
            format_name,
        ))
    if tempo["duplicate_candidate_positions"]:
        warnings.append(_issue(
            "tempo_duplicate_position_anchors",
            "候选 tempo 表含同一位置的多个锚点；审计按解析器实际生效的最后一个锚点计算。",
            format_name,
            positions_480ppqn=tempo["duplicate_candidate_positions"],
        ))
    if not signatures["equal"]:
        blockers.append(_issue(
            "time_signature_mismatch",
            "候选工程拍号与位置不能独立复现源 MIDI。",
            format_name,
        ))
    if matched["unknown_source_lyric_count"]:
        warnings.append(_issue(
            "source_lyric_unknown",
            "部分源 MIDI 音符没有明确歌词；候选填词只能证明导出结果，不能证明原词语义。",
            format_name,
            unknown_count=matched["unknown_source_lyric_count"],
            filled_count=matched["filled_unknown_lyric_count"],
        ))
    if any(value.get("ambiguous") for value in track_map.values()):
        warnings.append(_issue(
            "track_association_ambiguous",
            "候选声部与源轨道/通道的映射存在并列候选；未伪造唯一声部归属。",
            format_name,
        ))
    elif any(value.get("association") == "unsupported" for value in track_map.values()):
        warnings.append(_issue(
            "track_association_unsupported",
            "至少一个候选声部无法与源轨道/通道建立唯一映射。",
            format_name,
        ))
    elif any(value.get("association") == "content_or_title" for value in track_map.values()):
        warnings.append(_issue(
            "track_identity_normalized",
            "源轨道/通道身份未原样保留；声部关联由独立音符内容建立。",
            format_name,
        ))
    ambiguous_lyrics = sum(1 for note in source["notes"] if note.lyric_ambiguous)
    if ambiguous_lyrics:
        warnings.append(_issue(
            "source_lyric_association_ambiguous",
            "源 MIDI 同一 onset 存在不同歌词事件；歌词归属保持歧义。",
            format_name,
            count=ambiguous_lyrics,
        ))
    if any(note.overlap_ambiguous for note in source["notes"]):
        warnings.append(_issue(
            "source_polyphony_ambiguous",
            "源 MIDI 含同轨同通道同音高重叠音符，相关归属保持歧义。",
            format_name,
        ))

    actual_for_claims = {
        "candidate_note_count": len(candidate_notes),
        "candidate_explicit_lyric_count": sum(1 for note in candidate_notes if note.lyric is not None),
        "candidate_bytes": result["candidate_bytes"],
        "tempo": tempo,
        "timing": timing,
        "time_signatures": signatures,
    }
    blockers.extend(_claim_issues(row, actual_for_claims))

    has_unknown = matched["unknown_source_lyric_count"] > 0
    if not parsed["tempos"]:
        has_unknown = True
        warnings.append(_issue("tempo_metadata_unknown", "候选工程未提供可审计的 tempo 元数据。", format_name))
    if not parsed["signatures"]:
        has_unknown = True
        warnings.append(_issue("time_signature_metadata_unknown", "候选工程未提供可审计拍号元数据。", format_name))
    if blockers:
        result["status"] = "differences"
    elif has_unknown:
        result["status"] = "unknown"
    else:
        result["status"] = "verified"
    result["blockers"] = blockers[:_MAX_ISSUES]
    result["warnings"] = warnings[:_MAX_ISSUES]
    return result


def _base_result(source_path: Path, source_sha: str | None, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": _KIND,
        "status": "unknown",
        "source_sha256": source_sha,
        "source": {
            "path": str(source_path),
            "sha256": source_sha,
        },
        "receipt": None if receipt is None else {
            "kind": receipt.get("kind"),
            "plan_sha256": receipt.get("plan_sha256"),
            "declared_source_sha256": receipt.get("source_sha256"),
            "stored_sha256": receipt.get("receipt_sha256"),
            "source_sha256": source_sha,
        },
        "candidates": [],
        "blockers": [],
        "warnings": [],
        "inference_performed": False,
    }


def audit_export(source_path: Any, receipt: Any, export_runtime: Any = None,
                 output_root: Any = None, cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Audit a completed export without trusting its self-reported checks.

    ``output_root`` is used only for the read-only LibreSVIP runtime cache.  It
    never receives a modified source or candidate.  When omitted, an isolated
    temporary cache is used and removed before return.
    """
    check_cancel = _checker(cancel)
    check_cancel()
    source = _resolved_path(source_path, "source_path")
    source_raw, source_sha = _read_stable_bytes(source, label="源 MIDI", maximum=_MAX_SOURCE_BYTES)
    parsed_source = _parse_source(source_raw)
    saved = _extract_receipt(receipt)
    result = _base_result(source, source_sha, saved)
    result["source"].update({
        "declared_path": saved.get("source_path"),
        "ppqn": parsed_source["ppqn"],
        "midi_type": parsed_source["midi_type"],
        "note_count": len(parsed_source["notes"]),
        "tracks_with_notes": parsed_source["tracks_with_notes"],
    })

    digest_ok, stored_receipt_sha, computed_receipt_sha = _receipt_digest_status(saved)
    result["receipt"].update({
        "computed_sha256": computed_receipt_sha,
        "sha256_matches": digest_ok,
    })
    formats = saved.get("formats")
    rows = saved.get("candidates")
    plan_sha = saved.get("plan_sha256")
    receipt_root_value = saved.get("output_root")
    if not isinstance(formats, list) or not formats or any(
        not isinstance(fmt, str) or fmt not in _FORMATS for fmt in formats
    ) or len(set(formats)) != len(formats):
        raise ValueError("receipt.formats 必须是唯一、非空的 svp/ustx/vsqx 列表。")
    if not isinstance(rows, list) or len(rows) != len(formats):
        raise ValueError("receipt.candidates 必须与 formats 一一对应。")
    if not isinstance(plan_sha, str) or not _SHA256_RE.fullmatch(plan_sha):
        raise ValueError("receipt.plan_sha256 无效。")
    receipt_root = _resolved_path(receipt_root_value, "receipt.output_root")
    result["receipt"]["output_root"] = str(receipt_root)

    preflight_blockers: list[dict[str, Any]] = []
    if saved.get("source_path") != str(source):
        preflight_blockers.append(_issue(
            "source_path_mismatch", "receipt 绑定的源 MIDI 路径与审计输入不一致。",
            declared=str(saved.get("source_path")), actual=str(source),
        ))
    if saved.get("source_sha256") != source_sha:
        preflight_blockers.append(_issue(
            "source_sha256_mismatch", "源 MIDI 的实际 SHA-256 与 receipt 不一致。",
            declared=saved.get("source_sha256"), actual=source_sha,
        ))
    if not digest_ok:
        preflight_blockers.append(_issue(
            "receipt_sha256_mismatch", "receipt 的完整规范摘要不匹配，拒绝信任其内容。",
            declared=stored_receipt_sha, actual=computed_receipt_sha,
        ))
    row_formats = [row.get("format") if isinstance(row, Mapping) else None for row in rows]
    if row_formats != formats:
        preflight_blockers.append(_issue(
            "candidate_order_mismatch", "receipt.candidates 的格式顺序与 formats 不一致。"
        ))
    if preflight_blockers:
        result["candidates"] = [
            _skeleton_candidate(formats[index], row, source_sha)
            for index, row in enumerate(rows)
        ]
        for candidate in result["candidates"]:
            candidate["blockers"] = copy.deepcopy(preflight_blockers)
        result["blockers"] = preflight_blockers
        result["warnings"] = []
        unsigned = {key: value for key, value in result.items() if key != "audit_sha256"}
        result["audit_sha256"] = _digest(unsigned)
        return result

    check_cancel()
    try:
        runtime = _resolve_runtime(export_runtime)
    except (FileNotFoundError, ValueError, ImportError, OSError) as error:
        blocker = _issue(
            "export_runtime_unavailable",
            "只读 LibreSVIP 运行库不可用或无法安全加载：" + str(error),
        )
        result["candidates"] = [
            _skeleton_candidate(fmt, row, source_sha)
            for fmt, row in zip(formats, rows)
        ]
        for candidate in result["candidates"]:
            candidate["blockers"] = [copy.deepcopy(blocker)]
        result["blockers"] = [blocker]
        result["warnings"] = []
        unsigned = {key: value for key, value in result.items() if key != "audit_sha256"}
        result["audit_sha256"] = _digest(unsigned)
        return result

    candidates: list[dict[str, Any]] = []
    with _audit_scratch(output_root) as scratch:
        try:
            with _exports._runtime_session(runtime, scratch) as converters:
                result["runtime"] = {
                    "path": str(runtime),
                    "engine": "LibreSVIP",
                    "read_only_source": True,
                }
                for index, fmt in enumerate(formats):
                    check_cancel()
                    candidates.append(_audit_candidate(
                        fmt, rows[index], parsed_source, source_sha, receipt_root,
                        plan_sha, converters,
                    ))
                check_cancel()
        except (FileNotFoundError, ValueError, ImportError, OSError) as error:
            blocker = _issue(
                "export_runtime_unavailable",
                "只读 LibreSVIP 运行库不可用或无法安全加载：" + str(error),
            )
            result["candidates"] = [
                _skeleton_candidate(fmt, row, source_sha)
                for fmt, row in zip(formats, rows)
            ]
            for candidate in result["candidates"]:
                candidate["blockers"] = [copy.deepcopy(blocker)]
            result["blockers"] = [blocker]
            result["warnings"] = []
            unsigned = {key: value for key, value in result.items() if key != "audit_sha256"}
            result["audit_sha256"] = _digest(unsigned)
            return result

    result["candidates"] = candidates
    result["blockers"] = _merge_issues(candidates, "blockers")
    result["warnings"] = _merge_issues(candidates, "warnings")
    loss_codes = saved.get("conversion_losses")
    if isinstance(loss_codes, list):
        for loss in loss_codes:
            if not isinstance(loss, Mapping) or not isinstance(loss.get("code"), str):
                continue
            result["warnings"].append(_issue(
                "declared_conversion_loss",
                "导出回执声明了已知转换损失：" + str(loss.get("message", loss.get("code"))),
                declared_code=loss["code"],
            ))
            if len(result["warnings"]) >= _MAX_ISSUES:
                break
    if any(candidate["status"] == "unknown" for candidate in candidates):
        result["status"] = "unknown"
    elif any(candidate["status"] == "differences" for candidate in candidates):
        result["status"] = "differences"
    else:
        result["status"] = "verified"
    result["acoustic_or_phonetic_verified"] = False
    result["inference_performed"] = False
    unsigned = {key: value for key, value in result.items() if key != "audit_sha256"}
    result["audit_sha256"] = _digest(unsigned)
    return result


__all__ = ["audit_export"]
