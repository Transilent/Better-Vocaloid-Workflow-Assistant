"""Pure Python publication draft, validation, verification, drift & view-projection service (Qt-free)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Mapping

from common import ROOT, atomic_json, file_hash, load_config
from i18n import tr
import publication
from publication_browser_service import (
    BILIBILI,
    BROWSER_STATUS_FILENAME,
    _BrowserSession,
    _MAX_BROWSER_STATUS_BYTES,
    _read_json,
    _text,
    browser_view,
)

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
_HEX_ID = re.compile(r'^[a-f0-9]{32}$')
_HEX_SHA = re.compile(r'^[a-f0-9]{64}$')
_MAX_DRAFT_BYTES = 256 * 1024
_TAG_SPLIT = re.compile(r'[\s,，、]+')


def _default_local_url(path: Any) -> str:
    return Path(path).resolve().as_uri() if path else ''


def _same_path(left: Any, right: Any) -> bool:
    try:
        return bool(left and right and Path(left).expanduser().resolve() == Path(right).expanduser().resolve())
    except (OSError, TypeError, ValueError):
        return False


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


def _artifact_ref(row: Any, role: str, *, local_path_fn: Callable[[Any], Path] | None = None) -> _ArtifactRef:
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
    if local_path_fn is not None:
        path = local_path_fn(row.get('path'))
    else:
        raw_path = row.get('path')
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError('文件路径为空。')
        path = Path(raw_path).expanduser().resolve()
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


def verify_video(
    owner: Any,
    job: str,
    expected: _ArtifactRef | _LocalVideoRef,
    stop,
    *,
    local_path_fn: Callable[[Any], Path] | None = None,
):
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
    verifier = getattr(owner, 'verify_task_artifact', None)
    if not callable(verifier):
        raise RuntimeError('主控制器未提供任务产物核验入口。')
    row = verifier(job, expected.as_row(), 'video', check=stop.is_set)
    _check_stop(stop)
    verified = _artifact_ref(row, 'video', local_path_fn=local_path_fn)
    if not expected.same_identity(verified):
        raise ValueError('成品视频在核验期间发生变化。')
    return verified


def verify_bundle_row(
    owner: Any,
    job: str,
    expected: _ArtifactRef,
    stop,
    *,
    platform: str,
    local_path_fn: Callable[[Any], Path] | None = None,
) -> _ArtifactRef:
    verifier = getattr(owner, 'verify_task_artifact', None)
    if not callable(verifier):
        raise RuntimeError('主控制器未提供任务产物核验入口。')
    _check_stop(stop)
    row = verifier(job, expected.as_row(), 'publication', check=stop.is_set, platform=platform)
    _check_stop(stop)
    verified = _artifact_ref(row, 'publication', local_path_fn=local_path_fn)
    if not expected.same_identity(verified):
        raise ValueError('发布资料 manifest 在核验期间发生变化。')
    return verified


def assert_snapshot_content(snapshot: _Snapshot, data: Mapping[str, Any]) -> None:
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


def handoff_for_bilibili(bundle: Path, data: dict, stop) -> dict[str, Any]:
    bundle = Path(bundle).resolve()
    manifest = bundle / 'manifest.json'
    video = Path(publication.clean_path(data['video']['path'])).resolve()
    cover = bundle / 'cover.jpg'
    paths = [manifest, video, cover]
    variants = (data.get('platform_covers', {}).get(BILIBILI, {})
                if isinstance(data.get('platform_covers'), dict) else {})
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


def handoffs_for_snapshot(
    owner: Any,
    snapshot: _Snapshot,
    bundle: Path,
    data: dict,
    stop,
    *,
    bundle_ref=None,
    only=None,
    local_path_fn: Callable[[Any], Path] | None = None,
):
    handoffs, errors = {}, {}
    for platform in ((only,) if only else snapshot.selected_platforms):
        _check_stop(stop)
        try:
            if platform not in snapshot.selected_platforms:
                raise ValueError('该平台未被选中。')
            if bundle_ref is not None:
                verify_bundle_row(owner, snapshot.job, bundle_ref, stop,
                                  platform=platform, local_path_fn=local_path_fn)
            handoff = (publication.xiaohongshu_handoff(bundle)
                       if platform == XIAOHONGSHU else handoff_for_bilibili(bundle, data, stop))
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


def cover_variants(
    bundle: Path,
    data: Mapping[str, Any],
    *,
    local_url_fn: Callable[[Any], str] = _default_local_url,
):
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
            'preview': local_url_fn(path),
            'width': int(width) if type(width) is int else expected_width,
            'height': int(height) if type(height) is int else expected_height,
        })
    if items:
        result[BILIBILI] = items
    return result


def verify_current_bundle(
    owner: Any,
    state: _PublicationState,
    snapshot: _Snapshot,
    bundle_ref: _ArtifactRef | None,
    stop,
    *,
    platform=None,
    handoffs_fn: Callable | None = None,
    local_path_fn: Callable[[Any], Path] | None = None,
    local_url_fn: Callable[[Any], str] = _default_local_url,
):
    _check_stop(stop)
    verified_video = (verify_video(owner, snapshot.job, snapshot.video, stop, local_path_fn=local_path_fn)
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
        if not state.bundle_only:
            raise ValueError('当前发布资料没有可核验的成品视频。')
    if bundle_ref is not None:
        expected_manifest = bundle / 'manifest.json'
        if not snapshot.job or bundle_ref.path.resolve() != expected_manifest.resolve():
            raise ValueError('登记的发布资料入口与当前目录不一致。')
    content = data.get('content') if isinstance(data.get('content'), dict) else {}
    if set(snapshot.selected_platforms) != set(content):
        raise ValueError('发布资料平台集合与当前选择不一致，请重新生成同一发布包。')
    assert_snapshot_content(snapshot, data)
    if handoffs_fn is not None:
        handoffs, errors = handoffs_fn(snapshot, bundle, data, stop, bundle_ref=bundle_ref, only=platform)
    else:
        handoffs, errors = handoffs_for_snapshot(
            owner, snapshot, bundle, data, stop, bundle_ref=bundle_ref, only=platform,
            local_path_fn=local_path_fn)
    if platform and platform not in handoffs:
        raise ValueError(errors.get(platform, '该平台没有可用交接。'))
    return {'data': data, 'handoffs': handoffs, 'platform_errors': errors,
            'cover_variants': cover_variants(bundle, data, local_url_fn=local_url_fn) if BILIBILI in handoffs else {}}


def cover_path_from_verified(state: _PublicationState, platform: str, verified: Mapping[str, Any]) -> Path:
    handoff = verified.get('handoffs', {}).get(platform) if isinstance(verified.get('handoffs'), dict) else {}
    if not isinstance(handoff, Mapping):
        raise ValueError('该平台没有已核验的封面交接。')
    if platform != BILIBILI:
        return Path(handoff.get('cover')).resolve()
    bundle = Path(state.bundle).resolve()
    variants = (verified.get('cover_variants', {}).get(BILIBILI, [])
                if isinstance(verified.get('cover_variants'), dict) else [])
    ratio = state.cover_ratio
    item = next((value for value in variants if value.get('ratio') == ratio), None)
    target = bundle / item['file'] if isinstance(item, Mapping) else bundle / 'cover.jpg'
    if not target.is_file():
        raise ValueError('所选 B站封面比例文件不存在。')
    return target.resolve()


def handoff_cover_path(state: _PublicationState, platform: str) -> Path | None:
    handoff = state.handoffs.get(platform)
    if not isinstance(handoff, dict):
        return None
    if platform != BILIBILI:
        return Path(handoff.get('cover')).resolve() if handoff.get('cover') else None
    bundle = Path(state.bundle).resolve()
    variants = state.cover_variants.get(BILIBILI, [])
    item = next((value for value in variants if value.get('ratio') == state.cover_ratio), None)
    return (bundle / item['file']).resolve() if isinstance(item, Mapping) else Path(handoff.get('cover')).resolve()


def declaration_problem(platform: str, draft: _PlatformDraft) -> str:
    if type(draft.ai) is not bool:
        return (publication.AI_REQUIRED if platform == XIAOHONGSHU else
                f'请明确声明{PLATFORM_LABELS[platform]}素材是否包含 AI 生成或合成内容。')
    if not _text(draft.rights).strip():
        return f'请填写{PLATFORM_LABELS[platform]}的原创、转载、署名与授权声明。'
    return ''


def content_for_draft(platform: str, draft: _PlatformDraft, *, require_declarations: bool = True) -> dict[str, Any]:
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
    if require_declarations and (problem := declaration_problem(platform, draft)):
        raise ValueError(problem)
    return content


def contents_for_state(state: _PublicationState) -> dict[str, dict[str, Any]]:
    if not state.selected_platforms:
        raise ValueError('至少选择一个发布平台。')
    return {
        platform: content_for_draft(platform, state.drafts[platform])
        for platform in state.selected_platforms
    }


def bundle_declaration_problem(state: _PublicationState) -> str:
    if not state.bundle or state.bundle_only:
        return ''
    try:
        contents_for_state(state)
    except Exception as exc:
        return str(exc)
    return ''


def cover_problem(state: _PublicationState) -> str:
    draft = state.draft()
    if draft.cover_mode == 'original':
        if not Path(state.original_cover).is_file():
            return '没有可核验的本家原封面；请选择本地图片或视频画面。'
    elif draft.cover_mode == 'custom':
        try:
            _sniff_image(Path(draft.cover_file))
        except (OSError, ValueError) as exc:
            return str(exc)
    elif draft.cover_mode == 'frame':
        if state.video_ref is None or not Path(state.video).is_file():
            return '没有可截取封面的已核验成品。'
    elif draft.cover_mode == 'original-frame':
        if state.source_mode != 'bilibili':
            return '只有 B站来源任务可以截取本家原视频。'
    else:
        return '请选择封面来源。'
    if not math.isfinite(float(draft.cover_seconds)) or not 0 <= float(draft.cover_seconds) < 86400:
        return '截图时间必须在 0 到 86400 秒之间。'
    return ''


def prepare_problem(state: _PublicationState) -> str:
    if state.video_ref is None or not state.job:
        return '请选择一个已核验的成品视频和来源任务。'
    if not state.source_ready:
        return state.source_problem or '来源任务资料尚未准备完整。'
    try:
        contents_for_state(state)
    except Exception as exc:
        return str(exc)
    return cover_problem(state)


def build_snapshot(state: _PublicationState, *, check_only: bool = False) -> _Snapshot:
    draft = state.draft()
    if check_only and state.bundle_only:
        data = _read_json(Path(state.bundle) / 'manifest.json')
        stored = data.get('content') if isinstance(data, dict) else None
        if not isinstance(stored, dict):
            raise ValueError('发布资料平台文稿无效。')
        contents = {platform: copy.deepcopy(dict(stored[platform]))
                    for platform in state.selected_platforms}
    else:
        contents = contents_for_state(state)
    problem = (bundle_declaration_problem(state) if check_only else prepare_problem(state))
    if problem:
        raise ValueError(problem)
    return _Snapshot(
        state.job, state.video_ref, state.bundle_ref, state.bundle,
        state.platform, state.selected_platforms,
        draft.title.strip(), draft.description, tuple(_tags(draft.tags)),
        bool(draft.ai), _text(draft.rights), draft.cover_mode,
        float(draft.cover_seconds), draft.cover_file, contents,
        state.source_stamp, state.bundle_stamp,
    )


def page_validation_problem(state: _PublicationState, step: int) -> str:
    if step == 0:
        if state.video_ref is None or not state.job:
            return '请选择一个已核验的成品视频和来源任务。'
        if not state.source_ready:
            return state.source_problem or '来源任务资料尚未准备完整。'
        try:
            contents_for_state(state)
        except Exception as exc:
            return str(exc)
    elif step == 1:
        return cover_problem(state)
    return ''


def mark_stale(state: _PublicationState, reason: str) -> None:
    state.verified = False
    state.handoffs = {}
    state.platform_errors = {}
    state.cover_variants = {}
    if state.bundle:
        state.bundle_stale = True
        state.state = 'outdated'
        state.message = reason


def platform_handoffs_view(state: _PublicationState) -> dict[str, Any]:
    result = {}
    for platform, handoff in state.handoffs.items():
        if not isinstance(handoff, dict):
            continue
        fields = handoff.get('fields') if isinstance(handoff.get('fields'), dict) else {}
        cover = handoff_cover_path(state, platform)
        result[platform] = {
            'platform': platform,
            'fields': copy.deepcopy(fields),
            'video': str(handoff.get('video') or ''),
            'cover': str(cover or ''),
            'baseCover': str(handoff.get('cover') or ''),
            'ready': True,
        }
    for platform, message in state.platform_errors.items():
        if platform not in result and platform in state.selected_platforms:
            result[platform] = {'platform': platform, 'ready': False, 'error': message}
    return result


def review_view(state: _PublicationState) -> dict[str, Any]:
    draft = state.draft()
    video_ready = bool(state.video_ref or (state.bundle_only and state.video and not state.bundle_stale))
    video_problem = ('' if video_ready and state.source_ready else
                     state.source_problem if video_ready else '请选择已核验的成品视频。')
    cov_problem = '' if state.bundle_only and draft.cover_preview else cover_problem(state)
    platform_checks = {}
    for platform in state.selected_platforms:
        item = state.drafts[platform]
        copy_problem = ''
        try:
            content_for_draft(platform, item, require_declarations=False)
        except ValueError as error:
            copy_problem = str(error)
        platform_checks[platform] = {'copyProblem': copy_problem,
                                     'declarationProblem': declaration_problem(platform, item)}
    return {'videoReady': video_ready, 'videoProblem': video_problem,
            'coverState': 'previewed' if not cov_problem and draft.cover_preview else
                          'configured' if not cov_problem else 'missing',
            'coverProblem': cov_problem,
            'platforms': platform_checks,
            'packageState': 'stale' if state.bundle_stale else 'verified' if state.verified else
                            'unchecked' if state.bundle else 'missing'}


def actions_view(state: _PublicationState) -> dict[str, str]:
    if not state.bundle or not state.verified or state.bundle_stale:
        return {}
    return {
        'video': tr('打开成品视频'),
        'cover': tr('打开封面'),
        'bundle': tr('打开发布包'),
        'checklist': tr('打开核对清单'),
    }


def build_view(owner: Any, state: _PublicationState, active_kind: str) -> dict[str, Any]:
    draft = state.draft()
    view = {
        'step': int(state.step),
        'job': state.job,
        'video': state.video,
        'platform': state.platform,
        'title': draft.title,
        'description': draft.description,
        'tags': draft.tags,
        'ai': draft.ai,
        'rights': draft.rights,
        'coverMode': draft.cover_mode,
        'coverSeconds': float(draft.cover_seconds),
        'coverFile': draft.cover_file,
        'coverPreview': draft.cover_preview,
        'busy': bool(state.busy),
        'message': state.message,
        'state': state.state,
        'bundle': state.bundle,
        'bundleStale': bool(state.bundle_stale),
        'canPrepare': bool(state.can_prepare),
        'canCheck': bool(state.can_check),
        'verified': bool(state.verified),
        'actions': actions_view(state),
        'taskChoices': copy.deepcopy(state.task_choices),
        'taskMessage': state.task_message,
        'taskScanBusy': active_kind == 'publication-task-scan',
        'sourceReady': bool(state.source_ready),
        'sourceProblem': state.source_problem,
        'bundleOnly': bool(state.bundle_only),
        'review': review_view(state),
    }
    if (state.job or state.bundle or state.browser.state != 'idle'
            or state.selected_platforms != (BILIBILI,)):
        selected = list(state.selected_platforms)
        view.update({
            'selectedPlatforms': selected,
            'canFetchOriginalCover': state.source_mode == 'bilibili',
            'platformLabels': {platform: PLATFORM_LABELS[platform] for platform in selected},
            'coverRatio': state.cover_ratio,
            'coverVariants': copy.deepcopy(state.cover_variants.get(BILIBILI, [])),
            'handoffs': platform_handoffs_view(state),
            'browserSession': browser_view(owner, state, active_kind),
        })
    return view


def recompute_state(owner: Any, state: _PublicationState, active_kind: str) -> dict[str, Any]:
    state.busy = bool(active_kind)
    validation = prepare_problem(state)
    declaration = bundle_declaration_problem(state)
    if state.bundle and declaration:
        state.bundle_stale = True
        state.verified = False
        state.handoffs = {}
        state.cover_variants = {}
        if not state.busy and state.state not in ('error', 'failed'):
            state.state = 'outdated'
    state.can_prepare = bool(
        state.step == 2 and not state.busy
        and state.video_ref is not None and not validation)
    state.can_check = bool(
        state.bundle and not state.busy and not state.bundle_stale)
    if state.bundle_stale or (state.verified and declaration):
        state.verified = False
    return build_view(owner, state, active_kind)


def source_stamp(job: Path, video: Path, cover: Path | None) -> tuple:
    job = Path(job).resolve()
    paths = [job / name for name in ('request.json', 'source-info.json', 'upstream-info.json',
                                     '发布简介草稿.txt', 'original-cover.json')]
    paths.append(Path(video).resolve())
    if cover is not None:
        paths.append(Path(cover).resolve())
    return tuple(_stat_stamp(path) for path in paths)


def bundle_stamp(bundle: Path, data: Mapping[str, Any]) -> tuple:
    bundle = Path(bundle).resolve()
    paths = [bundle / 'manifest.json', bundle / 'cover.jpg']
    video = data.get('video') if isinstance(data, Mapping) else None
    if isinstance(video, Mapping) and isinstance(video.get('path'), str):
        try:
            paths.append(Path(publication.clean_path(video['path'])).resolve())
        except (OSError, ValueError):
            pass
    return tuple(_stat_stamp(path) for path in paths)


def bundle_platforms(state: _PublicationState) -> set[str]:
    try:
        data = _read_json(Path(state.bundle) / 'manifest.json')
        content = data.get('content') if isinstance(data, dict) else None
        return set(content) & set(PLATFORMS) if isinstance(content, dict) else set()
    except (OSError, ValueError, TypeError):
        return set()


def maybe_mark_drift(state: _PublicationState) -> bool:
    if state.video_ref is None and not state.bundle:
        return False
    drift = False
    if state.video_ref is not None and state.job and state.video:
        cover = Path(state.original_cover) if state.original_cover else None
        current_source = source_stamp(Path(state.job), Path(state.video), cover)
        if (not state.source_drift and state.source_stamp
                and current_source != state.source_stamp):
            state.source_drift = True
            state.source_ready = False if state.source_mode != 'local' else state.source_ready
            mark_stale(state, '来源资料或成品已发生变化；请重新载入任务或重新生成发布资料。')
            state.state = 'outdated' if state.bundle else 'editing'
            drift = True
    if state.bundle and not state.bundle_stale:
        try:
            data = _read_json(Path(state.bundle) / 'manifest.json')
            current_bundle = bundle_stamp(Path(state.bundle), data)
        except (OSError, ValueError, TypeError):
            current_bundle = ()
        if state.bundle_stamp and current_bundle != state.bundle_stamp:
            state.bundle_stamp = current_bundle
            mark_stale(state, '发布资料或本地素材已发生变化，请重新生成。')
            state.state = 'outdated'
            drift = True
        else:
            for platform, handoff in tuple(state.handoffs.items()):
                stamps = handoff['stamps']
                try:
                    unchanged = all(publication.publication_file_stamp(path) == stamp
                                    for path, stamp in stamps.items())
                except OSError:
                    unchanged = False
                if not unchanged:
                    state.handoffs.pop(platform, None)
                    state.platform_errors[platform] = PLATFORM_LABELS[platform] + '资料已变化，请重新核对。'
                    state.cover_variants.pop(platform, None)
                    drift = True
            if drift and not state.bundle_stale:
                state.verified = bool(state.handoffs)
                state.message = '；'.join(state.platform_errors.values())
    return drift


def cached_original_cover(job: Path) -> Path | None:
    try:
        info = publication.read_source_info(job)
        if not _text(info.get('bvid')):
            return None
        from cover_art import cached_cover
        found = cached_cover(info, Path(job).resolve())
        return found[0].resolve() if found is not None else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def probe_image(path: Path, stop) -> None:
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


def draft_path(owner: Any) -> Path:
    override = getattr(owner, 'quick_publication_draft_path', None)
    return Path(override or (Path(ROOT) / 'quick-publication-draft.json')).resolve()


def load_saved_draft(owner: Any, job: Path, video: _ArtifactRef, expected_source_stamp: tuple):
    try:
        path = draft_path(owner)
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
        if saved_stamp != expected_source_stamp:
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def draft_from_saved(
    value: Mapping[str, Any],
    fallback: _PlatformDraft,
    *,
    local_url_fn: Callable[[Any], str] = _default_local_url,
) -> _PlatformDraft:
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
        local_url_fn(cover_file) if result_mode == 'custom' and cover_file else '')
    return _PlatformDraft(
        title=_text(value.get('title')), description=_text(value.get('description')),
        tags=_text(value.get('tags')), ai=optional_bool(value.get('ai')),
        rights=rights,
        cover_mode=result_mode,
        cover_seconds=seconds, cover_file=cover_file,
        cover_preview=preview,
    )


def save_draft_safe(owner: Any, state: _PublicationState) -> None:
    if not state.bundle and (not state.job or state.video_ref is None):
        return
    payload = {
        'version': 1,
        'job': state.job,
        'video': state.video,
        'videoSha256': state.video_ref.sha256 if state.video_ref is not None else '',
        'sourceStamp': [list(item) if item is not None else None for item in state.source_stamp],
        'bundle': state.bundle,
        'bundleOnly': bool(state.bundle_only),
        'platform': state.platform,
        'selectedPlatforms': list(state.selected_platforms),
        'coverRatio': state.cover_ratio,
        'step': state.step,
        'drafts': {
            platform: {
                'title': draft.title, 'description': draft.description, 'tags': draft.tags,
                'ai': draft.ai, 'rights': draft.rights, 'coverMode': draft.cover_mode,
                'coverSeconds': draft.cover_seconds, 'coverFile': draft.cover_file,
            }
            for platform, draft in state.drafts.items()
        },
    }
    try:
        atomic_json(draft_path(owner), payload)
    except OSError:
        # Draft persistence is a convenience; the immutable bundle remains authoritative.
        pass


def discard_preview_dir(directory: Path | None, keep: Path | None) -> None:
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
