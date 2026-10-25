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
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QApplication
import video_mixdown
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


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while predicate() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    application.processEvents()
    assert not predicate(), 'timed out waiting for Qt task'


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

    # Pre-render analysis is discoverable, cancellable, bound to both inputs,
    # and an accepted report is explicitly convertible into a manual offset.
    mixdown = work / 'mixdown.wav'
    mixdown.write_bytes(b'analysis fixture')

    def accepted_analysis(job, source, mix, *, reference=None, max_shift=30, reset_cancel=True):
        assert Path(job).is_dir() and reset_cancel is False
        assert reference is None and max_shift == 30
        return {
            'offset_seconds': .25,
            'accepted': True,
            'method': 'normalized_stereo_energy_correlation',
            'correlation': .93,
            'margin': .17,
            'overlap_seconds': 12.0,
            'message': '匹配完成',
            'inputs': {
                str(Path(source).resolve()): file_hash(source),
                str(Path(mix).resolve()): file_hash(mix),
            },
        }

    page = VideoMixdownPage(owner)
    page.resize(1200, 900)
    page.show()
    page.fields['video'].setText(str(video))
    page.fields['mixdown'].setText(str(mixdown))
    page.fields['output'].setText(str(work / 'accepted.mp4'))
    assert page.alignment_analyze.text() == '分析音频对齐'
    assert page.alignment_analyze.objectName() == 'alignment-analyze'
    assert page.alignment_analyze.accessibleName() == '分析音频对齐'
    with patch.object(video_mixdown, 'analyze_alignment', accepted_analysis):
        page.alignment_analyze.click()
        assert page.alignment_busy() and page.preview_busy()
        wait_until(page.alignment_busy)
    assert page.alignment_result and page.alignment_result['accepted'] is True
    assert page.use_alignment_suggestion.isVisible() and page.use_alignment_suggestion.isEnabled()
    assert '+0.250' in page.alignment_state.text()
    assert 'correlation' not in page.alignment_state.text() and '.93' not in page.alignment_state.text()
    assert page.auto.isChecked() and page.offset.value() == 0
    page.use_alignment_suggestion.click()
    application.processEvents()
    assert not page.auto.isChecked() and abs(page.offset.value() - .25) < 1e-9
    assert '已使用建议偏移' in page.alignment_state.text() and '手动偏移' in page.alignment.text()

    captured = {}

    def fake_render(render_job, *, video, mixdown, output, reset_cancel=True, **options):
        captured.update(options)
        Path(output).write_bytes(b'rendered fixture')
        report = options.get('alignment_report') or accepted_analysis(
            render_job, video, mixdown, reset_cancel=reset_cancel)
        return {
            'output': str(Path(output).resolve()),
            'sha256': file_hash(output),
            'inputs': report.get('inputs', {}),
            'alignment': report,
            'normalized': False,
            'loudness_measurement': None,
        }

    # Returning to automatic mode reuses only the still-current validated report.
    page.auto.setChecked(True)
    with patch.object(video_mixdown, 'render', fake_render):
        page.render()
        wait_until(page.busy)
    assert captured.get('offset') is None
    assert captured.get('alignment_report', {}).get('accepted') is True, (captured, page.status.text())
    assert page.result and page.to_publish.isEnabled()
    assert page.preview_result is None, 'Analysis must not manufacture a still preview'
    page.close(); application.processEvents()

    def low_confidence_analysis(job, source, mix, *, reference=None, max_shift=30, reset_cancel=True):
        return {
            'offset_seconds': .18,
            'accepted': False,
            'method': 'normalized_stereo_energy_correlation',
            'correlation': .44,
            'margin': .03,
            'overlap_seconds': 12.0,
            'message': '存在相近的多个峰值，无法唯一确认偏移。',
            'inputs': {
                str(Path(source).resolve()): file_hash(source),
                str(Path(mix).resolve()): file_hash(mix),
            },
        }

    page = VideoMixdownPage(owner)
    page.fields['video'].setText(str(video))
    page.fields['mixdown'].setText(str(mixdown))
    with patch.object(video_mixdown, 'analyze_alignment', low_confidence_analysis):
        page.alignment_analyze.click()
        wait_until(page.alignment_busy)
    assert page.auto.isChecked() and page.offset.value() == 0
    assert page.use_alignment_suggestion.isHidden()
    assert '+0.180' in page.alignment_state.text() and '未自动采用' in page.alignment_state.text()
    assert 'correlation' not in page.alignment_state.text() and '相关度' not in page.alignment_state.text()
    assert page.open_mixdown.isEnabled()

    entered = threading.Event()

    def blocked_analysis(job, source, mix, *, reference=None, max_shift=30, reset_cancel=True):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (Path(job) / 'cancel.flag').is_file():
                raise RuntimeError('cancelled fixture')
            time.sleep(.01)
        raise RuntimeError('analysis fixture timeout')

    other_mixdown = work / 'other.wav'
    other_mixdown.write_bytes(b'changed analysis input')
    with patch.object(video_mixdown, 'analyze_alignment', blocked_analysis):
        page.alignment_analyze.click()
        assert entered.wait(5)
        assert not page.alignment_stop.isHidden() and not page.alignment_analyze.isEnabled()
        assert not page.preview_button.isEnabled() and not page.start.isEnabled()
        assert not page.choose_task_button.isEnabled()
        page.preview_still()
        assert page.preview_task is None and '等待音频对齐分析' in page.preview_state.text()
        started = time.monotonic()
        page.fields['mixdown'].setText(str(other_mixdown))
        assert time.monotonic() - started < .2, 'Input invalidation blocked the GUI'
        wait_until(page.alignment_busy)
    assert page.alignment_result is None and page.use_alignment_suggestion.isHidden()
    assert '已变化' in page.alignment_state.text()
    page.close(); application.processEvents()

    # Closing the page cancels the owned analysis while keeping the source files.
    page = VideoMixdownPage(owner)
    page.fields['video'].setText(str(video))
    page.fields['mixdown'].setText(str(mixdown))
    entered.clear()
    with patch.object(video_mixdown, 'analyze_alignment', blocked_analysis):
        page.alignment_analyze.click()
        assert entered.wait(5)
        event = QCloseEvent()
        page.closeEvent(event)
        assert not event.isAccepted()
        wait_until(page.alignment_busy)
    assert page.alignment_result is None and '已取消' in page.alignment_state.text()
    assert file_hash(video) == original
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


