"""Supervised preparation planning, creation, and real pipeline execution.

This module is the preparation boundary for the local Agent workflow. It
accepts a small, explicit option surface, creates a deterministic private
session below a caller-authorized parent, and delegates execution to the real
resumable pipeline. No inference or semantic provider is run here by planning
or creation.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import time
from urllib.parse import urlsplit

import common
from common import ROOT, app_path, atomic_json, clean_lyrics, file_hash, load_config, native_path, resolve_tools
import pipeline

PLAN_SCHEMA = 1
RECEIPT_SCHEMA = 1
SOURCE_SUMMARY_SCHEMA = 1
REUSE_SCHEMA = 1
SESSION_PREFIX = "agent-preparation-"
RECEIPT_NAME = "creation.json"
REUSE_RECEIPT_NAME = "preparation-reuse.json"
LOCK_NAME = "creation.lock"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_LYRICS_CHARS = 1024 * 1024
SUMMARY_TEXT_CHARS = 240
SHA256_RE = re.compile(r"[a-f0-9]{64}")
BV_RE = re.compile(r"BV1[A-Za-z0-9]{9}")
ALLOWED_OPTIONS = frozenset({
    "language", "lyrics", "backing_lyrics", "recognize_lyrics", "voice_mode",
    "midi_backend", "alignment_engine", "midi_steps", "separation_device",
    "midi_device", "dml_adapter", "cpu_threads", "roformer_device", "bpm",
})
DEFAULT_OPTIONS = {
    "language": "zh",
    "lyrics": "",
    "backing_lyrics": "",
    "recognize_lyrics": True,
    "voice_mode": "dual",
    "midi_backend": "v2",
    "alignment_engine": "hfa",
    "midi_steps": 16,
    "separation_device": "dml",
    "midi_device": "dml",
    "dml_adapter": "auto",
    "cpu_threads": 0,
    "roformer_device": "auto",
    "bpm": None,
}
PREPARATION_STAGES = ("metadata", "download", "audio", "separation", "midi")
SCORE_SUFFIXES = {".mid", ".midi", ".ustx", ".svp", ".vsqx"}
METADATA_AUXILIARY_FILES = ("原视频简介.txt", "歌词候选_需核对.txt")
_RUN_PATCH_LOCK = threading.Lock()

# A preparation prefix is intentionally conservative. Running an earlier stage
# invalidates later status entries in pipeline._run, so reusing a later stage
# without its complete physical prefix would only hide work that would rerun.
STAGE_DEPENDENCIES = {
    "metadata": (),
    "download": ("metadata",),
    "audio": ("download",),
    "separation": ("audio",),
    "midi": ("separation",),
}


class PreparationError(RuntimeError):
    """A preparation failure with machine-readable context."""

    def __init__(self, code: str, message: str, *, component="agent_preparation",
                 retryable=None, remediation=None, **details):
        super().__init__(message)
        self.code = code
        self.details = details
        self.component = component
        self.retryable = (code in {"session_busy", "reuse_plan_stale", "reuse_target_busy"}
                          if retryable is None else bool(retryable))
        self.remediation = remediation


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normal_path(value):
    """Return an ordinary absolute/resolved path for identities and results."""
    text = os.path.abspath(os.fspath(value))
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return Path(text)


def _resolved(value, *, strict=True):
    resolved = Path(native_path(value)).resolve(strict=strict)
    return _normal_path(resolved)


def _lstat(path):
    return os.lstat(native_path(path))


def _is_reparse(path):
    try:
        info = _lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _assert_real_path(path, *, boundary=None, leaf_file=None):
    """Reject links/reparse points from path through the optional boundary."""
    path = _normal_path(path)
    boundary = _normal_path(boundary) if boundary is not None else None
    candidate = path
    while True:
        if _is_reparse(candidate):
            raise PreparationError("unsafe_path", "路径经过符号链接或重解析点。", path=str(candidate))
        if candidate == candidate.parent or (boundary is not None and candidate == boundary):
            break
        candidate = candidate.parent
    if boundary is not None and not path.resolve().is_relative_to(boundary.resolve()):
        raise PreparationError("outside_parent", "路径超出本准备会话目录。", path=str(path))
    if leaf_file is True and not Path(native_path(path)).is_file():
        raise PreparationError("invalid_job", "准备任务文件不存在。", path=str(path))
    return path


def _read_json(path, *, default=None, required=False):
    path = _normal_path(path)
    last = None
    for attempt in range(7):
        try:
            info = _lstat(path)
            if _is_reparse(path):
                raise PreparationError("unsafe_path", "JSON 文件不能是链接。", path=str(path))
            if info.st_size > MAX_JSON_BYTES:
                raise PreparationError("invalid_json", "JSON 文件超出大小限制。", path=str(path))
            with open(native_path(path), "r", encoding="utf-8") as stream:
                value = json.load(stream)
            if not isinstance(value, dict):
                raise ValueError("JSON root is not an object")
            return value
        except FileNotFoundError:
            if required:
                raise PreparationError("missing_record", "所需记录不存在。", path=str(path))
            return copy.deepcopy(default if default is not None else {})
        except (PermissionError, OSError) as error:
            last = error
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 6:
                raise
            time.sleep(0.01 * (2 ** attempt))
        except (UnicodeError, ValueError) as error:
            raise PreparationError("invalid_json", "JSON 记录无法读取。", path=str(path)) from error
    raise OSError(f"无法读取 JSON：{path}") from last


def _hash_file(path):
    path = _assert_real_path(path, leaf_file=True)
    before = _lstat(path)
    digest = file_hash(_normal_path(path))
    after = _lstat(path)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PreparationError("source_changed", "文件在摘要计算期间发生变化。", path=str(path))
    return digest


def _bounded_text(value, name, default=""):
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是文本。")
    if len(value) > MAX_LYRICS_CHARS:
        raise ValueError(f"{name} 超出长度限制。")
    return value


def _summary_text(value, limit=SUMMARY_TEXT_CHARS):
    """Return bounded human-facing metadata without leaking collection data."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def _duration_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else round(number, 3)


def _voice_role_summary(mode):
    """Describe musical roles; voice_mode is not singer diarization."""
    if mode == "single":
        roles = [{"name": "vocals", "role": "mixed_vocal",
                  "description": "一条混合人声轨；主唱与和声保留在同一轨。"}]
        description = "single 是单条混合人声轨，不是按歌手音色或人数拆分。"
    elif mode == "dual":
        roles = [
            {"name": "lead", "role": "lead_vocal", "description": "主唱/前景人声轨。"},
            {"name": "backing", "role": "backing_or_harmony",
             "description": "和声、伴唱或残留人声；可能含串音，不代表第二位歌手。"},
        ]
        description = "dual 是主唱 + 和声/伴唱分离，不是双歌手 diarization。"
    else:
        roles = [{"name": "original_vocal", "role": "original_vocal",
                  "description": "所选分离组件返回的人声角色，不推断歌手身份。"}]
        description = "分离依据模型与设置，不推断歌手身份。"
    roles.append({"name": "instrumental", "role": "accompaniment",
                  "description": "伴奏/非人声轨。"})
    return {"voice_mode": mode, "roles": roles, "singer_diarization": False,
            "duet_singer_separation": False, "description": description}


def _job_path(job):
    if not isinstance(job, (str, os.PathLike)):
        raise ValueError("job 必须是已授权的任务目录。")
    try:
        path = _resolved(job)
        _assert_real_path(path)
    except FileNotFoundError as error:
        raise PreparationError("invalid_job", "任务目录不存在。", path=str(job)) from error
    if not Path(native_path(path)).is_dir():
        raise PreparationError("invalid_job", "任务路径不是目录。", path=str(path))
    return path


