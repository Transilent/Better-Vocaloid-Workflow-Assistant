"""Live meter data bridge and QPainter views for mixdown output blocks.

The module deliberately has no audio-engine dependency.  Backends publish
actual current-block meters through :meth:`MeterEnvelope.set_live_snapshot`;
sparse offline audition reports remain supported for export consumers.  The
views paint the current table, gain-reduction history, and a measured reverb
energy-decay curve only when an actual impulse measurement is available.

Playback positions passed to :class:`MeterEnvelope` are milliseconds relative to
the start of the audition report.  For an embedded player whose current media
position starts at zero for the audition clip, pass that position directly.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
import copy
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from PyQt5.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from i18n import tr as _tr


METER_REFRESH_HZ = 30.0
METER_REFRESH_MS = 33
PEAK_FLOOR_DBFS = -60.0
PEAK_ENVELOPE_FLOOR_DBFS = -96.0
PEAK_DECAY_DB_PER_SECOND = 24.0
GR_FLOOR_DB = -6.0
GR_AXIS_TICKS_DB = (0.0, -1.0, -2.0, -3.0, -4.0, -5.0, -6.0)
GR_ENVELOPE_FLOOR_DB = -60.0
GR_RELEASE_DB_PER_SECOND = 18.0
GR_HISTORY_SECONDS = 30.0
EDC_FLOOR_DB = -60.0
EDC_DEFAULT_SECONDS = 2.0
DEFAULT_ENVELOPE_INTERVAL_MS = 1000.0 / METER_REFRESH_HZ
PEAK_HOLD_MS = 1500
PEAK_HOLD_FALL_DB_PER_SECOND = 12.0
LIVE_POSITION_DISCONTINUITY_MS = 2000.0

_ENVELOPE_KEYS = ("points", "curve", "envelope", "values_dbfs", "values_db", "values", "samples", "db", "edc_db", "data")
_VARIANT_CONTAINER_KEYS = ("a", "before", "dry", "original", "b", "after", "wet", "processed", "post", "current")
_BUS_KEYS = {"bus", "mix", "master", "mixbus", "mix_bus", "master_bus"}
_BACKING_KEYS = {"backing", "instrumental", "music", "inst", "backing_track", "instrumental_track"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _container_interval_ms(container: Mapping[str, Any] | None) -> float | None:
    if not isinstance(container, Mapping):
        return None
    for key in ("sample_interval_ms", "envelope_interval_ms", "interval_ms",
                "meter_interval_ms", "step_ms", "period_ms", "dt_ms"):
        value = _finite(container.get(key))
        if value is not None and value > 0.0:
            return value
    rate = _finite(container.get("hz") or container.get("sample_rate_hz") or container.get("envelope_hz")
                   or container.get("meter_hz"))
    if rate is not None and rate > 0.0:
        return 1000.0 / rate
    return None



def lane_id(role: str | tuple[str, int] | list[Any] | Mapping[str, Any] | None = None,
            index: int | None = None, *, label: str | None = None) -> str | None:
    """Return the canonical ``vocal:0`` / ``backing:0`` / ``bus`` lane id."""
    del label
    if isinstance(role, Mapping):
        obj = role
        return lane_id(obj.get("role") or obj.get("kind") or obj.get("type")
                       or obj.get("id") or obj.get("key") or obj.get("name"),
                       obj.get("index"))
    if isinstance(role, (tuple, list)):
        values = list(role)
        if len(values) >= 2:
            return lane_id(values[0], values[1])
        role = values[0] if values else None
    if role is None:
        return None
    if isinstance(role, (int, float)) and index is None:
        role, index = "vocal", int(role)
    text = str(role).strip().lower().replace("-", "_").replace(" ", "_")
    compact = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if compact in _BUS_KEYS:
        return "bus"
    if compact in _BACKING_KEYS or compact.startswith("backing_") or compact.startswith("instrumental_"):
        return "backing:0"
    if compact.isdigit():
        return "vocal:%d" % max(0, int(compact))
    match = re.fullmatch(r"(?:vocal|voice|v|track|t)_?(\d+)", compact)
    if match:
        return "vocal:%d" % max(0, int(match.group(1)))
    numeric = _finite(index)
    if numeric is None:
        match = re.search(r"(\d+)$", compact)
        if match:
            numeric = float(match.group(1))
    if compact in {"vocal", "voice", "v", "track", "t"} or compact.startswith("vocal_"):
        return "vocal:%d" % max(0, int(numeric or 0))
    return None


def _split_lane(value: str) -> tuple[str, int] | None:
    text = str(value).strip().lower()
    if text == "bus":
        return "bus", 0
    if text == "backing:0":
        return "backing", 0
    match = re.fullmatch(r"([a-z_]+):(\d+)", text)
    if not match:
        normal = lane_id(text)
        if normal is None:
            return None
        return _split_lane(normal)
    return match.group(1), int(match.group(2))


def _display_lane_label(value: Any) -> str:
    text = str(value)
    match = re.fullmatch(r"歌声\s*(\d+)", text)
    if match:
        return _tr("歌声 %d") % int(match.group(1))
    return _tr(text)


def _lane_label(role: str, index: int) -> str:
    if role == "bus":
        return "MIX BUS"
    if role == "backing":
        return _display_lane_label("伴奏")
    if role == "vocal":
        return _display_lane_label("歌声 %d" % (index + 1))
    return "%s %d" % (role, index + 1)


def _lane_sort_key(value: str) -> tuple[int, int, str]:
    parsed = _split_lane(value)
    if parsed is None:
        return 9, 0, value
    role, index = parsed
    order = {"vocal": 0, "backing": 1, "bus": 2}.get(role, 8)
    return order, index, value


def _series_text_values(value: Any) -> tuple[list[float], list[float] | None, float | None, bool]:
    """Normalise one envelope descriptor into values plus optional times.

    The result is ``(values, times_ms, interval_ms, linear)``.  Values are kept
    in source units until :func:`_make_series` applies the optional dB
    conversion.
    """
    interval = None
    linear = False
    times: list[float] | None = None
    raw_values: Any = value
    if isinstance(value, Mapping):
        descriptor = value
        interval = _container_interval_ms(descriptor)
        linear = bool(descriptor.get("linear") or descriptor.get("amplitude")
                      or str(descriptor.get("scale", "")).lower() in {"linear", "amplitude"})
        unit = str(descriptor.get("time_unit") or descriptor.get("time_units") or "ms").lower()
        if unit in {"s", "sec", "second", "seconds"}:
            time_factor = 1000.0
        else:
            time_factor = 1.0
        for name in ("time_s", "t_s", "times_seconds", "time_seconds", "seconds"):
            if name in descriptor and isinstance(descriptor[name], Sequence):
                times = [_finite(v) for v in descriptor[name]]
                time_factor = 1000.0
                break
        if times is None:
            for name in ("times_ms", "time_ms", "t_ms", "times", "time"):
                if name in descriptor and isinstance(descriptor[name], Sequence):
                    times = [_finite(v) for v in descriptor[name]]
                    break
        if "points" in descriptor and isinstance(descriptor["points"], Sequence):
            raw_values = descriptor["points"]
        elif "curve" in descriptor and isinstance(descriptor["curve"], Sequence):
            raw_values = descriptor["curve"]
        else:
            for name in ("values_dbfs", "values_db", "values", "samples", "db", "edc_db", "data", "envelope"):
                if name in descriptor and isinstance(descriptor[name], Sequence):
                    raw_values = descriptor[name]
                    break
        if times is None and raw_values is value:
            return [], None, interval, linear
    elif not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return [], None, interval, linear

    if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes, bytearray)):
        return [], None, interval, linear
    items = list(raw_values)
    if items and all(isinstance(item, Mapping) for item in items):
        point_times: list[float] = []
        point_values: list[float] = []
        for item in items:
            point_time = None
            for name in ("time_ms", "t_ms", "ms", "time", "t", "second", "seconds"):
                if name in item:
                    point_time = _finite(item[name])
                    if name in {"second", "seconds"} and point_time is not None:
                        point_time *= 1000.0
                    break
            point_value = None
            for name in ("db", "value", "values_dbfs", "values_db", "edc_db", "level", "peak_dbfs", "gr_db"):
                if name in item:
                    point_value = _finite(item[name])
                    break
            if point_time is not None and point_value is not None:
                point_times.append(point_time)
                point_values.append(point_value)
        if point_values:
            return point_values, point_times, interval, linear
    pairs = bool(items) and all(
        isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray))
        and len(item) == 2 and _finite(item[0]) is not None and _finite(item[1]) is not None
        for item in items
    )
    if pairs:
        return ([float(item[1]) for item in items],
                [_finite(item[0]) * (time_factor if isinstance(value, Mapping) else 1.0)
                 for item in items],
                interval, linear)
    if times is not None:
        paired_times: list[float] = []
        paired_values: list[float] = []
        for timestamp, item in zip(times, items):
            timestamp_value = _finite(timestamp)
            value = _finite(item)
            if timestamp_value is not None and value is not None:
                paired_times.append(timestamp_value * time_factor)
                paired_values.append(value)
        return paired_values, paired_times, interval, linear
    values = [_finite(item) for item in items]
    return [item for item in values if item is not None], None, interval, linear

@dataclass(frozen=True)
class _Series:
    times_ms: tuple[float, ...]
    values: tuple[float, ...]
    interval_ms: float
    floor_db: float
    decay_db_per_second: float
    toward_zero: bool = False

    def value_at(self, position_ms: float) -> float | None:
        if not self.times_ms:
            return None
        position = max(0.0, float(position_ms))
        if position < self.times_ms[0]:
            return None
        if len(self.times_ms) == 1:
            return self._decayed(self.values[0], position - self.times_ms[0])
        index = bisect_left(self.times_ms, position)
        if index < len(self.times_ms) and abs(self.times_ms[index] - position) <= 1e-9:
            return self.values[index]
        if index >= len(self.times_ms):
            return self._decayed(self.values[-1], position - self.times_ms[-1])
        left = max(0, index - 1)
        right = index
        left_time = self.times_ms[left]
        right_time = self.times_ms[right]
        gap = max(0.0, right_time - left_time)
        nominal = self.interval_ms if self.interval_ms > 0.0 else DEFAULT_ENVELOPE_INTERVAL_MS
        # A long hole is a dropped/lost envelope interval, not a value to hold.
        if gap > max(250.0, nominal * 3.0):
            return self._decayed(self.values[left], position - left_time)
        span = max(1e-9, right_time - left_time)
        fraction = _clamp((position - left_time) / span, 0.0, 1.0)
        return self.values[left] + (self.values[right] - self.values[left]) * fraction

    def _decayed(self, value: float, elapsed_ms: float) -> float:
        seconds = max(0.0, elapsed_ms) / 1000.0
        if self.toward_zero:
            return _clamp(value + self.decay_db_per_second * seconds, self.floor_db, 0.0)
        return max(self.floor_db, value - self.decay_db_per_second * seconds)


def _make_series(value: Any, *, default_interval_ms: float,
                 floor_db: float, decay_db_per_second: float,
                 toward_zero: bool = False) -> _Series | None:
    values, times, interval, linear = _series_text_values(value)
    if not values:
        return None
    if linear:
        values = [20.0 * math.log10(max(item, 1e-8)) if item > 0.0 else floor_db
                  for item in values]
    step = _finite(interval) or _finite(default_interval_ms) or DEFAULT_ENVELOPE_INTERVAL_MS
    if times is None or len(times) != len(values):
        start = 0.0
        if isinstance(value, Mapping):
            start = _finite(value.get("start_ms") or value.get("start_time_ms")) or 0.0
        times = [start + index * step for index in range(len(values))]
    merged: dict[float, float] = {}
    for timestamp, number in zip(times, values):
        timestamp_value = _finite(timestamp)
        number_value = _finite(number)
        if timestamp_value is not None and number_value is not None:
            merged[max(0.0, timestamp_value)] = number_value
    if not merged:
        return None
    ordered = sorted(merged.items())
    times_sorted = tuple(item[0] for item in ordered)
    values_sorted = tuple(item[1] for item in ordered)
    differences = [b - a for a, b in zip(times_sorted, times_sorted[1:]) if b > a]
    effective_interval = median_interval(differences) if differences else step
    return _Series(times_sorted, values_sorted, effective_interval, floor_db,
                   decay_db_per_second, toward_zero)


def median_interval(values: Iterable[float]) -> float:
    ordered = sorted(float(item) for item in values if item is not None and float(item) > 0.0)
    if not ordered:
        return DEFAULT_ENVELOPE_INTERVAL_MS
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def _report_containers(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    containers: list[Mapping[str, Any]] = []
    seen: set[int] = set()

    def add(value: Any) -> None:
        if isinstance(value, Mapping) and id(value) not in seen:
            seen.add(id(value))
            containers.append(value)

    add(report)
    for key in _VARIANT_CONTAINER_KEYS:
        candidate = report.get(key)
        add(candidate)
        if isinstance(candidate, Mapping):
            add(candidate.get("meters"))
            add(candidate.get("meter"))
    for key in ("meters", "meter"):
        meter_container = report.get(key)
        add(meter_container)
        if isinstance(meter_container, Mapping):
            for bus_name in ("bus", "master", "mix"):
                add(meter_container.get(bus_name))
    for key in ("bus", "master", "mix_bus"):
        add(report.get(key))
    return containers


def _meter_report_roots(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return direct render reports in deterministic A/B merge order."""
    variants: list[Mapping[str, Any]] = []
    for key in _VARIANT_CONTAINER_KEYS:
        candidate = report.get(key)
        if isinstance(candidate, Mapping):
            variants.append(candidate)
    direct = any(name in report for name in (
        "peak_envelope", "peak_envelopes", "gr_envelope", "gr_envelopes",
        "tracks", "meters", "meter", "bus",
    ))
    if direct or not variants:
        return [report, *variants]
    return variants


