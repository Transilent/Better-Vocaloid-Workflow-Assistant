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
import shutil
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
import quick_results


BILIBILI = 'bilibili'
XIAOHONGSHU = 'xiaohongshu'
PLATFORMS = (BILIBILI, XIAOHONGSHU)
PLATFORM_LABELS = {BILIBILI: 'B站', XIAOHONGSHU: '小红书'}
COVER_MODES = ('original', 'custom', 'frame', 'original-frame')
COVER_LABELS = {'original': '本家原封面', 'custom': '本地图片', 'frame': '成品视频截帧',
                'original-frame': '本家原视频截帧'}
TAG_LIMITS = {BILIBILI: (10, 20), XIAOHONGSHU: (10, 20)}
RIGHTS_MAX_CHARS = 500
IMAGE_SUFFIXES = frozenset(('.jpg', '.jpeg', '.png', '.webp'))
XIAOHONGSHU_CREATOR_URL = publication.XIAOHONGSHU_CREATOR_URL
BILIBILI_COVER_RATIOS = ('4:3', '16:9')
BILIBILI_COVER_SIZES = {'4:3': (1440, 1080), '16:9': (1920, 1080)}
BROWSER_STATUS_FILENAME = BILIBILI + '_上传状态.json'
_HEX_ID = re.compile(r'^[a-f0-9]{32}$')
_HEX_SHA = re.compile(r'^[a-f0-9]{64}$')
_MAX_DRAFT_BYTES = 256 * 1024
_MAX_BROWSER_STATUS_BYTES = 64 * 1024
_TAG_SPLIT = re.compile(r'[\s,，、]+')


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ''


def _local_path(value: Any) -> Path:
    if isinstance(value, QUrl):
        if not value.isLocalFile():
            raise ValueError('请选择本机文件。')
        value = value.toLocalFile()
    if not isinstance(value, str) or not value.strip():
        raise ValueError('文件路径为空。')
    return Path(value).expanduser().resolve()


def _same_path(left: Any, right: Any) -> bool:
    try:
        return bool(left and right and Path(left).expanduser().resolve() == Path(right).expanduser().resolve())
    except (OSError, TypeError, ValueError):
        return False


def _bundle_directory(value: Any) -> Path:
    path = _local_path(value)
    if path.is_file():
        if path.name != 'manifest.json':
            raise ValueError('请选择发布资料文件夹或其 manifest.json。')
        path = path.parent
    if not path.is_dir() or not (path / 'manifest.json').is_file():
        raise ValueError('所选目录不是包含 manifest.json 的发布资料。')
    return path.resolve()


def _inspect_bundle(bundle: Path) -> dict[str, Any]:
    data = _read_json(Path(bundle) / 'manifest.json')
    if not isinstance(data, dict):
        raise ValueError('发布资料 manifest 格式无效。')
    if data.get('state') not in (None, 'prepared'):
        raise ValueError('发布资料尚未处于可读的准备状态。')
    if data.get('final_publish') not in (None, 'human_only'):
        raise ValueError('发布资料未声明最终投稿由用户完成。')
    content = data.get('content')
    if not isinstance(content, dict) or not content:
        raise ValueError('发布资料没有可恢复的平台文稿。')
    unsupported = set(content) - set(PLATFORMS)
    if unsupported:
        raise ValueError('发布资料包含不支持的平台。')
    for platform in content:
        publication.load_bundle(Path(bundle), platform, verify_hash=False)
    return data


def _read_json(path: Path, limit: int = 2 * 1024 * 1024) -> Any:
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f'{path.name} 文件过大。')
    return json.loads(path.read_text(encoding='utf-8'))


def _optional_json(path: Path, limit: int = 2 * 1024 * 1024) -> Any:
    try:
        return _read_json(path, limit)
    except (OSError, ValueError, TypeError):
        return None


def _check_stop(stop) -> None:
    if stop is not None and stop.is_set():
        raise InterruptedError('发布操作已取消。')


def _sniff_image(path: Path) -> None:
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError('封面图片不存在或为空。')
    if path.suffix.casefold() not in IMAGE_SUFFIXES:
        raise ValueError('请选择 JPG、PNG 或 WebP 封面。')
    with path.open('rb') as stream:
        head = stream.read(16)
    if head.startswith(b'\xff\xd8\xff'):
        return
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return
    raise ValueError('封面图片格式无法识别。')


def _song_title(value: str) -> str:
    title = re.sub(r'^(?:\s*【[^】]+】\s*)+', '', value or '').strip()
    return title.split('丨', 1)[0].split('|', 1)[0].strip().strip('《》')


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = _TAG_SPLIT.split(value.strip())
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError('标签必须是文本。')
            parts.extend(_TAG_SPLIT.split(item.strip().lstrip('#')))
    else:
        raise ValueError('标签必须是文本。')
    result = []
    for item in parts:
        item = item.strip().lstrip('#').strip()
        if item:
            result.append(item)
    return result


def _tags_text(value: Any) -> str:
    return ' '.join('#' + item for item in _tags(value))


def _local_url(path: Any) -> str:
    return QUrl.fromLocalFile(str(Path(path).resolve())).toString() if path else ''


