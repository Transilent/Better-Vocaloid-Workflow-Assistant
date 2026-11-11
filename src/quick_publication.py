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
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Mapping

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from common import ROOT, atomic_json, load_config
from i18n import tr
import publication


BILIBILI = 'bilibili'
XIAOHONGSHU = 'xiaohongshu'
PLATFORMS = (BILIBILI, XIAOHONGSHU)
PLATFORM_LABELS = {BILIBILI: 'B站', XIAOHONGSHU: '小红书'}
COVER_MODES = ('original', 'custom', 'frame')
COVER_LABELS = {'original': '本家原封面', 'custom': '本地图片', 'frame': '视频画面'}
TAG_LIMITS = {BILIBILI: (10, 20), XIAOHONGSHU: (10, 20)}
RIGHTS_MAX_CHARS = 500
IMAGE_SUFFIXES = frozenset(('.jpg', '.jpeg', '.png', '.webp'))
BILIBILI_UPLOAD_URL = 'https://member.bilibili.com/platform/upload/video/frame'
XIAOHONGSHU_CREATOR_URL = publication.XIAOHONGSHU_CREATOR_URL
_HEX_ID = re.compile(r'^[a-f0-9]{32}$')
_HEX_SHA = re.compile(r'^[a-f0-9]{64}$')
_MAX_DRAFT_BYTES = 256 * 1024
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
class _PublicationState:
    step: int = 0
    job: str = ''
    video: str = ''
    platform: str = BILIBILI
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
    video_ref: _ArtifactRef | None = None
    bundle_ref: _ArtifactRef | None = None
    original_cover: str = ''
    source_ready: bool = False
    source_problem: str = '尚未载入来源任务。'
    source_mode: str = 'unknown'
    source_stamp: tuple = ()
    bundle_stamp: tuple = ()
    handoff: dict[str, Any] | None = None
    source_drift: bool = False

    def draft(self) -> _PlatformDraft:
        if self.platform not in self.drafts:
            self.drafts[self.platform] = _PlatformDraft()
        return self.drafts[self.platform]


