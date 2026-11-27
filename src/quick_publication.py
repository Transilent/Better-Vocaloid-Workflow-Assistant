"""Qt Quick publication workflow over the existing immutable-bundle backend.

The controller owns only projection and editable draft state. Task metadata is
written by publication.prepare in one explicit transaction, after the selected
video and the source job have been re-verified by the application controller.
Restoring an existing bundle is read-only and never registers it again.
Bilibili filling reuses the existing safe runner; final submission is always a
user action on the platform page.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from vwa_io import safe_rmtree
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from common import ROOT, atomic_json, file_hash, load_config
from i18n import tr
import publication
import publication_browser_service as _browser_svc
import publication_draft_service as _draft_svc
import quick_results

from publication_browser_service import (
    BILIBILI,
    BROWSER_STATUS_FILENAME,
    _BrowserSession,
    _MAX_BROWSER_STATUS_BYTES,
    _read_json,
    _text,
)
from publication_draft_service import (
    BILIBILI_COVER_RATIOS,
    BILIBILI_COVER_SIZES,
    COVER_LABELS,
    COVER_MODES,
    IMAGE_SUFFIXES,
    PLATFORMS,
    PLATFORM_LABELS,
    RIGHTS_MAX_CHARS,
    TAG_LIMITS,
    XIAOHONGSHU,
    XIAOHONGSHU_CREATOR_URL,
    _ArtifactRef,
    _HEX_ID,
    _HEX_SHA,
    _LocalVideoRef,
    _MAX_DRAFT_BYTES,
    _PlatformDraft,
    _PreviewRequest,
    _PublicationState,
    _Snapshot,
    _TAG_SPLIT,
    _check_stop,
    _inspect_bundle,
    _optional_json,
    _same_path,
    _sniff_image,
    _song_title,
    _stat_stamp,
    _tags,
    _tags_text,
)


def _local_path(value: Any) -> Path:
    if isinstance(value, QUrl):
        if not value.isLocalFile():
            raise ValueError('请选择本机文件。')
        value = value.toLocalFile()
    if not isinstance(value, str) or not value.strip():
        raise ValueError('文件路径为空。')
    return Path(value).expanduser().resolve()


def _bundle_directory(value: Any) -> Path:
    path = _local_path(value)
    if path.is_file():
        if path.name != 'manifest.json':
            raise ValueError('请选择发布资料文件夹或其 manifest.json。')
        path = path.parent
    if not path.is_dir() or not (path / 'manifest.json').is_file():
        raise ValueError('所选目录不是包含 manifest.json 的发布资料。')
    return path.resolve()


def _local_url(path: Any) -> str:
    return QUrl.fromLocalFile(str(Path(path).resolve())).toString() if path else ''


def _artifact_ref(row: Any, role: str) -> _ArtifactRef:
    return _draft_svc._artifact_ref(row, role, local_path_fn=_local_path)


class QuickPublication(QObject):
    """Application-owned state for the three-step Quick publication page."""

    viewChanged = Signal()

    def __init__(self, parent, worker):
        super().__init__(parent)
        self._owner = parent
        self._worker = worker
        self._closed = False
        self._lock = threading.RLock()
        self._state = _PublicationState()
        self._active_kind = ''
        self._cancel_requested = False
        self._restoring_saved = False
        self._draft_restore_attempted = False
        self._preview_dir: Path | None = None
        self._drift_timer = QTimer(self)
        self._drift_timer.setInterval(1500)
        self._drift_timer.timeout.connect(self._poll_drift)
        self._browser_timer = QTimer(self)
        self._browser_timer.setInterval(700)
        self._browser_timer.timeout.connect(self._poll_browser)
        self._browser_sig = ()
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.setInterval(100)
        self._restore_timer.timeout.connect(self._auto_restore_saved_draft)
        self._view = self._recompute_view_locked()
        self._worker.changed.connect(self._worker_changed)
        self._restore_timer.start()

    @Property('QVariantMap', notify=viewChanged)
    def view(self):
        with self._lock:
            return copy.deepcopy(self._view)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def accept_task(self, job, video_row, bundle_row=None):
        """Accept rows that main has already rechecked.

        Only structural reads happen here. Large video hashes and manifest
        verification are repeated on the shared worker before any prepare,
        check, open, or launch action.
        """
        try:
            with self._lock:
                if self._closed:
                    return False
                if self._active_kind and not self._restoring_saved:
                    raise ValueError('发布操作进行中，不能切换任务。')
                job_path = Path(job).expanduser().resolve()
                if not job_path.is_dir():
                    raise ValueError('来源任务目录不存在。')
                video = _artifact_ref(video_row, 'video')
                request = _optional_json(job_path / 'request.json')
                if request is not None and not isinstance(request, dict):
                    raise ValueError('任务 request.json 格式无效。')
                request = request if isinstance(request, dict) else {}
                info = None
                try:
                    info = publication.read_source_info(job_path)
                except (OSError, ValueError):
                    pass
                source_kind = _text(request.get('source_kind')) or _text((info or {}).get('kind'))
                bv = _text(request.get('bv')) or _text((info or {}).get('bvid'))
                if source_kind == 'local':
                    is_local = True
                elif source_kind in ('bilibili', 'bv') or bv:
                    is_local = False
                else:
                    is_local = True
                draft_text, draft_error = '', ''
                try:
                    draft_text = publication.source_draft(job_path)
                except (OSError, ValueError) as exc:
                    draft_error = str(exc)

                source_ready = bool(request) and (is_local or bool(draft_text.strip()))
                source_problem = ''
                if not request:
                    source_problem = '来源任务缺少 request.json，请重新选择任务。'
                elif not is_local and not bv:
                    source_problem = 'B站来源任务缺少 BV，请重新读取来源资料。'
                elif not is_local and not draft_text.strip():
                    source_problem = draft_error or 'B站来源任务缺少可核验的简介草稿。'
                original = self._cached_original_cover(job_path)
                source_stamp = self._source_stamp(job_path, video.path, original)

                next_state = _PublicationState()
                next_state.job = str(job_path)
                next_state.video = str(video.path)
                next_state.video_ref = video
                next_state.task_choices = list(self._state.task_choices)
                next_state.task_message = (f'已读取来源任务：{source_title or job_path.name}'
                                           if (source_title := _text((info or {}).get('title')))
                                           else self._state.task_message)
                next_state.source_ready = source_ready
                next_state.source_problem = source_problem
                next_state.source_mode = 'local' if is_local else 'bilibili'
                next_state.original_cover = str(original) if original else ''
                source_title = _text((info or {}).get('title'))
                preferred_title = f'【翻唱】{_song_title(source_title)}' if _song_title(source_title) else ''
                default_mode = 'original' if original else 'frame'
                for platform in PLATFORMS:
                    next_state.drafts[platform] = _PlatformDraft(
                        title=preferred_title,
                        description=draft_text,
                        cover_mode=default_mode,
                        cover_preview=_local_url(original) if original else '',
                    )

                if bundle_row is not None:
                    bundle = _artifact_ref(bundle_row, 'publication')
                    bundle_dir = bundle.path.parent.resolve()
                    if bundle.path.name != 'manifest.json':
                        raise ValueError('发布的登记入口必须是 manifest.json。')
                    data = _read_json(bundle.path)
                    if not isinstance(data, dict) or data.get('state') != 'prepared':
                        raise ValueError('发布资料 manifest 状态无效。')
                    if data.get('final_publish') != 'human_only':
                        raise ValueError('发布资料未声明最终投稿由用户完成。')
                    if Path(publication.clean_path(data.get('source_job', ''))).resolve() != job_path:
                        raise ValueError('发布资料绑定的来源任务与当前任务不一致。')
                    video_data = data.get('video') if isinstance(data.get('video'), dict) else {}
                    if (Path(publication.clean_path(video_data.get('path', ''))).resolve() != video.path
                            or video_data.get('sha256') != video.sha256):
                        raise ValueError('发布资料绑定的成品与当前已核验视频不一致。')
                    content = data.get('content')
                    if not isinstance(content, dict) or not content:
                        raise ValueError('发布资料没有可恢复的平台文稿。')
                    unsupported = set(content) - set(PLATFORMS)
                    if unsupported:
                        raise ValueError('发布资料包含不支持的平台。')
                    for platform in content:
                        publication.load_bundle(bundle_dir, platform, verify_hash=False)
                    next_state.selected_platforms = tuple(
                        platform for platform in PLATFORMS if platform in content)
                    next_state.platform = (self._state.platform if self._state.platform in content
                                           else next_state.selected_platforms[0])
                    for platform, fields in content.items():
                        if not isinstance(fields, dict):
                            raise ValueError('发布资料中的平台文稿无效。')
                        draft = next_state.drafts[platform]
                        draft.title = _text(fields.get('title'))
                        draft.description = _text(fields.get('description'))
                        draft.tags = _tags_text(fields.get('tags'))
                        draft.ai = fields.get('ai_generated') if type(fields.get('ai_generated')) is bool else None
                        draft.rights = _text(fields.get('rights')) or None
                        cover = data.get('cover') if isinstance(data.get('cover'), dict) else {}
                        draft.cover_mode = ('original-frame' if cover.get('frame_source') == 'original'
                                            else _text(cover.get('kind')) or 'frame')
                        if draft.cover_mode not in COVER_MODES:
                            raise ValueError('发布资料包含未知封面来源。')
                        seconds = cover.get('seconds')
                        draft.cover_seconds = float(seconds) if isinstance(seconds, (int, float)) else 20.0
                        draft.cover_file = (_text(cover.get('source')) if draft.cover_mode == 'custom' else '')
                        bundle_cover = bundle_dir / 'cover.jpg'
                        if not bundle_cover.is_file():
                            raise ValueError('发布资料缺少 cover.jpg。')
                        draft.cover_preview = _local_url(bundle_cover)
                    next_state.bundle = str(bundle_dir)
                    next_state.bundle_ref = bundle
                    next_state.bundle_stamp = self._bundle_stamp(bundle_dir, data)
                    next_state.cover_variants = self._cover_variants(bundle_dir, data)
                    next_state.step = 2
                    next_state.state = 'restored'
                    next_state.message = '已从只读发布资料恢复文稿；核对前不会改写任务登记或发布包。'
                else:
                    saved = self._load_saved_draft(job_path, video, source_stamp)
                    if saved is not None:
                        saved_selected = saved.get('selectedPlatforms')
                        selected = [item for item in saved_selected if item in PLATFORMS]                             if isinstance(saved_selected, list) else []
                        platform = _text(saved.get('platform'))
                        if platform in PLATFORMS and platform not in selected:
                            selected = [platform]
                        if selected:
                            next_state.selected_platforms = tuple(
                                item for item in PLATFORMS if item in selected)
                        if platform in next_state.selected_platforms:
                            next_state.platform = platform
                        elif next_state.platform not in next_state.selected_platforms:
                            next_state.platform = next_state.selected_platforms[0]
                        for platform in PLATFORMS:
                            value = saved.get('drafts', {}).get(platform) if isinstance(saved.get('drafts'), dict) else None
                            if isinstance(value, dict):
                                next_state.drafts[platform] = self._draft_from_saved(value, next_state.drafts[platform])
                        step = saved.get('step')
                        next_state.step = step if type(step) is int and 0 <= step <= 2 else 0
                        ratio = saved.get('coverRatio')
                        if ratio in BILIBILI_COVER_RATIOS:
                            next_state.cover_ratio = ratio
                        next_state.state = 'editing'
                        next_state.message = '已恢复本地发布草稿；尚未写入任务数据。'
                    else:
                        next_state.state = 'editing'
                        next_state.message = ('已载入已核验成品。请填写文稿与声明。' if source_ready
                                              else source_problem)

                next_state.source_stamp = source_stamp
                next_state.can_prepare, next_state.can_check = False, False
                self._state = next_state
                self._discard_preview_dir(self._preview_dir, None)
                self._preview_dir = None
                self._cancel_requested = False
                self._drift_timer.start()
                self._browser_timer.start()
                self._refresh_browser_locked()
                self._recompute_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice('载入发布任务失败：' + str(exc), state='error')
            return False

    @Slot()
    @Slot(str)
    def refreshTaskChoices(self, query=''):
        """Refresh source-task choices without claiming any task is still verified."""
        with self._lock:
            if self._closed or self._active_kind or self._worker.kind:
                self._notice('已有后台操作正在进行，暂时不能刷新任务。')
                return False
        needle = _text(query).strip().casefold()

        def work(stop, send):
            send({'message': '正在读取任务列表…'})
            choices = []
            for item in quick_results.scan(stop.is_set):
                _check_stop(stop)
                try:
                    job = Path(item['jobId']).resolve()
                    request = _optional_json(job / 'request.json') or {}
                    info = None
                    try:
                        info = publication.read_source_info(job)
                    except (OSError, ValueError):
                        pass
                    source_kind = _text(request.get('source_kind')) or _text((info or {}).get('kind'))
                    bv = _text(request.get('bv')) or _text((info or {}).get('bvid'))
                    title = _text(item.get('title')) or _text((info or {}).get('title')) or job.name
                    label = f"{title} · {bv or '本地来源'}"
                except (OSError, TypeError, ValueError):
                    continue
                if needle and needle not in f'{label} {job}'.casefold():
                    continue
                choices.append({'label': label, 'value': str(job), 'title': title,
                                'sourceBv': bv, 'sourceKind': source_kind})
                if len(choices) >= 100:
                    break
            if not choices:
                send({'message': '没有找到匹配的来源任务。'})
            else:
                send({'message': f'已列出 {len(choices)} 个来源任务；选择后仍会重新读取资料。'})
            return choices

        def finish(choices, error):
            with self._lock:
                if error is not None:
                    self._state.task_choices = []
                    self._state.task_message = '无法读取任务列表：' + str(error)
                else:
                    self._state.task_choices = list(choices or ())
                    self._state.task_message = ('请选择任务，再单独选择成品视频或导入发布资料。'
                                                if self._state.task_choices else '没有找到匹配的来源任务。')
                self._recompute_locked()
            self._emit()

        return self._start_work('publication-task-scan', work, finish)

    @Slot(str)
    def chooseTask(self, job):
        """Load source-task fields while keeping video and bundle choices separate."""
        job_text = _text(job).strip()
        if not job_text:
            self._notice('请先选择一个来源任务。')
            return False
        return self._load_source_task(job_text)

    @Slot(str, result=bool)
    def fetchSourceMetadata(self, source):
        try:
            from common import extract_bv
            from source_contract import reject_unsupported_bilibili_subpage
            value = _text(source).strip()
            reject_unsupported_bilibili_subpage(value)
            bv = extract_bv(value)
        except (OSError, ValueError) as exc:
            self._notice(str(exc))
            return False
        return self._load_source_task('', bv=bv)

    def _load_source_task(self, job_text, *, bv=''):
        with self._lock:
            if self._closed or self._active_kind or self._worker.kind:
                self._notice('发布操作正在运行，暂时不能切换任务。')
                return False
        def work(stop, send):
            from task_store import known_job
            if bv:
                from common import remember_job
                from pipeline import create_job, run
                from preparation_commands import graph_for
                _check_stop(stop)
                send({'message': '正在读取本家资料；这一步只准备来源，不下载视频。'})
                job_path = create_job(bv, remember=False, workflow_graph=graph_for('separation'))
                run(job_path, until='metadata')
                remember_job(job_path)
            else:
                job_path = known_job(job_text)
            _check_stop(stop)
            request = _optional_json(job_path / 'request.json')
            if not isinstance(request, dict):
                raise ValueError('来源任务缺少有效 request.json。')
            info = None
            try:
                info = publication.read_source_info(job_path)
            except (OSError, ValueError):
                pass
            draft_text, draft_error = '', ''
            try:
                draft_text = publication.source_draft(job_path)
            except (OSError, ValueError) as exc:
                draft_error = str(exc)
            _check_stop(stop)
            return {'job': job_path, 'request': request, 'info': info,
                    'draft': draft_text, 'draftError': draft_error}

        def finish(value, error):
            if error is not None:
                self._notice('载入来源任务失败：' + str(error))
                return
            with self._lock:
                job_path = Path(value['job']).resolve()
                request, info = value['request'], value['info']
                draft_text = _text(value['draft'])
                declared = _text(request.get('source_kind')) or _text((info or {}).get('kind'))
                bv = _text(request.get('bv')) or _text((info or {}).get('bvid'))
                if declared == 'local':
                    is_local = True
                elif declared in ('bilibili', 'bv'):
                    is_local = False
                elif bv:
                    is_local = False
                else:
                    is_local = True
                source_problem = ''
                if not is_local and not bv:
                    source_problem = 'B站来源任务缺少 BV，请重新读取来源资料。'
                elif not is_local and not draft_text.strip():
                    source_problem = value['draftError'] or 'B站来源任务缺少可核验的简介草稿。'
                original = self._cached_original_cover(job_path)
                title = _text((info or {}).get('title'))
                preferred = f'【翻唱】{_song_title(title)}' if _song_title(title) else ''
                next_state = _PublicationState()
                next_state.job = str(job_path)
                next_state.source_ready = not source_problem
                next_state.source_problem = source_problem
                next_state.source_mode = 'local' if is_local else 'bilibili'
                next_state.original_cover = str(original) if original else ''
                next_state.task_choices = list(self._state.task_choices)
                next_state.task_message = f'已读取来源任务：{title or job_path.name}'
                default_mode = 'original' if original else 'frame'
                next_state.selected_platforms = (BILIBILI,)
                for platform in PLATFORMS:
                    next_state.drafts[platform] = _PlatformDraft(
                        title=preferred, description=draft_text, cover_mode=default_mode,
                        cover_preview=_local_url(original) if original else '')
                next_state.message = ('来源任务字段已载入。请单独选择成品视频，或导入已有发布资料。'
                                      if not source_problem else source_problem)
                next_state.state = 'editing'
                self._state = next_state
                self._discard_preview_dir(self._preview_dir, None)
                self._preview_dir = None
                self._drift_timer.start()
                self._browser_timer.start()
                self._refresh_browser_locked()
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()

        return self._start_work('publication-task-load', work, finish)

    @Slot(result=bool)
    def refreshOriginalCover(self):
        with self._lock:
            if self._closed or self._state.busy or self._worker.kind or self._state.source_mode != 'bilibili':
                return False
            job = self._state.job
            if not job:
                return False

        def work(stop, send):
            from cover_art import job_cover, cached_cover
            from pipeline import job_lock
            from task_store import known_job
            path = known_job(job)
            send({'message': '正在获取本家原始封面…'})
            _check_stop(stop)
            with job_lock(path):
                info = publication.read_source_info(path)
                job_cover(path, refresh=True)
                checked = cached_cover(info, path)
            if not checked:
                raise ValueError('无法核验下载后的封面。')
            return checked[0]

        def finish(value, error):
            if error is not None:
                self._notice(str(error))
                return
            with self._lock:
                self._state.original_cover = str(value)
                for draft in self._state.drafts.values():
                    if draft.cover_mode == 'original':
                        draft.cover_preview = _local_url(value)
                if self._state.video_ref:
                    self._state.source_stamp = self._source_stamp(Path(job), self._state.video_ref.path, value)
                self._state.message = '本家封面已核验，可以在封面来源中选择。'
                self._recompute_locked()
            self._emit()

        return self._start_work('publication-original-cover', work, finish)

    @Slot(QUrl)
    def chooseVideo(self, url):
        """Validate a separately chosen local finished video for the current task."""
        with self._lock:
            if self._closed or self._active_kind or self._worker.kind:
                self._notice('发布操作正在运行，暂时不能更换成品。')
                return False
            if not self._state.job:
                self._notice('请先选择来源任务，再单独选择成品视频。')
                return False
            job_text = self._state.job
        try:
            path = _local_path(url)
            if path.is_dir():
                raise ValueError('请选择视频文件。')
        except (OSError, ValueError) as exc:
            self._notice(str(exc))
            return False

        def work(stop, send):
            send({'message': '正在检查所选成品视频…'})
            info = publication.inspect_video(path)
            _check_stop(stop)
            sha256 = file_hash(path, check=stop.is_set)
            _check_stop(stop)
            return {'path': path.resolve(), 'info': info, 'sha256': sha256}

        def finish(value, error):
            if error is not None:
                self._notice('所选成品视频无法使用：' + str(error))
                return
            with self._lock:
                if self._state.job != job_text:
                    self._notice('任务已变化，没有采用刚才选择的成品视频。')
                    return
                path = Path(value['path']).resolve()
                self._state.video_ref = _LocalVideoRef(path, value['sha256'], path.stat().st_size)
                self._state.video = str(path)
                self._state.bundle = ''
                self._state.bundle_ref = None
                self._state.bundle_stamp = ()
                self._state.bundle_stale = False
                self._state.bundle_only = False
                self._state.verified = False
                self._state.handoffs = {}
                self._state.cover_variants = {}
                self._state.step = 0
                original = self._cached_original_cover(Path(self._state.job))
                self._state.original_cover = str(original) if original else ''
                if not original:
                    for draft in self._state.drafts.values():
                        if draft.cover_mode == 'original':
                            draft.cover_mode = 'frame'
                self._state.source_stamp = self._source_stamp(
                    Path(self._state.job), path, Path(original) if original else None)
                saved = self._load_saved_draft(Path(self._state.job), self._state.video_ref,
                                               self._state.source_stamp)
                if saved is not None:
                    self._apply_saved_platform_state_locked(saved)
                self._state.source_drift = False
                self._state.state = 'editing'
                self._state.message = ('已选择本地成品；生成时会把这份明确选择的视频登记到当前任务，'
                                       '并再次核对文件摘要。')
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()

        return self._start_work('publication-video-select', work, finish)

    @Slot(QUrl)
    def importBundle(self, url):
        """Open an existing publication bundle read-only, independently of a task."""
        with self._lock:
            if self._closed or self._active_kind or self._worker.kind:
                self._notice('发布操作正在运行，暂时不能导入发布资料。')
                return False
        try:
            directory = _bundle_directory(url)
        except (OSError, ValueError) as exc:
            self._notice(str(exc))
            return False

        def work(stop, send):
            send({'message': '正在读取并检查发布资料结构…'})
            data = _inspect_bundle(directory)
            _check_stop(stop)
            return {'bundle': directory, 'data': data}

        def finish(value, error):
            if error is not None:
                self._notice('无法导入发布资料：' + str(error))
                return
            with self._lock:
                self._install_bundle_locked(Path(value['bundle']), value['data'])
            self._emit()

        return self._start_work('publication-bundle-import', work, finish)

    @Slot()
    def restoreSavedDraft(self):
        """Restore the last source/video/bundle draft without waiting for a Results handoff."""
        with self._lock:
            if self._closed or self._active_kind or self._worker.kind:
                return False
            if self._state.job or self._state.video_ref or self._state.bundle:
                return False
        try:
            saved = _read_json(self._draft_path(), limit=_MAX_DRAFT_BYTES)
        except (OSError, ValueError, TypeError):
            self._draft_restore_attempted = True
            return False
        if not isinstance(saved, dict) or saved.get('version') != 1:
            self._draft_restore_attempted = True
            return False
        self._draft_restore_attempted = True

        bundle_text = _text(saved.get('bundle'))
        if bundle_text:
            try:
                bundle = _bundle_directory(bundle_text)
                data = _inspect_bundle(bundle)
                with self._lock:
                    self._install_bundle_locked(bundle, data, saved=saved)
                self._emit()
                return True
            except (OSError, ValueError, TypeError) as exc:
                with self._lock:
                    self._state.task_message = '上次发布资料无法直接恢复：' + str(exc)

        job_text = _text(saved.get('job'))
        video_text = _text(saved.get('video'))
        if not job_text or not video_text:
            self._emit()
            return False

        def work(stop, send):
            from task_store import known_job
            job_path = known_job(job_text)
            detail = quick_results.detail(job_path, stop.is_set)
            target = Path(video_text).resolve()
            video = next((row for row in detail.get('artifacts', ())
                          if row.get('role') == 'video' and row.get('state') == 'verified'
                          and row.get('available') is not False
                          and _same_path(row.get('path'), target)), None)
            expected_sha = _text(saved.get('videoSha256'))
            if video is None or (expected_sha and video.get('sha256') != expected_sha):
                raise ValueError('上次草稿中的成品视频已变化或不再通过任务校验。')
            _check_stop(stop)
            return {'job': job_path, 'video': dict(video)}

        def finish(value, error):
            if error is not None:
                with self._lock:
                    self._state.task_message = '上次发布草稿未恢复：' + str(error)
                self._emit()
                return
            self._restoring_saved = True
            try:
                accepted = self.accept_task(value['job'], value['video'])
            finally:
                self._restoring_saved = False
            if not accepted:
                with self._lock:
                    self._state.task_message = '上次发布草稿未能恢复：' + self._state.message
                self._emit()

        return self._start_work('publication-draft-restore', work, finish)

    def _apply_saved_platform_state_locked(self, saved: Mapping[str, Any]):
        chosen = saved.get('selectedPlatforms')
        allowed = set(self._bundle_platforms_locked()) if self._state.bundle else set(PLATFORMS)
        if isinstance(chosen, list):
            selected = tuple(item for item in PLATFORMS if item in chosen and item in allowed)
            if selected:
                self._state.selected_platforms = selected
        platform = _text(saved.get('platform'))
        if platform in self._state.selected_platforms:
            self._state.platform = platform
        ratio = saved.get('coverRatio')
        if ratio in BILIBILI_COVER_RATIOS:
            self._state.cover_ratio = ratio
        drafts = saved.get('drafts') if isinstance(saved.get('drafts'), dict) else {}
        for platform in PLATFORMS:
            value = drafts.get(platform)
            if isinstance(value, dict):
                self._state.drafts[platform] = self._draft_from_saved(
                    value, self._state.drafts[platform])
        step = saved.get('step')
        if type(step) is int and 0 <= step <= 2:
            self._state.step = step

    def _install_bundle_locked(self, bundle: Path, data: Mapping[str, Any], *, saved=None):
        content = data.get('content') if isinstance(data, Mapping) else None
        if not isinstance(content, dict) or not content:
            raise ValueError('发布资料没有可恢复的平台文稿。')
        bundle = Path(bundle).resolve()
        video_data = data.get('video') if isinstance(data.get('video'), dict) else {}
        raw_job = _text(data.get('source_job'))
        job_path = None
        if raw_job:
            try:
                candidate = Path(publication.clean_path(raw_job)).resolve()
                job_path = candidate if candidate.is_dir() else None
            except (OSError, ValueError):
                job_path = None
        info = None
        if job_path is not None:
            try:
                info = publication.read_source_info(job_path)
            except (OSError, ValueError):
                info = None
        next_state = _PublicationState()
        next_state.job = str(job_path) if job_path is not None else ''
        next_state.video = str(Path(publication.clean_path(video_data.get('path', ''))).resolve())
        next_state.selected_platforms = tuple(item for item in PLATFORMS if item in content)
        if not next_state.selected_platforms:
            raise ValueError('发布资料没有受支持平台。')
        next_state.platform = next_state.selected_platforms[0]
        cover = data.get('cover') if isinstance(data.get('cover'), dict) else {}
        for platform in PLATFORMS:
            fields = content.get(platform)
            if not isinstance(fields, dict):
                continue
            next_state.drafts[platform] = _PlatformDraft(
                title=_text(fields.get('title')), description=_text(fields.get('description')),
                tags=_tags_text(fields.get('tags')),
                ai=fields.get('ai_generated') if type(fields.get('ai_generated')) is bool else None,
                rights=_text(fields.get('rights')) or None,
                cover_mode=('original-frame' if cover.get('frame_source') == 'original'
                            else _text(cover.get('kind')) or 'frame'),
                cover_seconds=float(cover.get('seconds')) if isinstance(cover.get('seconds'), (int, float)) else 20.0,
                cover_file=_text(cover.get('source')) if _text(cover.get('kind')) == 'custom' else '',
                cover_preview=_local_url(bundle / 'cover.jpg'))
        if next_state.drafts[next_state.platform].cover_mode not in COVER_MODES:
            raise ValueError('发布资料包含未知封面来源。')
        next_state.bundle = str(bundle)
        next_state.bundle_ref = None
        next_state.bundle_stamp = self._bundle_stamp(bundle, data)
        next_state.bundle_only = True
        next_state.cover_variants = self._cover_variants(bundle, data)
        next_state.source_ready = True
        next_state.source_mode = 'bundle'
        next_state.original_cover = ''
        next_state.source_stamp = ()
        next_state.task_choices = list(self._state.task_choices)
        next_state.task_message = '已读取独立发布资料；来源任务可以缺失，交接时只核验发布包与素材。'
        next_state.step = 2
        next_state.state = 'restored'
        next_state.message = '已载入独立发布资料；请核对后再使用各平台手动交接。'
        if isinstance(saved, Mapping):
            saved_video = _text(saved.get('videoSha256'))
            saved_path = _text(saved.get('video'))
            manifest_sha = _text(video_data.get('sha256'))
            if ((not saved_video or saved_video == manifest_sha)
                    and (not saved_path or _same_path(saved_path, next_state.video))):
                chosen = saved.get('selectedPlatforms')
                if isinstance(chosen, list):
                    selected = tuple(item for item in PLATFORMS if item in chosen and item in content)
                    if selected:
                        next_state.selected_platforms = selected
                platform = _text(saved.get('platform'))
                if platform in next_state.selected_platforms:
                    next_state.platform = platform
                ratio = saved.get('coverRatio')
                if ratio in BILIBILI_COVER_RATIOS:
                    next_state.cover_ratio = ratio
                for platform in PLATFORMS:
                    value = saved.get('drafts', {}).get(platform) if isinstance(saved.get('drafts'), dict) else None
                    if isinstance(value, dict):
                        next_state.drafts[platform] = self._draft_from_saved(value, next_state.drafts[platform])
                step = saved.get('step')
                if type(step) is int and 0 <= step <= 2:
                    next_state.step = step
                next_state.message = '已恢复上次发布草稿；发布包仍只读，交接前会重新核验。'
        self._state = next_state
        self._discard_preview_dir(self._preview_dir, None)
        self._preview_dir = None
        self._drift_timer.start()
        self._browser_timer.start()
        self._refresh_browser_locked()
        self._recompute_locked()
        self._save_draft_safe_locked()

    def _auto_restore_saved_draft(self):
        if self._draft_restore_attempted or self._closed:
            return
        self.restoreSavedDraft()

    @Slot(str, 'QVariant')
    def setField(self, name, value):
        try:
            with self._lock:
                if self._closed:
                    return False
                if self._state.busy or self._active_kind:
                    raise ValueError('发布操作进行中，暂时不能修改输入。')
                self._maybe_mark_drift_locked()
                name = _text(name)
                draft = self._state.draft()
                if name == 'platform':
                    platform = _text(value).strip().lower()
                    if platform not in PLATFORMS:
                        raise ValueError('请选择 B站或小红书。')
                    if platform != self._state.platform or self._state.selected_platforms != (platform,):
                        self._state.platform = platform
                        self._state.selected_platforms = (platform,)
                        if self._state.bundle and set(self._state.selected_platforms) != self._bundle_platforms_locked():
                            self._mark_stale_locked('发布平台选择已变化，请重新生成同一发布包。')
                        else:
                            self._state.message = f'当前只发布到{PLATFORM_LABELS[platform]}。'
                elif name in ('title', 'description', 'tags'):
                    if not isinstance(value, str):
                        raise ValueError('该字段必须使用文本。')
                    old = getattr(draft, name)
                    if value != old:
                        setattr(draft, name, value)
                        self._mark_stale_locked('文稿已编辑；原发布资料已过期。')
                        self._state.message = '文稿已更新。请重新生成并核对发布资料。'
                elif name == 'ai':
                    if value is not None and type(value) is not bool:
                        raise ValueError('AI 内容声明必须明确选择“含”或“不含”。')
                    if value != draft.ai:
                        draft.ai = value
                        self._mark_stale_locked('AI 内容声明已修改；原发布资料已过期。')
                        self._state.message = 'AI 内容声明已更新。'
                elif name == 'rights':
                    if value is None:
                        rights = None
                    elif type(value) is bool:
                        rights = '已核对原创、转载、署名与授权' if value else None
                    elif isinstance(value, str):
                        if len(value) > RIGHTS_MAX_CHARS:
                            raise ValueError(f'版权与署名声明最多 {RIGHTS_MAX_CHARS} 字。')
                        rights = value.strip() or None
                    else:
                        raise ValueError('版权与署名声明必须使用文本。')
                    if rights != draft.rights:
                        draft.rights = rights
                        self._mark_stale_locked('版权声明已修改；原发布资料已过期。')
                        self._state.message = '版权与署名声明已更新。'


                elif name == 'coverMode':
                    mode = _text(value).strip().lower()
                    if mode not in COVER_MODES:
                        raise ValueError('未知封面来源。')
                    if mode == 'original' and not Path(self._state.original_cover).is_file():
                        raise ValueError('没有可核验的本家原封面；请选择本地图片或视频画面。')
                    if mode == 'original-frame' and self._state.source_mode != 'bilibili':
                        raise ValueError('只有 B站来源任务可以截取本家原视频。')
                    if mode != draft.cover_mode:
                        for item in self._state.drafts.values():
                            item.cover_mode = mode
                            item.cover_preview = ''
                        self._mark_stale_locked('封面来源已修改；原发布资料已过期。')
                        self._state.message = '发布包共用的封面来源已更新，请重新预览。'
                elif name == 'coverSeconds':
                    try:
                        seconds = float(value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError('截图时间必须是数字。') from exc
                    if not math.isfinite(seconds) or not 0 <= seconds < 86400:
                        raise ValueError('截图时间须在 0 到 86400 秒之间。')
                    if seconds != draft.cover_seconds:
                        for item in self._state.drafts.values():
                            item.cover_seconds = seconds
                            item.cover_preview = ''
                        self._mark_stale_locked('截图时间已修改；原发布资料已过期。')
                        self._state.message = '发布包共用的截图时间已更新，请重新预览。'
                elif name == 'coverFile':
                    path = _local_path(value) if value else None
                    text = str(path) if path else ''
                    if path is not None:
                        _sniff_image(path)
                    if text != draft.cover_file:
                        for item in self._state.drafts.values():
                            item.cover_file = text
                            item.cover_preview = _local_url(path) if path else ''
                        self._mark_stale_locked('封面图片已修改；原发布资料已过期。')
                        self._state.message = '发布包共用的本地封面已选择。'
                else:
                    raise ValueError('该字段不可编辑。')
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot(str, str, str, bool, bool, result=bool)
    def applyTitleCredits(self, song, voice, credit, include_title, all_platforms):
        song, voice, credit = (_text(value).strip() for value in (song, voice, credit))
        if (include_title and not song) or (not include_title and not (voice or credit)):
            return False
        if any(len(value) > 80 or '\n' in value or '\r' in value for value in (song, voice, credit)):
            self._notice('歌名、歌姬和调音署名请分别填写一行（最多 80 字）。')
            return False
        with self._lock:
            if self._closed or self._active_kind or self._state.bundle_only:
                return False
            platforms = self._state.selected_platforms if all_platforms else (self._state.platform,)
            for platform in platforms:
                draft = self._state.drafts[platform]
                if include_title:
                    draft.title = f'【{voice}翻唱】{song}' if voice else f'【翻唱】{song}'
                lines = ([f'本次翻唱歌姬：{voice}'] if voice else []) + ([f'本次调音：{credit}'] if credit else [])
                missing = [line for line in lines if line not in draft.description.splitlines()]
                if missing:
                    draft.description = '\n'.join(missing) + ('\n\n' + draft.description if draft.description else '')
            self._mark_stale_locked('文稿已修改；原发布资料已过期。')
            self._state.message = '已应用标题和署名。请按平台核对内容与字数。'
            self._recompute_locked()
            self._save_draft_safe_locked()
        self._emit()
        return True

    @Slot(str)
    def editPlatform(self, platform):
        """Switch the draft editor without changing the frozen platform set."""
        platform = _text(platform).strip().lower()
        if platform not in PLATFORMS:
            self._notice('请选择 B站或小红书。')
            return False
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            selected = set(self._state.selected_platforms)
            if platform not in selected:
                selected.add(platform)
                self._state.selected_platforms = tuple(item for item in PLATFORMS if item in selected)
                if self._state.bundle and selected != self._bundle_platforms_locked():
                    self._mark_stale_locked('已把平台加入发布选择；请重新生成同一发布包。')
            if self._state.platform != platform:
                self._state.platform = platform
                self._state.message = f'正在编辑{PLATFORM_LABELS[platform]}独立文稿。'
            self._recompute_locked()
            self._save_draft_safe_locked()
        self._emit()
        return True

    @Slot(str, bool)
    def togglePlatform(self, platform, selected):
        """Include or exclude one platform from the single frozen package."""
        platform = _text(platform).strip().lower()
        if platform not in PLATFORMS:
            self._notice('请选择 B站或小红书。')
            return False
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            current = set(self._state.selected_platforms)
            if selected:
                current.add(platform)
            else:
                current.discard(platform)
            if not current:
                self._notice('至少选择一个发布平台。')
                return False
            changed = current != set(self._state.selected_platforms)
            self._state.selected_platforms = tuple(item for item in PLATFORMS if item in current)
            if self._state.platform not in current:
                self._state.platform = self._state.selected_platforms[0]
            if changed and self._state.bundle and current != self._bundle_platforms_locked():
                self._mark_stale_locked('发布平台选择已变化；请重新生成同一发布包。')
            elif changed:
                labels = '、'.join(PLATFORM_LABELS[item] for item in self._state.selected_platforms)
                self._state.message = f'已选择在同一发布包内生成：{labels}。'
            self._recompute_locked()
            self._save_draft_safe_locked()
        self._emit()
        return True

    @Slot(str)
    def setCoverRatio(self, ratio):
        ratio = _text(ratio)
        if ratio not in BILIBILI_COVER_RATIOS:
            self._notice('请选择 4:3 或 16:9 封面比例。')
            return False
        with self._lock:
            if self._closed:
                return False
            self._state.cover_ratio = ratio
            self._recompute_locked()
        self._emit()
        return True

    @Slot(QUrl)
    def chooseCover(self, url):
        try:
            path = _local_path(url)
            if path.is_dir():
                raise ValueError('请选择图片文件。')
            _sniff_image(path)
            with self._lock:
                if self._state.busy or self._active_kind:
                    raise ValueError('发布操作进行中，暂时不能修改封面。')
                self._maybe_mark_drift_locked()
                for draft in self._state.drafts.values():
                    draft.cover_mode = 'custom'
                    draft.cover_file = str(path)
                    draft.cover_preview = _local_url(path)
                self._mark_stale_locked('本地封面已修改；原发布资料已过期。')
                self._state.message = '已选择本地封面。深度媒体校验将在生成或预览时进行。'
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot()
    def previewCover(self):
        with self._lock:
            if not self._validate_current_page_locked(0, raise_error=False):
                return False
            if self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            draft = self._state.draft()
            request = _PreviewRequest(
                self._state.job, self._state.video_ref, draft.cover_mode,
                float(draft.cover_seconds), draft.cover_file, self._state.original_cover)
            self._state.message = '正在核验并生成封面预览…'
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            _check_stop(stop)
            self._verify_video(request.job, request.video, stop)
            if request.mode == 'custom':
                path = _local_path(request.cover_file)
                _sniff_image(path)
                self._probe_image(path, stop)
                return {'path': str(path), 'temporary': False, 'message': '已校验本地封面。'}
            if request.mode == 'original':
                path = self._cached_original_cover(Path(request.job))
                if path is None:
                    raise ValueError('没有可核验的本家原封面。')
                return {'path': str(path), 'temporary': False, 'message': '已校验本家原封面。'}
            if request.mode not in ('frame', 'original-frame'):
                raise ValueError('未知封面来源。')
            _check_stop(stop)
            directory = Path(tempfile.mkdtemp(prefix='vwa-quick-publication-'))
            destination = directory / 'cover-preview.jpg'
            try:
                source = (self._original_video(request.job, stop)
                          if request.mode == 'original-frame' else request.video.path)
                stamp = publication.publication_file_stamp(source)
                publication.extract_cover(source, destination, request.seconds)
                if publication.publication_file_stamp(source) != stamp:
                    raise ValueError('截图期间视频已变化，请重新预览。')
                _check_stop(stop)
            except Exception:
                safe_rmtree(directory)
                raise
            return {'path': str(destination), 'temporary': True,
                    'directory': str(directory),
                    'message': '已从本家原视频截取封面。' if request.mode == 'original-frame' else '已从成品视频截取封面。'}

        return self._start_work('publication-preview-cover', work, self._finish_preview)

    def _original_video(self, job, stop):
        from pipeline import ensure_source_video
        from task_store import known_job
        path = known_job(job)
        _check_stop(stop)
        source = Path(ensure_source_video(path)).resolve(strict=True)
        if source != path / 'video/source.mp4':
            raise ValueError('本家原视频路径与当前任务不一致。')
        _check_stop(stop)
        return source

    @Slot()
    def nextStep(self):
        return self._move_step(delta=1)

    @Slot()
    def previousStep(self):
        return self._move_step(delta=-1)

    @Slot(int)
    def reviewStep(self, target):
        """Jump from a review item using the same forward checks as the stepper."""
        return self._move_step(target=target)

    def _move_step(self, *, target=None, delta=0):
        try:
            with self._lock:
                if self._closed or self._state.busy:
                    return False
                self._maybe_mark_drift_locked()
                current = self._state.step
                if target is None:
                    target = min(2, max(0, current + delta))
                if target not in (0, 1, 2):
                    return False
                imported = self._state.bundle_only and self._state.bundle and not self._state.bundle_stale
                if not imported:
                    for step in range(current, target):
                        if not self._validate_current_page_locked(step, raise_error=True):
                            return False
                self._state.step = target
                if target < current:
                    self._state.message = '可返回修改当前步骤；已生成资料会在修改后标记为过期。'
                elif target > current:
                    self._state.message = ('请检查并预览封面，然后核对发布资料。' if target == 1
                                           else '请核对文稿、封面和本地声明，再生成发布资料。')
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot()
    def prepareBundle(self):
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if self._state.step != 2:
                self._notice('请先完成文稿与封面步骤，再生成发布资料。')
                return False
            problem = self._prepare_problem_locked()
            if problem:
                self._notice(problem)
                return False
            snapshot = self._snapshot_locked()
            self._state.message = '正在复核成品和来源，并生成不可变发布资料…'
            self._recompute_locked()
        self._emit()

        def work(stop, send):
            _check_stop(stop)
            from task_contract import register_publication
            from pipeline import job_lock
            from task_store import known_job, read_json

            job = known_job(snapshot.job)
            local = isinstance(snapshot.video, _LocalVideoRef)
            binding = read_json(job / 'task.json').get('bindings', {}).get('video')
            previous = (binding['id'], binding['entry']['revision']) if binding else ('', '')
            send({'message': '正在核验选定的成品视频…'})
            verified_video = self._verify_video(snapshot.job, snapshot.video, stop)
            if snapshot.cover_mode == 'original-frame':
                send({'message': '正在核验本家原视频；必要时仅补齐原视频文件…'})
                self._original_video(snapshot.job, stop)
            _check_stop(stop)
            send({'message': '正在生成文稿和封面…'})
            content = {
                platform: copy.deepcopy(dict(snapshot.contents[platform]))
                for platform in snapshot.selected_platforms
            }
            bundle = publication.prepare(
                snapshot.job,
                str(verified_video.path),
                content,
                snapshot.cover_seconds,
                snapshot.cover_file or None,
                cover_mode=snapshot.cover_mode,
                expected_video=(verified_video.as_row() if not local else {'sha256': verified_video.sha256}),
                check=stop.is_set,
                register=False,
            )
            _check_stop(stop)
            bundle = Path(publication.clean_path(bundle)).resolve()
            data = _read_json(bundle / 'manifest.json')
            if (not isinstance(data, dict) or data.get('state') != 'prepared'
                    or data.get('final_publish') != 'human_only'):
                raise ValueError('生成后的发布资料状态无效。')
            if Path(publication.clean_path(data.get('source_job', ''))).resolve() != Path(snapshot.job):
                raise ValueError('生成后的发布资料来源任务不一致。')
            video_data = data.get('video') if isinstance(data.get('video'), dict) else {}
            if (Path(publication.clean_path(video_data.get('path', ''))).resolve() != verified_video.path
                    or video_data.get('sha256') != verified_video.sha256):
                raise ValueError('生成后的发布资料没有绑定已核验视频。')
            send({'message': '正在完整校验新发布资料与各平台交接…'})
            self._assert_snapshot_content(snapshot, data)
            handoffs, errors = self._handoffs_for_snapshot(snapshot, bundle, data, stop)
            if errors:
                raise ValueError('；'.join(errors.values()))
            variants = self._cover_variants(bundle, data)
            if snapshot.source_stamp != self._source_stamp(
                    job, verified_video.path, Path(self._state.original_cover) if self._state.original_cover else None):
                raise ValueError('来源资料在准备期间变化，请重新核对。')
            _check_stop(stop)
            with job_lock(job):
                receipt = register_publication(job, bundle,
                    expected_video=None if local else verified_video.as_row(),
                    new_video_binding=previous if local else None, check=stop.is_set)
            # The task now owns both files. A late Stop cannot undo the commit
            # or make the GUI report that nothing was registered.
            return {'bundle': str(bundle), 'data': data, 'handoffs': handoffs,
                    'cover_variants': variants, 'bundle_ref': receipt['bundle'],
                    'video_ref': receipt['video']}

        return self._start_work('publication-prepare', work, self._finish_prepare)

    @Slot()
    def checkBundle(self):
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if not self._state.bundle or self._state.bundle_stale:
                self._notice('当前文稿与发布资料不一致，请重新生成后再核对。')
                return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            self._state.message = '正在重新核验任务、成品、封面与平台文稿…'
            self._recompute_locked()
        self._emit()

        def work(stop, send):
            _check_stop(stop)
            send({'message': '正在核验任务产物…'})
            return self._verify_current_bundle(snapshot, bundle_ref, stop)

        return self._start_work('publication-check', work, self._finish_check)

    @Slot(str)
    def copyField(self, field):
        with self._lock:
            platform = self._state.platform
        return self.copyFieldFor(platform, field)

    @Slot(str, str)
    def copyFieldFor(self, platform, field):
        """Copy one field from that platform's verified, independent handoff."""
        try:
            platform = _text(platform).strip().lower()
            with self._lock:
                if self._closed:
                    return False
                self._maybe_mark_drift_locked()
                if platform not in self._state.selected_platforms:
                    raise ValueError('该平台不在当前发布包中。')
                if not self._state.verified or self._state.bundle_stale:
                    raise ValueError('请先核对当前发布资料，再复制交接内容。')
                handoff = self._state.handoffs.get(platform)
                if not isinstance(handoff, dict):
                    raise ValueError('该平台没有可用的已核验交接。')
                fields = handoff.get('fields') if isinstance(handoff.get('fields'), dict) else {}
                name = _text(field).strip().lower()
                cover = self._handoff_cover_path_locked(platform)
                values = {
                    'title': _text(fields.get('title')),
                    'description': _text(fields.get('description')),
                    'tags': _tags_text(fields.get('tags')),
                    'video': str(handoff.get('video') or ''),
                    'cover': str(cover or ''),
                    'bundle': self._state.bundle,
                }
                values['all'] = '\n\n'.join(value for value in (
                    values['title'], values['description'], values['tags']) if value)
                if name not in values or not values[name]:
                    raise ValueError('该字段没有可复制的内容。')
                text = values[name]
            copied = False
            hook = getattr(self._owner, 'copy_text', None)
            if callable(hook):
                copied = hook(text) is not False
            if not copied:
                app = QGuiApplication.instance()
                if app is None:
                    raise RuntimeError('剪贴板当前不可用。')
                app.clipboard().setText(text)
            with self._lock:
                self._state.message = f'已复制{PLATFORM_LABELS[platform]}交接内容。请在对应平台页面再次核对。'
                self._recompute_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot(str)
    def openMaterial(self, name):
        with self._lock:
            platform = self._state.platform
        return self.openMaterialFor(platform, name)

    @Slot(str, str)
    def openMaterialFor(self, platform, name):
        platform = _text(platform).strip().lower()
        name = _text(name).strip().lower()
        if platform not in PLATFORMS:
            self._notice('未知的发布平台。')
            return False
        if name not in ('video', 'cover', 'bundle', 'folder', 'checklist'):
            self._notice('未知的本地发布资料。')
            return False
        with self._lock:
            if self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if platform not in self._state.selected_platforms:
                self._notice('该平台不在当前发布包中。')
                return False
            if not self._state.verified or self._state.bundle_stale or not self._state.handoffs.get(platform):
                self._notice('请先核对当前发布资料，再打开本地素材。')
                return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            self._state.message = f'正在重新核验要打开的{name}…'
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            verified = self._verify_current_bundle(snapshot, bundle_ref, stop, platform=platform)
            bundle = Path(snapshot.bundle).resolve()
            handoff = verified['handoffs'][platform]
            if name == 'video':
                target = Path(handoff['video']).resolve()
            elif name == 'cover':
                target = self._cover_path_from_verified_locked(platform, verified)
            elif name == 'checklist':
                target = (bundle / '发布前核对.txt').resolve()
            else:
                target = bundle
            if not target.exists():
                raise ValueError('发布资料中的本地素材已经移动。')
            if name in ('bundle', 'folder'):
                if not target.is_dir() or target != bundle:
                    raise ValueError('发布资料目录无效。')
            elif not target.is_file():
                raise ValueError('要打开的发布素材无效。')
            return {'path': str(target), 'kind': name, 'platform': platform}

        return self._start_work('publication-open-material', work, self._finish_open_material)

    @Slot()
    def openCreator(self):
        with self._lock:
            platform = self._state.platform
        return self.openCreatorFor(platform)

    @Slot(str)
    def openCreatorFor(self, platform):
        platform = _text(platform).strip().lower()
        if platform not in PLATFORMS:
            self._notice('未知发布平台。')
            return False
        with self._lock:
            if self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if platform not in self._state.selected_platforms:
                self._notice('该平台不在当前发布包中。')
                return False
            if not self._state.verified or self._state.bundle_stale or not self._state.handoffs.get(platform):
                self._notice('请先核对当前发布资料，再打开平台。')
                return False
            if platform == BILIBILI:
                self._refresh_browser_locked()
                if self._browser_live_locked():
                    self._notice('B站填写会话已打开。请使用“继续填写”或“停止填写”。')
                    return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            self._state.message = ('正在复核发布资料，准备启动 B站填写会话…'
                                   if platform == BILIBILI else
                                   '正在复核发布资料，准备打开小红书创作中心…')
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            verified = self._verify_current_bundle(snapshot, bundle_ref, stop, platform=platform)
            return {'platform': platform, 'bundle': snapshot.bundle,
                    'handoff': verified['handoffs'][platform],
                    'data': verified['data']}

        return self._start_work('publication-open-creator', work, self._finish_open_creator)

    @Slot()
    def loginBilibili(self):
        """Start the existing isolated login window only when the user asks."""
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._refresh_browser_locked()
            if self._browser_live_locked():
                self._notice('B站填写会话已经打开；请先使用或停止当前会话。')
                return False
            storage_busy = self._storage_busy_locked()
            if storage_busy:
                self._notice('请等待缓存管理结束后再打开 B站登录会话。')
                return False
            try:
                self._launch_browser_locked(login_only=True, bundle=None)
            except Exception as exc:
                self._notice('无法启动 B站登录会话：' + str(exc))
                return False
            self._browser_timer.start()
            self._recompute_locked()
        self._emit()
        return True

    @Slot()
    def continueFill(self):
        """Ask the live helper to refill from this verified bundle without re-uploading."""
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            self._refresh_browser_locked()
            if not self._browser_live_locked():
                self._notice('B站填写会话已关闭，请重新打开并填写。')
                return False
            source_session = self._state.browser
            session = _BrowserSession(
                mode=source_session.mode, output=source_session.output,
                control_bundle=source_session.control_bundle,
                state=source_session.state, message=source_session.message,
                updated_at=source_session.updated_at,
                checks=dict(source_session.checks), returncode=source_session.returncode,
            )
            if session.mode != 'fill':
                self._notice('当前是登录会话；登录完成后请关闭窗口，再打开 B站填写。')
                return False
            if BILIBILI not in self._state.selected_platforms:
                self._notice('当前发布包不包含 B站。')
                return False
            if not self._state.verified or self._state.bundle_stale or not self._state.handoffs.get(BILIBILI):
                self._notice('当前 B站发布资料尚未核验，不能继续填写。')
                return False
            if not session.checks.get('video_selected'):
                self._notice('请先在 B站页面选择视频，再继续填写标题、简介与封面。')
                return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            self._state.message = '正在复核当前发布包，准备继续填写…'
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            _check_stop(stop)
            verified = self._verify_current_bundle(snapshot, bundle_ref, stop, platform=BILIBILI)
            active = self._read_active_browser_record()
            if not isinstance(active, dict):
                raise ValueError('B站填写会话已关闭。')
            if session.output and Path(_text(active.get('bundle'))).resolve() != Path(session.output).resolve():
                raise ValueError('B站填写会话已经切换到其他发布包，请重新打开当前资料。')
            current = Path(snapshot.bundle).resolve()
            active_bundle = Path(_text(active.get('bundle'))).resolve()
            old = _read_json(active_bundle / 'manifest.json')
            new = _read_json(current / 'manifest.json')
            old_video = old.get('video') if isinstance(old, dict) else None
            new_video = new.get('video') if isinstance(new, dict) else None
            if not isinstance(old_video, dict) or not isinstance(new_video, dict):
                raise ValueError('无法核对 B站填写会话绑定的成品。')
            if (Path(publication.clean_path(new_video.get('path', ''))).resolve()
                    != Path(publication.clean_path(old_video.get('path', ''))).resolve()
                    or new_video.get('sha256') != old_video.get('sha256')):
                raise ValueError('新发布包的成品与已上传视频不同，不能直接继续填写。')
            control = Path(_text(active.get('control_bundle')) or active_bundle).resolve()
            if control not in (active_bundle, current):
                raise ValueError('B站填写控制目录不属于当前发布交接。')
            _check_stop(stop)
            atomic_json(control / (BILIBILI + '.replacement.json'), {'bundle': str(current)})
            (control / (BILIBILI + '.repair')).touch()
            return {'bundle': str(current), 'handoff': verified['handoffs'][BILIBILI]}

        return self._start_work('publication-browser-continue', work, self._finish_continue_fill)

    @Slot()
    def stopFill(self):
        """Write the helper's public stop marker; never kill or submit automatically."""
        with self._lock:
            if self._closed or self._state.busy or self._active_kind:
                return False
            self._refresh_browser_locked()
            if not self._browser_live_locked():
                self._notice('当前没有可停止的 B站填写会话。')
                return False
            output = Path(self._state.browser.output or self._browser_profile_path()).resolve()
            try:
                output.mkdir(parents=True, exist_ok=True)
                (output / (BILIBILI + '.stop')).touch()
            except OSError as exc:
                self._notice('无法请求停止 B站填写会话：' + str(exc))
                return False
            self._state.browser.state = 'stopping'
            self._state.browser.message = '已请求停止；等待填写会话释放浏览器。'
            self._state.message = '已请求停止 B站填写；不会自动保存、提交或发布。'
            self._browser_timer.start()
            self._recompute_locked()
        self._emit()
        return True

    @Slot()
    def requestExit(self):
        with self._lock:
            if self._state.busy or self._active_kind:
                return self.cancel()
        navigate = getattr(self._owner, 'navigate', None)
        if callable(navigate):
            navigate('results')
            return True
        self._notice('当前页面无法离开；请使用左侧导航返回任务与结果。')
        return False

    @Slot()
    def cancel(self):
        with self._lock:
            if not self._active_kind or not self._active_kind.startswith('publication-'):
                return False
            self._cancel_requested = True
            self._state.message = '正在取消发布操作…'
            self._recompute_locked()
        self._worker.cancel()
        self._emit()
        return True

    def close(self):
        with self._lock:
            self._drift_timer.stop()
            self._browser_timer.stop()
            self._restore_timer.stop()
            if self._closed:
                return
            self._closed = True
            if self._active_kind:
                self._worker.cancel()
            stream = self._state.browser.log_stream
            self._state.browser.log_stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        try:
            self._worker.changed.disconnect(self._worker_changed)
        except (RuntimeError, TypeError):
            pass
        self._discard_preview_dir(self._preview_dir, None)
        self._preview_dir = None

    def _start_work(self, kind, work, finish):
        with self._lock:
            if self._closed:
                return False
            if self._active_kind:
                return False
            if self._worker.kind:
                self._notice('发布工作区正在处理其他操作，请稍候。')
                return False
            self._active_kind = kind
            self._cancel_requested = False
            self._state.busy = True
            self._recompute_locked()
        started = self._worker.start(
            kind,
            work,
            lambda value, error, kind=kind, finish=finish: self._finish_work(kind, value, error, finish),
            progress=self._progress,
        )
        if not started:
            with self._lock:
                if self._active_kind == kind:
                    self._active_kind = ''
                self._state.busy = False
                self._state.message = '无法启动发布操作，请稍后重试。'
                self._recompute_locked()
            self._emit()
            return False
        self._emit()
        return True

    def _finish_work(self, kind, value, error, finish):
        try:
            if not self._closed:
                try:
                    finish(value, error)
                except Exception as exc:
                    self._notice('发布工作区处理结果失败：' + str(exc), state='error')
                    if error is None:
                        report = getattr(self.parent(), 'report_problem', None)
                        if report is not None:
                            report(kind, exc)
        finally:
            with self._lock:
                if self._active_kind == kind:
                    self._active_kind = ''
                self._state.busy = False
                self._cancel_requested = False
                self._recompute_locked()
            self._emit()


    def _worker_changed(self):
        with self._lock:
            if self._closed:
                return
            self._recompute_locked()
        self._emit()

    def _progress(self, value):
        if isinstance(value, dict) and isinstance(value.get('message'), str):
            with self._lock:
                if self._active_kind:
                    self._state.message = value['message']
                    self._recompute_locked()
            self._emit()

    def _finish_preview(self, value, error):
        with self._lock:
            if error is not None:
                if isinstance(error, InterruptedError) or self._cancel_requested:
                    self._state.message = '已取消封面预览。'
                else:
                    self._state.message = '封面预览失败：' + str(error)
                self._recompute_locked()
                return
            path = Path(value['path']).resolve()
            if not path.is_file() or path.stat().st_size <= 0:
                self._state.message = '封面预览文件无效。'
                self._recompute_locked()
                return
            old = self._preview_dir
            new = Path(value['directory']).resolve() if value.get('temporary') else None
            for draft in self._state.drafts.values():
                draft.cover_preview = _local_url(path)
            self._preview_dir = new
            self._state.message = _text(value.get('message')) or '封面预览已更新。'
            self._recompute_locked()
        self._discard_preview_dir(old, new)

    def _finish_prepare(self, value, error):
        with self._lock:
            if error is not None:
                if isinstance(error, InterruptedError) or self._cancel_requested:
                    self._state.state = 'cancelled'
                    self._state.message = '已在登记前停止；任务原有成品和发布资料保留。'
                else:
                    self._state.state = 'failed'
                    self._state.message = '生成发布资料失败：' + str(error)
                self._recompute_locked()
                return
            bundle = Path(value['bundle']).resolve()
            data = value.get('data')
            if not isinstance(data, dict):
                self._state.state = 'failed'
                self._state.message = '生成结果缺少发布资料 manifest。'
                self._recompute_locked()
                return
            video_ref = value.get('video_ref')
            if not isinstance(video_ref, Mapping):
                self._state.state = 'failed'
                self._state.message = '生成结果缺少已核验成品视频身份。'
                self._recompute_locked()
                return
            self._state.video_ref = _artifact_ref(video_ref, 'video')
            self._state.video = str(self._state.video_ref.path)
            self._state.bundle = str(bundle)
            self._state.bundle_only = False
            self._state.bundle_stamp = self._bundle_stamp(bundle, data)
            self._state.source_stamp = (self._source_stamp(Path(self._state.job), Path(self._state.video), Path(self._state.original_cover) if self._state.original_cover else None)
                                         if self._state.job and self._state.video else ())
            self._state.source_drift = False
            handoffs = value.get('handoffs')
            self._state.handoffs = copy.deepcopy(handoffs) if isinstance(handoffs, dict) else {}
            self._state.platform_errors = {}
            variants = value.get('cover_variants')
            self._state.cover_variants = (copy.deepcopy(variants)
                                          if isinstance(variants, dict)
                                          else self._cover_variants(bundle, data))
            ref = value.get('bundle_ref')
            try:
                self._state.bundle_ref = _artifact_ref(ref, 'publication') if ref is not None else None
            except Exception:
                self._state.bundle_ref = None
            self._state.bundle_stale = False
            self._state.verified = True
            self._state.state = 'prepared'
            self._state.step = 2
            cover = bundle / 'cover.jpg'
            if cover.is_file():
                for draft in self._state.drafts.values():
                    draft.cover_preview = _local_url(cover)
            labels = '、'.join(PLATFORM_LABELS[item] for item in self._state.selected_platforms)
            self._state.message = f'{labels}发布资料已生成并核对；各平台交接相互独立，最终发布仍由你确认。'
            self._recompute_locked()
            self._save_draft_safe_locked()

    def _finish_check(self, value, error):
        with self._lock:
            if error is not None:
                self._state.verified = False
                if isinstance(error, InterruptedError) or self._cancel_requested:
                    self._state.state = 'cancelled'
                    self._state.message = '已取消发布资料核对。'
                else:
                    self._state.bundle_stale = True
                    self._state.state = 'outdated'
                    self._state.message = '发布资料核对失败：' + str(error)
                self._recompute_locked()
                return
            handoffs = value.get('handoffs') if isinstance(value, dict) else None
            data = value.get('data') if isinstance(value, dict) else None
            if not isinstance(handoffs, dict) or not isinstance(data, dict):
                self._state.verified = False
                self._state.bundle_stale = True
                self._state.state = 'outdated'
                self._state.message = '核对结果不完整，请重新生成发布资料。'
                self._recompute_locked()
                return
            self._state.handoffs = copy.deepcopy(handoffs)
            self._state.platform_errors = dict(value['platform_errors'])
            variants = value.get('cover_variants')
            self._state.cover_variants = (copy.deepcopy(variants)
                                          if isinstance(variants, dict)
                                          else self._cover_variants(
                                              Path(self._state.bundle), data))
            self._state.bundle_stamp = self._bundle_stamp(Path(self._state.bundle), data)
            self._state.source_stamp = self._source_stamp(Path(self._state.job), Path(self._state.video), Path(self._state.original_cover) if self._state.original_cover else None)
            self._state.source_drift = False
            self._state.bundle_stale = False
            self._state.verified = bool(handoffs)
            self._state.state = 'ready' if handoffs else 'failed'
            labels = '、'.join(PLATFORM_LABELS[item] for item in handoffs)
            failures = '；'.join(self._state.platform_errors.values())
            self._state.message = '；'.join(filter(None, (f'{labels}交接已核验' if labels else '', failures)))
            self._recompute_locked()

    def _finish_open_material(self, value, error):
        if error is not None:
            self._notice('无法打开本地素材：' + str(error))
            return
        if not isinstance(value, dict):
            self._notice('无法打开本地素材。')
            return
        target = Path(value.get('path', '')).resolve()
        if not target.exists():
            self._notice('本地素材已经移动，请重新核对发布资料。')
            return
        if not self._open_local(target):
            self._notice('系统无法打开该本地素材：' + str(target))

    def _finish_open_creator(self, value, error):
        if error is not None:
            self._notice('无法打开平台：' + str(error))
            return
        if not isinstance(value, dict):
            self._notice('平台交接结果无效。')
            return
        platform = _text(value.get('platform'))
        if platform == XIAOHONGSHU:
            if not self._open_url(XIAOHONGSHU_CREATOR_URL):
                self._notice('系统无法打开小红书创作中心，请手动访问：' + XIAOHONGSHU_CREATOR_URL)
            else:
                with self._lock:
                    self._state.message = (
                        '已打开小红书创作中心。请使用小红书交接区的复制按钮，'
                        '手动上传视频与封面并核对 AI、原创/转载与授权。'
                    )
                    self._recompute_locked()
                self._emit()
            return
        if platform != BILIBILI:
            self._notice('未知发布平台。')
            return
        with self._lock:
            try:
                self._launch_browser_locked(login_only=False, bundle=Path(value.get('bundle')))
            except Exception as exc:
                self._notice('无法启动 B站填写会话：' + str(exc))
                return
            self._browser_timer.start()
            self._state.message = (
                '已启动 B站填写会话。请在网页登录并选择视频；其余字段仍由你确认，'
                '助手不会提交最终投稿。'
            )
            self._recompute_locked()
        self._emit()

    def _finish_continue_fill(self, value, error):
        if error is not None:
            self._notice('无法继续填写：' + str(error))
            return
        with self._lock:
            self._refresh_browser_locked()
            if not self._browser_live_locked():
                self._notice('B站填写会话已关闭；继续填写未生效。')
                return
            self._state.message = (
                '已请求在现有 B站页面继续填写当前资料；视频不会重新上传，'
                '请核对网页字段与最终声明。'
            )
            self._browser_timer.start()
            self._recompute_locked()
        self._emit()

    def _browser_root_path(self) -> Path:
        return _browser_svc.browser_root_path(self._owner)

    def _browser_profile_path(self) -> Path:
        return _browser_svc.browser_profile_path(self._owner)

    def _read_active_browser_record(self):
        return _browser_svc.read_active_browser_record(self._owner)

    def _browser_output_allowed(self, path: Path) -> bool:
        return _browser_svc.browser_output_allowed(self._owner, self._state, path)

    def _browser_profile_locked(self) -> bool:
        return _browser_svc.browser_profile_locked(self._owner)

    def _browser_live_locked(self) -> bool:
        return _browser_svc.browser_live(self._owner, self._state)

    def _storage_busy_locked(self) -> bool:
        return _browser_svc.storage_busy(self._owner)

    def _launch_browser_locked(self, *, login_only: bool, bundle: Path | None):
        _browser_svc.launch_browser(
            self._owner, self._state, login_only=login_only, bundle=bundle,
            script_dir=Path(__file__).resolve().parent,
        )

    def _status_candidates_locked(self) -> list[Path]:
        return _browser_svc.status_candidates(self._state)

    def _browser_log_locked(self):
        return _browser_svc.browser_log_path(self._owner, self._state)

    @Slot(result=str)
    def browserLogTail(self):
        from preparation_commands import read_log_tail
        try:
            with self._lock:
                path = self._browser_log_locked()
            return read_log_tail(path) if path is not None else '当前会话没有可读取的填写日志。'
        except OSError as exc:
            return '无法读取填写日志：' + str(exc)[:500]

    def _refresh_browser_locked(self):
        _browser_svc.refresh_browser(self._owner, self._state)

    def _browser_view_locked(self):
        return _browser_svc.browser_view(self._owner, self._state, self._active_kind)

    def _browser_signature_locked(self):
        return _browser_svc.browser_signature(self._owner, self._state, self._active_kind)

    def _poll_browser(self):
        changed = False
        with self._lock:
            if self._closed:
                return
            self._refresh_browser_locked()
            signature = self._browser_signature_locked()
            if signature != self._browser_sig:
                self._browser_sig = signature
                self._recompute_locked()
                changed = True
        if changed:
            self._emit()

    def _verify_video(self, job: str, expected: _ArtifactRef | _LocalVideoRef, stop):
        return _draft_svc.verify_video(self._owner, job, expected, stop, local_path_fn=_local_path)

    def _verify_bundle_row(self, job: str, expected: _ArtifactRef, stop, *, platform: str) -> _ArtifactRef:
        return _draft_svc.verify_bundle_row(
            self._owner, job, expected, stop, platform=platform, local_path_fn=_local_path,
        )

    def _verify_current_bundle(self, snapshot: _Snapshot, bundle_ref: _ArtifactRef | None, stop, *, platform=None):
        return _draft_svc.verify_current_bundle(
            self._owner, self._state, snapshot, bundle_ref, stop, platform=platform,
            handoffs_fn=self._handoffs_for_snapshot, local_path_fn=_local_path, local_url_fn=_local_url,
        )

    def _assert_snapshot_content(self, snapshot: _Snapshot, data: Mapping[str, Any]):
        _draft_svc.assert_snapshot_content(snapshot, data)

    def _handoffs_for_snapshot(self, snapshot: _Snapshot, bundle: Path, data: dict, stop, *,
                               bundle_ref=None, only=None):
        return _draft_svc.handoffs_for_snapshot(
            self._owner, snapshot, bundle, data, stop, bundle_ref=bundle_ref,
            only=only, local_path_fn=_local_path,
        )

    def _handoff_for_bilibili(self, bundle: Path, data: dict, stop) -> dict[str, Any]:
        return _draft_svc.handoff_for_bilibili(bundle, data, stop)

    def _cover_variants(self, bundle: Path, data: Mapping[str, Any]):
        return _draft_svc.cover_variants(bundle, data, local_url_fn=_local_url)

    def _cover_path_from_verified_locked(self, platform: str, verified: Mapping[str, Any]) -> Path:
        return _draft_svc.cover_path_from_verified(self._state, platform, verified)

    def _handoff_cover_path_locked(self, platform: str) -> Path | None:
        return _draft_svc.handoff_cover_path(self._state, platform)

    def _snapshot_locked(self, *, check_only: bool = False) -> _Snapshot:
        return _draft_svc.build_snapshot(self._state, check_only=check_only)

    @staticmethod
    def _declaration_problem(platform, draft):
        return _draft_svc.declaration_problem(platform, draft)

    def _content_for_draft_locked(self, platform: str, draft: _PlatformDraft, *, require_declarations=True) -> dict[str, Any]:
        return _draft_svc.content_for_draft(platform, draft, require_declarations=require_declarations)

    def _contents_locked(self) -> dict[str, dict[str, Any]]:
        return _draft_svc.contents_for_state(self._state)

    def _bundle_declaration_problem_locked(self) -> str:
        return _draft_svc.bundle_declaration_problem(self._state)

    def _prepare_problem_locked(self) -> str:
        return _draft_svc.prepare_problem(self._state)

    def _cover_problem_locked(self):
        return _draft_svc.cover_problem(self._state)

    def _validate_current_page_locked(self, step: int, *, raise_error: bool) -> bool:
        problem = _draft_svc.page_validation_problem(self._state, step)
        if problem and raise_error:
            self._notice(problem)
        return not problem

    def _mark_stale_locked(self, reason: str):
        _draft_svc.mark_stale(self._state, reason)

    def _recompute_locked(self):
        self._view = _draft_svc.recompute_state(self._owner, self._state, self._active_kind)

    def _platform_handoffs_view_locked(self):
        return _draft_svc.platform_handoffs_view(self._state)

    def _review_locked(self):
        return _draft_svc.review_view(self._state)

    def _recompute_view_locked(self):
        return _draft_svc.build_view(self._owner, self._state, self._active_kind)

    def _actions_locked(self):
        return _draft_svc.actions_view(self._state)

    def _source_stamp(self, job: Path, video: Path, cover: Path | None) -> tuple:
        return _draft_svc.source_stamp(job, video, cover)

    def _bundle_stamp(self, bundle: Path, data: Mapping[str, Any]) -> tuple:
        return _draft_svc.bundle_stamp(bundle, data)

    def _bundle_platforms_locked(self) -> set[str]:
        return _draft_svc.bundle_platforms(self._state)

    def _poll_drift(self):
        with self._lock:
            if self._closed or (not self._state.video_ref and not self._state.bundle) or self._state.busy:
                return
            changed = self._maybe_mark_drift_locked()
        if changed:
            self._emit()

    def _maybe_mark_drift_locked(self) -> bool:
        drift = _draft_svc.maybe_mark_drift(self._state)
        if drift:
            self._recompute_locked()
        return drift

    def _cached_original_cover(self, job: Path) -> Path | None:
        return _draft_svc.cached_original_cover(job)

    def _probe_image(self, path: Path, stop) -> None:
        _draft_svc.probe_image(path, stop)

    def _draft_path(self) -> Path:
        return _draft_svc.draft_path(self._owner)

    def _load_saved_draft(self, job: Path, video: _ArtifactRef, source_stamp: tuple):
        return _draft_svc.load_saved_draft(self._owner, job, video, source_stamp)

    def _draft_from_saved(self, value: Mapping[str, Any], fallback: _PlatformDraft) -> _PlatformDraft:
        return _draft_svc.draft_from_saved(value, fallback, local_url_fn=_local_url)

    def _save_draft_safe_locked(self):
        _draft_svc.save_draft_safe(self._owner, self._state)

    def _discard_preview_dir(self, directory: Path | None, keep: Path | None):
        _draft_svc.discard_preview_dir(directory, keep)

    def _open_local(self, path: Path) -> bool:
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _open_url(self, url: str) -> bool:
        return QDesktopServices.openUrl(QUrl(url))

    def _notice(self, message: str, *, state: str | None = None):
        with self._lock:
            if self._closed:
                return
            self._state.message = str(message)
            if state is not None:
                self._state.state = state
            self._recompute_locked()
        self._emit()

    def _emit(self):
        if not self._closed:
            self.viewChanged.emit()


__all__ = ['QuickPublication']
