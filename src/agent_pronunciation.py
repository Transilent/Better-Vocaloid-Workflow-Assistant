"""Pronunciation evidence and explicit phoneme patches for VWA projects.

This worker is deliberately independent of Qt, MCP, providers and synthesis.
It reads the native facts already stored in MIDI, Synthesizer V (SVP) and
VOCALOID (VSQX) files. It never runs G2P, consults a pronunciation dictionary,
renders audio or claims that a written phoneme is acoustically correct.

The public objects use a stable, finite JSON schema:

* ``vwa-pronunciation-read`` contains ``source_sha256`` and
  ``notes[].note_ref``. A note reference identifies one native note in that
  exact source revision.
* ``vwa-pronunciation-patch-plan`` binds a strict list of
  ``{note_ref, phonemes}`` edits to a source and candidate SHA-256.
* ``vwa-pronunciation-patch-receipt`` binds the independent candidate and its
  receipt to the same plan and verifies that the source did not change.

Only SVP and VSQX can be patched. MIDI notes intentionally remain
``phoneme_state='unknown'`` because MIDI has no standard phoneme field. USTX is
intentionally unsupported in this implementation.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
import unicodedata
import xml.etree.ElementTree as ET

import agent_score as _score
from common import native_path


SCHEMA_VERSION = 1
READ_KIND = "vwa-pronunciation-read"
PLAN_KIND = "vwa-pronunciation-patch-plan"
RECEIPT_KIND = "vwa-pronunciation-patch-receipt"
DIFF_SCHEMA = "vwa-pronunciation-diff-v1"

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
MAX_XML_ELEMENTS = 1_000_000
MAX_STRUCTURE_DEPTH = 128
MAX_JSON_VALUES = 2_000_000
MAX_TRACKS = 512
MAX_GROUPS = 2_048
MAX_NOTES = 200_000
MAX_PAGE_SIZE = 500
MAX_EDITS = 1_024
MAX_PHONEME_BYTES = 4_096
MAX_NOTE_REF_BYTES = 512
MAX_ABSOLUTE_INTEGER = 2**63 - 1
SVP_UNITS_PER_QUARTER = 705_600_000

VSQX_NAMESPACES = {
    3: "http://www.yamaha.co.jp/vocaloid/schema/vsq3/",
    4: "http://www.yamaha.co.jp/vocaloid/schema/vsq4/",
}

_PLAN_KEYS = {
    "schema_version",
    "kind",
    "source_path",
    "source_sha256",
    "format",
    "format_version",
    "time_basis",
    "serialization",
    "edit_count",
    "note_count",
    "edits",
    "diff",
    "lock_changes",
    "expected_candidate_sha256",
    "validation",
    "warnings",
    "inference_performed",
    "g2p_performed",
    "provider_access_performed",
    "acoustic_pronunciation_verified",
    "plan_sha256",
}

_EDIT_KEYS = {"note_ref", "phonemes"}
_EDIT_STATE_KEYS = {"phonemes", "phoneme_state", "phoneme_override", "phoneme_locked"}
_PHONEME_CHILD_NAMES = {"p", "phnms", "typePhonemes"}

__all__ = [
    "PronunciationError",
    "SCHEMA_VERSION",
    "READ_KIND",
    "PLAN_KIND",
    "RECEIPT_KIND",
    "read_pronunciation",
    "plan_pronunciation_patch",
    "apply_pronunciation_patch",
]


class PronunciationError(ValueError):
    """Invalid pronunciation evidence, input or patch."""


def _io_path(path: Any) -> Path:
    return Path(native_path(path))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options: dict[str, Any] = {
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


def _canonical_digest(value: Any) -> str:
    return _sha256(_canonical_bytes(value))


def _normalise_sha(value: Any, field_name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)):
        raise PronunciationError(f"{field_name} 必须是 64 位十六进制 SHA-256。")
    return value.lower()


def _strict_int(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise PronunciationError(f"{field_name} 必须是整数；布尔值不算整数。")
    if value < minimum or (maximum is not None and value > maximum):
        raise PronunciationError(f"{field_name} 超出允许范围。")
    return value


def _finite_number(value: Any, field_name: str) -> int | float:
    if type(value) not in (int, float):
        raise PronunciationError(f"{field_name} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise PronunciationError(f"{field_name} 必须是有限数值。")
    return int(number) if number.is_integer() else number


def _safe_text(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = True,
    maximum_bytes: int = MAX_PHONEME_BYTES,
) -> str:
    if not isinstance(value, str):
        raise PronunciationError(f"{field_name} 必须是字符串。")
    if not allow_empty and not value:
        raise PronunciationError(f"{field_name} 不能为空。")
    if "\x00" in value:
        raise PronunciationError(f"{field_name} 不能包含 NUL。")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise PronunciationError(f"{field_name} 不是有效 UTF-8 文本。") from exc
    if len(raw) > maximum_bytes:
        raise PronunciationError(f"{field_name} 超过 {maximum_bytes} 字节上限。")
    return value


def _finite_float_text(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise PronunciationError("JSON 包含非有限数值。")
    return number


def _reject_json_constant(value: str) -> None:
    raise PronunciationError(f"JSON 包含不支持的非有限常量 {value}。")


class _DuplicateJsonKey(PronunciationError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"JSON 对象包含重复键 {key!r}。")
        value[key] = item
    return value


def _read_source(path: Any) -> tuple[Path, bytes, str]:
    if path is None or isinstance(path, (bool, bytes)):
        raise PronunciationError("工程路径无效。")
    try:
        source = Path(path).expanduser()
        native = _io_path(source)
        if not native.is_file():
            raise PronunciationError("工程文件不存在或不是普通文件。")
        size = native.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise PronunciationError(f"工程超过 {MAX_SOURCE_BYTES} 字节安全上限。")
        raw = native.read_bytes()
        if len(raw) != size:
            raise PronunciationError("工程在读取期间发生变化，请重试。")
        return source.resolve(strict=True), raw, _sha256(raw)
    except PronunciationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise PronunciationError(f"无法读取工程：{exc}") from exc


def _format_for_path(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix in {".mid", ".midi"}:
        return "midi"
    if suffix == ".svp":
        return "svp"
    if suffix == ".vsqx":
        return "vsqx"
    if suffix == ".ustx":
        raise PronunciationError("USTX 音素读取暂未启用；当前支持 MIDI、SVP 和 VSQX。")
    raise PronunciationError("不支持的工程格式；请使用 .mid、.midi、.svp 或 .vsqx。")


def _validate_limit_offset(limit: Any, offset: Any) -> tuple[int, int]:
    page_limit = _strict_int(limit, "limit", minimum=1, maximum=MAX_PAGE_SIZE)
    page_offset = _strict_int(offset, "offset", minimum=0, maximum=10_000_000)
    return page_limit, page_offset


def _strip_svp_trailing_nuls(raw: bytes) -> tuple[bytes, int]:
    end = len(raw)
    while end and raw[end - 1] == 0:
        end -= 1
    return raw[:end], len(raw) - end


def _validate_json_bounds(value: Any) -> None:
    stack = [(value, 0)]
    count = 0
    while stack:
        current, depth = stack.pop()
        count += 1
        if count > MAX_JSON_VALUES:
            raise PronunciationError(f"JSON 结构超过 {MAX_JSON_VALUES} 个值的安全上限。")
        if depth > MAX_STRUCTURE_DEPTH:
            raise PronunciationError(f"JSON 嵌套超过 {MAX_STRUCTURE_DEPTH} 层安全上限。")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _reject_overlaps(intervals: list[tuple[float, float, int]], label: str) -> None:
    ordered = sorted(intervals, key=lambda row: (row[0], row[1], row[2]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1] - 1e-9:
            raise PronunciationError(f"{label} 含重叠音符/复调，拒绝猜测音素归属。")


def _parse_svp(raw: bytes, source_path: Path, source_sha256: str) -> dict[str, Any]:
    payload, trailing_nuls = _strip_svp_trailing_nuls(raw)
    bom = payload.startswith(b"\xef\xbb\xbf")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise PronunciationError("SVP 必须以 UTF-8 编码。") from exc
    if "\x00" in text:
        raise PronunciationError("SVP JSON 正文包含 NUL。")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_float_text,
        )
    except PronunciationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise PronunciationError(f"无法解析 SVP JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PronunciationError("SVP 顶层必须是 JSON 对象。")
    _validate_json_bounds(data)
    format_version = data.get("version")
    if format_version is not None and (
        type(format_version) is not int or not 0 <= format_version <= 1_000_000
    ):
        raise PronunciationError("SVP version 必须是有限整数或 null。")

    library = data.get("library", [])
    if not isinstance(library, list):
        raise PronunciationError("SVP library 必须是数组。")
    if library:
        raise PronunciationError("SVP 使用库组/复杂分组引用，当前无法可靠展开，已拒绝。")
    tracks = data.get("tracks")
    if not isinstance(tracks, list):
        raise PronunciationError("SVP 缺少 tracks 数组。")
    if len(tracks) > MAX_TRACKS:
        raise PronunciationError(f"SVP 轨道超过 {MAX_TRACKS} 个安全上限。")

    time_section = data.get("time", {})
    if not isinstance(time_section, dict):
        raise PronunciationError("SVP time 必须是对象。")
    tempo_entries = time_section.get("tempo", [])
    if not isinstance(tempo_entries, list):
        raise PronunciationError("SVP time.tempo 必须是数组。")
    for index, entry in enumerate(tempo_entries):
        if not isinstance(entry, dict):
            raise PronunciationError(f"SVP tempo[{index}] 必须是对象。")
        _finite_number(entry.get("position"), f"tempo[{index}].position")
        bpm = _finite_number(entry.get("bpm"), f"tempo[{index}].bpm")
        if float(bpm) <= 0:
            raise PronunciationError(f"SVP tempo[{index}].bpm 必须为正。")
    tempo_positions, tempo_points = _score._svp_timeline(tempo_entries)
    tempo_events = []
    for entry in tempo_entries:
        position = _finite_number(entry["position"], "tempo.position")
        tempo_events.append({
            "position": position,
            "bpm": float(entry["bpm"]),
            "seconds": _score._round_seconds(
                _score._svp_seconds_at(position, tempo_positions, tempo_points)
            ),
        })
    tempo_events.sort(key=lambda row: (row["position"], row["bpm"]))

    notes: list[dict[str, Any]] = []
    tracks_public: list[dict[str, Any]] = []
    locations: dict[str, tuple[int, int]] = {}
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict):
            raise PronunciationError(f"SVP tracks[{track_index}] 必须是对象。")
        groups = track.get("groups", [])
        if not isinstance(groups, list):
            raise PronunciationError(f"SVP tracks[{track_index}].groups 必须是数组。")
        if groups:
            raise PronunciationError(
                f"SVP 轨道 {track_index} 使用库组/复杂分组引用，当前无法可靠展开，已拒绝。"
            )
        main_ref = track.get("mainRef", {})
        if not isinstance(main_ref, dict):
            raise PronunciationError(f"SVP tracks[{track_index}].mainRef 无效。")
        instrumental = main_ref.get("isInstrumental") is True
        main_group = track.get("mainGroup")
        if main_group is None and instrumental:
            main_group = {"notes": []}
        if not isinstance(main_group, dict):
            raise PronunciationError(f"SVP tracks[{track_index}].mainGroup 无效或缺失。")
        raw_notes = main_group.get("notes", [])
        if not isinstance(raw_notes, list):
            raise PronunciationError(f"SVP tracks[{track_index}].mainGroup.notes 必须是数组。")
        track_name = track.get("name") if isinstance(track.get("name"), str) else None
        group_identifier = main_group.get("uuid") or main_ref.get("groupID") or "main"
        if not isinstance(group_identifier, str):
            group_identifier = "main"
        group_name = main_group.get("name") if isinstance(main_group.get("name"), str) else None
        offset = _finite_number(main_ref.get("blickOffset", 0), "mainRef.blickOffset")
        track_ids: list[str] = []
        intervals: list[tuple[float, float, int]] = []
        for note_index, note in enumerate(raw_notes):
            if not isinstance(note, dict):
                raise PronunciationError(
                    f"SVP tracks[{track_index}] 音符 {note_index} 必须是对象。"
                )
            onset = _finite_number(note.get("onset"), "note.onset")
            duration = _finite_number(note.get("duration"), "note.duration")
            if float(onset) < 0 or float(duration) <= 0:
                raise PronunciationError("SVP 音符 onset 必须非负且 duration 必须为正。")
            pitch = _finite_number(note.get("pitch"), "note.pitch")
            if not 0 <= float(pitch) <= 127:
                raise PronunciationError("SVP 音符 pitch 必须位于 0..127。")
            lyric = note.get("lyrics")
            if lyric is not None:
                lyric = _safe_text(lyric, "SVP lyric", maximum_bytes=MAX_PHONEME_BYTES)
            raw_phonemes = note.get("phonemes", "")
            if raw_phonemes is None:
                raw_phonemes = ""
            if not isinstance(raw_phonemes, str):
                raise PronunciationError("SVP note.phonemes 必须是字符串。")
            raw_phonemes = _safe_text(raw_phonemes, "SVP phonemes")
            phonemes = raw_phonemes or None
            phoneme_state = "explicit" if phonemes is not None else "unknown"
            note_ref = f"note-{source_sha256[:16]}-svp-t{track_index}-g0-n{note_index}"
            if note_ref in locations:
                raise PronunciationError("SVP 生成了重复 note_ref。")
            locations[note_ref] = (track_index, note_index)
            track_ids.append(note_ref)
            project_onset = float(onset) + float(offset)
            onset_seconds = _score._round_seconds(
                _score._svp_seconds_at(project_onset, tempo_positions, tempo_points)
            )
            end_seconds = _score._round_seconds(
                _score._svp_seconds_at(
                    project_onset + float(duration), tempo_positions, tempo_points
                )
            )
            notes.append({
                "note_ref": note_ref,
                "track_index": track_index,
                "track_name": track_name,
                "group_index": 0,
                "group": group_identifier,
                "group_name": group_name,
                "note_index": note_index,
                "pitch": pitch,
                "onset": onset,
                "duration": duration,
                "end": float(onset) + float(duration),
                "onset_project": project_onset,
                "onset_seconds": onset_seconds,
                "duration_seconds": _score._round_seconds(
                    float(end_seconds) - float(onset_seconds)
                ),
                "time_unit": "blick",
                "lyric": lyric,
                "phonemes": phonemes,
                "phoneme_state": phoneme_state,
                "phoneme_override": phonemes is not None,
                "phoneme_locked": None,
                "phoneme_source": "svp:note.phonemes" if phonemes is not None else "unavailable",
                "editable": not instrumental,
            })
            intervals.append((project_onset, project_onset + float(duration), note_index))
        _reject_overlaps(intervals, f"SVP 轨道 {track_index}")
        tracks_public.append({
            "track_index": track_index,
            "track_name": track_name,
            "group_index": 0,
            "group": group_identifier,
            "group_name": group_name,
            "instrumental": instrumental,
            "note_count": len(raw_notes),
            "note_refs": track_ids,
        })
    if len(notes) > MAX_NOTES:
        raise PronunciationError(f"SVP 音符超过 {MAX_NOTES} 个安全上限。")
    return {
        "format": "svp",
        "format_version": format_version,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "source_bytes": raw,
        "data": data,
        "root": None,
        "notes": notes,
        "locations": locations,
        "tracks": tracks_public,
        "tempo_events": tempo_events,
        "time_basis": "SVP note onsets, including mainRef.blickOffset",
        "serialization": {
            "kind": "svp-json",
            "utf8_bom": bom,
            "trailing_nul_bytes": trailing_nuls,
        },
    }


def _local_name(tag: Any) -> str | None:
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: Any) -> str | None:
    if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
        return tag[1:tag.index("}")]
    return None


def _qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}" if namespace else local


def _direct_children(parent: ET.Element, names: set[str]) -> list[ET.Element]:
    return [child for child in list(parent) if _local_name(child.tag) in names]


def _one_direct_child(parent: ET.Element, names: set[str], label: str) -> ET.Element:
    matches = _direct_children(parent, names)
    if len(matches) != 1:
        raise PronunciationError(f"{label} 必须恰好有一个节点。")
    return matches[0]


def _element_text(element: ET.Element | None, label: str, *, required: bool = True) -> str | None:
    if element is None:
        if required:
            raise PronunciationError(f"{label} 缺失。")
        return None
    if len(element):
        raise PronunciationError(f"{label} 必须是纯文本节点。")
    value = element.text or ""
    if required and not value.strip():
        raise PronunciationError(f"{label} 不能为空。")
    return value


def _optional_direct_text(parent: ET.Element, names: set[str], label: str) -> str | None:
    matches = _direct_children(parent, names)
    if len(matches) > 1:
        raise PronunciationError(f"{label} 出现多个候选节点。")
    return None if not matches else _element_text(matches[0], label)


def _text_integer(value: str | None, label: str) -> int:
    if value is None:
        raise PronunciationError(f"{label} 缺失。")
    try:
        number = int(value.strip(), 10)
    except (TypeError, ValueError) as exc:
        raise PronunciationError(f"{label} 必须是十进制整数。") from exc
    if abs(number) > MAX_ABSOLUTE_INTEGER:
        raise PronunciationError(f"{label} 超出安全整数范围。")
    return number


def _direct_text_integer(
    parent: ET.Element,
    names: set[str],
    label: str,
    *,
    default: int | None = None,
) -> int:
    value = _optional_direct_text(parent, names, label)
    if value is None:
        if default is None:
            raise PronunciationError(f"{label} 缺失。")
        return default
    return _text_integer(value, label)


def _validate_xml_tree(root: ET.Element) -> None:
    stack = [(root, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_XML_ELEMENTS:
            raise PronunciationError(f"VSQX 节点超过 {MAX_XML_ELEMENTS} 个安全上限。")
        if depth > MAX_STRUCTURE_DEPTH:
            raise PronunciationError(f"VSQX 嵌套超过 {MAX_STRUCTURE_DEPTH} 层安全上限。")
        stack.extend((child, depth + 1) for child in list(node))


def _decode_vsqx(raw: bytes) -> tuple[str, bool]:
    if b"\x00" in raw:
        raise PronunciationError("VSQX 只接收不含 NUL 的 UTF-8/UTF-8 BOM XML。")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")):
        raise PronunciationError("VSQX 不接受 UTF-16/UTF-32；请先保存为 UTF-8。")
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise PronunciationError("VSQX 只接收 UTF-8/UTF-8 BOM XML。") from exc
    if "\x00" in text:
        raise PronunciationError("VSQX 文本包含 NUL。")
    declaration = re.match(r"^<\?xml\b[^?]*\?>", text, flags=re.IGNORECASE | re.DOTALL)
    if declaration:
        encoding = re.search(
            r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]",
            declaration.group(0),
            flags=re.IGNORECASE,
        )
        if encoding and encoding.group(1).lower().replace("_", "-") not in {"utf-8", "utf8"}:
            raise PronunciationError("VSQX XML 声明必须使用 UTF-8 编码。")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, flags=re.IGNORECASE):
        raise PronunciationError("VSQX 包含 DTD/ENTITY，拒绝解析。")
    return text, bom


def _parse_vsqx_xml(raw: bytes) -> tuple[ET.Element, dict[str, Any]]:
    text, bom = _decode_vsqx(raw)
    declaration = re.match(r"^<\?xml\b[^?]*\?>", text, flags=re.IGNORECASE | re.DOTALL)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        root = ET.fromstring(text, parser=parser)
    except (ET.ParseError, RecursionError) as exc:
        raise PronunciationError(f"无法解析 VSQX XML：{exc}") from exc
    _validate_xml_tree(root)
    local = _local_name(root.tag)
    namespace = _namespace(root.tag)
    if local not in {"vsq3", "vsq4"}:
        raise PronunciationError("VSQX 根节点必须是 vsq3 或 vsq4。")
    version = 3 if local == "vsq3" else 4
    if namespace != VSQX_NAMESPACES[version]:
        raise PronunciationError("VSQX 命名空间与 v3/v4 根节点不匹配。")

    root_match = re.search(rf"<{re.escape(local)}(?=[\s>])", text)
    if root_match is None:
        raise PronunciationError("无法定位 VSQX 根节点。")
    header_prefix = text[:root_match.start()]
    without_declaration = re.sub(
        r"^<\?xml\b[^?]*\?>", "", header_prefix, count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if without_declaration.strip():
        raise PronunciationError("VSQX 根节点前只允许 XML 声明和空白。")
    closing = f"</{local}>"
    closing_index = text.rfind(closing)
    if closing_index < root_match.start():
        raise PronunciationError("VSQX 缺少规范根节点结束标签。")
    suffix = text[closing_index + len(closing):]
    if suffix.strip():
        raise PronunciationError("VSQX 根节点后只允许空白。")
    return root, {
        "kind": "vsqx-xml",
        "version": version,
        "namespace": namespace,
        "utf8_bom": bom,
        "header_prefix": header_prefix,
        "suffix": suffix,
        "declaration_present": declaration is not None,
    }


def _vsqx_tick_prefix(root: ET.Element, resolution: int, pre_measure: int) -> int:
    master = _one_direct_child(root, {"masterTrack"}, "masterTrack")
    signatures: list[tuple[int, int, int]] = []
    for element in _direct_children(master, {"timeSig"}):
        bar = _direct_text_integer(element, {"m", "posMes"}, "timeSig measure")
        numerator = _direct_text_integer(element, {"nu", "nume"}, "timeSig numerator")
        denominator = _direct_text_integer(element, {"de", "denomi"}, "timeSig denominator")
        if numerator <= 0 or denominator <= 0:
            raise PronunciationError("VSQX 拍号必须为正。")
        signatures.append((bar, numerator, denominator))
    signatures.sort()
    if not signatures:
        signatures = [(0, 4, 4)]
    measure = 0
    prefix = 0.0
    bar_length = float(resolution * 4)
    for bar, numerator, denominator in signatures:
        if bar < measure:
            raise PronunciationError("VSQX 拍号顺序无效。")
        prefix += (bar - measure) * bar_length
        measure = bar
        bar_length = resolution * 4.0 * numerator / denominator
    prefix += (pre_measure - measure) * bar_length
    if not math.isfinite(prefix) or prefix < 0:
        raise PronunciationError("VSQX preMeasure/tick 前缀无效。")
    return int(round(prefix))


def _tempo_positions(events: list[dict[str, Any]], resolution: int) -> tuple[list[float], list[tuple[float, float]]]:
    ordered: list[tuple[float, float]] = []
    for index, event in enumerate(events):
        position = float(_finite_number(event["position"], f"tempo[{index}].position"))
        bpm = float(_finite_number(event["bpm"], f"tempo[{index}].bpm"))
        if position < 0 or bpm <= 0:
            raise PronunciationError("VSQX tempo 位置必须非负且 BPM 必须为正。")
        ordered.append((position, bpm))
    ordered.sort(key=lambda row: row[0])
    positions = [0.0]
    points = [(0.0, 120.0)]
    current_position = 0.0
    current_seconds = 0.0
    current_bpm = 120.0
    for position, bpm in ordered:
        current_seconds += (position - current_position) * 60.0 / (current_bpm * resolution)
        current_position = position
        current_bpm = bpm
        if position == positions[-1]:
            points[-1] = (current_seconds, bpm)
        else:
            positions.append(position)
            points.append((current_seconds, bpm))
    return positions, points


def _seconds_at_tick(
    position: float,
    positions: list[float],
    points: list[tuple[float, float]],
    resolution: int,
) -> float:
    index = max(0, bisect_right(positions, position) - 1)
    start_seconds, bpm = points[index]
    return start_seconds + (position - positions[index]) * 60.0 / (bpm * resolution)


def _parse_vsqx(raw: bytes, source_path: Path, source_sha256: str) -> dict[str, Any]:
    root, xml_metadata = _parse_vsqx_xml(raw)
    version = int(xml_metadata["version"])
    namespace = str(xml_metadata["namespace"])
    master = _one_direct_child(root, {"masterTrack"}, "masterTrack")
    resolution = _direct_text_integer(master, {"resolution"}, "resolution")
    if not 1 <= resolution <= 1_000_000:
        raise PronunciationError("VSQX resolution 超出安全范围。")
    pre_measure = _direct_text_integer(master, {"preMeasure"}, "preMeasure", default=0)
    if not 0 <= pre_measure <= 10_000:
        raise PronunciationError("VSQX preMeasure 超出安全范围。")
    tick_prefix = _vsqx_tick_prefix(root, resolution, pre_measure)

    tempo_events: list[dict[str, Any]] = []
    for index, element in enumerate(_direct_children(master, {"tempo"})):
        position = _direct_text_integer(element, {"t", "posTick"}, f"tempo[{index}] position")
        encoded_bpm = _direct_text_integer(element, {"v", "bpm"}, f"tempo[{index}] bpm")
        bpm = encoded_bpm / 100.0
        if bpm <= 0:
            raise PronunciationError("VSQX tempo BPM 必须为正。")
        tempo_events.append({"position": position, "bpm": bpm})
    if not tempo_events:
        tempo_events = [{"position": 0, "bpm": 120.0}]
    tempo_events.sort(key=lambda row: row["position"])
    tempo_positions, tempo_points = _tempo_positions(tempo_events, resolution)

    def raw_seconds(tick: float) -> float:
        return _seconds_at_tick(tick, tempo_positions, tempo_points, resolution)

    def project_seconds(tick: float) -> float:
        return raw_seconds(tick + tick_prefix) - raw_seconds(float(tick_prefix))

    for event in tempo_events:
        event["seconds"] = round(
            project_seconds(float(event["position"]) - float(tick_prefix)), 9
        )

    notes: list[dict[str, Any]] = []
    tracks_public: list[dict[str, Any]] = []
    locations: dict[str, tuple[int, int, int]] = {}
    for track_index, track in enumerate(_direct_children(root, {"vsTrack"})):
        if track_index >= MAX_TRACKS:
            raise PronunciationError(f"VSQX 轨道超过 {MAX_TRACKS} 个安全上限。")
        track_name = _optional_direct_text(track, {"name", "trackName"}, "vsTrack name")
        parts = _direct_children(track, {"musicalPart", "vsPart"})
        if len(parts) > MAX_GROUPS:
            raise PronunciationError(f"VSQX 分组超过 {MAX_GROUPS} 个安全上限。")
        part_rows: list[dict[str, Any]] = []
        track_refs: list[str] = []
        track_intervals: list[tuple[float, float, int]] = []
        for part_index, part in enumerate(parts):
            part_start = _direct_text_integer(
                part, {"t", "posTick"}, f"track[{track_index}] part[{part_index}] position",
                default=0,
            )
            if part_start < 0:
                raise PronunciationError("VSQX musicalPart 起点必须非负。")
            part_name = _optional_direct_text(
                part, {"name", "partName"}, f"track[{track_index}] part[{part_index}] name"
            )
            group_id = f"vsqx-part:{track_index}:{part_index}"
            note_elements = _direct_children(part, {"note"})
            intervals: list[tuple[float, float, int]] = []
            for note_index, note in enumerate(note_elements):
                onset = _direct_text_integer(
                    note, {"t", "posTick"},
                    f"track[{track_index}] part[{part_index}] note[{note_index}] onset",
                )
                duration = _direct_text_integer(
                    note, {"dur", "durTick"},
                    f"track[{track_index}] part[{part_index}] note[{note_index}] duration",
                )
                pitch = _direct_text_integer(
                    note, {"n", "noteNum"},
                    f"track[{track_index}] part[{part_index}] note[{note_index}] pitch",
                )
                if onset < 0 or duration <= 0 or not 0 <= pitch <= 127:
                    raise PronunciationError("VSQX 音符时序或音高无效。")
                lyric = _optional_direct_text(
                    note, {"y", "lyric"},
                    f"track[{track_index}] part[{part_index}] note[{note_index}] lyric",
                )
                if lyric is not None:
                    lyric = _safe_text(lyric, "VSQX lyric", maximum_bytes=MAX_PHONEME_BYTES)
                phoneme_elements = _direct_children(note, _PHONEME_CHILD_NAMES)
                if len(phoneme_elements) > 1:
                    raise PronunciationError("VSQX 音符含多个音素节点，归属不明确。")
                phoneme_node = phoneme_elements[0] if phoneme_elements else None
                phoneme_text = _element_text(phoneme_node, "VSQX phoneme", required=False) if phoneme_node is not None else None
                if phoneme_text is not None:
                    phoneme_text = _safe_text(phoneme_text.strip(), "VSQX phoneme")
                    phoneme_text = phoneme_text or None
                lock_value: bool | None = None
                if phoneme_node is not None:
                    raw_lock = phoneme_node.get("lock")
                    if raw_lock is None:
                        lock_value = False
                    elif raw_lock in {"0", "1"}:
                        lock_value = raw_lock == "1"
                    else:
                        raise PronunciationError("VSQX phoneme lock 只能是 0 或 1。")
                if phoneme_text is None:
                    state = "unknown"
                elif lock_value is True:
                    state = "explicit"
                else:
                    state = "derived_unverified"
                note_ref = (
                    f"note-{source_sha256[:16]}-vsqx-t{track_index}-p{part_index}-n{note_index}"
                )
                if note_ref in locations:
                    raise PronunciationError("VSQX 生成了重复 note_ref。")
                locations[note_ref] = (track_index, part_index, note_index)
                project_onset = onset + part_start - tick_prefix
                onset_seconds = round(project_seconds(float(project_onset)), 9)
                end_seconds = round(project_seconds(float(project_onset + duration)), 9)
                notes.append({
                    "note_ref": note_ref,
                    "track_index": track_index,
                    "track_name": track_name,
                    "group_index": part_index,
                    "group": group_id,
                    "group_name": part_name,
                    "note_index": note_index,
                    "pitch": pitch,
                    "onset": onset,
                    "onset_project": project_onset,
                    "duration": duration,
                    "end": onset + duration,
                    "onset_seconds": onset_seconds,
                    "duration_seconds": round(end_seconds - onset_seconds, 9),
                    "time_unit": "tick",
                    "lyric": lyric,
                    "phonemes": phoneme_text,
                    "phoneme_state": state,
                    "phoneme_override": state == "explicit",
                    "phoneme_locked": lock_value if phoneme_node is not None else False,
                    "phoneme_source": (
                        f"vsqx:{_local_name(phoneme_node.tag)}"
                        if phoneme_node is not None else "unavailable"
                    ),
                    "editable": True,
                })
                track_refs.append(note_ref)
                intervals.append((float(onset), float(onset + duration), note_index))
                track_intervals.append((float(project_onset), float(project_onset + duration), len(track_intervals)))
            _reject_overlaps(
                intervals, f"VSQX 轨道 {track_index} musicalPart {part_index}"
            )
            part_rows.append({
                "group_index": part_index,
                "group": group_id,
                "group_name": part_name,
                "part_start": part_start,
                "note_count": len(note_elements),
            })
        _reject_overlaps(track_intervals, f"VSQX 轨道 {track_index}")
        tracks_public.append({
            "track_index": track_index,
            "track_name": track_name,
            "parts": part_rows,
            "note_count": len(track_refs),
            "note_refs": track_refs,
        })
    if len(notes) > MAX_NOTES:
        raise PronunciationError(f"VSQX 音符超过 {MAX_NOTES} 个安全上限。")
    serialization = dict(xml_metadata)
    serialization.pop("kind", None)
    return {
        "format": "vsqx",
        "format_version": version,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "source_bytes": raw,
        "data": None,
        "root": root,
        "notes": notes,
        "locations": locations,
        "tracks": tracks_public,
        "tempo_events": tempo_events,
        "time_basis": "VSQX project ticks after preMeasure normalization",
        "serialization": {"kind": "vsqx-xml", **serialization},
        "namespace": namespace,
        "resolution": resolution,
        "tick_prefix": tick_prefix,
    }


def _parse_midi(raw: bytes, source_path: Path, source_sha256: str) -> dict[str, Any]:
    parsed = _score._parse_midi(raw, str(source_path))
    if parsed.source_sha256 != source_sha256:
        raise PronunciationError("MIDI 在读取期间发生变化，请重试。")
    score = _score._midi_score(parsed)
    positions, points = _score._tempo_timeline(parsed.tempo_events, parsed.midi.ticks_per_beat)
    tracks_by_index = {int(track["track_index"]): track for track in score["tracks"]}
    notes: list[dict[str, Any]] = []
    locations: dict[str, tuple[int, int]] = {}
    for track_index, track in enumerate(parsed.tracks):
        track_row = tracks_by_index[track_index]
        for note_index, note in enumerate(track.notes):
            view = _score._note_view(
                note, positions, points, parsed.midi.ticks_per_beat
            )
            duration = view.get("duration_tick")
            if type(duration) is not int or duration <= 0:
                raise PronunciationError("MIDI 音素读取要求音符具有唯一、正的关闭事件。")
            note_ref = str(view["note_id"])
            if note_ref in locations:
                raise PronunciationError("MIDI note_id 不唯一，拒绝生成读音清单。")
            locations[note_ref] = (track_index, note_index)
            notes.append({
                "note_ref": note_ref,
                "track_index": track_index,
                "track_name": track_row.get("name"),
                "group_index": int(view["channel"]),
                "group": f"midi-channel:{int(view['channel'])}",
                "group_name": f"Channel {int(view['channel']) + 1}",
                "note_index": note_index,
                "pitch": int(view["pitch"]),
                "onset": int(view["onset_tick"]),
                "onset_project": int(view["onset_tick"]),
                "duration": int(duration),
                "end": int(view["end_tick"]),
                "onset_seconds": view.get("onset_seconds"),
                "duration_seconds": view.get("duration_seconds"),
                "time_unit": "tick",
                "lyric": view.get("lyric"),
                "phonemes": None,
                "phoneme_state": "unknown",
                "phoneme_override": False,
                "phoneme_locked": None,
                "phoneme_source": "unavailable",
                "editable": False,
            })
    if len(notes) > MAX_NOTES:
        raise PronunciationError(f"MIDI 音符超过 {MAX_NOTES} 个安全上限。")
    return {
        "format": "midi",
        "format_version": score.get("midi_type"),
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "source_bytes": raw,
        "data": None,
        "root": None,
        "notes": notes,
        "locations": locations,
        "tracks": [
            {
                "track_index": int(track["track_index"]),
                "track_name": track.get("name"),
                "group_index": None,
                "group": None,
                "group_name": None,
                "instrumental": False,
                "note_count": int(track["note_count"]),
                "note_refs": list(track["note_ids"]),
            }
            for track in score["tracks"]
        ],
        "tempo_events": score["timing"]["tempo_events"],
        "time_basis": "MIDI ticks (PPQ)",
        "serialization": {"kind": "midi-read-only"},
        "ticks_per_beat": score["ticks_per_beat"],
    }


def _load_project(path: Any) -> dict[str, Any]:
    source, raw, source_sha256 = _read_source(path)
    format_name = _format_for_path(source)
    if format_name == "midi":
        return _parse_midi(raw, source, source_sha256)
    if format_name == "svp":
        return _parse_svp(raw, source, source_sha256)
    return _parse_vsqx(raw, source, source_sha256)


def _phoneme_summary(notes: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"unknown": 0, "derived_unverified": 0, "explicit": 0}
    for note in notes:
        state = note["phoneme_state"]
        if state not in summary:
            raise PronunciationError("内部音素状态无效。")
        summary[state] += 1
    return summary


def read_pronunciation(path, limit=100, offset=0, export_runtime=None) -> dict[str, Any]:
    """Read native pronunciation facts with stable references and pagination.

    ``export_runtime`` is accepted for API compatibility with the fixed MCP
    worker. SVP/VSQX parsing uses only the format's native JSON/XML structures,
    so no export library is imported or required.
    """

    del export_runtime
    page_limit, page_offset = _validate_limit_offset(limit, offset)
    project = _load_project(path)
    all_notes = project["notes"]
    total = len(all_notes)
    page = all_notes[page_offset:page_offset + page_limit]
    page_refs={note['note_ref'] for note in page}
    tracks=[]
    for track in project['tracks']:
        refs=track.get('note_refs',[])
        tracks.append({**track,'note_refs_total':len(refs),
                       'note_refs':[ref for ref in refs if ref in page_refs]})
    returned = len(page)
    next_offset = page_offset + returned if page_offset + returned < total else None
    write_supported = project["format"] in {"svp", "vsqx"}
    warnings = [
        "显式音素只表示工程中存在或已写入的文本证据，不代表该读音正确，也不证明声库实际发音。",
        "未存显式音素时保持 unknown；多音字、延音等不会被自动猜测。",
    ]
    if project["format"] == "midi":
        warnings.append("MIDI 使用现有 note_id，并且没有标准音素字段，因此所有音素事实为 unknown。")
    elif project["format"] == "svp":
        warnings.append("SVP 只展开未使用库组/额外分组引用的 mainGroup.notes。")
    else:
        warnings.append("VSQX 仅接收 UTF-8/UTF-8 BOM，XML 中的 DTD/ENTITY 与非 UTF-8 编码会被拒绝。")
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": READ_KIND,
        "source_path": project["source_path"],
        "source_sha256": project["source_sha256"],
        "format": project["format"],
        "format_version": project["format_version"],
        "time_basis": project["time_basis"],
        "timing": {
            "unit": "tick" if project["format"] != "svp" else "blick",
            "ticks_per_beat": (
                project.get("ticks_per_beat", project.get("resolution"))
                if project["format"] != "svp" else SVP_UNITS_PER_QUARTER
            ),
            "tempo_events": project["tempo_events"][:1024],
            "tempo_events_total": len(project['tempo_events']),
            "tempo_events_truncated": len(project['tempo_events'])>1024,
        },
        "tracks": tracks,
        "note_count": total,
        "notes": page,
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "total": total,
            "returned": returned,
            "has_more": next_offset is not None,
            "next_offset": next_offset,
        },
        "phoneme_summary": _phoneme_summary(all_notes),
        "write_supported": write_supported,
        "write_unsupported_reason": None if write_supported else "MIDI 音素写入不受支持。",
        "warnings": warnings,
        "inference_performed": False,
        "g2p_performed": False,
        "provider_access_performed": False,
        "acoustic_pronunciation_verified": False,
        "disclaimer": "本读取结果只描述工程中实际存在的文本与锁定字段，不构成音素正确性或听觉验收。",
    }
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def _normalise_edits(project: dict[str, Any], edits: Any) -> list[dict[str, str]]:
    if project["format"] not in {"svp", "vsqx"}:
        raise PronunciationError("只有 SVP 和 VSQX 支持显式音素替换。")
    if not isinstance(edits, list) or not edits:
        raise PronunciationError("edits 必须是非空数组。")
    if len(edits) > MAX_EDITS:
        raise PronunciationError(f"edits 最多允许 {MAX_EDITS} 项。")
    by_ref = {note["note_ref"]: note for note in project["notes"]}
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise PronunciationError(f"edits[{index}] 必须是对象。")
        keys = set(edit)
        if keys != _EDIT_KEYS:
            missing = sorted(_EDIT_KEYS - keys)
            extra = sorted(keys - _EDIT_KEYS, key=str)
            raise PronunciationError(
                f"edits[{index}] 字段必须严格为 note_ref/phonemes；缺少={missing}，额外={extra}。"
            )
        note_ref = _safe_text(
            edit["note_ref"], f"edits[{index}].note_ref",
            allow_empty=False, maximum_bytes=MAX_NOTE_REF_BYTES,
        )
        phonemes = _safe_text(edit["phonemes"], f"edits[{index}].phonemes")
        if note_ref in seen:
            raise PronunciationError(f"edits 重复包含 note_ref={note_ref}。")
        seen.add(note_ref)
        note = by_ref.get(note_ref)
        if note is None:
            raise PronunciationError(f"未知或旧 note_ref：{note_ref}。请按当前 source_sha256 重新读取。")
        if not note["editable"]:
            raise PronunciationError(f"音符 {note_ref} 所在轨道不支持音素编辑。")
        if project["format"] == "vsqx" and not phonemes:
            raise PronunciationError("VSQX 音素替换不能使用空字符串。")
        if phonemes:
            if phonemes != phonemes.strip():
                raise PronunciationError(f"edits[{index}].phonemes 不能带首尾空白。")
            if any(
                ord(character) < 32 or ord(character) == 127
                or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                for character in phonemes
            ):
                raise PronunciationError(f"edits[{index}].phonemes 包含控制字符。")
        normalized.append({"note_ref": note_ref, "phonemes": phonemes})
    return normalized


def _phoneme_state(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "phonemes": note["phonemes"],
        "phoneme_state": note["phoneme_state"],
        "phoneme_override": note["phoneme_override"],
        "phoneme_locked": note["phoneme_locked"],
    }


def _after_state(project_format: str, phonemes: str) -> dict[str, Any]:
    if project_format == "svp":
        explicit = bool(phonemes)
        return {
            "phonemes": phonemes or None,
            "phoneme_state": "explicit" if explicit else "unknown",
            "phoneme_override": explicit,
            "phoneme_locked": None,
        }
    return {
        "phonemes": phonemes,
        "phoneme_state": "explicit",
        "phoneme_override": True,
        "phoneme_locked": True,
    }


def _svp_note_object(data: dict[str, Any], location: tuple[int, int]) -> dict[str, Any]:
    track_index, note_index = location
    try:
        return data["tracks"][track_index]["mainGroup"]["notes"][note_index]
    except (IndexError, KeyError, TypeError) as exc:
        raise PronunciationError("SVP 音符定位失效。") from exc


def _vsqx_note_element(root: ET.Element, location: tuple[int, int, int]) -> ET.Element:
    track_index, part_index, note_index = location
    tracks = _direct_children(root, {"vsTrack"})
    try:
        parts = _direct_children(tracks[track_index], {"musicalPart", "vsPart"})
        notes = _direct_children(parts[part_index], {"note"})
        return notes[note_index]
    except (IndexError, KeyError, TypeError) as exc:
        raise PronunciationError("VSQX 音符定位失效。") from exc


def _set_vsqx_phoneme(note: ET.Element, namespace: str, version: int, phonemes: str) -> None:
    matches = _direct_children(note, _PHONEME_CHILD_NAMES)
    if len(matches) > 1:
        raise PronunciationError("VSQX 音符含多个音素节点，拒绝修改。")
    if matches:
        target = matches[0]
        target.text = phonemes
        target.set("lock", "1")
        return
    local = "p" if version == 4 else "phnms"
    target = ET.Element(_qname(namespace, local))
    target.text = phonemes
    target.set("lock", "1")
    insert_at = len(note)
    trailing_names = {"nStyle", "noteStyle"}
    for index, child in enumerate(list(note)):
        if _local_name(child.tag) in trailing_names:
            insert_at = index
            break
    note.insert(insert_at, target)


def _serialize_svp(project: dict[str, Any], data: dict[str, Any]) -> bytes:
    serialization = project["serialization"]
    text = json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    raw = text.encode("utf-8")
    if serialization.get("utf8_bom"):
        raw = b"\xef\xbb\xbf" + raw
    raw += b"\x00" * int(serialization.get("trailing_nul_bytes", 0))
    if len(raw) > MAX_CANDIDATE_BYTES:
        raise PronunciationError(f"候选 SVP 超过 {MAX_CANDIDATE_BYTES} 字节安全上限。")
    return raw


def _serialize_vsqx(project: dict[str, Any], root: ET.Element) -> bytes:
    serialization = project["serialization"]
    namespace = project["namespace"]
    ET.register_namespace("", namespace)
    try:
        body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    except (TypeError, ValueError) as exc:
        raise PronunciationError(f"无法序列化 VSQX 候选：{exc}") from exc
    text = serialization.get("header_prefix", "") + body + serialization.get("suffix", "")
    raw = text.encode("utf-8")
    if serialization.get("utf8_bom"):
        raw = b"\xef\xbb\xbf" + raw
    if b"\x00" in raw:
        raise PronunciationError("候选 VSQX 含 NUL。")
    if len(raw) > MAX_CANDIDATE_BYTES:
        raise PronunciationError(f"候选 VSQX 超过 {MAX_CANDIDATE_BYTES} 字节安全上限。")
    return raw


def _xml_semantic_tail(value: ET.Element) -> str | None:
    retro = value.tail
    return retro if retro is not None and retro.strip() else None


def _xml_semantic(value: ET.Element) -> Any:
    if value.tag is ET.Comment:
        return ["comment", value.text or "", _xml_semantic_tail(value)]
    if value.tag is ET.PI:
        return ["pi", value.text or "", _xml_semantic_tail(value)]
    children = [_xml_semantic(child) for child in list(value)]
    text = value.text
    if children and (text is None or not text.strip()):
        text = None
    if not children and text is None:
        text = ""
    return [
        "element",
        str(value.tag),
        [[str(key), str(item)] for key, item in sorted(value.attrib.items())],
        text,
        children,
        _xml_semantic_tail(value),
    ]


def _vsqx_semantic_without_edited_phonemes(
    project: dict[str, Any],
    root: ET.Element,
    locations: set[tuple[int, int, int]],
) -> Any:
    cloned = copy.deepcopy(root)
    namespace = project["namespace"]
    for location in sorted(locations):
        note = _vsqx_note_element(cloned, location)
        for child in list(note):
            if _local_name(child.tag) in _PHONEME_CHILD_NAMES:
                note.remove(child)
        placeholder = ET.Element(_qname(namespace, "vwa-phoneme-placeholder"))
        note.append(placeholder)
    return _xml_semantic(cloned)


def _svp_semantic_without_edited_phonemes(
    project: dict[str, Any],
    data: dict[str, Any],
    locations: set[tuple[int, int]],
) -> Any:
    cloned = copy.deepcopy(data)
    for location in sorted(locations):
        note = _svp_note_object(cloned, location)
        note["phonemes"] = "<vwa-phoneme-placeholder>"
    return cloned


def _nonphoneme_signature(
    project: dict[str, Any],
    *,
    data: dict[str, Any] | None = None,
    root: ET.Element | None = None,
    locations: set[Any],
) -> str:
    if project["format"] == "svp":
        value = _svp_semantic_without_edited_phonemes(
            project, project["data"] if data is None else data, locations
        )
    else:
        value = _vsqx_semantic_without_edited_phonemes(
            project, project["root"] if root is None else root, locations
        )
    return _canonical_digest(value)


def _note_core(note: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in note.items()
        if key not in {"note_ref", "phonemes", "phoneme_state", "phoneme_override", "phoneme_locked", "phoneme_source"}
    }


def _note_by_location(project: dict[str, Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
    if project["format"] == "svp":
        return {
            (note["track_index"], note["note_index"]): note
            for note in project["notes"]
        }
    return {
        (note["track_index"], note["group_index"], note["note_index"]): note
        for note in project["notes"]
    }


def _target_location(project: dict[str, Any], note_ref: str) -> tuple[Any, ...]:
    location = project["locations"].get(note_ref)
    if location is None:
        raise PronunciationError(f"未知或旧 note_ref：{note_ref}。")
    return tuple(location)


def _mutate_candidate(project: dict[str, Any], edits: list[dict[str, str]]) -> tuple[bytes, dict[str, Any]]:
    if project["format"] == "svp":
        data = copy.deepcopy(project["data"])
        for edit in edits:
            location = _target_location(project, edit["note_ref"])
            note = _svp_note_object(data, location)
            note["phonemes"] = edit["phonemes"]
        candidate_bytes = _serialize_svp(project, data)
        candidate = _parse_svp(candidate_bytes, Path(project["source_path"]), _sha256(candidate_bytes))
        return candidate_bytes, candidate
    root = copy.deepcopy(project["root"])
    version = int(project["format_version"])
    namespace = project["namespace"]
    for edit in edits:
        location = _target_location(project, edit["note_ref"])
        note = _vsqx_note_element(root, location)
        _set_vsqx_phoneme(note, namespace, version, edit["phonemes"])
    candidate_bytes = _serialize_vsqx(project, root)
    candidate = _parse_vsqx(candidate_bytes, Path(project["source_path"]), _sha256(candidate_bytes))
    return candidate_bytes, candidate


def _vsqx_phoneme_aux(root: ET.Element, location: tuple[int, int, int]) -> Any:
    note = _vsqx_note_element(root, location)
    matches = _direct_children(note, _PHONEME_CHILD_NAMES)
    if not matches:
        return None
    if len(matches) != 1:
        raise PronunciationError("VSQX 音素节点归属不明确。")
    node = matches[0]
    return {
        "tag": str(node.tag),
        "attributes_without_lock": {
            key: value for key, value in sorted(node.attrib.items()) if key != "lock"
        },
    }


def _validate_candidate(
    project: dict[str, Any],
    candidate: dict[str, Any],
    edits: list[dict[str, str]],
) -> None:
    if candidate["format"] != project["format"]:
        raise PronunciationError("候选格式与源格式不一致。")
    if len(candidate["notes"]) != len(project["notes"]):
        raise PronunciationError("候选改变了音符数量。")
    locations = {_target_location(project, edit["note_ref"]) for edit in edits}
    source_signature = _nonphoneme_signature(project, locations=locations)
    candidate_signature = _nonphoneme_signature(candidate, locations=locations)
    if source_signature != candidate_signature:
        raise PronunciationError("候选改变了音素之外的结构、时序、歌词、速度、曲线或 metadata。")

    source_notes = _note_by_location(project)
    candidate_notes = _note_by_location(candidate)
    if set(source_notes) != set(candidate_notes):
        raise PronunciationError("候选改变了音符定位集合。")
    for location in sorted(source_notes):
        source_note = source_notes[location]
        candidate_note = candidate_notes[location]
        if _note_core(source_note) != _note_core(candidate_note):
            raise PronunciationError(f"候选改变了音符 {source_note['note_ref']} 的非音素事实。")
        if location not in locations:
            if _phoneme_state(source_note) != _phoneme_state(candidate_note):
                raise PronunciationError(f"候选意外修改了未编辑音符 {source_note['note_ref']} 的音素。")

    edit_by_ref = {edit["note_ref"]: edit for edit in edits}
    for edit in edits:
        location = _target_location(project, edit["note_ref"])
        candidate_note = candidate_notes[location]
        expected = _after_state(project["format"], edit["phonemes"])
        if _phoneme_state(candidate_note) != expected:
            raise PronunciationError(f"候选音符 {edit['note_ref']} 的音素回读结果不符合计划。")
        if project["format"] == "vsqx":
            source_aux = _vsqx_phoneme_aux(project["root"], location)
            candidate_aux = _vsqx_phoneme_aux(candidate["root"], location)
            if source_aux is not None and source_aux != candidate_aux:
                raise PronunciationError("VSQX 候选改变了音素节点中 lock 之外的属性或标签。")
        if edit["note_ref"] not in edit_by_ref:
            raise PronunciationError("内部 edit 映射失效。")


def _build_plan(project: dict[str, Any], edits: Any) -> tuple[dict[str, Any], bytes]:
    normalized = _normalise_edits(project, edits)
    candidate_bytes, candidate = _mutate_candidate(project, normalized)
    _validate_candidate(project, candidate, normalized)
    source_notes = {note["note_ref"]: note for note in project["notes"]}
    diff: list[dict[str, Any]] = []
    lock_changes: list[dict[str, Any]] = []
    for edit in normalized:
        source_note = source_notes[edit["note_ref"]]
        before = _phoneme_state(source_note)
        after = _after_state(project["format"], edit["phonemes"])
        diff.append({
            "note_ref": edit["note_ref"],
            "track_index": source_note["track_index"],
            "group_index": source_note["group_index"],
            "pitch": source_note["pitch"],
            "onset": source_note["onset"],
            "duration": source_note["duration"],
            "lyric": source_note["lyric"],
            "before": before,
            "after": after,
        })
        if project["format"] == "vsqx":
            lock_changes.append({
                "note_ref": edit["note_ref"],
                "before": before["phoneme_locked"],
                "after": after["phoneme_locked"],
                "changed": before["phoneme_locked"] != after["phoneme_locked"],
            })
    warnings = [
        "音素 diff 只证明写入值或锁定字段发生了变化，不代表音素正确或声库实际发音正确。",
        "候选会完整回读，并比较全部非音素 JSON/XML 语义以及每个音符的音高、时序和歌词。",
    ]
    validation = {
        "source_unchanged_during_plan": True,
        "candidate_reparsed": True,
        "non_phoneme_metadata_unchanged": True,
        "note_pitch_timing_lyric_unchanged": True,
        "only_explicit_phoneme_changes": True,
        "acoustic_pronunciation_verified": False,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "source_path": project["source_path"],
        "source_sha256": project["source_sha256"],
        "format": project["format"],
        "format_version": project["format_version"],
        "time_basis": project["time_basis"],
        "serialization": copy.deepcopy(project["serialization"]),
        "edit_count": len(normalized),
        "note_count": len(project["notes"]),
        "edits": normalized,
        "diff": diff,
        "lock_changes": lock_changes,
        "expected_candidate_sha256": _sha256(candidate_bytes),
        "validation": validation,
        "warnings": warnings,
        "inference_performed": False,
        "g2p_performed": False,
        "provider_access_performed": False,
        "acoustic_pronunciation_verified": False,
    }
    plan = dict(payload)
    plan["plan_sha256"] = _canonical_digest(payload)
    json.dumps(plan, ensure_ascii=False, allow_nan=False)
    return plan, candidate_bytes


def plan_pronunciation_patch(path, expected_sha256, edits, export_runtime=None) -> dict[str, Any]:
    """Create a strict SVP/VSQX phoneme replacement plan without writing the source."""

    del export_runtime
    source, _raw, source_sha = _read_source(path)
    if source.suffix.lower() not in {".svp", ".vsqx"}:
        raise PronunciationError("只有 SVP/VSQX 支持音素补丁；USTX 当前明确不支持。")
    expected = _normalise_sha(expected_sha256, "expected_sha256")
    if expected != source_sha:
        raise PronunciationError("源文件 SHA-256 已变化；请重新读取并规划音素补丁。")
    project = _load_project(source)
    plan, _candidate_bytes = _build_plan(project, edits)
    _source_after, _raw_after, sha_after = _read_source(source)
    if sha_after != source_sha:
        raise PronunciationError("源文件在规划期间发生变化；已拒绝返回计划。")
    return plan


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise PronunciationError("plan 必须是对象。")
    supplied = dict(plan)
    if set(supplied) != _PLAN_KEYS:
        missing = sorted(_PLAN_KEYS - set(supplied))
        extra = sorted(set(supplied) - _PLAN_KEYS, key=str)
        raise PronunciationError(f"plan 字段不完整或不支持；缺少={missing}，额外={extra}。")
    if (
        type(supplied.get("schema_version")) is not int
        or supplied.get("schema_version") != SCHEMA_VERSION
        or supplied.get("kind") != PLAN_KIND
    ):
        raise PronunciationError("plan schema 或 kind 无效。")
    if supplied.get("format") not in {"svp", "vsqx"}:
        raise PronunciationError("plan.format 只允许 svp 或 vsqx。")
    if not isinstance(supplied.get("source_path"), str) or not supplied["source_path"]:
        raise PronunciationError("plan.source_path 无效。")
    _normalise_sha(supplied.get("source_sha256"), "plan.source_sha256")
    _normalise_sha(supplied.get("expected_candidate_sha256"), "plan.expected_candidate_sha256")
    for field in (
        "inference_performed", "g2p_performed", "provider_access_performed",
        "acoustic_pronunciation_verified",
    ):
        if supplied.get(field) is not False:
            raise PronunciationError(f"plan.{field} 必须为 false。")
    stored_digest = _normalise_sha(supplied.get("plan_sha256"), "plan.plan_sha256")
    digest_payload = {key: value for key, value in supplied.items() if key != "plan_sha256"}
    if _canonical_digest(digest_payload) != stored_digest:
        raise PronunciationError("plan_sha256 与计划内容不一致，拒绝已被篡改的计划。")
    if not isinstance(supplied.get("edits"), list) or not 1 <= len(supplied["edits"]) <= MAX_EDITS:
        raise PronunciationError("plan.edits 数量无效。")
    for index, edit in enumerate(supplied["edits"]):
        if not isinstance(edit, Mapping) or set(edit) != _EDIT_KEYS:
            raise PronunciationError(f"plan.edits[{index}] 字段无效。")
    return supplied


def _normalise_output_root(output_root: Any) -> Path:
    if output_root is None or isinstance(output_root, (bool, bytes)):
        raise PronunciationError("output_root 必须是目录路径。")
    try:
        if isinstance(output_root, str) and not output_root.strip():
            raise PronunciationError("output_root 不能为空。")
        root = Path(output_root).expanduser().resolve()
        native = _io_path(root)
        if native.exists() and not native.is_dir():
            raise PronunciationError("output_root 已存在但不是目录。")
        native.mkdir(parents=True, exist_ok=True)
        return root
    except PronunciationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise PronunciationError(f"output_root 无效：{exc}") from exc


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def _existing_file(path: Path, label: str) -> bytes | None:
    return _score._existing_file(path, label)


def _publish_exclusive(path: Path, data: bytes) -> None:
    _score._publish_exclusive(path, data)


def apply_pronunciation_patch(path, plan, output_root, export_runtime=None) -> dict[str, Any]:
    """Regenerate, reparse and atomically publish an independent phoneme candidate."""

    del export_runtime
    supplied = _validate_plan(plan)
    source, _raw, current_sha = _read_source(path)
    requested_source = Path(supplied["source_path"]).expanduser().resolve()
    if not _same_path(source, requested_source):
        raise PronunciationError("当前 path 与计划绑定的 source_path 不一致。")
    if current_sha != _normalise_sha(supplied["source_sha256"], "plan.source_sha256"):
        raise PronunciationError("当前源文件 SHA-256 与计划不一致；请重新读取和规划。")
    project = _load_project(source)
    regenerated, candidate_bytes = _build_plan(project, supplied["edits"])
    if regenerated != supplied:
        raise PronunciationError("当前源重新生成的计划与提交计划完全不一致；拒绝该计划。")
    candidate_sha = _sha256(candidate_bytes)
    if candidate_sha != supplied["expected_candidate_sha256"]:
        raise PronunciationError("重新生成的候选 SHA-256 与计划不一致。")

    root = _normalise_output_root(output_root)
    stem = f"vwa-pronunciation-{supplied['plan_sha256']}"
    suffix = ".svp" if supplied["format"] == "svp" else ".vsqx"
    candidate_path = (root / f"{stem}{suffix}").resolve()
    receipt_path = (root / f"{stem}.receipt.json").resolve()
    if _same_path(candidate_path, source) or _same_path(receipt_path, source):
        raise PronunciationError("输出路径与源文件冲突；拒绝覆盖源文件。")

    validation = {
        "source_unchanged": True,
        "candidate_reparsed": True,
        "non_phoneme_metadata_unchanged": True,
        "note_pitch_timing_lyric_unchanged": True,
        "only_explicit_phoneme_changes": True,
        "acoustic_pronunciation_verified": False,
    }
    receipt_payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "plan_sha256": supplied["plan_sha256"],
        "source_path": str(source),
        "source_sha256": supplied["source_sha256"],
        "source_unchanged": True,
        "format": supplied["format"],
        "format_version": supplied["format_version"],
        "candidate_path": str(candidate_path),
        "candidate_sha256": candidate_sha,
        "candidate_bytes": len(candidate_bytes),
        "receipt_path": str(receipt_path),
        "diff": supplied["diff"],
        "lock_changes": supplied["lock_changes"],
        "validation": validation,
        "warnings": supplied["warnings"],
        "inference_performed": False,
        "g2p_performed": False,
        "provider_access_performed": False,
        "acoustic_pronunciation_verified": False,
    }
    receipt = dict(receipt_payload)
    receipt["receipt_sha256"] = _canonical_digest(receipt_payload)
    receipt_bytes = _canonical_bytes(receipt, pretty=True)

    existing_candidate = _existing_file(candidate_path, "音素候选")
    if existing_candidate is not None and existing_candidate != candidate_bytes:
        raise PronunciationError("候选路径已有不同内容；拒绝覆盖或信任碰撞。")
    existing_receipt = _existing_file(receipt_path, "音素 receipt")
    if existing_receipt is not None:
        try:
            existing_value = json.loads(existing_receipt.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PronunciationError(f"已有 receipt 不是有效 UTF-8 JSON：{exc}") from exc
        if not isinstance(existing_value, dict):
            raise PronunciationError("已有 receipt 根节点不是对象。")
        stored = existing_value.get("receipt_sha256")
        unsigned = {key: value for key, value in existing_value.items() if key != "receipt_sha256"}
        if stored != _canonical_digest(unsigned):
            raise PronunciationError("已有 receipt 摘要不匹配，拒绝信任碰撞。")
        if existing_receipt != receipt_bytes:
            raise PronunciationError("已有 receipt 与当前计划不一致；拒绝覆盖或信任碰撞。")

    _source_before, _raw_before, before_sha = _read_source(source)
    if before_sha != current_sha:
        raise PronunciationError("源文件在发布前发生变化；未发布候选。")
    if existing_candidate is None:
        _publish_exclusive(candidate_path, candidate_bytes)
    if _existing_file(candidate_path, "音素候选") != candidate_bytes:
        raise PronunciationError("候选发布后字节校验失败。")
    if existing_receipt is None:
        _publish_exclusive(receipt_path, receipt_bytes)
    if _existing_file(receipt_path, "音素 receipt") != receipt_bytes:
        raise PronunciationError("receipt 发布后字节校验失败。")
    _source_after, _raw_after, after_sha = _read_source(source)
    if after_sha != current_sha:
        raise PronunciationError("源文件在发布期间发生变化；拒绝返回不可再验证的 receipt。")
    return receipt
