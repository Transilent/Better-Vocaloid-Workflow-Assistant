"""Retrieve the uploader's original Bilibili cover without taking a video frame."""
import argparse
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import atomic_json, file_hash, extract_bv, direct_urlopen

MAX_COVER_BYTES = 12 * 1024 * 1024


def _cover_url(info):
    url = info.get("pic", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not any(
            parsed.hostname == domain or parsed.hostname.endswith("." + domain)
            for domain in ("hdslb.com", "biliimg.com", "bilibili.com")):
        raise ValueError("本家资料中没有可用的原始封面地址，请重新读取 BV 资料或手动选择图片。")
    return urllib.parse.urlunsplit(parsed._replace(scheme="https"))


def cached_cover(info, folder):
    """Return only an intact original cover belonging to this source; never connect."""
    if not isinstance(info, dict):
        return None
    folder = Path(folder).resolve()
    record = folder / 'original-cover.json'
    try:
        if (record.is_symlink() or getattr(record.stat(), 'st_file_attributes', 0) & 1024
                or record.stat().st_size > 65536):
            return None
        saved = json.loads(record.read_text(encoding='utf-8'))
        if (not isinstance(saved, dict) or saved.get('file') not in
                ('original-cover.jpg', 'original-cover.png', 'original-cover.webp')
                or saved.get('url') != _cover_url(info) or saved.get('bvid') != info['bvid']
                or saved.get('kind') != 'original'):
            return None
        image = folder / saved['file']
        if (image.is_symlink() or image.resolve().parent != folder
                or getattr(image.stat(), 'st_file_attributes', 0) & 1024
                or not 0 < image.stat().st_size <= MAX_COVER_BYTES
                or file_hash(image) != saved.get('sha256')):
            return None
        return image, saved
    except (OSError, ValueError, KeyError, TypeError):
        return None


def reuse_cached_cover(previous, destination):
    """Copy an optional verified cover without invalidating legacy/audio-only jobs."""
    destination = Path(destination).resolve()
    try:
        info = json.loads((destination/'source-info.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    found = cached_cover(info, previous)
    if found is None:
        return False
    image, saved = found
    target = destination / image.name
    pending = target.with_name(target.name + '.pending')
    try:
        shutil.copy2(image, pending)
        if file_hash(pending) != saved['sha256']:
            raise ValueError('复制期间原始封面发生变化，请重试。')
        pending.replace(target)
        atomic_json(destination/'original-cover.json', saved)
    finally:
        pending.unlink(missing_ok=True)
    return True


def original_cover(info, folder, refresh=False):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    url = _cover_url(info)
    record = folder / "original-cover.json"
    if not refresh:
        found = cached_cover(info, folder)
        if found is not None:
            return found
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    with direct_urlopen(request, timeout=30) as response:
        data = response.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
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
