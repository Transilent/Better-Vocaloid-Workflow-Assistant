"""Lightweight local crash capture: Python faulthandler + Windows MiniDumpNormal.

Zero third-party dependencies. Uses only Python stdlib + Windows built-in
``dbghelp.dll`` (``MiniDumpWriteDump`` with ``MiniDumpNormal = 0x00000000``,
which captures only thread stacks and loaded modules — no full heap dump, no
user lyrics/media/cookies).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import faulthandler
import os
from pathlib import Path
from typing import Any

from vwa_paths import ROOT

DUMP_DIR_REL = Path("cache/diagnostics/dumps")
TRACEBACK_FILE_REL = Path("cache/diagnostics/crash-traceback.log")
MAX_DUMPS = 3

_MINI_DUMP_NORMAL = 0x00000000
_GENERIC_WRITE = 0x40000000
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_EXCEPTION_CONTINUE_SEARCH = 0

# Retain handles/callbacks at module scope so GC never invalidates them.
_TRACEBACK_STREAM = None
_SEH_FILTER_REF = None
_ACTIVE_DUMP_DIR: Path | None = None


class _MINIDUMP_EXCEPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ThreadId", wintypes.DWORD),
        ("ExceptionPointers", ctypes.c_void_p),
        ("ClientPointers", wintypes.BOOL),
    ]


def prune_old_dumps(dump_dir: Path, *, keep: int = MAX_DUMPS) -> int:
    """Keep only the newest *keep* MiniDump files in *dump_dir*."""
    if not dump_dir.is_dir():
        return 0
    dumps = sorted(
        (p for p in dump_dir.glob("vwa-crash-*.dmp") if p.is_file() and not p.is_symlink()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for stale in dumps[max(0, keep):]:
        try:
            stale.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def write_minidump(target_path: str | Path, exception_pointers: int | None = None) -> bool:
    """Write a privacy-safe Windows MiniDumpNormal (.dmp) using built-in dbghelp.dll."""
    if os.name != "nt":
        return False
    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)

        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcessId.argtypes = []
        k32.GetCurrentProcessId.restype = wintypes.DWORD
        k32.GetCurrentThreadId.argtypes = []
        k32.GetCurrentThreadId.restype = wintypes.DWORD

        dbghelp.MiniDumpWriteDump.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL

        handle = k32.CreateFileW(
            str(target),
            _GENERIC_WRITE,
            0,
            None,
            _CREATE_ALWAYS,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            return False
        try:
            exc_info_ptr = None
            exc_info = None
            if exception_pointers:
                exc_info = _MINIDUMP_EXCEPTION_INFORMATION(
                    ThreadId=k32.GetCurrentThreadId(),
                    ExceptionPointers=exception_pointers,
                    ClientPointers=False,
                )
                exc_info_ptr = ctypes.byref(exc_info)
            ok = bool(
                dbghelp.MiniDumpWriteDump(
                    k32.GetCurrentProcess(),
                    k32.GetCurrentProcessId(),
                    handle,
                    _MINI_DUMP_NORMAL,
                    exc_info_ptr,
                    None,
                    None,
                )
            )
            return ok and target.is_file() and target.stat().st_size > 0
        finally:
            k32.CloseHandle(handle)
    except OSError:
        return False


def enable_crash_capture(root=ROOT, *, install_seh: bool = True) -> dict[str, Any]:
    """Enable faulthandler + Windows SEH MiniDump capture into cache/diagnostics/."""
    global _TRACEBACK_STREAM, _SEH_FILTER_REF, _ACTIVE_DUMP_DIR

    root = Path(root).resolve()
    dump_dir = root / DUMP_DIR_REL
    tb_path = root / TRACEBACK_FILE_REL
    dump_dir.mkdir(parents=True, exist_ok=True)
    prune_old_dumps(dump_dir, keep=MAX_DUMPS)
    _ACTIVE_DUMP_DIR = dump_dir

    faulthandler_ok = False
    try:
        if _TRACEBACK_STREAM is not None:
            try:
                _TRACEBACK_STREAM.close()
            except OSError:
                pass
        _TRACEBACK_STREAM = tb_path.open("a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_TRACEBACK_STREAM, all_threads=True)
        faulthandler_ok = True
    except (OSError, RuntimeError, ValueError):
        faulthandler_ok = False

    seh_ok = False
    if install_seh and os.name == "nt":
        try:
            filter_type = ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.c_void_p)

            def _on_unhandled_exception(exc_ptrs):
                folder = _ACTIVE_DUMP_DIR
                if folder is not None:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                    write_minidump(folder / f"vwa-crash-{stamp}.dmp", exc_ptrs)
                return _EXCEPTION_CONTINUE_SEARCH

            callback = filter_type(_on_unhandled_exception)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.SetUnhandledExceptionFilter.argtypes = [ctypes.c_void_p]
            k32.SetUnhandledExceptionFilter.restype = ctypes.c_void_p
            k32.SetUnhandledExceptionFilter(callback)
            _SEH_FILTER_REF = callback
            seh_ok = True
        except OSError:
            seh_ok = False

    return {
        "faulthandler": faulthandler_ok,
        "seh_minidump": seh_ok,
        "dump_dir": str(dump_dir),
        "traceback_file": str(tb_path),
    }


def recent_crash_artifacts(root=ROOT) -> dict[str, Any]:
    """Summarize recent crash logs and MiniDump metadata (strictly redacted)."""
    root = Path(root).resolve()
    tb_path = root / TRACEBACK_FILE_REL
    dump_dir = root / DUMP_DIR_REL
    tb_tail = ""
    if tb_path.is_file() and not tb_path.is_symlink():
        try:
            tb_tail = tb_path.read_bytes()[-32768:].decode("utf-8", errors="replace")
        except OSError:
            tb_tail = ""
    dumps = []
    if dump_dir.is_dir() and not dump_dir.is_symlink():
        for p in sorted(
            (x for x in dump_dir.glob("vwa-crash-*.dmp") if x.is_file() and not x.is_symlink()),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )[:MAX_DUMPS]:
            try:
                st = p.stat()
                dumps.append(
                    {
                        "name": p.name,
                        "bytes": st.st_size,
                        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
            except OSError:
                continue
    return {
        "has_traceback": bool(tb_tail.strip()),
        "traceback_tail": tb_tail,
        "dumps": dumps,
    }
