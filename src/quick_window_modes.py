"""Qt 6 two-mode window policy with native maximize, restore and move."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, QSize, QTimer
from PySide6.QtGui import QWindow


WM_NCHITTEST = 0x0084
WM_SYSCOMMAND = 0x0112
SC_SIZE = 0xF000
HTBORDER = 18
SIZING_HITS = range(10, 18)

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1 = 19
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _hex_to_colorref(hex_color: str) -> int:
    text = str(hex_color or '').strip().lstrip('#')
    if len(text) != 6:
        return 0x0016100E
    r = int(text[0:2], 16)
    g = int(text[2:4], 16)
    b = int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_windows_titlebar_theme(window, dark: bool, caption_hex: str = '', text_hex: str = '', border_hex: str = ''):
    """Sync Windows 10/11 DWM native titlebar chrome with VWA's active dark/light surface."""
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return False
        dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)
        set_attr = dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        set_attr.restype = ctypes.c_long
        flag = ctypes.c_int(1 if dark else 0)
        hr = set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(flag), ctypes.sizeof(flag))
        if hr != 0:
            set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1, ctypes.byref(flag), ctypes.sizeof(flag))
        # Win11 22000+ supports exact caption, text, and window border tinting (ignored gracefully on Win10).
        cap_val = ctypes.c_uint32(_hex_to_colorref(caption_hex or ('#0e1016' if dark else '#f3f2f7')))
        set_attr(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(cap_val), ctypes.sizeof(cap_val))
        txt_val = ctypes.c_uint32(_hex_to_colorref(text_hex or ('#eeeef5' if dark else '#242233')))
        set_attr(hwnd, DWMWA_TEXT_COLOR, ctypes.byref(txt_val), ctypes.sizeof(txt_val))
        if border_hex:
            brd_val = ctypes.c_uint32(_hex_to_colorref(border_hex))
            set_attr(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(brd_val), ctypes.sizeof(brd_val))
        return True
    except (OSError, AttributeError, ValueError, RuntimeError):
        return False


def _disconnect(signal, slot):
    """Drop one signal connection without depending on sender lifetime."""
    try:
        signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass


class WindowsSizingFilter(QAbstractNativeEventFilter):
    """Keep the native frame non-resizable while leaving caption commands intact."""

    def __init__(self, window):
        super().__init__()
        self.hwnd = int(window.winId())
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.default_proc = self.user32.DefWindowProcW
        self.default_proc.argtypes = (wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM)
        self.default_proc.restype = ctypes.c_ssize_t
        self.blocked_size_commands = 0

    def detach(self):
        self.hwnd = 0

    def nativeEventFilter(self, event_type, message):
        if not self.hwnd or event_type != b'windows_generic_MSG':
            return False, 0
        event = wintypes.MSG.from_address(int(message))
        if event.hWnd != self.hwnd:
            return False, 0
        if event.message == WM_SYSCOMMAND and (event.wParam & 0xFFF0) == SC_SIZE:
            self.blocked_size_commands += 1
            return True, 0
        if event.message == WM_NCHITTEST:
            hit = self.default_proc(event.hWnd, event.message, event.wParam, event.lParam)
            if hit in SIZING_HITS:
                return True, HTBORDER
        return False, 0


