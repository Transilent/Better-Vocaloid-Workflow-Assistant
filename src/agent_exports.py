"""Plan and materialize editable vocal projects from corrected MIDI.

This worker has no MCP, Qt, semantic-model, network, synthesis, or publishing
dependency. MIDI is the source of truth. LibreSVIP converts an imported MIDI
project into independent SVP/USTX/VSQX candidates, which are reloaded and
checked before publication. Ordinary MIDI controllers are never presented as
editor automation.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any
import unicodedata
import xml.etree.ElementTree as ET

import mido

from common import native_path


class ExportError(ValueError):
    component = "agent_exports"
    retryable = False
    remediation = ["vwa_score_review", "vwa_score_patch_plan"]

    def __init__(self, code: str, message: str, *, details: Any = None,
                 retryable: bool | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        if retryable is not None:
            self.retryable = retryable


def _io_path(path: Any) -> Path:
    return Path(native_path(path))


_SCHEMA_VERSION = 1
_PLAN_KIND = "vwa-export-plan"
_RECEIPT_KIND = "vwa-export-receipt"
_LIBRESVIP_PPQN = 480
_DEFAULT_BPM = 120.0
_DEFAULT_PHONEME = "la"
_DEFAULT_TEMPO = 500_000
_MAX_TICK = 0x0FFFFFFF
_RUNTIME_ENV = "VWA_MCP_EXPORT_RUNTIME"
_RUNTIME_LOCK = threading.RLock()

_FORMATS = {
    "svp": {
        "converter": "libresvip.plugins.svp.synthv_studio_converter.SynthVStudioConverter",
        "label": "Synthesizer V Studio editable project",
        "editable": True,
    },
    "ustx": {
        "converter": "libresvip.plugins.ustx.ustx_converter.OpenUtauConverter",
        "label": "OpenUtau editable project",
        "editable": True,
    },
    "vsqx": {
        "converter": "libresvip.plugins.vsqx.vsqx_converter.VsqxConverter",
        "label": "VOCALOID VSQX editable project",
        "editable": True,
    },
}
_OPTION_KEYS = frozenset({
    "language", "default_bpm", "svp_version", "include_instrumental",
    "instrumental_path", "track_names", "project_name",
})
_LANGUAGES = frozenset({"zh", "ja"})
_SVP_VERSIONS = {"1.9": 100, "1.11": 135, "2.0": 182}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("值必须只包含有限的 JSON 数据。") from error


def _digest(value: Any) -> str:
    return _sha256(_canonical(value).encode("utf-8"))


def _canonical_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options = {"ensure_ascii": False, "allow_nan": False, "sort_keys": True}
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    text = json.dumps(value, **options)
    if pretty:
        text += "\n"
    return text.encode("utf-8")


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _finite_number(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(label + "必须是有限数值。")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须在 {minimum:g} 到 {maximum:g} 之间。")
    return number


def _normalise_formats(formats: Any) -> list[str]:
    if formats is None:
        return ["svp"]
    if not isinstance(formats, (list, tuple)) or not formats:
        raise ValueError("formats 必须是非空列表，可含 svp、ustx、vsqx。")
    result: list[str] = []
    for value in formats:
        if not isinstance(value, str):
            raise ValueError("formats 中的格式必须是字符串。")
        fmt = value.strip().lower()
        if fmt not in _FORMATS:
            raise ValueError("不支持的导出格式：" + value)
        if fmt in result:
            raise ValueError("formats 不能包含重复格式。")
        result.append(fmt)
    return result


def _normalise_metadata_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ExportError("export_metadata_invalid", label + "必须是字符串。")
    text = _normalise_text(value).strip()
    if (not text and not allow_empty) or len(text) > 256 or "\x00" in text:
        raise ExportError("export_metadata_invalid", label + "必须是非空的短文本。")
    if any(char in text for char in "\r\n"):
        raise ExportError("export_metadata_invalid", label + "不能包含换行。")
    return text


def _normalise_options(options: Any) -> dict[str, Any]:
    if options is None:
        options = {}
    if not isinstance(options, Mapping):
        raise ValueError("options 必须是对象。")
    unknown = set(options) - _OPTION_KEYS
    if unknown:
        raise ValueError("options 含有未支持字段：" + ", ".join(sorted(map(str, unknown))))
    language = options.get("language", "zh")
    if not isinstance(language, str) or language not in _LANGUAGES:
        raise ValueError("options.language 必须是 zh 或 ja。")
    default_bpm = _finite_number(
        options.get("default_bpm", _DEFAULT_BPM),
        "options.default_bpm", minimum=1.0, maximum=1000.0,
    )
    svp_version = options.get("svp_version", "1.9")
    if not isinstance(svp_version, str) or svp_version not in _SVP_VERSIONS:
        raise ValueError("options.svp_version 必须是 1.9、1.11 或 2.0。")
    result: dict[str, Any] = {
        "language": language,
        "default_bpm": default_bpm,
        "svp_version": svp_version,
    }
    # Preserve the original option object shape for callers that do not use the
    # accompaniment/metadata extension. Public control adds this switch only
    # after resolving an authorized artifact or source job.
    if not any(key in options for key in (
            "include_instrumental", "instrumental_path", "track_names", "project_name")):
        return result
    include_instrumental = options.get("include_instrumental", False)
    if type(include_instrumental) is not bool:
        raise ExportError("export_option_invalid", "options.include_instrumental 必须是布尔值。")
    instrumental_path = options.get("instrumental_path")
    if include_instrumental and (
            not isinstance(instrumental_path, str) or not instrumental_path.strip()
            or not Path(instrumental_path).is_absolute()):
        raise ExportError(
            "instrumental_path_required",
            "include_instrumental=true 需要控制层解析后的内部 instrumental_path。",
            details={"public_fields": ["instrumental_artifact_id", "source_job_id"]},
        )
    if not include_instrumental and instrumental_path is not None:
        raise ExportError("instrumental_option_conflict",
                          "instrumental_path 只能在 include_instrumental=true 时传入。")
    track_names = options.get("track_names")
    if track_names is not None:
        if not isinstance(track_names, (list, tuple)) or not track_names:
            raise ExportError("export_metadata_invalid", "options.track_names 必须是非空字符串列表。")
        track_names = [_normalise_metadata_text(value, "options.track_names[]") for value in track_names]
    project_name = options.get("project_name")
    if project_name is not None:
        project_name = _normalise_metadata_text(project_name, "options.project_name")
        if "/" in project_name or "\\" in project_name:
            raise ExportError("export_metadata_invalid", "options.project_name 不能包含路径分隔符。")
    result["include_instrumental"] = include_instrumental
    if instrumental_path is not None:
        result["instrumental_path"] = instrumental_path
    if track_names is not None:
        result["track_names"] = track_names
    if project_name is not None:
        result["project_name"] = project_name
    return result


def _source_path(path: Any) -> Path:
    if not isinstance(path, (str, bytes, os.PathLike)):
        raise ValueError("MIDI 路径必须是字符串或路径对象。")
    try:
        source = Path(path).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("MIDI 路径无效。") from error
    if source.suffix.lower() not in (".mid", ".midi"):
        raise ValueError("Export worker 的源文件必须是修正后的 .mid/.midi；SVP 输入仅供只读检查。")
    if not _io_path(source).is_file():
        raise ValueError("MIDI 文件不存在或不是普通文件：" + str(source))
    return source


def _read_stable(source: Path) -> tuple[bytes, str]:
    try:
        with _io_path(source).open("rb") as stream:
            raw = stream.read()
        with _io_path(source).open("rb") as stream:
            after = stream.read()
    except OSError as error:
        raise ValueError("无法读取 MIDI 文件：" + str(error)) from error
    if not raw:
        raise ValueError("MIDI 文件为空。")
    if raw != after:
        raise ValueError("规划或导出期间源 MIDI 发生变化，请重试。")
    return raw, _sha256(raw)


def _load_midi(raw: bytes) -> Any:
    try:
        midi = mido.MidiFile(file=io.BytesIO(raw), charset="utf8", clip=False)
    except (OSError, EOFError, ValueError, UnicodeError, IndexError) as error:
        raise ValueError("无法解析 MIDI 文件：" + str(error)) from error
    if midi.type not in (0, 1):
        raise ValueError("不支持 MIDI format 2；请先合并为统一时间线。")
    if type(midi.ticks_per_beat) is not int or not 0 < midi.ticks_per_beat < 32768:
        raise ValueError("MIDI ticks-per-quarter-note 超出支持范围。")
    if not midi.tracks:
        raise ValueError("MIDI 不含轨道。")
    return midi


def _parse_evidence(raw: bytes, ticks_per_beat: int) -> dict[str, Any]:
    midi = _load_midi(raw)
    if midi.ticks_per_beat != ticks_per_beat:
        raise ValueError("源 MIDI 在解析期间发生变化。")
    source_notes: list[dict[str, Any]] = []
    tempo_events: list[dict[str, Any]] = []
    time_signature_events: list[dict[str, Any]] = []
    channel_count = 0
    controller_count = 0
    pitchwheel_count = 0
    program_count = 0
    marker_count = 0
    lyric_event_count = 0
    tracks_with_notes = 0

    for track_index, track in enumerate(midi.tracks):
        tick = 0
        lyrics: dict[int, str] = {}
        active: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        has_notes = False
        for message in track:
            if type(message.time) is not int or not 0 <= message.time <= _MAX_TICK:
                raise ValueError(f"MIDI 轨道 {track_index} 含无效 delta tick。")
            tick += message.time
            if message.is_meta:
                if message.type == "lyrics":
                    lyric_event_count += 1
                    try:
                        text = message.text
                    except (AttributeError, UnicodeError) as error:
                        raise ValueError(f"MIDI 轨道 {track_index} 歌词不是有效 UTF-8。") from error
                    lyrics[tick] = _normalise_text(str(text))
                elif message.type == "set_tempo":
                    tempo = int(message.tempo)
                    tempo_events.append({
                        "track_index": track_index,
                        "tick": tick,
                        "tempo_microseconds_per_quarter": tempo,
                        "bpm": round(60_000_000.0 / tempo, 3),
                    })
                elif message.type == "time_signature":
                    time_signature_events.append({
                        "track_index": track_index,
                        "tick": tick,
                        "numerator": int(message.numerator),
                        "denominator": int(message.denominator),
                    })
                elif message.type == "marker":
                    marker_count += 1
                continue
            if message.type == "note_on" and message.velocity > 0:
                active[(int(message.channel), int(message.note))].append(
                    (tick, int(message.velocity))
                )
                channel_count += 1
                has_notes = True
                continue
            if message.type == "control_change":
                controller_count += 1
                continue
            if message.type == "pitchwheel":
                pitchwheel_count += 1
                continue
            if message.type == "program_change":
                program_count += 1
                continue
            is_note_off = message.type == "note_off" or (
                message.type == "note_on" and message.velocity == 0
            )
            if not is_note_off:
                continue
            key = (int(message.channel), int(message.note))
            opens = active.get(key, [])
            close = [row for row in opens if row[0] != tick]
            keep = [row for row in opens if row[0] == tick]
            for onset, velocity in close:
                source_notes.append({
                    "track_index": track_index,
                    "channel": int(message.channel),
                    "pitch": int(message.note),
                    "onset_tick": onset,
                    "duration_tick": tick - onset,
                    "velocity": velocity,
                    "explicit_lyric": onset in lyrics,
                    "lyric": lyrics.get(onset, _DEFAULT_PHONEME),
                })
            if keep:
                active[key] = keep
            else:
                active.pop(key, None)
        if any(active.values()):
            raise ValueError(f"MIDI 轨道 {track_index} 含未闭合音符；请先修正源文件。")
        if has_notes:
            tracks_with_notes += 1

    if not source_notes:
        raise ValueError("修正后的 MIDI 不含可导出音符。")
    source_notes.sort(key=lambda row: (
        row["track_index"], row["onset_tick"], row["channel"], row["pitch"],
        row["duration_tick"], row["lyric"],
    ))
    tempo_events.sort(key=lambda row: (row["tick"], row["track_index"]))
    time_signature_events.sort(key=lambda row: (row["tick"], row["track_index"]))

    expected_notes: list[tuple[int, int, int, str]] = []
    max_onset_error_internal = 0.0
    max_duration_error_internal = 0.0
    raw_evidence: list[tuple[int, int, int, int, int, int]] = []
    for row in source_notes:
        start_exact = row["onset_tick"] * _LIBRESVIP_PPQN / ticks_per_beat
        length_exact = row["duration_tick"] * _LIBRESVIP_PPQN / ticks_per_beat
        start = int(round(start_exact))
        length = int(round(length_exact))
        if length <= 0:
            raise ValueError("MIDI 音符量化到 LibreSVIP 的 480 PPQN 后长度为零；请调整过短音符。")
        lyric = _normalise_text(row["lyric"])
        expected_notes.append((row["pitch"], start, length, lyric))
        max_onset_error_internal = max(max_onset_error_internal, abs(start - start_exact))
        max_duration_error_internal = max(
            max_duration_error_internal, abs(length - length_exact)
        )
        raw_evidence.append((
            row["track_index"], row["channel"], row["pitch"], row["onset_tick"],
            row["duration_tick"], row["velocity"],
        ))

    expected_notes.sort()
    lyric_rows = [
        {
            "track_index": row["track_index"],
            "channel": row["channel"],
            "onset_tick": row["onset_tick"],
            "text": row["lyric"],
        }
        for row in source_notes if row["explicit_lyric"]
    ]
    unique_lyrics = sorted({_normalise_text(row["lyric"]) for row in source_notes})

    def max_error_ms(error_internal: float, tick: int) -> float:
        tempo = _DEFAULT_TEMPO
        for event in tempo_events:
            if event["tick"] > tick:
                break
            tempo = event["tempo_microseconds_per_quarter"]
        # A quarter note is 480 internal ticks; convert the rounding error to ms.
        return round(error_internal * tempo / (_LIBRESVIP_PPQN * 1000.0), 6)

    max_onset_ms = max(
        (max_error_ms(abs(round(t * _LIBRESVIP_PPQN / ticks_per_beat)
                            - t * _LIBRESVIP_PPQN / ticks_per_beat), t)
         for t in (row["onset_tick"] for row in source_notes)),
        default=0.0,
    )
    max_duration_ms = max(
        (max_error_ms(abs(round(row["duration_tick"] * _LIBRESVIP_PPQN / ticks_per_beat)
                            - row["duration_tick"] * _LIBRESVIP_PPQN / ticks_per_beat),
                      row["onset_tick"]) for row in source_notes),
        default=0.0,
    )
    samples = [
        {
            "pitch": pitch,
            "onset_internal_tick": start,
            "duration_internal_tick": length,
            "lyric": lyric,
        }
        for pitch, start, length, lyric in expected_notes[:12]
    ]
    return {
        "note_count": len(expected_notes),
        "explicit_lyric_note_count": len(lyric_rows),
        "missing_lyric_note_count": len(expected_notes) - len(lyric_rows),
        "lyric_event_count": lyric_event_count,
        "tracks_with_notes": tracks_with_notes,
        "note_on_count": channel_count,
        "controller_event_count": controller_count,
        "pitchwheel_event_count": pitchwheel_count,
        "program_change_count": program_count,
        "marker_count": marker_count,
        "tempos": tempo_events,
        "time_signatures": time_signature_events,
        "internal_ticks_per_quarter": _LIBRESVIP_PPQN,
        "timing_exact": max_onset_error_internal == 0 and max_duration_error_internal == 0,
        "max_onset_quantization_ms": max_onset_ms,
        "max_duration_quantization_ms": max_duration_ms,
        "notes_sha256": _digest([list(row) for row in expected_notes]),
        "raw_notes_sha256": _digest([list(row) for row in raw_evidence]),
        "lyrics_sha256": _digest(lyric_rows),
        "unique_lyrics": unique_lyrics,
        "note_samples": samples,
        "lyric_samples": lyric_rows[:24],
        "expected_notes": [list(row) for row in expected_notes],
    }


def _format_targets(formats: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "format": fmt,
            "extension": "." + fmt,
            "label": _FORMATS[fmt]["label"],
            "converter": _FORMATS[fmt]["converter"],
            "editable": _FORMATS[fmt]["editable"],
        }
        for fmt in formats
    ]


def _conversion_losses(evidence: dict[str, Any], formats: list[str]) -> list[dict[str, Any]]:
    losses = [
        {
            "code": "midi_controllers_not_automation",
            "count": evidence["controller_event_count"] + evidence["pitchwheel_event_count"],
            "message": "普通 MIDI controller 与 pitchwheel 不会自动变成 SVP/USTX/VSQX 自动化曲线。",
        },
        {
            "code": "midi_velocity_not_mapped",
            "message": "LibreSVIP 音符合同固定速度值；源 MIDI note velocity 不写入目标音符。",
        },
        {
            "code": "other_midi_metadata_not_mapped",
            "count": evidence["program_change_count"] + evidence["marker_count"],
            "message": "program change、marker、版权等非乐谱元数据不保证保留。",
        },
        {
            "code": "channel_layout_may_split",
            "message": "MIDI 每轨的多个通道可能拆成独立目标声部；只保证音符与歌词内容，不保证轨道标题和通道编号原样。",
        },
    ]
    if not evidence["timing_exact"]:
        losses.append({
            "code": "timing_quantized_to_480_ppqn",
            "purpose": "LibreSVIP 内部时间分辨率",
            "max_onset_error_ms": evidence["max_onset_quantization_ms"],
            "max_duration_error_ms": evidence["max_duration_quantization_ms"],
            "message": "原始 PPQN 会量化到 480 PPQN；损失以保守上界记录。",
        })
    if evidence["missing_lyric_note_count"]:
        losses.append({
            "code": "missing_lyric_filled",
            "count": evidence["missing_lyric_note_count"],
            "replacement": _DEFAULT_PHONEME,
            "message": "没有同一 onset 歌词事件的音符使用 LibreSVIP 默认歌词。",
        })
    if "vsqx" in formats:
        losses.append({
            "code": "vsqx_lyrics_restored_after_dump",
            "message": "VSQX 转换器会把中文/日文歌词改为读音；本 worker 在回读前恢复原歌词文本，发音/音素仍由转换器推导。",
        })
    return losses



def _instrumental_plan(options: Mapping[str, Any], formats: list[str]) -> dict[str, Any]:
    include = options.get("include_instrumental", False) is True
    if not include:
        return {
            "requested": False,
            "included": False,
            "reason": "not_requested",
            "supported_formats": ["svp"],
            "unsupported_formats": [fmt for fmt in formats if fmt != "svp"],
        }
    value = options.get("instrumental_path")
    if not isinstance(value, str) or not value:
        raise ExportError("instrumental_path_required", "伴奏导出缺少内部 instrumental_path。")
    try:
        from agent_media import audio_info, file_sha256
        path = Path(value).expanduser().resolve()
        if not _io_path(path).is_file() or _io_path(path).is_symlink():
            raise ExportError("instrumental_missing", "控制层解析的伴奏文件不存在。",
                              details={"path": str(path)})
        identity = file_sha256(path)
        media = audio_info(path)
    except ExportError:
        raise
    except (ValueError, OSError) as error:
        code = getattr(error, "code", "instrumental_unreadable")
        raise ExportError(code, str(error), details=getattr(error, "details", None)) from error
    return {
        "requested": True,
        "included": "svp" in formats,
        "reason": "included_in_svp" if "svp" in formats else "format_not_supported",
        "source_path": str(path),
        "sha256": identity["sha256"],
        "bytes": identity["bytes"],
        "duration_seconds": media["duration_seconds"],
        "sample_rate": media["sample_rate"],
        "channels": media["channels"],
        "format": media["format"],
        "supported_formats": ["svp"],
        "unsupported_formats": [fmt for fmt in formats if fmt != "svp"],
    }


def _monophonic_conflicts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        details = value.get("details")
        if isinstance(details, Mapping) and value.get("code") == "monophonic_overlap":
            rows = details.get("conflicts", [])
            return [dict(row) for row in rows if isinstance(row, Mapping)][:100]
        rows = value.get("conflicts")
        if rows is None and value.get("has_conflicts"):
            rows = [value]
        if rows is None:
            return []
    else:
        rows = value
    if not isinstance(rows, (list, tuple)):
        raise ExportError("monophonic_preflight_invalid", "monophonic_conflicts 返回了未识别结构。")
    return [dict(row) if isinstance(row, Mapping) else {"detail": row} for row in rows[:100]]


def _monophonic_preflight(source: Path) -> dict[str, Any]:
    try:
        from agent_monophony import monophonic_conflicts
    except ModuleNotFoundError:
        # This module is owned by a separate worker. Once present, every plan
        # uses its target-track/channel rules and blocks before conversion.
        return {
            "status": "unavailable",
            "code": "monophonic_preflight_unavailable",
            "message": "单声部预检模块尚未接入；当前计划不能证明跨音高重叠已排除。",
            "limit": 100,
        }
    from agent_score import read_score
    score = read_score(source)
    try:
        result = monophonic_conflicts(score, limit=100)
        conflicts = _monophonic_conflicts(result)
    except ExportError:
        raise
    except ValueError as error:
        if getattr(error, "code", None) == "monophonic_overlap":
            raise ExportError(
                "monophonic_overlap", str(error),
                details=getattr(error, "details", {"conflicts": []}),
            ) from error
        raise ExportError("monophonic_preflight_failed", str(error)) from error
    if conflicts:
        details = {"code": "monophonic_overlap", "conflicts": conflicts,
                   "conflict_count": len(conflicts), "limit": 100}
        raise ExportError(
            "monophonic_overlap",
            "导出前发现目标轨道或声部内存在重叠音符；请先修订乐谱。",
            details=details,
        )
    return {"status": "passed", "conflict_count": 0, "limit": 100}

def plan_export(path: Any, formats: Any = None, options: Any = None) -> dict[str, Any]:
    """Validate corrected MIDI and return a stable, read-only export plan."""
    source = _source_path(path)
    normal_formats = _normalise_formats(formats)
    normal_options = _normalise_options(options)
    raw, source_sha = _read_stable(source)
    midi = _load_midi(raw)
    evidence = _parse_evidence(raw, midi.ticks_per_beat)
    monophonic = _monophonic_preflight(source)
    instrumental = _instrumental_plan(normal_options, normal_formats)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": _PLAN_KIND,
        "source_path": str(source),
        "source_sha256": source_sha,
        "source_bytes": len(raw),
        "source_format": "midi",
        "ticks_per_beat": midi.ticks_per_beat,
        "formats": normal_formats,
        "options": normal_options,
        "timeline_adapter": "vwa-native-clock-v1",
        "targets": _format_targets(normal_formats),
        "expected": evidence,
        "monophonic_preflight": monophonic,
        "instrumental": instrumental,
        "conversion_losses": _conversion_losses(evidence, normal_formats),
        "runtime_requirements": {
            "environment": _RUNTIME_ENV,
            "argument": "export_runtime",
            "converter": "LibreSVIP",
            "source_runtime_bundled": False,
            "config_cache": "isolated under output_root/.export-runtime-cache",
        },
        "semantic_model_used": False,
        "synthesized": False,
    }
    plan = dict(payload)
    plan["plan_sha256"] = _digest(payload)
    _canonical(plan)
    _, after_sha = _read_stable(source)
    if after_sha != source_sha:
        raise ValueError("源 MIDI 在规划期间发生变化，请重新生成计划。")
    return plan


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ValueError("plan 必须是对象。")
    saved = copy.deepcopy(dict(plan))
    stored = saved.get("plan_sha256")
    if not isinstance(stored, str) or len(stored) != 64 or any(
        char not in "0123456789abcdef" for char in stored
    ):
        raise ValueError("plan_sha256 必须是小写 SHA-256。")
    unsigned = {key: value for key, value in saved.items() if key != "plan_sha256"}
    if _digest(unsigned) != stored:
        raise ValueError("计划摘要与当前 plan 内容不一致，计划可能已被篡改。")
    if saved.get("schema_version") != _SCHEMA_VERSION or saved.get("kind") != _PLAN_KIND:
        raise ValueError("不支持的 export plan schema/kind。")
    if saved.get('timeline_adapter') != 'vwa-native-clock-v1':
        raise ValueError('导出时间线合同已更新，请重新生成方案。')
    return saved


def _checker(cancel: Callable[[], Any] | None) -> Callable[[], None]:
    if cancel is not None and not callable(cancel):
        raise ValueError("cancel 必须可调用或为 None。")

    def check() -> None:
        if cancel is not None and cancel() is True:
            raise InterruptedError("已取消工程导出。")

    return check


def _progress_callback(progress: Callable[[str], Any] | None) -> Callable[[str], None]:
    if progress is not None and not callable(progress):
        raise ValueError("progress 必须可调用或为 None。")
    return (lambda text: None) if progress is None else progress


def _output_root(value: Any) -> Path:
    if value is None or isinstance(value, bool):
        raise ValueError("output_root 必须是目录路径。")
    try:
        root = Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("output_root 无效。") from error
    if _io_path(root).exists() and not _io_path(root).is_dir():
        raise ValueError("output_root 已存在但不是目录。")
    try:
        _io_path(root).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("无法创建 output_root：" + str(error)) from error
    return root


def _runtime_path(value: Any) -> Path:
    candidate = value
    if candidate is None:
        candidate = os.environ.get(_RUNTIME_ENV)
    if candidate is None:
        candidate = Path(__file__).resolve().parent.parent / "dependencies" / "export"
    if isinstance(candidate, bool) or not isinstance(candidate, (str, bytes, os.PathLike)):
        raise ValueError("export_runtime 必须是 LibreSVIP 运行库目录。")
    try:
        runtime = Path(candidate).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("export_runtime 路径无效。") from error
    expected = runtime / "libresvip" / "plugins" / "mid" / "midi_converter.py"
    if not _io_path(expected).is_file():
        raise FileNotFoundError(
            "LibreSVIP 导出运行库不可用；请通过 export_runtime 或 "
            + _RUNTIME_ENV + " 指定只读 dependencies/export。"
        )
    return runtime


@contextmanager
def _runtime_session(runtime: Path, cache_root: Path):
    cache_root = (cache_root / ".export-runtime-cache").resolve()
    _io_path(cache_root).mkdir(parents=True, exist_ok=True)
    keys = ("APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME")
    missing = object()
    previous = {key: os.environ.get(key, missing) for key in keys}
    inserted = False
    with _RUNTIME_LOCK:
        try:
            for key in keys:
                os.environ[key] = str(cache_root)
            runtime_text = str(runtime)
            if runtime_text not in sys.path:
                sys.path.insert(0, runtime_text)
                inserted = True
            from libresvip.plugins.mid.midi_converter import MidiConverter
            from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
            from libresvip.plugins.ustx.ustx_converter import OpenUtauConverter
            from libresvip.plugins.vsqx.vsqx_converter import VsqxConverter
            import libresvip
            module_file = Path(libresvip.__file__).resolve()
            if not module_file.is_relative_to(runtime):
                raise ValueError("Python 已从其他目录加载 libresvip；拒绝混用运行库。")
            yield {
                "midi": MidiConverter,
                "svp": SynthVStudioConverter,
                "ustx": OpenUtauConverter,
                "vsqx": VsqxConverter,
            }
        finally:
            for key, old in previous.items():
                if old is missing:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(old)
            if inserted:
                try:
                    sys.path.remove(str(runtime))
                except ValueError:
                    pass


def _input_options(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "import_lyrics": True,
        "lyric_encoding": "utf-8",
        "import_time_signatures": True,
        "multi_channel": "split",
        "import_pitch": False,
        "import_volume": False,
        "default_bpm": plan["options"]["default_bpm"],
    }


def _output_options(fmt: str, plan: dict[str, Any]) -> dict[str, Any]:
    language = plan["options"]["language"]
    if fmt == "svp":
        return {
            "down_sample": 0,
            "language_override": "mandarin" if language == "zh" else "japanese",
            "version_compatibility": _SVP_VERSIONS[plan["options"]["svp_version"]],
        }
    if fmt == "vsqx":
        return {"pretty_xml": True, "default_lang_id": 4 if language == "zh" else 0}
    return {}


def _project_notes(project: Any) -> list[tuple[int, int, int, str]]:
    result: list[tuple[int, int, int, str]] = []
    for track in project.track_list:
        if not hasattr(track, "note_list"):
            continue
        for note in track.note_list:
            result.append((
                int(note.key_number),
                int(note.start_pos),
                int(note.length),
                _normalise_text(str(note.lyric or "")),
            ))
    result.sort()
    return result


def _expected_notes(plan: dict[str, Any]) -> list[tuple[int, int, int, str]]:
    rows = plan["expected"].get("expected_notes")
    if not isinstance(rows, list):
        raise ValueError("计划缺少音符证据。")
    try:
        return sorted(
            (int(row[0]), int(row[1]), int(row[2]), _normalise_text(str(row[3])))
            for row in rows
        )
    except (IndexError, TypeError, ValueError) as error:
        raise ValueError("计划中的音符证据无效。") from error


def _counter_difference(
    expected: list[tuple[int, int, int, str]],
    actual: list[tuple[int, int, int, str]],
) -> dict[str, Any]:
    left, right = Counter(expected), Counter(actual)
    missing, extra = left - right, right - left
    return {
        "expected_note_count": len(expected),
        "actual_note_count": len(actual),
        "missing_count": sum(missing.values()),
        "extra_count": sum(extra.values()),
        "missing_sample": [list(key) for key in missing.elements()][:8],
        "extra_sample": [list(key) for key in extra.elements()][:8],
    }


def _project_tempo(project: Any) -> list[list[Any]]:
    return [
        [int(row.position), round(float(row.bpm), 3)]
        for row in project.song_tempo_list
    ]


def _source_tempo(plan):
    # MIDI's clock has no count-in. Resolve all track events in playback order.
    rows={0:float(plan['options']['default_bpm'])}
    for event in plan['expected']['tempos']:
        rows[round(event['tick']*480/plan['ticks_per_beat'])]=event['bpm']
    return [[position,bpm] for position,bpm in sorted(rows.items())]


def _converter_project(project, fmt):
    """Adapt local LibreSVIP plugin clocks without changing notes or the runtime.

    MIDI imports tempos in playback ticks. SVP/USTX writers remove one internal
    first bar, while VSQX also needs its on-disk pre-measure retained. The
    adapters are explicit and tested by independent physical-seconds audits.
    """
    from libresvip.core.tick_counter import shift_tempo_list,shift_beat_list
    adapted=project.model_copy(deep=True)
    prefix=round(project.time_signature_list[0].bar_length())
    adapted.song_tempo_list=shift_tempo_list(project.song_tempo_list,prefix*(2 if fmt=='vsqx' else 1))
    if fmt=='svp':
        adapted.time_signature_list=shift_beat_list(project.time_signature_list,1)
    return adapted


def _loaded_clock(project, fmt):
    """Return reloaded plugin data on the MIDI playback clock for comparison."""
    from libresvip.core.tick_counter import skip_tempo_list,skip_beat_list
    tempos=project.song_tempo_list
    signatures=project.time_signature_list
    if fmt in ('svp','ustx'):
        prefix=1920 if fmt=='ustx' else round(signatures[0].bar_length())
        tempos=skip_tempo_list(tempos,prefix)
    if fmt=='svp':signatures=skip_beat_list(signatures,1)
    return ([[int(row.position),round(float(row.bpm),3)] for row in tempos],
            [[int(row.bar_index),int(row.numerator),int(row.denominator)] for row in signatures])


def _project_timesig(project: Any) -> list[list[int]]:
    return [
        [int(row.bar_index), int(row.numerator), int(row.denominator)]
        for row in project.time_signature_list
    ]


def _new_temp_path(root: Path, fmt: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".vwa-export-", suffix="." + fmt, dir=native_path(root)
    )
    os.close(descriptor)
    path = Path(name)
    try:
        _io_path(path).unlink()
    except OSError as error:
        raise ValueError("无法准备候选临时路径：" + str(error)) from error
    return path


def _restore_vsqx_lyrics(path: Path, project: Any) -> int:
    """Restore exact lyric text while retaining generated VSQX phonemes."""
    try:
        tree = ET.parse(native_path(path))
        root = tree.getroot()
        namespace = root.tag.partition("}")[0].removeprefix("{")
        if namespace:
            ET.register_namespace("", namespace)
        parts = [row for row in root.iter() if row.tag.rsplit("}", 1)[-1] == "vsTrack"]
        tracks = [row for row in project.track_list if hasattr(row, "note_list")]
        if len(parts) != len(tracks):
            raise ValueError("VSQX 导出后的声部数量不一致。")
        changed = 0
        for part, track in zip(parts, tracks):
            elements = [row for row in part.iter() if row.tag.rsplit("}", 1)[-1] == "note"]
            if len(elements) != len(track.note_list):
                raise ValueError("VSQX 导出后的音符数量不一致。")
            for element, note in zip(elements, track.note_list):
                lyric = next(
                    (child for child in element
                     if child.tag.rsplit("}", 1)[-1] in ("y", "lyric")),
                    None,
                )
                if lyric is None:
                    raise ValueError("VSQX 音符缺少歌词节点，无法恢复原歌词。")
                lyric.text = _normalise_text(str(note.lyric or ""))
                changed += 1
        tree.write(native_path(path), encoding="utf-8", xml_declaration=True)
        reread = ET.parse(native_path(path))
        verified_parts = [
            row for row in reread.getroot().iter()
            if row.tag.rsplit("}", 1)[-1] == "vsTrack"
        ]
        if len(verified_parts) != len(tracks):
            raise ValueError("VSQX 歌词恢复后的声部数量不一致。")
        for part, track in zip(verified_parts, tracks):
            values = [
                _normalise_text(child.text or "")
                for child in part.iter()
                if child.tag.rsplit("}", 1)[-1] in ("y", "lyric")
            ]
            expected = [_normalise_text(str(note.lyric or "")) for note in track.note_list]
            if values != expected:
                raise ValueError("VSQX 歌词恢复后回读不一致。")
        return changed
    except ValueError:
        raise
    except (OSError, ET.ParseError) as error:
        raise ValueError("恢复 VSQX 歌词失败：" + str(error)) from error


def _publish_exclusive(path: Path, data: bytes) -> None:
    """Publish complete bytes without replacing an existing target."""
    native = _io_path(path)
    native.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix="." + native.name + ".", suffix=".tmp", dir=str(native.parent)
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(str(temporary), str(native))
            return
        except FileExistsError:
            return
        except BaseException as link_error:
            if os.name != "nt":
                raise
            if not isinstance(link_error, OSError):
                raise ValueError("无法创建原子候选链接：" + str(link_error)) from link_error
        for attempt in range(6):
            try:
                if os.path.lexists(native):
                    return
                os.rename(str(temporary), str(native))
                return
            except FileExistsError:
                return
            except OSError as error:
                winerror = getattr(error, "winerror", None)
                if winerror == 183:
                    return
                if winerror not in (5, 32, 33) or attempt == 5:
                    raise ValueError("Windows 无法原子发布候选：" + str(error)) from error
                time.sleep(0.01 * 2 ** attempt)
            except BaseException as error:
                raise ValueError("无法原子发布候选：" + str(error)) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _existing_file(path: Path, label: str) -> bytes | None:
    native = _io_path(path)
    if native.is_symlink():
        raise ValueError(label + " 是符号链接，拒绝信任。")
    if not native.exists():
        return None
    if not native.is_file():
        raise ValueError(label + " 已存在但不是普通文件。")
    try:
        return native.read_bytes()
    except OSError as error:
        raise ValueError("无法读取已有 " + label + "：" + str(error)) from error


def candidate_path_for(
    root: Path, plan_sha256: str, fmt: str, candidate_sha256: str
) -> Path:
    """Combine plan and candidate identities while keeping a readable suffix."""
    return (root / f"vwa-export-{plan_sha256[:16]}-{candidate_sha256[:16]}.{fmt}").resolve()


def _receipt_paths(
    plan: dict[str, Any], root: Path, candidate_hashes: list[str] | None = None
) -> tuple[Path, list[Path]]:
    prefix = "vwa-export-" + plan["plan_sha256"][:24]
    if candidate_hashes is None:
        candidates = [
            (root / f"vwa-export-{plan['plan_sha256'][:16]}-pending.{fmt}").resolve()
            for fmt in plan["formats"]
        ]
    else:
        if len(candidate_hashes) != len(plan["formats"]):
            raise ValueError("候选哈希数量与格式数量不一致。")
        candidates = [
            candidate_path_for(root, plan["plan_sha256"], fmt, candidate_sha)
            for fmt, candidate_sha in zip(plan["formats"], candidate_hashes)
        ]
    receipt = (root / f"{prefix}.receipt.json").resolve()
    return receipt, candidates


def _verify_receipt(path: Path, plan: dict[str, Any], root: Path) -> dict[str, Any] | None:
    raw = _existing_file(path, "export receipt")
    if raw is None:
        return None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("已有 export receipt 不是有效 UTF-8 JSON。") from error
    if not isinstance(document, dict):
        raise ValueError("已有 export receipt 根节点必须是对象。")
    stored = document.get("receipt_sha256")
    unsigned = {key: value for key, value in document.items() if key != "receipt_sha256"}
    if not isinstance(stored, str) or _digest(unsigned) != stored:
        raise ValueError("已有 export receipt 摘要不匹配，拒绝信任碰撞。")
    if (
        document.get("plan_sha256") != plan["plan_sha256"]
        or document.get("source_sha256") != plan["source_sha256"]
        or Path(document.get("output_root", "")).resolve() != root
        or document.get("formats") != plan["formats"]
    ):
        raise ValueError("已有 export receipt 绑定了不同计划、源或输出目录。")
    rows = document.get("candidates")
    if not isinstance(rows, list) or len(rows) != len(plan["formats"]):
        raise ValueError("已有 export receipt 的候选清单不完整。")
    seen_formats: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("format") not in plan["formats"]:
            raise ValueError("已有 export receipt 含无效候选记录。")
        seen_formats.append(row["format"])
        candidate_value = row.get("candidate_path")
        candidate_sha = row.get("candidate_sha256")
        if not isinstance(candidate_value, str) or not candidate_value:
            raise ValueError("已有 export receipt 的候选路径无效。")
        if (
            not isinstance(candidate_sha, str) or len(candidate_sha) != 64
            or any(char not in "0123456789abcdef" for char in candidate_sha)
        ):
            raise ValueError("已有 export receipt 的候选哈希无效。")
        candidate = Path(candidate_value).resolve()
        expected_path = candidate_path_for(
            root, plan["plan_sha256"], row["format"], candidate_sha
        )
        if candidate != expected_path or candidate == Path(plan["source_path"]):
            raise ValueError("已有 export receipt 的候选路径越界或身份不匹配。")
        current = _existing_file(candidate, "候选工程")
        if current is None or _sha256(current) != candidate_sha:
            raise ValueError("候选工程缺失或哈希已改变，拒绝信任已有 receipt。")
        requested = bool(plan.get("instrumental", {}).get("requested"))
        if row["format"] == "svp" and requested:
            from agent_media import inspect_svp_media
            media = row.get("media")
            if not isinstance(media, Mapping) or media.get("included") is not True:
                raise ValueError("已有含伴奏 receipt 缺少可验证的 SVP 媒体证据。")
            inspect_svp_media(candidate, allowed_roots=[root], require_relative=True,
                              expected=media)
            if row.get("instrumental_included") is not True:
                raise ValueError("已有含伴奏 SVP receipt 未声明 instrumental_included=true。")
        elif row["format"] != "svp" and row.get("instrumental_included") is True:
            raise ValueError("非 SVP 候选不能声明内嵌伴奏。")
    if seen_formats != plan["formats"]:
        raise ValueError("已有 export receipt 的格式顺序不匹配。")
    return document


def apply_export(
    path: Any,
    plan: Any,
    output_root: Any,
    export_runtime: Any = None,
    cancel: Callable[[], Any] | None = None,
    progress: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Convert corrected MIDI with LibreSVIP and publish verified candidates."""
    check_cancel = _checker(cancel)
    report = _progress_callback(progress)
    check_cancel()
    saved = _validate_plan(plan)
    source = _source_path(path)
    if saved.get("source_path") != str(source):
        raise ValueError("计划绑定的源 MIDI 与当前 path 不一致。")
    _, current_sha = _read_stable(source)
    if current_sha != saved.get("source_sha256"):
        raise ValueError("源 MIDI 已改变；请基于当前文件重新 plan_export。")
    regenerated = plan_export(source, saved.get("formats"), saved.get("options"))
    if regenerated != saved:
        raise ValueError("计划内容与当前源、选项或音符/歌词证据不一致，计划无效。")
    root = _output_root(output_root)
    receipt_path, _ = _receipt_paths(saved, root)
    if receipt_path == source:
        raise ValueError("确定性回执路径与源 MIDI 冲突，拒绝覆盖源文件。")
    if source.parent == root and source.name.startswith("vwa-export-"):
        raise ValueError("源 MIDI 占用了保留的导出名称；请重命名源文件。")

    existing_receipt = _verify_receipt(receipt_path, saved, root)
    if existing_receipt is not None:
        check_cancel()
        result = copy.deepcopy(existing_receipt)
        result["receipt"] = copy.deepcopy(existing_receipt)
        result["receipt_path"] = str(receipt_path)
        result["replay"] = True
        result["source_unchanged"] = _read_stable(source)[1] == current_sha
        return result

    runtime = _runtime_path(export_runtime)
    report("loaded export plan and runtime")
    check_cancel()
    expected = _expected_notes(saved)
    candidates: list[dict[str, Any]] = []
    pending_paths: list[Path] = []
    try:
        with _runtime_session(runtime, root) as converters:
            report("importing corrected MIDI through LibreSVIP")
            try:
                project = converters["midi"].load(
                    _io_path(source), _input_options(saved)
                )
            except Exception as error:
                raise ValueError("LibreSVIP 无法导入修正后的 MIDI：" + str(error)) from error
            actual_source = _project_notes(project)
            if actual_source != expected:
                diff = _counter_difference(expected, actual_source)
                raise ValueError(
                    "LibreSVIP 导入的 MIDI 音符或歌词与计划不一致："
                    + _canonical(diff)
                )
            from libresvip.model.base import SongTempo
            input_tempo = _source_tempo(saved)
            project.song_tempo_list=[SongTempo(position=position,bpm=bpm) for position,bpm in input_tempo]
            input_timesig = _project_timesig(project)

            for candidate_index, fmt in enumerate(saved["formats"]):
                check_cancel()
                report("exporting " + fmt)
                temporary = _new_temp_path(root, fmt)
                pending_paths.append(temporary)
                instrumental_requested = bool(saved.get("instrumental", {}).get("requested"))
                candidate_project = _converter_project(project, fmt)
                if fmt == "svp" and instrumental_requested:
                    from libresvip.model.base import InstrumentalTrack
                    candidate_project.track_list.append(InstrumentalTrack(
                        title="Instrumental",
                        audio_file_path=str(saved["options"]["instrumental_path"]),
                        offset=0,
                    ))
                try:
                    converters[fmt].dump(
                        _io_path(temporary), candidate_project, _output_options(fmt, saved)
                    )
                    if fmt == "vsqx":
                        _restore_vsqx_lyrics(temporary, project)
                    media = None
                    metadata = None
                    if fmt == "svp":
                        from agent_media import apply_svp_metadata, attach_instrumental
                        metadata = apply_svp_metadata(
                            temporary,
                            track_names=saved["options"].get("track_names"),
                            project_name=saved["options"].get("project_name"),
                        )
                        if instrumental_requested:
                            media = attach_instrumental(
                                temporary,
                                saved["options"]["instrumental_path"],
                                allowed_roots=[
                                    Path(saved["options"]["instrumental_path"]).resolve().parent,
                                    root,
                                ],
                                cancel=cancel,
                            )
                    elif any(key in saved["options"] for key in ("track_names", "project_name")):
                        metadata = {
                            "requested": True, "applied": False,
                            "reason": "metadata_not_supported_for_format",
                            "track_names": saved["options"].get("track_names"),
                            "project_name": saved["options"].get("project_name"),
                        }
                    check_cancel()
                    loaded = converters[fmt].load(
                        _io_path(temporary),
                        {"import_pitch": False, "import_instrumental_track": False},
                    )
                except Exception as error:
                    if isinstance(error, (ValueError, OSError, InterruptedError)) and hasattr(error, "code"):
                        raise
                    raise ValueError(f"{fmt.upper()} 转换或回读失败：{error}") from error
                actual = _project_notes(loaded)
                if actual != expected:
                    diff = _counter_difference(expected, actual)
                    raise ValueError(
                        f"{fmt.upper()} 回读后音符/歌词不一致：" + _canonical(diff)
                    )
                output_tempo,output_timesig = _loaded_clock(loaded,fmt)
                tempo_verified = output_tempo == input_tempo
                raw_bytes = _io_path(temporary).read_bytes()
                if not raw_bytes:
                    raise ValueError(f"{fmt.upper()} 转换产生空文件。")
                candidate_sha = _sha256(raw_bytes)
                final = candidate_path_for(
                    root, saved["plan_sha256"], fmt, candidate_sha
                )
                if final == source:
                    raise ValueError("候选路径与源 MIDI 冲突，拒绝覆盖源文件。")
                if final in {row_path for row_path in pending_paths}:
                    raise ValueError("候选路径与临时转换文件冲突。")
                previous = _existing_file(final, "候选工程")
                if previous is not None and previous != raw_bytes:
                    raise ValueError("候选路径已有不同内容，拒绝覆盖或信任该碰撞。")
                if previous is None:
                    _publish_exclusive(final, raw_bytes)
                published = _existing_file(final, "候选工程")
                if published != raw_bytes:
                    raise ValueError("候选发布后校验失败。")
                if fmt == "svp" and instrumental_requested:
                    from agent_media import inspect_svp_media
                    media_readback = inspect_svp_media(
                        final, allowed_roots=[root], require_relative=True,
                        expected=media,
                    )
                    media = {**media, **media_readback}
                    media["path"] = str(final.parent / media["relative_paths"][0])
                    media["post_publish_readback_verified"] = True
                if instrumental_requested and fmt == "svp":
                    instrumental_included = True
                    instrumental_reason = "included_in_svp_portable_package"
                elif instrumental_requested:
                    instrumental_included = False
                    instrumental_reason = "format_not_supported"
                else:
                    instrumental_included = False
                    instrumental_reason = "not_requested"
                candidates.append({
                    "format": fmt,
                    "candidate_path": str(final),
                    "candidate_sha256": candidate_sha,
                    "bytes": len(raw_bytes),
                    "instrumental_included": instrumental_included,
                    "instrumental_reason": instrumental_reason,
                    "media": media,
                    "metadata": metadata,
                    "verified_notes": len(actual),
                    "verified_lyrics": sum(1 for row in actual if row[3] != ""),
                    "tempo_verified": tempo_verified,
                    "timing_verified": True,
                    "time_signature_verified": output_timesig == input_timesig,
                    "time_signatures": output_timesig,
                    "expected_tempo": input_tempo,
                    "output_tempo": output_tempo,
                    "loss_codes": ([]
                        + ([] if tempo_verified else ["tempo_table_normalized"])
                        + ([] if output_timesig == input_timesig
                           else ["time_signature_normalized"])),
                    "engine": "LibreSVIP",
                    "timing_basis": "midi_playback_ticks_with_explicit_plugin_prefix_adapter",
                })
                try:
                    _io_path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
                pending_paths.remove(temporary)
                report("verified " + fmt)

        for row in candidates:
            if not _io_path(Path(row["candidate_path"])).is_file():
                raise ValueError("发布完成前候选工程丢失。")
        _, before_receipt_sha = _read_stable(source)
        if before_receipt_sha != current_sha:
            raise ValueError("导出期间源 MIDI 发生变化；未发布 receipt。")
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "kind": _RECEIPT_KIND,
            "plan_sha256": saved["plan_sha256"],
            "source_path": str(source),
            "source_sha256": current_sha,
            "output_root": str(root),
            "formats": saved["formats"],
            "runtime_path": str(runtime),
            "engine": "LibreSVIP",
            "candidates": candidates,
            "verified": {
                "notes": len(expected),
                "lyrics": sum(1 for row in expected if row[3] != ""),
                "tempo": input_tempo,
                "time_signatures": input_timesig,
            },
            "instrumental": copy.deepcopy(saved.get("instrumental", {
                "requested": False, "included": False, "reason": "not_requested"
            })),
            "conversion_losses": copy.deepcopy(saved["conversion_losses"]),
            "semantic_model_used": False,
            "synthesized": False,
            "published": False,
        }
        receipt = dict(payload)
        receipt["receipt_sha256"] = _digest(payload)
        receipt_bytes = _canonical_bytes(receipt, pretty=True)
        previous_receipt = _existing_file(receipt_path, "export receipt")
        if previous_receipt is not None and previous_receipt != receipt_bytes:
            raise ValueError("export receipt 路径已有不同内容，拒绝覆盖。")
        if previous_receipt is None:
            _publish_exclusive(receipt_path, receipt_bytes)
        if _existing_file(receipt_path, "export receipt") != receipt_bytes:
            raise ValueError("export receipt 发布后校验失败。")
        _, after_receipt_sha = _read_stable(source)
        if after_receipt_sha != current_sha:
            raise ValueError("源 MIDI 在 receipt 发布期间变化。")
        return {
            **copy.deepcopy(receipt),
            "receipt": copy.deepcopy(receipt),
            "receipt_path": str(receipt_path),
            "replay": False,
            "source_unchanged": True,
        }
    finally:
        for temporary in pending_paths:
            try:
                _io_path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["ExportError", "plan_export", "apply_export"]
