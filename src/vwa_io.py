"""Zero-cycle atomic JSON, file locking, hashing, safe tree checks, and cancellation helpers."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import errno
from functools import lru_cache
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import weakref

from vwa_paths import native_path

_ATOMIC_LOCKS = weakref.WeakValueDictionary()
_ATOMIC_GUARD = threading.Lock()
_EXPORT_IMPORT_LOCK = threading.Lock()


class SidecarBusy(BlockingIOError):
    """Another process holds the first byte of the agreed sidecar."""


@lru_cache(maxsize=16)
def bundled_export_module(root, name):
    """Load an existing export runtime module without leaving it on sys.path."""
    runtime = (Path(root) / "dependencies/export").resolve()
    if not runtime.is_dir():
        raise ImportError("VWA export runtime is missing")
    with _EXPORT_IMPORT_LOCK:
        path = str(runtime)
        inserted = path not in sys.path
        if inserted:
            sys.path.insert(0, path)
        try:
            module = import_module(name)
            if not Path(module.__file__).resolve().is_relative_to(runtime):
                raise ImportError("module is not from the VWA export runtime")
            return module
        finally:
            if inserted:
                sys.path.remove(path)


@contextmanager
def exclusive_sidecar(path, *, create=True, blocking=False, cancel=lambda: None):
    """Use the existing one-byte lock protocol; caller owns path validation.

    Probes use create=False so listing jobs never creates or updates a lock.
    The sidecar stays in place after release: unlinking it can split ownership.
    """
    if os.name == "nt":
        import msvcrt

        def acquire(stream):
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

        def release(stream):
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire(stream):
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release(stream):
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    with open(native_path(path), "a+b" if create else "r+b") as stream:
        while True:
            stream.seek(0)
            try:
                acquire(stream)
                break
            except OSError as error:
                if (
                    error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EWOULDBLOCK)
                    and getattr(error, "winerror", None) not in (32, 33)
                ):
                    raise
                if not blocking:
                    raise SidecarBusy(str(error)) from error
                cancel()
                time.sleep(0.05)
        try:
            if create and stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
            yield
        finally:
            stream.seek(0)
            release(stream)


def locked(job) -> bool:
    """Return True if *job/run.lock* is currently held by another active run."""
    path = Path(job) / "run.lock"
    try:
        with exclusive_sidecar(path, create=False):
            pass
        return False
    except FileNotFoundError:
        return False
    except OSError:
        return True


def checked_tree(path, boundary) -> Path:
    """Refuse links/junctions before any recursive operation; never follow them."""
    path, boundary = Path(path).absolute(), Path(boundary).resolve()
    if path == boundary or not path.resolve().is_relative_to(boundary):
        raise ValueError("清理目标超出允许的目录。")
    for candidate in (path, *path.parents):
        if candidate == boundary:
            break
        if candidate.is_symlink() or (
            candidate.exists() and getattr(candidate.lstat(), "st_file_attributes", 0) & 1024
        ):
            raise ValueError("目录包含链接或重解析点，已跳过清理。")
    if path.is_dir():
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                candidate = Path(directory) / name
                if candidate.is_symlink() or getattr(candidate.lstat(), "st_file_attributes", 0) & 1024:
                    raise ValueError("目录包含链接或重解析点，已跳过清理。")
                if not candidate.resolve().is_relative_to(path.resolve()):
                    raise ValueError("清理目标包含外部文件。")
    return path


def file_hash(path, *, check=None) -> str:
    digest = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if check is not None and check():
                raise InterruptedError("已取消文件校验。")
            digest.update(block)
    return digest.hexdigest()


def fingerprint(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atomic_json(path, data) -> None:
    path = Path(path)
    Path(native_path(path.parent)).mkdir(parents=True, exist_ok=True)
    key = str(path.absolute()).casefold()
    with _ATOMIC_GUARD:
        lock = _ATOMIC_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ATOMIC_LOCKS[key] = lock
    with lock:
        _write_json(path, data)


def _write_json(path, data) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=native_path(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
        for attempt in range(6):
            try:
                os.replace(name, native_path(path))
                break
            except OSError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(0.01 * 2**attempt)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def cancellation_requested(job) -> bool:
    job = Path(job)
    if Path(native_path(job / "cancel.flag")).exists():
        return True
    token = os.environ.get("VWA_RUN_CANCEL_TOKEN", "")
    return bool(re.fullmatch(r"[a-f0-9]{32}", token) and Path(native_path(job / f".cancel-{token}.flag")).exists())


def cancelled(job) -> None:
    if cancellation_requested(job):
        raise InterruptedError("已取消任务；已完成的步骤可以继续使用。")


def preserve_changed_file(job, path, expected_sha256=None, replacement_sha256=None):
    """Keep edits before regenerating an output; intact generated files need no copy."""
    job, path = Path(job).resolve(), Path(path).absolute()
    checked_tree(path, job)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    current = file_hash(path)
    if current in (expected_sha256, replacement_sha256):
        return None
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%y%m%d-%H%M%S")
    backup = job / "backups" / (stamp + "-" + uuid.uuid4().hex[:6] + "_" + path.stem[:60] + path.suffix)
    checked_tree(backup, job)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    if file_hash(backup) != current or file_hash(path) != current:
        raise OSError("备份期间文件发生变化，请重试：" + path.name)
    relative = backup.relative_to(job).as_posix()
    print("已保留原文件：" + relative, flush=True)
    return relative
