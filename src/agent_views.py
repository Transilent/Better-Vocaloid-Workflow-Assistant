"""Pure, bounded projections for VWA workflow and review payloads.

This module deliberately performs no I/O. It accepts values already produced by
``agent_workflow`` and returns detached JSON-compatible dictionaries. Default
views keep continuation state and review decisions visible while removing
duplicated note data, raw reports, and oversized source text. The original
mapping is never modified.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import copy
import hashlib
import json


SUMMARY_TARGET_BYTES = 16 * 1024
_VIEW_COMPONENT = "agent_views"
_MAX_TEXT = 2048
_MAX_SHORT_TEXT = 512
_MAX_ISSUES = 32
_MAX_HISTORY = 8
_DIFF_PREVIEW_LIMIT = 10
_MAX_DIFF_NOTE_IDS = 8
_MAX_DIFF_EVENT_IDS = 8
_MAX_DIFF_NOTE_CONTEXTS = 4
_DIFF_TEXT_LIMIT = 256
_RAW_KEYS = frozenset({
    "raw", "raw_evidence", "raw_report", "native_notes", "notes_raw",
    "binary", "bytes_data",
})


class ViewError(ValueError):
    """Stable error shape for projection failures at the SDK boundary."""

    component = _VIEW_COMPONENT

    def __init__(self, code, message, *, details=None, retryable=False, remediation=None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})
        self.retryable = bool(retryable)
        self.remediation = remediation or "修正读取参数后重试；需要审计证据时请显式请求 detail='full'。"


def _fail(code, message, *, details=None, remediation=None):
    raise ViewError(code, message, details=details, remediation=remediation)


def _mapping(value, label):
    if not isinstance(value, Mapping):
        _fail("invalid_view_input", f"{label} 必须是对象。",
              details={"expected": "object", "actual": type(value).__name__})
    return value


def _detail(value, allowed, default):
    if value is None:
        return default
    if not isinstance(value, str):
        _fail("invalid_detail", "detail 必须是字符串。",
              details={"allowed": list(allowed)})
    normal = value.strip().lower()
    if normal not in allowed:
        _fail("invalid_detail", "不支持该 detail 级别。",
              details={"detail": value, "allowed": list(allowed)})
    return normal


def _boolean(value, name):
    if type(value) is not bool:
        _fail("invalid_projection_flag", f"{name} 必须是布尔值。",
              details={name: type(value).__name__})
    return value


def _copy(value):
    return copy.deepcopy(value)


def projection_size(value):
    """Return the compact UTF-8 JSON size of a projection in bytes."""
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        _fail("invalid_view_value", "投影必须只包含有限的 JSON 数据。",
              details={"error": type(error).__name__})


def _digest_text(value):
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value, limit=_MAX_TEXT):
    if not isinstance(value, str):
        return _copy(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"…[已省略 {len(value) - limit} 字符；完整内容请用 detail='full']"


def _text_summary(value):
    if not isinstance(value, str):
        return {"present": value is not None, "type": type(value).__name__}
    return {
        "characters": len(value),
        "sha256": _digest_text(value),
        "preview": value[:160],
        "omitted": len(value) > 160,
    }


def _bounded(value, *, depth=0, max_depth=6, max_items=64, text_limit=1024):
    """Copy small JSON-shaped values without retaining unbounded raw arrays."""
    if depth > max_depth:
        return {"omitted": True, "reason": "depth_limit"}
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        return _text(value, text_limit)
    if isinstance(value, Mapping):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["_omitted_items"] = len(value) - max_items
                break
            if str(key) in _RAW_KEYS:
                result[f"{key}_summary"] = _generic_summary(item)
                continue
            result[str(key)] = _bounded(item, depth=depth + 1, max_depth=max_depth,
                                       max_items=max_items, text_limit=text_limit)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = list(value)
        result = [_bounded(item, depth=depth + 1, max_depth=max_depth,
                           max_items=max_items, text_limit=text_limit)
                  for item in sequence[:max_items]]
        if len(sequence) > max_items:
            result.append({"_omitted_items": len(sequence) - max_items})
        return result
    return {"omitted": True, "type": type(value).__name__}


def _generic_summary(value):
    if isinstance(value, Mapping):
        return {"type": "object", "keys": list(map(str, value.keys()))[:32],
                "key_count": len(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"type": "array", "items": len(value)}
    if isinstance(value, str):
        return _text_summary(value)
    return {"type": type(value).__name__}


def _without_note_ids(value):
    """Replace embedded note-id arrays with counts for summary payloads."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = str(key)
            if name == "note_ids" or name.endswith("_note_ids"):
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                    result[f"{name}_total"] = len(item)
                continue
            if key in _RAW_KEYS:
                continue
            result[name] = _without_note_ids(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_note_ids(item) for item in value]
    return _copy(value)