def source_summary(job) -> dict:
    """Return a small summary of this job's current video metadata.

    The complete source-info JSON remains an on-demand evidence file. Collection,
    subtitle, description body, and page lists are deliberately not returned.
    """
    job = _job_path(job)
    request = _read_json(job / "request.json", required=True)
    evidence_path = job / "source-info.json"
    info = {}
    metadata_error = None
    if Path(native_path(evidence_path)).is_file():
        try:
            info = _read_json(evidence_path, required=True)
        except (OSError, PreparationError, ValueError) as error:
            metadata_error = _summary_text(str(error), 300)

    owner_data = info.get("owner")
    if isinstance(owner_data, dict):
        owner = _summary_text(owner_data.get("name"), 200)
    else:
        owner = _summary_text(owner_data, 200)
    if not owner:
        owner = _summary_text(request.get("owner_name"), 200)

    pages = info.get("pages")
    pages = pages if isinstance(pages, list) else []
    page_count = info.get("videos")
    if type(page_count) is not int or page_count < 0:
        page_count = len(pages)
    selected_number = request.get("page", request.get("p", request.get("page_number", 1)))
    if type(selected_number) is not int or selected_number < 1:
        selected_number = 1
    selected_page = None
    for page in pages:
        if isinstance(page, dict) and page.get("page") == selected_number:
            selected_page = {
                "number": selected_number,
                "title": _summary_text(page.get("part"), 300),
                "duration_seconds": _duration_value(page.get("duration")),
            }
            break

    rights = info.get("rights") if isinstance(info.get("rights"), dict) else {}
    rights_flags = {
        str(key): bool(value) for key, value in rights.items()
        if type(value) is bool or (type(value) is int and value in (0, 1))
    }
    if len(rights_flags) > 32:
        rights_flags = dict(sorted(rights_flags.items())[:32])

    bvid = request.get("bv") or info.get("bvid")
    source_kind = request.get("source_kind") or info.get("kind") or (
        "bilibili" if bvid else "local")
    title = (_summary_text(info.get("title"), 500) or
             _summary_text(request.get("source_title"), 500) or "待读取")
    duration = _duration_value(info.get("duration"))
    if duration is None and selected_page:
        duration = selected_page.get("duration_seconds")
    evidence = None
    if Path(native_path(evidence_path)).is_file():
        stat_info = _lstat(evidence_path)
        evidence = {"path": str(_normal_path(evidence_path)), "bytes": stat_info.st_size,
                    "format": "json", "returned": False, "read": "on_demand"}

    summary = {
        "schema_version": SOURCE_SUMMARY_SCHEMA,
        "kind": "source_summary",
        "job_path": str(job),
        "source_kind": source_kind,
        "bvid": _summary_text(bvid, 64) or None,
        "title": title,
        "owner": owner,
        "duration_seconds": duration,
        "page_count": max(0, page_count),
        "current_page": selected_page or ({"number": selected_number,
                                            "title": title,
                                            "duration_seconds": duration}
                                           if page_count > 1 else None),
        "cover_url": _summary_text(info.get("pic"), 800) or None,
        "description_summary": _summary_text(info.get("desc")),
        "rights_flags": rights_flags,
        "collection_present": bool(info.get("ugc_season")),
        "collection_payload_returned": False,
        "voice_plan": _voice_role_summary(request.get("voice_mode", "single")),
        "raw_evidence": evidence,
    }
    if metadata_error:
        summary["metadata_error"] = metadata_error
    return summary


def _normalise_options(options):
    if options is None:
        options = {}
    if type(options) is not dict:
        raise ValueError("options 必须是对象。")
    unknown = sorted(set(options) - ALLOWED_OPTIONS)
    if unknown:
        raise ValueError("准备选项包含未授权字段：" + ", ".join(unknown))
    result = copy.deepcopy(DEFAULT_OPTIONS)
    result.update(copy.deepcopy(options))

    if result["language"] not in ("zh", "ja"):
        raise ValueError("language 只能是 zh 或 ja。")
    result["lyrics"] = _bounded_text(result["lyrics"], "lyrics")
    result["backing_lyrics"] = _bounded_text(result["backing_lyrics"], "backing_lyrics")
    if type(result["recognize_lyrics"]) is not bool:
        raise ValueError("recognize_lyrics 必须是布尔值。")
    if result["voice_mode"] not in ("single", "dual", "roformer"):
        raise ValueError("voice_mode 只能是 single、dual 或已安装的 roformer。")
    if result["midi_backend"] not in ("v1", "v2"):
        raise ValueError("midi_backend 只能是 v1 或 v2。")
    if result["alignment_engine"] not in ("hfa", "tifa"):
        raise ValueError("alignment_engine 只能是 hfa 或 tifa。")
    if result["alignment_engine"] == "tifa" and result["midi_backend"] != "v2":
        raise ValueError("TiFA 歌词对齐需要 midi_backend=v2。")
    if type(result["midi_steps"]) is not int or result["midi_steps"] not in (8, 16, 32):
        raise ValueError("midi_steps 只能是 8、16 或 32。")
    for name in ("separation_device", "midi_device"):
        if result[name] not in ("cpu", "dml"):
            raise ValueError(f"{name} 只能是 cpu 或 dml。")
    if result["roformer_device"] not in ("auto", "cpu", "cuda"):
        raise ValueError("roformer_device 只能是 auto、cpu 或 cuda。")
    adapter = result["dml_adapter"]
    if adapter != "auto" and (type(adapter) is not int or adapter < 0):
        raise ValueError("dml_adapter 只能是 auto 或非负整数。")
    if type(result["cpu_threads"]) is not int or not 0 <= result["cpu_threads"] <= 64:
        raise ValueError("cpu_threads 必须是 0–64 的整数。")
    if result["bpm"] is not None:
        if type(result["bpm"]) not in (int, float) or not math.isfinite(result["bpm"]) or not 20 <= result["bpm"] <= 400:
            raise ValueError("bpm 必须是 20–400 之间的有限数值或 null。")
        result["bpm"] = float(result["bpm"])
    return result


def _source_info(source):
    if not isinstance(source, str):
        raise ValueError("source 必须是 BV、Bilibili 视频链接或本地音乐绝对路径。")
    text = source.strip().strip('"')
    if not text or len(text) > 32768 or "\x00" in text:
        raise ValueError("source 不能为空且长度必须有效。")

    if re.fullmatch(r"BV1[A-Za-z0-9]{9}", text) and not re.match(r"^[A-Za-z]:[\\/]", text):
        return {"kind": "bilibili", "bv": text,
                "url": f"https://www.bilibili.com/video/{text}/", "input": source}

    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme.lower() not in ("http", "https") or not (host == "bilibili.com" or host.endswith(".bilibili.com")):
            raise ValueError("仅支持 Bilibili 视频链接或本地音乐文件，其他站点链接不受支持。")
        if parts.username or parts.password or parts.port not in (None, 80, 443):
            raise ValueError("Bilibili 链接格式不受支持。")
        match = re.fullmatch(r"/video/(BV1[A-Za-z0-9]{9})/?", parts.path)
        if not match:
            raise ValueError("Bilibili 链接必须指向一个 /video/BV… 视频。")
        bv = match.group(1)
        return {"kind": "bilibili", "bv": bv,
                "url": f"https://www.bilibili.com/video/{bv}/", "input": source}

    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("本地音乐必须使用明确的绝对路径。")
    try:
        identity = _resolved(path)
        identity = _assert_real_path(identity, leaf_file=True)
        info = _lstat(identity)
    except FileNotFoundError as error:
        raise ValueError("本地音乐文件不存在。") from error
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise ValueError("本地音乐必须是存在且非空的普通文件。")
    return {"kind": "local", "path": str(identity), "size": info.st_size,
            "sha256": _hash_file(identity), "suffix": identity.suffix.lower(), "input": source}


