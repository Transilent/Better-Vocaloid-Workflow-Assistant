"""Create immutable, editable publication bundles from finished videos."""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, file_hash, load_config, app_path

XIAOHONGSHU = 'xiaohongshu'
XIAOHONGSHU_CREATOR_URL = 'https://creator.xiaohongshu.com/'
BROWSER_PLATFORMS = ('bilibili',)
PUBLICATION_LIMITS = {'bilibili': (80, 2000), XIAOHONGSHU: (20, 1000)}
MANUAL_ONLY = '小红书仅支持手动投稿，请使用发布页的“小红书手动投稿”资料。'
AI_REQUIRED = '请先确认小红书笔记是否包含 AI 生成或合成内容。'
AI_PLATFORM_HINT = '含 AI 内容：请在小红书发布页使用“笔记内容声明”，选择“笔记含 AI 合成内容”。'


def require_browser_platform(platform):
    if platform == XIAOHONGSHU:
        raise ValueError(MANUAL_ONLY)
    if platform not in BROWSER_PLATFORMS:
        raise ValueError('未知的浏览器填写平台。')


def clean_path(value):
    return app_path(value)


class SourceInfoError(ValueError):
    """A missing or unreadable publication source that can be selected again."""


def read_source_info(job):
    if job is None or not str(job).strip().strip('"').strip():
        raise SourceInfoError("请先选择来源任务，或读取本家 BV 资料。")
    job = clean_path(job)
    path = job / "source-info.json"
    if not path.is_file():
        raise SourceInfoError("来源任务资料不存在，可能已移动或清理。请重新选择任务，或读取本家 BV 资料。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceInfoError("来源资料无法读取。请重新选择来源任务，或重新读取本家 BV 资料。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("title"), str):
        raise SourceInfoError("来源资料格式不完整。请重新选择来源任务，或重新读取本家 BV 资料。")
    if "owner" in data and not isinstance(data["owner"], dict):
        raise SourceInfoError("来源资料格式不完整。请重新选择来源任务，或重新读取本家 BV 资料。")
    return data


