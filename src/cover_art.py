"""Retrieve the uploader's original Bilibili cover without taking a video frame."""
import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import atomic_json, file_hash, extract_bv


def original_cover(info, folder, refresh=False):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    url = info.get("pic", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not any(
            parsed.hostname == domain or parsed.hostname.endswith("." + domain)
            for domain in ("hdslb.com", "biliimg.com", "bilibili.com")):
        raise ValueError("本家资料中没有可用的原始封面地址，请重新读取 BV 资料或手动选择图片。")
    url = urllib.parse.urlunsplit(parsed._replace(scheme="https"))
    record = folder / "original-cover.json"
    if not refresh and record.exists():
        try:
            saved = json.loads(record.read_text(encoding="utf-8"))
            image = folder / Path(saved["file"]).name
            if saved["url"] == url and saved["bvid"] == info["bvid"] and file_hash(image) == saved["sha256"]:
                return image, saved
        except (OSError, ValueError, KeyError):
            pass
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("封面文件过大，请手动选择图片。")
    if data.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        suffix = ".webp"
    else:
        raise ValueError("封面地址未返回可读取的图片，不会自动改用视频截图。")
    image = folder / ("original-cover" + suffix)
    pending = image.with_name(image.name + ".pending")
    pending.write_bytes(data)
    pending.replace(image)
    saved = {"bvid": info["bvid"], "url": url, "file": image.name,
             "sha256": file_hash(image), "kind": "original", "title": info.get("title", "")}
    atomic_json(record, saved)
    return image, saved


def job_cover(job, refresh=False):
    job = Path(job).resolve()
    info = json.loads((job / "source-info.json").read_text(encoding="utf-8"))
    return original_cover(info, job, refresh)


def main():
    parser = argparse.ArgumentParser(description="本地提取 B站本家原始封面")
    parser.add_argument("--source", required=True, help="BV 号或 B站链接")
    parser.add_argument("--output", required=True, help="封面输出目录")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    from pipeline import get_view
    image, _ = original_cover(get_view(extract_bv(args.source)), args.output, args.refresh)
    print(image, flush=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
