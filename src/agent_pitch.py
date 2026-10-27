"""Managed absolute-F0 preservation for LibreSVIP project export.

The module consumes measured schema-1 pitch NPZ files, builds a compact,
JSON-serializable plan, and installs the measured absolute pitch curve after
MIDI/notes have been corrected. It never runs inference or synthesis.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from agent_reference import _read_pitch, _sha256_file


SCHEMA_VERSION = 1
PLAN_KIND = "vwa-agent-pitch-plan"
APPLY_KIND = "vwa-agent-pitch-apply"
VERIFY_KIND = "vwa-agent-pitch-verify"
SAMPLE_COMPARE_KIND = "vwa-agent-pitch-sample-compare"
POLICY = "preserve_absolute"
ALGORITHM = "absolute-f0-v1"
TRACK_INDEX_BASIS = "libresvip_singing_track_ordinal"
PITCH_AXIS_OFFSET_TICKS = 1920
NATIVE_PITCH_SAMPLE_INTERVAL_TICKS = 5
ENDPOINT_GUARD_BEFORE_TICKS = 2
ENDPOINT_GUARD_AFTER_TICKS = 4
MIN_VOICED_RUN_FRAMES = 3
DEFAULT_TOLERANCE_CENTS = 5.0
_MAX_TIME_ORIGIN_SECONDS = 1_000_000_000.0
_MAX_AUDIT_SAMPLES = 32
_MAX_PITCH_VERIFY_FRAMES = 6_000_000


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _plan_digest(plan: Mapping[str, Any]) -> str:
    return _digest({key: value for key, value in plan.items() if key != "plan_sha256"})


def _round(value: float | None, digits: int = 9) -> float | None:
    return None if value is None else round(float(value), digits)


def _strict_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} 必须是大于等于 {minimum} 的整数。")
    return value


def _finite_number(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} 必须是有限数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数值。")
    return number


def _time_origin(value: Any) -> float:
    number = _finite_number(value, "time_origin_seconds")
    if abs(number) > _MAX_TIME_ORIGIN_SECONDS:
        raise ValueError("time_origin_seconds 超出支持范围。")
    return number


def _source_path(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path 必须是非空绝对路径。")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("path 必须是绝对路径。")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"path 无法解析：{error}") from error
    if not resolved.is_file():
        raise ValueError("path 必须指向普通 pitch NPZ 文件。")
    return resolved


def _cancel_checker(cancel: Callable[[], Any] | None) -> Callable[[], None]:
    if cancel is not None and not callable(cancel):
        raise ValueError("cancel 必须可调用或为 None。")

    def check() -> None:
        if cancel is not None and cancel() is True:
            raise InterruptedError("音高计划操作已取消。")

    return check


def _pitch_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    pitch = np.asarray(data["pitch"], dtype=np.float64)
    mask = np.asarray(data["voiced_mask"], dtype=np.bool_)
    step = float(data["time_step_seconds"])
    indices = np.flatnonzero(mask)
    voiced = pitch[mask]
    if indices.size:
        voiced_time_range = [_round(float(indices[0]) * step), _round(float(indices[-1]) * step)]
        voiced_midi_range = [_round(float(np.min(voiced))), _round(float(np.max(voiced)))]
    else:
        voiced_time_range = None
        voiced_midi_range = None
    return {
        "schema_version": int(data["schema_version"]),
        "time_step_seconds": _round(step),
        "frame_count": int(data["frame_count"]),
        "voiced_frame_count": int(data["voiced_frame_count"]),
        "voiced_time_range_seconds": voiced_time_range,
        "voiced_midi_range": voiced_midi_range,
        "pitch_dtype": str(data["pitch_dtype"]),
        "mask_dtype": str(data["mask_dtype"]),
        "file_size_bytes": int(data["file_size_bytes"]),
    }


def build_pitch_plan(sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Read measured F0 files and return a deterministic, compact plan.

    ``vocal_track_index`` is the zero-based ordinal among LibreSVIP
    ``SingingTrack`` objects, never an arbitrary project-track position.
    """
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources 必须是非空列表。")
    rows: list[dict[str, Any]] = []
    seen_tracks: set[int] = set()
    seen_paths: set[str] = set()
    for source_index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise ValueError(f"sources[{source_index}] 必须是对象。")
        unexpected = set(source) - {"vocal_track_index", "path", "time_origin_seconds"}
        if unexpected:
            raise ValueError(f"sources[{source_index}] 含未知字段：" + ",".join(sorted(unexpected)))
        track_index = _strict_int(source.get("vocal_track_index"), f"sources[{source_index}].vocal_track_index")
        if track_index in seen_tracks:
            raise ValueError(f"vocal_track_index 重复：{track_index}。")
        path = _source_path(source.get("path"))
        path_text = str(path)
        path_key = path_text.casefold()
        if path_key in seen_paths:
            raise ValueError("同一 pitch NPZ 不能隐式复用于多个演唱轨。")
        origin = _time_origin(source.get("time_origin_seconds", 0))
        data = _read_pitch(path)
        source_sha = str(data["source_sha256"])
        metadata = _pitch_metadata(data)
        if _sha256_file(path) != source_sha:
            raise ValueError("pitch NPZ 在规划期间发生变化；拒绝生成计划。")
        seen_tracks.add(track_index)
        seen_paths.add(path_key)
        rows.append({
            "vocal_track_index": track_index,
            "track_index_basis": TRACK_INDEX_BASIS,
            "path": path_text,
            "source_sha256": source_sha,
            "time_origin_seconds": _round(origin),
            "source": metadata,
        })
    rows.sort(key=lambda row: row["vocal_track_index"])
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "policy": POLICY,
        "algorithm": {
            "name": ALGORITHM,
            "time_mapping": "midi_seconds = frame_index * time_step_seconds + time_origin_seconds",
            "note_gate": "half-open current note [start,end), project actual tempo seconds",
            "pitch_axis_offset_ticks": PITCH_AXIS_OFFSET_TICKS,
            "minimum_voiced_run_frames": MIN_VOICED_RUN_FRAMES,
            "native_pitch_sample_interval_ticks": NATIVE_PITCH_SAMPLE_INTERVAL_TICKS,
            "endpoint_guard_before_ticks": ENDPOINT_GUARD_BEFORE_TICKS,
            "endpoint_guard_after_ticks": ENDPOINT_GUARD_AFTER_TICKS,
            "curve_value": "absolute MIDI pitch * 100 cents",
            "silence_break_value": -100,
        },
        "sources": rows,
        "totals": {
            "source_count": len(rows),
            "frame_count": sum(row["source"]["frame_count"] for row in rows),
            "voiced_frame_count": sum(row["source"]["voiced_frame_count"] for row in rows),
        },
        "inference_performed": False,
        "synthesis_performed": False,
        "provider_access_performed": False,
    }
    plan["plan_sha256"] = _plan_digest(plan)
    return plan

