"""Dual QMediaPlayer playback with a short, non-blocking crossfade.

The public player is deliberately UI-framework-light: it needs only QtCore for
its object/signals/timers.  The real backend wraps Qt5 QMediaPlayer; tests can
inject two data-only adapters without touching an audio device.

Public methods must be called from the MixdownPlayer's QObject thread (normally
the GUI thread).  Adapter signals are connected with Qt.QueuedConnection, so
media callbacks and errors originating elsewhere are always handled on that
same thread.  No callback spins or pumps the event loop.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot

try:
    from PyQt5.QtGui import QGuiApplication
except Exception:  # pragma: no cover - the application always ships QtGui
    QGuiApplication = None

_QT_MULTIMEDIA_ATTEMPTED = False
_QtMediaContent = None
_QtMediaPlayer = None
_QT_MULTIMEDIA_ERROR = ''
_WINDOWS_MULTIMEDIA_PREFERENCE = 'windowsmediafoundation'


def _prefer_windows_media_foundation(platform_name=None):
    """Prefer WMF on Windows without replacing an explicit user choice.

    Qt treats this as a preference and may still fall back to another bundled
    media service when Windows Media Foundation is unavailable.
    """
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == 'nt':
        os.environ.setdefault(
            'QT_MULTIMEDIA_PREFERRED_PLUGINS',
            _WINDOWS_MULTIMEDIA_PREFERENCE)


# Set this before the local QtMultimedia import can create a media service.
_prefer_windows_media_foundation()


def _load_qt_multimedia():
    """Load the media DLLs only on the real, non-offscreen playback path."""
    global _QT_MULTIMEDIA_ATTEMPTED, _QtMediaContent, _QtMediaPlayer
    global _QT_MULTIMEDIA_ERROR
    _prefer_windows_media_foundation()
    if _QT_MULTIMEDIA_ATTEMPTED:
        return _QtMediaContent is not None and _QtMediaPlayer is not None
    _QT_MULTIMEDIA_ATTEMPTED = True
    try:
        from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    except Exception as error:
        _QT_MULTIMEDIA_ERROR = str(error) or type(error).__name__
        return False
    _QtMediaContent = QMediaContent
    _QtMediaPlayer = QMediaPlayer
    _QT_MULTIMEDIA_ERROR = ''
    return True


DEFAULT_VOLUME = 72
DEFAULT_CROSSFADE_MS = 80
# Qt's default 1000 ms position notification makes playback-driven meters
# visibly jump. This only changes UI timing; it does not rerender audio.
DEFAULT_POSITION_NOTIFY_MS = 50

__all__ = [
    'BaseMediaAdapter',
    'DEFAULT_CROSSFADE_MS',
    'DEFAULT_VOLUME',
    'MediaStatus',
    'MixdownPlayer',
    'PlaybackState',
    'QtMediaAdapter',
]


class PlaybackState:
    """Qt5 QMediaPlayer state values, kept stable without QtMultimedia."""

    StoppedState = 0
    PlayingState = 1
    PausedState = 2


class MediaStatus:
    """Qt 5.15 QMediaPlayer media-status values.

    The application's confirmed Qt5 build includes UnknownMediaStatus at 0;
    keeping the values here also makes offscreen fake tests independent of the
    multimedia DLL being importable.
    """

    UnknownMediaStatus = 0
    NoMedia = 1
    LoadingMedia = 2
    LoadedMedia = 3
    StalledMedia = 4
    BufferingMedia = 5
    BufferedMedia = 6
    EndOfMedia = 7
    InvalidMedia = 8


class BaseMediaAdapter(QObject):
    """Data/control interface implemented by Qt and fake media backends.

    Signals carry a request id as their last argument.  MixdownPlayer ignores a
    status whose id is no longer current, which makes cancellation and source
    replacement safe when an old decoder reports late.
    """

    mediaStatusChanged = pyqtSignal(int, int)
    positionChanged = pyqtSignal(int, int)
    durationChanged = pyqtSignal(int, int)
    stateChanged = pyqtSignal(int, int)
    failed = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._request_id = 0

    @property
    def source(self):
        return self._source

    @property
    def request_id(self):
        return self._request_id

    def has_media(self):
        return bool(self._source)

    def set_media(self, path, request_id):
        raise NotImplementedError

    def play(self):
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def set_position(self, position_ms):
        raise NotImplementedError

    def position(self):
        raise NotImplementedError

    def duration(self):
        raise NotImplementedError

    def state(self):
        raise NotImplementedError

    def set_volume(self, volume):
        raise NotImplementedError


class QtMediaAdapter(BaseMediaAdapter):
    """Thin Qt5 QMediaPlayer adapter.  One instance represents one player."""

    def __init__(self, parent=None):
        _load_qt_multimedia()
        if _QtMediaPlayer is None or _QtMediaContent is None:
            raise RuntimeError(
                'QtMultimedia is unavailable' +
                (': ' + _QT_MULTIMEDIA_ERROR if _QT_MULTIMEDIA_ERROR else ''))
        super().__init__(parent)
        self._player = _QtMediaPlayer(self)
        self._player.setNotifyInterval(DEFAULT_POSITION_NOTIFY_MS)
        self._player.mediaStatusChanged.connect(self._mirror_media_status)
        self._player.positionChanged.connect(self._mirror_position)
        self._player.durationChanged.connect(self._mirror_duration)
        self._player.stateChanged.connect(self._mirror_state)
        self._player.error.connect(self._mirror_error)

    def _mirror_media_status(self, status):
        self.mediaStatusChanged.emit(int(status), self._request_id)

    def _mirror_position(self, position):
        self.positionChanged.emit(max(0, int(position)), self._request_id)

    def _mirror_duration(self, duration):
        self.durationChanged.emit(max(0, int(duration)), self._request_id)

    def _mirror_state(self, state):
        self.stateChanged.emit(int(state), self._request_id)

    def _mirror_error(self, *_):
        message = ''
        try:
            message = self._player.errorString()
        except Exception:
            pass
        self.failed.emit(message or 'QtMultimedia playback failed', self._request_id)

    def set_media(self, path, request_id):
        self._request_id = int(request_id)
        self._source = str(path) if path else None
        if self._source:
            self._player.setMedia(_QtMediaContent(QUrl.fromLocalFile(self._source)))
        else:
            self._player.setMedia(_QtMediaContent())

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def stop(self):
        self._player.stop()

    def set_position(self, position_ms):
        self._player.setPosition(max(0, int(position_ms)))

    def position(self):
        return max(0, int(self._player.position()))

    def duration(self):
        return max(0, int(self._player.duration()))

    def state(self):
        return int(self._player.state())

    def set_volume(self, volume):
        self._player.setVolume(max(0, min(100, int(volume))))


def _offscreen_platform():
    requested = os.environ.get('QT_QPA_PLATFORM', '').split(';', 1)[0].strip().lower()
    if requested == 'offscreen':
        return True
    if QGuiApplication is None:
        return False
    app = QGuiApplication.instance()
    return bool(app is not None and QGuiApplication.platformName().lower() == 'offscreen')


def _default_adapter_factory(parent):
    if _offscreen_platform():
        raise RuntimeError('offscreen Qt platform uses the non-audio fallback')
    _load_qt_multimedia()
    if _QtMediaPlayer is None or _QtMediaContent is None:
        raise RuntimeError(
            'QtMultimedia is unavailable' +
            (': ' + _QT_MULTIMEDIA_ERROR if _QT_MULTIMEDIA_ERROR else ''))
    if QGuiApplication is None or QGuiApplication.instance() is None:
        raise RuntimeError('a QGuiApplication is required before audio playback')
    return QtMediaAdapter(parent)


def _source_path(path):
    if path is None:
        return None
    value = os.fspath(path)
    if not value:
        return None
    return os.path.abspath(os.path.expanduser(value))


def _volume(value):
    return max(0, min(100, int(round(value))))


def _smoothstep(value):
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


@dataclass
class _DirectLoad:
    index: int
    request_id: int
    path: str
    position_ms: int


@dataclass
class _Crossfade:
    active_index: int
    candidate_index: int
    request_id: int
    path: str
    position_ms: int
    duration_ms: int
    active_position_ms: int = 0
    started: bool = False
    started_at: Optional[float] = None
    paused_elapsed_ms: Optional[float] = None
    progress: float = 0.0


class MixdownPlayer(QObject):
    """Two-player façade with crossfade, loop and safe no-audio fallback.

    ``load`` is the ordinary single-player replacement path.  ``load_crossfade``
    loads into the inactive player, leaves the active player untouched until
    LoadedMedia, ramps both volumes, and only then stops the old player.
    """

    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(int)
    volumeChanged = pyqtSignal(int)
    loopChanged = pyqtSignal(bool)
    loaded = pyqtSignal(str)
    sourceChanged = pyqtSignal(str)
    crossfadeLoading = pyqtSignal(str)
    crossfadeStarted = pyqtSignal(str)
    crossfadeProgress = pyqtSignal(float)
    crossfadeFinished = pyqtSignal(str)
    crossfadeCancelled = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent=None, adapter_factory=None, clock=None,
                 volume=DEFAULT_VOLUME):
        super().__init__(parent)
        self._adapters = []
        self._available = False
        self._unavailable_reason = ''
        self._active_index = 0
        self._expected_request_ids = [0, 0]
        self._request_sequence = 0
        self._direct = None
        self._crossfade = None
        self._master_volume = _volume(volume)
        self._loop = False
        self._clock = clock or time.monotonic
        self._swapping = False
        self._last_position = None
        self._last_duration = None
        self._last_state = None
        self._last_source = None

        self._crossfade_timer = QTimer(self)
        self._crossfade_timer.setSingleShot(True)
        self._crossfade_timer.timeout.connect(self._on_crossfade_tick)
        self._loop_timer = QTimer(self)
        self._loop_timer.setSingleShot(True)
        self._loop_timer.setInterval(0)
        self._loop_timer.timeout.connect(self._restart_active_after_end)

        factory = adapter_factory or _default_adapter_factory
        try:
            for index in range(2):
                adapter = factory(self)
                if not isinstance(adapter, BaseMediaAdapter):
                    raise TypeError('adapter_factory must return BaseMediaAdapter instances')
                if adapter in self._adapters:
                    raise ValueError('adapter_factory must create two distinct adapters')
                self._adapters.append(adapter)
        except Exception as error:
            self._unavailable_reason = str(error) or type(error).__name__
            for adapter in self._adapters:
                try:
                    adapter.stop()
                    adapter.setParent(None)
                    adapter.deleteLater()
                except Exception:
                    pass
            self._adapters = []
            return

        self._available = True
        for adapter in self._adapters:
            adapter.mediaStatusChanged.connect(
                self._on_adapter_media_status, Qt.QueuedConnection)
            adapter.positionChanged.connect(
                self._on_adapter_position, Qt.QueuedConnection)
            adapter.durationChanged.connect(
                self._on_adapter_duration, Qt.QueuedConnection)
            adapter.stateChanged.connect(
                self._on_adapter_state, Qt.QueuedConnection)
            adapter.failed.connect(
                self._on_adapter_failed, Qt.QueuedConnection)
        self._adapters[0].set_volume(self._master_volume)
        self._adapters[1].set_volume(0)

    @property
    def available(self):
        return self._available

    @property
    def unavailable_reason(self):
        return self._unavailable_reason

    @property
    def transitioning(self):
        return self._crossfade is not None

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, enabled):
        self.set_loop(enabled)

    def _reject_unavailable(self, action):
        reason = self._unavailable_reason or 'audio backend is unavailable'
        self.errorOccurred.emit(action + ' unavailable: ' + reason)
        return False

    def _new_request_id(self):
        self._request_sequence += 1
        return self._request_sequence

    def _index_of(self, adapter):
        for index, candidate in enumerate(self._adapters):
            if candidate is adapter:
                return index
        return None

    def _sender_index(self):
        return self._index_of(self.sender())

    def _active_adapter(self):
        if not self._available:
            return None
        return self._adapters[self._active_index]

    def _standby_adapter(self):
        if not self._available:
            return None
        return self._adapters[1 - self._active_index]

    def _clear_adapter(self, index):
        adapter = self._adapters[index]
        try:
            adapter.stop()
        except Exception:
            pass
        request_id = self._new_request_id()
        self._expected_request_ids[index] = request_id
        adapter.set_media(None, request_id)

    def _set_adapter_volume(self, index, value):
        self._adapters[index].set_volume(_volume(value))

    def _publish_active_snapshot(self, force=False):
        if not self._available:
            return
        adapter = self._active_adapter()
        position = max(0, int(adapter.position()))
        duration = max(0, int(adapter.duration()))
        state = int(adapter.state())
        source = adapter.source or ''
        if source != self._last_source:
            self._last_source = source
            self.sourceChanged.emit(source)
        if force or position != self._last_position:
            self._last_position = position
            self.positionChanged.emit(position)
        if force or duration != self._last_duration:
            self._last_duration = duration
            self.durationChanged.emit(duration)
        if force or state != self._last_state:
            self._last_state = state
            self.stateChanged.emit(state)

    def load(self, path, position_ms=0):
        """Replace the current media immediately; playback starts via play()."""
        if not self._available:
            return self._reject_unavailable('load')
        source = _source_path(path)
        if not source:
            self.errorOccurred.emit('load requires a non-empty media path')
            return False
        self._cancel_crossfade()
        self._loop_timer.stop()
        self._direct = None
        try:
            self._clear_adapter(self._active_index)
            self._clear_adapter(1 - self._active_index)
            request_id = self._new_request_id()
            self._expected_request_ids[self._active_index] = request_id
            self._set_adapter_volume(self._active_index, self._master_volume)
            self._set_adapter_volume(1 - self._active_index, 0)
            self._direct = _DirectLoad(
                self._active_index, request_id, source, max(0, int(position_ms)))
            self._adapters[self._active_index].set_media(source, request_id)
            self._last_source = None
            self._publish_active_snapshot(force=True)
            return True
        except Exception as error:
            self._direct = None
            self.errorOccurred.emit('load failed: ' + (str(error) or type(error).__name__))
            return False

    def load_crossfade(self, path, position_ms, duration_ms=DEFAULT_CROSSFADE_MS):
        """Load a replacement while the active source keeps playing.

        The standby player is loaded and seeked first.  Only after its
        LoadedMedia callback starts an 80 ms (default) volume ramp; the old
        player is stopped at completion.  A replacement request cancels the
        previous candidate but never pauses/stops the active player.
        """
        if not self._available:
            return self._reject_unavailable('load_crossfade')
        source = _source_path(path)
        if not source:
            self.errorOccurred.emit('load_crossfade requires a non-empty media path')
            return False
        self._cancel_crossfade()
        self._loop_timer.stop()
        if self._direct is not None:
            # A direct load that never reached LoadedMedia is not audible; drop
            # it so its late status cannot position the new active source.
            direct_index = self._direct.index
            self._direct = None
            self._clear_adapter(direct_index)

        active_index = self._active_index
        candidate_index = 1 - active_index
        active = self._adapters[active_index]
        active_position = max(0, int(active.position())) if active.has_media() else 0
        candidate = self._adapters[candidate_index]
        target_duration = max(1, int(duration_ms))
        target_position = max(0, int(position_ms))
        request_id = self._new_request_id()
        self._expected_request_ids[candidate_index] = request_id
        try:
            candidate.stop()
            self._set_adapter_volume(candidate_index, 0)
            candidate.set_media(source, request_id)
        except Exception as error:
            self._set_adapter_volume(active_index, self._master_volume)
            self.errorOccurred.emit(
                'load_crossfade could not load standby: ' +
                (str(error) or type(error).__name__))
            return False
        self._crossfade = _Crossfade(
            active_index=active_index,
            candidate_index=candidate_index,
            request_id=request_id,
            path=source,
            position_ms=target_position,
            duration_ms=target_duration,
            active_position_ms=active_position,
        )
        self.crossfadeLoading.emit(source)
        return True

    def play(self):
        if not self._available:
            return self._reject_unavailable('play')
        transition = self._crossfade
        try:
            if transition is not None and transition.started:
                if transition.paused_elapsed_ms is not None:
                    transition.started_at = (
                        self._clock() - transition.paused_elapsed_ms / 1000.0)
                    transition.paused_elapsed_ms = None
                self._adapters[transition.active_index].play()
                self._adapters[transition.candidate_index].play()
                self._schedule_crossfade_tick()
                self._publish_active_snapshot(force=True)
                return True
            adapter = self._active_adapter()
            if adapter.has_media():
                adapter.play()
                self._publish_active_snapshot(force=True)
                return True
            return False
        except Exception as error:
            self.errorOccurred.emit('play failed: ' + (str(error) or type(error).__name__))
            return False

    def pause(self):
        if not self._available:
            return self._reject_unavailable('pause')
        try:
            transition = self._crossfade
            if transition is not None and not transition.started:
                # Pause during loading is an explicit stop intent. Discard the
                # in-flight candidate so a late LoadedMedia cannot play it or
                # commit it over the source the user paused.
                self._cancel_crossfade()
                adapter = self._active_adapter()
                if adapter.has_media():
                    adapter.pause()
            elif transition is not None and transition.started:
                elapsed = min(
                    float(transition.duration_ms),
                    self._elapsed_crossfade_ms(transition))
                self._crossfade_timer.stop()
                self._apply_crossfade_progress(
                    elapsed / float(max(1, transition.duration_ms)))
                if elapsed >= transition.duration_ms:
                    self._finish_crossfade(transition)
                    adapter = self._active_adapter()
                    if adapter.has_media():
                        adapter.pause()
                else:
                    transition.paused_elapsed_ms = elapsed
                    self._adapters[transition.active_index].pause()
                    self._adapters[transition.candidate_index].pause()
            else:
                adapter = self._active_adapter()
                if adapter.has_media():
                    adapter.pause()
            self._publish_active_snapshot(force=True)
            return True
        except Exception as error:
            self.errorOccurred.emit('pause failed: ' + (str(error) or type(error).__name__))
            return False

    def seek(self, position_ms):
        if not self._available:
            return self._reject_unavailable('seek')
        try:
            # A user seek must not later be undone by a candidate that was
            # loaded at the old position. Keep the active player and abort it.
            if self._crossfade is not None:
                self._cancel_crossfade()
            adapter = self._active_adapter()
            if not adapter.has_media():
                return False
            position = max(0, int(position_ms))
            duration = max(0, int(adapter.duration()))
            if duration:
                position = min(position, duration)
            adapter.set_position(position)
            self._publish_active_snapshot(force=True)
            return True
        except Exception as error:
            self.errorOccurred.emit('seek failed: ' + (str(error) or type(error).__name__))
            return False

    def stop(self):
        if not self._available:
            self._reject_unavailable('stop')
            return False
        had_crossfade = self._crossfade
        cancelled_path = had_crossfade.path if had_crossfade else ''
        self._crossfade_timer.stop()
        self._loop_timer.stop()
        self._crossfade = None
        self._direct = None
        try:
            for index in range(2):
                self._clear_adapter(index)
                self._set_adapter_volume(index, self._master_volume if index == 0 else 0)
            self._active_index = 0
            if cancelled_path:
                self.crossfadeCancelled.emit(cancelled_path)
            self._last_position = None
            self._last_duration = None
            self._last_state = None
            self._last_source = None
            self._publish_active_snapshot(force=True)
            self.mediaStatusChanged.emit(MediaStatus.NoMedia)
            return True
        except Exception as error:
            self.errorOccurred.emit('stop failed: ' + (str(error) or type(error).__name__))
            return False

    def position(self):
        adapter = self._active_adapter()
        if adapter is None or not adapter.has_media():
            return 0
        try:
            return max(0, int(adapter.position()))
        except Exception:
            return 0

    def duration(self):
        adapter = self._active_adapter()
        if adapter is None or not adapter.has_media():
            return 0
        try:
            return max(0, int(adapter.duration()))
        except Exception:
            return 0

    def state(self):
        adapter = self._active_adapter()
        if adapter is None:
            return PlaybackState.StoppedState
        try:
            return int(adapter.state())
        except Exception:
            return PlaybackState.StoppedState

    def source(self):
        adapter = self._active_adapter()
        if adapter is None:
            return ''
        return adapter.source or ''

    def volume(self, value=None):
        if value is None:
            return self._master_volume
        return self.set_volume(value)

    def set_volume(self, value):
        new_volume = _volume(value)
        if new_volume == self._master_volume:
            return new_volume
        self._master_volume = new_volume
        if self._available:
            transition = self._crossfade
            if transition is not None and transition.started:
                self._apply_crossfade_progress(transition.progress)
            else:
                self._set_adapter_volume(self._active_index,
                                         self._master_volume)
                self._set_adapter_volume(1 - self._active_index, 0)
        self.volumeChanged.emit(new_volume)
        return new_volume

    def set_loop(self, enabled):
        enabled = bool(enabled)
        if enabled == self._loop:
            return enabled
        self._loop = enabled
        if not enabled:
            self._loop_timer.stop()
        self.loopChanged.emit(enabled)
        return enabled

    def _cancel_crossfade(self, restore_volume=True):
        transition = self._crossfade
        if transition is None:
            return False
        self._crossfade_timer.stop()
        self._loop_timer.stop()
        self._crossfade = None
        if restore_volume:
            try:
                self._set_adapter_volume(transition.active_index,
                                         self._master_volume)
            except Exception:
                pass
        try:
            self._clear_adapter(transition.candidate_index)
        except Exception as error:
            self.errorOccurred.emit(
                'crossfade cleanup failed: ' +
                (str(error) or type(error).__name__))
        self.crossfadeCancelled.emit(transition.path)
        return True

    def _elapsed_crossfade_ms(self, transition):
        if transition.paused_elapsed_ms is not None:
            return transition.paused_elapsed_ms
        if transition.started_at is None:
            return 0.0
        return max(0.0, (self._clock() - transition.started_at) * 1000.0)

    def _apply_crossfade_progress(self, progress):
        transition = self._crossfade
        if transition is None:
            return
        transition.progress = max(0.0, min(1.0, float(progress)))
        eased = _smoothstep(transition.progress)
        active_volume = _volume(self._master_volume * (1.0 - eased))
        candidate_volume = _volume(self._master_volume * eased)
        self._set_adapter_volume(transition.active_index, active_volume)
        self._set_adapter_volume(transition.candidate_index, candidate_volume)
        self.crossfadeProgress.emit(transition.progress)

    def _schedule_crossfade_tick(self):
        transition = self._crossfade
        if (transition is None or not transition.started or
                transition.paused_elapsed_ms is not None or
                self._crossfade_timer.isActive()):
            return
        interval = max(1, min(16, transition.duration_ms // 8 or 1))
        self._crossfade_timer.start(interval)

    @pyqtSlot()
    def _on_crossfade_tick(self):
        self._advance_crossfade()

    def _advance_crossfade(self):
        transition = self._crossfade
        if (transition is None or not transition.started or
                transition.paused_elapsed_ms is not None):
            return
        elapsed = self._elapsed_crossfade_ms(transition)
        progress = min(1.0, elapsed / float(max(1, transition.duration_ms)))
        self._apply_crossfade_progress(progress)
        if progress >= 1.0:
            self._finish_crossfade(transition)
        else:
            self._schedule_crossfade_tick()

    def _begin_candidate(self, transition):
        self._loop_timer.stop()
        candidate = self._adapters[transition.candidate_index]
        active = self._adapters[transition.active_index]
        old_playing = bool(
            active.has_media() and active.state() == PlaybackState.PlayingState)
        self._set_adapter_volume(transition.candidate_index, 0)
        candidate.play()
        self.crossfadeStarted.emit(transition.path)
        if not old_playing:
            self._set_adapter_volume(transition.candidate_index,
                                     self._master_volume)
            self._finish_crossfade(transition)
            return
        transition.started = True
        transition.started_at = self._clock()
        transition.paused_elapsed_ms = None
        self._apply_crossfade_progress(0.0)
        self._schedule_crossfade_tick()

    def _finish_crossfade(self, transition):
        if self._crossfade is not transition:
            return
        self._crossfade_timer.stop()
        self._loop_timer.stop()
        active = self._adapters[transition.active_index]
        try:
            self._set_adapter_volume(transition.candidate_index,
                                     self._master_volume)
            self._set_adapter_volume(transition.active_index, 0)
            # Requirement order: stop the old source, then exchange roles.
            self._swapping = True
            try:
                # _clear_adapter stops exactly once, then clears stale media.
                self._clear_adapter(transition.active_index)
            finally:
                self._swapping = False
            self._crossfade = None
            self._direct = None
            self._active_index = transition.candidate_index
            self._last_source = None
            self._publish_active_snapshot(force=True)
            self.crossfadeProgress.emit(1.0)
            self.crossfadeFinished.emit(transition.path)
        except Exception as error:
            self._crossfade = None
            self.errorOccurred.emit(
                'crossfade finish failed: ' +
                (str(error) or type(error).__name__))

    @pyqtSlot(int, int)
    def _on_adapter_media_status(self, status, request_id):
        index = self._sender_index()
        if index is None or request_id != self._expected_request_ids[index]:
            return
        status = int(status)
        transition = self._crossfade
        if index != self._active_index and not (
                transition is not None and index == transition.candidate_index):
            return
        candidate_loaded = False
        loaded_path = self._adapters[index].source or ''
        if status == MediaStatus.LoadedMedia:
            if transition is not None and index == transition.candidate_index:
                candidate = self._adapters[index]
                position = transition.position_ms
                active = self._adapters[transition.active_index]
                if active.has_media() and active.state() == PlaybackState.PlayingState:
                    # The old player advances while the new media loads. Seek
                    # to the same song position at the moment the fade starts.
                    elapsed = int(active.position()) - transition.active_position_ms
                    if elapsed < 0 and self._loop:
                        elapsed += max(0, int(active.duration()))
                    position = max(0, position + elapsed)
                duration = max(0, int(candidate.duration()))
                if duration:
                    if self._loop:
                        # A loading interval may cross the old source's loop
                        # boundary. Preserve that elapsed time by wrapping in
                        # the candidate, even when its duration differs.
                        position %= duration
                    else:
                        position = min(position, duration)
                candidate.set_position(position)
                candidate_loaded = True
                loaded_path = transition.path
            elif self._direct is not None and index == self._direct.index:
                direct = self._direct
                position = direct.position_ms
                active = self._adapters[index]
                duration = max(0, int(active.duration()))
                if duration:
                    position = min(position, duration)
                active.set_position(position)
                loaded_path = direct.path
                self._direct = None
        if status == MediaStatus.LoadedMedia and loaded_path:
            self.loaded.emit(loaded_path)
        if index == self._active_index:
            self.mediaStatusChanged.emit(status)
        if candidate_loaded:
            self._begin_candidate(transition)
            return
        if status == MediaStatus.EndOfMedia:
            if index == self._active_index:
                if self._loop and self._adapters[index].has_media():
                    if not self._loop_timer.isActive():
                        self._loop_timer.start(0)
            elif transition is not None and index == transition.candidate_index:
                if self._loop and self._adapters[index].has_media():
                    self._adapters[index].set_position(0)
                    self._adapters[index].play()
                else:
                    self._cancel_crossfade(restore_volume=True)
                    self.errorOccurred.emit(
                        'crossfade candidate reached EndOfMedia before switching')
        elif status == MediaStatus.InvalidMedia:
            if transition is not None and index == transition.candidate_index:
                self._cancel_crossfade(restore_volume=True)
                self.errorOccurred.emit(
                    'crossfade candidate is invalid; active audio was preserved')
            elif index == self._active_index:
                self._direct = None
                self.errorOccurred.emit('active media is invalid')

    @pyqtSlot(int, int)
    def _on_adapter_position(self, position, request_id):
        index = self._sender_index()
        if index is None or request_id != self._expected_request_ids[index]:
            return
        if index != self._active_index or self._swapping:
            return
        position = max(0, int(position))
        if position != self._last_position:
            self._last_position = position
            self.positionChanged.emit(position)

    @pyqtSlot(int, int)
    def _on_adapter_duration(self, duration, request_id):
        index = self._sender_index()
        if index is None or request_id != self._expected_request_ids[index]:
            return
        if index != self._active_index or self._swapping:
            return
        duration = max(0, int(duration))
        if duration != self._last_duration:
            self._last_duration = duration
            self.durationChanged.emit(duration)

    @pyqtSlot(int, int)
    def _on_adapter_state(self, state, request_id):
        index = self._sender_index()
        if index is None or request_id != self._expected_request_ids[index]:
            return
        if index != self._active_index or self._swapping:
            return
        state = int(state)
        if state != self._last_state:
            self._last_state = state
            self.stateChanged.emit(state)

    @pyqtSlot(str, int)
    def _on_adapter_failed(self, message, request_id):
        index = self._sender_index()
        if index is None or request_id != self._expected_request_ids[index]:
            return
        transition = self._crossfade
        if transition is not None and index == transition.candidate_index:
            self._cancel_crossfade(restore_volume=True)
            message = str(message) + '; active audio was preserved'
        self.errorOccurred.emit(str(message) or 'media playback failed')

    @pyqtSlot()
    def _restart_active_after_end(self):
        transition = self._crossfade
        if (not self._available or not self._loop or
                (transition is not None and transition.started)):
            return
        # Keep the old source audible across a loop boundary while a candidate
        # is still loading. Once the fade starts, that transition owns the pair.
        adapter = self._active_adapter()
        if adapter is None or not adapter.has_media():
            return
        try:
            adapter.set_position(0)
            adapter.play()
            self._publish_active_snapshot(force=True)
        except Exception as error:
            self.errorOccurred.emit(
                'loop restart failed: ' + (str(error) or type(error).__name__))
