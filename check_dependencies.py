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


def check(full=False):
    started = time.monotonic()
    config = load_config()
    missing = []
    for key in TOOL_PATHS:
        value = config.get(key)
        if not value or not Path(value).exists():
            missing.append({"component": key, "file": value or "未配置"})
    manifest = json.loads((ROOT / "dependencies/manifest.json").read_text(encoding="utf-8"))
    files = manifest["entries"] if full else [e for e in manifest["entries"] if "/experiments/" in e["file"]]
    def verify(entry):
        path = (ROOT / entry["file"]).resolve()
        if not path.is_relative_to(ROOT):
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
            "seconds": round(time.monotonic() - started, 2),
            "updated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")}


def validate():
    result = check()
    if result["errors"]:
        details = "\n".join(str(e["file"]) for e in result["errors"][:10])
        raise FileNotFoundError("助手依赖缺失或损坏。请恢复整个 dependencies 目录，或运行 Check-Dependencies.bat：\n" + details)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="检查 Better Vocaloid Workflow Assistant 目录中的运行库与模型")
    parser.add_argument("--full", action="store_true", help="逐文件 SHA-256 校验")
    args = parser.parse_args()
    try:
        result = check(args.full)
    except Exception as exc:
        result = {"state": "failed", "errors": [{"reason": str(exc)}]}
    atomic_json(ROOT / "依赖检查报告.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["state"] == "passed" else 1)
