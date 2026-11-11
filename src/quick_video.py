"""Resident Quick video controller.

The QML page projects this object. Source selection, alignment and rendering are
separate commands; rendering a new MP4 never registers it.  A task-backed video
is registered only by an explicit command against the currently bound, verified
task Mixdown (and a registered source video when one was actually used).
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import importlib
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import publication
import video_mixdown
from common import ROOT, file_hash
from i18n import tr

_VIDEO_KINDS = frozenset(('video-analyze', 'video-adopt', 'video-render', 'video-register',
                          'video-preview', 'video-publish'))
_ACTIVE_STATES = {'video-analyze': 'analyzing', 'video-render': 'rendering',
                  'video-preview': 'previewing', 'video-adopt': 'adopting'}
_OWNED_JOB = re.compile(r'task-[0-9a-f]{32}\Z')


def _local_path(value: Any, *, allow_empty: bool = False) -> Path | None:
    if isinstance(value, QUrl):
        if value.isEmpty():
            if allow_empty:
                return None
            raise ValueError('文件路径为空。')
        if not value.isLocalFile():
            raise ValueError('请选择本机文件。')
        text = value.toLocalFile()
    else:
        text = str(value or '')
    text = text.strip().strip('"').strip()
    if not text:
        if allow_empty:
            return None
        raise ValueError('文件路径为空。')
    return Path(text).expanduser().resolve()


def _path_key(value: Any) -> str:
    if value in (None, ''):
        return ''
    try:
        text = str(Path(value).expanduser().resolve())
    except (OSError, TypeError, ValueError):
        text = str(value)
    return os.path.normcase(text)


def _same_path(left: Any, right: Any) -> bool:
    return bool(left and right and _path_key(left) == _path_key(right))


def _stop_check(stop) -> None:
    if stop is not None and stop.is_set():
        raise InterruptedError('已取消视频操作。')


def _row_identity(row: dict[str, Any] | None) -> tuple[str, str, str]:
    if not isinstance(row, dict):
        return '', '', ''
    return (str(row.get('id') or ''), str(row.get('revision') or ''),
            str(row.get('sha256') or ''))


def _freeze_row(value: Any, role: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f'缺少已核验的 {role} 产物条目。')
    row = deepcopy(value)
    if role and row.get('role') != role:
        raise ValueError(f'产物角色必须是 {role}。')
    if row.get('state') != 'verified' or row.get('available') is False:
        raise ValueError(f'{role} 产物尚未通过当前校验。')
    path = str(row.get('path') or '').strip()
    identifier = str(row.get('id') or '').strip()
    revision = str(row.get('revision') or '').strip()
    digest = str(row.get('sha256') or '').strip()
    if not path or not identifier or not revision or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError(f'{role} 产物缺少完整身份或 SHA-256。')
    row.update(path=path, id=identifier, revision=revision, sha256=digest)
    return row


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _same_alignment_inputs(expected: Any, current: dict[str, dict[str, str]]) -> bool:
    """Check the analysis' immutable input records against current files."""
    if not isinstance(expected, dict):
        return False
    for role, record in current.items():
        saved = expected.get(role)
        if not isinstance(saved, dict):
            return False
        try:
            same_path = Path(saved.get('path', '')).resolve() == Path(record['path']).resolve()
        except (OSError, TypeError, ValueError):
            return False
        if not same_path or saved.get('sha256') != record['sha256']:
            return False
    return True