def _validated_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ValueError("plan 必须是对象。")
    value = copy.deepcopy(dict(plan))
    stored = value.get("plan_sha256")
    if not isinstance(stored, str) or stored != _plan_digest(value):
        raise ValueError("plan_sha256 不匹配；计划已改变或被篡改。")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("kind") != PLAN_KIND:
        raise ValueError("不支持的 pitch plan schema/kind。")
    if value.get("policy") != POLICY:
        raise ValueError("不支持的 pitch plan policy。")
    algorithm = value.get("algorithm")
    if not isinstance(algorithm, dict):
        raise ValueError("pitch plan 缺少算法元数据。")
    required_algorithm = {
        "name": ALGORITHM,
        "pitch_axis_offset_ticks": PITCH_AXIS_OFFSET_TICKS,
        "minimum_voiced_run_frames": MIN_VOICED_RUN_FRAMES,
        "native_pitch_sample_interval_ticks": NATIVE_PITCH_SAMPLE_INTERVAL_TICKS,
        "endpoint_guard_before_ticks": ENDPOINT_GUARD_BEFORE_TICKS,
        "endpoint_guard_after_ticks": ENDPOINT_GUARD_AFTER_TICKS,
        "silence_break_value": -100,
    }
    for key, expected in required_algorithm.items():
        if algorithm.get(key) != expected:
            raise ValueError(f"pitch plan 算法字段 {key} 不受支持。")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("pitch plan sources 必须是非空列表。")
    seen_tracks: set[int] = set()
    seen_paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError("pitch plan source 必须是对象。")
        track_index = _strict_int(row.get("vocal_track_index"), "vocal_track_index")
        if track_index in seen_tracks:
            raise ValueError(f"pitch plan 中 vocal_track_index 重复：{track_index}。")
        if row.get("track_index_basis") != TRACK_INDEX_BASIS:
            raise ValueError("pitch plan 的演唱轨索引基准无效。")
        path = _source_path(row.get("path"))
        path_text = str(path)
        if path_text.casefold() in seen_paths:
            raise ValueError("pitch plan 不得把同一 F0 文件隐式复用于多个轨道。")
        source_sha = row.get("source_sha256")
        if (not isinstance(source_sha, str) or len(source_sha) != 64
                or any(char not in "0123456789abcdef" for char in source_sha)):
            raise ValueError("pitch plan source_sha256 无效。")
        origin = _time_origin(row.get("time_origin_seconds"))
        source_metadata = row.get("source")
        if not isinstance(source_metadata, dict):
            raise ValueError("pitch plan source 元数据缺失。")
        _strict_int(source_metadata.get("frame_count"), "source.frame_count")
        _strict_int(source_metadata.get("voiced_frame_count"), "source.voiced_frame_count")
        if source_metadata["voiced_frame_count"] > source_metadata["frame_count"]:
            raise ValueError("pitch plan 有声帧数不得超过总帧数。")
        seen_tracks.add(track_index)
        seen_paths.add(path_text.casefold())
        normalized.append({**row, "vocal_track_index": track_index, "path": path_text,
                           "time_origin_seconds": _round(origin)})
    normalized.sort(key=lambda row: row["vocal_track_index"])
    if normalized != sources:
        raise ValueError("pitch plan sources 必须按 vocal_track_index 排序。")
    totals = value.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("pitch plan totals 缺失。")
    expected_totals = {
        "source_count": len(normalized),
        "frame_count": sum(int(row["source"]["frame_count"]) for row in normalized),
        "voiced_frame_count": sum(int(row["source"]["voiced_frame_count"]) for row in normalized),
    }
    if totals != expected_totals:
        raise ValueError("pitch plan totals 与 sources 不一致。")
    return value


def _vocal_tracks(project: Any) -> list[Any]:
    tracks = getattr(project, "track_list", None)
    if not isinstance(tracks, list):
        raise ValueError("project.track_list 无效。")
    return [track for track in tracks
            if hasattr(track, "note_list") and hasattr(track, "edited_params")]


def _load_planned_source(row: Mapping[str, Any]) -> dict[str, Any]:
    path = _source_path(row["path"])
    data = _read_pitch(path)
    if data["source_sha256"] != row["source_sha256"]:
        raise ValueError("pitch NPZ 的 SHA-256 与计划不一致。")
    metadata = _pitch_metadata(data)
    expected = row["source"]
    for key in ("schema_version", "time_step_seconds", "frame_count",
                "voiced_frame_count", "pitch_dtype", "mask_dtype", "file_size_bytes"):
        if metadata.get(key) != expected.get(key):
            raise ValueError(f"pitch NPZ 元数据与计划不一致：{key}。")
    if _sha256_file(path) != row["source_sha256"]:
        raise ValueError("pitch NPZ 在应用读取期间发生变化。")
    return data


def _time_sync(tempo_list: Any) -> Any:
    from libresvip.core.time_sync import TimeSynchronizer
    return TimeSynchronizer(tempo_list)


