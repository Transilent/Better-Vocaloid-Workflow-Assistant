"""PySide6 signal adapter for the framework-neutral realtime transport."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from mixdown_realtime_core import (
    MediaStatus,
    PlaybackState,
    RealtimePreviewPlayerCore,
    native_output_path,
)


class QuickMixdownPlayer(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    stateChanged = Signal(int)
    mediaStatusChanged = Signal(int)
    sourceChanged = Signal(str)
    errorOccurred = Signal(str)
    prepared = Signal()
    parametersApplied = Signal(str)
    seekConfirmed = Signal()
    telemetryChanged = Signal(dict)

    def __init__(self, dll_path, *, cache_dir, parent=None, service_factory=None,
                 service_options=None):
        super().__init__(parent)
        self._core = RealtimePreviewPlayerCore(
            dll_path,
            cache_dir=cache_dir,
            service_factory=service_factory,
            service_options=service_options,
            event_sink=self._emit_core_event,
        )

    def __getattr__(self, name):
        core = self.__dict__.get('_core')
        if core is not None and hasattr(core, name):
            return getattr(core, name)
        raise AttributeError(f'{type(self).__name__!s} object has no attribute {name!r}')

    def _emit_core_event(self, name, *arguments):
        getattr(self, name).emit(*arguments)

    @property
    def ready(self):
        return self._core.ready

    @property
    def generation(self):
        return self._core.generation

    @property
    def play_intent(self):
        return self._core.play_intent

    @property
    def seek_pending(self):
        return self._core.seek_pending

    @property
    def is_alive(self):
        return self._core.is_alive

    @property
    def snapshot(self):
        return self._core.snapshot

    @property
    def available(self):
        return self._core.available

    @available.setter
    def available(self, value):
        self._core.available = bool(value)

    def set_play_intent(self, enabled):
        return self._core.set_play_intent(enabled)

    def open(self, request, *, position_ms=0, autoplay=False, key='after'):
        return self._core.open(request, position_ms=position_ms, autoplay=autoplay, key=key)

    def parameters(self, request, *, key='after'):
        return self._core.parameters(request, key=key)

    def seek(self, position_ms, *, autoplay=None):
        return self._core.seek(position_ms, autoplay=autoplay)

    def play(self):
        return self._core.play()

    def pause(self):
        return self._core.pause()

    def stop(self):
        return self._core.stop()

    def release(self):
        return self._core.release()

    def set_volume(self, volume):
        return self._core.set_volume(volume)

    def set_loop(self, enabled):
        return self._core.set_loop(enabled)

    def set_loop_range(self, start_ms, end_ms):
        return self._core.set_loop_range(start_ms, end_ms)

    def position(self):
        return self._core.position()

    def duration(self):
        return self._core.duration()

    def state(self):
        return self._core.state()

    def source(self):
        return self._core.source()

    def poll(self):
        return self._core.poll()

    def close(self, timeout=3.0):
        return self._core.close(timeout)


RealtimePreviewPlayer = QuickMixdownPlayer
