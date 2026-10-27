"""Recover a failed dual-vocal separation from intact, reusable raw stems.

The backend is deliberately independent of model execution. A failed
separation stage is never accepted as a cache entry; only the verified
metadata/download/audio prefix is reused. The three raw vocal stems are copied
into a new managed job, validated, and repaired through ``vocal_partition``.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time

import agent_preparation
import common
import pipeline

SCHEMA_VERSION = 1
PLAN_KIND = "separation_repair"
RECEIPT_KIND = "separation_repair_receipt"
SOURCE_EVIDENCE_KIND = "separation_repair_source"
REPAIR_RECEIPT_NAME = "separation-repair.json"
SOURCE_EVIDENCE_NAME = "separation-repair-source.json"
REPAIR_REPORT_NAME = "separation-report.json"
STEM_ROLES = ("vocals", "lead", "backing")
SUPPORTING_ROLE = "instrumental"
AUDIO_ROLES = STEM_ROLES + (SUPPORTING_ROLE,)
AUDIO_RELATIVE = {role: f"audio/{role}.wav" for role in AUDIO_ROLES}
REUSE_STAGES = ("metadata", "download", "audio")
SAMPLE_RATE = 44100
CHANNELS = 2
AUDIO_SUBTYPE = "FLOAT"
COMPLEMENTARITY_TOLERANCE = 1e-4
COPY_BLOCK = 1024 * 1024
SCAN_BLOCK = 262144
SHA256_RE = re.compile(r"[a-f0-9]{64}")
JSON_LIMIT = 16 * 1024 * 1024

_STEM_REMEDIATION = ["vwa_preparation_plan", "vwa_preparation_start"]


class SeparationRepairError(RuntimeError):
    """Machine-readable separation recovery failure."""

    component = "agent_separation_repair"

    def __init__(self, code, message, *, retryable=False, remediation=None, **details):
        super().__init__(message)
        self.code = code
        self.retryable = bool(retryable)
        self.remediation = list(_STEM_REMEDIATION if remediation is None else remediation)
        self.details = details

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "component": self.component,
            "retryable": self.retryable,
            "remediation": list(self.remediation),
            "details": copy.deepcopy(self.details),
        }


def _digest(value):
    return agent_preparation._digest(value)


def _json_bytes(value, *, pretty=False):
    if pretty:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                          allow_nan=False)
    else:
        text = agent_preparation._canonical_bytes(value).decode("utf-8")
    return text.encode("utf-8")


def _bounded(value, limit=800):
    if not isinstance(value, str):
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _suggestions(kind):
    if kind == "rebuild":
        return [
            {"action": "rerun_separation", "instruction": "重新运行模型分离，生成同一时间轴上的新 vocals/lead/backing。"},
            {"action": "new_single_job", "tool": "vwa_preparation_plan",
             "instruction": "若不需要拆主唱与和声，在新准备计划中显式选择 voice_mode=single。"},
        ]
    if kind == "stale":
        return [
            {"action": "regenerate_plan", "tool": "vwa_preparation_repair_plan",
             "instruction": "源任务或音频已经变化，请重新生成修复计划。"},
            {"action": "rerun_separation", "instruction": "若源仍不可采样核验，请重新运行分离或改用 single。"},
        ]
    return []


def _evidence_details(kind):
    return {"action_suggestions": _suggestions(kind)}


def _relative(value):
    name = str(value).replace("\\", "/")
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts or ":" in name
            or name in ("", ".")):
        raise SeparationRepairError("unsafe_path", "修复路径必须是任务内的普通相对路径。",
                                    path=name, **_evidence_details("rebuild"))
    return relative.as_posix()


def _job_file(job, relative, *, leaf_file=True):
    relative = _relative(relative)
    path = Path(job) / PurePosixPath(relative)
    agent_preparation._assert_real_path(
        path, boundary=Path(job), leaf_file=True if leaf_file else None)
    if leaf_file and not Path(common.native_path(path)).is_file():
        raise SeparationRepairError(
            "rawstem_missing", f"缺少声部修复输入：{relative}。", role=Path(relative).stem,
            path=str(path),
            **_evidence_details("rebuild"))
    return relative, path


def _cancel_requested(cancel):
    return agent_preparation._cancel_requested(cancel)


def _check_cancel(cancel):
    if _cancel_requested(cancel):
        raise InterruptedError("分离声部修复已取消。")


def _stable_hash(path):
    path = Path(path)
    before = agent_preparation._lstat(path)
    digest = agent_preparation._hash_file(path)
    after = agent_preparation._lstat(path)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SeparationRepairError(
            "separation_source_changed", "音频在读取期间发生变化，请重新生成修复计划。",
            path=str(path),
            **_evidence_details("stale"))
    return digest, int(before.st_size)


def _audio_row(job, role):
    relative, path = _job_file(job, AUDIO_RELATIVE[role])
    try:
        import soundfile as sf
        digest, size = _stable_hash(path)
        info = sf.info(str(common.native_path(path)))
    except SeparationRepairError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise SeparationRepairError(
            "rawstem_unreadable", f"无法读取声部文件 {role}.wav：{_bounded(str(exc))}",
            role=role, path=str(path),
            **_evidence_details("rebuild")) from exc
    return {
        "path": relative,
        "sha256": digest,
        "bytes": size,
        "format": str(info.format),
        "subtype": str(info.subtype),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
    }

def _validate_rows(job, rows):
    invalid = []
    for role in AUDIO_ROLES:
        row = rows.get(role)
        if not isinstance(row, dict):
            invalid.append({"role": role, "reason": "missing_identity"})
            continue
        if (row.get("format") != "WAV" or row.get("subtype") != AUDIO_SUBTYPE
                or row.get("sample_rate") != SAMPLE_RATE or row.get("channels") != CHANNELS
                or not isinstance(row.get("frames"), int) or row["frames"] <= 0):
            invalid.append({
                "role": role,
                "reason": "format",
                "expected": {"format": "WAV", "subtype": AUDIO_SUBTYPE,
                             "sample_rate": SAMPLE_RATE, "channels": CHANNELS},
                "actual": {key: row.get(key) for key in
                           ("format", "subtype", "sample_rate", "channels", "frames")},
            })
    lengths = {role: rows.get(role, {}).get("frames") for role in AUDIO_ROLES}
    if len(set(lengths.values())) != 1:
        invalid.append({"reason": "length_mismatch", "frames": lengths})
    source_path = Path(job) / "audio/source.wav"
    if Path(common.native_path(source_path)).is_file():
        try:
            import soundfile as sf
            source_frames = int(sf.info(str(common.native_path(source_path))).frames)
        except (OSError, RuntimeError, ValueError) as exc:
            invalid.append({"reason": "source_unreadable", "message": _bounded(str(exc))})
        else:
            if source_frames != lengths.get("vocals"):
                invalid.append({"reason": "source_length_mismatch",
                                "source_frames": source_frames,
                                "stem_frames": lengths.get("vocals")})
    if invalid:
        raise SeparationRepairError(
            "rawstem_format_invalid", "vocals、lead、backing、instrumental 的格式或长度不一致。",
            validation=invalid,
            **_evidence_details("rebuild"))


def _validate_audio_content(job, rows, *, cancel=None):
    """Scan every sample without substituting or normalizing bad values."""
    import numpy as np
    import soundfile as sf

    _validate_rows(job, rows)
    _check_cancel(cancel)
    max_error = 0.0
    max_location = None
    over_threshold = 0
    scanned = 0
    with ExitStack() as stack:
        handles = {}
        for role in AUDIO_ROLES:
            _, path = _job_file(job, AUDIO_RELATIVE[role])
            handles[role] = stack.enter_context(sf.SoundFile(str(common.native_path(path))))
        while True:
            _check_cancel(cancel)
            blocks = {
                role: handles[role].read(SCAN_BLOCK, dtype="float32", always_2d=True)
                for role in AUDIO_ROLES
            }
            count = len(blocks["vocals"])
            if count == 0:
                break
            if any(len(block) != count for block in blocks.values()):
                raise SeparationRepairError(
                    "rawstem_length_changed", "声部文件在扫描时出现长度不一致。",
                    offsets={role: int(handles[role].tell()) for role in AUDIO_ROLES},
                    **_evidence_details("rebuild"))
            for role, block in blocks.items():
                bad = ~np.isfinite(block)
                if not bad.any():
                    continue
                positions = np.argwhere(bad)
                first = positions[0]
                last = positions[-1]
                raise SeparationRepairError(
                    "rawstem_nonfinite",
                    f"{role}.wav 含 NaN/Inf；已停止，禁止用 nan_to_num 掩盖真实坏数据。",
                    role=role, file=AUDIO_RELATIVE[role],
                    sample_range=[int(scanned + first[0]), int(scanned + last[0] + 1)],
                    channels=sorted({int(row[1]) for row in positions}),
                    nonfinite_count=int(bad.sum()), first_offset=int(scanned),
                    **_evidence_details("rebuild"))
            vocals = blocks["vocals"].astype(np.float64, copy=False)
            lead = blocks["lead"].astype(np.float64, copy=False)
            backing = blocks["backing"].astype(np.float64, copy=False)
            delta = np.abs(lead + backing - vocals)
            local_error = float(np.max(delta))
            if local_error > max_error:
                max_error = local_error
                channel, sample = (int(value) for value in np.unravel_index(
                    np.argmax(delta), delta.shape))
                max_location = {"sample": int(scanned + sample),
                                "channel": channel, "max_abs_error": local_error}
            over_threshold += int((delta > COMPLEMENTARITY_TOLERANCE).sum())
            scanned += count
    if max_error > COMPLEMENTARITY_TOLERANCE:
        raise SeparationRepairError(
            "rawstem_non_complementary",
            "lead + backing 无法还原 vocals，现有文件不是可修复的互补声部。",
            tolerance=COMPLEMENTARITY_TOLERANCE, max_abs_error=max_error,
            samples_over_threshold=over_threshold, location=max_location,
            files=[AUDIO_RELATIVE[role] for role in STEM_ROLES],
            **_evidence_details("rebuild"))
    return {
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "frames": scanned,
        "subtype": AUDIO_SUBTYPE,
        "finite": True,
        "complementary": True,
        "complementarity_tolerance": COMPLEMENTARITY_TOLERANCE,
        "max_complementarity_error": max_error,
        "samples_over_threshold": over_threshold,
    }


def _source_records(source_job):
    request = agent_preparation._read_json(source_job / "request.json", required=True)
    status = agent_preparation._read_json(source_job / "status.json", required=True)
    mode = request.get("voice_mode")
    if mode not in ("dual", "roformer"):
        raise SeparationRepairError(
            "separation_mode_unsupported",
            "声部修复要求源任务是 dual 或 roformer 双声部模式。",
            voice_mode=mode,
            **_evidence_details("rebuild"))
    steps = status.get("steps") if isinstance(status.get("steps"), dict) else {}
    separation = steps.get("separation") if isinstance(steps.get("separation"), dict) else {}
    if status.get("state") == "running" or separation.get("state") == "running":
        raise SeparationRepairError(
            "separation_source_busy", "源任务仍在运行，不能读取半成品声部。",
            source_job=str(source_job), retryable=True,
            remediation=["vwa_jobs_get", "vwa_preparation_repair_plan"],
            **_evidence_details("stale"))
    if separation.get("state") == "done":
        raise SeparationRepairError(
            "separation_already_complete",
            "源 separation 已登记完成；这不是失败恢复目标，请先检查产物而不是伪装成缓存。",
            source_job=str(source_job), remediation=["vwa_jobs_get", "vwa_artifacts_inspect"],
            **_evidence_details("stale"))
    if separation.get("state") not in ("failed", "cancelled"):
        raise SeparationRepairError(
            "separation_not_recoverable",
            "源 separation 不是 failed/cancelled，无法建立可审计恢复计划。",
            source_job=str(source_job), separation_state=separation.get("state"),
            retryable=True, remediation=["vwa_jobs_get"],
            **_evidence_details("stale"))
    return request, status


def _source_snapshot(source_job):
    request, status = _source_records(source_job)
    request_sha = agent_preparation._hash_file(source_job / "request.json")
    status_sha = agent_preparation._hash_file(source_job / "status.json")
    rows = {role: _audio_row(source_job, role) for role in AUDIO_ROLES}
    validation = _validate_audio_content(source_job, rows, cancel=None)
    if agent_preparation._hash_file(source_job / "request.json") != request_sha:
        raise SeparationRepairError(
            "separation_source_changed", "源 request.json 在规划期间发生变化。",
            source_job=str(source_job),
            **_evidence_details("stale"))
    if agent_preparation._hash_file(source_job / "status.json") != status_sha:
        raise SeparationRepairError(
            "separation_source_changed", "源 status.json 在规划期间发生变化。",
            source_job=str(source_job),
            **_evidence_details("stale"))
    separation = status["steps"]["separation"]
    failure = {
        "state": separation.get("state"),
        "message": _bounded(separation.get("error") or status.get("error") or ""),
    }
    details = separation.get("error_details") or status.get("error_details")
    if isinstance(details, dict) and isinstance(details.get("code"), str):
        failure["error_code"] = _bounded(details["code"], 120)
    return request, status, request_sha, status_sha, rows, validation, failure


def _reuse_plan(source_job):
    plan = agent_preparation.plan_preparation_reuse(
        source_job, options=None, reuse_stages=["audio"])
    reuse = plan.get("reuse") if isinstance(plan, dict) else None
    if not isinstance(reuse, dict):
        raise SeparationRepairError(
            "reuse_plan_invalid", "准备复用计划缺少阶段证据。", source_job=str(source_job),
            remediation=["vwa_preparation_reuse_plan"], **_evidence_details("stale"))
    expected = list(REUSE_STAGES)
    if (reuse.get("requested_stages") != ["audio"]
            or reuse.get("reuse_stages") != expected
            or reuse.get("run_stages") != ["separation", "midi"]):
        raise SeparationRepairError(
            "separation_prefix_incomplete",
            "metadata/download/audio 未全部通过真实回执验证；不能只复制半套任务。",
            source_job=str(source_job), reuse=reuse,
            **_evidence_details("stale"))
    return plan


def plan_separation_repair(source_job) -> dict:
    """Build a deterministic, read-only plan for repairing existing raw stems."""
    source_job = agent_preparation._job_path(source_job)
    request, status, request_sha, status_sha, rows, validation, failure = _source_snapshot(source_job)
    reuse = _reuse_plan(source_job)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "source_job": str(source_job),
        "source_request_sha256": request_sha,
        "source_status_sha256": status_sha,
        "mode": request["voice_mode"],
        "source_failure": failure,
        "audio": {role: copy.deepcopy(rows[role]) for role in STEM_ROLES},
        "supporting_audio": {SUPPORTING_ROLE: copy.deepcopy(rows[SUPPORTING_ROLE])},
        "validation": validation,
        "reuse_plan": reuse,
        "model_execution": False,
        "planned_action": "copy_rawstems_and_run_vocal_partition_repair",
    }
    if plan["reuse_plan"].get("source_job") != str(source_job):
        raise SeparationRepairError(
            "reuse_plan_invalid", "复用计划的源任务与修复计划不一致。",
            source_job=str(source_job), remediation=["vwa_preparation_reuse_plan"],
            **_evidence_details("stale"))
    plan["plan_sha256"] = _digest(plan)
    json.dumps(plan, ensure_ascii=False, allow_nan=False)
    return plan

def _validate_plan_shape(plan):
    if type(plan) is not dict:
        raise ValueError("分离修复计划必须是 JSON 对象。")
    supplied = plan.get("plan_sha256")
    if not isinstance(supplied, str) or not SHA256_RE.fullmatch(supplied):
        raise SeparationRepairError(
            "separation_repair_plan_tampered", "修复计划缺少规范 plan_sha256。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    unsigned = {key: copy.deepcopy(value) for key, value in plan.items()
                if key != "plan_sha256"}
    if _digest(unsigned) != supplied:
        raise SeparationRepairError(
            "separation_repair_plan_tampered", "修复计划摘要不匹配，拒绝执行。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    if plan.get("schema_version") != SCHEMA_VERSION or plan.get("kind") != PLAN_KIND:
        raise SeparationRepairError(
            "separation_repair_plan_invalid", "不支持的分离修复计划版本。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    source_job = plan.get("source_job")
    if not isinstance(source_job, str) or not Path(source_job).is_absolute():
        raise SeparationRepairError(
            "separation_repair_plan_invalid", "修复计划必须保存绝对 source_job。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    if plan.get("model_execution") is not False:
        raise SeparationRepairError(
            "separation_repair_plan_invalid", "修复计划必须声明 model_execution=false。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    audio = plan.get("audio")
    supporting = plan.get("supporting_audio")
    if (not isinstance(audio, dict) or set(audio) != set(STEM_ROLES)
            or not isinstance(supporting, dict) or set(supporting) != {SUPPORTING_ROLE}):
        raise SeparationRepairError(
            "separation_repair_plan_invalid", "修复计划的三轨身份不完整。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    for role in AUDIO_ROLES:
        row = audio.get(role) or supporting.get(role)
        if (not isinstance(row, dict) or row.get("path") != AUDIO_RELATIVE[role]
                or not isinstance(row.get("sha256"), str)
                or not SHA256_RE.fullmatch(row["sha256"])):
            raise SeparationRepairError(
                "separation_repair_plan_invalid", "修复计划的音频身份无效。",
                role=role, remediation=["vwa_preparation_repair_plan"],
                **_evidence_details("stale"))
    if not isinstance(plan.get("reuse_plan"), dict):
        raise SeparationRepairError(
            "separation_repair_plan_invalid", "修复计划缺少准备复用计划。",
            remediation=["vwa_preparation_repair_plan"], **_evidence_details("stale"))
    return plan


def _assert_source_matches_plan(plan):
    source_job = agent_preparation._job_path(plan["source_job"])
    current = plan_separation_repair(source_job)
    if current.get("plan_sha256") != plan.get("plan_sha256"):
        raise SeparationRepairError(
            "separation_repair_plan_stale",
            "源任务、失败状态、音频摘要或复用依赖已变化；请重新生成修复计划。",
            source_job=str(source_job),
            remediation=["vwa_preparation_repair_plan", "vwa_jobs_get"],
            **_evidence_details("stale"))
    return source_job, current


def _publish_exclusive_bytes(path, data):
    path = Path(path)
    parent = path.parent
    Path(common.native_path(parent)).mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(common.native_path(parent)))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(str(common.native_path(temporary)), str(common.native_path(path)))
            return
        except FileExistsError:
            return
        except OSError as link_error:
            if os.name != "nt":
                raise SeparationRepairError(
                    "repair_publish_failed", f"无法原子发布修复记录：{link_error}",
                    path=str(path), **_evidence_details("stale")) from link_error
            if os.path.lexists(common.native_path(path)):
                return
        try:
            os.rename(str(common.native_path(temporary)), str(common.native_path(path)))
        except FileExistsError:
            return
        except OSError as rename_error:
            raise SeparationRepairError(
                "repair_publish_failed", f"无法发布修复记录：{rename_error}",
                path=str(path), **_evidence_details("stale")) from rename_error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_json_exclusive(path, value):
    path = Path(path)
    expected = _json_bytes(value, pretty=True)
    _publish_exclusive_bytes(path, expected)
    try:
        info = agent_preparation._lstat(path)
        if info.st_size > JSON_LIMIT:
            raise ValueError("修复记录超过 JSON 大小限制")
        with open(common.native_path(path), "rb") as stream:
            actual = stream.read()
        saved = json.loads(actual.decode("utf-8"))
    except SeparationRepairError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise SeparationRepairError(
            "repair_receipt_invalid", "修复记录不是可读 JSON。", path=str(path),
            **_evidence_details("stale")) from exc
    if actual != expected or saved != value:
        raise SeparationRepairError(
            "repair_target_collision", "修复记录路径已有不同内容；拒绝覆盖。", path=str(path),
            **_evidence_details("stale"))
    return path


def _publish_exclusive_bytes_from_file(temporary, target):
    temporary = Path(temporary)
    target = Path(target)
    try:
        os.link(str(common.native_path(temporary)), str(common.native_path(target)))
        return
    except FileExistsError:
        return
    except OSError as link_error:
        if os.name != "nt":
            raise SeparationRepairError(
                "repair_copy_failed", f"无法原子发布声部副本：{link_error}",
                target=str(target), **_evidence_details("stale")) from link_error
        if os.path.lexists(common.native_path(target)):
            return
    try:
        os.rename(str(common.native_path(temporary)), str(common.native_path(target)))
    except FileExistsError:
        return
    except OSError as rename_error:
        raise SeparationRepairError(
            "repair_copy_failed", f"无法发布声部副本：{rename_error}",
            target=str(target), **_evidence_details("stale")) from rename_error


def _copy_stream_exclusive(source, target, expected_sha, *, job, cancel=None):
    source = agent_preparation._assert_real_path(source, leaf_file=True)
    target = Path(target)
    agent_preparation._assert_real_path(target.parent, boundary=job)
    if Path(common.native_path(target)).exists():
        agent_preparation._assert_real_path(target, boundary=job, leaf_file=True)
        if agent_preparation._is_reparse(target) or agent_preparation._hash_file(target) != expected_sha:
            raise SeparationRepairError(
                "repair_target_collision",
                "新任务中已有同名声部文件且摘要不同；未覆盖该文件。",
                path=str(target), expected_sha256=expected_sha,
                **_evidence_details("stale"))
        return target
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".copy-tmp",
        dir=str(common.native_path(target.parent)))
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    before = agent_preparation._lstat(source)
    try:
        with open(common.native_path(source), "rb") as reader, os.fdopen(descriptor, "wb") as writer:
            while True:
                _check_cancel(cancel)
                block = reader.read(COPY_BLOCK)
                if not block:
                    break
                digest.update(block)
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        after = agent_preparation._lstat(source)
        actual_sha = digest.hexdigest()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SeparationRepairError(
                "separation_source_changed", "复制期间源声部发生变化。",
                path=str(source), **_evidence_details("stale"))
        if actual_sha != expected_sha:
            raise SeparationRepairError(
                "separation_repair_plan_stale", "源声部 SHA 与修复计划不一致。",
                path=str(source), expected_sha256=expected_sha,
                actual_sha256=actual_sha,
                **_evidence_details("stale"))
        _publish_exclusive_bytes_from_file(temporary, target)
        if (not Path(common.native_path(target)).is_file()
                or agent_preparation._is_reparse(target)):
            raise SeparationRepairError(
                "repair_copy_failed", "新任务声部复制未产生普通文件。", path=str(target),
                **_evidence_details("stale"))
        return target
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _row_identities(job):
    return {role: _audio_row(job, role) for role in AUDIO_ROLES}


def _target_ready_for_repair(job):
    status = agent_preparation._read_json(job / "status.json", required=True)
    if status.get("state") == "running" or status.get("current_step") or any(
            isinstance(row, dict) and row.get("state") == "running"
            for row in (status.get("steps") or {}).values()):
        raise SeparationRepairError(
            "separation_repair_target_busy", "确定性新任务已开始运行。", path=str(job),
            retryable=True, remediation=["vwa_jobs_get"], **_evidence_details("stale"))
    if Path(common.native_path(job / REPAIR_REPORT_NAME)).exists():
        raise SeparationRepairError(
            "repair_target_incomplete",
            "新任务已有 separation-report.json 但缺少最终修复回执；未覆盖半成品。",
            path=str(job / REPAIR_REPORT_NAME), remediation=["vwa_jobs_get"],
            **_evidence_details("stale"))
    return status

def _mark_failure(job, plan, error, *, cancelled=False):
    if job is None:
        return
    status = agent_preparation._read_json(
        job / "status.json", required=False, default={"schema": 1, "steps": {}})
    if not isinstance(status.get("steps"), dict):
        status["steps"] = {}
    state = "cancelled" if cancelled else "failed"
    status["state"] = state
    status["error"] = _bounded(str(error), 4000)
    status.pop("current_step", None)
    status["steps"]["separation"] = {
        **copy.deepcopy(status["steps"].get("separation") or {}),
        "state": state,
        "error": _bounded(str(error), 4000),
        "repair_plan_sha256": plan.get("plan_sha256"),
        "model_execution": False,
    }
    status["steps"]["separation"].pop("receipt", None)
    status["separation_repair"] = {
        "plan_sha256": plan.get("plan_sha256"),
        "source_job": plan.get("source_job"),
        "model_execution": False,
        "state": state,
    }
    try:
        common.atomic_json(job / "status.json", status)
    finally:
        try:
            import task_contract
            task_contract.fail_stage(job, "separation", state, error)
        except Exception:
            pass


def _source_evidence(plan, job, copied):
    value = {
        "schema_version": SCHEMA_VERSION,
        "kind": SOURCE_EVIDENCE_KIND,
        "plan_sha256": plan["plan_sha256"],
        "source_job": plan["source_job"],
        "source_request_sha256": plan["source_request_sha256"],
        "source_status_sha256": plan["source_status_sha256"],
        "job_path": str(job),
        "mode": plan["mode"],
        "source_failure": copy.deepcopy(plan["source_failure"]),
        "audio": copy.deepcopy(plan["audio"]),
        "supporting_audio": copy.deepcopy(plan["supporting_audio"]),
        "validation": copy.deepcopy(plan["validation"]),
        "copied_sha256": {role: copied[role] for role in AUDIO_ROLES},
        "model_execution": False,
    }
    value["evidence_sha256"] = _digest(value)
    return value


def _repair_report(plan, validation, partition):
    return {
        "method": "reused_rawstems_partition_repair",
        "mode": plan["mode"],
        "voice_mode": plan["mode"],
        "samples": validation["frames"],
        "sample_rate": validation["sample_rate"],
        "timeline_offset_seconds": 0,
        "source_job": plan["source_job"],
        "separation_repair_plan_sha256": plan["plan_sha256"],
        "model_execution": False,
        "model_invocations": 0,
        "rawstem_reuse": True,
        "validation": copy.deepcopy(validation),
        "partition": copy.deepcopy(partition),
    }


def _receipt_unsigned(receipt):
    return {key: value for key, value in receipt.items() if key != "receipt_sha256"}


def _contract_entry_present(data, stage, relative, sha):
    for row in (data.get("entries") or {}).values():
        location = row.get("location") or {}
        if (row.get("producer") == stage and row.get("active", True)
                and location.get("kind") == "task" and location.get("path") == relative
                and row.get("sha256") == sha):
            return True
    return False


def _verify_repair_result(job, plan, *, creation=None):
    receipt_path = job / REPAIR_RECEIPT_NAME
    if not Path(common.native_path(receipt_path)).is_file():
        raise SeparationRepairError(
            "repair_receipt_missing", "修复任务缺少最终回执，不能视为成功。",
            path=str(job), remediation=["vwa_jobs_get"], **_evidence_details("stale"))
    receipt = agent_preparation._read_json(receipt_path, required=True)
    if (receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("kind") != RECEIPT_KIND
            or receipt.get("plan_sha256") != plan.get("plan_sha256")
            or receipt.get("job_path") != str(job)
            or receipt.get("source_job") != plan.get("source_job")
            or receipt.get("model_execution") is not False
            or receipt.get("receipt_sha256") != _digest(_receipt_unsigned(receipt))):
        raise SeparationRepairError(
            "repair_receipt_invalid", "分离修复最终回执无效。",
            path=str(receipt_path), remediation=["vwa_jobs_get"],
            **_evidence_details("stale"))
    evidence_path = job / SOURCE_EVIDENCE_NAME
    evidence = agent_preparation._read_json(evidence_path, required=True)
    evidence_sha = agent_preparation._hash_file(evidence_path)
    evidence_unsigned = {key: value for key, value in evidence.items()
                         if key != "evidence_sha256"}
    if (evidence.get("kind") != SOURCE_EVIDENCE_KIND
            or evidence.get("plan_sha256") != plan.get("plan_sha256")
            or evidence.get("evidence_sha256") != _digest(evidence_unsigned)
            or evidence_sha != receipt.get("source_evidence_sha256")):
        raise SeparationRepairError(
            "repair_source_evidence_invalid", "修复源证据无效或已变化。",
            path=str(evidence_path), **_evidence_details("stale"))
    status = agent_preparation._read_json(job / "status.json", required=True)
    step = (status.get("steps") or {}).get("separation")
    if (status.get("state") != "partial" or status.get("current_step")
            or not isinstance(step, dict) or step.get("state") != "done"
            or step.get("receipt") != receipt.get("separation_receipt")
            or (status.get("steps") or {}).get("midi", {}).get("state") == "done"):
        raise SeparationRepairError(
            "repair_status_invalid", "修复任务 status 不是 separation 完成、MIDI 未执行。",
            path=str(job / "status.json"), **_evidence_details("stale"))
    report = agent_preparation._read_json(job / REPAIR_REPORT_NAME, required=True)
    if (report.get("model_execution") is not False
            or report.get("separation_repair_plan_sha256") != plan.get("plan_sha256")
            or report.get("source_job") != plan.get("source_job")):
        raise SeparationRepairError(
            "repair_report_invalid", "separation-report 的修复来源或模型声明无效。",
            path=str(job / REPAIR_REPORT_NAME), **_evidence_details("stale"))
    outputs = receipt.get("outputs")
    if not isinstance(outputs, dict):
        raise SeparationRepairError(
            "repair_receipt_invalid", "修复回执缺少输出摘要。", **_evidence_details("stale"))
    for relative, expected in outputs.items():
        relative = _relative(relative)
        _, path = _job_file(job, relative)
        if agent_preparation._hash_file(path) != expected:
            raise SeparationRepairError(
                "repair_output_changed", "修复输出在回执后被修改。", path=str(path),
                **_evidence_details("stale"))
    rows = _row_identities(job)
    validation = _validate_audio_content(job, rows)
    if validation["frames"] != plan["validation"]["frames"]:
        raise SeparationRepairError(
            "repair_output_changed", "修复输出的采样长度与计划不一致。",
            expected=plan["validation"]["frames"], actual=validation["frames"],
            **_evidence_details("stale"))
    from artifact_cache import valid_receipt
    request = agent_preparation._read_json(job / "request.json", required=True)
    required = list(pipeline.requirements("separation", request))
    saved = receipt.get("separation_receipt")
    if not valid_receipt(job, request, "separation", required, saved):
        raise SeparationRepairError(
            "repair_separation_receipt_invalid", "separation 阶段回执或内容 SHA 不匹配。",
            path=str(job), **_evidence_details("stale"))
    import task_contract
    data = task_contract.read(job)
    if (data.get("stages", {}).get("separation", {}).get("state") != "ready"
            or not all(_contract_entry_present(data, "separation", relative, sha)
                       for relative, sha in outputs.items())):
        raise SeparationRepairError(
            "repair_artifact_index_invalid", "separation 产物未按 task_contract 正常登记。",
            path=str(job / "artifacts.json"), **_evidence_details("stale"))
    if creation is not None and creation.get("request_sha256") != receipt.get("request_sha256"):
        raise SeparationRepairError(
            "repair_receipt_invalid", "修复回执与创建回执的 request SHA 不一致。",
            **_evidence_details("stale"))
    return receipt, evidence, rows, validation, report


def _result(plan, job, created, *, replay, receipt):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "status": "repaired_partial",
        "state": "partial",
        "job_path": str(job),
        "request_sha256": created["request_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "source_job": plan["source_job"],
        "creation_receipt": created.get("creation_receipt"),
        "creation_receipt_sha256": created.get("receipt_sha256"),
        "reuse_receipt": created.get("reuse_receipt"),
        "reuse_receipt_sha256": created.get("reuse_receipt_sha256"),
        "repair_receipt": str(job / REPAIR_RECEIPT_NAME),
        "repair_receipt_sha256": receipt["receipt_sha256"],
        "separation_receipt_sha256": _digest(receipt["separation_receipt"]),
        "reused_stages": list(REUSE_STAGES),
        "run_stages": ["midi"],
        "next_stage": "midi",
        "model_execution": False,
        "midi_executed": False,
        "replay": bool(replay),
    }

def apply_separation_repair(plan, parent, *, cancel=None, progress=None):
    """Create a new partial preparation job and repair copied raw stems."""
    plan = _validate_plan_shape(plan)
    _check_cancel(cancel)
    source_job, current = _assert_source_matches_plan(plan)
    if not isinstance(parent, (str, os.PathLike)):
        raise ValueError("parent 必须是控制层已授权的绝对目录。")
    parent_path = agent_preparation._resolved(parent)
    agent_preparation._assert_real_path(parent_path)
    if parent_path == source_job or parent_path.is_relative_to(source_job):
        raise SeparationRepairError(
            "unsafe_path", "parent 不能位于修复源任务内；源任务必须保持只读。",
            parent=str(parent_path), source_job=str(source_job),
            **_evidence_details("stale"))
    _check_cancel(cancel)
    created = agent_preparation.create_preparation_reuse(current["reuse_plan"], parent_path)
    job = agent_preparation._job_path(created["job_path"])
    container = agent_preparation._resolved(Path(created["creation_receipt"]).parent)
    agent_preparation._assert_real_path(job, boundary=container)
    if job == source_job or job.is_relative_to(source_job):
        raise SeparationRepairError(
            "unsafe_path", "新建修复任务不能落在源任务内。", path=str(job),
            **_evidence_details("stale"))
    receipt_path = job / REPAIR_RECEIPT_NAME
    if Path(common.native_path(receipt_path)).is_file():
        receipt, _, _, _, _ = _verify_repair_result(job, plan, creation=created)
        return _result(plan, job, created, replay=True, receipt=receipt)

    _target_ready_for_repair(job)
    _check_cancel(cancel)
    copied = {}
    try:
        for role in AUDIO_ROLES:
            expected = (plan["audio"].get(role) or plan["supporting_audio"].get(role))["sha256"]
            source = source_job / PurePosixPath(AUDIO_RELATIVE[role])
            target = job / PurePosixPath(AUDIO_RELATIVE[role])
            _copy_stream_exclusive(source, target, expected, job=job, cancel=cancel)
            copied[role] = expected

        target_rows = _row_identities(job)
        _validate_audio_content(job, target_rows, cancel=cancel)
        evidence = _source_evidence(plan, job, copied)
        evidence_path = _write_json_exclusive(job / SOURCE_EVIDENCE_NAME, evidence)

        status = agent_preparation._read_json(job / "status.json", required=True)
        status["state"] = "partial"
        status["current_step"] = "separation"
        status["run_stages"] = ["midi"]
        status.pop("error", None)
        status.pop("error_details", None)
        status.setdefault("steps", {})["separation"] = {
            "state": "running",
            "started": time.time(),
            "repair_plan_sha256": plan["plan_sha256"],
            "source_job": plan["source_job"],
            "model_execution": False,
        }
        status["separation_repair"] = {
            "plan_sha256": plan["plan_sha256"],
            "source_job": plan["source_job"],
            "source_evidence": str(evidence_path),
            "model_execution": False,
            "state": "running",
        }
        common.atomic_json(job / "status.json", status)
        import task_contract
        task_contract.begin_stage(job, "separation")
        _check_cancel(cancel)

        from vocal_partition import repair
        partition = repair(
            Path(common.native_path(job / "audio")), job,
            cancel_checker=lambda: _cancel_requested(cancel),
            progress=progress,
        )
        _check_cancel(cancel)
        final_rows = _row_identities(job)
        validation = _validate_audio_content(job, final_rows, cancel=cancel)
        _assert_source_matches_plan(plan)
        report = _repair_report(plan, validation, partition)
        report_path = _write_json_exclusive(job / REPAIR_REPORT_NAME, report)

        from artifact_cache import receipt as artifact_receipt
        request = agent_preparation._read_json(job / "request.json", required=True)
        files = list(pipeline.requirements("separation", request))
        separation_receipt = artifact_receipt(job, request, "separation", files)
        outputs = {
            _relative(name): agent_preparation._hash_file(job / PurePosixPath(name))
            for name in files
        }
        repair_receipt = {
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "plan_sha256": plan["plan_sha256"],
            "job_path": str(job),
            "source_job": plan["source_job"],
            "request_sha256": created["request_sha256"],
            "mode": plan["mode"],
            "source_evidence": str(evidence_path),
            "source_evidence_sha256": agent_preparation._hash_file(evidence_path),
            "source_stems": copy.deepcopy(plan["audio"]),
            "supporting_audio": copy.deepcopy(plan["supporting_audio"]),
            "copied_sha256": copy.deepcopy(copied),
            "final_audio": {role: copy.deepcopy(final_rows[role]) for role in AUDIO_ROLES},
            "validation": copy.deepcopy(validation),
            "diagnostics": {
                "partition": copy.deepcopy(partition),
                "input_sum_max_error": partition.get("input_sum_max_error"),
                "sum_max_error": partition.get("sum_max_error"),
            },
            "report": {"path": _relative(REPAIR_REPORT_NAME),
                       "sha256": agent_preparation._hash_file(report_path)},
            "separation_receipt": copy.deepcopy(separation_receipt),
            "separation_receipt_sha256": _digest(separation_receipt),
            "outputs": outputs,
            "model_execution": False,
            "midi_executed": False,
            "state": "partial",
        }
        repair_receipt["receipt_sha256"] = _digest(repair_receipt)

        status = agent_preparation._read_json(job / "status.json", required=True)
        status["steps"]["separation"] = {
            "state": "done",
            "seconds": 0,
            "source_job": plan["source_job"],
            "repair_plan_sha256": plan["plan_sha256"],
            "model_execution": False,
            "receipt": copy.deepcopy(separation_receipt),
        }
        task_contract.index_stage(job, "separation", files,
                                  receipt=separation_receipt, replace=True)
        status["state"] = "partial"
        status.pop("current_step", None)
        status.pop("error", None)
        status.pop("error_details", None)
        status["run_stages"] = ["midi"]
        status["separation_repair"] = {
            "plan_sha256": plan["plan_sha256"],
            "source_job": plan["source_job"],
            "source_evidence": str(evidence_path),
            "source_evidence_sha256": repair_receipt["source_evidence_sha256"],
            "model_execution": False,
            "state": "repaired",
        }
        common.atomic_json(job / "status.json", status)
        _write_json_exclusive(receipt_path, repair_receipt)
        saved, _, _, _, _ = _verify_repair_result(job, plan, creation=created)
        return _result(plan, job, created, replay=False, receipt=saved)
    except InterruptedError as exc:
        _mark_failure(job, plan, exc, cancelled=True)
        raise
    except SeparationRepairError as exc:
        _mark_failure(job, plan, exc, cancelled=False)
        raise
    except Exception as exc:
        _mark_failure(job, plan, exc, cancelled=False)
        from agent_errors import error_payload
        cause = error_payload(exc)
        raise SeparationRepairError(
            "separation_repair_failed",
            f"分离声部修复失败：{_bounded(str(exc))}",
            job_path=str(job), source_job=plan["source_job"],
            cause=cause,
            retryable=bool(getattr(exc, "retryable", False)),
            remediation=getattr(exc, "remediation", None) or _STEM_REMEDIATION,
            **_evidence_details("rebuild")) from exc