class QuickVideo(QObject):
    """Qt projection for a guided video alignment and immutable MP4 workflow."""

    changed = Signal()
    publishRequested = Signal(str, dict)

    def __init__(self, owner, worker):
        super().__init__(owner)
        self._owner = owner
        self._worker = worker
        self._closed = False
        self._active_kind = ''
        self._active_job: Path | None = None
        self._owned_jobs: set[str] = set()
        self._job_path: Path | None = None
        self._mix_pin: dict[str, Any] | None = None
        self._source_pin: dict[str, Any] | None = None
        self._source_video = ''
        self._mixdown = ''
        self._output = ''
        self._offset = 0.0
        self._auto_align = True
        self._normalize = False
        self._state = 'idle'
        self._message = '选择原视频和 Mixdown；自动对齐后生成新的 MP4。'
        self._alignment: dict[str, Any] | None = None
        self._preview = ''
        self._result = ''
        self._result_sha256 = ''
        self._result_signature: tuple[int, int] | None = None
        self._result_token: tuple[Any, ...] | None = None
        self._result_receipt: dict[str, Any] | None = None
        self._registered_row: dict[str, Any] | None = None
        self._registered_sha256 = ''
        self._worker.changed.connect(self._emit)

    @Property('QVariantMap', notify=changed)
    def view(self) -> dict[str, Any]:
        busy = bool(self._worker.kind or self._active_kind)
        source_ok = bool(self._source_video and Path(self._source_video).is_file())
        mix_ok = bool(self._mixdown and Path(self._mixdown).is_file())
        output = Path(self._output) if self._output else None
        output_ok = bool(output and output.suffix.casefold() == '.mp4'
                         and not output.exists()
                         and not _same_path(output, self._source_video)
                         and not _same_path(output, self._mixdown))
        result_present = bool(self._result and Path(self._result).is_file())
        result_fresh = result_present and not self._is_result_stale()
        job_known = bool(self._job_path)
        task_mix_ok = bool(job_known and self._mix_pin
                           and _same_path(self._mixdown, self._mix_pin.get('path')))
        receipt = self._result_receipt or {}
        receipt_mix = receipt.get('mixdown') if isinstance(receipt.get('mixdown'), dict) else {}
        receipt_has_task_mix = bool(receipt_mix.get('artifact'))
        can_register = bool(job_known and result_fresh and receipt_has_task_mix
                            and task_mix_ok and not busy and not self._registered_row)
        can_publish = bool(job_known and result_fresh and self._registered_row
                           and self._registered_sha256
                           and self._registered_sha256 == self._result_sha256 and not busy)
        return {
            'job': str(self._job_path or ''),
            'sourceVideo': self._source_video,
            'mixdown': self._mixdown,
            'output': self._output,
            'offset': float(self._offset),
            'autoAlign': bool(self._auto_align),
            'normalize': bool(self._normalize),
            'state': self._state,
            'message': self._message,
            'busy': busy,
            'preview': self._preview,
            'result': self._result,
            'resultStale': self._is_result_stale(),
            'canAnalyze': bool(source_ok and mix_ok and not busy),
            'canRender': bool(source_ok and mix_ok and output_ok and not busy),
            'canRegister': can_register,
            'canPublish': can_publish,
            'canCancel': bool((self._worker.kind or self._active_kind) in _VIDEO_KINDS),
            'alignmentSummary': self._alignment_summary(),
            'canAdoptSuggestion': self._can_adopt_suggestion(),
            'resultSha256': self._result_sha256,
            'receipt': deepcopy(self._result_receipt) if self._result_receipt else {},
        }

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def _emit(self) -> None:
        self.changed.emit()

    def _notice(self, message: str) -> None:
        self._message = str(message or '')[:1600]
        self._emit()

    def _alignment_summary(self) -> str:
        report = self._alignment
        if not isinstance(report, dict):
            return ''
        try:
            offset = float(report.get('offset_seconds', 0) or 0)
        except (TypeError, ValueError):
            offset = 0.0
        method = str(report.get('method') or '')
        if method == 'manual':
            return f'手动偏移 {offset:+.3f} 秒'
        if report.get('accepted') is False:
            detail = str(report.get('message') or report.get('reason_code') or '结果不够明确')
            return f'自动对齐未采用，可试听后手动填写偏移：{detail}'
        confidence = report.get('correlation')
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            value = None
        text = f'自动对齐 {offset:+.3f} 秒'
        if value is not None:
            text += f' · 相关度 {value:.3f}'
        if method:
            text += f' · {method}'
        return text

    def _alignment_offset(self) -> float | None:
        report = self._alignment
        if (not isinstance(report, dict) or report.get('accepted') is not True
                or report.get('method') != video_mixdown.ALIGNMENT_METHOD):
            return None
        try:
            offset = float(report.get('offset_seconds'))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(offset) or abs(offset) > 3600:
            return None
        return offset

    def _can_adopt_suggestion(self) -> bool:
        return bool(not self._worker.kind and not self._active_kind and self._auto_align
                    and self._source_video and Path(self._source_video).is_file()
                    and self._mixdown and Path(self._mixdown).is_file()
                    and self._alignment_offset() is not None)

    def _media_token(self) -> tuple[Any, ...]:
        return (
            _path_key(self._job_path),
            _row_identity(self._mix_pin),
            _row_identity(self._source_pin),
            _path_key(self._source_video),
            _path_key(self._mixdown),
        )

    def _form_token(self) -> tuple[Any, ...]:
        return self._media_token() + (
            _path_key(self._output),
            None if self._auto_align else round(float(self._offset), 6),
            bool(self._auto_align),
            bool(self._normalize),
        )

    def _is_result_stale(self) -> bool:
        if not self._result or not self._result_receipt:
            return False
        if self._result_token != self._form_token():
            return True
        if not Path(self._result).is_file():
            return True
        return self._result_signature is not None and _signature(Path(self._result)) != self._result_signature

    @Slot(str, QUrl)
    def chooseFile(self, key: str, url):
        if self._closed or self._worker.kind or self._active_kind:
            return
        if key not in ('sourceVideo', 'mixdown', 'output'):
            self._notice('未知的视频文件字段。')
            return
        try:
            path = _local_path(url, allow_empty=True)
        except (OSError, ValueError) as error:
            self._notice(str(error))
            return
        text = '' if path is None else str(path)
        media_changed = False
        if key == 'sourceVideo':
            media_changed = not _same_path(self._source_video, text)
            self._source_video = text
        elif key == 'mixdown':
            media_changed = not _same_path(self._mixdown, text)
            self._mixdown = text
        else:
            if text and Path(text).suffix.casefold() != '.mp4':
                self._notice('成品必须使用 .mp4 文件名。')
                return
            self._output = text
        if media_changed:
            self._alignment = None
            self._preview = ''
        self._state = 'ready'
        if self._result:
            self._message = ('输入已修改；旧 MP4 保留，但当前结果不可登记。'
                             if self._is_result_stale() else '可视化检查所选输入。')
        else:
            self._message = '文件已选择；可以自动分析对齐后生成新视频。'
        self._emit()

    @Slot(str, 'QVariant')
    def setOption(self, key: str, value):
        if self._closed or self._worker.kind or self._active_kind:
            return
        if key == 'offset':
            try:
                number = float(value)
            except (TypeError, ValueError):
                self._notice('手动偏移必须是秒数。')
                return
            if not math.isfinite(number) or not -3600 <= number <= 3600:
                self._notice('手动偏移应在 -3600 到 3600 秒之间。')
                return
            self._offset = number
        elif key == 'autoAlign':
            self._auto_align = bool(value)
        elif key == 'normalize':
            self._normalize = bool(value)
        else:
            self._notice('未知的视频选项。')
            return
        self._state = 'ready'
        self._message = ('设置已修改；旧 MP4 保留，但需要重新合成后才能登记。'
                         if self._is_result_stale() else '设置已更新。')
        self._emit()

    def accept_task(self, job, mix_row, source_row=None) -> bool:
        """Freeze task rows reverified by the shared worker lane."""
        if self._closed or self._worker.kind or self._active_kind:
            self._notice('后台视频操作进行中，暂时不能切换任务。')
            return False
        try:
            job_path = Path(job).expanduser().resolve()
            mix = _freeze_row(mix_row, 'mixdown')
            if Path(mix['path']).suffix.casefold() != '.wav':
                raise ValueError('任务 Mixdown 必须是已核验的 WAV。')
            source = _freeze_row(source_row, 'source_video') if source_row is not None else None
        except (OSError, TypeError, ValueError) as error:
            self._notice('无法载入任务视频参数：' + str(error))
            return False
        if (job_path == self._job_path and _row_identity(mix) == _row_identity(self._mix_pin)
                and _row_identity(source) == _row_identity(self._source_pin)):
            return True
        self._job_path = job_path
        self._mix_pin = mix
        self._source_pin = source
        self._mixdown = mix['path']
        self._source_video = source['path'] if source else ''
        output = job_path / 'rendered' / 'cover-mixdown.mp4'
        if output.exists():
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            output = output.with_stem(f'cover-mixdown-{stamp}')
        self._output = str(output)
        self._alignment = None
        self._preview = ''
        self._result = ''
        self._result_sha256 = ''
        self._result_signature = None
        self._result_token = None
        self._result_receipt = None
        self._registered_row = None
        self._registered_sha256 = ''
        self._state = 'ready'
        self._message = '已冻结任务当前绑定的 Mixdown；合成与登记将再次核验同一产物。'
        self._emit()
        return True

    def _new_owned_job(self) -> Path:
        root = (Path(ROOT) / 'cache' / 'video-mixdown').resolve()
        root.mkdir(parents=True, exist_ok=True)
        for _attempt in range(8):
            job = root / ('task-' + uuid.uuid4().hex)
            try:
                job.mkdir()
            except FileExistsError:
                continue
            self._owned_jobs.add(str(job))
            return job
        raise RuntimeError('无法创建独立视频缓存任务。')

    def _discard_owned_job(self, job: Any) -> None:
        if not job:
            return
        try:
            path = Path(job).resolve()
            root = (Path(ROOT) / 'cache' / 'video-mixdown').resolve()
        except (OSError, TypeError, ValueError):
            return
        if path.parent != root or not _OWNED_JOB.fullmatch(path.name):
            return
        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError:
                return
        self._owned_jobs.discard(str(path))

    def _cleanup_owned_jobs(self) -> None:
        for value in tuple(self._owned_jobs):
            self._discard_owned_job(value)

    def _start(self, kind: str, label: str, work, finish) -> bool:
        if self._closed or self._worker.kind or self._active_kind:
            self._notice('工作通道正被其他后台操作占用。')
            return False
        self._active_kind = kind
        self._state = _ACTIVE_STATES.get(kind, 'running')
        self._message = label
        self._emit()

        def progress(value):
            if isinstance(value, dict):
                text = value.get('message') or value.get('detail') or ''
            else:
                text = value
            if text:
                self._message = str(text)[:1600]
                self._emit()

        def complete(value, error):
            self._active_kind = ''
            self._active_job = None
            if error is not None:
                if isinstance(error, InterruptedError):
                    self._state = 'cancelled'
                    self._message = '视频操作已取消；未完成的文件没有被当作成品。'
                else:
                    self._state = 'failed'
                    self._message = str(error)[:1600] or error.__class__.__name__
            else:
                try:
                    finish(value)
                except Exception as failure:
                    self._state = 'failed'
                    self._message = str(failure)[:1600]
            if self._closed:
                self._cleanup_owned_jobs()
            self._emit()

        if not self._worker.start(kind, work, complete, progress):
            self._active_kind = ''
            self._active_job = None
            self._state = 'ready'
            self._notice('工作通道正被其他后台操作占用。')
            return False
        return True

    @staticmethod
    def _handoff():
        try:
            return importlib.import_module('quick_media_handoff')
        except ImportError as error:
            raise ValueError('媒体交接校验模块不可用；本次没有读取或写入任务产物。') from error

    def _verify_pinned_rows(self, stop) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not self._job_path or (self._mix_pin is None and self._source_pin is None):
            return None, None
        handoff = self._handoff()
        verify = getattr(handoff, 'verify_task_artifact', None)
        if not callable(verify):
            raise ValueError('媒体交接校验接口不可用；本次没有读取或写入任务产物。')
        mix = source = None
        if self._mix_pin is not None and _same_path(self._mixdown, self._mix_pin['path']):
            mix = _freeze_row(verify(str(self._job_path), deepcopy(self._mix_pin), 'mixdown',
                                     check=stop.is_set), 'mixdown')
        _stop_check(stop)
        if self._source_pin is not None and _same_path(self._source_video, self._source_pin['path']):
            source = _freeze_row(verify(str(self._job_path), deepcopy(self._source_pin), 'source_video',
                                        check=stop.is_set), 'source_video')
        _stop_check(stop)
        return mix, source

    def _resolve_used_inputs(self, stop, source_value: str, mix_value: str):
        verified_mix, verified_source = self._verify_pinned_rows(stop)
        source = Path(source_value).expanduser().resolve() if source_value else None
        mix = Path(mix_value).expanduser().resolve() if mix_value else None
        if source is None or not source.is_file():
            raise ValueError('请选择可读取的原视频。')
        if mix is None or not mix.is_file():
            raise ValueError('请选择可读取的 Mixdown。')
        used_mix = verified_mix if verified_mix and _same_path(mix, self._mix_pin.get('path')) else None
        used_source = (verified_source if verified_source and _same_path(source, self._source_pin.get('path'))
                       else None)
        return source, mix, used_mix, used_source

    @Slot(float)
    def previewFrame(self, seconds: float):
        if self._closed or self._worker.kind or self._active_kind:
            return
        if not self._source_video or not Path(self._source_video).is_file():
            self._notice('请先选择可读取的原视频。')
            return
        try:
            seconds = float(seconds)
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('预览时间必须是非负秒数。')
            source = Path(self._source_video).expanduser().resolve()
            job = self._new_owned_job()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._notice(str(error))
            return
        self._active_job = job

        def work(stop, send):
            before = _signature(source)
            send('正在读取视频静帧…')
            destination = job / 'preview.jpg'
            cover = publication.extract_cover(source, destination, seconds)
            _stop_check(stop)
            if _signature(source) != before:
                raise ValueError('预览期间原视频发生变化，请重新选择后再试。')
            return {'url': QUrl.fromLocalFile(str(destination.resolve())).toString(),
                    'cover': cover}

        def finish(value):
            self._preview = str(value['url'])
            self._state = 'done' if self._result else 'ready'
            self._message = '静帧已更新；这不会写入或登记正式成品。'

        if not self._start('video-preview', '正在生成独立静帧预览…', work, finish):
            self._discard_owned_job(job)

    @Slot()
    def analyze(self):
        if self._worker.kind or self._active_kind:
            self._notice('已有后台操作正在运行。')
            return
        source_value, mix_value = self._source_video, self._mixdown
        if not source_value or not mix_value:
            self._notice('请先选择原视频和 Mixdown。')
            return
        token = self._media_token()
        try:
            job = self._new_owned_job()
        except (OSError, RuntimeError) as error:
            self._notice(str(error))
            return
        self._active_job = job
        self._alignment = None

        def work(stop, send):
            source, mix, _used_mix, _used_source = self._resolve_used_inputs(
                stop, source_value, mix_value)
            send('正在解码并比较参考音频与 Mixdown…')
            report = video_mixdown.analyze_alignment(
                str(job), str(source), str(mix), reset_cancel=False)
            _stop_check(stop)
            return report

        def finish(report):
            if token != self._media_token():
                self._state = 'ready'
                self._message = '对齐已完成，但输入在分析期间发生变化；请重新分析。'
                return
            self._alignment = deepcopy(report)
            try:
                offset = float(report.get('offset_seconds', 0) or 0)
            except (TypeError, ValueError):
                offset = 0.0
            if report.get('accepted') is False:
                self._state = 'needs_manual'
                self._message = (f'自动对齐候选 {offset:+.3f} 秒，但没有达到可靠阈值；'
                                 '请试听后改用手动偏移。')
            else:
                self._state = 'analyzed'
                self._message = self._alignment_summary() + '；确认后可生成新 MP4。'

        if not self._start('video-analyze', '正在冻结任务输入并分析音频对齐…', work, finish):
            self._discard_owned_job(job)

    @Slot()
    def adoptSuggestion(self):
        if self._closed or self._worker.kind or self._active_kind:
            self._notice('已有后台操作正在运行。')
            return
        if not self._auto_align:
            self._notice('当前已经是手动偏移，无需再次采用建议。')
            return
        report = deepcopy(self._alignment)
        offset = self._alignment_offset()
        if offset is None:
            self._notice('当前没有可用且可靠的对齐建议，请重新分析或手动填写偏移。')
            return
        try:
            source = Path(self._source_video).expanduser().resolve()
            mix = Path(self._mixdown).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            self._notice('当前原视频或 Mixdown 路径无效，请重新选择。')
            return
        token = self._media_token()
        expected_inputs = report.get('input_fingerprints')

        def work(stop, send):
            send('正在校验建议偏移仍对应当前原视频与 Mixdown…')
            current = {}
            for role, path in (('video', source), ('mixdown', mix)):
                _stop_check(stop)
                if not path.is_file():
                    raise ValueError('分析所对应的输入文件已不存在。')
                current[role] = {'path': str(path), 'sha256': file_hash(path, check=stop.is_set)}
            _stop_check(stop)
            if not _same_alignment_inputs(expected_inputs, current):
                raise ValueError('对齐分析已过期：原视频或 Mixdown 内容已经变化，请重新分析。')
            return {'offset': offset, 'token': token, 'alignment': report}

        def finish(value):
            if value['token'] != self._media_token() or self._alignment != value['alignment']:
                raise ValueError('采用建议前输入或分析结果已经变化，请重新分析。')
            self._auto_align = False
            self._offset = float(value['offset'])
            self._state = 'ready'
            self._message = (f'已显式采用建议偏移 {self._offset:+.3f} 秒，并切换为手动偏移；'
                             '合成时会再次使用当前输入校验。')

        self._start('video-adopt', '正在核验并采用建议偏移…', work, finish)

    def _receipt_from_report(self, report, job, source, mix, used_mix, used_source, output, options):
        if not isinstance(report, dict):
            raise ValueError('视频合成后端没有返回有效回执。')
        output = Path(output).expanduser().resolve()
        if not output.is_file():
            raise FileNotFoundError('视频合成后端未生成目标 MP4。')
        if not _same_path(report.get('output'), output):
            raise ValueError('视频合成后端返回了不同的成品路径。')
        input_hashes = report.get('inputs') if isinstance(report.get('inputs'), dict) else {}

        def digest_for(path):
            value = input_hashes.get(str(Path(path).resolve()))
            return str(value or '')
        source_sha = digest_for(source)
        mix_sha = digest_for(mix)
        if not source_sha or not mix_sha:
            raise ValueError('视频合成回执缺少原视频或 Mixdown 的文件 SHA-256。')
        if used_mix and mix_sha != used_mix['sha256']:
            raise ValueError('合成使用的 Mixdown 与冻结任务修订不一致。')
        if used_source and source_sha != used_source['sha256']:
            raise ValueError('合成使用的原视频与冻结任务修订不一致。')
        report_sha = str(report.get('sha256') or '').strip()
        if report_sha and not re.fullmatch(r'[0-9a-f]{64}', report_sha):
            raise ValueError('视频合成回执中的 SHA-256 无效。')
        dependencies = []
        if used_mix:
            dependencies.append({'id': used_mix['id'], 'revision': used_mix['revision']})
        if used_source:
            dependencies.append({'id': used_source['id'], 'revision': used_source['revision']})
        source_artifact = ({'id': used_source['id'], 'revision': used_source['revision'],
                            'sha256': used_source['sha256']} if used_source else None)
        mix_artifact = ({'id': used_mix['id'], 'revision': used_mix['revision'],
                         'sha256': used_mix['sha256']} if used_mix else None)
        return {
            'version': 1,
            'job': str(self._job_path or ''),
            'render_job': str(job),
            'output': str(output),
            'sha256': report_sha,
            'source_video': {'path': str(Path(source).resolve()), 'sha256': source_sha,
                             'artifact': source_artifact},
            'mixdown': {'path': str(Path(mix).resolve()), 'sha256': mix_sha,
                        'artifact': mix_artifact},
            'options': {'auto_align': bool(options['auto_align']),
                        'normalize': bool(options['normalize']),
                        'offset': None if options['auto_align'] else float(options['offset'])},
            'alignment': deepcopy(report.get('alignment') or {}),
            'normalized': bool(report.get('normalized')),
            'loudness_measurement': deepcopy(report.get('loudness_measurement')),
            'dependencies': dependencies,
        }

    @Slot()
    def render(self):
        if self._worker.kind or self._active_kind:
            self._notice('已有后台操作正在运行。')
            return
        source_value, mix_value, output_value = self._source_video, self._mixdown, self._output
        if not source_value or not mix_value or not output_value:
            self._notice('请选择原视频、Mixdown 和新的 MP4 保存位置。')
            return
        output = Path(output_value).expanduser().resolve()
        if output.suffix.casefold() != '.mp4':
            self._notice('成品必须使用 .mp4 文件名。')
            return
        if output.exists():
            self._notice('成品已存在；请选择新的文件名，旧 MP4 不会被覆盖。')
            return
        if _same_path(output, source_value) or _same_path(output, mix_value):
            self._notice('成品路径不能与输入文件相同。')
            return
        options = {'auto_align': bool(self._auto_align), 'normalize': bool(self._normalize),
                   'offset': float(self._offset)}
        alignment = deepcopy(self._alignment) if self._auto_align else None
        token = self._form_token()
        try:
            job = self._new_owned_job()
        except (OSError, RuntimeError) as error:
            self._notice(str(error))
            return
        self._active_job = job

        def work(stop, send):
            source, mix, used_mix, used_source = self._resolve_used_inputs(
                stop, source_value, mix_value)
            _stop_check(stop)
            send('正在冻结输入并准备独立 MP4…')
            arguments = {'normalize': bool(options['normalize']), 'reset_cancel': False}
            if options['auto_align']:
                if alignment and alignment.get('accepted') is True:
                    arguments['alignment_report'] = alignment
            else:
                arguments['offset'] = float(options['offset'])
            report = video_mixdown.render(
                str(job), str(source), str(mix), str(output), **arguments)
            _stop_check(stop)
            receipt = self._receipt_from_report(
                report, job, source, mix, used_mix, used_source, output, options)
            report_sha = str(report.get('sha256') or '').strip()
            cancelled_after_commit = False
            try:
                actual_sha = file_hash(output, check=stop.is_set)
            except InterruptedError:
                if not output.is_file() or not report_sha:
                    raise
                actual_sha = report_sha
                cancelled_after_commit = True
            if report_sha and actual_sha != report_sha:
                raise ValueError('成品 MP4 在回执生成后发生变化。')
            if not re.fullmatch(r'[0-9a-f]{64}', actual_sha):
                raise ValueError('成品 MP4 缺少有效 SHA-256。')
            receipt['sha256'] = actual_sha
            cancelled_after_commit = cancelled_after_commit or stop.is_set()
            return {'receipt': receipt, 'token': token,
                    'signature': _signature(output),
                    'cancelled_after_commit': cancelled_after_commit}

        def finish(value):
            receipt = deepcopy(value['receipt'])
            self._result = str(Path(receipt['output']).resolve())
            self._result_sha256 = receipt['sha256']
            self._result_signature = value['signature']
            self._result_token = value['token']
            self._result_receipt = receipt
            self._alignment = deepcopy(receipt.get('alignment') or self._alignment)
            self._registered_row = None
            self._registered_sha256 = ''
            stale = self._is_result_stale()
            if value.get('cancelled_after_commit'):
                self._state = 'partial'
                self._message = ('新的 MP4 已提交但停止请求随后到达；文件已保留，检查后可登记。'
                                 f' SHA-256：{self._result_sha256}')
            elif stale:
                self._state = 'ready'
                self._message = ('新的 MP4 已保留，但当前输入或设置已变化；'
                                 '旧结果不可登记，请重新合成。')
            elif not ((receipt.get('mixdown') or {}).get('artifact')):
                self._state = 'done'
                self._message = ('独立 MP4 已生成；手动替换的 Mixdown 不会自动登记到任务。'
                                 f' SHA-256：{self._result_sha256}')
            else:
                self._state = 'done'
                self._message = ('新的 MP4 已生成。播放检查对齐和响度后，'
                                 '再显式登记到任务或进入发布。'
                                 f' SHA-256：{self._result_sha256}')

        if not self._start('video-render', '正在对齐、合成并校验新的 MP4…', work, finish):
            self._discard_owned_job(job)

    @Slot()
    def cancel(self):
        if (self._worker.kind or self._active_kind) not in _VIDEO_KINDS:
            return
        if self._active_job is not None:
            try:
                (Path(self._active_job) / 'cancel.flag').parent.mkdir(parents=True, exist_ok=True)
                (Path(self._active_job) / 'cancel.flag').touch()
            except OSError:
                pass
        self._worker.cancel()
        self._message = '正在停止视频操作…'
        self._emit()

    @Slot()
    def registerResult(self):
        if self._worker.kind or self._active_kind:
            self._notice('已有后台操作正在运行。')
            return
        if not self._job_path or not self._mix_pin or not self._result or not self._result_receipt:
            self._notice('请先完成任务内视频合成，再显式登记。')
            return
        if self._is_result_stale():
            self._notice('输入、设置或成品文件已变化；旧结果不能登记。')
            return
        if self._registered_row:
            self._notice('当前成品已经登记。')
            return
        try:
            from task_store import known_job, locked
            job = known_job(self._job_path)
            if locked(job):
                raise ValueError('任务仍在处理占用中，请结束后再登记。')
        except (OSError, ValueError) as error:
            self._notice('暂时不能登记：' + str(error))
            return
        receipt = deepcopy(self._result_receipt)
        receipt_mix = receipt.get('mixdown') if isinstance(receipt.get('mixdown'), dict) else {}
        if not receipt_mix.get('artifact'):
            self._notice('手动替换的 Mixdown 只能用于独立成品；登记时必须使用当前已核验的任务 Mixdown。')
            return
        if not _same_path(self._mixdown, self._mix_pin.get('path')):
            self._notice('当前 Mixdown 不是任务当前已核验的 WAV；独立成品可以保留，但不能登记。')
            return
        expected_sha = str(receipt.get('sha256') or '')
        result = Path(self._result).expanduser().resolve()
        token = self._form_token()
        expected_dependencies = deepcopy(receipt.get('dependencies') or [])

        def work(stop, send):
            import task_contract
            from task_store import known_job, locked
            current_job = known_job(job)
            if locked(current_job):
                raise ValueError('任务在登记前进入运行状态；本次没有写入任务登记。')
            send('正在核验冻结的原视频、Mixdown 和成品…')
            verified_mix, verified_source = self._verify_pinned_rows(stop)
            if verified_mix is None:
                raise ValueError('任务 Mixdown 的冻结修订已不可用；本次没有写入任务登记。')
            mix_artifact = receipt_mix.get('artifact') or {}
            if _row_identity(verified_mix) != (
                    str(mix_artifact.get('id') or ''), str(mix_artifact.get('revision') or ''),
                    str(mix_artifact.get('sha256') or '')):
                raise ValueError('任务 Mixdown 的修订或摘要已变化；本次没有写入任务登记。')
            source_artifact = (receipt.get('source_video') or {}).get('artifact')
            if source_artifact:
                if _row_identity(verified_source) != (
                        str(source_artifact.get('id') or ''), str(source_artifact.get('revision') or ''),
                        str(source_artifact.get('sha256') or '')):
                    raise ValueError('任务原视频的修订或摘要已变化；本次没有写入任务登记。')
            if token != self._form_token():
                raise ValueError('登记前输入或设置已经变化；本次没有写入任务登记。')
            _stop_check(stop)
            dependencies = [{'id': verified_mix['id'], 'revision': verified_mix['revision']}]
            if source_artifact and verified_source:
                dependencies.append({'id': verified_source['id'], 'revision': verified_source['revision']})
            if dependencies != expected_dependencies:
                raise ValueError('登记依赖与合成回执不一致；本次没有写入任务登记。')
            if token != self._form_token() or locked(current_job):
                raise ValueError('登记写入前输入、设置或任务状态已变化；本次没有写入任务登记。')
            _stop_check(stop)
            source_receipt = receipt['source_video']
            manual_source = (None if source_artifact else
                             {'path': source_receipt['path'], 'sha256': source_receipt['sha256']})
            artifact_id = task_contract.bind(job, 'video', result, expected_sha256=expected_sha,
                                             dependencies=dependencies, video_source=manual_source,
                                             check=stop.is_set)
            rows = task_contract.inspect(job, artifact_ids=(artifact_id,))
            row = next((item for item in rows if item.get('id') == artifact_id), None)
            if row is None:
                raise ValueError('登记已提交，但无法读取新的视频产物条目；请在任务结果中检查。')
            return {'artifact_id': artifact_id, 'row': dict(row), 'sha256': expected_sha,
                    'dependencies': row['dependencies'], 'cancelled_after_commit': stop.is_set()}

        def finish(value):
            row = dict(value['row'])
            if row.get('state') != 'verified':
                raise ValueError('登记后视频条目未通过当前依赖校验。')
            self._registered_row = row
            self._registered_sha256 = value['sha256']
            receipt = deepcopy(self._result_receipt or {})
            receipt.update(artifact_id=value['artifact_id'], registered_at=
                           datetime.now().astimezone().isoformat(timespec='seconds'),
                           dependencies=value['dependencies'],
                           row={'id': row['id'], 'revision': row['revision'],
                                'sha256': row['sha256']})
            self._result_receipt = receipt
            self._state = 'registered'
            self._message = ('视频已显式登记到任务；仍保留独立 MP4，不会覆盖已有成品。'
                             f' SHA-256：{value["sha256"]}')

        self._start('video-register', '正在重新核验并登记视频成品…', work, finish)

    def _open_input(self, value: str, label: str):
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            path = None
        if path is None or not path.is_file():
            self._notice(f'当前没有可打开的{label}。')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._notice(f'系统无法打开当前{label}。')

    @Slot()
    def openSourceVideo(self):
        self._open_input(self._source_video, '原视频')

    @Slot()
    def openMixdown(self):
        self._open_input(self._mixdown, 'Mixdown')

    @Slot()
    def openResult(self):
        if not self._result or not Path(self._result).is_file():
            self._notice('当前没有可打开的 MP4 成品。')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._result).resolve()))):
            self._notice('系统无法打开当前 MP4。')

    @Slot()
    def publishResult(self):
        if self._worker.kind or self._active_kind:
            self._notice('已有后台操作正在运行。')
            return
        if not self._job_path or not self._registered_row or not self._result:
            self._notice('请先将当前成品显式登记，再进入发布。')
            return
        if self._is_result_stale() or self._registered_sha256 != self._result_sha256:
            self._notice('当前成品或输入已变化；请重新核验后再进入发布。')
            return
        job = self._job_path
        expected_row = deepcopy(self._registered_row)
        expected_sha = self._result_sha256
        result = Path(self._result).resolve()

        def work(stop, send):
            from task_store import known_job
            current_job = known_job(job)
            send('正在核验当前绑定的视频与依赖…')
            handoff = self._handoff()
            verify = getattr(handoff, 'verify_task_artifact', None)
            if not callable(verify):
                raise ValueError('媒体交接校验接口不可用。')
            row = verify(str(current_job), expected_row, 'video', check=stop.is_set)
            row = dict(row)
            _stop_check(stop)
            if (row.get('state') != 'verified' or _row_identity(row) != _row_identity(expected_row)
                    or not _same_path(row.get('path'), result) or row.get('sha256') != expected_sha):
                raise ValueError('当前绑定视频已改变或校验失效；不会交给发布页。')
            return {'job': str(current_job), 'row': row}

        def finish(value):
            self._state = 'registered'
            self._message = '已把当前绑定视频交给发布工作流。'
            self.publishRequested.emit(str(value['job']), dict(value['row']))

        self._start('video-publish', '正在刷新绑定视频校验…', work, finish)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._worker.kind in _VIDEO_KINDS:
            self.cancel()
        else:
            self._cleanup_owned_jobs()


__all__ = ('QuickVideo',)