def _required_stages(source_kind):
    result = []
    for name in PREPARATION_STAGES:
        if name == "metadata":
            reason = "读取 Bilibili 元数据并保存发布来源证据" if source_kind == "bilibili" else "登记本地音乐标题并建立任务来源证据"
        elif name == "download":
            reason = "下载原视频" if source_kind == "bilibili" else "复制并摘要核验本地音乐快照"
        elif name == "audio":
            reason = "用 ffmpeg 解码原始音频与封面"
        elif name == "separation":
            reason = "运行本地声部分离模型"
        else:
            reason = "运行本地 MIDI/歌词提取模型并生成待复核谱面"
        result.append({"name": name, "reason": reason})
    return result


def _tool_evidence(options):
    config = load_config(resolve_paths=False)
    draft = dict(config)
    draft["vocal2midi"] = "dependencies/vocal2midi2" if options["midi_backend"] == "v2" else "dependencies/vocal2midi"
    draft["separation_device"] = options["separation_device"]
    draft["midi_device"] = options["midi_device"]
    draft["dml_adapter"] = options["dml_adapter"]
    draft["cpu_threads"] = options["cpu_threads"]
    resolved = resolve_tools(draft)
    keys = ["python", "ffmpeg", "vocal2midi", "mdx_model", "mdx_metadata"]
    if options["voice_mode"] == "dual":
        keys += ["chorus_model", "chorus_metadata"]
    rows = []
    for key in keys:
        value = resolved.get(key)
        exists = bool(value and Path(native_path(value)).exists())
        row_path = str(_normal_path(value)) if value else None
        rows.append({"name": key, "path": row_path, "exists": exists})
    return {"config_sha256": _hash_file(ROOT / "config.json") if Path(native_path(ROOT / "config.json")).is_file() else None,
            "required": rows}


def _dependency_evidence(options, stage_names):
    from dependency_profiles import task_features, status
    request = {
        "language": options["language"], "recognize_lyrics": options["recognize_lyrics"],
        "midi_backend": options["midi_backend"], "project_formats": [],
        "tools": {"vocal2midi": "dependencies/vocal2midi2" if options["midi_backend"] == "v2" else "dependencies/vocal2midi"},
    }
    features = sorted(task_features(request, set(stage_names)))
    rows = status(features)
    missing = []
    for name in features:
        row = rows.get(name, {})
        for item in row.get("missing", []):
            missing.append({"feature": name, "file": str(item)})
    missing.sort(key=lambda item: (item["feature"], item["file"]))
    return {"features": features, "ready": not missing, "missing_count": len(missing),
            "missing": missing[:64], "missing_truncated": len(missing) > 64}


def _preflight(options, stage_names, source):
    missing = []
    unverified = []
    try:
        tools = _tool_evidence(options)
        for row in tools["required"]:
            if not row["exists"]:
                missing.append({"kind": "tool", "name": row["name"], "path": row["path"]})
    except (OSError, ValueError, KeyError, TypeError) as error:
        tools = {"config_sha256": None, "required": []}
        missing.append({"kind": "tool_evidence", "message": str(error)})
    try:
        dependencies = _dependency_evidence(options, stage_names)
        missing.extend({"kind": "dependency", **row} for row in dependencies["missing"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        dependencies = {"features": [], "ready": False, "missing_count": 1,
                        "missing": [], "missing_truncated": False}
        missing.append({"kind": "dependency_evidence", "message": str(error)})

    if options["voice_mode"] == "roformer":
        try:
            from optional_components import status as roformer_status
            state = roformer_status()
            if not state.get("ready"):
                missing.append({"kind": "optional_component", "name": "bs-roformer", "ready": False})
        except (OSError, ValueError, KeyError, TypeError) as error:
            missing.append({"kind": "optional_component", "name": "bs-roformer", "message": str(error)})
    if options["alignment_engine"] == "tifa":
        tifa = ROOT / "dependencies/vocal2midi2/models/tifa-1.0-onnx"
        if not Path(native_path(tifa)).is_dir():
            missing.append({"kind": "model", "name": "tifa-1.0-onnx", "path": str(_normal_path(tifa))})

    if source["kind"] == "bilibili":
        unverified.append({"kind": "network", "evidence": "视频存在性、登录要求、简介与媒体内容", "verified": False})
    else:
        unverified.append({"kind": "inference_input", "evidence": "实际音频解码、声部质量与歌词可辨识度", "verified": False})

    alternatives = []
    for row in missing:
        if row.get("kind") == "model" and row.get("name") == "tifa-1.0-onnx":
            remediation = ("将 alignment_engine 改为 hfa 后重新生成准备计划；"
                           "HubertFA 不依赖 TiFA 模型目录。")
            row["remediation"] = remediation
            row["alternative_options"] = {"alignment_engine": "hfa"}
            alternatives.append({"reason": "tifa_missing", "action": "switch_engine",
                                 "options": {"alignment_engine": "hfa"},
                                 "instructions": remediation})
        elif row.get("kind") == "optional_component" and row.get("name") == "bs-roformer":
            remediation = "改用 voice_mode=single 或 dual 后重新生成准备计划。"
            row["remediation"] = remediation
            row["alternative_options"] = {"voice_mode": "single"}
            alternatives.append({"reason": "roformer_missing", "action": "switch_voice_mode",
                                 "options": {"voice_mode": "single"},
                                 "instructions": remediation})
        else:
            row.setdefault("retryable", False)
            row.setdefault("remediation", "在“模型与组件”中修复此处列出的具体文件，然后重新执行 preflight。")

    suggested = alternatives[0]["options"] if len(alternatives) == 1 else {}
    return {
        "ready": not missing,
        "missing_required": missing,
        "alternatives": alternatives,
        "suggested_options": suggested,
        "readiness_summary": {
            "tools_ready": not any(row.get("kind") in ("tool", "tool_evidence") for row in missing),
            "dependencies_ready": bool(dependencies.get("ready")) and not any(
                row.get("kind") in ("dependency", "dependency_evidence") for row in missing),
            "preparation_ready": not missing,
            "note": "dependencies.ready 只描述依赖清单；preparation_ready 还包含工具和模型检查。",
        },
        "voice_plan": _voice_role_summary(options["voice_mode"]),
        "unverified": unverified,
        "tools": tools,
        "dependencies": dependencies,
    }


def _validate_plan(plan, *, allow_reuse=False):
    if type(plan) is not dict:
        raise ValueError("准备计划必须是对象。")
    supplied = plan.get("plan_sha256")
    if not isinstance(supplied, str) or not SHA256_RE.fullmatch(supplied):
        raise ValueError("plan_sha256 必须是小写 SHA-256。")
    unsigned = {key: copy.deepcopy(value) for key, value in plan.items() if key != "plan_sha256"}
    if _digest(unsigned) != supplied:
        raise PreparationError("plan_tampered", "准备计划摘要不匹配；计划已损坏或被篡改。")
    kind = plan.get("kind")
    allowed_kinds = {"preparation_plan", "preparation_reuse"} if allow_reuse else {"preparation_plan"}
    if plan.get("schema_version") != PLAN_SCHEMA or kind not in allowed_kinds:
        raise ValueError("不支持的准备计划版本。")
    if kind == "preparation_reuse":
        source_job = plan.get("source_job")
        reuse = plan.get("reuse")
        if not isinstance(source_job, str) or not Path(source_job).is_absolute():
            raise ValueError("复用计划必须包含绝对 source_job 路径。")
        if type(reuse) is not dict or reuse.get("schema_version") != REUSE_SCHEMA:
            raise ValueError("复用计划的 reuse 证据无效。")
    options = _normalise_options(plan.get("options"))
    if options != plan.get("options"):
        raise PreparationError("plan_tampered", "准备计划参数不是规范形式。")
    if plan.get("prepare_only") is not True:
        raise PreparationError("plan_tampered", "准备计划必须停在待复核产物。")
    source = plan.get("source")
    if type(source) is not dict:
        raise ValueError("准备计划来源无效。")
    if source.get("kind") == "bilibili":
        bv = source.get("bv")
        if not isinstance(bv, str) or not BV_RE.fullmatch(bv):
            raise ValueError("准备计划 BV 无效。")
        if source.get("url") != f"https://www.bilibili.com/video/{bv}/":
            raise PreparationError("plan_tampered", "准备计划 Bilibili 地址不一致。")
    elif source.get("kind") == "local":
        current = _source_info(source.get("path"))
        if current["kind"] != "local" or current["sha256"] != source.get("sha256") or current["size"] != source.get("size"):
            raise PreparationError("source_changed", "本地音乐在计划后发生变化，请重新生成计划。", path=source.get("path"))
    else:
        raise ValueError("准备计划来源类型无效。")
    return options, source


def plan_preparation(source, options=None) -> dict:
    """Build a deterministic, read-only preparation plan.

    Planning only stats/reads explicit inputs and local dependency evidence.
    It does not fetch metadata, download media, run a model, or call an AI
    provider. The default workflow stops after MIDI/native score generation.
    """
    normal = _normalise_options(options)
    source_info = _source_info(source)
    stages = _required_stages(source_info["kind"])
    stage_names = [row["name"] for row in stages]
    preflight = _preflight(normal, stage_names, source_info)
    plan = {
        "schema_version": PLAN_SCHEMA,
        "kind": "preparation_plan",
        "source": source_info,
        "options": normal,
        "prepare_only": True,
        "stages": stages,
        "required_stages": stage_names,
        "network": {
            "required": source_info["kind"] == "bilibili",
            "operations": (["metadata", "media_download"] if source_info["kind"] == "bilibili" else []),
            "direct_only": source_info["kind"] == "bilibili",
            "verified_at_plan": False,
        },
        "inference": {
            "required": True,
            "activities": ["vocal_separation", "midi_and_lyric_extraction"],
            "providers": [],
            "semantic_model": False,
            "performed": False,
        },
        "voice_plan": _voice_role_summary(normal["voice_mode"]),
        "separation_roles": _voice_role_summary(normal["voice_mode"])["roles"],
        "preflight": preflight,
        "expected_handoff": "score_review",
        "semantic_model_used": False,
        "synthesis_performed": False,
    }
    plan["plan_sha256"] = _digest(plan)
    return plan


@contextmanager
def _directory_lock(directory):
    directory = _assert_real_path(directory)
    if not Path(native_path(directory)).exists():
        Path(native_path(directory)).mkdir(parents=True)
    if not Path(native_path(directory)).is_dir():
        raise PreparationError("collision", "准备会话路径不是目录。", path=str(directory))
    lock_path = directory / LOCK_NAME
    if _is_reparse(lock_path):
        raise PreparationError("unsafe_path", "准备会话锁不能是链接。", path=str(lock_path))
    with open(native_path(lock_path), "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 15
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise PreparationError("session_busy", "准备会话正在创建，请稍后重试。", path=str(directory))
                    time.sleep(0.025)
            try:
                if stream.seek(0, 2) == 0:
                    stream.write(b"0")
                    stream.flush()
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _receipt_sha(receipt):
    return _digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})


def _validate_receipt(receipt):
    supplied = receipt.get("receipt_sha256")
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("kind") != "preparation_creation":
        raise PreparationError("receipt_invalid", "准备创建回执版本无效。")
    if not isinstance(supplied, str) or not SHA256_RE.fullmatch(supplied) or _receipt_sha(receipt) != supplied:
        raise PreparationError("receipt_invalid", "准备创建回执摘要不匹配。")
    return receipt


def _job_candidates(container):
    found = []
    try:
        with os.scandir(native_path(container)) as entries:
            for entry in entries:
                if entry.name in (LOCK_NAME, RECEIPT_NAME):
                    continue
                path = container / entry.name
                if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                    raise PreparationError("unsafe_path", "准备会话包含链接或重解析点。", path=str(path))
                if entry.is_dir(follow_symlinks=False) and Path(native_path(path / "request.json")).is_file():
                    found.append(_resolved(path))
                else:
                    raise PreparationError("collision", "准备会话包含未知条目，未做自动覆盖。", path=str(path))
    except FileNotFoundError:
        return []
    return found


def _receipt_for(container, job, plan_sha, replay):
    request_sha = _hash_file(job / "request.json")
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "kind": "preparation_creation",
        "plan_sha256": plan_sha,
        "request_sha256": request_sha,
        "session_path": str(_resolved(container)),
        "job_path": str(_resolved(job)),
        "job_relative": _resolved(job).relative_to(_resolved(container)).as_posix(),
        "replay": bool(replay),
    }
    receipt["receipt_sha256"] = _receipt_sha(receipt)
    return receipt


