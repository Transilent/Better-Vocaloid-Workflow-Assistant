"""Global MRU (Most Recently Used) project/file store and smart drop classifier.

Pure-Python domain module (zero Qt imports). Persists up to 12 deduplicated
recent items under ``cache/recent-items.json`` and automatically prunes files
that no longer exist on disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Any, Sequence

from vwa_paths import ROOT

RECENT_SCHEMA = 'vwa-recent-items/1'
MAX_RECENT_ITEMS = 12

CHORUS_EXTS = frozenset({'.vwachorus'})
VOCAL_PROJECT_EXTS = frozenset({'.svp', '.ustx', '.vpr', '.vsqx', '.acep', '.ufdata'})
MIDI_EXTS = frozenset({'.mid', '.midi'})
AUDIO_EXTS = frozenset({'.wav', '.flac', '.mp3', '.ogg', '.opus', '.m4a', '.aac', '.aif', '.aiff'})
VIDEO_EXTS = frozenset({'.mp4', '.mkv', '.mov', '.webm', '.avi'})

VALID_KINDS = frozenset({'chorus', 'tuning', 'project_convert', 'mixdown', 'video', 'preparation'})

KIND_ROUTES = {
    'chorus': 'chorus',
    'tuning': 'tuning',
    'project_convert': 'project_convert',
    'mixdown': 'audio_mixdown',
    'video': 'video_mixdown',
    'preparation': 'processing',
}

KIND_LABELS = {
    'chorus': '修音工作台',
    'tuning': '调音工作台',
    'project_convert': '工程格式互转',
    'mixdown': '简易混音',
    'video': '视频合成',
    'preparation': '一键准备',
}


def recent_items_path(root: Path = ROOT) -> Path:
    return Path(root).resolve() / 'cache' / 'recent-items.json'


def _atomic_write(path: Path, doc: dict[str, Any]) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix='.recent-', suffix='.tmp', delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump(doc, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _read_raw(root: Path = ROOT) -> list[dict[str, Any]]:
    path = recent_items_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict) or data.get('schema') != RECENT_SCHEMA:
        return []
    items = data.get('items')
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        raw_path = str(row.get('path') or '').strip()
        kind = str(row.get('kind') or '').strip()
        if not raw_path or kind not in VALID_KINDS:
            continue
        out.append({
            'path': raw_path,
            'kind': kind,
            'route': KIND_ROUTES.get(kind, 'processing'),
            'kindLabel': KIND_LABELS.get(kind, kind),
            'title': str(row.get('title') or Path(raw_path).stem),
            'fileName': Path(raw_path).name,
            'fmt': str(row.get('fmt') or Path(raw_path).suffix.lstrip('.').upper()),
            'opened_at': float(row.get('opened_at', 0.0) or 0.0),
        })
    return out


def list_recent(
    kind: str | None = None,
    *,
    root: Path = ROOT,
    prune_missing: bool = True,
) -> list[dict[str, Any]]:
    items = _read_raw(root)
    if prune_missing:
        existing = [row for row in items if Path(row['path']).is_file()]
        if len(existing) != len(items):
            try:
                _atomic_write(recent_items_path(root), {'schema': RECENT_SCHEMA, 'items': existing})
            except OSError:
                pass
        items = existing
    if kind:
        items = [row for row in items if row['kind'] == kind]
    return items[:MAX_RECENT_ITEMS]


def record_recent(
    path: str | Path,
    kind: str,
    *,
    title: str = '',
    fmt: str = '',
    root: Path = ROOT,
) -> list[dict[str, Any]]:
    norm_kind = str(kind or '').strip()
    if norm_kind not in VALID_KINDS:
        raise ValueError(f'Unsupported recent item kind: {kind!r}')
    resolved = str(Path(str(path)).expanduser().resolve())
    key = (os.path.normcase(resolved), norm_kind)
    items = _read_raw(root)
    filtered = [
        row for row in items
        if (os.path.normcase(row['path']), row['kind']) != key
    ]
    entry = {
        'path': resolved,
        'kind': norm_kind,
        'route': KIND_ROUTES.get(norm_kind, 'processing'),
        'kindLabel': KIND_LABELS.get(norm_kind, norm_kind),
        'title': str(title or '').strip() or Path(resolved).stem,
        'fileName': Path(resolved).name,
        'fmt': str(fmt or '').strip().upper() or Path(resolved).suffix.lstrip('.').upper(),
        'opened_at': time.time(),
    }
    updated = [entry, *filtered][:MAX_RECENT_ITEMS]
    _atomic_write(recent_items_path(root), {'schema': RECENT_SCHEMA, 'items': updated})
    return updated


def clear_recent(root: Path = ROOT) -> None:
    try:
        recent_items_path(root).unlink(missing_ok=True)
    except OSError:
        pass


def classify_dropped_files(paths: Sequence[str | Path]) -> dict[str, Any]:
    """Classify dropped local file paths into actionable workspace routing targets."""
    clean: list[str] = []
    for raw in paths or ():
        text = str(raw or '').strip()
        if text:
            clean.append(text)
    if not clean:
        return {'category': 'empty', 'paths': [], 'primaryName': '', 'targets': []}

    primary = Path(clean[0])
    exts = {Path(p).suffix.lower() for p in clean}
    name = primary.name if len(clean) == 1 else f'{primary.name} (+{len(clean) - 1})'

    if any(ext in CHORUS_EXTS for ext in exts):
        return {
            'category': 'chorus_project',
            'paths': clean,
            'primaryName': name,
            'targets': [
                {'id': 'chorus_project', 'route': 'chorus', 'label': '在修音工作台打开工程', 'subtitle': '加载全部人声轨、乐谱与编辑进度'},
            ],
        }
    if any(ext in VOCAL_PROJECT_EXTS for ext in exts):
        return {
            'category': 'vocal_project',
            'paths': clean,
            'primaryName': name,
            'targets': [
                {'id': 'tuning_project', 'route': 'tuning', 'label': '在调音工作台打开', 'subtitle': '进入钢琴卷帘编辑音符、歌词与演唱风格'},
                {'id': 'convert_project', 'route': 'project_convert', 'label': '在工程格式互转打开', 'subtitle': '转换为 SVP / USTX / VPR / VSQX / UFData / MIDI'},
                {'id': 'chorus_score', 'route': 'chorus', 'label': '作为修音对照谱轨导入', 'subtitle': '导入到修音工作台作为音高与节奏参照'},
            ],
        }
    if any(ext in MIDI_EXTS for ext in exts):
        return {
            'category': 'midi',
            'paths': clean,
            'primaryName': name,
            'targets': [
                {'id': 'chorus_score', 'route': 'chorus', 'label': '在修音工作台导入乐谱', 'subtitle': '对照 MIDI 自动对齐与修音'},
                {'id': 'convert_project', 'route': 'project_convert', 'label': '在工程格式互转打开', 'subtitle': '将 MIDI 转为各歌声合成器工程'},
            ],
        }
    if any(ext in VIDEO_EXTS for ext in exts):
        return {
            'category': 'video',
            'paths': clean,
            'primaryName': name,
            'targets': [
                {'id': 'video_source', 'route': 'video_mixdown', 'label': '在视频合成中载入原视频', 'subtitle': '与混音成品对齐并封装成品 MP4'},
                {'id': 'prep_audio', 'route': 'processing', 'label': '在一键准备中提取音频素材', 'subtitle': '作为翻唱准备源自动分离伴奏与干声'},
            ],
        }
    if any(ext in AUDIO_EXTS for ext in exts):
        return {
            'category': 'audio',
            'paths': clean,
            'primaryName': name,
            'targets': [
                {'id': 'prep_audio', 'route': 'processing', 'label': '在一键准备中开始扒谱/分离', 'subtitle': '分离人声伴奏并生成音符工程'},
                {'id': 'chorus_audio', 'route': 'chorus', 'label': '在修音工作台导入干声', 'subtitle': '添加为人声轨进行节奏对齐与音高精修'},
                {'id': 'mixdown_vocal', 'route': 'audio_mixdown', 'label': '在简易混音中载入人声', 'subtitle': '进入实时混音台调整 EQ、压缩与混响'},
            ],
        }
    return {'category': 'unknown', 'paths': clean, 'primaryName': name, 'targets': []}