def _gate_notes(times: np.ndarray, notes: Sequence[Any], clock: Any) -> np.ndarray:
    inside = np.zeros(times.size, dtype=np.bool_)
    for note in notes:
        try:
            start_tick = int(note.start_pos)
            end_tick = int(note.end_pos)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("当前工程含无效音符时间。") from error
        if end_tick <= start_tick:
            raise ValueError("当前工程含零长或倒置音符。")
        start = float(clock.get_actual_secs_from_ticks(start_tick))
        end = float(clock.get_actual_secs_from_ticks(end_tick))
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("当前工程音符的播放时间无效。")
        left = int(np.searchsorted(times, start, side="left"))
        right = int(np.searchsorted(times, end, side="left"))
        if right > left:
            inside[left:right] = True
    return inside


def _active_runs(active: np.ndarray) -> list[tuple[int, int]]:
    if not active.size:
        return []
    transitions = np.diff(np.r_[False, active, False].astype(np.int8))
    return [(int(left), int(right))
            for left, right in zip(np.flatnonzero(transitions == 1),
                                   np.flatnonzero(transitions == -1))]

def _note_rows(notes: Sequence[Any], clock: Any, times: np.ndarray,
               voiced_mask: np.ndarray, gated_active: np.ndarray,
               written_mask: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    missing = 0
    for note_index, note in enumerate(notes):
        start_seconds = float(clock.get_actual_secs_from_ticks(int(note.start_pos)))
        end_seconds = float(clock.get_actual_secs_from_ticks(int(note.end_pos)))
        left = int(np.searchsorted(times, start_seconds, side="left"))
        right = int(np.searchsorted(times, end_seconds, side="left"))
        span = max(0, right - left)
        voiced_count = int(np.count_nonzero(voiced_mask[left:right]))
        gated_count = int(np.count_nonzero(gated_active[left:right]))
        written_count = int(np.count_nonzero(written_mask[left:right]))
        note_voiced = voiced_mask[left:right].astype(np.int8)
        transitions = np.diff(np.r_[0, note_voiced, 0])
        voiced_runs = int(np.count_nonzero(transitions == 1))
        gaps = max(0, voiced_runs - 1)
        if span == 0:
            status = "outside_reference_frames"
        elif voiced_count == 0:
            status = "no_voiced_reference"
        elif gated_count == 0:
            status = "no_gated_reference"
        elif written_count == 0:
            status = "too_short_for_curve"
        elif gaps:
            status = "covered_with_voicing_gaps"
        else:
            status = "covered"
        if status not in ("covered", "covered_with_voicing_gaps"):
            missing += 1
        if status != "covered":
            rows.append({
                "note_index": note_index,
                "start_seconds": _round(start_seconds),
                "end_seconds": _round(end_seconds),
                "status": status,
                "frame_span": span,
                "voiced_frames": voiced_count,
                "gated_voiced_frames": gated_count,
                "written_frames": written_count,
                "voicing_gaps": gaps,
            })
    return rows, missing


def _install_track_pitch(track: Any, project: Any, data: Mapping[str, Any],
                         origin_seconds: float,
                         check_cancel: Callable[[], None]) -> tuple[Any, dict[str, Any]]:
    from libresvip.model.base import ParamCurve
    from libresvip.model.point import Point

    pitch = np.asarray(data["pitch"], dtype=np.float64)
    voiced_mask = np.asarray(data["voiced_mask"], dtype=np.bool_)
    step = float(data["time_step_seconds"])
    times = np.arange(pitch.size, dtype=np.float64) * step + origin_seconds
    clock = _time_sync(project.song_tempo_list)
    inside = _gate_notes(times, track.note_list, clock)
    active = voiced_mask & np.isfinite(pitch) & inside
    written_mask = np.zeros(pitch.size, dtype=np.bool_)
    short_runs = 0
    duplicate_frames = 0
    errors: list[float] = []
    curve_values: list[float] = []
    source_curve_points = 0
    runs_data: list[dict[str, Any]] = []
    for run_index, (left, right) in enumerate(_active_runs(active)):
        if run_index % 256 == 0:
            check_cancel()
        if right - left < MIN_VOICED_RUN_FRAMES:
            short_runs += 1
            continue
        current: dict[int, int] = {}
        expected_by_tick: dict[int, list[float]] = {}
        for frame in range(left, right):
            if frame % 4096 == 0:
                check_cancel()
            tick = round(clock.get_actual_ticks_from_secs(float(times[frame]))) + PITCH_AXIS_OFFSET_TICKS
            cents = int(round(float(pitch[frame]) * 100))
            current[tick] = cents
            expected_by_tick.setdefault(tick, []).append(float(pitch[frame]) * 100)
        if not current:
            continue
        ordered = sorted(current.items())
        source_curve_points += len(ordered)
        written_mask[left:right] = True
        for tick, values in expected_by_tick.items():
            actual = float(current[tick])
            errors.extend(abs(actual - value) for value in values)
            curve_values.append(actual)
            duplicate_frames += max(0, len(values) - 1)
        runs_data.append({
            "left": left,
            "right": right,
            "ordered": ordered,
            "first_tick": ordered[0][0],
            "last_tick": ordered[-1][0],
            "first_value": ordered[0][1],
            "last_value": ordered[-1][1],
        })

    points: list[Any] = [Point.start_point()]
    guard_before = 0
    guard_after = 0
    max_guard_seconds = 0.0
    for run_index, run in enumerate(runs_data):
        previous_last = runs_data[run_index - 1]["last_tick"] if run_index else None
        next_first = runs_data[run_index + 1]["first_tick"] if run_index + 1 < len(runs_data) else None
        first_tick = int(run["first_tick"])
        last_tick = int(run["last_tick"])
        start_guard = first_tick - ENDPOINT_GUARD_BEFORE_TICKS
        if previous_last is not None:
            start_guard = max(start_guard, int(previous_last) + 2)
        if start_guard >= first_tick:
            start_guard = first_tick
        end_guard = last_tick + ENDPOINT_GUARD_AFTER_TICKS
        if next_first is not None:
            end_guard = min(end_guard, int(next_first) - 2)
        if end_guard <= last_tick:
            end_guard = last_tick
        points.append(Point(start_guard - 1, -100))
        if start_guard < first_tick:
            points.append(Point(start_guard, int(run["first_value"])))
            guard_before += 1
            max_guard_seconds = max(
                max_guard_seconds,
                abs(float(clock.get_actual_secs_from_ticks(first_tick))
                    - float(clock.get_actual_secs_from_ticks(start_guard))),
            )
        points.extend(Point(tick, value) for tick, value in run["ordered"])
        if end_guard > last_tick:
            points.append(Point(end_guard, int(run["last_value"])))
            guard_after += 1
            max_guard_seconds = max(
                max_guard_seconds,
                abs(float(clock.get_actual_secs_from_ticks(end_guard))
                    - float(clock.get_actual_secs_from_ticks(last_tick))),
            )
        points.append(Point(end_guard + 1, -100))
    points.append(Point.end_point())

    note_samples, missing_notes = _note_rows(
        track.note_list, clock, times, voiced_mask, active, written_mask)
    old_curve = track.edited_params.pitch
    report = {
        "vocal_track_index": None,
        "title": str(getattr(track, "title", "")),
        "notes_total": len(track.note_list),
        "notes_missing_local_reference": missing_notes,
        "local_missing_samples": note_samples[:_MAX_AUDIT_SAMPLES],
        "local_missing_truncated": len(note_samples) > _MAX_AUDIT_SAMPLES,
        "voiced_frames": int(np.count_nonzero(voiced_mask & np.isfinite(pitch))),
        "gated_voiced_frames": int(np.count_nonzero(active)),
        "written_frames": int(np.count_nonzero(written_mask)),
        "curve_points": source_curve_points,
        "object_points": len(points),
        "native_guard_points": guard_before + guard_after,
        "native_guard_before_runs": guard_before,
        "native_guard_after_runs": guard_after,
        "native_guard_extension_seconds_max": _round(max_guard_seconds, 9),
        "native_guard_policy": (
            f"reuse endpoint F0 for at most {ENDPOINT_GUARD_BEFORE_TICKS} ticks before "
            f"and {ENDPOINT_GUARD_AFTER_TICKS} ticks after each voiced run; preserve -100 break"
        ),
        "runs": len(runs_data),
        "short_runs_dropped": short_runs,
        "duplicate_frame_collisions": duplicate_frames,
        "max_error_cents": _round(max(errors) if errors else None, 6),
        "mean_error_cents": _round(float(np.mean(errors)) if errors else None, 6),
        "curve_pitch_span_cents": _round(float(max(curve_values) - min(curve_values))
                                        if curve_values else None, 6),
        "coverage_of_voiced_frames": _round(
            float(np.count_nonzero(written_mask))
            / max(1, int(np.count_nonzero(voiced_mask & np.isfinite(pitch)))), 9),
    }
    return ParamCurve(points=points), {"old_curve": old_curve, "report": report}

def apply_pitch_plan(project: Any, plan: Mapping[str, Any], *,
                     cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Install planned measured F0 without changing notes, lyrics or tempo."""
    check_cancel = _cancel_checker(cancel)
    checked = _validated_plan(plan)
    tracks = _vocal_tracks(project)
    sources = checked["sources"]
    for row in sources:
        index = int(row["vocal_track_index"])
        if index >= len(tracks):
            raise ValueError(f"vocal_track_index {index} 越界；工程仅有 {len(tracks)} 条演唱轨。")
    loaded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in sources:
        check_cancel()
        loaded.append((row, _load_planned_source(row)))
    prepared: list[tuple[Any, Any, dict[str, Any], dict[str, Any]]] = []
    for row, data in loaded:
        check_cancel()
        track = tracks[int(row["vocal_track_index"])]
        new_curve, state = _install_track_pitch(
            track, project, data, float(row["time_origin_seconds"]), check_cancel)
        state["report"]["vocal_track_index"] = int(row["vocal_track_index"])
        prepared.append((track, new_curve, state, row))
    before_hashes: list[str] = []
    for row, _data in loaded:
        check_cancel()
        current = _sha256_file(_source_path(row["path"]))
        if current != row["source_sha256"]:
            raise ValueError("pitch NPZ 在写入工程前发生变化；未修改工程。")
        before_hashes.append(current)
    old_pitches: list[tuple[Any, Any]] = []
    try:
        for track, new_curve, _state, _row in prepared:
            check_cancel()
            old_pitches.append((track, track.edited_params.pitch))
            track.edited_params.pitch = new_curve
        after_hashes: list[str] = []
        for row, _data in loaded:
            check_cancel()
            current = _sha256_file(_source_path(row["path"]))
            if current != row["source_sha256"]:
                raise ValueError("pitch NPZ 在写入工程期间发生变化；已回滚音高曲线。")
            after_hashes.append(current)
    except BaseException:
        for track, old_curve in reversed(old_pitches):
            track.edited_params.pitch = old_curve
        raise
    track_reports = [state["report"] for _track, _curve, state, _row in prepared]
    total_voiced = sum(row["voiced_frames"] for row in track_reports)
    total_written = sum(row["written_frames"] for row in track_reports)
    total_points = sum(row["curve_points"] for row in track_reports)
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": APPLY_KIND,
        "status": "applied" if total_points else "partial_no_curve",
        "policy": POLICY,
        "plan_sha256": checked["plan_sha256"],
        "time_mapping": checked["algorithm"]["time_mapping"],
        "pitch_axis_offset_ticks": PITCH_AXIS_OFFSET_TICKS,
        "tracks": track_reports,
        "totals": {
            "tracks_modified": len(track_reports),
            "voiced_frames": total_voiced,
            "written_frames": total_written,
            "curve_points": total_points,
            "notes_missing_local_reference": sum(
                row["notes_missing_local_reference"] for row in track_reports),
            "coverage_of_voiced_frames": _round(total_written / max(1, total_voiced)),
        },
        "source_verification": [
            {"vocal_track_index": int(row["vocal_track_index"]),
             "sha256_before": before, "sha256_after": after,
             "unchanged": before == after == row["source_sha256"]}
            for (row, _data), before, after in zip(loaded, before_hashes, after_hashes)
        ],
        "project_tempo": [[int(tempo.position), _round(float(tempo.bpm), 9)]
                          for tempo in project.song_tempo_list],
        "inference_performed": False,
        "synthesis_performed": False,
        "provider_access_performed": False,
    }
    return result


def _curve_segments(points: Sequence[Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    xs: list[int] = []
    ys: list[float] = []
    for point in points:
        x = int(point.x)
        y = float(point.y)
        if not math.isfinite(y):
            raise ValueError("LibreSVIP 回读工程含非有限 pitch 点。")
        if y == -100:
            if xs:
                segments.append((np.asarray(xs, dtype=np.int64), np.asarray(ys, dtype=np.float64)))
            xs = []
            ys = []
        elif -1_000_000 < x < 1_000_000_000:
            xs.append(x)
            ys.append(y)
    if xs:
        segments.append((np.asarray(xs, dtype=np.int64), np.asarray(ys, dtype=np.float64)))
    return segments


def _sample_curve(segments: Sequence[tuple[np.ndarray, np.ndarray]], tick: int) -> float | None:
    for xs, ys in segments:
        left = int(np.searchsorted(xs, tick, side="left"))
        if left < xs.size and int(xs[left]) == tick:
            return float(ys[left])
        right = left
        left -= 1
        if left < 0 or right >= xs.size:
            continue
        x1, x2 = float(xs[left]), float(xs[right])
        if x2 <= x1:
            return float(ys[right])
        return float(ys[left] + (ys[right] - ys[left]) * (tick - x1) / (x2 - x1))
    return None

def _error_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"sampled_frames": 0, "max_error_cents": None,
                "mean_error_cents": None, "p95_error_cents": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "sampled_frames": len(values),
        "max_error_cents": _round(float(np.max(array)), 6),
        "mean_error_cents": _round(float(np.mean(array)), 6),
        "p95_error_cents": _round(float(np.percentile(array, 95)), 6),
    }


def _verification_tempo(project: Any) -> dict[str, Any]:
    """Use only the explicit playback tempo restored by the caller.

    No first-bar skip is guessed here. A wrong time axis must fail instead of
    silently selecting whichever prefix happens to look best.
    """
    tempos = list(getattr(project, "song_tempo_list", None) or [])
    if not tempos:
        raise ValueError("回读工程缺少 tempo，无法核验 F0 时轴。")
    return {
        "skip_ticks": 0,
        "tempos": tempos,
        "clock": _time_sync(tempos),
    }

def _evaluate_verify_candidate(
    project: Any,
    sources: Sequence[Mapping[str, Any]],
    loaded: Sequence[dict[str, Any]],
    tracks: Sequence[Any],
    candidate: Mapping[str, Any],
    tolerance_cents: float,
    check_cancel: Callable[[], None],
) -> dict[str, Any]:
    all_errors: list[float] = []
    interior_errors: list[float] = []
    boundary_errors: list[float] = []
    source_results: list[dict[str, Any]] = []
    for row, data in zip(sources, loaded):
        check_cancel()
        track = tracks[int(row["vocal_track_index"])]
        pitch = np.asarray(data["pitch"], dtype=np.float64)
        mask = np.asarray(data["voiced_mask"], dtype=np.bool_)
        origin = float(row["time_origin_seconds"])
        times = np.arange(pitch.size, dtype=np.float64) * float(data["time_step_seconds"]) + origin
        clock = candidate["clock"]
        inside = _gate_notes(times, track.note_list, clock)
        active = mask & np.isfinite(pitch) & inside
        eligible = np.zeros(pitch.size, dtype=np.bool_)
        boundary = np.zeros(pitch.size, dtype=np.bool_)
        short_runs = 0
        runs = 0
        for left, right in _active_runs(active):
            if right - left < MIN_VOICED_RUN_FRAMES:
                short_runs += 1
            else:
                eligible[left:right] = True
                boundary[left] = True
                boundary[right - 1] = True
                runs += 1
        segments = _curve_segments(track.edited_params.pitch.points.root)
        errors: list[float] = []
        local_interior: list[float] = []
        local_boundary: list[float] = []
        missing = 0
        missing_boundary = 0
        indices = np.flatnonzero(eligible)
        for position, frame in enumerate(indices):
            if position % 4096 == 0:
                check_cancel()
            tick = round(clock.get_actual_ticks_from_secs(float(times[frame]))) + PITCH_AXIS_OFFSET_TICKS
            actual = _sample_curve(segments, tick)
            if actual is None:
                missing += 1
                if boundary[frame]:
                    missing_boundary += 1
                continue
            error = abs(actual - float(pitch[frame]) * 100.0)
            errors.append(error)
            all_errors.append(error)
            if boundary[frame]:
                local_boundary.append(error)
                boundary_errors.append(error)
            else:
                local_interior.append(error)
                interior_errors.append(error)
        source_summary = _error_summary(errors)
        source_summary.update({
            "interior": _error_summary(local_interior),
            "boundary": _error_summary(local_boundary),
            "vocal_track_index": int(row["vocal_track_index"]),
            "eligible_frames": int(indices.size),
            "missing_samples": missing,
            "missing_boundary_samples": missing_boundary,
            "within_tolerance": sum(error <= tolerance_cents for error in errors),
            "interior_within_tolerance": sum(
                error <= tolerance_cents for error in local_interior),
            "boundary_within_tolerance": sum(
                error <= tolerance_cents for error in local_boundary),
            "curve_points": sum(xs.size for xs, _ys in segments),
            "curve_runs": len(segments),
            "expected_runs": runs,
            "short_runs_not_required": short_runs,
        })
        source_results.append(source_summary)
    summary = _error_summary(all_errors)
    summary.update({
        "interior": _error_summary(interior_errors),
        "boundary": _error_summary(boundary_errors),
        "eligible_frames": sum(row["eligible_frames"] for row in source_results),
        "missing_samples": sum(row["missing_samples"] for row in source_results),
        "missing_boundary_samples": sum(
            row["missing_boundary_samples"] for row in source_results),
        "within_tolerance": sum(
            row["within_tolerance"] for row in source_results),
        "interior_within_tolerance": sum(
            row["interior_within_tolerance"] for row in source_results),
        "boundary_within_tolerance": sum(
            row["boundary_within_tolerance"] for row in source_results),
        "curve_points": sum(row["curve_points"] for row in source_results),
    })
    return {
        "skip_ticks": 0,
        "tempo": [[int(row.position), _round(float(row.bpm), 9)]
                  for row in candidate["tempos"]],
        "summary": summary,
        "sources": source_results,
    }

def verify_pitch_plan(project: Any, plan: Mapping[str, Any], *,
                      cancel: Callable[[], Any] | None = None,
                      tolerance_cents: float = DEFAULT_TOLERANCE_CENTS) -> dict[str, Any]:
    """Verify measured F0 detail in a reloaded LibreSVIP project.

    The caller must restore ``song_tempo_list`` to the explicit playback tempo
    used by ``apply_pitch_plan``. The pitch parameter axis then uses the fixed
    1920-tick offset. No alternate first-bar skip is guessed: a wrong time axis
    is reported as a failure.
    """
    check_cancel = _cancel_checker(cancel)
    checked = _validated_plan(plan)
    tolerance = _finite_number(tolerance_cents, "tolerance_cents")
    if not 0 < tolerance <= 1200:
        raise ValueError("tolerance_cents 必须大于 0 且不超过 1200。")
    tracks = _vocal_tracks(project)
    sources = checked["sources"]
    for row in sources:
        if int(row["vocal_track_index"]) >= len(tracks):
            raise ValueError("pitch plan vocal_track_index 超出回读工程演唱轨范围。")
    loaded: list[dict[str, Any]] = []
    hashes_before: list[str] = []
    for row in sources:
        check_cancel()
        data = _load_planned_source(row)
        loaded.append(data)
        hashes_before.append(str(data["source_sha256"]))
    tempo = _verification_tempo(project)
    best = _evaluate_verify_candidate(
        project, sources, loaded, tracks, tempo, tolerance, check_cancel)
    eligible = int(best["summary"]["eligible_frames"])
    sampled = int(best["summary"]["sampled_frames"])
    missing = int(best["summary"]["missing_samples"])
    within = int(best["summary"]["within_tolerance"])
    max_error = best["summary"]["max_error_cents"]
    interior = best["summary"]["interior"]
    boundary = best["summary"]["boundary"]
    interior_samples = int(interior["sampled_frames"])
    boundary_samples = int(boundary["sampled_frames"])
    interior_missing = missing - int(best["summary"]["missing_boundary_samples"])
    interior_ok = (
        interior_missing == 0
        and (interior["max_error_cents"] is None
             or interior["max_error_cents"] <= tolerance)
    )
    boundary_ok = (
        int(best["summary"]["missing_boundary_samples"]) == 0
        and (boundary["max_error_cents"] is None
             or boundary["max_error_cents"] <= tolerance)
    )
    curve_points = int(best["summary"]["curve_points"])
    if eligible == 0:
        status, reason, verified = "unverifiable", "no_gated_reference_frames", False
    elif curve_points == 0:
        status, reason, verified = "failed", "continuous_pitch_missing", False
    elif sampled == 0:
        status, reason, verified = "failed", "no_sample_at_expected_times", False
    elif missing:
        status, reason, verified = "failed", "missing_expected_samples", False
    elif not interior_ok:
        status, reason, verified = "failed", "f0_detail_outside_tolerance", False
    elif not boundary_ok:
        status = "partial"
        reason = "boundary_transient_outside_tolerance"
        verified = False
    elif within != eligible or max_error is None or max_error > tolerance:
        status = "failed"
        reason = "f0_detail_outside_tolerance"
        verified = False
    else:
        status, reason, verified = "verified", None, True
    hashes_after: list[str] = []
    for row in sources:
        check_cancel()
        current = _sha256_file(_source_path(row["path"]))
        if current != row["source_sha256"]:
            raise ValueError("pitch NPZ 在核验期间发生变化。")
        hashes_after.append(current)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": VERIFY_KIND,
        "status": status,
        "verified": verified,
        "detail_verified": interior_ok and (interior_samples > 0 or boundary_samples > 0),
        "reason": reason,
        "policy": POLICY,
        "plan_sha256": checked["plan_sha256"],
        "tolerance_cents": _round(tolerance),
        "selected_time_mapping": {
            "tempo_skip_ticks": 0,
            "tempo": best["tempo"],
            "pitch_axis_offset_ticks": PITCH_AXIS_OFFSET_TICKS,
            "source": "explicit_playback_tempo",
        },
        "summary": best["summary"],
        "sources": best["sources"],
        "time_mapping": {
            "automatic_prefix_search": False,
            "explicit_playback_tempo_required": True,
        },
        "source_verification": [
            {"vocal_track_index": int(row["vocal_track_index"]),
             "sha256_before": before, "sha256_after": after,
             "unchanged": before == after == row["source_sha256"]}
            for row, before, after in zip(sources, hashes_before, hashes_after)
        ],
        "inference_performed": False,
        "synthesis_performed": False,
        "provider_access_performed": False,
    }

def restore_native_pitch(path: Any, fmt: str, project: Any, plan: Any, *,
                         cancel: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Encode absolute F0 against the generated SVP's actual native baseline.

    The generic MIDI exporter subtracts a flat note baseline. SynthV adds its
    own transitions, so that delta cannot preserve measured F0 at adjacent
    notes. Use the native note/voice model, on its playback clock, and write
    one-tick deltas only within the measured runs. This operates on a private
    conversion candidate; notes and all other native fields stay unchanged.
    """
    if fmt != "svp":
        return {"applied": False, "reason": "native_correction_not_required"}
    from libresvip.model.base import SongTempo
    from libresvip.plugins.svp.constants import TICK_RATE
    from libresvip.plugins.svp.model import SVProject
    from libresvip.plugins.svp.param_expression import CurveGenerator, PitchGenerator
    from libresvip.utils.music_math import linear_interpolation

    saved = _validated_plan(plan)
    check = _cancel_checker(cancel)
    check()
    native_path = Path(path)
    raw = native_path.read_bytes()
    text = raw.removesuffix(b"\x00").removeprefix("\ufeff".encode()).decode("utf-8")
    document = json.loads(text)
    native = SVProject.model_validate_json(text, context={"path": native_path})
    tracks = _vocal_tracks(project)
    native_tracks = [track for track in native.tracks if not track.main_ref.is_instrumental]
    raw_tracks = [track for track in document["tracks"]
                  if not track.get("mainRef", {}).get("isInstrumental", False)]
    if len(tracks) != len(native_tracks):
        raise ValueError("SVP 原生音高重建的声部数不一致。")
    tempos = [SongTempo(position=round(row.position / TICK_RATE), bpm=row.bpm)
              for row in native.time_sig.tempo]
    if [(t.position, round(t.bpm, 3)) for t in tempos] != [
            (t.position, round(t.bpm, 3)) for t in project.song_tempo_list]:
        raise ValueError("SVP 原生音高重建的播放时钟不一致。")
    clock = _time_sync(tempos)
    rows = []
    total_points = 0
    for source in saved["sources"]:
        check()
        _load_planned_source(source)
        index = source["vocal_track_index"]
        if index >= len(tracks):
            raise ValueError("SVP 原生音高绑定超出演唱轨范围。")
        track, sv_track = tracks[index], native_tracks[index]
        if sv_track.groups:
            raise ValueError("SVP 原生音高重建暂不支持音符组引用。")
        expected = [(n.start_pos, n.length, n.key_number) for n in track.note_list]
        actual = [(round(n.onset / TICK_RATE), round(n.duration / TICK_RATE), n.pitch)
                  for n in sv_track.main_group.notes]
        if actual != expected:
            raise ValueError("SVP 原生音高重建的音符身份不一致。")
        master = sv_track.main_ref.voice.to_attributes()
        for note in sv_track.main_group.notes:
            note.merge_attributes(master)
        base = PitchGenerator(
            _synchronizer=clock, _note_list=sv_track.main_group.notes,
            _pitch_diff=CurveGenerator(_point_list=[], _base_value=0,
                                       _interpolation=linear_interpolation),
            _vibrato_env=CurveGenerator(_point_list=[], _base_value=1000,
                                        _interpolation=linear_interpolation),
            _pitch_controls=None,
        )
        points: dict[int, float] = {}
        runs = _curve_segments(track.edited_params.pitch.points.root)
        count = sum(max(0, int(xs[-1]) - max(int(xs[0]), PITCH_AXIS_OFFSET_TICKS) + 1)
                    for xs, _ in runs)
        if total_points + count > _MAX_PITCH_VERIFY_FRAMES:
            raise ValueError("SVP 原生音高重建超出受管采样上限。")
        for xs, ys in runs:
            start = max(int(xs[0]), PITCH_AXIS_OFFSET_TICKS)
            end = int(xs[-1])
            if end < start:
                continue
            start -= PITCH_AXIS_OFFSET_TICKS
            end -= PITCH_AXIS_OFFSET_TICKS
            if start > 0:
                points.setdefault(start - 1, 0.0)
            targets = np.interp(np.arange(start, end + 1) + PITCH_AXIS_OFFSET_TICKS, xs, ys)
            for tick, target in zip(range(start, end + 1), targets):
                if tick % 4096 == 0:
                    check()
                delta = float(target) - float(base.value_at_ticks(tick))
                if not math.isfinite(delta):
                    raise ValueError("SVP 原生音高重建产生非有限值。")
                points[tick] = round(delta, 6)
            points.setdefault(end + 1, 0.0)
        total_points += len(points)
        # The v2 exporter also derives pitchControls from the same absolute
        # curve. Replace that generated layer to avoid applying F0 twice.
        replaced_controls = len(sv_track.main_group.pitch_controls)
        if replaced_controls:
            raw_tracks[index]["mainGroup"]["pitchControls"] = []
        raw_tracks[index]["mainGroup"]["parameters"]["pitchDelta"] = {
            "mode": "linear",
            "points": [value for tick, delta in sorted(points.items())
                       for value in (tick * TICK_RATE, delta)],
        }
        rows.append({"vocal_track_index": index, "points": len(points), "runs": len(runs),
                     "replaced_generated_pitch_controls": replaced_controls})
    for source in saved["sources"]:
        if _sha256_file(Path(source["path"])) != source["source_sha256"]:
            raise ValueError("pitch NPZ 在原生音高重建期间发生变化。")
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    encoded = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + encoded
    if raw.endswith(b"\x00"):
        encoded += b"\x00"
    check()
    native_path.write_bytes(encoded)
    return {"applied": True, "algorithm": "native-synthv-baseline-v1",
            "clock": "native_playback", "sample_interval_ticks": 1,
            "tracks": rows, "total_points": total_points}


def load_pitch_project(path: Any, fmt: str, converters: Mapping[str, Any]) -> Any:
    """Load a verification project without SVP's five-tick pitch downsampling.

    SVP is read through a local ``SynthVParser`` subclass that forces the pitch
    curve interval to one tick. USTX and VSQX use their normal converter read
    path. No global monkeypatch or runtime-file change is performed.
    """
    if not isinstance(fmt, str) or fmt.lower() not in {"svp", "ustx", "vsqx"}:
        raise ValueError("fmt 必须是 svp、ustx 或 vsqx。")
    if not isinstance(converters, Mapping):
        raise ValueError("converters 必须是格式转换器映射。")
    format_name = fmt.lower()
    converter = converters.get(format_name)
    if converter is None:
        raise ValueError(f"converters 缺少 {format_name} 转换器。")
    try:
        source = Path(path).resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"回读工程路径无效：{error}") from error
    if not source.is_file():
        raise ValueError("回读工程路径必须指向普通文件。")
    options = {"import_pitch": True, "import_instrumental_track": False}
    if format_name != "svp":
        return converter.load(source, options)

    from libresvip.plugins.svp.model import SVProject
    from libresvip.plugins.svp.synthv_parser import SynthVParser

    class FinePitchParser(SynthVParser):
        def parse_pitch_curve(
            self,
            pitch_diff: Any,
            vibrato_env: Any,
            sv_notes: Any,
            step: int = 5,
            master_pitch_diff: Any = None,
            master_vibrato_env: Any = None,
            pitch_controls: Any = None,
        ) -> Any:
            # The plugin prefixes its project tempo list for internal storage;
            # native note onsets and the SynthV baseline use playback ticks.
            previous_clock = self.synchronizer
            self.synchronizer = native_clock
            try:
                curve = super().parse_pitch_curve(
                    pitch_diff, vibrato_env, sv_notes, 1,
                    master_pitch_diff, master_vibrato_env, pitch_controls,
                )
            finally:
                self.synchronizer = previous_clock
            # ParamCurve pitch has a fixed 1920-tick prefix, even in 3/4.
            shift = PITCH_AXIS_OFFSET_TICKS - self.first_bar_tick
            if shift:
                from libresvip.model.point import Point
                curve.points.root = [Point(p.x + shift, p.y)
                                     if -1_000_000 < p.x < 1_000_000_000 else p
                                     for p in curve.points.root]
            return curve

    options_object = converter.input_option_cls(**options)
    try:
        raw = source.read_bytes()
        text = raw.removesuffix(b"\x00").removeprefix("\ufeff".encode()).decode("utf-8")
        sv_project = SVProject.model_validate_json(text, context={"path": source})
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"无法读取 SVP 原生工程：{error}") from error
    if options_object.instant and sv_project.instant_mode_enabled is not None:
        options_object.instant = sv_project.instant_mode_enabled
    from libresvip.model.base import SongTempo
    from libresvip.plugins.svp.constants import TICK_RATE
    native_clock = _time_sync([SongTempo(position=round(t.position / TICK_RATE), bpm=t.bpm)
                               for t in sv_project.time_sig.tempo])
    return FinePitchParser(options_object).parse_project(sv_project)

