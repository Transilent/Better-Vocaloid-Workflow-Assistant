"""Independent still-preview extraction, run lock, cancellation and cleanup."""
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import file_hash, load_config
import media_preview
from media_preview import (PreviewCancelled, PreviewTask, discard_preview_job, extract_still,
                           new_preview_job)
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from video_mixdown_ui import VideoMixdownPage
application = QApplication([])

ffmpeg = load_config()["ffmpeg"]


def run_ffmpeg(*arguments):
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, arguments)],
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result


with tempfile.TemporaryDirectory(dir=APP / "work", prefix="video-preview-") as folder:
    work = Path(folder)
    video = work / "source.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "color=c=0x735AF7:s=160x90:r=2:d=1",
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
    original = file_hash(video)

    cache = work / "cache"
    token_a, job_a, still_a = new_preview_job(cache)
    token_b, job_b, still_b = new_preview_job(cache)
    assert token_a != token_b and job_a != job_b and still_a != still_b
    assert job_a.parent == cache.resolve() and job_b.parent == cache.resolve()
    assert token_a.startswith("preview-") and len(token_a) == len("preview-") + 32

    result = extract_still(video, still_a)
    assert Path(result["output"]) == still_a.resolve()
    assert result["method"] == "ffmpeg_first_frame" and result["position_seconds"] == 0.0
    assert file_hash(video) == original, "Preview modified the source video"
    data = still_a.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert 0 < width <= 1280 and height > 0, (width, height)

    # Real Qt queued delivery must release its task handle, display the frame,
    # and remain separate from the formal result / publication gate.
    owner = SimpleNamespace(storage=SimpleNamespace(busy=lambda: False),
                            updates=SimpleNamespace(busy=lambda: False))
    page = VideoMixdownPage(owner)
    page.resize(1200, 850)
    page.show()
    page.fields['video'].setText(str(video))
    page.preview_still()
    deadline = time.monotonic() + 20
    while page.preview_busy() and time.monotonic() < deadline:
        application.processEvents(); QTest.qWait(10)
    application.processEvents()
    assert not page.preview_busy() and page.preview_task is None
    assert page.preview_result and not page.preview._source.isNull(), page.preview_state.text()
    assert page.result is None and not page.to_publish.isEnabled()
    finished_cache = Path(page.preview_result['preview_job'])
    entered = threading.Event()
    def wait_for_cancel(*args, cancel_event, **kwargs):
        entered.set()
        assert cancel_event.wait(5), 'cancel did not reach the worker'
        raise PreviewCancelled('cancelled fixture')
    with patch.object(media_preview, 'extract_still', side_effect=wait_for_cancel):
        page.preview_still()
        assert entered.wait(5)
        started = time.monotonic(); page.cancel_preview()
        assert time.monotonic() - started < 0.2, 'GUI cancellation blocked'
        deadline = time.monotonic() + 10
        while page.preview_busy() and time.monotonic() < deadline:
            application.processEvents(); QTest.qWait(10)
    application.processEvents()
    assert not page.preview_busy() and page.preview_task is None and page.preview_result is None
    assert page.result is None and not page.to_publish.isEnabled()
    assert not finished_cache.exists() and file_hash(video) == original
    page.close(); application.processEvents()

    task = PreviewTask(video, source_job=work, cache_root=cache)
    inputs = dict(task.preview_inputs)
    assert inputs["source_job"] == str(work.resolve())
    assert inputs["video"] == str(video.resolve())
    assert inputs["token"] == task.token and inputs["preview_job"] == str(task.job)
    assert task.job.parent == cache.resolve()
    task.run()
    assert task.output.is_file() and (task.job / "run.lock").is_file()
    from pipeline import job_lock
    with job_lock(task.job):
        pass

    cancelled = threading.Event()
    cancelled.set()
    try:
        extract_still(video, still_b, cancel_event=cancelled)
        raise AssertionError("A pre-cancelled preview extracted a frame")
    except PreviewCancelled:
        pass
    assert not still_b.exists()

    extra_token, extra_job, _ = new_preview_job(cache)
    (extra_job / "unexpected.txt").write_text("must not be removed", encoding="utf-8")
    try:
        discard_preview_job(extra_job, cache)
        raise AssertionError("Preview cleanup accepted an unknown file")
    except ValueError:
        pass
    (extra_job / "unexpected.txt").unlink()
    discard_preview_job(extra_job, cache)

    wrong_name = cache / ("not-preview-" + uuid.uuid4().hex)
    wrong_name.mkdir()
    try:
        discard_preview_job(wrong_name, cache)
        raise AssertionError("Preview cleanup accepted a non-UUID directory")
    except ValueError:
        pass
    wrong_name.rmdir()

    import _winapi
    junction = cache / ("preview-" + uuid.uuid4().hex)
    try:
        _winapi.CreateJunction(str(job_b), str(junction))
    except OSError:
        # The unified runner normally permits junctions; the managed desktop
        # sandbox can deny reparse creation. Exercise the same fail-closed branch.
        if junction.exists():
            os.rmdir(junction)
        junction.mkdir()
        original_reparse = media_preview._is_reparse_point
        with patch.object(media_preview, "_is_reparse_point",
                          side_effect=lambda path: Path(path) == junction or original_reparse(path)):
            try:
                discard_preview_job(junction, cache)
                raise AssertionError("Preview cleanup followed a directory junction")
            except ValueError:
                pass
        junction.rmdir()
    else:
        try:
            try:
                discard_preview_job(junction, cache)
                raise AssertionError("Preview cleanup followed a directory junction")
            except ValueError:
                pass
        finally:
            assert junction.is_junction()
            os.rmdir(junction)

    discard_preview_job(job_a, cache)
    discard_preview_job(job_b, cache)
    discard_preview_job(task.job, cache)
    try:
        discard_preview_job(work, cache)
        raise AssertionError("Preview cleanup accepted an unrelated directory")
    except ValueError:
        pass

print("PASS: real configured-FFmpeg first frame, stable input, UUID/run.lock lifecycle, cancellation and fail-closed cleanup")


