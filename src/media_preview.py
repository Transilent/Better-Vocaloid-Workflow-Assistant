"""Independent, cancellable still-frame previews for the video workflow.

This module intentionally has no dependency on ``video_mixdown.render`` or the
task contract. A preview gets its own UUID directory and process, and a caller
can discard that directory without affecting formal outputs or job bindings.
"""
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from common import ROOT, load_config


class PreviewError(RuntimeError):
    """A still preview could not be produced."""


class PreviewCancelled(PreviewError):
    """The caller cancelled the still preview."""


def preview_cache_root():
    return Path(ROOT) / "cache" / "video-preview"


def _is_reparse_point(path):
    """Return true for a symlink, junction or other Windows reparse entry."""
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return (path.is_symlink() or path.is_junction()
            or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _normal_cache_root(cache_root=None):
    base = Path(cache_root) if cache_root is not None else preview_cache_root()
    base.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(base):
        raise ValueError("视频预览缓存根目录不能是符号链接或目录联接。")
    return base.resolve()


def _valid_token(token):
    if not isinstance(token, str) or not token.startswith("preview-"):
        return False
    suffix = token[len("preview-"):]
    return len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix)


def new_preview_job(cache_root=None):
    """Return ``(token, job, output)`` for one UUID-owned preview."""
    base = _normal_cache_root(cache_root)
    token = "preview-" + uuid.uuid4().hex
    job = base / token
    job.mkdir()
    return token, job, job / "still.png"


def discard_preview_job(job, cache_root=None):
    """Remove one UUID preview job without following links or deleting extras.

    Only the files this module owns may exist at the job root. Unknown entries,
    subdirectories, symlinks and junctions make cleanup fail closed.
    """
    base = (Path(cache_root) if cache_root is not None else preview_cache_root())
    base.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(base):
        raise ValueError("视频预览缓存根目录不能是符号链接或目录联接。")
    base = base.resolve()
    job = Path(job).absolute()
    if not _valid_token(job.name):
        raise ValueError("视频预览目录必须使用 preview-<32位UUID> 名称。")
    if _is_reparse_point(job):
        raise ValueError("拒绝清理符号链接或目录联接形式的视频预览。")
    if not job.exists():
        return
    if not job.is_dir() or job.parent.resolve() != base:
        raise ValueError("只能清理预览缓存根目录下的 UUID 直接子目录。")
    allowed = {"still.png", "still.part", "ffmpeg.log", "run.lock"}
    children = list(job.iterdir())
    if any(child.name not in allowed or _is_reparse_point(child) or not child.is_file()
           for child in children):
        raise ValueError("视频预览目录包含非本模块文件，已拒绝清理。")
    for child in children:
        child.unlink()
    job.rmdir()


def _input_signature(path):
    info = Path(path).stat()
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns}