@dataclass(frozen=True)
class _Snapshot:
    job: str
    video: _ArtifactRef
    bundle_ref: _ArtifactRef | None
    bundle: str
    platform: str
    title: str
    description: str
    tags: tuple[str, ...]
    ai: bool
    rights: str
    cover_mode: str
    cover_seconds: float
    cover_file: str
    content: Mapping[str, Any]
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
        self._preview_dir: Path | None = None
        self._drift_timer = QTimer(self)
        self._drift_timer.setInterval(1500)
        self._drift_timer.timeout.connect(self._poll_drift)
        self._view = self._recompute_view_locked()
        self._worker.changed.connect(self._worker_changed)

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
                if self._active_kind:
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
                is_local = source_kind == 'local' or not bv
                draft_text, draft_error = '', ''
                try:
                    draft_text = publication.source_draft(job_path)
                except (OSError, ValueError) as exc:
                    draft_error = str(exc)

                source_ready = bool(request) and (is_local or bool(draft_text.strip()))
                source_problem = ''
                if not request:
                    source_problem = '来源任务缺少 request.json，请重新选择任务。'
                elif not is_local and not draft_text.strip():
                    source_problem = draft_error or 'B站来源任务缺少可核验的简介草稿。'
                original = self._cached_original_cover(job_path)
                source_stamp = self._source_stamp(job_path, video.path, original)

                next_state = _PublicationState()
                next_state.job = str(job_path)
                next_state.video = str(video.path)
                next_state.video_ref = video
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
                    next_state.platform = (self._state.platform if self._state.platform in content
                                           else BILIBILI if BILIBILI in content else XIAOHONGSHU)
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
                        draft.cover_mode = _text(cover.get('kind')) or 'frame'
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
                    next_state.step = 2
                    next_state.state = 'restored'
                    next_state.message = '已从只读发布资料恢复文稿；核对前不会改写任务登记或发布包。'
                else:
                    saved = self._load_saved_draft(job_path, video, source_stamp)
                    if saved is not None:
                        platform = _text(saved.get('platform'))
                        if platform in PLATFORMS:
                            next_state.platform = platform
                        for platform in PLATFORMS:
                            value = saved.get('drafts', {}).get(platform) if isinstance(saved.get('drafts'), dict) else None
                            if isinstance(value, dict):
                                next_state.drafts[platform] = self._draft_from_saved(value, next_state.drafts[platform])
                        step = saved.get('step')
                        next_state.step = step if type(step) is int and 0 <= step <= 2 else 0
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
                self._recompute_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice('载入发布任务失败：' + str(exc), state='error')
            return False

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
                    if platform != self._state.platform:
                        self._state.platform = platform
                        if self._state.bundle and platform not in self._bundle_platforms_locked():
                            self._mark_stale_locked('已切换到发布包未包含的平台，请重新生成资料。')
                        else:
                            self._state.message = f'已切换到{PLATFORM_LABELS[platform]}。'
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
                    if mode != draft.cover_mode:
                        draft.cover_mode = mode
                        draft.cover_preview = ''
                        self._mark_stale_locked('封面来源已修改；原发布资料已过期。')
                        self._state.message = '封面来源已更新，请重新预览。'
                elif name == 'coverSeconds':
                    try:
                        seconds = float(value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError('截图时间必须是数字。') from exc
                    if not math.isfinite(seconds) or not 0 <= seconds < 86400:
                        raise ValueError('截图时间须在 0 到 86400 秒之间。')
                    if seconds != draft.cover_seconds:
                        draft.cover_seconds = seconds
                        draft.cover_preview = ''
                        self._mark_stale_locked('截图时间已修改；原发布资料已过期。')
                        self._state.message = '截图时间已更新，请重新预览。'
                elif name == 'coverFile':
                    path = _local_path(value) if value else None
                    text = str(path) if path else ''
                    if path is not None:
                        _sniff_image(path)
                    if text != draft.cover_file:
                        draft.cover_file = text
                        draft.cover_preview = _local_url(path) if path else ''
                        self._mark_stale_locked('封面图片已修改；原发布资料已过期。')
                        self._state.message = '本地封面已选择。'
                else:
                    raise ValueError('该字段不可编辑。')
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

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
                draft = self._state.draft()
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
            if request.mode != 'frame':
                raise ValueError('未知封面来源。')
            _check_stop(stop)
            directory = Path(tempfile.mkdtemp(prefix='vwa-quick-publication-'))
            destination = directory / 'cover-preview.jpg'
            try:
                publication.extract_cover(request.video.path, destination, request.seconds)
                _check_stop(stop)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            return {'path': str(destination), 'temporary': True,
                    'directory': str(directory), 'message': '已从已核验成品截取封面预览。'}

        return self._start_work('publication-preview-cover', work, self._finish_preview)

    @Slot()
    def nextStep(self):
        try:
            with self._lock:
                if self._closed or self._state.busy:
                    return False
                self._maybe_mark_drift_locked()
                if self._state.step >= 2:
                    return True
                if not self._validate_current_page_locked(self._state.step, raise_error=True):
                    return False
                self._state.step += 1
                self._state.message = ('请检查并预览封面，然后核对发布资料。' if self._state.step == 1
                                       else '请核对文稿、封面和本地声明，再生成发布资料。')
                self._recompute_locked()
                self._save_draft_safe_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot()
    def previousStep(self):
        with self._lock:
            if self._closed or self._state.busy:
                return False
            if self._state.step > 0:
                self._state.step -= 1
                self._state.message = '可返回修改当前步骤；已生成资料会在修改后标记为过期。'
                self._recompute_locked()
                self._save_draft_safe_locked()
        self._emit()
        return True

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
            send({'message': '正在核验已登记的成品视频…'})
            verified_video = self._verify_video(snapshot.job, snapshot.video, stop)
            _check_stop(stop)
            send({'message': '正在生成文稿、封面并登记来源依赖…'})
            bundle = publication.prepare(
                snapshot.job,
                str(verified_video.path),
                {snapshot.platform: copy.deepcopy(dict(snapshot.content))},
                snapshot.cover_seconds,
                snapshot.cover_file or None,
                cover_mode=snapshot.cover_mode,
                expected_video=verified_video.as_row(),
                check=stop.is_set,
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
            send({'message': '正在完整校验新发布资料…'})
            self._assert_snapshot_content(snapshot, data)
            handoff = (publication.xiaohongshu_handoff(bundle) if snapshot.platform == XIAOHONGSHU
                       else self._handoff_for_bilibili(bundle, data, stop))
            _check_stop(stop)
            verifier = getattr(self._owner, 'verify_publication_bundle', None)
            bundle_ref = verifier(snapshot.job, str(bundle), check=stop.is_set) if callable(verifier) else None
            return {'bundle': str(bundle), 'data': data, 'handoff': handoff,
                    'bundle_ref': bundle_ref, 'source_stamp': snapshot.source_stamp}

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
        try:
            with self._lock:
                if self._closed:
                    return False
                self._maybe_mark_drift_locked()
                if not self._state.verified or self._state.bundle_stale or not self._state.handoff:
                    raise ValueError('请先核对当前发布资料，再复制交接内容。')
                handoff = self._state.handoff
                fields = handoff.get('fields') if isinstance(handoff.get('fields'), dict) else {}
                name = _text(field).strip().lower()
                values = {
                    'title': _text(fields.get('title')),
                    'description': _text(fields.get('description')),
                    'tags': _tags_text(fields.get('tags')),
                    'video': str(handoff.get('video') or ''),
                    'cover': str(handoff.get('cover') or ''),
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
                self._state.message = '已复制到剪贴板。请在平台页面再次核对。'
                self._recompute_locked()
            self._emit()
            return True
        except Exception as exc:
            self._notice(str(exc))
            return False

    @Slot(str)
    def openMaterial(self, name):
        name = _text(name).strip().lower()
        if name not in ('video', 'cover', 'bundle', 'folder', 'checklist'):
            self._notice('未知的本地发布资料。')
            return False
        with self._lock:
            if self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if not self._state.verified or self._state.bundle_stale or not self._state.handoff:
                self._notice('请先核对当前发布资料，再打开本地素材。')
                return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            self._state.message = f'正在重新核验要打开的{name}…'
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            verified = self._verify_current_bundle(snapshot, bundle_ref, stop)
            bundle = Path(snapshot.bundle).resolve()
            handoff = verified['handoff']
            if name == 'video':
                target = Path(handoff['video']).resolve()
            elif name == 'cover':
                target = Path(handoff['cover']).resolve()
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
            return {'path': str(target), 'kind': name}

        return self._start_work('publication-open-material', work, self._finish_open_material)

    @Slot()
    def openCreator(self):
        with self._lock:
            if self._state.busy or self._active_kind:
                return False
            self._maybe_mark_drift_locked()
            if not self._state.verified or self._state.bundle_stale or not self._state.handoff:
                self._notice('请先核对当前发布资料，再打开平台。')
                return False
            snapshot = self._snapshot_locked(check_only=True)
            bundle_ref = self._state.bundle_ref
            platform = self._state.platform
            self._state.message = '正在复核发布资料，准备打开平台填写…'
            self._recompute_locked()
        self._emit()

        def work(stop, _send):
            verified = self._verify_current_bundle(snapshot, bundle_ref, stop)
            return {'platform': platform, 'bundle': snapshot.bundle,
                    'handoff': verified['handoff']}

        return self._start_work('publication-open-creator', work, self._finish_open_creator)

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
            if self._closed:
                return
            self._closed = True
            if self._active_kind:
                self._worker.cancel()
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
            draft = self._state.draft()
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
                    self._state.message = '已取消生成发布资料；已完成磁盘写入的旧包不会修改。'
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
            self._state.bundle = str(bundle)
            self._state.bundle_stamp = self._bundle_stamp(bundle, data)
            self._state.source_stamp = self._source_stamp(Path(self._state.job), Path(self._state.video), Path(self._state.original_cover) if self._state.original_cover else None)
            self._state.source_drift = False
            self._state.handoff = value.get('handoff') if isinstance(value.get('handoff'), dict) else None
            ref = value.get('bundle_ref')
            try:
                self._state.bundle_ref = _artifact_ref(ref, 'publication') if ref is not None else None
            except Exception:
                self._state.bundle_ref = None
            self._state.bundle_stale = False
            self._state.verified = True
            self._state.state = 'prepared'
            self._state.step = 2
            draft = self._state.draft()
            cover = bundle / 'cover.jpg'
            if cover.is_file():
                draft.cover_preview = _local_url(cover)
            if self._state.platform == XIAOHONGSHU:
                self._state.message = '发布资料已生成并核对。可复制文稿与本地素材，或打开小红书创作中心手动投稿。'
            else:
                self._state.message = '发布资料已生成并核对。可打开 B站填写页；最终发布仍由你在平台确认。'
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
            handoff = value.get('handoff') if isinstance(value, dict) else None
            data = value.get('data') if isinstance(value, dict) else None
            if not isinstance(handoff, dict) or not isinstance(data, dict):
                self._state.verified = False
                self._state.bundle_stale = True
                self._state.state = 'outdated'
                self._state.message = '核对结果不完整，请重新生成发布资料。'
                self._recompute_locked()
                return
            self._state.handoff = handoff
            self._state.bundle_stamp = self._bundle_stamp(Path(self._state.bundle), data)
            self._state.source_stamp = self._source_stamp(Path(self._state.job), Path(self._state.video), Path(self._state.original_cover) if self._state.original_cover else None)
            self._state.source_drift = False
            self._state.bundle_stale = False
            self._state.verified = True
            self._state.state = 'ready'
            self._state.message = ('小红书资料已核验。请使用复制字段、本地素材与官方创作中心手动投稿。'
                                   if self._state.platform == XIAOHONGSHU else
                                   'B站资料已核验。可打开安全填写助手；最终投稿必须由你确认。')
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
                    self._state.message = '已打开小红书创作中心。请手动上传视频与封面，并核对 AI 与版权声明。'
                    self._recompute_locked()
                self._emit()
            return
        if platform != BILIBILI:
            self._notice('未知发布平台。')
            return
        hook = getattr(self._owner, 'open_publication_browser', None)
        if callable(hook):
            try:
                if hook(value.get('bundle')) is not False:
                    with self._lock:
                        self._state.message = '已交给 B站安全填写助手；最终发布仍由你在平台确认。'
                        self._recompute_locked()
                    self._emit()
                    return
            except Exception as exc:
                self._notice('B站填写助手不可用：' + str(exc))
                return
        script = Path(__file__).with_name('publish_browser.py').resolve()
        if not script.is_file():
            self._manual_bilibili_fallback()
            return
        try:
            config = load_config()
            python = Path(str(config.get('python') or sys.executable)).resolve()
        except (OSError, ValueError, TypeError):
            self._manual_bilibili_fallback()
            return
        if not python.is_file():
            self._manual_bilibili_fallback()
            return
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            subprocess.Popen(
                [str(python), '-B', '-u', str(script), '--platform', BILIBILI,
                 '--bundle', str(Path(value.get('bundle')))],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
            )
        except OSError:
            self._manual_bilibili_fallback()
            return
        with self._lock:
            self._state.message = '已打开 B站安全填写助手；它只填写已核验资料，不会提交最终投稿。'
            self._recompute_locked()
        self._emit()

    def _manual_bilibili_fallback(self):
        opened = self._open_url(BILIBILI_UPLOAD_URL)
        message = ('浏览器填写助手不可用，请改用官方上传页手动填写：' + BILIBILI_UPLOAD_URL +
                   '。助手不会代替你提交最终投稿。')
        with self._lock:
            self._state.message = message if opened else ('浏览器填写助手不可用。请手动访问：' + BILIBILI_UPLOAD_URL)
            self._recompute_locked()
        self._emit()

    def _verify_video(self, job: str, expected: _ArtifactRef, stop) -> _ArtifactRef:
        verifier = getattr(self._owner, 'verify_task_artifact', None)
        if not callable(verifier):
            raise RuntimeError('主控制器未提供任务产物核验入口。')
        _check_stop(stop)
        row = verifier(job, expected.as_row(), 'video', check=stop.is_set)
        _check_stop(stop)
        verified = _artifact_ref(row, 'video')
        if not expected.same_identity(verified):
            raise ValueError('成品视频在核验期间发生变化。')
        return verified

    def _verify_bundle_row(self, job: str, expected: _ArtifactRef, stop) -> _ArtifactRef:
        verifier = getattr(self._owner, 'verify_task_artifact', None)
        if not callable(verifier):
            raise RuntimeError('主控制器未提供任务产物核验入口。')
        _check_stop(stop)
        row = verifier(job, expected.as_row(), 'publication', check=stop.is_set)
        _check_stop(stop)
        verified = _artifact_ref(row, 'publication')
        if not expected.same_identity(verified):
            raise ValueError('发布资料 manifest 在核验期间发生变化。')
        return verified

    def _verify_current_bundle(self, snapshot: _Snapshot, bundle_ref: _ArtifactRef | None, stop):
        _check_stop(stop)
        verified_video = self._verify_video(snapshot.job, snapshot.video, stop)
        if bundle_ref is not None:
            self._verify_bundle_row(snapshot.job, bundle_ref, stop)
        bundle = Path(snapshot.bundle).resolve() if snapshot.bundle else None
        if bundle is None or not bundle.is_dir():
            raise ValueError('当前没有可核对的发布资料。')
        manifest_path = bundle / 'manifest.json'
        data = _read_json(manifest_path)
        if not isinstance(data, dict):
            raise ValueError('发布资料 manifest 格式无效。')
        if data.get('state') != 'prepared' or data.get('final_publish') != 'human_only':
            raise ValueError('发布资料未处于只读准备状态。')
        if Path(publication.clean_path(data.get('source_job', ''))).resolve() != Path(snapshot.job).resolve():
            raise ValueError('发布资料来源任务不一致。')
        video_data = data.get('video') if isinstance(data.get('video'), dict) else {}
        if (Path(publication.clean_path(video_data.get('path', ''))).resolve() != verified_video.path
                or video_data.get('sha256') != verified_video.sha256):
            raise ValueError('发布资料绑定的成品与当前已核验视频不一致。')
        if bundle_ref is not None:
            expected_manifest = bundle / 'manifest.json'
            if bundle_ref.path.resolve() != expected_manifest.resolve():
                raise ValueError('登记的发布资料入口与当前目录不一致。')
        if snapshot.platform not in (data.get('content') or {}):
            raise ValueError('发布资料不包含当前平台文稿。')
        handoff = (publication.xiaohongshu_handoff(bundle) if snapshot.platform == XIAOHONGSHU
                   else self._handoff_for_bilibili(bundle, data, stop))
        fields = handoff.get('fields') if isinstance(handoff, dict) else None
        if not isinstance(fields, dict):
            raise ValueError('发布资料平台文稿无效。')
        self._assert_snapshot_content(snapshot, data)


        return {'data': data, 'handoff': handoff}

    def _assert_snapshot_content(self, snapshot: _Snapshot, data: Mapping[str, Any]):
        content = data.get('content') if isinstance(data, Mapping) else None
        fields = content.get(snapshot.platform) if isinstance(content, Mapping) else None
        if not isinstance(fields, dict):
            raise ValueError('发布资料平台文稿无效。')
        if (_text(fields.get('title')) != snapshot.title
                or _text(fields.get('description')) != snapshot.description
                or _tags(fields.get('tags')) != list(snapshot.tags)
                or fields.get('ai_generated') is not snapshot.ai
                or _text(fields.get('rights')) != snapshot.rights):
            raise ValueError('发布资料文稿与当前编辑内容不一致，请重新生成。')


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

    def _snapshot_locked(self, *, check_only: bool = False) -> _Snapshot:
        draft = self._state.draft()
        content = self._content_locked()
        problem = (self._bundle_declaration_problem_locked() if check_only else self._prepare_problem_locked())
        if problem:
            raise ValueError(problem)
        return _Snapshot(
            self._state.job, self._state.video_ref, self._state.bundle_ref, self._state.bundle,
            self._state.platform, draft.title.strip(), draft.description,
            tuple(_tags(draft.tags)), bool(draft.ai), _text(draft.rights),
            draft.cover_mode, float(draft.cover_seconds), draft.cover_file,
            content, self._state.source_stamp, self._state.bundle_stamp,
        )

    def _content_locked(self) -> dict[str, Any]:
        draft = self._state.draft()
        content = {
            'title': draft.title.strip(),
            'description': draft.description,
            'tags': _tags(draft.tags),
            'rights': draft.rights,
        }
        if type(draft.ai) is bool:
            content['ai_generated'] = draft.ai
        publication.validate_content(content, self._state.platform, require_ai=True)
        return content

    def _bundle_declaration_problem_locked(self) -> str:
        if not self._state.bundle:
            return ''
        draft = self._state.draft()
        if not draft.title.strip():
            return '请填写发布标题。'
        if not draft.description.strip():
            return '请填写发布正文并保留必要的本家与 STAFF 信息。'
        try:
            content = {
                'title': draft.title.strip(), 'description': draft.description,
                'tags': _tags(draft.tags), 'rights': draft.rights,
            }
            if type(draft.ai) is bool:
                content['ai_generated'] = draft.ai
            publication.validate_content(content, self._state.platform, require_ai=True)
        except Exception as exc:
            return str(exc)
        tags = _tags(draft.tags)
        maximum, each = TAG_LIMITS[self._state.platform]
        if len(tags) > maximum:
            return f'{PLATFORM_LABELS[self._state.platform]}最多填写 {maximum} 个标签。'
        if any(len(tag) > each for tag in tags):
            return f'每个标签最多 {each} 字，请删减或拆分。'
        if type(draft.ai) is not bool:
            return '请明确声明素材是否包含 AI 生成或合成内容。'
        if not _text(draft.rights).strip():
            return '请填写原创、转载、署名与授权声明。'
        return ''


    def _prepare_problem_locked(self) -> str:
        if self._state.video_ref is None or not self._state.job:
            return '请选择一个已核验的成品视频和来源任务。'
        if not self._state.source_ready:
            return self._state.source_problem or '来源任务资料尚未准备完整。'
        draft = self._state.draft()
        if not draft.title.strip():
            return '请填写发布标题。'
        if not draft.description.strip():
            return '请填写发布正文并保留必要的本家与 STAFF 信息。'
        try:
            content = {
                'title': draft.title.strip(), 'description': draft.description,
                'tags': _tags(draft.tags), 'rights': draft.rights,
            }
            if type(draft.ai) is bool:
                content['ai_generated'] = draft.ai
            publication.validate_content(content, self._state.platform, require_ai=True)
        except Exception as exc:
            return str(exc)
        tags = _tags(draft.tags)
        maximum, each = TAG_LIMITS[self._state.platform]
        if len(tags) > maximum:
            return f'{PLATFORM_LABELS[self._state.platform]}最多填写 {maximum} 个标签。'
        if any(len(tag) > each for tag in tags):
            return f'每个标签最多 {each} 字，请删减或拆分。'
        if type(draft.ai) is not bool:
            return '请明确声明素材是否包含 AI 生成或合成内容。'
        if not _text(draft.rights).strip():
            return '请填写原创、转载、署名与授权声明。'
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
                draft = self._state.draft()
                try:
                    self._content_locked()
                except Exception as exc:
                    problem = str(exc)
                if not problem and type(draft.ai) is not bool:
                    problem = '请明确声明素材是否包含 AI 生成或合成内容。'
                if not problem and not _text(draft.rights).strip():
                    problem = '请填写原创、转载、署名与授权声明。'
                if not problem:
                    tags = _tags(draft.tags)
                    maximum, each = TAG_LIMITS[self._state.platform]
                    if len(tags) > maximum:
                        problem = f'{PLATFORM_LABELS[self._state.platform]}最多填写 {maximum} 个标签。'
                    elif any(len(tag) > each for tag in tags):
                        problem = f'每个标签最多 {each} 字。'
        elif step == 1:
            draft = self._state.draft()
            if draft.cover_mode == 'original' and not Path(self._state.original_cover).is_file():
                problem = '没有可核验的本家原封面。'
            elif draft.cover_mode == 'custom':
                try:
                    _sniff_image(Path(draft.cover_file))
                except (OSError, ValueError) as exc:
                    problem = str(exc)
            elif draft.cover_mode == 'frame' and not Path(self._state.video).is_file():
                problem = '没有可截取封面的已核验成品。'
            elif draft.cover_mode not in COVER_MODES:
                problem = '请选择封面来源。'
        if problem and raise_error:
            self._notice(problem)
        return not problem


    def _mark_stale_locked(self, reason: str):
        self._state.verified = False
        self._state.handoff = None
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
            self._state.handoff = None
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


    def _recompute_view_locked(self):
        draft = self._state.draft()
        return {
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
        }

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
        variants = data.get('platform_covers') if isinstance(data, Mapping) else None
        if isinstance(variants, Mapping):
            for values in variants.values():
                if not isinstance(values, Mapping):
                    continue
                for asset in values.values():
                    if isinstance(asset, Mapping) and isinstance(asset.get('file'), str):
                        paths.append(bundle / asset['file'])
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
            if self._closed or not self._state.video_ref or self._state.busy:
                return
            changed = self._maybe_mark_drift_locked()
        if changed:
            self._emit()


    def _maybe_mark_drift_locked(self) -> bool:
        if self._state.video_ref is None:
            return False
        cover = Path(self._state.original_cover) if self._state.original_cover else None
        current_source = self._source_stamp(Path(self._state.job), Path(self._state.video), cover)
        drift = False
        if not self._state.source_drift and self._state.source_stamp and current_source != self._state.source_stamp:
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
                    or Path(_text(value.get('video'))).resolve() != video.path
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
        if not self._state.job or self._state.video_ref is None:
            return
        payload = {
            'version': 1,
            'job': self._state.job,
            'video': self._state.video,
            'videoSha256': self._state.video_ref.sha256,
            'sourceStamp': [list(item) if item is not None else None for item in self._state.source_stamp],
            'platform': self._state.platform,
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
