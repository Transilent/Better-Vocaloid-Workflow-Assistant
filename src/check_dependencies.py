"""Local dependency diagnostics; no installation or global configuration."""
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, TOOL_PATHS, atomic_json, file_hash, load_config


def check(full=False, features=None):
    started = time.monotonic()
    config = load_config()
    missing = []
    selected = set(features) if features is not None else None
    paths = TOOL_PATHS if selected is None else ('python', 'ffmpeg')
    for key in paths:
        value = config.get(key)
        if not value or not Path(value).exists():
            missing.append({"component": key, "file": value or "未配置"})
    manifest = json.loads((ROOT / "dependencies/manifest.json").read_text(encoding="utf-8"))
    critical={'dependencies/vocal2midi2/application/pipeline.py',
              'dependencies/vocal2midi2/inference/pipeline/auto_lyric_hybrid.py',
              'dependencies/export/libresvip/__init__.py'}
    files = manifest["entries"] if full else [e for e in manifest["entries"] if "/experiments/" in e["file"] or e['file'] in critical]
    if selected is not None:
        from dependency_profiles import group
        files = [e for e in files if group(e['file']) in selected]
    def verify(entry):
        path = (ROOT / entry["file"]).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError("依赖清单包含目录外路径。")
        try:
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                return {"component": "dependency_file", "file": entry["file"], "reason": "文件缺失或大小不符"}
            elif full and file_hash(path) != entry["sha256"]:
                return {"component": "dependency_file", "file": entry["file"], "reason": "SHA-256 不符"}
        except OSError:
            return {"component": "dependency_file", "file": entry["file"], "reason": "校验时无法读取文件"}
        return None

    checked = 0
    with ThreadPoolExecutor(max_workers=4 if full else 1) as pool:
        for issue in pool.map(verify, files):
            if issue:
                missing.append(issue)
            checked += 1
            if full and checked % 4000 == 0:
                print(f"已校验 {checked}/{len(files)} 个依赖文件。", flush=True)
    return {"state": "failed" if missing else "passed", "mode": "sha256" if full else "startup",
            "checked_files": checked, "errors": missing,
            "feature_states": feature_states(selected, missing),
            "seconds": round(time.monotonic() - started, 2),
            "updated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")}


def feature_states(features, errors):
    """Keep Chinese and Japanese checks independent for task startup gating."""
    from dependency_profiles import LABELS, group
    selected = set(LABELS) if features is None else set(features)
    states = {name: {'state': 'ready', 'missing': []} for name in selected}
    for error in errors:
        name = error.get('file')
        feature = group(name) if isinstance(name, str) and error.get('component') == 'dependency_file' else 'core'
        if feature not in states:
            continue
        states[feature]['missing'].append(name or error.get('reason', '运行库'))
        states[feature]['state'] = 'missing'
    return states


def missing_guidance(errors):
    """Build feature-specific recovery text without blaming unrelated models."""
    from dependency_profiles import group, LABELS
    labels = set()
    for error in errors:
        if error.get('component') != 'dependency_file':
            continue
        name = error.get('file')
        if isinstance(name, str):
            labels.add(LABELS[group(name)])
    parts = []
    if '中文歌词识别' in labels:
        parts.append('中文模型缺失（中文歌词识别 · Qwen3-ASR）。请打开“模型与组件”中的'
                     '“中文歌词识别”，点击“下载中文模型”；下载可取消并支持断点续传。'
                     '日语识别和“不识别歌词”模式不需要此权重。')
    if '日语歌词识别' in labels:
        parts.append('日语模型缺失（日语歌词识别 · Romaji ASR）。请恢复“日语歌词识别”对应的模型文件；'
                     '中文模型与此功能无关。')
    other = sorted(labels - {'中文歌词识别', '日语歌词识别'})
    if other:
        parts.append('同时缺少：' + '、'.join(other) + '。运行库与模型可在配套文件中恢复。')
    if not parts and errors and all(error.get('component') != 'dependency_file' for error in errors):
        parts.append('所需运行库缺失。请检查工具路径，并使用 tools/Check-Dependencies.bat 核对。')
    return ' '.join(parts) or '所需运行库或模型缺失。'


def validate(features=None):
    result = check(features={'core'} if features is None else features)
    if result["errors"]:
        details = "\n".join(str(e.get("file") or e.get("reason") or e) for e in result["errors"][:10])
        raise FileNotFoundError(missing_guidance(result["errors"]) + "\n" + details)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="检查 Vocaloid Workflow Assistant 目录中的运行库与模型")
    parser.add_argument("--full", action="store_true", help="逐文件 SHA-256 校验")
    args = parser.parse_args()
    try:
        result = check(args.full)
    except Exception as exc:
        result = {"state": "failed", "errors": [{"reason": str(exc)}]}
    atomic_json(ROOT / "依赖检查报告.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["state"] == "passed" else 1)