def _terminate_owned(process):
    """Terminate and reap a process from its worker thread."""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def extract_still(video, output, *, cancel_event=None, ffmpeg=None, process_changed=None):
    """Extract one real video frame using the configured FFmpeg executable.

    FFmpeg writes PNG bytes to a binary file handle opened by Python rather than
    to a deep output path. This avoids Windows path/sequence ambiguity and pipe
    backpressure. ``ffprobe`` is deliberately unused.
    """
    video = Path(video).resolve()
    output = Path(output).resolve()
    if not video.is_file():
        raise FileNotFoundError("原视频不存在，无法生成静帧预览。")
    if output.suffix.lower() != ".png":
        raise ValueError("静帧预览输出必须为 PNG。")
    if output.exists():
        raise FileExistsError("预览目标已存在；预览缓存必须使用新的 UUID 目录。")

    cancel_event = cancel_event or threading.Event()
    if cancel_event.is_set():
        raise PreviewCancelled("静帧预览已取消。")

    executable = Path(ffmpeg or load_config()["ffmpeg"])
    if not executable.is_file():
        raise FileNotFoundError("配置的 FFmpeg 不存在，请先在设置中检查组件。")

    before = _input_signature(video)
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name("still.part")
    log_path = output.with_name("ffmpeg.log")
    if pending.exists() or log_path.exists():
        raise FileExistsError("预览缓存不是空目录，拒绝覆盖。")
    argv = [
        str(executable), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-map", "0:v:0", "-frames:v", "1", "-an",
        "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease",
        "-c:v", "png", "-f", "image2pipe", "pipe:1",
    ]
    process = None
    try:
        # Handles drain immediately into files; no unread stdout/stderr pipe can
        # fill and stall FFmpeg in a deeply nested Windows test sandbox.
        with pending.open("wb") as frame_writer, log_path.open("wb") as log_writer:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=frame_writer,
                stderr=log_writer,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if process_changed is not None:
                process_changed(process)
            if cancel_event.is_set():
                _terminate_owned(process)
                raise PreviewCancelled("静帧预览已取消。")

            deadline = time.monotonic() + 90.0
            while process.poll() is None:
                if cancel_event.is_set():
                    _terminate_owned(process)
                    raise PreviewCancelled("静帧预览已取消。")
                if time.monotonic() >= deadline:
                    _terminate_owned(process)
                    raise PreviewError("静帧预览超时，请检查视频是否可解码。")
                time.sleep(0.05)

        if cancel_event.is_set():
            raise PreviewCancelled("静帧预览已取消。")
        if process.returncode:
            detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
            detail = detail.splitlines()[-1] if detail else "FFmpeg 未能读取视频帧"
            raise PreviewError("静帧预览失败：" + detail)
        if not pending.is_file() or pending.stat().st_size <= 8:
            detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
            raise PreviewError("FFmpeg 未生成有效 PNG。" + ((" " + detail.splitlines()[-1]) if detail else ""))
        with pending.open("rb") as reader:
            if reader.read(8) != b"\x89PNG\r\n\x1a\n":
                raise PreviewError("FFmpeg 输出的静帧不是有效 PNG。")
        if _input_signature(video) != before:
            raise PreviewError("读取静帧期间原视频发生变化，请重新选择后再预览。")
        pending.replace(output)
        return {
            "output": str(output),
            "video": str(video),
            "position_seconds": 0.0,
            "method": "ffmpeg_first_frame",
            "input_signature": before,
        }
    finally:
        if process_changed is not None:
            process_changed(None)
        if process is not None and process.poll() is None:
            _terminate_owned(process)
        for temporary in (pending, log_path):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class PreviewTask(QThread):
    """QThread wrapper with an owned process and frozen input snapshot."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, video, *, source_job=None, cache_root=None, ffmpeg=None, parent=None):
        super().__init__(parent)
        video = Path(video).resolve()
        token, job, output = new_preview_job(cache_root)
        self.token = token
        self.job = job
        self.output = output
        self.video = video
        self.source_job = Path(source_job).resolve() if source_job else None
        self.ffmpeg = ffmpeg
        self._cancel_event = threading.Event()
        self._process = None
        self._process_lock = threading.Lock()
        self.preview_inputs = {
            "source_job": str(self.source_job) if self.source_job else None,
            "video": str(video),
            "token": token,
            "preview_job": str(job),
            "cache": str(job),
            "output": str(output),
        }

    @property
    def cancel_event(self):
        return self._cancel_event

    def _set_process(self, process):
        with self._process_lock:
            self._process = process
        if process is not None and self._cancel_event.is_set():
            _terminate_owned(process)

    def cancel(self):
        """Set cancellation only; the worker owns termination and reaping."""
        self._cancel_event.set()

    def run(self):
        try:
            from pipeline import job_lock
            with job_lock(self.job):
                if self._cancel_event.is_set():
                    raise PreviewCancelled('静帧预览已取消。')
                result = extract_still(
                    self.video,
                    self.output,
                    cancel_event=self._cancel_event,
                    ffmpeg=self.ffmpeg,
                    process_changed=self._set_process,
                )
            if self._cancel_event.is_set():
                raise PreviewCancelled("静帧预览已取消。")
            result.update({
                "token": self.token,
                "preview_job": str(self.job),
                "source_job": str(self.source_job) if self.source_job else None,
            })
            self.succeeded.emit(result)
        except PreviewCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