def inspect_video(path, ffmpeg=None, *, require_audio=True):
    path = clean_path(path)
    if not path.is_file():
        raise ValueError("找不到成品视频，请选择完整文件路径。")
    result = subprocess.run([ffmpeg or load_config()["ffmpeg"], "-hide_banner", "-i", str(path)],
                            capture_output=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    text = result.stderr
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not duration or not re.search(r"Stream .*Video:", text) or (require_audio and not re.search(r"Stream .*Audio:", text)):
        raise ValueError("成品需要同时包含视频和音频，且能读取完整时长。" if require_audio
                         else "原视频需要包含可读取的画面和时长。")
    hours, minutes, seconds = map(float, duration.groups())
    size = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", text)
    return {"path": str(path), "bytes": path.stat().st_size,
            "duration": hours * 3600 + minutes * 60 + seconds,
            "width": int(size[1]) if size else None, "height": int(size[2]) if size else None}


def extract_cover(video, destination, seconds, ffmpeg=None, *, require_audio=True):
    video = clean_path(video)
    info = inspect_video(video, ffmpeg, require_audio=require_audio)
    if not 0 <= float(seconds) < info["duration"]:
        raise ValueError(f"封面时间须在 0 到 {info['duration']:.2f} 秒之间。")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.stem + ".pending.jpg")
    result = subprocess.run([ffmpeg or load_config()["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                             "-ss", str(float(seconds)), "-i", str(video), "-map", "0:v:0",
                             "-frames:v", "1", "-q:v", "2", str(pending)],
                            capture_output=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90)
    if result.returncode or not pending.is_file() or pending.stat().st_size < 100:
        pending.unlink(missing_ok=True)
        raise RuntimeError("封面截取失败：" + result.stderr[-800:])
    pending.replace(destination)
    return {"source": str(video), "seconds": float(seconds), "sha256": file_hash(destination)}


def source_draft(job):
    read_source_info(job)
    job = clean_path(job)
    path = job / "发布简介草稿.txt"
    if not path.is_file():
        raise SourceInfoError("这个来源任务缺少简介草稿。请重新选择已读取资料的任务，或重新读取本家 BV 资料。")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise SourceInfoError("来源简介无法读取。请重新选择来源任务，或重新读取本家 BV 资料。") from exc


def validate_content(content, platform, *, require_ai=False):
    if platform not in PUBLICATION_LIMITS:
        raise ValueError("未知发布平台。")
    if not content.get("title", "").strip():
        raise ValueError("请先填写发布标题。")
    if not content.get("description", "").strip():
        raise ValueError("请填写简介并保留本家与 STAFF 信息。")
    # Conservative local limits; never truncate title, description or credits.
    limits = PUBLICATION_LIMITS[platform]
    for key, limit in zip(("title", "description"), limits):
        if len(content[key]) > limit:
            label = "标题" if key == "title" else "简介"
            raise ValueError(f"{platform} {label}超过助手的 {limit} 字检查限制，请在发布准备页编辑。")
    if platform == XIAOHONGSHU and (require_ai or 'ai_generated' in content):
        if type(content.get('ai_generated')) is not bool:
            raise ValueError(AI_REQUIRED)


def prepare_bilibili_covers(bundle):
    """Keep the source JPEG and fit it into each required ratio without cropping."""
    bundle = Path(bundle)
    image = bundle / "cover.jpg"
    probe = subprocess.run([load_config()["ffmpeg"], "-hide_banner", "-i", str(image)],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", probe.stderr)
    if not size:
        raise ValueError("无法读取封面尺寸。")
    width, height = map(int, size.groups())
    variants = {"policy": "contain"}
    for ratio, w, h, filename in (("4:3", 1440, 1080, "cover-bilibili-4-3.jpg"),
                                  ("16:9", 1920, 1080, "cover-bilibili-16-9.jpg")):
        if width * h == height * w:
            destination, actual_w, actual_h = image, width, height
        else:
            destination, actual_w, actual_h = bundle / filename, w, h
            result = subprocess.run([load_config()["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                                     "-i", str(image), "-vf",
                                     f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
                                     "-frames:v", "1", "-q:v", "2", str(destination)],
                                    capture_output=True, timeout=60,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not destination.is_file():
                raise ValueError("封面补边生成失败。")
        variants[ratio] = {"file": destination.name, "sha256": file_hash(destination),
                           "width": actual_w, "height": actual_h}
    return variants


def prepare(job, video, content, cover_seconds=20, cover_file=None, parent=None, cover_mode="original",
            *, expected_video=None, check=None, register=True):
    """Snapshot only deliberate inputs; finished video stays in its location."""
    if expected_video is None:
        source_draft(job)
    job = clean_path(job)
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    bv = str(request.get('bv') or '')
    if expected_video is not None and bv:
        source_draft(job)

    def cancelled():
        if check and check():
            raise InterruptedError('已取消准备发布资料。')

    cancelled()
    if not content:
        raise ValueError("至少选择一个发布平台。")
    for platform, fields in content.items():
        validate_content(fields, platform, require_ai=True)
    info = inspect_video(video)
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    prefix = bv if re.fullmatch(r'BV[0-9a-zA-Z]{10}', bv) else 'Local'
    bundle = Path(parent or ROOT / "publish-packages") / f"{prefix}_{stamp}"
    bundle.mkdir(parents=True)
    try:
        if cover_file or cover_mode == "original":
            art = None
            if cover_file:
                image = clean_path(cover_file)
            elif expected_video is not None:
                from cover_art import cached_cover
                found = cached_cover(read_source_info(job), job)
                if found is None:
                    raise ValueError('任务中的本家原封面已失效，请改用成品截图或选择图片。')
                image, art = found
            else:
                from cover_art import job_cover
                image, art = job_cover(job)
            if not image.is_file():
                raise ValueError("找不到选择的封面图片。")
            if image.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                raise ValueError("请选择 JPG、PNG 或 WebP 封面。")
            destination = bundle / "cover.jpg"
            raw_jpeg = image.read_bytes().startswith(b"\xff\xd8\xff")
            argv = [load_config()["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-i", str(image), "-frames:v", "1"]
            result = subprocess.run(argv + (["-f", "null", "-"] if raw_jpeg else ["-q:v", "2", str(destination)]),
                                    capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if raw_jpeg and result.returncode == 0:
                shutil.copy2(image, destination)
            if result.returncode or not (bundle / "cover.jpg").is_file():
                raise ValueError("封面图片无法读取。")
            if expected_video is not None and art and file_hash(image, check=check) != art['sha256']:
                raise ValueError('本家原封面在生成期间已变化，请重新选择。')
            cover = {"kind": "original" if art else "custom", "source": str(image), "seconds": None,
                     "url": art["url"] if art else None, "sha256": file_hash(destination)}
        elif cover_mode in ("frame", "original-frame"):
            use_source = cover_mode == 'original-frame' or expected_video is None
            original = job / "video/source.mp4" if use_source else Path(info['path'])
            if not original.is_file():
                raise ValueError("任务中没有原视频；请先完成下载，或选择封面图片。")
            stamp = publication_file_stamp(original)
            cover = extract_cover(original, bundle / "cover.jpg", cover_seconds)
            if publication_file_stamp(original) != stamp:
                raise ValueError('原画面在截图期间已变化，请重试。')
            cover["kind"] = "frame"
            cover["frame_source"] = 'original' if use_source else 'finished'
        else:
            raise ValueError("未知封面来源。")
        cancelled()
        for name in ("source-info.json", "upstream-info.json", "发布简介草稿.txt"):
            path = job / name
            if path.exists():
                shutil.copy2(path, bundle / name)
        platform_covers = {"bilibili": prepare_bilibili_covers(bundle)} if "bilibili" in content else {}
        manifest = {"version": 2, "state": "prepared", "source_job": str(job), "source_bv": bv,
                    "video": {**info, "sha256": file_hash(info["path"], check=check)}, "cover": cover,
                    "platform_covers": platform_covers,
                    "content": content, "created_at": stamp, "final_publish": "human_only"}
        atomic_json(bundle / "manifest.json", manifest)
        for platform, fields in content.items():
            (bundle / (platform + "_标题.txt")).write_text(fields["title"], encoding="utf-8")
            (bundle / (platform + "_简介.txt")).write_text(fields["description"], encoding="utf-8")
        checklist = (
            f"成品：{info['path']}\n本家：{request.get('bv') or '待填写（本地音乐）'}\n时长：{info['duration']:.2f} 秒\n"
            f"封面来源：{ {'original':'本家原始封面','frame':'视频画面截图','custom':'手选图片'}[cover['kind']] }。\n"
            "标题与简介使用你在助手中填写的版本。\n"
        )
        if 'bilibili' in content:
            checklist += (
                "\nB站：封面按4:3、16:9分别适配，比例不同时补黑边，保留完整原图。\n"
                "上传后请在网页核对封面、分区、标签、原创/转载及其他必填项，再手动发布。\n"
            )
        if XIAOHONGSHU in content:
            ai_generated = content[XIAOHONGSHU]['ai_generated']
            checklist += (
                "\n小红书：请在官方网页或 App 手动上传成品和 cover.jpg，并粘贴标题与正文。\n"
                f"创作中心：{XIAOHONGSHU_CREATOR_URL}\n"
                f"AI 生成或合成内容（本地选择）：{'含有' if ai_generated else '不含'}。\n"
                + (AI_PLATFORM_HINT + '\n' if ai_generated else '若编辑后加入 AI 生成或合成内容，请在平台更新声明。\n')
                + "本地选择不会替你设置平台声明。发布前请核对原创/转载、署名与授权。\n"
            )
        (bundle / "发布前核对.txt").write_text(checklist, encoding="utf-8")
        # Only unchanged generated files can later be offered for cleanup.
        # Edited drafts, unknown files and legacy bundles remain protected.
        atomic_json(bundle / 'cleanup-inventory.json', {'version':1,'kind':'publication-generated',
            'entries':[{'file':path.name,'bytes':path.stat().st_size,'sha256':file_hash(path)}
                       for path in sorted(bundle.iterdir()) if path.is_file()]})
        cancelled()
        if not register:
            return bundle.resolve()
        from task_contract import register_publication
        if expected_video is None and check is None:
            register_publication(job, bundle)
        else:
            register_publication(job, bundle, expected_video=expected_video, check=check)
        return bundle.resolve()
    except BaseException:
        # Preserve diagnostics without presenting an incomplete bundle as ready.
        atomic_json(bundle / "status.json", {"state": "prepare_failed"})
        raise


def load_bundle(bundle, platform, verify_hash=True, *, require_ai=False):
    bundle = clean_path(bundle)
    data = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    validate_content(data["content"].get(platform, {}), platform, require_ai=require_ai)
    video = clean_path(data["video"]["path"])
    cover = bundle / "cover.jpg"
    if not video.is_file() or not cover.is_file():
        raise ValueError("视频或封面已经移动，请重新准备发布包。")
    if verify_hash and (file_hash(video) != data["video"]["sha256"] or file_hash(cover) != data["cover"]["sha256"]):
        raise ValueError("成品或封面已修改，请重新准备发布包。")
    variants = data.get("platform_covers", {}).get(platform, {})
    for ratio in ("4:3", "16:9"):
        if ratio not in variants:
            continue
        asset = variants[ratio]
        if Path(asset["file"]).name != asset["file"]:
            raise ValueError("发布包封面文件路径无效。")
        path = bundle / asset["file"]
        if not path.is_file() or (verify_hash and file_hash(path) != asset["sha256"]):
            raise ValueError("平台封面已修改或移动，请重新准备发布包。")
    return data


def publication_file_stamp(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def xiaohongshu_handoff(bundle):
    """Verify a local handoff without opening a browser or mutating the bundle."""
    bundle = clean_path(bundle)
    manifest = bundle / 'manifest.json'
    manifest_stamp = publication_file_stamp(manifest)
    data = load_bundle(bundle, XIAOHONGSHU, verify_hash=False, require_ai=True)
    video = clean_path(data['video']['path'])
    cover = bundle / 'cover.jpg'
    stamps = {manifest: manifest_stamp, video: publication_file_stamp(video),
              cover: publication_file_stamp(cover)}
    verified = load_bundle(bundle, XIAOHONGSHU, require_ai=True)
    if (verified != data or clean_path(verified['video']['path']) != video
            or any(publication_file_stamp(path) != stamp for path, stamp in stamps.items())):
        raise ValueError('核对期间发布资料或素材发生变化，请重新核对。')
    return {'fields': verified['content'][XIAOHONGSHU], 'video': video, 'cover': cover,
            'stamps': stamps}


def main():
    parser = argparse.ArgumentParser(description="从成品和原视频制作发布资料包")
    parser.add_argument("--job", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--content-json", required=True, help="各平台 title/description JSON 文件")
    parser.add_argument("--cover-seconds", type=float, default=20)
    parser.add_argument("--cover-file")
    parser.add_argument("--cover-mode", choices=("original", "frame"), default="original")
    args = parser.parse_args()
    content = json.loads(Path(args.content_json).read_text(encoding="utf-8-sig"))
    print(prepare(args.job, args.video, content, args.cover_seconds, args.cover_file, cover_mode=args.cover_mode), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
