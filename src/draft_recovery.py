"""Crash-safe debounced draft recovery for Chorus and Mixdown workspaces.

Pure-Python domain module (zero Qt dependency). Draft snapshots are written
atomically under ``cache/recovery/`` and cleared automatically on clean save
or graceful exit.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Any

from vwa_paths import ROOT

DRAFT_SCHEMA = 'vwa-draft-recovery/1'
VALID_WORKSPACES = frozenset({'chorus', 'mixdown'})


def recovery_dir(root: Path = ROOT) -> Path:
    return Path(root).resolve() / 'cache' / 'recovery'


def _normalize_workspace(workspace: str) -> str:
    key = str(workspace or '').strip().lower()
    if key not in VALID_WORKSPACES:
        raise ValueError(f'Unsupported recovery workspace: {workspace!r}')
    return key


def draft_path(workspace: str, root: Path = ROOT) -> Path:
    return recovery_dir(root) / f'{_normalize_workspace(workspace)}.draft.json'


def chorus_draft_project_path(root: Path = ROOT) -> Path:
    return recovery_dir(root) / 'chorus.draft.vwachorus'


def _atomic_write_json(path: Path, document: dict[str, Any]) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix=f'.{path.name}-', suffix='.tmp', delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_draft(
    workspace: str,
    payload: dict[str, Any],
    *,
    label: str = '',
    source_path: str = '',
    root: Path = ROOT,
) -> Path:
    key = _normalize_workspace(workspace)
    now = time.time()
    stamp = datetime.fromtimestamp(now).strftime('%H:%M')
    clean_label = str(label or '').strip() or ('修音草稿' if key == 'chorus' else '混音草稿')
    envelope = {
        'schema': DRAFT_SCHEMA,
        'workspace': key,
        'saved_at': now,
        'saved_clock': stamp,
        'label': f'{clean_label} ({stamp})',
        'source_path': str(source_path or ''),
        'payload': payload,
    }
    return _atomic_write_json(draft_path(key, root), envelope)


def load_draft(workspace: str, root: Path = ROOT) -> dict[str, Any] | None:
    path = draft_path(workspace, root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('schema') != DRAFT_SCHEMA:
        return None
    if data.get('workspace') != _normalize_workspace(workspace):
        return None
    if not isinstance(data.get('payload'), dict):
        return None
    return data


def read_draft_meta(workspace: str, root: Path = ROOT) -> dict[str, Any] | None:
    data = load_draft(workspace, root)
    if data is None:
        return None
    if _normalize_workspace(workspace) == 'chorus':
        project_file = chorus_draft_project_path(root)
        if not project_file.is_file():
            return None
    return {
        'workspace': data['workspace'],
        'saved_at': float(data.get('saved_at', 0.0) or 0.0),
        'saved_clock': str(data.get('saved_clock') or ''),
        'label': str(data.get('label') or ''),
        'source_path': str(data.get('source_path') or ''),
    }


def has_draft(workspace: str, root: Path = ROOT) -> bool:
    return read_draft_meta(workspace, root) is not None


def clear_draft(workspace: str, root: Path = ROOT) -> bool:
    key = _normalize_workspace(workspace)
    removed = False
    for target in (draft_path(key, root), *(
        (chorus_draft_project_path(root),) if key == 'chorus' else ()
    )):
        try:
            if target.exists():
                target.unlink()
                removed = True
        except OSError:
            pass
    return removed


def write_chorus_draft(project: Any, *, source_path: str = '', root: Path = ROOT) -> Path:
    """Persist a ChorusProject draft as both standard vwa-chorus/1 file and metadata."""
    document = asdict(project)
    if document.get('score') and isinstance(document['score'], dict):
        document['score'].pop('lanes', None)
    document['schema'] = 'vwa-chorus/1'
    project_file = _atomic_write_json(chorus_draft_project_path(root), document)
    title = str(getattr(project, 'title', '') or '未命名修音')
    return save_draft(
        'chorus',
        {'project_file': str(project_file), 'track_count': len(getattr(project, 'tracks', ()))},
        label=title,
        source_path=str(source_path or ''),
        root=root,
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def mixdown_state_to_draft(state: Any) -> dict[str, Any]:
    """Capture editable mixdown track paths & DSP parameters into a JSON-safe dict."""
    tracks = []
    for row in getattr(state, 'tracks', ()):
        tracks.append({
            'id': str(row.get('id') or ''),
            'label': str(row.get('label') or ''),
            'path': str(row.get('path') or ''),
            'gain': _safe_float(row.get('gain'), 0.0),
            'muted': bool(row.get('muted', False)),
            'solo': bool(row.get('solo', False)),
            'pan': _safe_float(row.get('pan'), 0.0),
            'offset': _safe_float(row.get('offset'), 0.0),
            'send': _safe_float(row.get('send'), 1.0),
            'deess_enabled': bool(row.get('deess_enabled', False)),
            'deess_strength': _safe_float(row.get('deess_strength'), 0.35),
        })
    return {
        'job': str(getattr(state, 'job', '') or ''),
        'output': str(getattr(state, 'output', '') or ''),
        'preset_name': str(getattr(state, 'preset_name', '自定义') or '自定义'),
        'master_gain': _safe_float(getattr(state, 'master_gain', 0.0), 0.0),
        'eq_enabled': bool(getattr(state, 'eq_enabled', False)),
        'bands': list(getattr(state, 'bands', ())),
        'compression_enabled': bool(getattr(state, 'compression_enabled', False)),
        'compression_strength': _safe_float(getattr(state, 'compression_strength', 0.25), 0.25),
        'reverb_enabled': bool(getattr(state, 'reverb_enabled', False)),
        'reverb_room': str(getattr(state, 'reverb_room', 'natural') or 'natural'),
        'reverb_mix': _safe_float(getattr(state, 'reverb_mix', 0.10), 0.10),
        'reverb_predelay_ms': _safe_float(getattr(state, 'reverb_predelay_ms', 0.0), 0.0),
        'width': _safe_float(getattr(state, 'width', 1.0), 1.0),
        'tracks': tracks,
    }


def apply_mixdown_draft(state: Any, payload: dict[str, Any]) -> int:
    """Restore a saved mixdown draft onto a MixdownWorkspaceState. Returns restored media count."""
    restored_media = 0
    tracks = payload.get('tracks') if isinstance(payload.get('tracks'), list) else []
    existing_ids = {row['id'] for row in state.tracks}
    for row in tracks:
        if not isinstance(row, dict):
            continue
        tid = str(row.get('id') or '')
        if not tid:
            continue
        while tid not in existing_ids and tid != 'instrumental':
            try:
                added = state.add_vocal()
                existing_ids.add(added)
            except ValueError:
                break
        if tid not in existing_ids:
            continue
        raw_path = str(row.get('path') or '').strip()
        if raw_path and Path(raw_path).is_file():
            try:
                state.set_source(tid, raw_path)
                restored_media += 1
            except ValueError:
                pass
        for field in ('gain', 'pan', 'muted', 'solo', 'send', 'deess_enabled', 'deess_strength'):
            if field in row:
                try:
                    state.set_track(tid, field, row[field])
                except ValueError:
                    pass
        if tid != 'instrumental' and 'offset' in row:
            try:
                state.set_track(tid, 'offset', row['offset'])
            except ValueError:
                pass
    for attr in ('master_gain', 'eq_enabled', 'compression_enabled', 'compression_strength',
                 'reverb_enabled', 'reverb_room', 'reverb_mix', 'reverb_predelay_ms', 'width'):
        if attr in payload:
            try:
                state.set_option(attr, payload[attr])
            except (ValueError, AttributeError):
                setattr(state, attr, payload[attr])
    if isinstance(payload.get('bands'), list) and len(payload['bands']) == 4:
        for idx, band in enumerate(payload['bands']):
            if isinstance(band, dict):
                for key in ('freq_hz', 'gain_db', 'q', 'enabled'):
                    if key in band:
                        try:
                            state.set_band(idx, key, band[key])
                        except (ValueError, AttributeError):
                            pass
    if payload.get('preset_name'):
        state.preset_name = str(payload['preset_name'])
    return restored_media
