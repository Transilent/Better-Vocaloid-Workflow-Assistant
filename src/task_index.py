"""Rebuildable SQLite3 task metadata read-cache index.

Source-of-truth invariant: every task folder's own ``request.json`` and
``status.json`` remain the sole authoritative records. ``cache/tasks-index.sqlite3``
is purely a disposable read-acceleration index that can be deleted or rebuilt
from disk at any time with zero data loss.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from vwa_paths import ROOT

INDEX_SCHEMA_VERSION = 1
INDEX_REL_PATH = Path("cache/tasks-index.sqlite3")
INDEX_MAX_ENTRIES = 512

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    job_path TEXT PRIMARY KEY,
    stamps_json TEXT NOT NULL,
    title TEXT NOT NULL,
    modified REAL NOT NULL,
    row_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_modified ON tasks(modified DESC);
"""


def index_path(root=ROOT) -> Path:
    return Path(root).resolve() / INDEX_REL_PATH


def _purge_sqlite_files(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(db_path) + suffix)
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=3.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(_DDL)
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(INDEX_SCHEMA_VERSION),),
            )
            conn.commit()
        elif row[0] != str(INDEX_SCHEMA_VERSION):
            conn.execute("DELETE FROM tasks")
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(INDEX_SCHEMA_VERSION),),
            )
            conn.commit()
        return conn
    except BaseException:
        conn.close()
        raise


def load_cached_entries(root=ROOT) -> tuple[dict[str, dict[str, Any]], bool]:
    """Load cached task rows keyed by absolute job path.

    Returns ``(entries, healthy)``. On corruption, automatically purges the
    disposable database file so the caller transparently rebuilds from disk.
    """
    db_path = index_path(root)
    if not db_path.is_file():
        return {}, False
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute("SELECT job_path, stamps_json, row_json FROM tasks").fetchall()
        finally:
            conn.close()
        entries: dict[str, dict[str, Any]] = {}
        for job_path, stamps_raw, row_raw in rows:
            try:
                stamps = json.loads(stamps_raw)
                row = json.loads(row_raw)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(job_path, str)
                and isinstance(stamps, dict)
                and isinstance(row, dict)
                and isinstance(row.get("request"), dict)
                and isinstance(row.get("status"), dict)
                and isinstance(row.get("title"), str)
                and type(row.get("modified")) in (int, float)
                and math.isfinite(row["modified"])
            ):
                entries[job_path] = {"stamps": stamps, "row": row}
        return entries, True
    except (sqlite3.DatabaseError, OSError):
        _purge_sqlite_files(db_path)
        return {}, False


def sync_entries(root=ROOT, entries: dict[str, dict[str, Any]] | None = None, *, limit: int = INDEX_MAX_ENTRIES) -> bool:
    """Incrementally sync task entries into the SQLite index (bounded by newest *limit*)."""
    db_path = index_path(root)
    entries = entries or {}
    ordered = sorted(
        entries.items(),
        key=lambda item: float(item[1].get("row", {}).get("modified", 0.0)),
        reverse=True,
    )[: max(1, int(limit))]
    keep_keys = {k for k, _ in ordered}

    for attempt in range(2):
        try:
            conn = _connect(db_path)
            try:
                existing = {
                    row[0]: row[1]
                    for row in conn.execute("SELECT job_path, stamps_json FROM tasks").fetchall()
                }
                to_delete = [k for k in existing if k not in keep_keys]
                to_upsert = []
                for job_path, record in ordered:
                    stamps_str = json.dumps(record["stamps"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    if existing.get(job_path) == stamps_str:
                        continue
                    row = record["row"]
                    row_str = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    to_upsert.append((job_path, stamps_str, str(row.get("title", "")), float(row.get("modified", 0.0)), row_str))
                if to_delete or to_upsert:
                    with conn:
                        if to_delete:
                            conn.executemany("DELETE FROM tasks WHERE job_path = ?", [(k,) for k in to_delete])
                        if to_upsert:
                            conn.executemany(
                                "INSERT OR REPLACE INTO tasks(job_path, stamps_json, title, modified, row_json) "
                                "VALUES(?, ?, ?, ?, ?)",
                                to_upsert,
                            )
                return True
            finally:
                conn.close()
        except (sqlite3.DatabaseError, OSError):
            _purge_sqlite_files(db_path)
            if attempt == 1:
                return False
    return False