def _compact_options(options):
    if not isinstance(options, Mapping):
        return _bounded(options, max_items=32, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key, value in options.items():
        name = str(key)
        if name in {"lyrics", "backing_lyrics", "reference_lyrics"} and isinstance(value, str) and len(value) > _MAX_SHORT_TEXT:
            result[f"{name}_summary"] = _text_summary(value)
        else:
            result[name] = _bounded(value, max_items=32, text_limit=_MAX_SHORT_TEXT)
    return result


def _compact_request(request):
    if not isinstance(request, Mapping):
        return _bounded(request, max_items=32)
    result = {}
    for key, value in request.items():
        name = str(key)
        if name in {"reference_lyrics", "lyrics", "backing_lyrics"} and isinstance(value, str) and len(value) > _MAX_SHORT_TEXT:
            result[f"{name}_summary"] = _text_summary(value)
            continue
        if name == "options":
            result[name] = _compact_options(value)
        elif name == "brief":
            result[name] = _text(value, _MAX_TEXT)
        elif name == "source":
            result[name] = _text(value, 4096)
        else:
            result[name] = _bounded(value, max_items=32, text_limit=_MAX_SHORT_TEXT)
    return result


def _compact_artifact(artifact):
    if not isinstance(artifact, Mapping):
        return _bounded(artifact, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key, value in artifact.items():
        name = str(key)
        if name in _RAW_KEYS or name in {"notes", "note_ids", "content", "data"}:
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                result[f"{name}_count"] = len(value)
            elif isinstance(value, str):
                result[f"{name}_summary"] = _text_summary(value)
            continue
        result[name] = _bounded(value, max_items=24, text_limit=1024)
    return result


def _compact_artifacts(artifacts):
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        return _bounded(artifacts, max_items=16, text_limit=_MAX_SHORT_TEXT)
    return [_compact_artifact(row) for row in list(artifacts)[:16]]


def _compact_issue(row, *, include_note_ids=False, allowed=None):
    if not isinstance(row, Mapping):
        return {"code": "unknown", "message": _text(str(row), _MAX_SHORT_TEXT)}
    allowed = set(allowed or ())
    result = {}
    for key, value in row.items():
        name = str(key)
        if (name == "event_ids" or name.endswith("_event_ids")) and isinstance(
                value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            unique = _unique_strings(value)
            result[name] = _bounded_event_ids(unique)
            result[f"{name}_total"] = len(unique)
            omitted = len(unique) - len(result[name])
            if omitted:
                result[f"{name}_omitted"] = omitted
            continue
        if name in {"note_ids", "aligned_note_ids", "notes", "raw", "raw_evidence"}:
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                filtered = [ref for ref in value if isinstance(ref, str) and ref in allowed]
                if include_note_ids and name.endswith("note_ids"):
                    result[name] = list(dict.fromkeys(filtered))
                result[f"{name}_total"] = len(value)
            continue
        if name in {"message", "reason", "instructions", "expected", "actual"}:
            result[name] = _text(value, _MAX_SHORT_TEXT)
        else:
            result[name] = _bounded(value, max_items=16, text_limit=_MAX_SHORT_TEXT)
    return result


def _compact_issues(rows, limit=_MAX_ISSUES, *, include_note_ids=False, allowed=None):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    selected = list(rows) if limit is None else list(rows)[:limit]
    return [_compact_issue(row, include_note_ids=include_note_ids, allowed=allowed)
            for row in selected]


def _unique_strings(values):
    result = []
    seen = set()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return result
    for value in values:
        if isinstance(value, str) and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _bounded_note_ids(values, limit=_MAX_DIFF_NOTE_IDS):
    """Keep both ends of an oversized ID list so page boundaries stay locatable."""
    unique = _unique_strings(values)
    if len(unique) <= limit:
        return unique
    head = max(1, (limit + 1) // 2)
    tail = max(0, limit - head)
    return unique[:head] + (unique[-tail:] if tail else [])


def _bounded_event_ids(values, limit=_MAX_DIFF_EVENT_IDS):
    """Keep complete event identities while bounding an unusually large list."""
    unique = _unique_strings(values)
    if len(unique) <= limit:
        return unique
    head = max(1, (limit + 1) // 2)
    tail = max(0, limit - head)
    return unique[:head] + (unique[-tail:] if tail else [])


def _issue_note_ids(row):
    if not isinstance(row, Mapping):
        return []
    result = []
    for key, value in row.items():
        name = str(key)
        if name == "note_ids" or name.endswith("_note_ids"):
            result.extend(_unique_strings(value))
        elif name.endswith("_note_id") and isinstance(value, str):
            result.append(value)
    return _unique_strings(result)


def _note_context(row, notes_by_id):
    if not isinstance(row, Mapping):
        return []
    result = []
    for note_id in _issue_note_ids(row)[:_MAX_DIFF_NOTE_CONTEXTS]:
        note = notes_by_id.get(note_id)
        if not isinstance(note, Mapping):
            continue
        result.append({
            key: _bounded(note[key], max_items=8, text_limit=_DIFF_TEXT_LIMIT) for key in
            ("note_id", "track_index", "channel", "pitch", "onset_tick", "duration_tick",
             "onset_seconds", "duration_seconds", "end_seconds", "lyric")
            if key in note
        })
    return result


def _compact_diff_issue(row, *, allowed=None, notes_by_id=None):
    """Keep action-critical values and bounded identities for a diff preview."""
    if not isinstance(row, Mapping):
        return {"code": "unknown", "message": _text(str(row), _DIFF_TEXT_LIMIT)}
    allowed = set(allowed or ())
    notes_by_id = notes_by_id if isinstance(notes_by_id, Mapping) else {}
    result = {}
    preferred = (
        "hint_id", "code", "severity", "track_index", "channel", "tick", "onset_tick",
        "onset_seconds", "seconds", "duration_seconds", "observed_seconds", "pitch_delta",
        "expected", "actual", "lyric", "reason", "message", "event_id",
        "source_event_id", "candidate_event_id", "target_note_id",
        "alignment_before", "alignment_after",
    )
    for name in preferred:
        if name not in row:
            continue
        value = row[name]
        result[name] = _bounded(value, max_items=8, text_limit=_DIFF_TEXT_LIMIT)

    for key, value in row.items():
        name = str(key)
        if name in result or name in _RAW_KEYS:
            continue
        if name == "note_ids" or name.endswith("_note_ids"):
            unique = _unique_strings(value)
            result[name] = _bounded_note_ids(unique)
            result[f"{name}_total"] = len(unique)
            omitted = len(unique) - len(result[name])
            if omitted:
                result[f"{name}_omitted"] = omitted
            continue
        if name.endswith("_note_id") and isinstance(value, str):
            result[name] = value
            continue
        if (name == "event_ids" or name.endswith("_event_ids")) and isinstance(
                value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            unique = _unique_strings(value)
            result[name] = _bounded_event_ids(unique)
            result[f"{name}_total"] = len(unique)
            omitted = len(unique) - len(result[name])
            if omitted:
                result[f"{name}_omitted"] = omitted
            continue
        if name.endswith("_event_id") and isinstance(value, str):
            result[name] = value
            continue
        if isinstance(value, Mapping):
            result[f"{name}_summary"] = _generic_summary(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            result[f"{name}_count"] = len(value)
        else:
            result[name] = _bounded(value, max_items=8, text_limit=_DIFF_TEXT_LIMIT)

    refs = _issue_note_ids(row)
    if refs:
        linked = [ref for ref in refs if ref in allowed]
        result["linked_to_page"] = bool(linked)
        result["page_note_ref_count"] = len(linked)
        result["outside_page_note_ref_count"] = len(refs) - len(linked)
        contexts = _note_context(row, notes_by_id)
        if contexts:
            result["note_context"] = contexts
    return result


def _compact_pronunciation(value):
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=16, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("status", "reason", "evidence_available", "acoustic_verification",
                "source_sha256", "report_sha256", "note_count", "available_note_count"):
        if key in value:
            result[key] = _bounded(value[key], max_items=16, text_limit=_MAX_SHORT_TEXT)
    for key, item in value.items():
        if key not in result and (str(key).endswith("_count") or str(key).endswith("_total")):
            result[str(key)] = item
    return result


def _compact_media(value):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=12, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("included", "count", "sha256", "duration_seconds", "relative_paths",
                "project_sha256", "readback_verified", "post_publish_readback_verified",
                "source_sha256", "relative_path", "copied", "reused", "reason"):
        if key in value:
            result[key] = _copy(value[key])
    rows = value.get("tracks")
    if rows is None:
        rows = value.get("files")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
        files = []
        for row in list(rows)[:8]:
            if not isinstance(row, Mapping):
                continue
            files.append({key: _copy(row[key]) for key in
                          ("track_index", "track_name", "reference", "relative_path", "sha256",
                           "bytes", "duration_seconds", "sample_rate", "channels", "format", "verified")
                          if key in row})
        result["files"] = files
        result["file_count"] = len(rows)
    vocals = value.get("vocals")
    if isinstance(vocals, Sequence) and not isinstance(vocals, (str, bytes, bytearray)):
        result["vocals"] = [
            {key: _copy(row[key]) for key in ("track_index", "name", "notes") if key in row}
            for row in list(vocals)[:8] if isinstance(row, Mapping)
        ]
        result["vocal_count"] = len(vocals)
    return result


def compact_export_summary(summary):
    """Return loss/status evidence necessary to prepare an export_review."""
    if not isinstance(summary, Mapping):
        return _bounded(summary, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("schema", "schema_version", "kind", "operation_id", "source_sha256",
                "plan_sha256", "receipt_sha256", "report_sha256", "required_loss_codes",
                "review_required", "can_accept", "inference_performed"):
        if key in summary:
            result[key] = _copy(summary[key])
    result["instructions"] = _text(summary.get("instructions", ""), 1024)
    pitch = summary.get("continuous_pitch")
    if isinstance(pitch, Mapping):
        result["continuous_pitch"] = {key: _copy(pitch[key]) for key in
            ("status", "policy", "reason") if key in pitch}

    raw_candidates = summary.get("candidates")
    candidates = []
    if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes, bytearray)):
        for row in list(raw_candidates)[:16]:
            if not isinstance(row, Mapping):
                continue
            compact = {}
            for key in ("artifact_id", "format", "sha256", "verified_notes", "verified_lyrics",
                        "tick_roundtrip_verified", "tempo_table_equal", "time_signature_equal",
                        "loss_codes"):
                if key in row:
                    compact[key] = _copy(row[key])
            if isinstance(row.get("expected_tempo"), Sequence):
                compact["expected_tempo_count"] = len(row["expected_tempo"])
            if isinstance(row.get("output_tempo"), Sequence):
                compact["output_tempo_count"] = len(row["output_tempo"])
            compact["instrumental_included"] = row.get("instrumental_included") is True
            compact["instrumental_reason"] = _text(row.get("instrumental_reason") or "not_requested", _MAX_SHORT_TEXT)
            compact["media"] = _compact_media(row.get("media"))
            compact["pronunciation"] = _compact_pronunciation(
                row.get("pronunciation") or {"status": "unknown", "reason": "not_reported"})
            if isinstance(row.get("continuous_pitch"), Mapping):
                detail = row["continuous_pitch"]
                compact["continuous_pitch"] = {"status": detail.get("status"),
                    "readback": _bounded(detail.get("readback"), max_items=24, text_limit=_MAX_SHORT_TEXT)}
            candidates.append(compact)
    result["candidates"] = candidates

    warning_rows = summary.get("warnings", [])
    blocker_rows = summary.get("blockers", [])
    information_rows = summary.get("information", [])
    result["warnings"] = _compact_issues(warning_rows)
    result["blockers"] = _compact_issues(blocker_rows)
    result["information"] = _compact_issues(information_rows, limit=16)
    result["counts"] = {
        "candidates": len(raw_candidates) if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes, bytearray)) else 0,
        "warnings": len(warning_rows) if isinstance(warning_rows, Sequence) else 0,
        "blockers": len(blocker_rows) if isinstance(blocker_rows, Sequence) else 0,
        "information": len(information_rows) if isinstance(information_rows, Sequence) else 0,
        "verified_notes": sum(int(row.get("verified_notes", 0) or 0) for row in candidates),
        "verified_lyrics": sum(int(row.get("verified_lyrics", 0) or 0) for row in candidates),
    }

    audit = summary.get("independent_audit")
    audit_status = None
    if isinstance(audit, Mapping):
        audit_status = audit.get("status", "unknown")
        compact_audit = {}
        for key in ("schema_version", "kind", "status", "source_sha256", "audit_sha256",
                    "acoustic_or_phonetic_verified", "inference_performed"):
            if key in audit:
                compact_audit[key] = _copy(audit[key])
        compact_audit["status"] = audit_status if isinstance(audit_status, str) else "unknown"
        compact_audit["blocker_count"] = len(audit.get("blockers", [])) if isinstance(audit.get("blockers"), Sequence) else 0
        compact_audit["warning_count"] = len(audit.get("warnings", [])) if isinstance(audit.get("warnings"), Sequence) else 0
        compact_audit["candidate_count"] = len(audit.get("candidates", [])) if isinstance(audit.get("candidates"), Sequence) else 0
        result["independent_audit"] = compact_audit
    elif audit is not None:
        result["independent_audit"] = _bounded(audit, max_items=12, text_limit=_MAX_SHORT_TEXT)
    else:
        result["independent_audit"] = {"status": "unknown"}
        audit_status = "unknown"

    can_accept = summary.get("can_accept")
    if can_accept is not True:
        status = "blocked" if blocker_rows else "needs_review"
    elif audit_status == "unknown":
        status = "reviewable_with_unknown_audit"
    else:
        status = "reviewable"
    result["status"] = status
    result["export_review"] = {
        "report_sha256": summary.get("report_sha256"),
        "required_loss_codes": _copy(summary.get("required_loss_codes", [])),
        "can_accept": can_accept,
        "status": status,
    }
    return result


def _compact_review_decision(value):
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("report_sha256", "decision", "rationale", "accepted_loss_codes",
                "source_sha256", "caller_reviewed", "acoustic_validation", "reviewed_at"):
        if key in value:
            result[key] = _text(value[key], _MAX_TEXT) if key == "rationale" else _copy(value[key])
    if isinstance(value.get("candidates"), Sequence):
        result["candidates"] = [
            {key: _copy(row[key]) for key in ("artifact_id", "format", "sha256") if key in row}
            for row in value["candidates"] if isinstance(row, Mapping)
        ]
    return result


def _compact_history(history, include_raw):
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return history
    if include_raw:
        return _copy(history)
    rows = []
    for row in list(history)[-_MAX_HISTORY:]:
        if isinstance(row, Mapping):
            rows.append({key: _copy(row[key]) for key in ("action", "revision", "at", "error")
                         if key in row})
        else:
            rows.append(_bounded(row, max_items=8, text_limit=_MAX_SHORT_TEXT))
    return rows


def _compact_operation(operation):
    if not isinstance(operation, Mapping):
        return _bounded(operation, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("id", "state", "status", "progress", "cancel_requested", "created", "updated",
                "details_tool", "action", "kind"):
        if key in operation:
            result[key] = _bounded(operation[key], max_items=24, text_limit=_MAX_SHORT_TEXT)
    request = operation.get("request")
    if isinstance(request, Mapping):
        result["request"] = {"action": request.get("action")}
    error = operation.get("error")
    if error is not None:
        result["error"] = error_summary(error)
    if "result" in operation:
        result["result_summary"] = _generic_summary(operation["result"])
    return result


def _compact_references(value):
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key, item in value.items():
        name = str(key)
        if name in {"note_ids", "aligned_note_ids"}:
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                result[f"{name}_total"] = len(item)
        elif name in {"rationale", "instructions"}:
            result[name] = _text(item, _MAX_SHORT_TEXT)
        else:
            result[name] = _bounded(item, max_items=24, text_limit=1024)
    return result


def _compact_receipt(value):
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = {}
    for key in ("artifact_id", "source_sha256", "rationale", "caller_reviewed",
                "details_available", "reviewed_at", "decision"):
        if key in value:
            result[key] = _text(value[key], _MAX_SHORT_TEXT) if key == "rationale" else _copy(value[key])
    for key, item in value.items():
        if key not in result and (str(key).endswith("_count") or str(key).endswith("_total")):
            result[str(key)] = item
    return result


def _legacy_next(value):
    if not isinstance(value, Mapping):
        return _bounded(value, max_items=24, text_limit=_MAX_SHORT_TEXT)
    result = _without_note_ids(value)
    error = result.get("error") if isinstance(result, Mapping) else None
    if error is not None:
        result["error"] = error_summary(error)
    return result


def workflow_view(value, detail="summary", include_note_ids=False, include_raw_evidence=False):
    """Project a ``Workflow.get`` value without performing I/O or mutation.

    ``summary`` keeps the state needed for a client to continue a workflow,
    including artifact IDs, the exact export report hash/loss requirements, and
    the independent-audit status. ``full`` returns a detached complete copy.
    """
    raw = _mapping(value, "workflow value")
    selected = _detail(detail, {"summary", "full"}, "summary")
    _boolean(include_note_ids, "include_note_ids")
    _boolean(include_raw_evidence, "include_raw_evidence")
    if selected == "full":
        return _copy(raw)

    out = _copy(raw)
    if "request" in raw:
        out["request"] = _compact_request(raw.get("request"))
    for key in ("project_artifacts", "final_artifacts"):
        if key in raw:
            out[key] = _compact_artifacts(raw.get(key))
    for key in ("score_artifact", "project_artifact", "artifact"):
        if key in raw:
            out[key] = _compact_artifact(raw.get(key))
    for key in ("export_summary", "previous_export_summary"):
        if key in raw:
            out[key] = compact_export_summary(raw.get(key))
    if "export_review" in raw:
        out["export_review"] = _compact_review_decision(raw.get("export_review"))
    if "history" in raw:
        out["history"] = _compact_history(raw.get("history"), include_raw_evidence)
        if isinstance(raw.get("history"), Sequence) and not isinstance(raw.get("history"), (str, bytes, bytearray)):
            out["history_count"] = len(raw["history"])
    for key in ("operation", "last_operation"):
        if key in raw:
            out[key] = _compact_operation(raw.get(key))
    if "next" in raw:
        out["next"] = _legacy_next(raw.get("next"))
    if "review_receipt" in raw:
        out["review_receipt"] = (_copy(raw.get("review_receipt")) if include_raw_evidence
                                 else _compact_receipt(raw.get("review_receipt")))
    if "references" in raw:
        out["references"] = _compact_references(raw.get("references"))
    if "export_options" in raw:
        out["export_options"] = _compact_options(raw.get("export_options"))
    if "pronunciation_receipts" in raw:
        receipts = raw.get("pronunciation_receipts")
        if isinstance(receipts, Sequence) and not isinstance(receipts, (str, bytes, bytearray)):
            out["pronunciation_receipts"] = [_compact_pronunciation(row) if isinstance(row, Mapping) else _bounded(row)
                                               for row in list(receipts)[:8]]
            out["pronunciation_receipt_count"] = len(receipts)
        else:
            out["pronunciation_receipts"] = _bounded(receipts, max_items=8)
    for key in ("stage_error", "last_error"):
        if raw.get(key) is not None:
            out[key] = error_summary(raw[key])
    for key in ("notes", "note_ids"):
        if key in out and not include_note_ids:
            item = out.pop(key)
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                out[f"{key}_total"] = len(item)
    if not include_note_ids:
        out = _without_note_ids(out)
    workflow_id = raw.get("id") or raw.get("workflow_id")
    out["view"] = {
        "detail": "summary",
        "summary_target_bytes": SUMMARY_TARGET_BYTES,
        "full_entry": {"tool": "vwa_workflow_get",
                       "arguments": {"workflow_id": workflow_id, "include_details": True}},
        "raw_evidence_omitted": not include_raw_evidence,
    }
    return out


def _page_notes(value):
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping):
        return []
    score = evidence.get("score")
    if not isinstance(score, Mapping):
        return []
    notes = score.get("notes")
    if not isinstance(notes, Sequence) or isinstance(notes, (str, bytes, bytearray)):
        return []
    return [row for row in notes if isinstance(row, Mapping)]


def _page_ids_from(notes):
    result = []
    seen = set()
    for row in notes:
        note_id = row.get("note_id")
        if isinstance(note_id, str) and note_id not in seen:
            seen.add(note_id)
            result.append(note_id)
    return result


def _filter_note_id_lists(value, allowed, *, keep_ids):
    """Filter every embedded note-ID array to the current page only."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = str(key)
            if name == "note_id" and isinstance(item, str) and item not in allowed:
                result["note_id_outside_page"] = True
                continue
            if name.endswith("_note_id") and isinstance(item, str):
                if item in allowed and keep_ids:
                    result[name] = item
                else:
                    result[f"{name}_omitted"] = True
                continue
            if (name == "note_ids" or name.endswith("_note_ids")) and isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                filtered = []
                outside = 0
                seen = set()
                for ref in item:
                    if isinstance(ref, str) and ref in allowed and ref not in seen:
                        filtered.append(ref)
                        seen.add(ref)
                    elif isinstance(ref, str):
                        outside += 1
                if keep_ids:
                    result[name] = filtered
                if filtered or outside:
                    result[f"{name}_page_total"] = len(filtered)
                    if outside:
                        result[f"{name}_outside_page"] = outside
                continue
            result[name] = _filter_note_id_lists(item, allowed, keep_ids=keep_ids)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_filter_note_id_lists(item, allowed, keep_ids=keep_ids) for item in value]
    return _copy(value)


def _filter_rows_by_note(value, allowed):
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in {"notes", "unknown_notes", "ambiguous_notes"} and isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                rows = [row for row in item if not isinstance(row, Mapping) or row.get("note_id") in allowed]
                result[key] = [_copy(row) for row in rows]
            else:
                result[key] = _filter_rows_by_note(item, allowed)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_filter_rows_by_note(item, allowed) for item in value]
    return _copy(value)


def _review_counts(evidence, page_count, total_notes, next_offset):
    score = evidence.get("score") if isinstance(evidence, Mapping) else {}
    lyrics = evidence.get("lyrics") if isinstance(evidence, Mapping) else {}
    score_review = evidence.get("score_review") if isinstance(evidence, Mapping) else {}
    lyric_review = evidence.get("lyric_review") if isinstance(evidence, Mapping) else {}
    lyric_events = lyrics.get("lyric_events", []) if isinstance(lyrics, Mapping) else []
    score_hints = score_review.get("hints", []) if isinstance(score_review, Mapping) else []
    lyric_hints = lyric_review.get("hints", []) if isinstance(lyric_review, Mapping) else []
    return {
        "page_notes": page_count,
        "total_notes": total_notes,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "tracks": len(score.get("tracks", [])) if isinstance(score, Mapping) and isinstance(score.get("tracks"), Sequence) else 0,
        "lyric_events": int(lyrics.get("lyric_events_total", len(lyric_events)))
                          if isinstance(lyrics, Mapping) and isinstance(lyric_events, Sequence) else 0,
        "score_hints": int(score_review.get("hint_count", len(score_hints)))
                        if isinstance(score_review, Mapping) and isinstance(score_hints, Sequence) else 0,
        "lyric_hints": int(lyric_review.get("hint_count", len(lyric_hints)))
                        if isinstance(lyric_review, Mapping) and isinstance(lyric_hints, Sequence) else 0,
    }


def _hint_counts(rows):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return {}
    counts = Counter()
    for row in rows:
        if isinstance(row, Mapping):
            counts[str(row.get("code", "unknown"))] += 1
    return dict(sorted(counts.items()))


def _numeric_range(rows, *keys):
    values = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for key in keys:
            value = row.get(key)
            if type(value) in (int, float):
                values.append(value)
                break
    return [min(values), max(values)] if values else None


def _severity_rank(value):
    return {"error": 0, "warning": 1, "info": 2, "notice": 3}.get(
        str(value or "").strip().lower(), 4)


def _diff_rank(candidate):
    row = candidate["row"]
    refs = _issue_note_ids(row)
    code = str(row.get("code", "unknown"))
    explicit = 0 if row.get("expected") not in (None, "") or row.get("actual") not in (None, "") else 1
    actionable = 0 if any(token in code for token in (
        "mismatch", "missing", "extra", "conflict", "ambiguous", "orphan",
        "incomplete", "empty", "placeholder")) else 1
    linked = 0 if any(ref in candidate["allowed"] for ref in refs) else 1
    return (explicit, _severity_rank(row.get("severity")), actionable, linked,
            candidate["ordinal"])


def _diff_context(evidence, page_notes):
    """Return a small, current-page diff index that survives client truncation."""
    page_notes = [row for row in page_notes if isinstance(row, Mapping)]
    notes_by_id = {
        row["note_id"]: row for row in page_notes if isinstance(row.get("note_id"), str)
    }
    allowed = set(notes_by_id)
    section_stats = {}
    candidates = []
    ordinal = 0
    for section in ("lyric_review", "score_review"):
        data = evidence.get(section) if isinstance(evidence, Mapping) else None
        data = data if isinstance(data, Mapping) else {}
        rows = data.get("hints", [])
        rows = list(rows) if isinstance(rows, Sequence) and not isinstance(
            rows, (str, bytes, bytearray)) else []
        try:
            total_hints = int(data.get("hint_count", len(rows)))
        except (TypeError, ValueError):
            total_hints = len(rows)
        severity_counts = Counter(
            str(row.get("severity", "unknown")) for row in rows if isinstance(row, Mapping))
        track_indices = sorted({
            row.get("track_index") for row in rows
            if isinstance(row, Mapping) and type(row.get("track_index")) is int
        })
        linked_count = sum(
            1 for row in rows
            if isinstance(row, Mapping) and any(ref in allowed for ref in _issue_note_ids(row)))
        stats = {
            "page_hints": len(rows),
            "total_hints": total_hints,
            "severity_counts": dict(sorted(severity_counts.items())),
            "code_counts": _hint_counts(rows),
            "linked_to_page": linked_count,
            "track_indices": track_indices,
            "tick_range": _numeric_range(rows, "tick", "onset_tick"),
            "seconds_range": _numeric_range(rows, "onset_seconds", "seconds"),
        }
        section_stats[section] = stats
        for row in rows:
            if isinstance(row, Mapping):
                candidates.append({
                    "row": row, "section": section, "ordinal": ordinal, "allowed": allowed,
                })
                ordinal += 1

    ranked = sorted(candidates, key=_diff_rank)
    preview_rows = ranked[:_DIFF_PREVIEW_LIMIT]
    preview = []
    preview_section_counts = Counter()
    for candidate in preview_rows:
        row = _compact_diff_issue(candidate["row"], allowed=allowed, notes_by_id=notes_by_id)
        row["section"] = candidate["section"]
        preview.append(row)
        preview_section_counts[candidate["section"]] += 1
    for section, stats in section_stats.items():
        retained = preview_section_counts[section]
        stats["preview_returned"] = retained
        stats["preview_omitted_in_page"] = max(0, stats["page_hints"] - retained)

    return {
        "schema": "vwa-review-diff-context-v1",
        "scope": "current_page_hints",
        "selection": "expected_actual_then_severity_then_page_link",
        "preview_limit": _DIFF_PREVIEW_LIMIT,
        "page_hint_count": sum(stats["page_hints"] for stats in section_stats.values()),
        "total_hint_count": sum(stats["total_hints"] for stats in section_stats.values()),
        "preview": preview,
        "preview_omitted_in_page": max(0, len(candidates) - len(preview)),
        "sections": section_stats,
    }


def _review_continuation(raw, notes, total_notes):
    next_offset = raw.get("next_offset")
    return {
        "scope": "supplied_page_only",
        "returned_notes": len(notes),
        "total_notes": total_notes,
        "total_evidence_rows": raw.get("total_evidence_rows"),
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "next_action": "request_next_page" if next_offset is not None else "review_returned_pages",
        "review_complete": False,
        "acceptance_evidence_complete": False,
    }


def _ordered_review_projection(out, *, diff_context, continuation):
    """Put compact decision context before potentially large evidence tables."""
    ordered = {}
    for key in ("schema", "workflow_id", "revision", "artifact_id", "segment",
                "segment_note_count", "total_notes"):
        if key in out:
            ordered[key] = out[key]
    ordered["diff_context"] = diff_context
    ordered["continuation"] = continuation
    for key, value in out.items():
        if key in ordered or key in {"diff_context", "continuation"}:
            continue
        ordered[key] = value
    return ordered


def _summary_evidence(evidence):
    if not isinstance(evidence, Mapping):
        return {}
    result = {}
    for section, drop in (("score", {"notes", "tracks"}),
                          ("lyrics", {"notes", "unknown_notes", "ambiguous_notes",
                                      "lyric_events", "orphan_lyric_events",
                                      "ambiguous_lyric_events", "meta_evidence", "tracks"}),
                          ("score_review", {"hints"}),
                          ("lyric_review", {"hints"})):
        data = evidence.get(section)
        if not isinstance(data, Mapping):
            if data is not None:
                result[section] = _bounded(data, max_items=16, text_limit=_MAX_SHORT_TEXT)
            continue
        compact = {}
        for key, item in data.items():
            if key in drop or key in _RAW_KEYS:
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                    compact[f"{key}_count"] = len(item)
                continue
            if key == "tracks" and section == "score":
                compact["track_summaries"] = [
                    {field: _copy(row[field]) for field in ("track_index", "name", "instrument_name",
                                                           "channels", "note_count", "lyric_count")
                     if field in row}
                    for row in item if isinstance(row, Mapping)
                ] if isinstance(item, Sequence) else []
            elif key == "timing" and isinstance(item, Mapping):
                compact[key] = {}
                for field, val in item.items():
                    if field == "tempo_events" and isinstance(val, Sequence):
                        compact[key]["tempo_event_count"] = len(val)
                    else:
                        compact[key][field] = _bounded(val, max_items=16, text_limit=_MAX_SHORT_TEXT)
            else:
                compact[key] = _bounded(item, max_items=16, text_limit=_MAX_SHORT_TEXT)
        if section in {"score_review", "lyric_review"}:
            hints = data.get("hints", [])
            compact["hint_code_counts"] = _hint_counts(hints)
            compact["hint_code_counts_in_page"] = _hint_counts(hints)
            compact["hints_in_page"] = len(hints) if isinstance(
                hints, Sequence) and not isinstance(hints, (str, bytes, bytearray)) else 0
        result[section] = compact
    return result


def review_view(value, detail="notes", include_note_ids=False, include_raw_evidence=False):
    """Project an already-paged ``Workflow.review`` bundle.

    Evidence note-ID arrays are restricted to IDs present in the supplied page.
    The explicitly scoped diff preview retains a bounded first/last sample of
    relevant IDs so an offset boundary does not hide the decisive note. Summary
    mode removes the note table; neither summary nor notes mode claims that the
    supplied page, or a final page, proves the whole review was accepted.
    """
    raw = _mapping(value, "review bundle")
    selected = _detail(detail, {"summary", "notes", "full"}, "notes")
    _boolean(include_note_ids, "include_note_ids")
    _boolean(include_raw_evidence, "include_raw_evidence")

    notes = _page_notes(raw)
    page_ids = _page_ids_from(notes)
    allowed = set(page_ids)
    total_notes = raw.get("total_notes", len(notes))
    try:
        total_notes = int(total_notes)
    except (TypeError, ValueError):
        total_notes = len(notes)
    next_offset = raw.get("next_offset")
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else {}

    if selected == "full":
        out = _filter_rows_by_note(raw, allowed)
        return _filter_note_id_lists(out, allowed, keep_ids=True)

    diff_context = _diff_context(evidence, notes)
    continuation = _review_continuation(raw, notes, total_notes)
    out = _copy(raw)
    if selected == "summary":
        compact_evidence = _summary_evidence(evidence)
        out["evidence"] = compact_evidence
        out["note_page"] = {
            "returned": 0,
            "available_in_page": len(notes),
            "total": total_notes,
            "next_offset": next_offset,
            "has_more": next_offset is not None,
        }
        out["counts"] = _review_counts(evidence, len(notes), total_notes, next_offset)
        out["has_more"] = next_offset is not None
        out["review_complete"] = False
        out["instructions"] = (
            "摘要和差异预览仅用于定位；验收音符、歌词并提交修改前，请逐页请求 detail='notes'（必要时用 full）。"
        )
        out.pop("reference_evidence", None)
        if isinstance(out.get("references"), Mapping):
            out["references"] = _compact_references(out["references"])
        out["view"] = {"detail": "summary", "contains_note_page": False,
                       "current_page_only": True,
                       "review_complete": False,
                       "acceptance_evidence_complete": False,
                       "summary_target_bytes": SUMMARY_TARGET_BYTES}
        filtered = _filter_note_id_lists(out, set(), keep_ids=False)
        return _ordered_review_projection(
            filtered, diff_context=diff_context, continuation=continuation)

    score = out.get("evidence", {}).get("score") if isinstance(out.get("evidence"), Mapping) else None
    lyrics = out.get("evidence", {}).get("lyrics") if isinstance(out.get("evidence"), Mapping) else None
    score_notes = [_copy(row) for row in notes]
    lyric_by_id = {}
    if isinstance(lyrics, Mapping) and isinstance(lyrics.get("notes"), Sequence):
        lyric_by_id = {row.get("note_id"): row for row in lyrics["notes"]
                       if isinstance(row, Mapping) and row.get("note_id") in allowed}
    for row in score_notes:
        lyric = lyric_by_id.get(row.get("note_id"))
        if isinstance(lyric, Mapping):
            for key in ("lyric_alignment", "lyric_event_id",
                        "competing_lyric_event_indexes", "competing_lyric_event_ids"):
                if (key in {"competing_lyric_event_indexes", "competing_lyric_event_ids"}
                        and lyric.get('lyric_alignment') == 'aligned'):
                    continue
                if key in lyric:
                    row.setdefault(key, _copy(lyric[key]))
    if isinstance(score, Mapping):
        compact_score = _copy(score)
        compact_score["notes"] = score_notes
        tracks = compact_score.get("tracks")
        if isinstance(tracks, Sequence):
            compact_tracks = []
            for track in tracks:
                if not isinstance(track, Mapping):
                    compact_tracks.append(_bounded(track, max_items=12))
                    continue
                row = {key: _copy(item) for key, item in track.items()
                       if key not in {"note_ids", "lyric_events"} and key not in _RAW_KEYS}
                if include_note_ids and "note_ids" in track:
                    row["note_ids"] = [ref for ref in track.get("note_ids", []) if ref in allowed]
                    row["note_id_total"] = len(row["note_ids"])
                row["lyric_events_count"] = len(track.get("lyric_events", [])) if isinstance(track.get("lyric_events"), Sequence) else 0
                compact_tracks.append(row)
            compact_score["tracks"] = compact_tracks
        out["evidence"]["score"] = compact_score
    if isinstance(lyrics, Mapping):
        compact_lyrics = _copy({key: item for key, item in lyrics.items()
                                if key not in {"notes", "meta_evidence"} and key not in _RAW_KEYS})
        if include_raw_evidence and "meta_evidence" in lyrics:
            compact_lyrics["meta_evidence"] = _copy(lyrics["meta_evidence"])
        lyric_tracks = lyrics.get("tracks")
        if isinstance(lyric_tracks, Sequence) and not isinstance(lyric_tracks, (str, bytes, bytearray)):
            compact_lyrics["tracks"] = []
            for track in lyric_tracks:
                if not isinstance(track, Mapping):
                    compact_lyrics["tracks"].append(_bounded(track, max_items=12))
                    continue
                row = {key: _copy(track[key]) for key in
                       ("track_index", "name", "instrument_name", "channels", "note_count",
                        "lyric_count", "alignment")
                       if key in track}
                row["note_ids_total"] = len(track.get("note_ids", [])) if isinstance(track.get("note_ids"), Sequence) else 0
                row["lyric_events_count"] = len(track.get("lyric_events", [])) if isinstance(track.get("lyric_events"), Sequence) else 0
                compact_lyrics["tracks"].append(row)
        compact_lyrics["notes"] = [
            {key: _copy(row[key]) for key in (
                ("note_id", "lyric_alignment", "lyric_event_id")
                if row.get('lyric_alignment') == 'aligned'
                else ("note_id", "track_index", "lyric", "lyric_alignment",
                      "lyric_event_id", "competing_lyric_event_indexes",
                      "competing_lyric_event_ids", "pairing_ambiguous")
            )
             if key in row}
            for row in lyric_by_id.values()
        ]
        if not include_raw_evidence:
            compact_lyrics['lyric_events']=[{key:_copy(row[key]) for key in
                ('event_id','track_index','event_index','tick','seconds',
                 'text','alignment','note_ids') if key in row}
                for row in compact_lyrics.get('lyric_events',[])]
        out["evidence"]["lyrics"] = compact_lyrics
    for section in ("score_review", "lyric_review"):
        item = out.get("evidence", {}).get(section) if isinstance(out.get("evidence"), Mapping) else None
        if isinstance(item, Mapping):
            compact = _copy(item)
            hint_rows = item.get("hints", [])
            hint_rows = list(hint_rows) if isinstance(hint_rows, Sequence) and not isinstance(
                hint_rows, (str, bytes, bytearray)) else []
            compact["hints"] = _compact_issues(
                hint_rows, limit=len(hint_rows), include_note_ids=include_note_ids,
                allowed=allowed)
            compact["hint_code_counts"] = _hint_counts(hint_rows)
            compact["hint_code_counts_in_page"] = _hint_counts(hint_rows)
            compact["hints_returned"] = len(hint_rows)
            compact["hints_in_page"] = len(hint_rows)
            try:
                compact["hints_total"] = int(item.get("hint_count", len(hint_rows)))
            except (TypeError, ValueError):
                compact["hints_total"] = len(hint_rows)
            compact["hints_omitted_in_page"] = 0
            out["evidence"][section] = compact
    if not include_raw_evidence:
        ev = out.get("evidence")
        if isinstance(ev, Mapping):
            for data_key in ("score", "lyrics"):
                data = ev.get(data_key)
                if isinstance(data, Mapping):
                    for key in ("meta_evidence", "raw", "raw_evidence"):
                        data.pop(key, None)
        if "reference_evidence" in out:
            out["reference_evidence"] = _bounded(
                out["reference_evidence"], max_depth=8, max_items=32, text_limit=512)
    if isinstance(out.get("references"), Mapping):
        out["references"] = _compact_references(out["references"])
    out["has_more"] = next_offset is not None
    out["note_page"] = {"returned": len(score_notes), "total": total_notes,
                        "next_offset": next_offset, "has_more": next_offset is not None}
    out["counts"] = _review_counts(evidence, len(score_notes), total_notes, next_offset)
    out["review_complete"] = False
    out["view"] = {"detail": "notes", "contains_note_page": True,
                   "current_page_only": True,
                   "review_complete": False,
                   "acceptance_evidence_complete": False,
                   "include_note_ids": include_note_ids,
                   "include_raw_evidence": include_raw_evidence,
                   "summary_target_bytes": SUMMARY_TARGET_BYTES}
    out = _filter_rows_by_note(out, allowed)
    filtered = _filter_note_id_lists(out, allowed, keep_ids=include_note_ids)
    return _ordered_review_projection(
        filtered, diff_context=diff_context, continuation=continuation)


_REMEDIATION = {
    "workflow_conflict": "重新读取 workflow_get，并使用最新 revision 重试。",
    "stale_artifact": "重新读取当前 artifact 与 workflow revision，再提交操作。",
    "wrong_stage": "按返回的 next.tool/arguments 推进到正确阶段。",
    "export_blockers": "按 blockers 修正工程或歌词；不要认可阻断差异。",
    "export_losses_unacknowledged": "逐项核对并在 accepted_loss_codes 中认可必需损失。",
    "invalid_detail": "detail 仅使用接口允许的 summary/notes/full。",
    "invalid_page": "limit 与 offset 使用接口允许范围内的整数。",
}


def error_summary(error):
    """Return a compact, JSON-compatible error envelope for MCP/worker replies."""
    if isinstance(error, Mapping):
        data = error
        code = data.get("code", "business_error")
        message = data.get("message", "")
        details = data.get("details", {})
        component = data.get("component") or "vwa"
        retryable = data.get("retryable", False)
        remediation = data.get("remediation")
    else:
        code = getattr(error, "code", "business_error")
        message = str(error)
        details = getattr(error, "details", {})
        component = getattr(error, "component", None) or "vwa"
        retryable = getattr(error, "retryable", False)
        remediation = getattr(error, "remediation", None)
    code = code if isinstance(code, str) and code else "business_error"
    if code in _REMEDIATION:
        remediation = _REMEDIATION[code]
    result = {
        "code": code,
        "message": _text(message if isinstance(message, str) else str(message), 2048),
        "component": component if isinstance(component, str) and component else "vwa",
        "retryable": bool(retryable),
        "remediation": (_text(remediation, 1024) if remediation else
                        _REMEDIATION.get(code, "修正请求后重试；冲突时先读取最新状态。")),
    }
    if details not in (None, {}, []):
        result["details"] = _bounded(details, max_items=16, text_limit=512)
    return result


def _append_suggestion(items, seen, source, value, default_code="suggestion"):
    if len(items) >= 32:
        return
    if isinstance(value, Mapping):
        code = value.get("code") or default_code
        message = value.get("message") or value.get("reason") or value.get("remediation") or value.get("detail")
        row = {"source": source, "code": str(code), "message": _text(str(message or code), 1024)}
        for key in ("severity", "tool", "action", "field", "format", "count"):
            if key in value:
                row[key] = _bounded(value[key], max_items=8, text_limit=256)
    elif isinstance(value, str):
        row = {"source": source, "code": default_code, "message": _text(value, 1024)}
    else:
        row = {"source": source, "code": default_code, "message": _text(str(value), 1024)}
    key = (row["source"], row["code"], row["message"])
    if key not in seen:
        seen.add(key)
        items.append(row)


def suggestion_summary(value):
    """Extract bounded errors and actionable suggestions from a result/error."""
    if not isinstance(value, Mapping):
        return {"errors": [error_summary(value)], "suggestions": []}
    data = value
    errors = []
    suggestions = []
    seen = set()
    for key in ("error", "stage_error", "last_error"):
        if key in data and data[key] is not None:
            errors.append(error_summary(data[key]))
    operation = data.get("operation")
    if isinstance(operation, Mapping) and operation.get("error") is not None:
        errors.append(error_summary(operation["error"]))
    next_value = data.get("next")
    if isinstance(next_value, Mapping) and (next_value.get("tool") or next_value.get("reason")):
        _append_suggestion(suggestions, seen, "next", next_value, "next_step")
    for key in ("suggestions", "recommendations", "advice", "remediation"):
        rows = data.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            for row in rows:
                _append_suggestion(suggestions, seen, key, row)
        elif rows:
            _append_suggestion(suggestions, seen, key, rows)
    export_summary = data.get("export_summary")
    if isinstance(export_summary, Mapping):
        for key in ("blockers", "warnings"):
            rows = export_summary.get(key, [])
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
                for row in rows:
                    _append_suggestion(suggestions, seen, f"export_summary.{key}", row)
        if export_summary.get("can_accept") is False:
            _append_suggestion(suggestions, seen, "export_summary", {
                "code": "resolve_export_blockers",
                "message": "先处理阻断差异，再重新导出和核对。",
            }, "resolve_export_blockers")
    return {
        "id": data.get("id") or data.get("workflow_id"),
        "revision": data.get("revision"),
        "stage": data.get("stage"),
        "errors": errors[:8],
        "suggestions": suggestions[:16],
        "counts": {"errors": len(errors), "suggestions": len(suggestions)},
    }


# Descriptive alias for callers that use "advice" rather than "suggestion".
advice_summary = suggestion_summary


__all__ = [
    "SUMMARY_TARGET_BYTES",
    "ViewError",
    "advice_summary",
    "compact_export_summary",
    "error_summary",
    "projection_size",
    "review_view",
    "suggestion_summary",
    "workflow_view",
]
