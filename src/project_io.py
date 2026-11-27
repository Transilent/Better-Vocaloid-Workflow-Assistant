"""Pure read-only project/media loaders and inspection helpers shared across layers.

This leaf module deliberately avoids importing workflow/orchestration layers
(pipeline, task_contract, tuning_workflow, project_exports, publication,
calibration_session) so every upper layer can depend on it without cycles.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
from importlib import import_module
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any

from agent_primitives import json_digest as _json_digest
from common import app_path, file_hash, load_config, native_path
import style_catalog
from svp_tuning import Layout
from tuning_clock import ProjectClock, SV_QUARTER as _SV_QUARTER
from tuning_native import NativeProject

STYLE_ADAPTERS = {
    'ustx': 'ustx_tuning',
    'vpr': 'vpr_tuning',
    'vsqx': 'vsqx_tuning',
    'acep': 'ace_tuning:ACEP',
    'ufdata': 'ace_tuning:UFDATA',
}
TUNING_SUFFIXES = frozenset('.' + fmt for fmt in ('svp', *STYLE_ADAPTERS))


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    format: str
    sha256: str
    size: int
    data: dict


def project_data(path):
    """Read an SVP JSON project stripping any trailing NUL byte."""
    return json.loads(Path(native_path(path)).read_bytes().removesuffix(b'\x00').decode('utf-8-sig'))


USTX_MAX_BYTES = 64 * 1024**2
_USTX_TEXT_FIELDS = frozenset((
    'ustx_version', 'name', 'comment', 'lyric', 'singer', 'phonemizer',
    'phoneme', 'relative_path', 'abbr', 'track_name', 'track_color',
    'output_dir', 'cache_dir', 'renderer', 'resampler', 'wavtool',
))
PROJECT_FORMATS = ('ustx', 'vsqx', 'svp')
AUDIO_FORMATS = ('wav', 'flac', 'mp3', 'ogg')


def validate_export_options(projects, audio):
    if (not isinstance(projects, list) or not isinstance(audio, list)
            or any(fmt not in PROJECT_FORMATS for fmt in projects)
            or any(fmt not in AUDIO_FORMATS for fmt in audio)
            or len(set(projects)) != len(projects) or len(set(audio)) != len(audio)):
        raise ValueError('请选择支持的工程与音频格式。')


def library():
    import os
    import sys
    from common import ROOT
    runtime = ROOT / 'dependencies/export'
    if not (runtime / 'libresvip/__init__.py').is_file():
        raise FileNotFoundError('工程转换运行库缺失，请恢复 dependencies/export 目录。')
    settings = ROOT / 'cache/export-app'
    settings.mkdir(parents=True, exist_ok=True)
    os.environ['APPDATA'] = os.environ['LOCALAPPDATA'] = str(settings)
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    from libresvip.plugins.ustx.ustx_converter import OpenUtauConverter
    from libresvip.plugins.vsqx.vsqx_converter import VsqxConverter
    from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
    from libresvip.model.base import Project, SingingTrack, InstrumentalTrack
    return {'ustx': OpenUtauConverter, 'vsqx': VsqxConverter, 'svp': SynthVStudioConverter}, Project, SingingTrack, InstrumentalTrack


def read_ustx_bytes(raw, *, require_version=True):
    """Parse ordinary OpenUtau YAML without aliases, ambiguous keys or YAML 1.1 booleans."""
    import re
    import yaml

    fast_loader = getattr(yaml, 'CSafeLoader', None)

    class Loader(fast_loader or yaml.SafeLoader):
        depth = 0

        def compose_node(self, parent, index):
            if self.depth >= 64 or self.check_event(yaml.AliasEvent):
                raise ValueError('USTX 不支持 YAML 别名或过深的嵌套。')
            self.depth += 1
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError('USTX 包含非文本或重复的 YAML 键。')
                if (key in _USTX_TEXT_FIELDS and isinstance(value_node, yaml.ScalarNode)
                        and value_node.tag != 'tag:yaml.org,2002:null'):
                    value = value_node.value
                else:
                    value = self.construct_object(value_node, deep=deep)
                result[key] = value
            return result

    Loader.yaml_implicit_resolvers = {
        ch: [rule for rule in rules if rule[0] not in
             ('tag:yaml.org,2002:bool', 'tag:yaml.org,2002:timestamp')]
        for ch, rules in Loader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver('tag:yaml.org,2002:bool',
                                 re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'), list('tTfF'))
    if not 0 < len(raw) <= USTX_MAX_BYTES:
        raise ValueError('USTX 工程为空或超过 64 MiB。')
    try:
        text = raw.decode('utf-8-sig')
        if fast_loader:
            depth = 0
            for event in yaml.parse(text, Loader=fast_loader):
                if isinstance(event, yaml.AliasEvent):
                    raise ValueError('USTX 不支持 YAML 别名。')
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                    if depth >= 64:
                        raise ValueError('USTX 的 YAML 嵌套过深。')
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
        data = yaml.load(text, Loader=Loader)
    except (UnicodeError, yaml.YAMLError, TypeError, OverflowError) as error:
        raise ValueError('USTX 工程不是有效的 UTF-8 YAML。') from error
    if not isinstance(data, dict) or (require_version and 'ustx_version' not in data):
        raise ValueError('请选择带版本号的 OpenUtau USTX 工程。')
    return data


def read_ustx(path):
    """Use the same lyric and YAML rules for export verification and tuning."""
    return read_ustx_bytes(Path(path).read_bytes(), require_version=False)


def voices_for(request):
    return ('vocals',) if request.get('voice_mode') == 'single' else ('lead', 'backing')


def context_requested(request):
    """Inspect optional settings before importing the independent context layer."""
    enabled = request.get('context_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('context_enabled必须为布尔值。')
    plan = request.get('context_plan')
    if plan is not None and (not isinstance(plan, str) or not plan.strip()):
        raise ValueError('context_plan必须为非空计划文件路径。')
    return enabled or plan is not None


def retained_signature(job, request, *, adapter_file=None):
    spec = request['retained_expression']
    job = Path(job).resolve()
    if file_hash(job / 'audio/source.wav') != spec['source_audio_sha256']:
        raise ValueError('当前音乐与原参数的来源不同，无法保留参数。')
    for row in spec['tracks'].values():
        path = (job / row['file']).resolve()
        if not path.is_relative_to(job) or file_hash(path) != row['sha256']:
            raise ValueError('待保留的参数工程缺失或已改变，已停止导出。')
    target_adapter = Path(adapter_file) if adapter_file is not None else Path(__file__).with_name('calibration_workflow.py')
    return {'spec': spec, 'adapter': file_hash(target_adapter)}


def inspect_video(path, ffmpeg=None, *, require_audio=True):
    path = app_path(path)
    if not path.is_file():
        raise ValueError('找不到成品视频，请选择完整文件路径。')
    result = subprocess.run(
        [ffmpeg or load_config()['ffmpeg'], '-hide_banner', '-i', str(path)],
        capture_output=True, encoding='utf-8', errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30,
    )
    text = result.stderr
    duration = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    if not duration or not re.search(r'Stream .*Video:', text) or (require_audio and not re.search(r'Stream .*Audio:', text)):
        raise ValueError(
            '成品需要同时包含视频和音频，且能读取完整时长。' if require_audio
            else '原视频需要包含可读取的画面和时长。'
        )
    hours, minutes, seconds = map(float, duration.groups())
    size = re.search(r'Video:.*?\b(\d{2,5})x(\d{2,5})\b', text)
    return {
        'path': str(path), 'bytes': path.stat().st_size,
        'duration': hours * 3600 + minutes * 60 + seconds,
        'width': int(size[1]) if size else None, 'height': int(size[2]) if size else None,
    }


def _raw(path):
    return Path(native_path(path)).read_bytes()


def _stat(path):
    return Path(native_path(path)).stat()


def project_format(path):
    suffix = Path(path).suffix.casefold()
    if suffix not in TUNING_SUFFIXES:
        raise ValueError('请选择 SVP、USTX、VPR、VSQX、ACEP 或 UFData 工程。')
    return suffix[1:]


def style_adapter(format):
    name = STYLE_ADAPTERS.get(format)
    if not name:
        return None
    module, _, symbol = name.partition(':')
    adapter = import_module(module)
    return getattr(adapter, symbol) if symbol else adapter


def _project(raw, format='svp'):
    if adapter := style_adapter(format):
        return adapter.read(raw)
    value = json.loads(raw.decode('utf-8-sig').rstrip('\x00'))
    if not isinstance(value, dict) or not isinstance(value.get('tracks'), list) or not isinstance(value.get('time'), dict):
        raise ValueError('请选择有效的SVP工程。')
    return value


def read_project(path, format=None):
    format = format or project_format(path)
    return _project(path if format == 'vpr' else _raw(path), format)


def read_source(path):
    """Read one stable project view; for VPR hash the container without loading its audio into RAM."""
    path = Path(path).resolve()
    format = project_format(path)
    size = _stat(path).st_size
    if format == 'vpr':
        limit = style_adapter(format).MAX_FILE_BYTES
        if not 0 < size <= limit:
            raise ValueError('VPR 工程为空或超过 512 MiB。')
        sha = file_hash(path)
        data = read_project(path, format)
        if file_hash(path) != sha or _stat(path).st_size != size:
            raise ValueError('读取期间 VPR 工程发生变化，请重新载入。')
    else:
        if not 0 < size <= 64 * 1024**2:
            raise ValueError('工程为空或超过 64 MiB。')
        raw = _raw(path)
        data = _project(raw, format)
        size = len(raw)
        sha = hashlib.sha256(raw).hexdigest()
    return SourceSnapshot(path, format, sha, size, data)


def score_layout(data, format):
    if adapter := style_adapter(format):
        layout = adapter.Layout(data)
        score = layout.score
    else:
        score, layout = data, Layout(data)
    layout.clock = ProjectClock(
        score['time'], step=getattr(layout, 'step', 1),
        native_origin=getattr(layout, 'native_origin', 0),
        measure_prefix=getattr(layout, 'measure_prefix', 0),
    )
    layout.native = NativeProject(data, format, layout)
    return score, layout


def inspect_project(path, *, snapshot=None, geometry=None):
    snapshot = read_source(path) if snapshot is None else snapshot
    path, format, sha = snapshot.path, snapshot.format, snapshot.sha256
    score, layout = geometry or score_layout(snapshot.data, format)
    tracks = []
    for row, track in zip(layout.tracks, score['tracks']):
        if row.instrumental is None:
            raise ValueError('SVP轨道格式无效。')
        tracks.append({
            'track_index': row.index, 'name': track.get('name', '演唱轨'),
            'note_count': row.note_count, 'instrumental': row.instrumental,
        })
    if not any(not row['instrumental'] for row in tracks):
        raise ValueError('工程中没有演唱轨。')
    from tuning_project_identity import inspect_candidate_origin

    return {
        'path': str(path), 'sha256': sha, 'bytes': snapshot.size, 'tracks': tracks,
        'format': format, 'format_version': layout.version,
        'synthesizer_v_2': getattr(layout, 'sv2', False),
        'candidate_origin': inspect_candidate_origin(path, sha256=sha),
        **({'format_notice': layout.notice} if hasattr(layout, 'notice') else {}),
    }


STYLE_CONTEXT_SCHEMA_VERSION = 1
STYLE_CONTEXT_KIND = "vwa.style-context"


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    return prefix + "_" + _json_digest(value)[:24]


def _preset_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        style_catalog.style_descriptors(style_catalog.VISIBLE_STYLES),
        style_catalog.technique_descriptors(style_catalog.TECHNIQUE_IDS),
    )


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


def context_for_snapshot(snapshot, geometry=None) -> dict[str, Any]:
    source, format, source_sha = snapshot.path, snapshot.format, snapshot.sha256
    project, layout = geometry or score_layout(snapshot.data, format)
    clock = layout.clock
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
        first_reason = ""

        def reason(code: str, message: str) -> None:
            nonlocal first_reason
            if code not in reasons:
                if not reasons:
                    first_reason = message
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

        main_offset = raw_ref.get("blickOffset", 0)
        main_offset = main_offset if type(main_offset) is int else None
        track = layout.tracks[track_index]
        for code, message in track.issues.items():
            reason(code, message)

        parsed_count = 0
        placed_notes = ((placement, note) for placement in track.placements for note in placement.notes)
        for placement, note in placed_notes:
            note_index, raw_note = note.index, note.data
            onset, duration = raw_note["onset"], raw_note["duration"]
            offset, project_onset = placement.offset, note.start
            try:
                start_seconds = float(clock.to_seconds(project_onset))
                end_seconds = float(clock.to_seconds(note.end))
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

            singing = _note_is_singing(raw_note, _lyric(raw_note)[0])
            lyric, lyric_raw, lyric_known = _lyric(
                {"lyrics": raw_note["source_lyric"]} if "source_lyric" in raw_note else raw_note)
            pitch = note.pitch
            pitch_known = pitch is not None
            reference = {"group_placement": placement.key} if format != "svp" or placement.library_index is not None else {}
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
                **reference,
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
            protection = getattr(placement, 'protection', None)
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
                **({"project_end_tick": note.end, **reference} if placement.adapted else {}),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "lyric": lyric,
                "lyric_raw": lyric_raw,
                "lyric_known": lyric_known,
                "singing": singing,
                **({"read_only_reason": protection[1]} if protection else {}),
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
            "offset_tick": main_offset,
            "offset_quarters": None if main_offset is None else main_offset / _SV_QUARTER,
            "main_group_present": isinstance(raw_track.get("mainGroup"), dict),
            "note_count_raw": track.raw_count,
            "note_count_parsed": parsed_count,
            "database": copy.deepcopy(raw_ref.get("database")),
            "voice": copy.deepcopy(raw_ref.get("voice")),
            "eligible_for_style": bool(vocal and parsed_count and not reasons),
            "ineligibility_reasons": reasons,
            "read_only_reason": first_reason,
            "style_only": track.style_only,
        })

    presets, techniques = _preset_catalog()
    return {
        "schema": STYLE_CONTEXT_SCHEMA_VERSION,
        "kind": STYLE_CONTEXT_KIND,
        "source_path": str(source),
        "source_sha256": source_sha,
        "format": format,
        "format_version": layout.version,
        "synthesizer_v_2": getattr(layout, "sv2", False),
        "tempo": [{"position_tick": tick, "bpm": float(bpm), "start_seconds": seconds}
                  for tick, bpm, seconds in zip(clock.positions, clock.bpms, clock.seconds)],
        "meter": [{"index": row.bar, "numerator": row.numerator, "denominator": row.denominator,
                   "position_tick": clock.position_at_bar(row.bar)} for row in clock.meters],
        "time_basis": {
            "tick": ("SVP blick" if format == "svp"
                     else f"shared score blick (1 native tick = {getattr(layout, 'step', 1470000)} blick)"),
            "quarter_ticks": _SV_QUARTER,
            "native_tick_step": clock.step,
            "native_content_origin": clock.native_origin,
            "project_start_tick": clock.project_start,
            "measure_prefix": clock.measure_prefix,
            "note_fields": "onset_tick/duration_tick are group-local; start_seconds/end_seconds are project positions including placement offset and trim",
            "region_fields": "project seconds",
        },
        "tracks": track_rows,
        "notes": note_rows,
        "presets": presets,
        "preset_ids": list(style_catalog.VISIBLE_STYLES),
        "techniques": techniques,
        "technique_ids": list(style_catalog.TECHNIQUE_IDS),
        "unknowns": unknown_rows,
        "semantic_model_used": False,
        "local_only": True,
    }