def create_preparation(plan, parent, *, _allow_reuse=False) -> dict:
    """Create or replay the deterministic preparation session and job."""
    options, source = _validate_plan(plan, allow_reuse=_allow_reuse)
    plan_sha = plan["plan_sha256"]
    if not isinstance(parent, (str, os.PathLike)):
        raise ValueError("parent 必须是控制层已授权的绝对目录。")
    parent_path = Path(parent).expanduser()
    if not parent_path.is_absolute():
        raise ValueError("parent 必须是绝对路径。")
    parent_path = _resolved(parent_path)
    if not Path(native_path(parent_path)).is_dir():
        raise PreparationError("parent_missing", "授权父目录不存在或不是目录。", path=str(parent_path))

    container = parent_path / (SESSION_PREFIX + plan_sha)
    if Path(native_path(container)).exists() and not Path(native_path(container)).is_dir():
        raise PreparationError("collision", "确定性准备会话路径已被文件占用。", path=str(container))
    container = _assert_real_path(container)

    with _directory_lock(container):
        receipt_path = container / RECEIPT_NAME
        if Path(native_path(receipt_path)).exists():
            receipt = _validate_receipt(_read_json(receipt_path, required=True))
            if receipt.get("plan_sha256") != plan_sha:
                raise PreparationError("receipt_collision", "创建回执属于另一份计划。", path=str(receipt_path))
            job = _resolved(receipt.get("job_path", ""))
            _assert_real_path(job, boundary=container, leaf_file=False)
            if job == _resolved(container):
                raise PreparationError("job_invalid", "准备任务不能与会话目录相同。", path=str(job))
            if not Path(native_path(job)).is_dir() or not Path(native_path(job / "request.json")).is_file():
                raise PreparationError("job_missing", "创建回执登记的任务缺失。", path=str(job))
            request_sha = _hash_file(job / "request.json")
            if request_sha != receipt.get("request_sha256"):
                raise PreparationError("job_changed", "创建后的 request.json 已改变。", path=str(job))
            return {"schema_version": 1, "job_path": str(job), "request_sha256": request_sha,
                    "plan_sha256": plan_sha, "replay": True,
                    "creation_receipt": str(_resolved(receipt_path)),
                    "receipt_sha256": receipt["receipt_sha256"]}

        candidates = _job_candidates(container)
        if len(candidates) > 1:
            raise PreparationError("collision", "准备会话存在多个候选任务，未做自动覆盖。", path=str(container))
        replay = bool(candidates)
        if replay:
            job = candidates[0]
        else:
            kwargs = dict(options)
            kwargs.update({
                "source": source["bv"] if source["kind"] == "bilibili" else "",
                "local_music": source["path"] if source["kind"] == "local" else None,
                "parent": container,
                "remember": False,
                "prepare_only": True,
                "project_formats": [],
                "audio_formats": [],
            })
            job = _resolved(pipeline.create_job(**kwargs))
        job = _assert_real_path(job, boundary=container, leaf_file=False)
        if job == _resolved(container):
            raise PreparationError("job_invalid", "准备任务不能与会话目录相同。", path=str(job))
        if not Path(native_path(job)).is_dir() or not Path(native_path(job / "request.json")).is_file():
            raise PreparationError("job_invalid", "pipeline 没有返回有效准备任务。", path=str(job))
        receipt = _receipt_for(container, job, plan_sha, replay)
        atomic_json(receipt_path, receipt)
        return {"schema_version": 1, "job_path": str(_resolved(job)),
                "request_sha256": receipt["request_sha256"], "plan_sha256": plan_sha,
                "replay": replay, "creation_receipt": str(_resolved(receipt_path)),
                "receipt_sha256": receipt["receipt_sha256"]}


