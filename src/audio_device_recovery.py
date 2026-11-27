"""WASAPI hot-swap auto-reconnect state guard and output device resolution.

Pure-Python domain module (zero Qt imports). Detects transient WASAPI endpoint
disconnects/stops and permits a single bounded automatic reconnect so plugging
or unplugging headphones resumes playback seamlessly without infinite loops.
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable, Mapping

import vwa_config
from vwa_paths import ROOT

_RECOVERABLE_KEYWORDS = (
    'disconnected',
    'stopped unexpectedly',
    'device stopped',
    'device_stop',
    'wasapi playback device',
    'endpoint',
    'audclnt_e_device_invalidated',
    'ma_device_start',
    'interrupted',
)


def is_recoverable_device_error(message: Any) -> bool:
    text = str(message or '').strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _RECOVERABLE_KEYWORDS)


class DeviceReconnectGuard:
    """Bounded 1-shot automatic reconnect controller with cooldown protection."""

    def __init__(
        self,
        *,
        max_attempts: int = 1,
        cooldown_seconds: float = 2.0,
        backoff_ms: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.backoff_ms = max(0, int(backoff_ms))
        self._clock = clock
        self._attempts = 0
        self._last_attempt_at = -1e9

    def should_attempt(self, message: Any) -> bool:
        if not is_recoverable_device_error(message):
            return False
        now = self._clock()
        if now - self._last_attempt_at > max(self.cooldown_seconds * 4.0, 10.0):
            self._attempts = 0
        if self._attempts >= self.max_attempts:
            return False
        if now - self._last_attempt_at < self.cooldown_seconds:
            return False
        return True

    def mark_attempt(self) -> int:
        self._attempts += 1
        self._last_attempt_at = self._clock()
        return self._attempts

    def reset(self) -> None:
        self._attempts = 0


def list_wasapi_devices(dll_path: str | Path | None = None, *, root: Path = ROOT) -> list[dict[str, Any]]:
    """Return available WASAPI playback endpoints, always starting with default option."""
    target = Path(dll_path) if dll_path else (Path(root).resolve() / 'components' / 'native-audio' / 'vwa_native_output.dll')
    devices: list[dict[str, Any]] = []
    if target.is_file():
        try:
            from mixdown_native_output import list_output_devices
            devices = list_output_devices(target)
        except (OSError, RuntimeError, ValueError):
            devices = []
    return devices


def resolve_device_index(
    preferred_name: str | None = None,
    *,
    devices: list[dict[str, Any]] | None = None,
    dll_path: str | Path | None = None,
    root: Path = ROOT,
) -> int:
    """Resolve configured audio_output_device name to a WASAPI index (-1 = system default)."""
    if preferred_name is None:
        cfg, _ = vwa_config.load_validated_config(root=root, persist_migration=False)
        preferred_name = str(cfg.get('audio_output_device') or '').strip()
    name = str(preferred_name or '').strip()
    if not name or name.lower() in ('default', 'auto', 'system'):
        return -1
    rows = list_wasapi_devices(dll_path, root=root) if devices is None else devices
    folded = name.casefold()
    for row in rows:
        if str(row.get('name') or '').strip().casefold() == folded:
            try:
                return int(row['index'])
            except (KeyError, TypeError, ValueError):
                return -1
    return -1