def compare_pitch_samples(project: Any, samples: Sequence[Mapping[str, Any]], *,
                          vocal_track_index: int = 0,
                          tolerance_cents: float = DEFAULT_TOLERANCE_CENTS) -> dict[str, Any]:
    """Raw sample comparison helper for diagnosing format roundtrip drift.

    Each sample is ``{"seconds": finite_seconds, "midi_pitch": midi_value}``.
    The caller can inspect drift without changing the verifier threshold.
    """
    tolerance = _finite_number(tolerance_cents, "tolerance_cents")
    if not 0 < tolerance <= 1200:
        raise ValueError("tolerance_cents 必须大于 0 且不超过 1200。")
    index = _strict_int(vocal_track_index, "vocal_track_index")
    tracks = _vocal_tracks(project)
    if index >= len(tracks):
        raise ValueError("vocal_track_index 超出演唱轨范围。")
    if not isinstance(samples, list) or not samples:
        raise ValueError("samples 必须是非空列表。")
    if len(samples) > _MAX_PITCH_VERIFY_FRAMES:
        raise ValueError("samples 数量超出支持上限。")
    segments = _curve_segments(tracks[index].edited_params.pitch.points.root)
    clock = _time_sync(project.song_tempo_list)
    errors: list[float] = []
    misses: list[dict[str, Any]] = []
    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise ValueError("samples 项必须是对象。")
        seconds = _finite_number(sample.get("seconds"), "samples.seconds")
        expected = _finite_number(sample.get("midi_pitch"), "samples.midi_pitch")
        tick = round(clock.get_actual_ticks_from_secs(seconds)) + PITCH_AXIS_OFFSET_TICKS
        actual = _sample_curve(segments, tick)
        if actual is None:
            if len(misses) < _MAX_AUDIT_SAMPLES:
                misses.append({"sample_index": sample_index, "seconds": _round(seconds),
                               "expected_cents": _round(expected * 100)})
            continue
        error = abs(actual - expected * 100.0)
        errors.append(error)
        if error > tolerance and len(misses) < _MAX_AUDIT_SAMPLES:
            misses.append({"sample_index": sample_index, "seconds": _round(seconds),
                           "expected_cents": _round(expected * 100),
                           "actual_cents": _round(actual), "error_cents": _round(error)})
    summary = _error_summary(errors)
    summary.update({
        "samples": len(samples),
        "within_tolerance": sum(error <= tolerance for error in errors),
        "missing_or_outside": len(samples) - len(errors),
        "outside_tolerance": sum(error > tolerance for error in errors),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": SAMPLE_COMPARE_KIND,
        "status": "match" if len(errors) == len(samples)
        and summary["within_tolerance"] == len(samples) else "mismatch",
        "vocal_track_index": index,
        "tolerance_cents": _round(tolerance),
        "pitch_axis_offset_ticks": PITCH_AXIS_OFFSET_TICKS,
        "summary": summary,
        "misses": misses,
        "misses_truncated": len(misses) == _MAX_AUDIT_SAMPLES,
        "curve_runs": len(segments),
        "inference_performed": False,
        "synthesis_performed": False,
    }
