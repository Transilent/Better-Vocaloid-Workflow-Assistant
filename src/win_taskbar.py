"""Windows taskbar progress (ITaskbarList3) and attention flash via stdlib ctypes.

Zero external dependencies, zero Qt imports, and guaranteed safe no-op on
non-Windows platforms, headless/offscreen runs, or when COM is unavailable.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from typing import Any, Mapping, Sequence

_LOG = logging.getLogger(__name__)

TBPF_NOPROGRESS = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_NORMAL = 0x2
TBPF_ERROR = 0x4
TBPF_PAUSED = 0x8

FLASHW_TRAY = 0x00000002
FLASHW_TIMERNOFG = 0x0000000C
CLSCTX_INPROC_SERVER = 0x1


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT),
        ('hwnd', wintypes.HWND),
        ('dwFlags', wintypes.DWORD),
        ('uCount', wintypes.UINT),
        ('dwTimeout', wintypes.DWORD),
    ]


_CLSID_TASKBAR_LIST = _GUID(
    0x56FDF344, 0xFD6D, 0x11D0,
    (ctypes.c_ubyte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
)
_IID_ITASKBAR_LIST3 = _GUID(
    0xEA1AFB91, 0x9E28, 0x4B86,
    (ctypes.c_ubyte * 8)(0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF),
)


def compute_taskbar_state(
    active_operations: Sequence[Mapping[str, Any]],
    *,
    has_recent_error: bool = False,
) -> tuple[str, int, int]:
    """Pure state reducer mapping active operations to (mode, completed, total)."""
    if active_operations:
        primary = active_operations[0]
        total = primary.get('total')
        done = primary.get('done')
        fraction = primary.get('fraction')
        if fraction is not None:
            try:
                pct = max(0, min(1000, int(round(float(fraction) * 1000.0))))
                return ('progress', pct, 1000)
            except (TypeError, ValueError):
                pass
        if total and done is not None:
            try:
                t_val = max(1, int(round(float(total))))
                d_val = max(0, min(t_val, int(round(float(done)))))
                return ('progress', d_val, t_val)
            except (TypeError, ValueError):
                pass
        return ('indeterminate', 0, 0)
    if has_recent_error:
        return ('error', 0, 0)
    return ('none', 0, 0)


class TaskbarProgress:
    """Thin ctypes wrapper around ITaskbarList3 for a single Win32 HWND."""

    def __init__(self, hwnd: int = 0, *, enabled: bool | None = None):
        self._hwnd = int(hwnd or 0)
        if enabled is None:
            enabled = (
                os.name == 'nt'
                and self._hwnd != 0
                and os.environ.get('VWA_TESTING') != '1'
                and os.environ.get('QT_QPA_PLATFORM', '').lower() != 'offscreen'
            )
        self._enabled = bool(enabled and os.name == 'nt' and self._hwnd != 0)
        self._ptr = ctypes.c_void_p()
        self._vtable = None
        self._last_state: tuple[str, int, int] = ('none', 0, 0)
        self._was_busy = False
        if self._enabled:
            self._init_com()

    @property
    def active(self) -> bool:
        return self._enabled and bool(self._ptr.value) and self._vtable is not None

    def _init_com(self) -> None:
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            hr = ole32.CoCreateInstance(
                ctypes.byref(_CLSID_TASKBAR_LIST),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(_IID_ITASKBAR_LIST3),
                ctypes.byref(self._ptr),
            )
            if hr != 0 or not self._ptr.value:
                self._enabled = False
                return
            vtable_ptr = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            self._vtable = vtable_ptr
            hr_init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtable_ptr[3])
            if hr_init(self._ptr) != 0:
                self.close()
        except (OSError, AttributeError, ValueError) as exc:
            _LOG.debug('ITaskbarList3 unavailable: %s', exc)
            self._enabled = False

    def _set_state(self, flags: int) -> None:
        if not self.active:
            return
        try:
            fn = ctypes.WINFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_int,
            )(self._vtable[10])
            fn(self._ptr, self._hwnd, int(flags))
        except (OSError, ValueError) as exc:
            _LOG.debug('SetProgressState ignored: %s', exc)

    def _set_value(self, completed: int, total: int) -> None:
        if not self.active:
            return
        try:
            fn = ctypes.WINFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong,
            )(self._vtable[9])
            fn(self._ptr, self._hwnd, max(0, int(completed)), max(1, int(total)))
        except (OSError, ValueError) as exc:
            _LOG.debug('SetProgressValue ignored: %s', exc)

    def set_progress(self, completed: int, total: int) -> None:
        self._last_state = ('progress', int(completed), int(total))
        self._set_state(TBPF_NORMAL)
        self._set_value(completed, total)

    def set_indeterminate(self) -> None:
        self._last_state = ('indeterminate', 0, 0)
        self._set_state(TBPF_INDETERMINATE)

    def set_error(self) -> None:
        self._last_state = ('error', 0, 0)
        self._set_state(TBPF_ERROR)

    def clear(self) -> None:
        self._last_state = ('none', 0, 0)
        self._set_state(TBPF_NOPROGRESS)

    def flash_attention(self, count: int = 2) -> None:
        if not self._enabled or not self._hwnd:
            return
        try:
            info = _FLASHWINFO(
                cbSize=ctypes.sizeof(_FLASHWINFO),
                hwnd=self._hwnd,
                dwFlags=FLASHW_TRAY | FLASHW_TIMERNOFG,
                uCount=max(1, int(count)),
                dwTimeout=0,
            )
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except (OSError, AttributeError) as exc:
            _LOG.debug('FlashWindowEx ignored: %s', exc)

    def sync_operations(self, active_operations: Sequence[Mapping[str, Any]], *, has_recent_error: bool = False) -> None:
        mode, done, total = compute_taskbar_state(active_operations, has_recent_error=has_recent_error)
        busy = mode in ('progress', 'indeterminate')
        if mode == 'progress':
            self.set_progress(done, total)
        elif mode == 'indeterminate':
            self.set_indeterminate()
        elif mode == 'error':
            self.set_error()
            if self._was_busy:
                self.flash_attention(2)
        else:
            self.clear()
            if self._was_busy:
                self.flash_attention(2)
        self._was_busy = busy

    def close(self) -> None:
        if self._ptr.value and self._vtable is not None:
            try:
                self.clear()
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(self._vtable[2])
                release(self._ptr)
            except (OSError, ValueError) as exc:
                _LOG.debug('ITaskbarList3 Release ignored: %s', exc)
        self._ptr = ctypes.c_void_p()
        self._vtable = None
        self._enabled = False