def _source_options(request):
    tools = request.get("tools") if isinstance(request.get("tools"), dict) else {}
    backend = request.get("midi_backend")
    if backend not in ("v1", "v2"):
        backend = "v2" if str(tools.get("vocal2midi", "")).replace("\\", "/").rstrip("/").endswith(
            "vocal2midi2") else "v1"
    return _normalise_options({
        "language": request.get("language", "zh"),
        "lyrics": request.get("lyrics", ""),
        "backing_lyrics": request.get("backing_lyrics", ""),
        "recognize_lyrics": request.get("recognize_lyrics", True),
        "voice_mode": request.get("voice_mode", "single"),
        "midi_backend": backend,
        "alignment_engine": request.get("alignment_engine", "hfa"),
        "midi_steps": request.get("midi_steps", 16),
        "separation_device": request.get("separation_device", tools.get("separation_device", "dml")),
        "midi_device": request.get("midi_device", tools.get("midi_device", "dml")),
        "dml_adapter": request.get("dml_adapter", tools.get("dml_adapter", "auto")),
        "cpu_threads": request.get("cpu_threads", tools.get("cpu_threads", 0)),
        "roformer_device": request.get("roformer_device", "auto"),
        "bpm": request.get("bpm"),
    })


def _merged_reuse_options(request, options):
    if options is not None and type(options) is not dict:
        raise ValueError("options 必须是对象。")
    if options:
        unknown = sorted(set(options) - ALLOWED_OPTIONS)
        if unknown:
            raise ValueError("复用选项包含未授权字段：" + ", ".join(unknown))
    merged = _source_options(request)
    if options:
        merged.update(copy.deepcopy(options))
    return _normalise_options(merged)


def _source_argument(job, request):
    if request.get("source_kind") == "local":
        candidates = []
        if request.get("source_audio"):
            try:
                candidates.append(app_path(request["source_audio"]))
            except (OSError, ValueError):
                pass
        if request.get("input_file"):
            candidates.append(job / request["input_file"])
        for candidate in candidates:
            if Path(native_path(candidate)).is_file() and candidate.stat().st_size > 0:
                return str(_resolved(candidate))
        raise PreparationError("source_missing", "源任务没有可核验的本地音乐快照。",
                               path=str(job))
    bvid = request.get("bv")
    if isinstance(bvid, str) and BV_RE.fullmatch(bvid):
        return bvid
    raise PreparationError("source_missing", "源任务没有受支持的 Bilibili 视频身份。",
                           path=str(job))


def _target_request(source_request, options):
    request = copy.deepcopy(source_request)
    request.update({
        "language": options["language"],
        "lyrics": clean_lyrics(options["lyrics"]),
        "lyric_lines": clean_lyrics(options["lyrics"], preserve_lines=True).splitlines(),
        "backing_lyrics": clean_lyrics(options["backing_lyrics"]),
        "backing_lyric_lines": clean_lyrics(options["backing_lyrics"],
                                            preserve_lines=True).splitlines(),
        "recognize_lyrics": options["recognize_lyrics"],
        "voice_mode": options["voice_mode"],
        "midi_steps": options["midi_steps"],
        "bpm": options["bpm"],
        "alignment_engine": options["alignment_engine"],
        "prepare_only": True,
        "project_formats": [],
        "audio_formats": [],
        "export_pitch": False,
    })
    try:
        tools = load_config(resolve_paths=False)
    except (OSError, ValueError, KeyError):
        tools = copy.deepcopy(source_request.get("tools") or {})
    tools = dict(tools)
    tools["vocal2midi"] = ("dependencies/vocal2midi2"
                            if options["midi_backend"] == "v2" else "dependencies/vocal2midi")
    tools["separation_device"] = options["separation_device"]
    tools["midi_device"] = options["midi_device"]
    tools["dml_adapter"] = options["dml_adapter"]
    tools["cpu_threads"] = options["cpu_threads"]
    request["tools"] = tools
    if options["midi_backend"]:
        request["midi_backend"] = options["midi_backend"]
    if options["voice_mode"] == "roformer":
        request["roformer_device"] = options["roformer_device"]
    return request


def _relative_stage_name(value):
    name = str(value).replace("\\", "/")
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts or ":" in name
            or name in (".", "")):
        raise PreparationError("unsafe_reuse_path", "阶段产物相对路径无效。", path=name)
    return relative.as_posix()


def _stage_files(job, request, stage):
    files = list(pipeline.requirements(stage, request))
    if stage == "metadata":
        for name in METADATA_AUXILIARY_FILES:
            path = job / name
            if Path(native_path(path)).is_file() and not _is_reparse(path):
                files.append(name)
    return sorted({_relative_stage_name(name) for name in files})


def _local_download_content_ok(job, request):
    if request.get("source_kind") != "local":
        return True
    try:
        report = _read_json(job / "import-report.json", required=True)
        snapshot = job / request["input_file"]
        expected = report.get("sha256")
        return (isinstance(expected, str) and SHA256_RE.fullmatch(expected) is not None
                and expected == _hash_file(snapshot))
    except (OSError, KeyError, PreparationError, ValueError):
        return False


def _source_stage_probe(job, request, status, stage):
    step = (status.get("steps") or {}).get(stage)
    step = step if isinstance(step, dict) else {}
    row = {"stage": stage, "source_state": step.get("state", "missing"),
           "source_complete": False, "source_reason": None,
           "source_receipt_sha256": None, "files": {}}
    if step.get("state") != "done":
        row["source_reason"] = "source_not_completed"
        return row, None
    files = _stage_files(job, request, stage)
    try:
        file_hashes = {}
        required_names = {_relative_stage_name(name)
                          for name in pipeline.requirements(stage, request)}
        for name in files:
            path = _assert_real_path(job / PurePosixPath(name), boundary=job, leaf_file=True)
            if name in required_names and path.stat().st_size <= 0:
                raise PreparationError("empty_stage_file", "阶段产物为空。", path=str(path))
            file_hashes[name] = _hash_file(path)
        if stage == "download" and not _local_download_content_ok(job, request):
            raise PreparationError("source_changed", "本地导入快照摘要不匹配。", path=str(job))
        from artifact_cache import receipt, valid_receipt
        required = list(pipeline.requirements(stage, request))
        if stage == "midi":
            voices = ("vocals",) if request.get("voice_mode") == "single" else ("lead", "backing")
            if not all(common.completed_voice(job, request, voice) for voice in voices):
                raise PreparationError("stage_unverified", "MIDI 的输入签名或产物摘要不匹配。",
                                       stage=stage)
            if len(voices) == 2:
                merged = _read_json(job / "midi/report.json", required=True)
                if merged.get("merged_sha256") != _hash_file(job / "midi/voices.mid"):
                    raise PreparationError("stage_unverified", "合并 MIDI 摘要不匹配。",
                                           stage=stage)
            receipt_value = None
        else:
            saved = step.get("receipt")
            if saved is None:
                receipt_value = receipt(job, request, stage, required)
            elif not isinstance(saved, dict) or not valid_receipt(
                    job, request, stage, required, saved):
                raise PreparationError("stage_unverified", "阶段回执或内容 SHA 不匹配。",
                                       stage=stage)
            else:
                receipt_value = copy.deepcopy(saved)
    except (OSError, KeyError, PreparationError, TypeError, ValueError) as error:
        row["source_reason"] = getattr(error, "code", "stage_unverified")
        row["source_error"] = _summary_text(str(error), 300)
        return row, None
    row["source_complete"] = True
    row["files"] = file_hashes
    if receipt_value is not None:
        row["source_receipt_sha256"] = _digest(receipt_value)
    return row, receipt_value