def _stat_stamp(path: Any) -> tuple[str, int, int] | None:
    try:
        item = Path(path).resolve()
        stat = item.stat()
        return str(item), int(stat.st_size), int(stat.st_mtime_ns)
    except (OSError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _ArtifactRef:
    id: str
    revision: str
    sha256: str
    path: Path
    bytes: int
    role: str
    row: Mapping[str, Any] = field(repr=False, compare=False)

    def as_row(self) -> dict[str, Any]:
        value = copy.deepcopy(dict(self.row))
        value.update({'id': self.id, 'revision': self.revision, 'sha256': self.sha256,
                      'path': str(self.path), 'bytes': self.bytes,
                      'role': self.role, 'state': 'verified'})
        return value

    def same_identity(self, other: '_ArtifactRef') -> bool:
        return (self.id == other.id and self.revision == other.revision
                and self.sha256 == other.sha256 and self.path == other.path
                and self.bytes == other.bytes)


def _artifact_ref(row: Any, role: str) -> _ArtifactRef:
    if not isinstance(row, Mapping):
        raise ValueError('任务产物条目无效。')
    if row.get('state') != 'verified' or row.get('available') is False:
        raise ValueError('选中的任务产物尚未通过核验。')
    if row.get('role') != role:
        raise ValueError(f'任务产物角色不是 {role}。')
    identifier = _text(row.get('id'))
    revision = _text(row.get('revision'))
    sha256 = _text(row.get('sha256'))
    if (not _HEX_ID.fullmatch(identifier) or not _HEX_SHA.fullmatch(revision)
            or not _HEX_SHA.fullmatch(sha256)):
        raise ValueError('任务产物缺少完整 id、revision 或 sha256。')
    path = _local_path(row.get('path'))
    if not path.is_file():
        raise ValueError('任务产物文件不存在，可能已移动或清理。')
    size = row.get('bytes')
    if type(size) is not int or size < 0 or path.stat().st_size != size:
        raise ValueError('任务产物字节数与当前文件不一致，请刷新任务后重试。')
    return _ArtifactRef(identifier, revision, sha256, path, size, role, copy.deepcopy(dict(row)))


@dataclass(frozen=True)
class _LocalVideoRef:
    path: Path
    sha256: str
    bytes: int


@dataclass
class _PlatformDraft:
    title: str = ''
    description: str = ''
    tags: str = ''
    ai: bool | None = None
    rights: str | None = None
    cover_mode: str = 'frame'
    cover_seconds: float = 20.0
    cover_file: str = ''
    cover_preview: str = ''


@dataclass
class _BrowserSession:
    process: Any = field(default=None, repr=False, compare=False)
    log_stream: Any = field(default=None, repr=False, compare=False)
    mode: str = ''
    output: str = ''
    control_bundle: str = ''
    log_path: str = ''
    state: str = 'idle'
    message: str = '尚未启动 B站填写会话。'
    updated_at: str = ''
    checks: dict[str, bool] = field(default_factory=dict)
    returncode: int | None = None
    started_ns: int = 0


@dataclass
class _PublicationState:
    step: int = 0
    job: str = ''
    video: str = ''
    platform: str = BILIBILI
    selected_platforms: tuple[str, ...] = (BILIBILI,)
    drafts: dict[str, _PlatformDraft] = field(default_factory=lambda: {
        platform: _PlatformDraft() for platform in PLATFORMS})
    busy: bool = False
    message: str = '请选择一个已核验的成品视频和来源任务。'
    state: str = 'empty'
    bundle: str = ''
    bundle_stale: bool = False
    can_prepare: bool = False
    can_check: bool = False
    verified: bool = False
    video_ref: _ArtifactRef | _LocalVideoRef | None = None
    bundle_ref: _ArtifactRef | None = None
    original_cover: str = ''
    source_ready: bool = False
    source_problem: str = '尚未载入来源任务。'
    source_mode: str = 'unknown'
    source_stamp: tuple = ()
    bundle_stamp: tuple = ()
    handoffs: dict[str, dict[str, Any]] = field(default_factory=dict)
    platform_errors: dict[str, str] = field(default_factory=dict)
    cover_ratio: str = '16:9'
    cover_variants: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    browser: _BrowserSession = field(default_factory=_BrowserSession)
    source_drift: bool = False
    bundle_only: bool = False
    task_choices: list[dict[str, Any]] = field(default_factory=list)
    task_message: str = '尚未读取任务列表。'

    def draft(self) -> _PlatformDraft:
        if self.platform not in self.drafts:
            self.drafts[self.platform] = _PlatformDraft()
        return self.drafts[self.platform]


@dataclass(frozen=True)
class _Snapshot:
    job: str
    video: _ArtifactRef | _LocalVideoRef | None
    bundle_ref: _ArtifactRef | None
    bundle: str
    platform: str
    selected_platforms: tuple[str, ...]
    title: str
    description: str
    tags: tuple[str, ...]
    ai: bool
    rights: str
    cover_mode: str
    cover_seconds: float
    cover_file: str
    contents: Mapping[str, Mapping[str, Any]]
    source_stamp: tuple
    bundle_stamp: tuple


@dataclass(frozen=True)
class _PreviewRequest:
    job: str
    video: _ArtifactRef
    mode: str
    seconds: float
    cover_file: str
    original_cover: str

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
                shutil.rmtree(directory, ignore_errors=True)
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
        override = getattr(self._owner, 'quick_publication_browser_root', None)
        return Path(override or (Path(ROOT) / 'cache/publish-browser')).resolve()

    def _browser_profile_path(self) -> Path:
        root = self._browser_root_path()
        return (root if root.name == BILIBILI else root / BILIBILI).resolve()

    def _read_active_browser_record(self):
        path = self._browser_profile_path() / 'active.json'
        try:
            value = _read_json(path, limit=_MAX_BROWSER_STATUS_BYTES)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def _browser_output_allowed(self, path: Path) -> bool:
        candidate = Path(path).resolve()
        profile = self._browser_profile_path()
        if candidate == profile:
            return True
        if self._state.bundle:
            try:
                if candidate == Path(self._state.bundle).resolve():
                    return True
            except (OSError, ValueError):
                pass
        try:
            return candidate.is_relative_to((Path(ROOT) / 'publish-packages').resolve())
        except (OSError, ValueError):
            return False

    def _browser_profile_locked(self) -> bool:
        profile = self._browser_profile_path()
        try:
            from task_store import locked
            return bool(locked(profile))
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def _browser_live_locked(self) -> bool:
        process = self._state.browser.process
        if process is not None:
            try:
                return process.poll() is None
            except OSError:
                return True
        return self._read_active_browser_record() is not None and self._browser_profile_locked()

    def _storage_busy_locked(self) -> bool:
        storage = getattr(self._owner, 'storage', None)
        if storage is None:
            support = getattr(self._owner, 'support', None)
            storage = getattr(support, 'storage', None)
        if isinstance(storage, Mapping):
            return bool(storage.get('busy'))
        busy = getattr(storage, 'busy', None)
        try:
            return bool(callable(busy) and busy())
        except Exception:
            return True

    def _launch_browser_locked(self, *, login_only: bool, bundle: Path | None):
        if self._browser_live_locked():
            raise ValueError('B站填写会话已经打开。')
        if self._storage_busy_locked():
            raise ValueError('缓存管理正在进行，请稍后再打开 B站。')
        script = Path(__file__).with_name('publish_browser.py').resolve()
        if not script.is_file():
            raise ValueError('找不到现有 B站填写服务 publish_browser.py。')
        override = getattr(self._owner, 'quick_publication_python', '')
        try:
            if override:
                python = Path(str(override)).resolve()
            else:
                python = Path(str(load_config().get('python') or sys.executable)).resolve()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError('无法确定 Python 运行环境。') from exc
        if not python.is_file():
            raise ValueError('配置的 Python 运行环境不存在。')
        profile = self._browser_profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        output = profile if login_only else Path(bundle).resolve()
        if not login_only and (bundle is None or not output.is_dir()):
            raise ValueError('请先生成并核对当前发布资料。')
        log_path = output / (BILIBILI + '_浏览器.log')
        log = log_path.open('a', encoding='utf-8')
        argv = [str(python), '-B', '-u', str(script), '--platform', BILIBILI]
        if login_only:
            argv.append('--login')
        else:
            argv.extend(['--bundle', str(output)])
        env = os.environ.copy()
        env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            process = subprocess.Popen(
                argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, env=env, creationflags=flags, close_fds=True,
            )
        except Exception:
            log.close()
            raise
        old = self._state.browser
        if old.log_stream is not None:
            try:
                old.log_stream.close()
            except OSError:
                pass
        self._state.browser = _BrowserSession(
            process=process, log_stream=log, mode='login' if login_only else 'fill',
            output=str(output), control_bundle=str(output), log_path=str(log_path),
            state='starting',
            message=('正在打开隔离登录窗口…' if login_only else '正在打开 B站填写会话…'),
            started_ns=time.time_ns(),
        )
        self._state.message = ('正在打开 B站隔离登录会话；登录后请关闭窗口。'
                               if login_only else
                               '正在打开 B站填写会话；登录、核对与最终发布由你完成。')

    def _status_candidates_locked(self) -> list[Path]:
        session = self._state.browser
        paths = []
        for value in (session.output, session.control_bundle):
            if value:
                path = Path(value).resolve() / BROWSER_STATUS_FILENAME
                if path not in paths:
                    paths.append(path)
        return paths

    def _browser_log_locked(self):
        session = self._state.browser
        profile = self._browser_profile_path()
        for directory in (session.output, self._state.bundle, str(profile)):
            if not directory:
                continue
            try:
                folder = Path(directory).resolve(strict=True)
                if not self._browser_output_allowed(folder):
                    continue
                path = folder / (BILIBILI + '_浏览器.log')
                if (path.is_file() and path.resolve(strict=True) == path and not path.is_symlink()
                        and not getattr(path.lstat(), 'st_file_attributes', 0) & 1024):
                    return path
            except (OSError, ValueError):
                continue
        return None

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
        session = self._state.browser
        active = self._read_active_browser_record()
        process = session.process
        own_live = False
        if process is not None:
            try:
                own_live = process.poll() is None
            except OSError:
                own_live = True
        if process is None and active is not None and self._browser_profile_locked():
            output = Path(_text(active.get('bundle'))).resolve()
            if not self._browser_output_allowed(output):
                return
            self._state.browser = _BrowserSession(
                mode='login' if output == self._browser_profile_path() else 'fill',
                output=str(output),
                control_bundle=str(Path(_text(active.get('control_bundle')) or output).resolve()),
                state='active',
                message='已发现现有 B站填写会话，正在读取回执…',
            )
            session = self._state.browser
            own_live = True
        live = own_live
        candidates = []
        for path in self._status_candidates_locked():
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                continue
            if session.started_ns and modified < session.started_ns:
                continue
            candidates.append((modified, path))
        if candidates:
            path = max(candidates)[1]
            try:
                status = _read_json(path, limit=_MAX_BROWSER_STATUS_BYTES)
            except (OSError, ValueError, TypeError):
                status = None
            if isinstance(status, dict):
                state = _text(status.get('state')) or 'active'
                checks = status.get('checks') if isinstance(status.get('checks'), dict) else {}
                session.state = state
                session.message = _text(status.get('message')) or state
                session.updated_at = _text(status.get('updated_at'))
                session.checks = {key: value for key, value in checks.items() if type(value) is bool}
                control = status.get('control_bundle')
                if isinstance(control, str) and control.strip():
                    session.control_bundle = str(Path(control).resolve())
        if not live and session.process is not None:
            try:
                returncode = session.process.poll()
            except OSError:
                returncode = None
            session.returncode = returncode
            stream, session.log_stream = session.log_stream, None
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            if session.state not in ('failed', 'cancelled', 'login_window_closed', 'needs_manual'):
                session.state = 'closed'
                session.message = ('B站填写会话已关闭；最终发布状态必须由你在网页确认。'
                                   if returncode in (None, 0) else
                                   'B站填写会话异常结束，请查看填写日志。')

    def _browser_view_locked(self):
        session = self._state.browser
        live = bool(self._browser_live_locked())
        selected = BILIBILI in self._state.selected_platforms
        verified = bool(
            selected and self._state.verified and not self._state.bundle_stale
            and self._state.handoffs.get(BILIBILI)
        )
        active = self._active_kind
        return {
            'state': session.state,
            'message': session.message,
            'active': live,
            'mode': session.mode,
            'pid': int(getattr(session.process, 'pid', 0) or 0),
            'updatedAt': session.updated_at,
            'checks': copy.deepcopy(session.checks),
            'output': session.output,
            'controlBundle': session.control_bundle,
            'returncode': session.returncode,
            'finalPublish': 'human_only',
            'hasLog': self._browser_log_locked() is not None,
            'canLogin': bool(not live and not self._state.busy and not active),
            'canOpen': bool(verified and not live and not self._state.busy and not active),
            'canContinue': bool(verified and live and session.mode == 'fill'
                                and session.state != 'stopping'
                                and session.checks.get('video_selected')
                                and not self._state.busy and not active),
            'canStop': bool(live and session.state != 'stopping'
                            and not self._state.busy and not active),
        }

    def _browser_signature_locked(self):
        browser = self._browser_view_locked()
        return (browser['state'], browser['message'], browser['active'], browser['mode'],
                tuple(sorted(browser['checks'].items())), browser['updatedAt'],
                browser['controlBundle'], browser['returncode'])

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
        _check_stop(stop)
        if isinstance(expected, _LocalVideoRef):
            path = Path(expected.path).resolve()
            if not path.is_file() or path.stat().st_size != expected.bytes:
                raise ValueError('所选本地成品已移动或大小发生变化，请重新选择。')
            publication.inspect_video(path)
            if file_hash(path, check=stop.is_set) != expected.sha256:
                raise ValueError('所选本地成品内容已变化，请重新选择。')
            _check_stop(stop)
            return expected
        verifier = getattr(self._owner, 'verify_task_artifact', None)
        if not callable(verifier):
            raise RuntimeError('主控制器未提供任务产物核验入口。')
        row = verifier(job, expected.as_row(), 'video', check=stop.is_set)
        _check_stop(stop)
        verified = _artifact_ref(row, 'video')
        if not expected.same_identity(verified):
            raise ValueError('成品视频在核验期间发生变化。')
        return verified

    def _verify_bundle_row(self, job: str, expected: _ArtifactRef, stop, *, platform: str) -> _ArtifactRef:
        verifier = getattr(self._owner, 'verify_task_artifact', None)
        if not callable(verifier):
            raise RuntimeError('主控制器未提供任务产物核验入口。')
        _check_stop(stop)
        row = verifier(job, expected.as_row(), 'publication', check=stop.is_set, platform=platform)
        _check_stop(stop)
        verified = _artifact_ref(row, 'publication')
        if not expected.same_identity(verified):
            raise ValueError('发布资料 manifest 在核验期间发生变化。')
        return verified

    def _verify_current_bundle(self, snapshot: _Snapshot, bundle_ref: _ArtifactRef | None, stop, *, platform=None):
        _check_stop(stop)
        verified_video = (self._verify_video(snapshot.job, snapshot.video, stop)
                          if snapshot.video is not None else None)
        bundle = Path(snapshot.bundle).resolve() if snapshot.bundle else None
        if bundle is None or not bundle.is_dir():
            raise ValueError('当前没有可核对的发布资料。')
        manifest_path = bundle / 'manifest.json'
        data = _read_json(manifest_path)
        if not isinstance(data, dict):
            raise ValueError('发布资料 manifest 格式无效。')
        if data.get('state') not in (None, 'prepared') or data.get('final_publish') not in (None, 'human_only'):
            raise ValueError('发布资料未处于只读准备状态。')
        video_data = data.get('video') if isinstance(data.get('video'), dict) else {}
        if verified_video is not None:
            if (Path(publication.clean_path(video_data.get('path', ''))).resolve() != verified_video.path
                    or video_data.get('sha256') != verified_video.sha256):
                raise ValueError('发布资料绑定的成品与当前已核验视频不一致。')
            if snapshot.job:
                if Path(publication.clean_path(data.get('source_job', ''))).resolve() != Path(snapshot.job).resolve():
                    raise ValueError('发布资料来源任务不一致。')
        else:
            if not self._state.bundle_only:
                raise ValueError('当前发布资料没有可核验的成品视频。')
        if bundle_ref is not None:
            expected_manifest = bundle / 'manifest.json'
            if not snapshot.job or bundle_ref.path.resolve() != expected_manifest.resolve():
                raise ValueError('登记的发布资料入口与当前目录不一致。')
        content = data.get('content') if isinstance(data.get('content'), dict) else {}
        if set(snapshot.selected_platforms) != set(content):
            raise ValueError('发布资料平台集合与当前选择不一致，请重新生成同一发布包。')
        self._assert_snapshot_content(snapshot, data)
        handoffs, errors = self._handoffs_for_snapshot(
            snapshot, bundle, data, stop, bundle_ref=bundle_ref, only=platform)
        if platform and platform not in handoffs:
            raise ValueError(errors.get(platform, '该平台没有可用交接。'))
        return {'data': data, 'handoffs': handoffs, 'platform_errors': errors,
                'cover_variants': self._cover_variants(bundle, data) if BILIBILI in handoffs else {}}

    def _assert_snapshot_content(self, snapshot: _Snapshot, data: Mapping[str, Any]):
        content = data.get('content') if isinstance(data, Mapping) else None
        if not isinstance(content, Mapping):
            raise ValueError('发布资料平台文稿无效。')
        for platform in snapshot.selected_platforms:
            fields = content.get(platform)
            expected = snapshot.contents.get(platform)
            if not isinstance(fields, dict) or not isinstance(expected, Mapping):
                raise ValueError('发布资料平台文稿无效。')
            if (fields.get('title') != expected.get('title')
                    or fields.get('description') != expected.get('description')
                    or _tags(fields.get('tags')) != list(expected.get('tags') or [])
                    or fields.get('ai_generated') is not expected.get('ai_generated')
                    or fields.get('rights') != expected.get('rights')):
                raise ValueError(
                    f'{PLATFORM_LABELS[platform]}文稿与当前编辑内容不一致，请重新生成。')

    def _handoffs_for_snapshot(self, snapshot: _Snapshot, bundle: Path, data: dict, stop, *,
                               bundle_ref=None, only=None):
        handoffs, errors = {}, {}
        for platform in ((only,) if only else snapshot.selected_platforms):
            _check_stop(stop)
            try:
                if platform not in snapshot.selected_platforms:
                    raise ValueError('该平台未被选中。')
                if bundle_ref is not None:
                    self._verify_bundle_row(snapshot.job, bundle_ref, stop, platform=platform)
                handoff = (publication.xiaohongshu_handoff(bundle)
                           if platform == XIAOHONGSHU else self._handoff_for_bilibili(bundle, data, stop))
                fields = handoff.get('fields') if isinstance(handoff, dict) else None
                if fields != data['content'][platform]:
                    raise ValueError('平台文稿在核对期间已变化。')
                # These files are optional for imported historical bundles, but
                # if present their removal invalidates the affected handoff.
                for suffix in ('标题.txt', '简介.txt'):
                    path = bundle / (platform + '_' + suffix)
                    if path.is_file():
                        handoff['stamps'][path] = publication.publication_file_stamp(path)
                handoffs[platform] = handoff
            except InterruptedError:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors[platform] = PLATFORM_LABELS[platform] + '核对失败：' + str(exc)[:500]
        return handoffs, errors

    def _handoff_for_bilibili(self, bundle: Path, data: dict, stop) -> dict[str, Any]:
        bundle = Path(bundle).resolve()
        manifest = bundle / 'manifest.json'
        video = Path(publication.clean_path(data['video']['path'])).resolve()
        cover = bundle / 'cover.jpg'
        paths = [manifest, video, cover]
        variants = data.get('platform_covers', {}).get(BILIBILI, {}) if isinstance(data.get('platform_covers'), dict) else {}
        if isinstance(variants, dict):
            for item in variants.values():
                if isinstance(item, dict) and isinstance(item.get('file'), str):
                    paths.append(bundle / item['file'])
        before = {path: publication.publication_file_stamp(path) for path in paths}
        _check_stop(stop)
        verified = publication.load_bundle(bundle, BILIBILI, verify_hash=True, require_ai=True)
        _check_stop(stop)
        if verified != data:
            raise ValueError('核对期间发布资料发生变化，请重新核对。')
        after = {path: publication.publication_file_stamp(path) for path in paths}
        if before != after:
            raise ValueError('核对期间发布资料或素材发生变化，请重新核对。')
        return {'fields': verified['content'][BILIBILI], 'video': video, 'cover': cover,
                'stamps': after}

    def _cover_variants(self, bundle: Path, data: Mapping[str, Any]):
        bundle = Path(bundle).resolve()
        result = {}
        covers = data.get('platform_covers') if isinstance(data, Mapping) else None
        bilibili = covers.get(BILIBILI) if isinstance(covers, Mapping) else None
        if not isinstance(bilibili, Mapping):
            return result
        items = []
        for ratio in BILIBILI_COVER_RATIOS:
            asset = bilibili.get(ratio)
            if not isinstance(asset, Mapping):
                continue
            name = _text(asset.get('file'))
            if not name or Path(name).name != name:
                continue
            path = bundle / name
            if not path.is_file():
                continue
            expected_width, expected_height = BILIBILI_COVER_SIZES[ratio]
            width = asset.get('width')
            height = asset.get('height')
            items.append({
                'ratio': ratio,
                'file': name,
                'path': str(path),
                'preview': _local_url(path),
                'width': int(width) if type(width) is int else expected_width,
                'height': int(height) if type(height) is int else expected_height,
            })
        if items:
            result[BILIBILI] = items
        return result

    def _cover_path_from_verified_locked(self, platform: str, verified: Mapping[str, Any]) -> Path:
        handoff = verified.get('handoffs', {}).get(platform) if isinstance(verified.get('handoffs'), dict) else {}
        if not isinstance(handoff, Mapping):
            raise ValueError('该平台没有已核验的封面交接。')
        if platform != BILIBILI:
            return Path(handoff.get('cover')).resolve()
        bundle = Path(self._state.bundle).resolve()
        variants = (verified.get('cover_variants', {}).get(BILIBILI, [])
                    if isinstance(verified.get('cover_variants'), dict) else [])
        ratio = self._state.cover_ratio
        item = next((value for value in variants if value.get('ratio') == ratio), None)
        target = bundle / item['file'] if isinstance(item, Mapping) else bundle / 'cover.jpg'
        if not target.is_file():
            raise ValueError('所选 B站封面比例文件不存在。')
        return target.resolve()

    def _handoff_cover_path_locked(self, platform: str) -> Path | None:
        handoff = self._state.handoffs.get(platform)
        if not isinstance(handoff, dict):
            return None
        if platform != BILIBILI:
            return Path(handoff.get('cover')).resolve() if handoff.get('cover') else None
        bundle = Path(self._state.bundle).resolve()
        variants = self._state.cover_variants.get(BILIBILI, [])
        item = next((value for value in variants if value.get('ratio') == self._state.cover_ratio), None)
        return (bundle / item['file']).resolve() if isinstance(item, Mapping) else Path(handoff.get('cover')).resolve()

    def _snapshot_locked(self, *, check_only: bool = False) -> _Snapshot:
        draft = self._state.draft()
        if check_only and self._state.bundle_only:
            data = _read_json(Path(self._state.bundle) / 'manifest.json')
            stored = data.get('content') if isinstance(data, dict) else None
            if not isinstance(stored, dict):
                raise ValueError('发布资料平台文稿无效。')
            contents = {platform: copy.deepcopy(dict(stored[platform]))
                        for platform in self._state.selected_platforms}
        else:
            contents = self._contents_locked()
        problem = (self._bundle_declaration_problem_locked() if check_only else self._prepare_problem_locked())
        if problem:
            raise ValueError(problem)
        return _Snapshot(
            self._state.job, self._state.video_ref, self._state.bundle_ref, self._state.bundle,
            self._state.platform, self._state.selected_platforms,
            draft.title.strip(), draft.description, tuple(_tags(draft.tags)),
            bool(draft.ai), _text(draft.rights), draft.cover_mode,
            float(draft.cover_seconds), draft.cover_file, contents,
            self._state.source_stamp, self._state.bundle_stamp,
        )

    @staticmethod
    def _declaration_problem(platform, draft):
        if type(draft.ai) is not bool:
            return (publication.AI_REQUIRED if platform == XIAOHONGSHU else
                    f'请明确声明{PLATFORM_LABELS[platform]}素材是否包含 AI 生成或合成内容。')
        if not _text(draft.rights).strip():
            return f'请填写{PLATFORM_LABELS[platform]}的原创、转载、署名与授权声明。'
        return ''

    def _content_for_draft_locked(self, platform: str, draft: _PlatformDraft, *, require_declarations=True) -> dict[str, Any]:
        content = {
            'title': draft.title.strip(),
            'description': draft.description,
            'tags': _tags(draft.tags),
            'rights': draft.rights,
        }
        if type(draft.ai) is bool:
            content['ai_generated'] = draft.ai
        publication.validate_content(content, platform)
        tags = content['tags']
        maximum, each = TAG_LIMITS[platform]
        if len(tags) > maximum:
            raise ValueError(f'{PLATFORM_LABELS[platform]}最多填写 {maximum} 个标签。')
        if any(len(tag) > each for tag in tags):
            raise ValueError(f'{PLATFORM_LABELS[platform]}每个标签最多 {each} 字，请删减或拆分。')
        if require_declarations and (problem := self._declaration_problem(platform, draft)):
            raise ValueError(problem)
        return content

    def _contents_locked(self) -> dict[str, dict[str, Any]]:
        if not self._state.selected_platforms:
            raise ValueError('至少选择一个发布平台。')
        return {
            platform: self._content_for_draft_locked(platform, self._state.drafts[platform])
            for platform in self._state.selected_platforms
        }

    def _bundle_declaration_problem_locked(self) -> str:
        if not self._state.bundle:
            return ''
        if self._state.bundle_only:
            return ''
        try:
            self._contents_locked()
        except Exception as exc:
            return str(exc)
        return ''

    def _prepare_problem_locked(self) -> str:
        if self._state.video_ref is None or not self._state.job:
            return '请选择一个已核验的成品视频和来源任务。'
        if not self._state.source_ready:
            return self._state.source_problem or '来源任务资料尚未准备完整。'
        try:
            self._contents_locked()
        except Exception as exc:
            return str(exc)
        return self._cover_problem_locked()

    def _cover_problem_locked(self):
        draft = self._state.draft()
        if draft.cover_mode == 'original':
            if not Path(self._state.original_cover).is_file():
                return '没有可核验的本家原封面；请选择本地图片或视频画面。'
        elif draft.cover_mode == 'custom':
            try:
                _sniff_image(Path(draft.cover_file))
            except (OSError, ValueError) as exc:
                return str(exc)
        elif draft.cover_mode == 'frame':
            if self._state.video_ref is None or not Path(self._state.video).is_file():
                return '没有可截取封面的已核验成品。'
        elif draft.cover_mode == 'original-frame':
            if self._state.source_mode != 'bilibili':
                return '只有 B站来源任务可以截取本家原视频。'
        else:
            return '请选择封面来源。'
        if not math.isfinite(float(draft.cover_seconds)) or not 0 <= float(draft.cover_seconds) < 86400:
            return '截图时间必须在 0 到 86400 秒之间。'
        return ''

    def _validate_current_page_locked(self, step: int, *, raise_error: bool) -> bool:
        problem = ''
        if step == 0:
            if self._state.video_ref is None or not self._state.job:
                problem = '请选择一个已核验的成品视频和来源任务。'
            elif not self._state.source_ready:
                problem = self._state.source_problem or '来源任务资料尚未准备完整。'
            else:
                try:
                    self._contents_locked()
                except Exception as exc:
                    problem = str(exc)
        elif step == 1:
            problem = self._cover_problem_locked()
        if problem and raise_error:
            self._notice(problem)
        return not problem

    def _mark_stale_locked(self, reason: str):
        self._state.verified = False
        self._state.handoffs = {}
        self._state.platform_errors = {}
        self._state.cover_variants = {}
        if self._state.bundle:
            self._state.bundle_stale = True
            self._state.state = 'outdated'
            self._state.message = reason

    def _recompute_locked(self):
        self._state.busy = bool(self._active_kind)
        validation = self._prepare_problem_locked()
        declaration = self._bundle_declaration_problem_locked()
        if self._state.bundle and declaration:
            self._state.bundle_stale = True
            self._state.verified = False
            self._state.handoffs = {}
            self._state.cover_variants = {}
            if not self._state.busy and self._state.state not in ('error', 'failed'):
                self._state.state = 'outdated'
        self._state.can_prepare = bool(
            self._state.step == 2 and not self._state.busy
            and self._state.video_ref is not None and not validation)
        self._state.can_check = bool(
            self._state.bundle and not self._state.busy and not self._state.bundle_stale)
        if self._state.bundle_stale or (self._state.verified and declaration):
            self._state.verified = False
        self._view = self._recompute_view_locked()


    def _platform_handoffs_view_locked(self):
        result = {}
        for platform, handoff in self._state.handoffs.items():
            if not isinstance(handoff, dict):
                continue
            fields = handoff.get('fields') if isinstance(handoff.get('fields'), dict) else {}
            cover = self._handoff_cover_path_locked(platform)
            result[platform] = {
                'platform': platform,
                'fields': copy.deepcopy(fields),
                'video': str(handoff.get('video') or ''),
                'cover': str(cover or ''),
                'baseCover': str(handoff.get('cover') or ''),
                'ready': True,
            }
        for platform, message in self._state.platform_errors.items():
            if platform not in result and platform in self._state.selected_platforms:
                result[platform] = {'platform': platform, 'ready': False, 'error': message}
        return result

    def _review_locked(self):
        state = self._state
        draft = state.draft()
        video_ready = bool(state.video_ref or (state.bundle_only and state.video and not state.bundle_stale))
        video_problem = ('' if video_ready and state.source_ready else
                         state.source_problem if video_ready else '请选择已核验的成品视频。')
        cover_problem = '' if state.bundle_only and draft.cover_preview else self._cover_problem_locked()
        platform_checks = {}
        for platform in state.selected_platforms:
            item = state.drafts[platform]
            copy_problem = ''
            try:
                self._content_for_draft_locked(platform, item, require_declarations=False)
            except ValueError as error:
                copy_problem = str(error)
            platform_checks[platform] = {'copyProblem': copy_problem,
                                         'declarationProblem': self._declaration_problem(platform, item)}
        return {'videoReady': video_ready, 'videoProblem': video_problem,
                'coverState': 'previewed' if not cover_problem and draft.cover_preview else
                              'configured' if not cover_problem else 'missing',
                'coverProblem': cover_problem,
                'platforms': platform_checks,
                'packageState': 'stale' if state.bundle_stale else 'verified' if state.verified else
                                'unchecked' if state.bundle else 'missing'}

    def _recompute_view_locked(self):
        draft = self._state.draft()
        view = {
            'step': int(self._state.step),
            'job': self._state.job,
            'video': self._state.video,
            'platform': self._state.platform,
            'title': draft.title,
            'description': draft.description,
            'tags': draft.tags,
            'ai': draft.ai,
            'rights': draft.rights,
            'coverMode': draft.cover_mode,
            'coverSeconds': float(draft.cover_seconds),
            'coverFile': draft.cover_file,
            'coverPreview': draft.cover_preview,
            'busy': bool(self._state.busy),
            'message': self._state.message,
            'state': self._state.state,
            'bundle': self._state.bundle,
            'bundleStale': bool(self._state.bundle_stale),
            'canPrepare': bool(self._state.can_prepare),
            'canCheck': bool(self._state.can_check),
            'verified': bool(self._state.verified),
            'actions': self._actions_locked(),
            'taskChoices': copy.deepcopy(self._state.task_choices),
            'taskMessage': self._state.task_message,
            'taskScanBusy': self._active_kind == 'publication-task-scan',
            'sourceReady': bool(self._state.source_ready),
            'sourceProblem': self._state.source_problem,
            'bundleOnly': bool(self._state.bundle_only),
            'review': self._review_locked(),
        }
        if (self._state.job or self._state.bundle or self._state.browser.state != 'idle'
                or self._state.selected_platforms != (BILIBILI,)):
            selected = list(self._state.selected_platforms)
            view.update({
                'selectedPlatforms': selected,
                'canFetchOriginalCover': self._state.source_mode == 'bilibili',
                'platformLabels': {platform: PLATFORM_LABELS[platform] for platform in selected},
                'coverRatio': self._state.cover_ratio,
                'coverVariants': copy.deepcopy(self._state.cover_variants.get(BILIBILI, [])),
                'handoffs': self._platform_handoffs_view_locked(),
                'browserSession': self._browser_view_locked(),
            })
        return view

    def _actions_locked(self):
        if not self._state.bundle or not self._state.verified or self._state.bundle_stale:
            return {}
        return {
            'video': tr('打开成品视频'),
            'cover': tr('打开封面'),
            'bundle': tr('打开发布包'),
            'checklist': tr('打开核对清单'),
        }


    def _source_stamp(self, job: Path, video: Path, cover: Path | None) -> tuple:
        job = Path(job).resolve()
        paths = [job / name for name in ('request.json', 'source-info.json', 'upstream-info.json',
                                         '发布简介草稿.txt', 'original-cover.json')]
        paths.append(Path(video).resolve())
        if cover is not None:
            paths.append(Path(cover).resolve())
        return tuple(_stat_stamp(path) for path in paths)

    def _bundle_stamp(self, bundle: Path, data: Mapping[str, Any]) -> tuple:
        bundle = Path(bundle).resolve()
        paths = [bundle / 'manifest.json', bundle / 'cover.jpg']
        video = data.get('video') if isinstance(data, Mapping) else None
        if isinstance(video, Mapping) and isinstance(video.get('path'), str):
            try:
                paths.append(Path(publication.clean_path(video['path'])).resolve())
            except (OSError, ValueError):
                pass
        return tuple(_stat_stamp(path) for path in paths)

    def _bundle_platforms_locked(self) -> set[str]:
        try:
            data = _read_json(Path(self._state.bundle) / 'manifest.json')
            content = data.get('content') if isinstance(data, dict) else None
            return set(content) & set(PLATFORMS) if isinstance(content, dict) else set()
        except (OSError, ValueError, TypeError):
            return set()

    def _poll_drift(self):
        with self._lock:
            if self._closed or (not self._state.video_ref and not self._state.bundle) or self._state.busy:
                return
            changed = self._maybe_mark_drift_locked()
        if changed:
            self._emit()


    def _maybe_mark_drift_locked(self) -> bool:
        if self._state.video_ref is None and not self._state.bundle:
            return False
        drift = False
        if self._state.video_ref is not None and self._state.job and self._state.video:
            cover = Path(self._state.original_cover) if self._state.original_cover else None
            current_source = self._source_stamp(Path(self._state.job), Path(self._state.video), cover)
            if (not self._state.source_drift and self._state.source_stamp
                    and current_source != self._state.source_stamp):
                self._state.source_drift = True
                self._state.source_ready = False if self._state.source_mode != 'local' else self._state.source_ready
                self._mark_stale_locked('来源资料或成品已发生变化；请重新载入任务或重新生成发布资料。')
                self._state.state = 'outdated' if self._state.bundle else 'editing'
                drift = True
        if self._state.bundle and not self._state.bundle_stale:
            try:
                data = _read_json(Path(self._state.bundle) / 'manifest.json')
                current_bundle = self._bundle_stamp(Path(self._state.bundle), data)
            except (OSError, ValueError, TypeError):
                current_bundle = ()
            if self._state.bundle_stamp and current_bundle != self._state.bundle_stamp:
                self._state.bundle_stamp = current_bundle
                self._mark_stale_locked('发布资料或本地素材已发生变化，请重新生成。')
                self._state.state = 'outdated'
                drift = True
            else:
                for platform, handoff in tuple(self._state.handoffs.items()):
                    stamps = handoff['stamps']
                    try:
                        unchanged = all(publication.publication_file_stamp(path) == stamp
                                        for path, stamp in stamps.items())
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        self._state.handoffs.pop(platform, None)
                        self._state.platform_errors[platform] = PLATFORM_LABELS[platform] + '资料已变化，请重新核对。'
                        self._state.cover_variants.pop(platform, None)
                        drift = True
                if drift and not self._state.bundle_stale:
                    self._state.verified = bool(self._state.handoffs)
                    self._state.message = '；'.join(self._state.platform_errors.values())
        if drift:
            self._recompute_locked()
        return drift

    def _cached_original_cover(self, job: Path) -> Path | None:
        try:
            info = publication.read_source_info(job)
            if not _text(info.get('bvid')):
                return None
            from cover_art import cached_cover
            found = cached_cover(info, Path(job).resolve())
            return found[0].resolve() if found is not None else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _probe_image(self, path: Path, stop) -> None:
        _check_stop(stop)
        executable = Path(str(load_config().get('ffmpeg', ''))).resolve()
        if not executable.is_file():
            return
        result = subprocess.run(
            [str(executable), '-hide_banner', '-loglevel', 'error', '-i', str(path),
             '-frames:v', '1', '-f', 'null', '-'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        _check_stop(stop)
        if result.returncode:
            raise ValueError('封面图片无法读取：' + (result.stderr[-400:] or '媒体解码失败。'))

    def _draft_path(self) -> Path:
        override = getattr(self._owner, 'quick_publication_draft_path', None)
        return Path(override or (Path(ROOT) / 'quick-publication-draft.json')).resolve()

    def _load_saved_draft(self, job: Path, video: _ArtifactRef, source_stamp: tuple):
        try:
            path = self._draft_path()
            if path.stat().st_size > _MAX_DRAFT_BYTES:
                return None
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('version') != 1:
                return None
            if (Path(_text(value.get('job'))).resolve() != Path(job).resolve()
                    or Path(_text(value.get('video'))).resolve() != Path(video.path).resolve()
                    or value.get('videoSha256') != video.sha256):
                return None
            saved_stamp = value.get('sourceStamp')
            if isinstance(saved_stamp, list):
                saved_stamp = tuple(tuple(item) if isinstance(item, list) else item for item in saved_stamp)
            if saved_stamp != source_stamp:
                return None
            return value
        except (OSError, ValueError, TypeError):
            return None

    def _draft_from_saved(self, value: Mapping[str, Any], fallback: _PlatformDraft) -> _PlatformDraft:
        def optional_bool(item):
            return item if type(item) is bool else None
        try:
            seconds = float(value.get('coverSeconds', fallback.cover_seconds))
        except (TypeError, ValueError):
            seconds = fallback.cover_seconds
        if not math.isfinite(seconds) or not 0 <= seconds < 86400:
            seconds = fallback.cover_seconds
        mode = _text(value.get('coverMode'))
        rights = _text(value.get('rights')) or None
        cover_file = _text(value.get('coverFile'))
        result_mode = mode if mode in COVER_MODES else fallback.cover_mode
        preview = fallback.cover_preview if result_mode == 'original' else (
            _local_url(cover_file) if result_mode == 'custom' and cover_file else '')
        return _PlatformDraft(
            title=_text(value.get('title')), description=_text(value.get('description')),
            tags=_text(value.get('tags')), ai=optional_bool(value.get('ai')),
            rights=rights,
            cover_mode=result_mode,
            cover_seconds=seconds, cover_file=cover_file,
            cover_preview=preview,
        )


    def _save_draft_safe_locked(self):
        if not self._state.bundle and (not self._state.job or self._state.video_ref is None):
            return
        payload = {
            'version': 1,
            'job': self._state.job,
            'video': self._state.video,
            'videoSha256': self._state.video_ref.sha256 if self._state.video_ref is not None else '',
            'sourceStamp': [list(item) if item is not None else None for item in self._state.source_stamp],
            'bundle': self._state.bundle,
            'bundleOnly': bool(self._state.bundle_only),
            'platform': self._state.platform,
            'selectedPlatforms': list(self._state.selected_platforms),
            'coverRatio': self._state.cover_ratio,
            'step': self._state.step,
            'drafts': {
                platform: {
                    'title': draft.title, 'description': draft.description, 'tags': draft.tags,
                    'ai': draft.ai, 'rights': draft.rights, 'coverMode': draft.cover_mode,
                    'coverSeconds': draft.cover_seconds, 'coverFile': draft.cover_file,
                }
                for platform, draft in self._state.drafts.items()
            },
        }
        try:
            atomic_json(self._draft_path(), payload)
        except OSError:
            # Draft persistence is a convenience; the immutable bundle remains authoritative.
            pass

    def _discard_preview_dir(self, directory: Path | None, keep: Path | None):
        if directory is None:
            return
        try:
            directory = Path(directory).resolve()
            keep_path = Path(keep).resolve() if keep is not None else None
            temp_root = Path(tempfile.gettempdir()).resolve()
            if keep_path is not None and directory == keep_path:
                return
            if directory.parent == temp_root and directory.name.startswith('vwa-quick-publication-'):
                shutil.rmtree(directory, ignore_errors=True)
        except (OSError, ValueError):
            pass

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
