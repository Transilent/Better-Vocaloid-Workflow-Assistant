"""Persistent multi-song preparation batch queue.

Pure-Python domain module (zero Qt imports). Stores queued songs under
``cache/prep-queue.json`` so users can drop or queue multiple songs and let
the preparation pipeline advance sequentially.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from typing import Any
from uuid import uuid4

from vwa_paths import ROOT

QUEUE_SCHEMA = 'vwa-prep-queue/1'
MAX_QUEUE_ITEMS = 32


def queue_path(root: Path = ROOT) -> Path:
    return Path(root).resolve() / 'cache' / 'prep-queue.json'


def _atomic_write(path: Path, doc: dict[str, Any]) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix='.prep-queue-', suffix='.tmp', delete=False,
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


class PrepQueueStore:
    """File-backed FIFO queue for sequential multi-song preparation tasks."""

    def __init__(self, root: Path = ROOT) -> None:
        self._root = Path(root).resolve()

    def _read(self) -> list[dict[str, Any]]:
        path = queue_path(self._root)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, TypeError):
            return []
        if not isinstance(data, dict) or data.get('schema') != QUEUE_SCHEMA:
            return []
        rows = data.get('items')
        return [dict(r) for r in rows if isinstance(r, dict) and r.get('id') and r.get('music_path')] if isinstance(rows, list) else []

    def _write(self, items: list[dict[str, Any]]) -> None:
        _atomic_write(queue_path(self._root), {'schema': QUEUE_SCHEMA, 'items': items[:MAX_QUEUE_ITEMS]})

    def list_items(self) -> list[dict[str, Any]]:
        return self._read()

    def enqueue(
        self,
        music_path: str | Path,
        *,
        title: str = '',
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = str(Path(str(music_path)).expanduser().resolve())
        items = self._read()
        if len(items) >= MAX_QUEUE_ITEMS:
            raise ValueError(f'等待队列已满（最多 {MAX_QUEUE_ITEMS} 首）。')
        entry = {
            'id': uuid4().hex[:12],
            'music_path': resolved,
            'fileName': Path(resolved).name,
            'title': str(title or '').strip() or Path(resolved).stem,
            'options': dict(options or {}),
            'queued_at': time.time(),
        }
        items.append(entry)
        self._write(items)
        return entry

    def dequeue(self) -> dict[str, Any] | None:
        items = self._read()
        if not items:
            return None
        first = items.pop(0)
        self._write(items)
        return first

    def remove(self, item_id: str) -> bool:
        items = self._read()
        updated = [row for row in items if row.get('id') != item_id]
        if len(updated) == len(items):
            return False
        self._write(updated)
        return True

    def move(self, item_id: str, delta: int) -> bool:
        items = self._read()
        idx = next((i for i, row in enumerate(items) if row.get('id') == item_id), -1)
        if idx < 0:
            return False
        target = max(0, min(len(items) - 1, idx + int(delta)))
        if target == idx:
            return False
        row = items.pop(idx)
        items.insert(target, row)
        self._write(items)
        return True

    def clear(self) -> int:
        count = len(self._read())
        try:
            queue_path(self._root).unlink(missing_ok=True)
        except OSError:
            pass
        return count