def _target_stage_compatible(job, source_request, target_request, stage, receipt_value):
    if stage == "metadata":
        return True, "metadata_reused_with_request_lyrics"
    try:
        if stage in ("download", "audio", "separation"):
            from artifact_cache import stage_signature
            target_signature = stage_signature(job, target_request, stage)
            source_signature = (receipt_value or {}).get("signature")
            if source_signature != target_signature:
                if (stage == "separation"
                        and source_request.get("voice_mode") != target_request.get("voice_mode")):
                    return False, "voice_mode_changed"
                return False, "dependency_options_changed"
            return True, "content_and_dependencies_match"
        if stage == "midi":
            voices = ("vocals",) if target_request.get("voice_mode") == "single" else (
                "lead", "backing")
            if all(common.completed_voice(job, target_request, voice) for voice in voices):
                return True, "midi_inputs_and_outputs_match"
            if source_request.get("voice_mode") != target_request.get("voice_mode"):
                return False, "voice_mode_changed"
            return False, "midi_options_or_lyrics_changed"
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        return False, "dependency_unverified:" + _summary_text(str(error), 120)
    return False, "unknown_stage"


def _canonical_reuse_stages(reuse_stages):
    if reuse_stages is None:
        return list(PREPARATION_STAGES)
    if not isinstance(reuse_stages, (list, tuple)) or isinstance(reuse_stages, (str, bytes)):
        raise ValueError("reuse_stages 必须是阶段名称数组。")
    requested = []
    for stage in reuse_stages:
        if stage not in PREPARATION_STAGES:
            raise ValueError("未知复用阶段：" + str(stage))
        if stage in requested:
            raise ValueError("reuse_stages 不能重复：" + str(stage))
        requested.append(stage)
    return [stage for stage in PREPARATION_STAGES if stage in requested]


def _build_reuse_evidence(source_job, request, status, options, reuse_stages):
    target_request = _target_request(request, options)
    requested = _canonical_reuse_stages(reuse_stages)
    closure = set()
    for stage in requested:
        index = PREPARATION_STAGES.index(stage)
        closure.update(PREPARATION_STAGES[:index + 1])

    stages = []
    probes = {}
    reused = []
    for stage in PREPARATION_STAGES:
        row, receipt_value = _source_stage_probe(source_job, request, status, stage)
        probes[stage] = (row, receipt_value)
    for stage in PREPARATION_STAGES:
        row, receipt_value = probes[stage]
        required_by_request = stage in closure
        compatible = False
        compatibility_reason = "not_in_reuse_prefix"
        if row["source_complete"] and required_by_request:
            compatible, compatibility_reason = _target_stage_compatible(
                source_job, request, target_request, stage, receipt_value)
        dependencies_ready = all(name in reused for name in STAGE_DEPENDENCIES[stage])
        reuse = bool(required_by_request and row["source_complete"] and compatible
                     and dependencies_ready)
        if reuse:
            reused.append(stage)
            action = "reuse"
            reason = "verified_complete_and_dependency_compatible"
        elif not required_by_request:
            action = "not_requested"
            reason = "not_in_reuse_prefix"
        elif not row["source_complete"]:
            action = "run"
            reason = row["source_reason"] or "source_not_completed"
        elif not compatible:
            action = "run"
            reason = compatibility_reason
        elif not dependencies_ready:
            action = "run"
            reason = "dependency_not_reusable"
        else:
            action = "run"
            reason = "not_reusable"
        stages.append({
            "stage": stage,
            "explicitly_requested": stage in requested,
            "required_by_reuse_prefix": required_by_request,
            "source_state": row["source_state"],
            "source_complete": row["source_complete"],
            "source_receipt_sha256": row["source_receipt_sha256"],
            "files": copy.deepcopy(row["files"]),
            "compatible": compatible,
            "compatibility_reason": compatibility_reason,
            "action": action,
            "reason": reason,
        })
    run = [stage for stage in PREPARATION_STAGES if stage not in reused]
    return {
        "schema_version": REUSE_SCHEMA,
        "kind": "preparation_reuse",
        "source_job": str(source_job),
        "source_request_sha256": _hash_file(source_job / "request.json"),
        "source_status_sha256": _hash_file(source_job / "status.json"),
        "target_options_sha256": _digest(options),
        "requested_stages": requested,
        "reuse_stages": reused,
        "run_stages": run,
        "dependency_stages": list(STAGE_DEPENDENCIES),
        "voice_before": request.get("voice_mode", "single"),
        "voice_after": options["voice_mode"],
        "stages": stages,
    }


def plan_preparation_reuse(source_job, options=None, reuse_stages=None) -> dict:
    """Plan a new preparation job while source_job remains strictly read-only."""
    source_job = _job_path(source_job)
    request = _read_json(source_job / "request.json", required=True)
    status = _read_json(source_job / "status.json", required=True)
    target_options = _merged_reuse_options(request, options)
    source = _source_argument(source_job, request)
    plan = plan_preparation(source, target_options)
    plan["kind"] = "preparation_reuse"
    plan["source_job"] = str(source_job)
    plan["reuse"] = _build_reuse_evidence(
        source_job, request, status, target_options, reuse_stages)
    plan["plan_sha256"] = _digest({
        key: value for key, value in plan.items() if key != "plan_sha256"})
    return plan


def _validate_reuse_plan(plan):
    options, source = _validate_plan(plan, allow_reuse=True)
    if plan.get("kind") != "preparation_reuse":
        raise ValueError("不是准备复用计划。")
    source_job = _job_path(plan.get("source_job"))
    reuse = plan.get("reuse")
    if type(reuse) is not dict or reuse.get("kind") != "preparation_reuse":
        raise PreparationError("plan_tampered", "复用计划证据无效。")
    if reuse.get("source_job") != str(source_job):
        raise PreparationError("plan_tampered", "复用计划的源任务路径不一致。")
    for name in ("source_request_sha256", "source_status_sha256", "target_options_sha256"):
        value = reuse.get(name)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise PreparationError("plan_tampered", "复用计划缺少内容摘要。", field=name)
    if reuse.get("target_options_sha256") != _digest(options):
        raise PreparationError("plan_tampered", "复用计划目标选项摘要不一致。")
    requested = reuse.get("requested_stages")
    reusable = reuse.get("reuse_stages")
    run = reuse.get("run_stages")
    if (not isinstance(requested, list) or not isinstance(reusable, list)
            or not isinstance(run, list)
            or any(stage not in PREPARATION_STAGES for stage in requested + reusable + run)
            or len(set(requested)) != len(requested)
            or len(set(reusable)) != len(reusable) or len(set(run)) != len(run)
            or set(reusable).intersection(run)
            or set(reusable).union(run) != set(PREPARATION_STAGES)):
        raise PreparationError("plan_tampered", "复用阶段列表无效。")
    return options, source, source_job, reuse


def _atomic_text(path, text):
    import tempfile
    path = Path(path)
    Path(native_path(path.parent)).mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=native_path(path.parent),
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(native_path(temporary), native_path(path))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _copy_reuse_file(target_job, source_job, name, expected_sha256):
    name = _relative_stage_name(name)
    source = _assert_real_path(source_job / PurePosixPath(name), boundary=source_job,
                               leaf_file=True)
    current_sha = _hash_file(source)
    if not isinstance(expected_sha256, str) or current_sha != expected_sha256:
        raise PreparationError("reuse_stale_source", "源阶段文件在计划后发生变化。",
                               path=str(source), stage_file=name)
    destination = _resolved(target_job / PurePosixPath(name), strict=False)
    _assert_real_path(destination.parent, boundary=target_job)
    Path(native_path(destination.parent)).mkdir(parents=True, exist_ok=True)
    _assert_real_path(destination.parent, boundary=target_job)
    if Path(native_path(destination)).exists():
        if _is_reparse(destination) or not Path(native_path(destination)).is_file():
            raise PreparationError("reuse_collision", "目标复用路径不是普通文件。",
                                   path=str(destination))
        if _hash_file(destination) != expected_sha256:
            raise PreparationError("reuse_collision", "目标文件与源阶段 SHA 不一致，未覆盖。",
                                   path=str(destination))
        return destination
    shutil.copy2(native_path(source), native_path(destination))
    if _hash_file(destination) != expected_sha256:
        raise PreparationError("reuse_copy_failed", "复制后的阶段文件 SHA 不一致。",
                               path=str(destination))
    return destination