class WindowModes(QObject):
    """Apply a screen-aware default size while native maximization stays available.

    ``preferred_size`` and all geometry from Qt use device-independent logical
    pixels. Qt resolves those through the active screen's DPR and system scale,
    and this object recomputes when that screen, its work area or DPI changes.
    ``fixture_viewport`` keeps an exact logical viewport for isolated UI checks.
    """

    def __init__(self, app, window, preferred_size, *, fixture_viewport=False):
        super().__init__(window)
        self.app = app
        self.window = window
        self.preferred_size = preferred_size
        self.fixture_viewport = fixture_viewport
        self.screen = None
        self._screen_signals = ()
        self._closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fit_normal_window)
        self._window_signals = (
            (window.screenChanged, self._screen_changed),
            (window.visibilityChanged, self.schedule_fit),
            (window.widthChanged, self.schedule_fit),
            (window.heightChanged, self.schedule_fit),
            (window.destroyed, self._window_destroyed),
        )
        for signal, slot in self._window_signals:
            signal.connect(slot)
        self.native_filter = None
        self._theme_tokens = None
        if app.platformName() == 'windows':
            self.native_filter = WindowsSizingFilter(window)
            app.installNativeEventFilter(self.native_filter)
            self._bind_titlebar_theme()
        self.set_screen(window.screen())

    def _bind_titlebar_theme(self):
        tokens = self.window.property('tokens')
        self._theme_tokens = tokens
        self._sync_titlebar_theme()
        if tokens is not None and hasattr(tokens, 'darkChanged'):
            tokens.darkChanged.connect(self._sync_titlebar_theme)

    def _sync_titlebar_theme(self, *_):
        if self._closed:
            return
        tokens = self._theme_tokens
        dark = bool(tokens.property('dark')) if tokens is not None else True
        cap = str(tokens.property('sidebar').name()) if (tokens is not None and tokens.property('sidebar')) else ''
        txt = str(tokens.property('text').name()) if (tokens is not None and tokens.property('text')) else ''
        brd = str(tokens.property('line').name()) if (tokens is not None and tokens.property('line')) else ''
        apply_windows_titlebar_theme(self.window, dark, cap, txt, brd)

    def close(self):
        """Release all native and signal connections; safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        _disconnect(self.timer.timeout, self.fit_normal_window)
        if self._theme_tokens is not None and hasattr(self._theme_tokens, 'darkChanged'):
            _disconnect(self._theme_tokens.darkChanged, self._sync_titlebar_theme)
        self._theme_tokens = None
        if self.native_filter is not None:
            native_filter = self.native_filter
            try:
                self.app.removeNativeEventFilter(native_filter)
            except RuntimeError:
                pass
            native_filter.detach()
            self.native_filter = None
        for signal, slot in self._window_signals:
            _disconnect(signal, slot)
        self._disconnect_screen()
        self.screen = None

    def _window_destroyed(self, *_):
        self.close()

    def _screen_changed(self, screen):
        self.set_screen(screen)

    def _connect_screen(self, screen):
        signals = ()
        if screen is not None:
            signals = (
                (screen.availableGeometryChanged, self.schedule_fit),
                (screen.logicalDotsPerInchChanged, self.schedule_fit),
            )
            for signal, slot in signals:
                signal.connect(slot)
        self._screen_signals = signals

    def _disconnect_screen(self):
        for signal, slot in self._screen_signals:
            _disconnect(signal, slot)
        self._screen_signals = ()

    def set_screen(self, screen):
        """Move sizing ownership to ``screen`` and drop every old-screen hook."""
        if self._closed:
            return
        if self.screen is screen:
            self.schedule_fit()
            return
        self._disconnect_screen()
        self.screen = screen
        self._connect_screen(screen)
        self.schedule_fit()

    def schedule_fit(self, *_):
        if not self._closed and self.window.visibility() == QWindow.Visibility.Windowed:
            self.timer.start(0)

    def fit_normal_window(self):
        """Clamp or restore the normal mode using the active logical work area."""
        if (self._closed or self.screen is None
                or self.window.visibility() != QWindow.Visibility.Windowed):
            return
        preferred = QSize(max(1, self.preferred_size.width()),
                          max(1, self.preferred_size.height()))
        if self.fixture_viewport:
            if self.window.size() != preferred:
                self.window.resize(preferred)
            return

        area = self.screen.availableGeometry()
        margins = self.window.frameMargins()
        available_width = area.width() - margins.left() - margins.right()
        available_height = area.height() - margins.top() - margins.bottom()
        horizontal_gap = 40 if available_width >= 1120 else 12
        vertical_gap = 42 if available_height >= 760 else 12
        fitted = QSize(
            max(1, min(preferred.width(), available_width - horizontal_gap)),
            max(1, min(preferred.height(), available_height - vertical_gap)),
        )
        if self.window.size() != fitted:
            self.window.resize(fitted)

        top_left = self.window.framePosition()
        outer_width = fitted.width() + margins.left() + margins.right()
        outer_height = fitted.height() + margins.top() + margins.bottom()
        x = min(max(top_left.x(), area.left()),
                area.x() + max(0, area.width() - outer_width))
        y = min(max(top_left.y(), area.top()),
                area.y() + max(0, area.height() - outer_height))
        if top_left != QPoint(x, y):
            self.window.setFramePosition(QPoint(x, y))


__all__ = (
    'WindowModes',
    'WindowsSizingFilter',
    'apply_windows_titlebar_theme',
    'WM_NCHITTEST',
    'WM_SYSCOMMAND',
    'SC_SIZE',
    'HTBORDER',
    'SIZING_HITS',
)