def _report_duration_ms(report: Mapping[str, Any]) -> float | None:
    durations: list[float] = []
    for container in _report_containers(report):
        value_ms = None
        for name in ("duration_ms", "preview_duration_ms", "audition_duration_ms"):
            value_ms = _finite(container.get(name))
            if value_ms is not None:
                break
        if value_ms is None:
            for name in ("duration_seconds", "preview_duration_seconds",
                         "audition_duration_seconds", "window_duration_seconds"):
                seconds = _finite(container.get(name))
                if seconds is not None:
                    value_ms = seconds * 1000.0
                    break
        if value_ms is not None and value_ms > 0.0:
            durations.append(value_ms)
    return max(durations) if durations else None


def _report_scope_values(report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the report identity that invalidates playback-relative history."""
    values: dict[str, Any] = {}
    for container in _report_containers(report):
        output = container.get("output_path")
        if output is not None and str(output).strip():
            values["output_path"] = str(output)
        for name in ("preview_start_seconds", "audition_start_seconds"):
            number = _finite(container.get(name))
            if number is not None:
                values["preview_start_seconds"] = number
                break
        for name in ("preview_duration_seconds", "audition_duration_seconds"):
            number = _finite(container.get(name))
            if number is not None:
                values["preview_duration_seconds"] = number
                break
        duration = _finite(container.get("duration_seconds"))
        if duration is not None:
            values["duration_seconds"] = duration
        if "bypass_processing" in container:
            values["bypass_processing"] = bool(container.get("bypass_processing"))
    return values


def _report_scope_conflicts(previous: Mapping[str, Any],
                            incoming: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return (scope_changed, segment_changed) for a newly published report."""
    changed = False
    segment = False
    old_path = previous.get("output_path")
    new_path = incoming.get("output_path")
    if old_path is not None and new_path is not None and old_path != new_path:
        changed = True
    if ("bypass_processing" in previous and "bypass_processing" in incoming
            and bool(previous["bypass_processing"]) != bool(incoming["bypass_processing"])):
        changed = True
    for key in ("preview_start_seconds", "preview_duration_seconds"):
        old = _finite(previous.get(key))
        new = _finite(incoming.get(key))
        if old is not None and new is not None and abs(old - new) > 0.001:
            changed = True
            segment = True
    return changed, segment


def _normalise_position_ms(position_ms: Any, duration_ms: float | None) -> float:
    value = _finite(position_ms)
    position = 0.0 if value is None else float(value)
    duration = _finite(duration_ms)
    if duration is not None and duration > 0.0:
        position = math.fmod(position, duration)
        if position < 0.0:
            position += duration
        if abs(position - duration) <= 1e-9:
            position = 0.0
        return position
    return max(0.0, position)


def _position_ratio_ms(position_ms: float, duration_ms: float | None) -> float:
    duration = _finite(duration_ms)
    if duration is None or duration <= 0.0:
        return 0.0
    return _clamp(float(position_ms) / duration, 0.0, 1.0)


def _report_track_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for container in _report_containers(report):
        rows = container.get("tracks")
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            mappings = [row for row in rows if isinstance(row, Mapping)]
            if mappings:
                return mappings
    return []


def _row_lane(row: Mapping[str, Any], fallback_index: int = 0) -> str | None:
    lane = lane_id(row)
    if lane is not None:
        return lane
    role = row.get("role") or row.get("kind") or row.get("type")
    if role is not None:
        return lane_id(role, row.get("index", fallback_index))
    return lane_id("vocal", fallback_index)


def _safe_deepcopy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    number = _finite(value)
    if number is not None:
        return bool(number)
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    return None


def _live_parameter_revision(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return _safe_deepcopy(value)
    number = _finite(value)
    if number is not None and number.is_integer():
        return int(number)
    return _safe_deepcopy(value)


def _live_history_epoch(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    number = _finite(value)
    if number is None or not number.is_integer():
        return None
    return max(0, int(number))


def _live_track_row(row: Mapping[str, Any], fallback_index: int) -> dict[str, Any] | None:
    lane = _row_lane(row, fallback_index)
    if lane is None or lane == "bus":
        return None
    role, index = _split_lane(lane) or ("vocal", fallback_index)
    peak = _finite(row.get("peak_dbfs"))
    name = row.get("label") or row.get("name") or row.get("display_name")
    return {
        "id": lane,
        "role": role,
        "index": index,
        "label": _display_lane_label(
            str(name).strip() if name is not None and str(name).strip()
            else _lane_label(role, index)),
        "peak_dbfs": peak,
        "rms_dbfs": _finite(row.get("rms_dbfs")),
        "gr_db": _finite(row.get("gr_db")),
        "audible": _optional_bool(row.get("audible")),
        "clip": peak is not None and peak >= -1.0,
        "present": True,
    }


def _live_bus_row(value: Any) -> dict[str, Any]:
    present = isinstance(value, Mapping)
    row = value if present else {}
    peak = _finite(row.get("peak_dbfs"))
    limiter_gr = _finite(row.get("limiter_gr_db"))
    gr = limiter_gr if limiter_gr is not None else _finite(row.get("gr_db"))
    if limiter_gr is None:
        limiter_gr = gr
    name = row.get("label") or row.get("name") or row.get("display_name")
    return {
        "id": "bus",
        "role": "bus",
        "index": 0,
        "label": _display_lane_label(
            str(name).strip() if name is not None and str(name).strip()
            else "MIX BUS"),
        "peak_dbfs": peak,
        "rms_dbfs": _finite(row.get("rms_dbfs")),
        "gr_db": gr,
        "limiter_gr_db": limiter_gr,
        "audible": _optional_bool(row.get("audible")),
        "clip": peak is not None and peak >= -1.0,
        "present": present,
    }


def _lane_gr_db_value(item: Mapping[str, Any]) -> float | None:
    value = _finite(item.get("gr_db"))
    if value is None and (item.get("id") == "bus" or item.get("role") == "bus"):
        value = _finite(item.get("limiter_gr_db"))
    return value


def _find_envelope(report: Mapping[str, Any], kind: str) -> tuple[Any, Mapping[str, Any] | None]:
    names = (kind + "_envelope", kind + "_envelopes")
    for container in _report_containers(report):
        for name in names:
            if name in container:
                value = container.get(name)
                if value is not None:
                    return value, value if isinstance(value, Mapping) else container
        envelopes = container.get("envelopes")
        if isinstance(envelopes, Mapping):
            for name in (kind, kind + "_db"):
                if name in envelopes:
                    return envelopes[name], container
    return None, None


def _payload_for_kind(payload: Any, kind: str) -> Any:
    if isinstance(payload, Mapping):
        for name in (kind + "_envelope", kind + "_envelopes", kind, "envelope", "curve"):
            if name in payload and payload.get(name) is not None:
                return payload[name]
    return payload


def _with_row_timing(payload: Any, row: Mapping[str, Any]) -> Any:
    timing = {}
    for name in ("hz", "interval_ms", "sample_interval_ms", "step_ms", "period_ms"):
        if row.get(name) is not None:
            timing[name] = row.get(name)
    if not timing:
        return payload
    if isinstance(payload, Mapping):
        merged = dict(payload)
        for name, value in timing.items():
            merged.setdefault(name, value)
        return merged
    descriptor = {"db": payload}
    descriptor.update(timing)
    return descriptor


def _parse_envelope_collection(value: Any, *, kind: str,
                               rows: Sequence[Mapping[str, Any]],
                               default_interval_ms: float,
                               labels: dict[str, str]) -> dict[str, _Series]:
    result: dict[str, _Series] = {}

    def put(lane: str | None, payload: Any) -> None:
        if lane is None:
            return
        series = _make_series(
            _payload_for_kind(payload, kind),
            default_interval_ms=default_interval_ms,
            floor_db=(PEAK_ENVELOPE_FLOOR_DBFS if kind == "peak" else GR_ENVELOPE_FLOOR_DB),
            decay_db_per_second=(PEAK_DECAY_DB_PER_SECOND if kind == "peak"
                                 else GR_RELEASE_DB_PER_SECOND),
            toward_zero=(kind == "gr"),
        )
        if series is None:
            return
        if kind == "gr":
            series = _Series(series.times_ms,
                             tuple(-abs(item) for item in series.values),
                             series.interval_ms, series.floor_db,
                             series.decay_db_per_second, True)
        result[lane] = series
        if isinstance(payload, Mapping):
            name = payload.get("label") or payload.get("name") or payload.get("display_name")
            if name:
                labels[lane] = str(name)

    def row_at(index: int) -> str | None:
        return _row_lane(rows[index], index) if 0 <= index < len(rows) else None

    def parse_track_sequence(items: Sequence[Any]) -> None:
        for index, item in enumerate(items):
            if isinstance(item, Mapping):
                lane = _row_lane(item, index) or row_at(index)
                put(lane, item)
            else:
                put(row_at(index) or ("vocal:%d" % index), item)

    if value is None:
        return result
    if isinstance(value, Mapping):
        # A descriptor carrying values or points is one lane, not a mapping of
        # independently named lanes.
        has_curve = any(name in value for name in _ENVELOPE_KEYS)
        if has_curve and not any(name in value for name in ("tracks", "bus")):
            put(_row_lane(value) or lane_id("bus"), value)
            return result
        if "tracks" in value:
            tracks = value.get("tracks")
            if isinstance(tracks, Mapping):
                for key, payload in tracks.items():
                    put(lane_id(key), payload)
            elif isinstance(tracks, Sequence) and not isinstance(tracks, (str, bytes, bytearray)):
                parse_track_sequence(tracks)
        if "bus" in value:
            put("bus", value.get("bus"))
        for key, payload in value.items():
            if key in {"tracks", "bus", "sample_interval_ms", "envelope_interval_ms",
                       "interval_ms", "meter_interval_ms", "step_ms", "period_ms",
                       "dt_ms", "hz", "sample_rate_hz", "envelope_hz", "meter_hz",
                       "envelope_time_unit", "time_unit"}:
                continue
            lane = lane_id(key)
            if lane is not None:
                put(lane, payload)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        mapping_rows = [item for item in items if isinstance(item, Mapping)]
        if mapping_rows and any(_row_lane(item, index) is not None
                                for index, item in enumerate(items) if isinstance(item, Mapping)):
            parse_track_sequence(items)
            return result
        pairs = bool(items) and all(
            isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray))
            and len(item) == 2 and _finite(item[0]) is not None and _finite(item[1]) is not None
            for item in items
        )
        numeric = [item for item in items if _finite(item) is not None]
        if (len(numeric) == len(items) or pairs):
            put("bus", items)
            return result
        if len(rows) > 0:
            parse_track_sequence(items)
            return result
        put("bus", items)
    return result


def _normalise_current_gr(value: Any) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    return _clamp(-abs(number), GR_ENVELOPE_FLOOR_DB, 0.0)


def _current_lane_value(row: Mapping[str, Any], kind: str) -> float | None:
    if kind == "peak":
        for name in ("peak_dbfs", "peak_after_effects_dbfs", "meter_peak_dbfs", "peak"):
            value = _finite(row.get(name))
            if value is not None:
                return value
        return None
    for name in ("gr_db_p95", "gr_db", "gain_reduction_db", "gr"):
        value = _normalise_current_gr(row.get(name))
        if value is not None:
            return value
    return None


def _fallback_current_series(report: Mapping[str, Any], kind: str,
                             rows: Sequence[Mapping[str, Any]]) -> dict[str, _Series]:
    result: dict[str, _Series] = {}
    for index, row in enumerate(rows):
        line = _current_lane_value(row, kind)
        if line is not None:
            lane = _row_lane(row, index)
            if lane is not None:
                # Summary fields such as gr_db_p95 describe the whole audition
                # segment; with no time axis they remain visible for the segment
                # instead of decaying as if they were the final envelope point.
                result[lane] = _Series((0.0,), (line,), DEFAULT_ENVELOPE_INTERVAL_MS,
                                       PEAK_ENVELOPE_FLOOR_DBFS if kind == "peak"
                                       else GR_ENVELOPE_FLOOR_DB,
                                       0.0, kind == "gr")
    for container in _report_containers(report):
        if kind == "peak":
            candidates = (
                container.get("bus_peak_dbfs"), container.get("mix_peak_dbfs"),
                container.get("peak_dbfs"),
            )
        else:
            candidates = (
                container.get("bus_gr_db"), container.get("gr_db"),
                container.get("gain_reduction_db"), container.get("gr_db_p95"),
            )
        for candidate in candidates:
            value = _finite(candidate) if kind == "peak" else _normalise_current_gr(candidate)
            if value is not None:
                result["bus"] = _Series((0.0,), (value,), DEFAULT_ENVELOPE_INTERVAL_MS,
                                        PEAK_ENVELOPE_FLOOR_DBFS if kind == "peak"
                                        else GR_ENVELOPE_FLOOR_DB,
                                        0.0, kind == "gr")
                return result
    return result


class MeterEnvelope(QObject):
    """Playback-time bridge for offline reports and actual live block meters.

    :meth:`set_live_snapshot` is the live UI entry point.  Each call replaces
    the published lane set with the measurements from that output block; it
    never interpolates an offline envelope or reuses an absent value.  The
    original :meth:`set_report` API remains available for existing report and
    export consumers.

    In report mode, :meth:`set_position_ms` accepts arbitrary player cadence and
    normalises values with the report's ``duration_seconds`` (or an explicit
    :meth:`set_duration_ms`) so positive overshoot and negative positions wrap
    instead of being silently clamped.  In normal operation one QTimer delivers
    at most 30 snapshots per second.  Live snapshots already carry their own
    transport position and are published immediately, cutting any pending
    report delivery.

    Reports merge by lane by default.  A report with a different output
    identity, A/B processing state, or audition start invalidates the previous
    series even in merge mode.  Live source, parameter revision, duration, lane
    set, and transport discontinuities advance ``history_epoch`` so views can
    discard measurements from an earlier session.

    ``changed`` retains the original snapshot shape and adds
    ``duration_ms``/``position_ratio``/``wrapped``/``seeked`` and report
    generation ``history_epoch`` keys.
    ``positionChanged`` emits only the delivered normalised millisecond
    position; ``positionWrapped`` additionally announces loop/overshoot
    normalisation.  Envelope arrays are parsed only by report updates, never by
    30 Hz position refreshes.
    """

    changed = pyqtSignal(object)
    cleared = pyqtSignal()
    positionChanged = pyqtSignal(float)
    positionWrapped = pyqtSignal(float)

    def __init__(self, parent: QObject | None = None, *, refresh_hz: float = METER_REFRESH_HZ,
                 auto_start: bool = True, clock: Callable[[], float] | None = None):
        super().__init__(parent)
        refresh = _finite(refresh_hz)
        self.refresh_hz = _clamp(refresh or METER_REFRESH_HZ, 1.0, 120.0)
        self.refresh_interval_ms = 1000.0 / self.refresh_hz
        self.auto_start = bool(auto_start)
        self._clock = clock or (lambda: time.monotonic() * 1000.0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._peak_series: dict[str, _Series] = {}
        self._gr_series: dict[str, _Series] = {}
        self._labels: dict[str, str] = {}
        self._track_ids: list[str] = []
        self._duration_ms: float | None = None
        self._position_ms = 0.0
        self._pending_position_ms = 0.0
        self._pending_source_position_ms = 0.0
        self._pending_wrapped = False
        self._pending_seeked = False
        self._wrapped = False
        self._seeked = False
        self._snapshot = self._empty_snapshot(0.0)
        self._dirty = False
        self._last_delivery_ms: float | None = None
        self._received = 0
        self._delivered = 0
        self._coalesced = 0
        self._parse_error: str | None = None
        self._report_revision = 0
        self._report_scope: dict[str, Any] = {}
        self._history_epoch = 0
        self._live_mode = False
        self._live_initialized = False
        self._live_input_history_epoch: int | None = None
        self._live_source: Any = None
        self._live_parameter_revision: Any = None
        self._live_bus_present = False

    def set_report(self, report: Mapping[str, Any] | None, *, merge: bool = True) -> None:
        """Merge one audition or render-side report into the live bridge.

        ``merge=False`` is equivalent to an explicit reset followed by this
        report.  ``None`` always clears, matching the historical API.
        """
        if report is None:
            self.clear()
            return
        if not isinstance(report, Mapping):
            raise TypeError("MeterEnvelope.set_report 需要 audition report 字典。")
        if self._live_mode:
            # Report parsing belongs to offline/export consumers.  Switching
            # back must never let a previously queued report overwrite live
            # output or append its synthetic envelope to live GR history.
            self._live_mode = False
            self._live_initialized = False
            self._live_input_history_epoch = None
            self._live_source = None
            self._live_parameter_revision = None
            self._live_bus_present = False
            self._timer.stop()
            self._dirty = False
            self._clear_series()
            self._history_epoch += 1
        incoming_scope = _report_scope_values(report)
        scope_changed, segment_changed = _report_scope_conflicts(
            self._report_scope, incoming_scope)
        if not merge:
            self._clear_series()
            self._history_epoch += 1
        elif scope_changed:
            # A/B, a changed parameter render, or a new audition segment owns a
            # different curve. Never append its samples to the previous report.
            self._clear_series()
            self._history_epoch += 1
        if incoming_scope:
            self._report_scope.update(incoming_scope)
        if segment_changed:
            self._position_ms = 0.0
            self._pending_position_ms = 0.0
            self._pending_source_position_ms = 0.0
            self._pending_seeked = True
        for root in _meter_report_roots(report):
            self._ingest_report(root)
        discovered_duration = _report_duration_ms(report)
        if discovered_duration is not None:
            self._duration_ms = discovered_duration
            self._pending_position_ms = _normalise_position_ms(
                self._pending_source_position_ms, self._duration_ms)
        self._parse_error = None
        self._report_revision += 1
        self._request_delivery()

    def set_live_snapshot(self, snapshot: Mapping[str, Any] | None) -> None:
        """Publish one actual output block without report interpolation.

        Required producer fields are ``tracks`` (lane dictionaries with
        role/index, peak_dbfs, rms_dbfs, gr_db, and audible), ``bus``
        (peak_dbfs, rms_dbfs, limiter_gr_db), ``position_ms``,
        ``duration_ms``, ``history_epoch``, ``source``, and
        ``parameter_revision``.  Unknown or missing metric values are
        published as ``None``; this method never falls back to a previous
        report value.

        Every call is a complete current-frame replacement and emits
        ``changed`` synchronously.  A pending report timer is cancelled so a
        stale offline frame cannot later overwrite the live reading.
        """
        if snapshot is None:
            self.clear()
            return
        if not isinstance(snapshot, Mapping):
            raise TypeError("MeterEnvelope.set_live_snapshot 需要 live snapshot 字典。")

        # Live snapshots are complete frames.  A report timer started before
        # the first live frame is stale by definition.
        self._timer.stop()
        self._dirty = False
        if not self._live_mode:
            had_report_measurement = bool(
                self._peak_series or self._gr_series
                or self._snapshot.get("tracks")
                or bool((self._snapshot.get("bus") or {}).get("present")))
            self._clear_series()
            self._report_scope.clear()
            self._live_mode = True
            self._live_initialized = False
            self._live_input_history_epoch = None
            self._live_source = None
            self._live_parameter_revision = None
            self._live_bus_present = False
            if had_report_measurement:
                self._history_epoch += 1

        position_value = _finite(snapshot.get("position_ms"))
        position = 0.0 if position_value is None else float(position_value)
        duration = _finite(snapshot.get("duration_ms"))
        if duration is not None and duration <= 0.0:
            duration = None
        wrapped = bool(_optional_bool(snapshot.get("wrapped")) or False)
        seek_requested = bool(
            _optional_bool(snapshot.get("seeked"))
            or _optional_bool(snapshot.get("seek"))
            or False)

        raw_tracks = snapshot.get("tracks")
        if (not isinstance(raw_tracks, Sequence)
                or isinstance(raw_tracks, (str, bytes, bytearray))):
            raw_tracks = []
        tracks: list[dict[str, Any]] = []
        track_slots: dict[str, int] = {}
        for fallback_index, raw_row in enumerate(raw_tracks):
            if not isinstance(raw_row, Mapping):
                continue
            row = _live_track_row(raw_row, fallback_index)
            if row is None:
                continue
            lane = str(row["id"])
            if lane in track_slots:
                tracks[track_slots[lane]] = row
            else:
                track_slots[lane] = len(tracks)
                tracks.append(row)

        bus = _live_bus_row(snapshot.get("bus"))
        track_ids = tuple(str(row["id"]) for row in tracks)
        bus_present = bool(bus["present"])
        source_value = _safe_deepcopy(snapshot.get("source"))
        revision_value = _live_parameter_revision(snapshot.get("parameter_revision"))
        incoming_epoch = _live_history_epoch(snapshot.get("history_epoch"))

        source_changed = (
            self._live_initialized and source_value != self._live_source)
        revision_changed = (
            self._live_initialized
            and revision_value != self._live_parameter_revision)
        incoming_epoch_changed = (
            self._live_initialized
            and incoming_epoch != self._live_input_history_epoch)
        duration_changed = False
        if self._live_initialized:
            if duration is None or self._duration_ms is None:
                duration_changed = duration is not self._duration_ms
            else:
                duration_changed = abs(duration - self._duration_ms) > 0.001
        lanes_changed = (
            self._live_initialized
            and (set(track_ids) != set(self._track_ids)
                 or bus_present != self._live_bus_present))
        position_seek = (
            self._live_initialized
            and not wrapped
            and (position + 1e-6 < self._position_ms
                 or position - self._position_ms
                 > LIVE_POSITION_DISCONTINUITY_MS))
        seeked = seek_requested or position_seek

        if not self._live_initialized:
            self._history_epoch = max(
                self._history_epoch, incoming_epoch or 0)
        elif (source_changed or revision_changed or incoming_epoch_changed
              or duration_changed or lanes_changed or wrapped or seeked):
            self._history_epoch = max(
                self._history_epoch + 1, incoming_epoch or 0)
        elif (incoming_epoch is not None
              and incoming_epoch > self._history_epoch):
            self._history_epoch = incoming_epoch

        self._duration_ms = duration
        self._position_ms = position
        self._pending_position_ms = position
        self._pending_source_position_ms = position
        self._pending_wrapped = False
        self._pending_seeked = False
        self._wrapped = wrapped
        self._seeked = seeked
        self._track_ids = list(track_ids)
        self._labels = {str(row["id"]): str(row["label"]) for row in tracks}
        if bus_present:
            self._labels["bus"] = str(bus["label"])
        self._live_bus_present = bus_present
        self._live_source = _safe_deepcopy(source_value)
        self._live_parameter_revision = _safe_deepcopy(revision_value)
        self._live_input_history_epoch = incoming_epoch
        self._live_initialized = True
        self._parse_error = None

        self._snapshot = {
            "position_ms": position,
            "duration_ms": duration,
            "position_ratio": _position_ratio_ms(position, duration),
            "wrapped": wrapped,
            "seeked": seeked,
            "history_epoch": self._history_epoch,
            "source": _safe_deepcopy(source_value),
            "parameter_revision": _safe_deepcopy(revision_value),
            "tracks": _safe_deepcopy(tracks),
            "bus": _safe_deepcopy(bus),
            "lanes": _safe_deepcopy([*tracks, bus]),
        }
        self._received += 1
        self._delivered += 1
        self.positionChanged.emit(position)
        if wrapped:
            self.positionWrapped.emit(position)
        self.changed.emit(self.snapshot())

    def _ingest_report(self, report: Mapping[str, Any]) -> None:
        rows = _report_track_rows(report)
        labels = dict(self._labels)
        track_ids = list(self._track_ids)
        for index, row in enumerate(rows):
            lane = _row_lane(row, index)
            if lane is None or lane == "bus":
                continue
            if lane not in track_ids:
                track_ids.append(lane)
            name = row.get("label") or row.get("name") or row.get("display_name")
            if name:
                labels[lane] = str(name)
        peak_value, peak_container = _find_envelope(report, "peak")
        gr_value, gr_container = _find_envelope(report, "gr")
        peak_interval = (_container_interval_ms(peak_container)
                         or _container_interval_ms(report)
                         or DEFAULT_ENVELOPE_INTERVAL_MS)
        gr_interval = (_container_interval_ms(gr_container)
                       or _container_interval_ms(report)
                       or DEFAULT_ENVELOPE_INTERVAL_MS)
        peaks = _parse_envelope_collection(
            peak_value, kind="peak", rows=rows, default_interval_ms=peak_interval,
            labels=labels,
        )
        reductions = _parse_envelope_collection(
            gr_value, kind="gr", rows=rows, default_interval_ms=gr_interval,
            labels=labels,
        )
        for index, row in enumerate(rows):
            lane = _row_lane(row, index)
            peak_payload = row.get("peak_envelope") or row.get("peak_envelopes")
            gr_payload = row.get("gr_envelope") or row.get("gr_envelopes")
            peak_payload = _with_row_timing(peak_payload, row) if peak_payload is not None else None
            gr_payload = _with_row_timing(gr_payload, row) if gr_payload is not None else None
            if lane is not None and peak_payload is not None:
                peaks.update(_parse_envelope_collection(
                    {lane: peak_payload}, kind="peak", rows=rows,
                    default_interval_ms=peak_interval, labels=labels))
            if lane is not None and gr_payload is not None:
                reductions.update(_parse_envelope_collection(
                    {lane: gr_payload}, kind="gr", rows=rows,
                    default_interval_ms=gr_interval, labels=labels))
        for container in _report_containers(report):
            bus_row = container.get("bus")
            if not isinstance(bus_row, Mapping):
                continue
            bus_peak_payload = bus_row.get("peak_envelope") or bus_row.get("peak_envelopes")
            bus_gr_payload = bus_row.get("gr_envelope") or bus_row.get("gr_envelopes")
            bus_peak_payload = _with_row_timing(bus_peak_payload, bus_row) if bus_peak_payload is not None else None
            bus_gr_payload = _with_row_timing(bus_gr_payload, bus_row) if bus_gr_payload is not None else None
            if bus_peak_payload is not None:
                peaks.update(_parse_envelope_collection(
                    {"bus": bus_peak_payload}, kind="peak", rows=rows,
                    default_interval_ms=peak_interval, labels=labels))
            if bus_gr_payload is not None:
                reductions.update(_parse_envelope_collection(
                    {"bus": bus_gr_payload}, kind="gr", rows=rows,
                    default_interval_ms=gr_interval, labels=labels))
            bus_name = bus_row.get("label") or bus_row.get("name")
            if bus_name:
                labels["bus"] = str(bus_name)
        for lane, series in _fallback_current_series(report, "peak", rows).items():
            peaks.setdefault(lane, series)
        for lane, series in _fallback_current_series(report, "gr", rows).items():
            reductions.setdefault(lane, series)
        self._peak_series.update(peaks)
        self._gr_series.update(reductions)
        self._labels.update(labels)
        for lane in sorted(set(peaks) | set(reductions), key=_lane_sort_key):
            if lane != "bus" and lane not in track_ids:
                track_ids.append(lane)
        self._track_ids = sorted(set(track_ids), key=_lane_sort_key)

    def set_duration_ms(self, duration_ms: int | float | None) -> None:
        """Set or clear the report duration used for loop/seek normalisation."""
        if self._live_mode:
            # Live snapshots own position and duration.  Mutating only the
            # timeline would pair old measurements with a new transport frame.
            return
        duration = _finite(duration_ms)
        self._duration_ms = duration if duration is not None and duration > 0.0 else None
        self._position_ms = _normalise_position_ms(self._position_ms, self._duration_ms)
        self._pending_position_ms = _normalise_position_ms(
            self._pending_source_position_ms, self._duration_ms)
        self._request_delivery()

    def set_position_ms(self, position_ms: int | float | None, *,
                        wrapped: bool | None = None, seek: bool | None = None) -> None:
        """Queue a report-mode playback position.

        In live mode the position arrives with each actual snapshot; this
        method deliberately does nothing rather than redisplaying old levels at
        a new transport position.  ``wrapped`` may be supplied by a report
        transport that knows a loop boundary; otherwise it is inferred.
        ``seek`` is advisory metadata for report consumers.
        """
        if self._live_mode:
            return
        value = _finite(position_ms)
        source = 0.0 if value is None else float(value)
        previous = self._pending_position_ms
        normalised = _normalise_position_ms(source, self._duration_ms)
        inferred_wrap = False
        if self._duration_ms is not None and self._duration_ms > 0.0:
            inferred_wrap = (source < 0.0 or source >= self._duration_ms
                             or normalised + self._duration_ms * 0.5 < previous)
        did_wrap = inferred_wrap if wrapped is None else bool(wrapped)
        if seek is None:
            did_seek = not did_wrap and normalised + 1e-9 < previous
        else:
            did_seek = bool(seek)
        self._pending_source_position_ms = source
        self._pending_position_ms = normalised
        self._pending_wrapped = did_wrap
        self._pending_seeked = did_seek
        self._request_delivery()

    def refresh(self, position_ms: int | float | None) -> None:
        """Alias for :meth:`set_position_ms`; does not reparse report arrays."""
        self.set_position_ms(position_ms)

    def merge_report(self, report: Mapping[str, Any]) -> None:
        """Merge a partial report without clearing lanes absent from this call."""
        self.set_report(report, merge=True)

    def replace_report(self, report: Mapping[str, Any] | None) -> None:
        """Explicitly reset accumulated lanes, then apply one report."""
        self.set_report(report, merge=False)

    def _clear_series(self) -> None:
        self._peak_series.clear()
        self._gr_series.clear()
        self._labels.clear()
        self._track_ids.clear()

    def clear(self) -> None:
        """Explicitly reset all reports, duration, position, and derived state."""
        self._timer.stop()
        self._clear_series()
        self._duration_ms = None
        self._position_ms = 0.0
        self._pending_position_ms = 0.0
        self._pending_source_position_ms = 0.0
        self._pending_wrapped = False
        self._pending_seeked = False
        self._wrapped = False
        self._seeked = False
        self._dirty = False
        self._last_delivery_ms = None
        self._report_scope.clear()
        self._live_mode = False
        self._live_initialized = False
        self._live_input_history_epoch = None
        self._live_source = None
        self._live_parameter_revision = None
        self._live_bus_present = False
        self._history_epoch += 1
        self._snapshot = self._empty_snapshot(0.0)
        self._snapshot["history_epoch"] = self._history_epoch
        self.changed.emit(self.snapshot())
        self.cleared.emit()

    def reset(self) -> None:
        """Alias for :meth:`clear`; explicit reset is required to drop lanes."""
        self.clear()

    @property
    def lane_ids(self) -> tuple[str, ...]:
        lanes = list(self._track_ids)
        if (self._live_bus_present if self._live_mode
                else self._has_series("bus")):
            lanes.append("bus")
        return tuple(lanes)

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(self._track_ids)

    @property
    def has_bus_data(self) -> bool:
        return self._live_bus_present if self._live_mode else self._has_series("bus")

    @property
    def current_position_ms(self) -> float:
        return self._position_ms

    @property
    def pending_position_ms(self) -> float:
        return self._pending_position_ms

    @property
    def duration_ms(self) -> float | None:
        return self._duration_ms

    @property
    def position_ratio(self) -> float:
        return _position_ratio_ms(self._position_ms, self._duration_ms)

    @property
    def history_epoch(self) -> int:
        """Generation that changes whenever old GR history must be discarded."""
        return self._history_epoch

    @property
    def wrapped(self) -> bool:
        return self._wrapped

    @property
    def seeked(self) -> bool:
        return self._seeked

    @property
    def pending_event_count(self) -> int:
        return 1 if self._dirty else 0

    @property
    def throttle_stats(self) -> dict[str, int]:
        return {
            "received": self._received,
            "delivered": self._delivered,
            "coalesced": self._coalesced,
        }

    @property
    def report_revision(self) -> int:
        """Monotonic count of report parses; position refreshes do not change it."""
        return self._report_revision

    @property
    def parse_error(self) -> str | None:
        return self._parse_error

    def reset_throttle_stats(self) -> None:
        self._received = self._delivered = self._coalesced = 0

    def snapshot(self, position_ms: int | float | None = None) -> dict[str, Any]:
        if position_ms is not None and not self._live_mode:
            position = _normalise_position_ms(position_ms, self._duration_ms)
            return self._snapshot_at(position)
        return _copy_snapshot(self._snapshot)

    def sample_at(self, position_ms: int | float | None) -> dict[str, Any]:
        """Return a report sample, or the unchanged current live measurement.

        Live values are valid only at the block that produced them, so asking
        for another position must not interpolate a synthetic frame.
        """
        if self._live_mode:
            return self.snapshot()
        return self._snapshot_at(_normalise_position_ms(position_ms, self._duration_ms))

    read_at = sample_at

    def flush(self) -> dict[str, Any]:
        self._deliver(float(self._clock()))
        return self.snapshot()

    def peak_db(self, lane: str, position_ms: int | float | None = None) -> float | None:
        key = lane_id(lane) or str(lane)
        if self._live_mode:
            if (position_ms is not None
                    and abs(float(position_ms) - self._position_ms) > 0.5):
                return None
            item = next((row for row in self._snapshot.get("lanes", [])
                         if isinstance(row, Mapping) and row.get("id") == key), None)
            return _finite(item.get("peak_dbfs")) if item is not None else None
        position = (self._position_ms if position_ms is None
                    else _normalise_position_ms(position_ms, self._duration_ms))
        series = self._peak_series.get(key)
        return series.value_at(position) if series is not None else None

    def gr_db(self, lane: str, position_ms: int | float | None = None) -> float | None:
        key = lane_id(lane) or str(lane)
        if self._live_mode:
            if (position_ms is not None
                    and abs(float(position_ms) - self._position_ms) > 0.5):
                return None
            item = next((row for row in self._snapshot.get("lanes", [])
                         if isinstance(row, Mapping) and row.get("id") == key), None)
            return _lane_gr_db_value(item) if item is not None else None
        position = (self._position_ms if position_ms is None
                    else _normalise_position_ms(position_ms, self._duration_ms))
        series = self._gr_series.get(key)
        return series.value_at(position) if series is not None else None

    def _has_series(self, lane: str) -> bool:
        return lane in self._peak_series or lane in self._gr_series

    def _request_delivery(self) -> None:
        self._received += 1
        if self._dirty:
            self._coalesced += 1
        self._dirty = True
        if not self.auto_start:
            self._deliver(float(self._clock()))
            return
        if self._timer.isActive():
            return
        now = float(self._clock())
        due = (now if self._last_delivery_ms is None
               else self._last_delivery_ms + self.refresh_interval_ms)
        delay = max(0, int(math.ceil(due - now)))
        self._timer.start(delay)

    def _on_timer(self) -> None:
        now = float(self._clock())
        due = (now if self._last_delivery_ms is None
               else self._last_delivery_ms + self.refresh_interval_ms)
        if now + 1e-6 < due:
            self._timer.start(max(0, int(math.ceil(due - now))))
            return
        self._deliver(now)

    def _deliver(self, now_ms: float) -> None:
        self._timer.stop()
        if not self._dirty:
            return
        self._dirty = False
        self._position_ms = _normalise_position_ms(
            self._pending_position_ms, self._duration_ms)
        self._wrapped = self._pending_wrapped
        self._seeked = self._pending_seeked
        self._last_delivery_ms = now_ms
        self._snapshot = self._snapshot_at(self._position_ms)
        self._delivered += 1
        self.positionChanged.emit(self._position_ms)
        if self._wrapped:
            self.positionWrapped.emit(self._position_ms)
        self.changed.emit(self.snapshot())

    def _snapshot_at(self, position_ms: float) -> dict[str, Any]:
        position = _normalise_position_ms(position_ms, self._duration_ms)
        tracks: list[dict[str, Any]] = []
        for lane in self._track_ids:
            parsed = _split_lane(lane) or ("vocal", 0)
            role, index = parsed
            peak_series = self._peak_series.get(lane)
            gr_series = self._gr_series.get(lane)
            peak = peak_series.value_at(position) if peak_series is not None else None
            gr = gr_series.value_at(position) if gr_series is not None else None
            tracks.append({
                "id": lane,
                "role": role,
                "index": index,
                "label": _display_lane_label(
                    self._labels.get(lane) or _lane_label(role, index)),
                "peak_dbfs": peak,
                "rms_dbfs": None,
                "gr_db": gr,
                "audible": None,
                "clip": peak is not None and peak >= -1.0,
                "present": peak_series is not None or gr_series is not None,
            })
        bus_peak = self._peak_series.get("bus")
        bus_gr = self._gr_series.get("bus")
        bus_peak_value = bus_peak.value_at(position) if bus_peak is not None else None
        bus_gr_value = bus_gr.value_at(position) if bus_gr is not None else None
        bus = {
            "id": "bus",
            "role": "bus",
            "index": 0,
            "label": _display_lane_label(
                self._labels.get("bus") or "MIX BUS"),
            "peak_dbfs": bus_peak_value,
            "rms_dbfs": None,
            "gr_db": bus_gr_value,
            "limiter_gr_db": bus_gr_value,
            "audible": None,
            "clip": bus_peak_value is not None and bus_peak_value >= -1.0,
            "present": bus_peak is not None or bus_gr is not None,
        }
        return {
            "position_ms": position,
            "duration_ms": self._duration_ms,
            "position_ratio": _position_ratio_ms(position, self._duration_ms),
            "wrapped": self._wrapped,
            "seeked": self._seeked,
            "history_epoch": self._history_epoch,
            "source": None,
            "parameter_revision": None,
            "tracks": tracks,
            "bus": bus,
            "lanes": [*tracks, bus],
        }

    @staticmethod
    def _empty_snapshot(position_ms: float) -> dict[str, Any]:
        bus = {
            "id": "bus", "role": "bus", "index": 0, "label": "MIX BUS",
            "peak_dbfs": None, "rms_dbfs": None, "gr_db": None,
            "limiter_gr_db": None, "audible": None, "clip": False,
            "present": False,
        }
        return {
            "position_ms": position_ms,
            "duration_ms": None,
            "position_ratio": 0.0,
            "wrapped": False,
            "seeked": False,
            "history_epoch": 0,
            "source": None,
            "parameter_revision": None,
            "tracks": [],
            "bus": bus,
            "lanes": [bus],
        }


def _copy_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    result = _safe_deepcopy(dict(snapshot))
    raw_tracks = snapshot.get("tracks")
    if not isinstance(raw_tracks, Sequence) or isinstance(
            raw_tracks, (str, bytes, bytearray)):
        raw_tracks = []
    tracks = [_safe_deepcopy(dict(item)) for item in raw_tracks
              if isinstance(item, Mapping)]
    raw_bus = snapshot.get("bus")
    bus = (_safe_deepcopy(dict(raw_bus)) if isinstance(raw_bus, Mapping)
           else MeterEnvelope._empty_snapshot(0.0)["bus"])
    bus_gr = _finite(bus.get("gr_db"))
    bus_limiter_gr = _finite(bus.get("limiter_gr_db"))
    if bus_limiter_gr is None:
        bus["limiter_gr_db"] = bus_gr
    elif bus_gr is None:
        bus["gr_db"] = bus_limiter_gr
    position = _finite(snapshot.get("position_ms"))
    duration = _finite(snapshot.get("duration_ms"))
    if duration is not None and duration <= 0.0:
        duration = None
    result.update({
        "position_ms": 0.0 if position is None else float(position),
        "duration_ms": duration,
        "position_ratio": _position_ratio_ms(
            0.0 if position is None else float(position), duration),
        "wrapped": bool(snapshot.get("wrapped", False)),
        "seeked": bool(snapshot.get("seeked", False)),
        "history_epoch": max(0, int(snapshot.get("history_epoch", 0) or 0)),
        "source": _safe_deepcopy(snapshot.get("source")),
        "parameter_revision": _live_parameter_revision(
            snapshot.get("parameter_revision")),
        "tracks": tracks,
        "bus": bus,
        "lanes": [_safe_deepcopy(item) for item in tracks] + [bus],
    })
    return result


def format_dbfs(value: float | None) -> str:
    number = _finite(value)
    if number is None:
        return "— dBFS"
    if number <= PEAK_FLOOR_DBFS + 0.05:
        return "-∞ dBFS"
    return "%.1f dBFS" % number


def _widget_font(base: QFont, delta: float = 0.0, *, bold: bool = False) -> QFont:
    """Scale from the effective QSS font without discarding pixel sizing."""
    font = QFont(base)
    if font.pixelSize() > 0:
        font.setPixelSize(max(8, int(round(font.pixelSize() + delta))))
    elif font.pointSizeF() > 0.0:
        font.setPointSizeF(max(8.0, font.pointSizeF() + delta * 0.75))
    else:
        font.setPointSizeF(max(8.5, 10.0 + delta * 0.75))
    font.setBold(bold)
    return font


def format_time_ms(value_ms: int | float | None) -> str:
    """Format a non-negative audition position as ``m:ss.mmm``."""
    value = _finite(value_ms)
    milliseconds = max(0, int(round(value or 0.0)))
    minutes, remainder = divmod(milliseconds, 60000)
    seconds, millis = divmod(remainder, 1000)
    return "%d:%02d.%03d" % (minutes, seconds, millis)


def _contrast_ratio(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        channels = []
        for channel in (color.redF(), color.greenF(), color.blueF()):
            channels.append(channel / 12.92 if channel <= 0.04045
                            else ((channel + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    first_luma = luminance(first)
    second_luma = luminance(second)
    lighter = max(first_luma, second_luma)
    darker = min(first_luma, second_luma)
    return (lighter + 0.05) / max(0.05, darker + 0.05)


def _drawing_colors(widget: QWidget) -> tuple[QColor, QColor, QColor, QColor]:
    """Return a readable opaque-enough surface and foreground for any theme."""
    palette = widget.palette()
    base = QColor(palette.color(palette.Base))
    window = QColor(palette.color(palette.Window))
    text = QColor(palette.color(palette.Text))
    if text.alpha() < 64:
        text = QColor(palette.color(palette.WindowText))
    if base.alpha() < 64:
        if window.alpha() >= 64:
            base = window
        elif text.alpha() >= 64 and text.lightness() < 128:
            base = QColor("#f7f8fb")
        else:
            base = QColor("#20232b")
    base = QColor(base.red(), base.green(), base.blue(), 238)
    target = QColor("#12151b") if base.lightness() >= 142 else QColor("#f6f7fb")
    if text.alpha() < 64 or _contrast_ratio(text, base) < 3.0:
        text = target
    text = QColor(text.red(), text.green(), text.blue(), 255)
    grid = QColor(text)
    grid.setAlpha(42)
    secondary = QColor(text)
    secondary.setAlpha(178)
    return base, text, grid, secondary


def _draw_playhead(painter: QPainter, x: float, top: float, bottom: float,
                   surface: QColor) -> None:
    """Draw a high-contrast vertical transport cursor at current geometry."""
    if not math.isfinite(float(x)) or bottom <= top:
        return
    accent = QColor("#087fab") if surface.lightness() >= 142 else QColor("#4bc8ff")
    halo = QColor(surface.red(), surface.green(), surface.blue(), 225)
    painter.save()
    painter.setPen(QPen(halo, 4.0))
    painter.drawLine(QPointF(x, top), QPointF(x, bottom))
    painter.setPen(QPen(accent, 1.5))
    painter.drawLine(QPointF(x, top), QPointF(x, bottom))
    cap = QPainterPath()
    cap.moveTo(x - 4.0, max(top - 5.0, 0.0))
    cap.lineTo(x + 4.0, max(top - 5.0, 0.0))
    cap.lineTo(x, top + 1.0)
    cap.closeSubpath()
    painter.setPen(Qt.NoPen)
    painter.setBrush(accent)
    painter.drawPath(cap)
    painter.restore()


def _is_computing(container: Mapping[str, Any]) -> bool:
    for key in ("computing", "calculating", "pending", "in_progress"):
        if key in container:
            return bool(container.get(key))
    return str(container.get("cache_state", "")).lower() in {"computing", "pending", "working"}


def _cache_text(container: Mapping[str, Any], *, key: str | None) -> str:
    if _is_computing(container):
        return _tr("计算中")
    candidates = (
        container.get("cache_status"), container.get("cache_state"),
        container.get("edc_cache_status"), container.get("state"),
    )
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            value = str(candidate).strip()
            lowered = value.lower()
            labels = {
                "hit": "缓存命中", "cached": "缓存命中",
                "miss": "缓存未命中", "computed": "已生成",
                "error": "计算失败", "failed": "计算失败",
                "empty": "无数据",
            }
            return _tr(labels.get(lowered, value))
    for name in ("cache_hit", "reverb_cache_hit", "edc_cache_hit"):
        if name in container:
            return _tr("缓存命中") if bool(container.get(name)) else _tr("缓存未命中")
    if key:
        return _tr("缓存键 ") + (key[:18] + "…" if len(key) > 19 else key)
    return "—"


def _edc_containers(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    containers = _report_containers(report)
    seen = {id(item) for item in containers}
    for container in list(containers):
        for name in ("reverb", "reverb_measurement", "reverb_edc_meta"):
            value = container.get(name)
            if isinstance(value, Mapping) and id(value) not in seen:
                seen.add(id(value))
                containers.append(value)
    return containers


def _edc_metadata(report: Mapping[str, Any], descriptor: Mapping[str, Any] | None) -> dict[str, Any]:
    containers = ([descriptor] if isinstance(descriptor, Mapping) else []) + _edc_containers(report)
    metadata: dict[str, Any] = {
        "profile": None, "predelay_ms": None, "key": None,
        "cache_status": "—", "cache_hit": None, "measured": None,
        "computing": False, "duration_ms": None,
    }
    for container in containers:
        if metadata["profile"] is None:
            for name in ("profile", "reverb_profile", "space"):
                if container.get(name) is not None:
                    metadata["profile"] = str(container.get(name))
                    break
        if metadata["predelay_ms"] is None:
            for name in ("predelay_ms", "reverb_predelay_ms", "pre_delay_ms"):
                value = _finite(container.get(name))
                if value is not None:
                    metadata["predelay_ms"] = value
                    break
        if metadata["key"] is None:
            for name in ("key", "cache_key", "edc_key"):
                if container.get(name) is not None:
                    metadata["key"] = str(container.get(name))
                    break
        if metadata["duration_ms"] is None:
            duration_ms = _finite(container.get("duration_ms"))
            duration_s = _finite(container.get("duration_s") or container.get("duration_seconds"))
            if duration_ms is not None:
                metadata["duration_ms"] = max(0.0, duration_ms)
            elif duration_s is not None:
                metadata["duration_ms"] = max(0.0, duration_s * 1000.0)
        if metadata["measured"] is None:
            for name in ("measured", "is_measured", "edc_measured"):
                if name in container:
                    metadata["measured"] = bool(container.get(name))
                    break
        if metadata["cache_hit"] is None:
            for name in ("cache_hit", "reverb_cache_hit", "edc_cache_hit"):
                if name in container:
                    metadata["cache_hit"] = bool(container.get(name))
                    break
            if metadata["cache_hit"] is None:
                for name in ("cache_status", "cache_state", "edc_cache_status", "state"):
                    state = str(container.get(name) or "").strip().lower()
                    if state in {"hit", "cached"}:
                        metadata["cache_hit"] = True
                        break
                    if state in {"miss", "computed"}:
                        metadata["cache_hit"] = False
                        break
        if _is_computing(container):
            metadata["computing"] = True
    metadata["cache_status"] = _cache_text(
        descriptor if isinstance(descriptor, Mapping) else report,
        key=metadata["key"],
    )
    return metadata


def _find_reverb_edc(report: Mapping[str, Any]) -> tuple[Any, Mapping[str, Any] | None]:
    # Later A/B variants (especially ``after``) take precedence over dry/base.
    for container in reversed(_edc_containers(report)):
        for name in ("reverb_edc", "edc", "edc_curve", "energy_decay_curve", "edc_db"):
            if name in container and container.get(name) is not None:
                # Keep a descriptor when the curve itself is a bare sequence
                # so ``edc_db`` and ``time_seconds`` remain paired.
                value = container.get(name)
                descriptor = value if isinstance(value, Mapping) else container
                return descriptor, descriptor
    return None, None


def _reverb_edc_declared(report: Mapping[str, Any]) -> bool:
    for container in _edc_containers(report):
        if any(name in container for name in (
                "reverb_edc", "edc", "edc_curve", "energy_decay_curve", "edc_db")):
            return True
    return False


def _parse_edc(report: Mapping[str, Any]) -> tuple[tuple[tuple[float, float], ...], dict[str, Any]]:
    value, descriptor = _find_reverb_edc(report)
    metadata = _edc_metadata(report, descriptor)
    if value is None:
        return (), metadata
    payload = _payload_for_kind(value, "edc")
    default_interval = (_container_interval_ms(descriptor)
                        or _container_interval_ms(report)
                        or DEFAULT_ENVELOPE_INTERVAL_MS)
    series = _make_series(
        payload,
        default_interval_ms=default_interval,
        floor_db=EDC_FLOOR_DB,
        decay_db_per_second=0.0,
    )
    if series is None:
        return (), metadata
    points = tuple(
        (float(timestamp), _clamp(float(level), EDC_FLOOR_DB, 0.0))
        for timestamp, level in zip(series.times_ms, series.values)
    )
    if metadata["duration_ms"] is None and points:
        metadata["duration_ms"] = max(points[-1][0], default_interval)
    if metadata["measured"] is None:
        metadata["measured"] = bool(points)
    return points, metadata


class GrHistoryView(QWidget):
    """Playback-aligned gain-reduction graph on a focused 0 to -6 dB scale."""

    historyChanged = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None, *, feed: MeterEnvelope | None = None,
                 auto_start: bool = True, history_seconds: float = GR_HISTORY_SECONDS):
        super().__init__(parent)
        self.setObjectName("mixdownGrHistory")
        self.setAccessibleName(_tr("GR 历史曲线"))
        self.setMinimumSize(320, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        seconds = _finite(history_seconds) or GR_HISTORY_SECONDS
        self.history_seconds = _clamp(seconds, 1.0, 120.0)
        self.feed = feed or MeterEnvelope(self, auto_start=auto_start)
        self.feed.changed.connect(self._apply_snapshot)
        self.feed.cleared.connect(self._clear_history)
        self._selected_lane = "bus"
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._last_position_ms: float | None = None
        self._position_ms = float(self.feed.current_position_ms)
        self._current_gr: float | None = None
        self._has_report = False
        self._history_epoch = int(self.feed.history_epoch)

    def sizeHint(self) -> QSize:
        return QSize(460, 238)

    @property
    def selected_lane(self) -> str:
        return self._selected_lane

    @property
    def current_gr_db(self) -> float | None:
        return self._current_gr

    @property
    def current_display_gr_db(self) -> float | None:
        """Return the current value after clamping it to the visible range."""
        return self.clamped_gr_db()

    @property
    def current_gr_over_range(self) -> bool:
        """Whether the current value exceeds the visible -6 dB floor."""
        return self.is_gr_over_range()

    @property
    def current_range_text(self) -> str:
        """Return a compact label that never hides a clamped raw value."""
        value = self._current_gr
        if value is None:
            return "—"
        if self.is_gr_over_range(value):
            return "%.1f dB / -6 dB MAX" % value
        return "%.1f dB" % value

    @property
    def has_data(self) -> bool:
        return bool(self.history_points())

    @property
    def caption(self) -> str:
        return _tr("GR 为分块RMS估计（非采样精确）")

    @property
    def meter_hint(self) -> str:
        """Clarify that a true 0 dB reading can mean compression is inactive."""
        return _tr("分块估算 · 压缩关闭/未触发=0 dB")

    @property
    def compact_meter_hint(self) -> str:
        """Shorter form for the minimum-width graph without losing the zero cue."""
        return _tr("分块 · 0 dB=OFF")

    @property
    def position_ms(self) -> float:
        return self._position_ms

    def clamped_gr_db(self, value: float | None = None) -> float | None:
        """Normalise GR magnitude to a negative dB value and clamp at -6 dB."""
        number = self._current_gr if value is None else _finite(value)
        if number is None:
            return None
        return _clamp(-abs(number), GR_FLOOR_DB, 0.0)

    def is_gr_over_range(self, value: float | None = None) -> bool:
        """Return whether a value falls below the visible -6 dB floor."""
        number = self._current_gr if value is None else _finite(value)
        return number is not None and -abs(number) < GR_FLOOR_DB - 1e-9

    def gr_plot_y(self, value: float | None = None,
                  graph: QRectF | None = None) -> float | None:
        """Map 0 dB to the plot top and -6 dB to the plot bottom."""
        display = self.clamped_gr_db(value)
        if display is None:
            return None
        target = graph or self.plot_rect()
        fraction = abs(display) / abs(GR_FLOOR_DB)
        return target.top() + target.height() * _clamp(fraction, 0.0, 1.0)

    def axis_tick_values(self) -> tuple[float, ...]:
        """Expose the full 0-to--6 dB axis for callers and rendering tests."""
        return GR_AXIS_TICKS_DB

    def visible_time_range_ms(self) -> tuple[float, float]:
        """Return the playback-relative time window painted by this view."""
        return self._time_window()

    def playhead_ratio(self) -> float:
        """Return the transport cursor ratio inside the visible GR window."""
        start_ms, end_ms = self._time_window()
        return _clamp((self._position_ms - start_ms) / max(1.0, end_ms - start_ms),
                      0.0, 1.0)

    def plot_rect(self) -> QRectF:
        """Return the current graph rectangle; resize recalculates this geometry."""
        width = max(1, self.width())
        height = max(1, self.height())
        margin_left, margin_right = 44.0, 14.0
        graph_top = 70.0
        graph_bottom = max(graph_top + 30.0, height - 24.0)
        return QRectF(margin_left, graph_top,
                      max(30.0, width - margin_left - margin_right),
                      max(30.0, graph_bottom - graph_top))

    def playhead_x(self) -> float:
        """Return the resize-safe x coordinate of the current GR playhead."""
        graph = self.plot_rect()
        return graph.left() + graph.width() * self.playhead_ratio()

    def _time_window(self) -> tuple[float, float]:
        window_ms = self.history_seconds * 1000.0
        duration_ms = _finite(self.feed.duration_ms)
        if duration_ms is not None and duration_ms > 0.0:
            if duration_ms <= window_ms + 1e-9:
                return 0.0, duration_ms
            end_ms = max(window_ms, min(duration_ms, self._position_ms))
            return end_ms - window_ms, end_ms
        end_ms = max(window_ms, self._position_ms)
        return end_ms - window_ms, end_ms

    def available_lanes(self) -> tuple[str, ...]:
        return self.feed.lane_ids

    def set_lane(self, lane: str | tuple[str, int] | Mapping[str, Any] | None) -> None:
        if isinstance(lane, Mapping):
            candidate = lane_id(lane)
        elif isinstance(lane, tuple):
            candidate = lane_id(lane[0], lane[1] if len(lane) > 1 else 0)
        else:
            candidate = lane_id(lane) or str(lane) if lane is not None else None
        if candidate in self.feed.lane_ids:
            self._selected_lane = candidate
            snapshot = self.feed.snapshot()
            self._select_current(snapshot)
            self.update()

    def set_report(self, report: Mapping[str, Any] | None, *, merge: bool = True) -> None:
        """Merge partial reports by default; use ``merge=False`` or clear to reset."""
        if report is None:
            self.clear()
            return
        if not merge:
            self._clear_history()
        self.feed.set_report(report, merge=merge)
        self._has_report = True
        if self._selected_lane not in self.feed.lane_ids:
            lanes = self.feed.lane_ids
            if self.feed.has_bus_data or not lanes:
                self._selected_lane = "bus"
            else:
                self._selected_lane = next((lane for lane in lanes if lane != "bus"), lanes[0])
        self._select_current(self.feed.snapshot())

    def set_position_ms(self, position_ms: int | float | None) -> None:
        self.feed.set_position_ms(position_ms)

    def clear(self) -> None:
        self.feed.clear()
        self._has_report = False

    def reset(self) -> None:
        """Alias for :meth:`clear`; explicit reset drops every merged lane."""
        self.clear()

    def history_points(self, lane: str | None = None) -> tuple[tuple[float, float], ...]:
        candidate = lane or self._selected_lane
        key = lane_id(candidate) or "bus"
        return tuple(self._history.get(key, ()))

    def _clear_history(self) -> None:
        self._history.clear()
        self._last_position_ms = None
        self._current_gr = None
        self.update()

    def _select_current(self, snapshot: Mapping[str, Any]) -> None:
        lane = next((item for item in snapshot.get("lanes", [])
                     if item.get("id") == self._selected_lane), None)
        value = _lane_gr_db_value(lane) if isinstance(lane, Mapping) else None
        self._current_gr = _finite(value)
        self.update()

    def _apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping):
            return
        position = float(snapshot.get("position_ms", 0.0) or 0.0)
        self._position_ms = position
        epoch = int(snapshot.get("history_epoch", self._history_epoch) or 0)
        epoch_changed = epoch != self._history_epoch
        if epoch_changed:
            self._history.clear()
            self._last_position_ms = None
            self._current_gr = None
            self._history_epoch = epoch
        if (bool(snapshot.get("wrapped")) or bool(snapshot.get("seeked"))
                or (self._last_position_ms is not None
                    and (position + 1.0 < self._last_position_ms
                         or position - self._last_position_ms > 2000.0))):
            self._history.clear()
        lanes = snapshot.get("lanes") or []
        if self._selected_lane not in {item.get("id") for item in lanes if isinstance(item, Mapping)}:
            candidates = [item.get("id") for item in lanes
                          if isinstance(item, Mapping) and item.get("id")]
            if self.feed.has_bus_data or not candidates:
                self._selected_lane = "bus"
            elif candidates:
                self._selected_lane = candidates[0]
        history_changed = epoch_changed
        for item in lanes:
            if not isinstance(item, Mapping):
                continue
            lane = str(item.get("id") or "")
            value = _lane_gr_db_value(item)
            if not lane or value is None:
                continue
            bucket = self._history.setdefault(lane, deque())
            if bucket and abs(bucket[-1][0] - position) < 0.5:
                if abs(bucket[-1][1] - value) > 1e-6:
                    bucket[-1] = (position, value)
                    history_changed = True
            else:
                bucket.append((position, value))
                history_changed = True
        self._last_position_ms = position
        cutoff = position - self.history_seconds * 1000.0
        for bucket in self._history.values():
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
                history_changed = True
        self._select_current(snapshot)
        if history_changed:
            self.historyChanged.emit(self.history_points())

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base, text, grid, secondary = _drawing_colors(self)
        red = QColor("#c43d58") if base.lightness() >= 142 else QColor("#ff6b83")
        width = max(1, self.width())
        height = max(1, self.height())
        margin_left = 52.0
        graph = self.plot_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0), 8.0, 8.0)

        title_font = _widget_font(self.font(), 1.0, bold=True)
        painter.setFont(title_font)
        painter.setPen(text)
        start_ms, end_ms = self._time_window()
        span_seconds = max(0.001, (end_ms - start_ms) / 1000.0)
        title_rect = QRectF(12.0, 7.0, max(80.0, width - 238.0), 20.0)
        title = (_tr("GR 历史 · 最近 %g s") % span_seconds
                 if width >= 520.0 else _tr("压缩 GR"))
        title = painter.fontMetrics().elidedText(
            title, Qt.ElideRight, max(1, int(title_rect.width())))
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.setFont(self.font())
        over_color = QColor("#e98420") if base.lightness() >= 142 else QColor("#ffb454")
        current_over = self.is_gr_over_range()
        painter.setPen(over_color if current_over else text)
        painter.drawText(QRectF(width - 224.0, 7.0, 210.0, 20.0),
                         Qt.AlignRight | Qt.AlignVCenter, self.current_range_text)

        bar = QRectF(graph.left(), 31.0, graph.width(), 8.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(text.red(), text.green(), text.blue(), 22))
        painter.drawRoundedRect(bar, 4.0, 4.0)
        display_current = self.clamped_gr_db()
        if display_current is not None and display_current < 0.0:
            fraction = _clamp(abs(display_current) / abs(GR_FLOOR_DB), 0.0, 1.0)
            fill = QRectF(bar.left(), bar.top(), bar.width() * fraction, bar.height())
            painter.setBrush(over_color if current_over else red)
            painter.drawRoundedRect(fill, 4.0, 4.0)
        painter.setPen(secondary)
        timestamp_width = 92.0
        hint_rect = QRectF(
            graph.left(), 43.0, max(30.0, graph.width() - timestamp_width), 18.0)
        hint_text = (self.meter_hint if hint_rect.width() >= 280.0
                     else self.compact_meter_hint)
        hint = painter.fontMetrics().elidedText(
            hint_text, Qt.ElideRight, max(1, int(hint_rect.width())))
        painter.drawText(hint_rect, Qt.AlignLeft | Qt.AlignVCenter, hint)
        painter.drawText(
            QRectF(graph.right() - timestamp_width, 43.0,
                   timestamp_width, 18.0),
            Qt.AlignRight | Qt.AlignVCenter, format_time_ms(self._position_ms))

        ticks = self.axis_tick_values() if graph.height() >= 84.0 else (
            0.0, -2.0, -4.0, -6.0,
        )
        for db in ticks:
            y = self.gr_plot_y(db, graph)
            if y is None:
                continue
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
            painter.setPen(QColor(text.red(), text.green(), text.blue(), 165))
            label = ("%d dB" % db) if db in (0.0, GR_FLOOR_DB) else "%d" % db
            painter.drawText(QRectF(2.0, y - 8.0, margin_left - 7.0, 16.0),
                             Qt.AlignRight | Qt.AlignVCenter, label)

        for index, fraction in enumerate((0.0, 0.5, 1.0)):
            x = graph.left() + graph.width() * fraction
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
            timestamp = start_ms + (end_ms - start_ms) * fraction
            align = (Qt.AlignLeft if index == 0 else
                     Qt.AlignRight if index == 2 else Qt.AlignCenter)
            painter.setPen(QColor(text.red(), text.green(), text.blue(), 145))
            tick_rect = (QRectF(graph.left(), graph.bottom() + 2.0, 76.0, 18.0)
                         if index == 0 else
                         QRectF(graph.right() - 76.0, graph.bottom() + 2.0, 76.0, 18.0)
                         if index == 2 else
                         QRectF(x - 38.0, graph.bottom() + 2.0, 76.0, 18.0))
            painter.drawText(tick_rect, align | Qt.AlignVCenter,
                             format_time_ms(timestamp))

        points = [(timestamp, value) for timestamp, value in self.history_points()
                  if start_ms - 1.0 <= timestamp <= end_ms + 1.0]
        visible_over_range = current_over or any(
            self.is_gr_over_range(value) for _, value in points)
        if visible_over_range:
            painter.setPen(over_color)
            painter.drawText(QRectF(graph.right() - 84.0, graph.top() + 3.0, 78.0, 16.0),
                             Qt.AlignRight | Qt.AlignVCenter, "-6 dB MAX")

        def draw_current_marker() -> None:
            marker_y = self.gr_plot_y(graph=graph)
            if marker_y is None:
                return
            marker = QPointF(self.playhead_x(), marker_y)
            painter.setPen(QPen(base, 3.5))
            painter.setBrush(over_color if current_over else red)
            painter.drawEllipse(marker, 4.5, 4.5)

        if not points:
            _draw_playhead(painter, self.playhead_x(), graph.top(), graph.bottom(), base)
            draw_current_marker()
            painter.setPen(QColor(text.red(), text.green(), text.blue(), 165))
            message = (_tr("暂无 GR 数据 · 等待试听包络")
                       if not self._has_report else _tr("当前区间没有 GR 采样"))
            painter.drawText(graph, Qt.AlignCenter, message)
            return
        path = QPainterPath()
        for index, (timestamp, value) in enumerate(points):
            x = (graph.left() + (timestamp - start_ms)
                 / max(1.0, end_ms - start_ms) * graph.width())
            y = self.gr_plot_y(value, graph)
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        area = QPainterPath(path)
        first_x = (graph.left() + (points[0][0] - start_ms)
                   / max(1.0, end_ms - start_ms) * graph.width())
        last_x = (graph.left() + (points[-1][0] - start_ms)
                  / max(1.0, end_ms - start_ms) * graph.width())
        area.lineTo(last_x, graph.top())
        area.lineTo(first_x, graph.top())
        area.closeSubpath()
        fill = QColor(red)
        fill.setAlpha(34)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawPath(area)
        painter.setPen(QPen(red, 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        if visible_over_range:
            painter.setPen(QPen(over_color, 2.0))
            for timestamp, value in points:
                if not self.is_gr_over_range(value):
                    continue
                x = (graph.left() + (timestamp - start_ms)
                     / max(1.0, end_ms - start_ms) * graph.width())
                painter.drawLine(QPointF(x, graph.bottom() - 3.0),
                                 QPointF(x, graph.bottom()))
        _draw_playhead(painter, self.playhead_x(), graph.top(), graph.bottom(), base)
        draw_current_marker()


class ReverbDecayView(QWidget):
    """Measured reverb EDC with cache provenance and an injectable playhead.

    Pass the same :class:`MeterEnvelope` used by the meter bank and GR history
    so ``set_position_ms`` and player-driven ``changed`` signals update all
    views at the same coalesced cadence.  The playback cursor is a dedicated
    transport rail: EDC tail time remains the graph x-axis and is never confused
    with audition duration.  ``playhead_x()`` is derived from current widget
    geometry, so resize-safe callers can test or paint the exact same position.
    """

    curveChanged = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None, *,
                 feed: MeterEnvelope | None = None, auto_start: bool = True):
        super().__init__(parent)
        self.setObjectName("mixdownReverbDecay")
        self.setAccessibleName(_tr("实测混响能量衰减曲线"))
        self.setMinimumSize(320, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.feed = feed or MeterEnvelope(self, auto_start=auto_start)
        self.feed.changed.connect(self._apply_meter_snapshot)
        self._position_ms = float(self.feed.current_position_ms)
        self._audition_duration_ms = self.feed.duration_ms
        self._points: tuple[tuple[float, float], ...] = ()
        self._profile = "—"
        self._predelay_ms: float | None = None
        self._cache_status = "—"
        self._cache_hit: bool | None = None
        self._measured = False
        self._key: str | None = None
        self._computing = False
        self._duration_ms = EDC_DEFAULT_SECONDS * 1000.0
        self._error: str | None = None

    def sizeHint(self) -> QSize:
        return QSize(460, 238)

    @property
    def has_data(self) -> bool:
        return bool(self._points)

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def predelay_ms(self) -> float | None:
        return self._predelay_ms

    @property
    def cache_status(self) -> str:
        return _tr(self._cache_status)

    @property
    def cache_hit(self) -> bool | None:
        return self._cache_hit

    @property
    def measured(self) -> bool:
        return self._measured

    @property
    def cache_key(self) -> str | None:
        return self._key

    @property
    def computing(self) -> bool:
        return self._computing

    @property
    def playback_position_ms(self) -> float:
        return self._position_ms

    @property
    def position_ms(self) -> float:
        """Alias used by playhead-only consumers."""
        return self._position_ms

    @property
    def audition_duration_ms(self) -> float | None:
        return self._audition_duration_ms

    @property
    def time_range_ms(self) -> tuple[float, float]:
        return 0.0, max(0.0, self._duration_ms)

    @property
    def time_range_text(self) -> str:
        start_ms, end_ms = self.time_range_ms
        return "%.2f–%.2f s" % (start_ms / 1000.0, end_ms / 1000.0)

    @property
    def measurement_status(self) -> str:
        if self._computing:
            return _tr("计算中")
        return _tr("实测") if self._measured else _tr("未实测")

    @property
    def placeholder_text(self) -> str:
        if self._error:
            return _tr("实测 EDC 不可用 · %s") % _tr(self._error)
        if self._computing:
            return _tr("正在测量 EDC · 等待脉冲结果")
        return _tr("暂无实测 EDC · 等待脉冲测量")

    def curve_points(self) -> tuple[tuple[float, float], ...]:
        return self._points

    def set_report(self, report: Mapping[str, Any] | None, *, merge: bool = True) -> None:
        """Merge EDC metadata/reports; absent EDC data leaves the old curve intact."""
        if report is None:
            self.clear()
            return
        if not isinstance(report, Mapping):
            raise TypeError("ReverbDecayView.set_report 需要字典。")
        discovered_duration = _report_duration_ms(report)
        if discovered_duration is not None:
            self.feed.set_duration_ms(discovered_duration)
        if not _reverb_edc_declared(report):
            if not merge:
                self.clear()
            return
        points, metadata = _parse_edc(report)
        value, _ = _find_reverb_edc(report)
        if value is None:
            self.clear()
            return
        computing = bool(metadata.get("computing"))
        error = None
        for container in _edc_containers(report):
            if container.get("error"):
                error = str(container.get("error"))
                break
        if not points:
            if computing and self._points:
                # A cache miss may retain the last measured curve while the new
                # 200 ms-debounced measurement is running.
                self._profile = str(metadata.get("profile") or self._profile)
                if metadata.get("predelay_ms") is not None:
                    self._predelay_ms = float(metadata["predelay_ms"])
                self._cache_status = str(metadata.get("cache_status") or _tr("计算中"))
                self._cache_hit = metadata.get("cache_hit")
                self._key = metadata.get("key") or self._key
                self._computing = True
                self._error = error
                self.update()
                return
            self._points = ()
            self._profile = str(metadata.get("profile") or "—")
            self._predelay_ms = (float(metadata["predelay_ms"])
                                 if metadata.get("predelay_ms") is not None else None)
            self._cache_status = str(metadata.get("cache_status") or "—")
            self._cache_hit = metadata.get("cache_hit")
            self._measured = bool(metadata.get("measured"))
            self._key = metadata.get("key")
            self._computing = computing
            self._duration_ms = max(20.0, float(metadata.get("duration_ms") or 0.0))
            self._error = error
            self.curveChanged.emit(self.curve_points())
            self.update()
            return
        self._points = points
        self._profile = str(metadata.get("profile") or "—")
        self._predelay_ms = (float(metadata["predelay_ms"])
                             if metadata.get("predelay_ms") is not None else None)
        self._cache_status = str(metadata.get("cache_status") or "—")
        self._cache_hit = metadata.get("cache_hit")
        self._measured = bool(metadata.get("measured"))
        self._key = metadata.get("key")
        self._computing = computing
        self._error = error
        measured_duration = points[-1][0] if points else 0.0
        self._duration_ms = max(20.0, float(metadata.get("duration_ms") or measured_duration))
        self.curveChanged.emit(self.curve_points())
        self.update()

    def set_edc(self, db: Any = None, *, time_s: Any = None, times_ms: Any = None,
                interval_ms: float | None = None, hz: float | None = None,
                profile: str | None = None, predelay_ms: float | None = None,
                cache_state: str | None = None, cache_hit: bool | None = None,
                key: str | None = None, computing: bool = False,
                duration_s: float | None = None) -> None:
        descriptor: dict[str, Any] = {"db": db}
        if time_s is not None:
            descriptor["time_s"] = time_s
        if times_ms is not None:
            descriptor["times_ms"] = times_ms
        if interval_ms is not None:
            descriptor["interval_ms"] = interval_ms
        if hz is not None:
            descriptor["hz"] = hz
        if profile is not None:
            descriptor["profile"] = profile
        if predelay_ms is not None:
            descriptor["predelay_ms"] = predelay_ms
        if cache_state is not None:
            descriptor["cache_state"] = cache_state
        if cache_hit is not None:
            descriptor["cache_hit"] = cache_hit
        if key is not None:
            descriptor["key"] = key
        if duration_s is not None:
            descriptor["duration_s"] = duration_s
        descriptor["computing"] = bool(computing)
        self.set_report({"reverb_edc": descriptor})

    def set_position_ms(self, position_ms: int | float | None) -> None:
        """Refresh the transport cursor through the shared or internal feed."""
        self.feed.set_position_ms(position_ms)

    def set_duration_ms(self, duration_ms: int | float | None) -> None:
        """Set audition duration used for the transport rail progress ratio."""
        self.feed.set_duration_ms(duration_ms)

    def playhead_ratio(self) -> float:
        """Return audition progress in ``0..1`` for the transport rail."""
        duration = _finite(self._audition_duration_ms)
        if duration is None or duration <= 0.0:
            return 0.0
        return _clamp(self._position_ms / duration, 0.0, 1.0)

    def plot_rect(self) -> QRectF:
        """Return the EDC graph rectangle, recomputed on every resize/paint."""
        width = max(1, self.width())
        height = max(1, self.height())
        margin_left, margin_right = 47.0, 14.0
        graph_top = 82.0
        graph_bottom = max(graph_top + 30.0, height - 26.0)
        return QRectF(margin_left, graph_top,
                      max(30.0, width - margin_left - margin_right),
                      max(30.0, graph_bottom - graph_top))

    def playback_rail_rect(self) -> QRectF:
        graph = self.plot_rect()
        return QRectF(graph.left(), 66.0, graph.width(), 4.0)

    def playhead_x(self) -> float:
        """Return the resize-safe x coordinate of the audition transport cursor."""
        graph = self.plot_rect()
        return graph.left() + graph.width() * self.playhead_ratio()

    def predelay_x(self) -> float | None:
        predelay = _finite(self._predelay_ms)
        if predelay is None or self._duration_ms <= 0.0:
            return None
        graph = self.plot_rect()
        return graph.left() + graph.width() * _clamp(
            predelay / self._duration_ms, 0.0, 1.0)

    def _apply_meter_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping):
            return
        self._position_ms = float(snapshot.get("position_ms", 0.0) or 0.0)
        duration = _finite(snapshot.get("duration_ms"))
        self._audition_duration_ms = duration if duration is not None and duration > 0.0 else None
        self.update()

    def clear(self) -> None:
        self._points = ()
        self._profile = "—"
        self._predelay_ms = None
        self._cache_status = "—"
        self._cache_hit = None
        self._measured = False
        self._key = None
        self._computing = False
        self._duration_ms = EDC_DEFAULT_SECONDS * 1000.0
        self._error = None
        self.curveChanged.emit(self.curve_points())
        self.update()

    def _status_text(self) -> str:
        predelay = "—" if self._predelay_ms is None else "%g ms" % self._predelay_ms
        parts = [
            self.measurement_status,
            self.cache_status,
            _tr("预延迟") + " " + predelay,
        ]
        if self._profile and self._profile != "—":
            parts.append(_tr(self._profile))
        return "  ·  ".join(parts)

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base, text, grid, secondary = _drawing_colors(self)
        accent = QColor("#6546d7") if base.lightness() >= 142 else QColor("#a78dff")
        marker_color = QColor("#8a5a00") if base.lightness() >= 142 else QColor("#f0c15a")
        width = max(1, self.width())
        height = max(1, self.height())
        graph = self.plot_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0), 8.0, 8.0)

        title_font = _widget_font(self.font(), 1.0, bold=True)
        painter.setFont(title_font)
        painter.setPen(text)
        painter.drawText(
            QRectF(12.0, 7.0, max(80.0, graph.width() - 130.0), 20.0),
            Qt.AlignLeft | Qt.AlignVCenter, _tr("实测 EDC · 能量衰减"),
        )
        painter.setFont(self.font())
        if self._computing:
            painter.setPen(QColor("#d7a74b"))
            painter.drawText(QRectF(width - 150.0, 7.0, 136.0, 20.0),
                             Qt.AlignRight | Qt.AlignVCenter, _tr("计算中…"))
        painter.setPen(secondary)
        detail_width = max(1.0, width - 24.0)
        status_text = painter.fontMetrics().elidedText(
            self._status_text(), Qt.ElideRight, int(detail_width))
        painter.drawText(QRectF(12.0, 28.0, detail_width, 18.0),
                         Qt.AlignLeft | Qt.AlignVCenter, status_text)
        detail_text = self.time_range_text
        if self._key:
            detail_text += "  ·  key: " + (
                self._key[:30] + "…" if len(self._key) > 31 else self._key)
        painter.setPen(QColor(text.red(), text.green(), text.blue(), 125))
        detail_text = painter.fontMetrics().elidedText(
            detail_text, Qt.ElideRight, int(detail_width))
        painter.drawText(QRectF(12.0, 46.0, detail_width, 17.0),
                         Qt.AlignLeft | Qt.AlignVCenter, detail_text)

        rail = self.playback_rail_rect()
        painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 34), 1.0))
        painter.drawLine(QPointF(rail.left(), rail.center().y()),
                         QPointF(rail.right(), rail.center().y()))
        painter.setPen(QPen(accent, 2.0))
        painter.drawLine(QPointF(rail.left(), rail.center().y()),
                         QPointF(self.playhead_x(), rail.center().y()))
        _draw_playhead(painter, self.playhead_x(), rail.top() - 1.0,
                       rail.bottom() + 1.0, base)

        for index in range(6):
            db = -12.0 * index
            y = graph.top() + graph.height() * index / 5.0
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
            painter.setPen(secondary)
            painter.drawText(QRectF(1.0, y - 8.0, graph.left() - 6.0, 16.0),
                             Qt.AlignRight | Qt.AlignVCenter, "%d" % int(db))

        duration = max(0.02, self._duration_ms / 1000.0)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = graph.left() + graph.width() * fraction
            painter.setPen(QPen(grid, 1.0))
            painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
            painter.setPen(secondary)
            if fraction <= 0.0:
                label_rect = QRectF(graph.left(), graph.bottom() + 2.0, 56.0, 18.0)
                alignment = Qt.AlignLeft | Qt.AlignVCenter
            elif fraction >= 1.0:
                label_rect = QRectF(graph.right() - 56.0, graph.bottom() + 2.0,
                                    56.0, 18.0)
                alignment = Qt.AlignRight | Qt.AlignVCenter
            else:
                label_rect = QRectF(x - 28.0, graph.bottom() + 2.0, 56.0, 18.0)
                alignment = Qt.AlignCenter
            painter.drawText(label_rect, alignment,
                             "%.2f s" % (duration * fraction))

        if not self._points:
            painter.setPen(QColor(text.red(), text.green(), text.blue(), 170))
            painter.drawText(graph, Qt.AlignCenter, self.placeholder_text)
            return

        predelay_x = self.predelay_x()
        if predelay_x is not None:
            painter.setPen(QPen(marker_color, 1.0, Qt.DashLine))
            painter.drawLine(QPointF(predelay_x, graph.top()),
                             QPointF(predelay_x, graph.bottom()))
            painter.setPen(marker_color)
            painter.drawText(
                QRectF(predelay_x + 4.0, graph.top() + 2.0,
                       max(60.0, graph.right() - predelay_x - 4.0), 16.0),
                Qt.AlignLeft | Qt.AlignVCenter,
                _tr("预延迟") + " %g ms" % self._predelay_ms,
            )

        path = QPainterPath()
        for index, (time_ms, db) in enumerate(self._points):
            x = graph.left() + _clamp(time_ms / (duration * 1000.0), 0.0, 1.0) * graph.width()
            y = graph.top() + _clamp(abs(db) / abs(EDC_FLOOR_DB), 0.0, 1.0) * graph.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        area = QPainterPath(path)
        if self._points:
            first_x = graph.left() + _clamp(
                self._points[0][0] / (duration * 1000.0), 0.0, 1.0) * graph.width()
            last_x = graph.left() + _clamp(
                self._points[-1][0] / (duration * 1000.0), 0.0, 1.0) * graph.width()
            area.lineTo(last_x, graph.top())
            area.lineTo(first_x, graph.top())
            area.closeSubpath()
        fill = QColor(accent)
        fill.setAlpha(35)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawPath(area)
        painter.setPen(QPen(accent, 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        if self._computing:
            painter.setPen(QPen(QColor("#d7a74b"), 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(graph.adjusted(1.0, 1.0, -1.0, -1.0))


class MeterBankWidget(QWidget):
    """Per-track peak readout, live bar, hold, and per-lane transport cursors.

    The bank still coalesces through :class:`MeterEnvelope.changed`; report
    merges preserve lanes absent from a partial side, while each track, backing,
    and MIX BUS row receives a resize-safe bottom timeline cursor.
    """

    levelsChanged = pyqtSignal(object)
    laneSelected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, feed: MeterEnvelope | None = None,
                 auto_start: bool = True, peak_hold_ms: int = PEAK_HOLD_MS):
        super().__init__(parent)
        self.setObjectName("mixdownMeterBank")
        self.setAccessibleName(_tr("实时混音电平表"))
        self.setMinimumSize(340, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.feed = feed or MeterEnvelope(self, auto_start=auto_start)
        self.feed.changed.connect(self._apply_snapshot)
        self.feed.cleared.connect(self._reset_visuals)
        self._hold_ms = max(0, int(peak_hold_ms))
        self._snapshot = self.feed.snapshot()
        self._hold_db: dict[str, float] = {}
        self._hold_until: dict[str, float] = {}
        self._clip_latched: dict[str, bool] = {}
        self._last_position_ms: float = float(self._snapshot["position_ms"])
        self._position_ms: float = float(self._snapshot["position_ms"])
        self._audition_duration_ms: float | None = self._snapshot.get("duration_ms")
        self._has_report = False

    def sizeHint(self) -> QSize:
        rows = max(2, len(self._snapshot.get("lanes") or []))
        return QSize(460, 42 + rows * 42)

    @property
    def snapshot(self) -> dict[str, Any]:
        return _copy_snapshot(self._snapshot)

    @property
    def peak_hold_ms(self) -> int:
        return self._hold_ms

    @property
    def playback_position_ms(self) -> float:
        return self._position_ms

    @property
    def position_ms(self) -> float:
        """Alias used by playhead-only consumers."""
        return self._position_ms

    @property
    def position_text(self) -> str:
        duration = _finite(self._audition_duration_ms)
        if duration is None or duration <= 0.0:
            return _tr("位置") + " " + format_time_ms(self._position_ms)
        return (_tr("位置") + " " + format_time_ms(self._position_ms)
                + " / " + format_time_ms(duration))

    def playhead_ratio(self) -> float:
        """Return current audition progress in ``0..1``."""
        duration = _finite(self._audition_duration_ms)
        if duration is None or duration <= 0.0:
            return 0.0
        return _clamp(self._position_ms / duration, 0.0, 1.0)

    def playhead_x(self, rect: QRectF | None = None) -> float:
        """Return the cursor x for a widget or supplied row rectangle."""
        target = rect if isinstance(rect, QRectF) else QRectF(
            12.0, 0.0, max(1.0, self.width() - 24.0), 0.0)
        return target.left() + target.width() * self.playhead_ratio()

    def lane_playhead_x(self, rect: QRectF) -> float:
        """Return the per-lane cursor x using the same ratio as the header."""
        return self.playhead_x(rect)

    @property
    def formatted_lane_values(self) -> dict[str, str]:
        return {
            str(item.get("id")): format_dbfs(item.get("peak_dbfs"))
            for item in self._snapshot.get("lanes", [])
            if isinstance(item, Mapping)
        }

    def set_peak_hold_ms(self, value_ms: int) -> None:
        self._hold_ms = max(0, int(value_ms))
        self.update()

    def peak_hold_db(self, lane: str) -> float | None:
        key = lane_id(lane) or str(lane)
        return self._hold_db.get(key)

    def clip_latched(self, lane: str) -> bool:
        key = lane_id(lane) or str(lane)
        return bool(self._clip_latched.get(key))

    def set_report(self, report: Mapping[str, Any] | None, *, merge: bool = True) -> None:
        """Merge partial reports by default; use clear/reset for explicit drop."""
        if report is None:
            self.clear()
            return
        if not merge:
            self._reset_visuals()
        self.feed.set_report(report, merge=merge)
        self._has_report = True

    def set_position_ms(self, position_ms: int | float | None) -> None:
        self.feed.set_position_ms(position_ms)

    def clear(self) -> None:
        self.feed.clear()
        self._has_report = False

    def reset(self) -> None:
        """Alias for :meth:`clear`; explicit reset drops every merged lane."""
        self.clear()

    def mousePressEvent(self, event: Any) -> None:
        lane = self._lane_at_y(float(event.pos().y()))
        if lane:
            self.laneSelected.emit(lane)
        super().mousePressEvent(event)

    def _reset_visuals(self) -> None:
        self._snapshot = self.feed.snapshot()
        self._hold_db.clear()
        self._hold_until.clear()
        self._clip_latched.clear()
        self._position_ms = float(self._snapshot["position_ms"])
        self._audition_duration_ms = self._snapshot.get("duration_ms")
        self._last_position_ms = self._position_ms
        self.updateGeometry()
        self.update()

    def _apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping):
            return
        position = float(snapshot.get("position_ms", 0.0) or 0.0)
        self._position_ms = position
        duration = _finite(snapshot.get("duration_ms"))
        self._audition_duration_ms = duration if duration is not None and duration > 0.0 else None
        if (bool(snapshot.get("wrapped")) or bool(snapshot.get("seeked"))
                or position + 1.0 < self._last_position_ms):
            self._hold_db.clear()
            self._hold_until.clear()
            self._clip_latched.clear()
        for item in snapshot.get("lanes", []):
            if not isinstance(item, Mapping):
                continue
            lane = str(item.get("id") or "")
            if not lane:
                continue
            level = _finite(item.get("peak_dbfs"))
            if level is None:
                continue
            held = self._hold_db.get(lane)
            if held is None or level >= held:
                self._hold_db[lane] = level
                self._hold_until[lane] = position + self._hold_ms
            else:
                expired = position - self._hold_until.get(lane, position)
                if expired > 0.0:
                    held = max(level, held - PEAK_HOLD_FALL_DB_PER_SECOND * expired / 1000.0)
                    self._hold_db[lane] = held
            if level >= -1.0:
                self._clip_latched[lane] = True
        self._last_position_ms = position
        self._snapshot = _copy_snapshot(snapshot)
        self.levelsChanged.emit(self.snapshot)
        self.updateGeometry()
        self.update()

    def _lane_rows(self) -> list[Mapping[str, Any]]:
        lanes = [item for item in self._snapshot.get("lanes", []) if isinstance(item, Mapping)]
        tracks = [item for item in lanes if item.get("role") != "bus"]
        bus = next((item for item in lanes if item.get("role") == "bus"),
                   self._snapshot.get("bus"))
        return [*tracks, bus] if isinstance(bus, Mapping) else tracks

    def _row_geometry(self) -> tuple[float, list[tuple[Mapping[str, Any], QRectF]]]:
        top = 42.0
        row_height = 40.0
        bus_height = 48.0
        available = max(1.0, self.height() - top - 8.0)
        rows = self._lane_rows()
        requested = row_height * max(0, len(rows) - 1) + (bus_height if rows else 0.0)
        if requested > available and rows:
            row_height = max(27.0, (available - bus_height) / max(1, len(rows) - 1))
            bus_height = min(bus_height, max(31.0, available - row_height * max(0, len(rows) - 1)))
        geometry: list[tuple[Mapping[str, Any], QRectF]] = []
        cursor = top
        for index, row in enumerate(rows):
            height = bus_height if row.get("role") == "bus" else row_height
            geometry.append((row, QRectF(8.0, cursor, max(1.0, self.width() - 16.0), height - 3.0)))
            cursor += height
        return top, geometry

    def _lane_at_y(self, y: float) -> str | None:
        _, geometry = self._row_geometry()
        for row, rect in geometry:
            if rect.top() <= y <= rect.bottom():
                return str(row.get("id") or "") or None
        return None

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base, text, grid, secondary = _drawing_colors(self)
        width = max(1, self.width())
        height = max(1, self.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0), 8.0, 8.0)

        title_font = _widget_font(self.font(), 1.0, bold=True)
        painter.setFont(title_font)
        painter.setPen(text)
        painter.drawText(QRectF(12.0, 6.0, width - 24.0, 21.0),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         _tr("实时电平 · dBFS"))
        painter.setFont(self.font())
        painter.setPen(secondary)
        hold_text = _tr("保持 %g s") % (self._hold_ms / 1000.0)
        painter.drawText(
            QRectF(12.0, 24.0, width - 24.0, 17.0),
            Qt.AlignLeft | Qt.AlignVCenter,
            "30 Hz · %s · %s" % (self.position_text, hold_text),
        )
        if not self._has_report and not any(item.get("present") for item in self._lane_rows()):
            painter.setPen(QColor(text.red(), text.green(), text.blue(), 170))
            painter.drawText(QRectF(12.0, 48.0, width - 24.0, height - 55.0),
                             Qt.AlignCenter, _tr("暂无实时电平 · 等待试听包络"))
            return

        _, rows = self._row_geometry()
        label_width = min(120.0, max(78.0, width * 0.24))
        value_width = min(122.0, max(92.0, width * 0.25))
        meter_left = 12.0 + label_width
        meter_right = width - 22.0 - value_width
        meter_width = max(32.0, meter_right - meter_left)
        track_font = _widget_font(self.font(), -1.0)
        for row, rect in rows:
            is_bus = row.get("role") == "bus"
            row_base = QColor(text.red(), text.green(), text.blue(), 22 if is_bus else 10)
            painter.setPen(Qt.NoPen)
            painter.setBrush(row_base)
            painter.drawRoundedRect(rect, 6.0, 6.0)
            lane = str(row.get("id") or "")
            level = _finite(row.get("peak_dbfs"))
            present = bool(row.get("present")) or level is not None
            role_color = QColor("#a78dff" if not is_bus else "#f0b75a")
            if not present:
                role_color = QColor(text.red(), text.green(), text.blue(), 90)
            painter.setBrush(role_color)
            painter.drawRoundedRect(QRectF(rect.left() + 4.0, rect.top() + 5.0, 3.0,
                                           rect.height() - 10.0), 1.5, 1.5)
            painter.setFont(title_font if is_bus else track_font)
            painter.setPen(text if present else QColor(text.red(), text.green(), text.blue(), 115))
            painter.drawText(
                QRectF(rect.left() + 12.0, rect.top(),
                       label_width - 10.0, rect.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                _display_lane_label(row.get("label") or lane),
            )
            painter.setFont(track_font)
            painter.setPen(text if present else QColor(text.red(), text.green(), text.blue(), 115))
            painter.drawText(QRectF(rect.right() - 8.0 - value_width, rect.top() + 2.0,
                                    value_width, rect.height() / 2.0),
                             Qt.AlignRight | Qt.AlignVCenter, format_dbfs(level))
            gr = _finite(row.get("gr_db"))
            if gr is not None and gr < -0.05:
                painter.setPen(QColor("#ef5b72"))
                painter.drawText(QRectF(rect.right() - 8.0 - value_width, rect.top() + rect.height() / 2.0,
                                        value_width, rect.height() / 2.0 - 1.0),
                                 Qt.AlignRight | Qt.AlignVCenter, "GR %.1f" % gr)

            meter = QRectF(meter_left, rect.center().y() - 5.5, meter_width, 11.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(text.red(), text.green(), text.blue(), 24))
            painter.drawRoundedRect(meter, 5.5, 5.5)
            gradient = QLinearGradient(meter.left(), 0.0, meter.right(), 0.0)
            gradient.setColorAt(0.0, QColor("#35d0a5"))
            gradient.setColorAt(0.72, QColor("#b7d756"))
            gradient.setColorAt(0.9, QColor("#f1c75b"))
            gradient.setColorAt(1.0, QColor("#ef5b72"))
            if level is not None:
                fraction = _clamp((level - PEAK_FLOOR_DBFS) / -PEAK_FLOOR_DBFS, 0.0, 1.0)
                if fraction > 0.0:
                    fill_rect = QRectF(meter.left(), meter.top(), meter.width() * fraction, meter.height())
                    painter.save()
                    painter.setClipRect(fill_rect)
                    painter.setBrush(gradient)
                    painter.drawRoundedRect(meter, 5.5, 5.5)
                    painter.restore()
            held = self._hold_db.get(lane)
            if held is not None:
                fraction = _clamp((held - PEAK_FLOOR_DBFS) / -PEAK_FLOOR_DBFS, 0.0, 1.0)
                x = meter.left() + meter.width() * fraction
                painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 225), 1.2))
                painter.drawLine(QPointF(x, meter.top() - 1.5), QPointF(x, meter.bottom() + 1.5))
            if self._clip_latched.get(lane):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#ef5b72"))
                painter.drawEllipse(QPointF(width - 11.5, rect.center().y()), 3.5, 3.5)

            lane_rail = QRectF(meter.left(), rect.bottom() - 3.0, meter.width(), 1.5)
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 30), 1.0))
            painter.drawLine(QPointF(lane_rail.left(), lane_rail.center().y()),
                             QPointF(lane_rail.right(), lane_rail.center().y()))
            cursor_x = self.lane_playhead_x(lane_rail)
            cursor_color = QColor("#087fab") if base.lightness() >= 142 else QColor("#4bc8ff")
            painter.setPen(QPen(cursor_color, 1.5))
            painter.drawLine(QPointF(cursor_x, lane_rail.top() - 1.5),
                             QPointF(cursor_x, lane_rail.bottom() + 1.5))


__all__ = [
    "MeterEnvelope",
    "MeterBankWidget",
    "GrHistoryView",
    "ReverbDecayView",
    "lane_id",
    "format_dbfs",
    "format_time_ms",
    "METER_REFRESH_HZ",
    "METER_REFRESH_MS",
    "PEAK_FLOOR_DBFS",
    "GR_FLOOR_DB",
    "GR_AXIS_TICKS_DB",
    "GR_HISTORY_SECONDS",
    "EDC_FLOOR_DB",
]