def _target_stage_receipt(job, request, stage):
    if stage == "midi":
        voices = ("vocals",) if request.get("voice_mode") == "single" else ("lead", "backing")
        if not all(common.completed_voice(job, request, voice) for voice in voices):
            raise PreparationError("reuse_target_unverified", "新任务的 MIDI 输入签名不匹配。",
                                   stage=stage)
        if len(voices) == 2:
            report = _read_json(job / "midi/report.json", required=True)
            if report.get("merged_sha256") != _hash_file(job / "midi/voices.mid"):
                raise PreparationError("reuse_target_unverified", "新任务的合并 MIDI 摘要不匹配。",
                                       stage=stage)
        return None
    from artifact_cache import receipt
    return receipt(job, request, stage, list(pipeline.requirements(stage, request)))


def _reuse_receipt_sha(receipt):
    return _digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})


def _validate_reuse_receipt(receipt, plan_sha256):
    if (receipt.get("schema_version") != REUSE_SCHEMA
            or receipt.get("kind") != "preparation_reuse"
            or receipt.get("plan_sha256") != plan_sha256
            or receipt.get("receipt_sha256") != _reuse_receipt_sha(receipt)):
        raise PreparationError("reuse_receipt_invalid", "准备复用回执无效。")
    return receipt


def _replay_reuse_receipt(path, job, plan_sha256):
    receipt = _validate_reuse_receipt(_read_json(path, required=True), plan_sha256)
    if receipt.get("job_path") != str(_resolved(job)):
        raise PreparationError("reuse_receipt_invalid", "复用回执的任务路径不一致。")
    for stage, files in (receipt.get("files") or {}).items():
        if stage not in PREPARATION_STAGES or not isinstance(files, dict):
            raise PreparationError("reuse_receipt_invalid", "复用回执的阶段文件无效。")
        for name, expected in files.items():
            target = job / PurePosixPath(_relative_stage_name(name))
            if not Path(native_path(target)).is_file() or _hash_file(target) != expected:
                raise PreparationError("reuse_receipt_stale", "复用回执登记的文件已变化。",
                                       path=str(target))
    return receipt


def create_preparation_reuse(plan, parent) -> dict:
    """Create a new managed job and copy only revalidated reused stages."""
    options, source, source_job, reuse = _validate_reuse_plan(plan)
    source_request = _read_json(source_job / "request.json", required=True)
    source_status = _read_json(source_job / "status.json", required=True)
    current = _build_reuse_evidence(
        source_job, source_request, source_status, options,
        copy.deepcopy(reuse.get("requested_stages")))
    if _digest(current) != _digest(reuse):
        raise PreparationError(
            "reuse_plan_stale",
            "源任务、阶段回执或模型依赖已变化；请重新生成复用计划。",
            source_job=str(source_job))

    created = create_preparation(plan, parent, _allow_reuse=True)
    job = _job_path(created["job_path"])
    container = _resolved(Path(created["creation_receipt"]).parent)
    with _directory_lock(container):
        receipt_path = job / REUSE_RECEIPT_NAME
        if Path(native_path(receipt_path)).is_file():
            saved_receipt = _replay_reuse_receipt(receipt_path, job, plan["plan_sha256"])
            return {**created, "reuse_receipt": str(_resolved(receipt_path)),
                    "reuse_receipt_sha256": saved_receipt["receipt_sha256"],
                    "source_job": str(source_job),
                    "reused_stages": copy.deepcopy(saved_receipt["reused_stages"]),
                    "run_stages": copy.deepcopy(saved_receipt["run_stages"]),
                    "replay": True}

        status_path = job / "status.json"
        old_status = _read_json(status_path, required=False)
        if old_status.get("current_step") or any(
                isinstance(row, dict) and row.get("state") == "running"
                for row in (old_status.get("steps") or {}).values()):
            raise PreparationError("reuse_target_busy", "新准备任务已开始运行，未覆盖其状态。",
                                   path=str(job))

        target_request = _read_json(job / "request.json", required=True)
        rows = {row["stage"]: row for row in reuse.get("stages") or []
                if isinstance(row, dict) and row.get("stage") in PREPARATION_STAGES}
        steps = {}
        copied = {}
        for stage in reuse["reuse_stages"]:
            row = rows.get(stage)
            if not row or row.get("action") != "reuse" or not isinstance(row.get("files"), dict):
                raise PreparationError("reuse_target_unverified", "计划中的复用阶段证据缺失。",
                                       stage=stage)
            for name, expected in row["files"].items():
                _copy_reuse_file(job, source_job, name, expected)
            if stage == "metadata":
                _atomic_text(job / "参考歌词.txt", target_request.get("lyrics", ""))
            stage_receipt = _target_stage_receipt(job, target_request, stage)
            steps[stage] = {
                "state": "done",
                "seconds": 0,
                "reused_from": str(source_job),
                "source_request_sha256": reuse["source_request_sha256"],
                "source_receipt_sha256": row.get("source_receipt_sha256"),
            }
            if stage_receipt is not None:
                steps[stage]["receipt"] = stage_receipt
            copied[stage] = {name: _hash_file(job / PurePosixPath(name))
                             for name in row["files"]}

        if _hash_file(source_job / "request.json") != reuse["source_request_sha256"]:
            raise PreparationError("reuse_stale_source", "复制期间源任务 request.json 发生变化。",
                                   path=str(source_job))
        if _hash_file(source_job / "status.json") != reuse["source_status_sha256"]:
            raise PreparationError("reuse_stale_source", "复制期间源任务 status.json 发生变化。",
                                   path=str(source_job))

        status = {"schema": 1, "steps": steps,
                  "state": "done" if all(stage in steps for stage in PREPARATION_STAGES) else "partial",
                  "source_job": str(source_job), "reuse_plan_sha256": plan["plan_sha256"],
                  "reused_stages": list(steps), "run_stages": copy.deepcopy(reuse["run_stages"])}
        atomic_json(status_path, status)
        reuse_receipt = {
            "schema_version": REUSE_SCHEMA,
            "kind": "preparation_reuse",
            "plan_sha256": plan["plan_sha256"],
            "job_path": str(job),
            "source_job": str(source_job),
            "source_request_sha256": reuse["source_request_sha256"],
            "source_status_sha256": reuse["source_status_sha256"],
            "reused_stages": copy.deepcopy(reuse["reuse_stages"]),
            "run_stages": copy.deepcopy(reuse["run_stages"]),
            "files": copied,
            "voice_before": reuse["voice_before"],
            "voice_after": reuse["voice_after"],
        }
        reuse_receipt["receipt_sha256"] = _reuse_receipt_sha(reuse_receipt)
        atomic_json(receipt_path, reuse_receipt)
        return {**created, "reuse_receipt": str(_resolved(receipt_path)),
                "reuse_receipt_sha256": reuse_receipt["receipt_sha256"],
                "source_job": str(source_job),
                "reused_stages": copy.deepcopy(reuse["reuse_stages"]),
                "run_stages": copy.deepcopy(reuse["run_stages"])}


def _cancel_requested(cancel):
    if cancel is None:
        return False
    if callable(cancel):
        try:
            value = cancel()
        except InterruptedError:
            return True
    else:
        method = getattr(cancel, "is_set", None)
        if not callable(method):
            raise TypeError("cancel 必须是可调用对象或 Event。")
        value = method()
    return bool(value)


def _voice_for(path, job):
    relative = path.relative_to(job)
    lowered = [part.lower() for part in relative.parts]
    stem = relative.stem.lower()
    for name in ("lead", "backing", "vocals"):
        if name in lowered or re.search(rf"(^|[-_.]){name}($|[-_.])", stem):
            return name
    return None


def _score_manifest(job):
    """Enumerate current score files without treating a clean job as unchanged."""
    job = _resolved(job)
    root = native_path(job)
    rows = []
    errors = []
    skipped = {
        "action", "backups", "calibration", "cache", "cover", "covers", "exports",
        "logs", "media", "preview", "publish", "publication", "receipts", "scratch",
        "subtitles", "temp", "temporary", "tmp", "video", "work", "workdir", "音频",
        "临时", "备份", "导出", "字幕", "视频",
    }
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = _normal_path(directory)
        kept = []
        for name in dirs:
            candidate = directory_path / name
            if directory_path == job and name.lower() in skipped:
                continue
            try:
                if _is_reparse(candidate):
                    errors.append({"path": str(_resolved(candidate)), "error": "unsafe_path"})
                else:
                    kept.append(name)
            except OSError as error:
                errors.append({"path": str(candidate), "error": str(error)})
        dirs[:] = kept
        for name in files:
            path = directory_path / name
            if path.suffix.lower() not in SCORE_SUFFIXES:
                continue
            try:
                identity = _resolved(path)
                _assert_real_path(identity, boundary=job, leaf_file=True)
                info = _lstat(identity)
                if info.st_size <= 0:
                    errors.append({"path": str(identity), "error": "empty_score_file"})
                    continue
                digest = _hash_file(identity)
                row = {"path": str(identity), "sha256": digest,
                       "role": "midi" if identity.suffix.lower() in (".mid", ".midi") else "editor_project"}
                voice = _voice_for(identity, job)
                if voice:
                    row["voice"] = voice
                rows.append(row)
            except (OSError, PreparationError) as error:
                errors.append({"path": str(path), "error": str(error)})
    rows.sort(key=lambda row: row["path"].casefold())
    errors.sort(key=lambda row: row["path"].casefold())
    return rows, errors


def _read_status(job):
    try:
        return _read_json(job / "status.json", required=False)
    except (OSError, PreparationError, ValueError):
        return {}


def run_preparation(job, until=None, cancel=None, progress=None) -> dict:
    """Run the real resumable preparation pipeline under owned cancellation.

    Heavy stage internals remain in pipeline. Tests may replace only the stage
    runner. The caller's cancel signal is observed while this process owns the
    real pipeline job lock; a competing owner can never receive our flag.
    """
    job = _resolved(job)
    if not Path(native_path(job)).is_dir() or not Path(native_path(job / "request.json")).is_file():
        raise ValueError("准备任务必须包含 request.json。")
    _assert_real_path(job, leaf_file=False)
    if until is not None and until not in PREPARATION_STAGES:
        raise ValueError("until 只能是 metadata、download、audio、separation 或 midi。")
    if cancel is not None and not callable(cancel) and not callable(getattr(cancel, "is_set", None)):
        raise TypeError("cancel 必须是可调用对象或 Event。")
    if progress is not None and not callable(progress):
        raise TypeError("progress 必须是可调用对象。")

    if not _RUN_PATCH_LOCK.acquire(blocking=False):
        raise RuntimeError("本进程已有另一个准备任务在运行。")
    pipeline_error = None
    callback_errors = []
    try:
        original_run = pipeline.run
        original_job_lock = pipeline.job_lock
        original_stage_run = pipeline._run
        with original_job_lock(job):
            stop = threading.Event()
            cancel_written = threading.Event()

            @contextmanager
            def already_owned_lock(inner_job):
                if _resolved(inner_job) != job:
                    raise RuntimeError("准备执行器只能使用已持有的任务锁。")
                yield

            def monitored_stage_run(stage_job, stage_until):
                def monitor():
                    last_event = None
                    cancel_probe_failed = False
                    while not stop.is_set():
                        try:
                            if (not cancel_probe_failed and not cancel_written.is_set()
                                    and _cancel_requested(cancel)):
                                atomic_json(job / "cancel.flag", {
                                    "requested": True,
                                    "owner_pid": os.getpid(),
                                    "source": "agent_preparation",
                                })
                                cancel_written.set()
                        except Exception as error:
                            cancel_probe_failed = True
                            callback_errors.append({"callback": "cancel", "error": str(error)})
                        try:
                            event = _read_json(job / "progress.json", required=False)
                            if event and event != last_event:
                                last_event = copy.deepcopy(event)
                                if progress is not None:
                                    try:
                                        progress(copy.deepcopy(event))
                                    except Exception as error:
                                        callback_errors.append({"callback": "progress", "error": str(error)})
                        except (OSError, PreparationError, ValueError):
                            pass
                        stop.wait(0.05)

                thread = threading.Thread(target=monitor, name="vwa-preparation-monitor", daemon=True)
                thread.start()
                try:
                    return original_stage_run(stage_job, stage_until)
                finally:
                    stop.set()
                    thread.join(timeout=5)

            pipeline.job_lock = already_owned_lock
            pipeline._run = monitored_stage_run
            try:
                original_run(job, until=until)
            finally:
                pipeline.job_lock = original_job_lock
                pipeline._run = original_stage_run
    except InterruptedError as error:
        pipeline_error = error
    except (OSError, ValueError, RuntimeError, FileNotFoundError) as error:
        pipeline_error = error
    finally:
        _RUN_PATCH_LOCK.release()

    status = _read_status(job)
    pipeline_state = status.get("state", "failed" if pipeline_error else "unknown")
    try:
        files, file_errors = _score_manifest(job)
    except (OSError, PreparationError) as error:
        files, file_errors = [], [{"path": str(job), "error": str(error)}]
    result_status = pipeline_state
    error_row = None
    if pipeline_error is not None:
        result_status = "cancelled" if isinstance(pipeline_error, InterruptedError) else "failed"
        error_row = {"type": type(pipeline_error).__name__, "message": str(pipeline_error),
                     "code": getattr(pipeline_error, "code", None)}
    elif pipeline_state == "done" and until in (None, "midi") and not any(
            Path(row["path"]).suffix.lower() in (".mid", ".midi") for row in files):
        result_status = "failed"
        error_row = {"type": "PreparationError", "message": "MIDI 阶段完成但没有可复核的 MIDI 产物。",
                     "code": "missing_score_output"}
    elif pipeline_state not in ("done", "partial", "failed", "cancelled", "interrupted"):
        result_status = "failed"
        error_row = {"type": "PreparationError", "message": "流水线没有返回可验证状态。",
                     "code": "missing_pipeline_status"}
    elif pipeline_state in ("failed", "cancelled", "interrupted"):
        result_status = pipeline_state
        error_row = {"type": "PipelineStatus", "message": str(status.get("error", pipeline_state)),
                     "code": pipeline_state}
    elif pipeline_state == "partial":
        result_status = "partial"
    if file_errors and result_status == "done":
        result_status = "failed"
        error_row = {"type": "PreparationError", "message": "部分 score 产物无法安全核验。",
                     "code": "score_manifest_error"}
    if result_status in ("failed", "cancelled", "interrupted"):
        next_step = "retry_preparation"
    elif files:
        next_step = "score_review"
    else:
        next_step = "continue_preparation"
    return {
        "schema_version": 1,
        "job_path": str(job),
        "status": result_status,
        "pipeline_status": status,
        "files": files,
        "file_errors": file_errors,
        "next_step": next_step,
        "error": error_row,
        "callback_errors": callback_errors,
        "completed_scope": "preparation_only",
        "synthesis_performed": False,
        "semantic_model_used": False,
    }
