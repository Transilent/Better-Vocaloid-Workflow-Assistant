"""Single-output, sample-aligned player for rendered mixdown WAV segments.

Public methods belong to this QObject's thread. The audio pull callback only
copies preloaded immutable PCM; it never performs file I/O or emits signals.
See ``start_crossfade`` for the optional early, exact-boundary contract.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
import threading
import time
import weakref

try:
    import numpy as np
    import soundfile as sf
except ImportError as _dependency_error:  # Make fallback selectable by the caller.
    np = sf = None
    _DEPENDENCY_ERROR = str(_dependency_error)
else:
    _DEPENDENCY_ERROR = ''
from PyQt5.QtCore import QIODevice, QObject, QThread, QTimer, Qt, pyqtSignal


DEFAULT_VOLUME = 72
DEFAULT_CROSSFADE_MS = 80
DEFAULT_POSITION_NOTIFY_MS = 20
_IDLE_STALL_SECONDS = 1.5
_FRAME_BYTES = 8                 # little-endian float32 stereo
_RATES = (8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 192000)


class PlaybackState:
    StoppedState = 0
    PlayingState = 1
    PausedState = 2


class MediaStatus:
    UnknownMediaStatus = 0
    NoMedia = 1
    LoadingMedia = 2
    LoadedMedia = 3
    StalledMedia = 4
    BufferingMedia = 5
    BufferedMedia = 6
    EndOfMedia = 7
    InvalidMedia = 8


def _path(value):
    if value is None or not os.fspath(value):
        raise ValueError('a non-empty media path is required')
    return os.path.abspath(os.path.expanduser(os.fspath(value)))


def _frames(milliseconds, rate):
    return max(0, int(round(int(milliseconds) * rate / 1000.0)))


def _milliseconds(frames, rate):
    return max(0, int(frames) * 1000 // rate)


def _read_wav(path):
    return sf.read(path, dtype='float32', always_2d=True)


@dataclass(frozen=True)
class _Clip:
    path: str
    rate: int
    samples: np.ndarray
    pcm: bytes
    origin: int = 0

    @property
    def frames(self):
        return len(self.samples)

    @property
    def end(self):
        return self.origin + self.frames

    def placed(self, origin):
        return _Clip(self.path, self.rate, self.samples, self.pcm, int(origin))


def _decode(path, reader):
    samples, rate = reader(path)
    rate = int(rate)
    samples = np.asarray(samples)
    if rate not in _RATES:
        raise ValueError('unsupported sample rate: %s Hz' % rate)
    if samples.ndim != 2 or samples.shape[1] not in (1, 2):
        raise ValueError('unsupported channel count: expected mono or stereo')
    if not len(samples):
        raise ValueError('empty audio')
    if samples.shape[1] == 1:
        # Use the same mono signal on both output channels; WAV export and the
        # measured source remain untouched.
        samples = np.repeat(samples, 2, axis=1)
    samples = np.ascontiguousarray(samples, dtype='<f4')
    if not np.isfinite(samples).all():
        raise ValueError('audio contains non-finite samples')
    samples.setflags(write=False)
    return _Clip(path, rate, samples, samples.tobytes())


@dataclass
class _Transition:
    clip: _Clip
    path: str
    duration_ms: int
    requested_position_ms: int
    request_frame: int
    auto: bool
    candidate_origin: object = None
    ready: bool = False
    plan: object = None
    started: bool = False


@dataclass(frozen=True)
class _Plan:
    old: _Clip
    new: _Clip
    boundary: int
    fade_frames: int
    fade_pcm: bytes

    @property
    def end(self):
        return self.boundary + self.fade_frames


def _make_plan(old, candidate, boundary, candidate_offset, fade_frames):
    """Precompute the overlap, using the same absolute frame in both clips."""
    boundary, candidate_offset, fade_frames = map(
        int, (boundary, candidate_offset, fade_frames))
    if fade_frames < 1 or boundary < old.origin or candidate_offset < 0:
        raise ValueError('invalid transition position')
    old_offset = boundary - old.origin
    if (old_offset + fade_frames > old.frames or
            candidate_offset + fade_frames > candidate.frames):
        raise ValueError('insufficient overlap for the requested switch and fade')
    new = candidate.placed(boundary - candidate_offset)
    t = np.arange(fade_frames, dtype=np.float64) / fade_frames
    weight = (t * t * (3.0 - 2.0 * t)).astype(np.float32)[:, None]
    a = old.samples[old_offset:old_offset + fade_frames]
    b = new.samples[candidate_offset:candidate_offset + fade_frames]
    faded = np.ascontiguousarray(a * (np.float32(1.0) - weight) + b * weight,
                                 dtype='<f4')
    return _Plan(old, new, boundary, fade_frames, faded.tobytes())


class TimelineIODevice(QIODevice):
    """Sequential byte source with an atomically replaceable future plan.

    The lock covers every read and mutation, including final close. No GUI,
    backend, filesystem, signal, or costly fade computation runs under it.
    Non-frame-aligned reads are supported without losing bytes.
    """
    def __init__(self, clip, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._clip = clip
        self._plan = None
        self._cursor = clip.origin * _FRAME_BYTES
        self._closed = False
        self.last_error = ''
        self.read_calls = 0
        self.reader_threads = set()
        if not self.open(QIODevice.ReadOnly | QIODevice.Unbuffered):
            raise RuntimeError('could not open audio stream')

    def isSequential(self):
        return True

    def _end_byte(self):
        return (self._plan.new.end if self._plan else self._clip.end) * _FRAME_BYTES

    def bytesAvailable(self):
        with self._lock:
            remaining = 0 if self._closed else max(0, self._end_byte() - self._cursor)
        return remaining + super().bytesAvailable()

    def cursor_bytes(self):
        with self._lock:
            return self._cursor

    def set_position(self, frame):
        with self._lock:
            self._cursor = int(frame) * _FRAME_BYTES

    def install(self, plan):
        with self._lock:
            if self._closed or self._plan is not None or self._clip is not plan.old:
                return False
            if self._cursor > plan.boundary * _FRAME_BYTES:
                return False
            self._plan = plan
            return True

    def commit(self, plan):
        with self._lock:
            if self._plan is not plan:
                return False
            self._clip = plan.new
            self._plan = None
            return True

    def discard(self, plan):
        with self._lock:
            if self._plan is not plan:
                return False
            self._plan = None
            return True

    def discard_if_unread(self, plan):
        """Check the read head and discard under one lock, without a race."""
        with self._lock:
            if self._plan is not plan or self._cursor > plan.boundary * _FRAME_BYTES:
                return False
            self._plan = None
            return True

    def finish_close(self):
        with self._lock:
            self._closed = True
        super().close()

    @staticmethod
    def _clip_bytes(clip, begin, end):
        start = begin - clip.origin * _FRAME_BYTES
        return clip.pcm[start:start + (end - begin)]

    def readData(self, maxlen):
        try:
            with self._lock:
                if self._closed or maxlen <= 0:
                    return b''
                begin = self._cursor
                end = min(begin + int(maxlen), self._end_byte())
                if end <= begin:
                    return b''
                plan = self._plan
                if plan is None:
                    result = self._clip_bytes(self._clip, begin, end)
                else:
                    parts = []
                    boundary = plan.boundary * _FRAME_BYTES
                    fade_end = plan.end * _FRAME_BYTES
                    if begin < boundary:
                        part_end = min(end, boundary)
                        parts.append(self._clip_bytes(plan.old, begin, part_end))
                        begin = part_end
                    if begin < end and begin < fade_end:
                        part_end = min(end, fade_end)
                        parts.append(plan.fade_pcm[begin - boundary:part_end - boundary])
                        begin = part_end
                    if begin < end:
                        parts.append(self._clip_bytes(plan.new, begin, end))
                    result = b''.join(parts)
                if len(result) != end - self._cursor:
                    raise RuntimeError('short timeline read')
                self._cursor = end
                self.read_calls += 1
                self.reader_threads.add(threading.get_ident())
                return result
        except Exception as error:
            with self._lock:
                self.last_error = '%s: %s' % (type(error).__name__, error)
            return b''

    def writeData(self, data):
        return -1


class _QtOutputFactory:
    def __init__(self):
        from PyQt5.QtMultimedia import QAudioDeviceInfo, QAudioFormat, QAudioOutput
        self._format_type = QAudioFormat
        self._output_type = QAudioOutput
        self.device = QAudioDeviceInfo.defaultOutputDevice()
        if self.device.isNull():
            raise RuntimeError('no default audio output device')
        if not any(self.supports(rate) for rate in _RATES):
            raise RuntimeError('device does not support a standard float32 stereo sample rate')

    def _format(self, rate):
        cls = self._format_type
        fmt = cls()
        fmt.setCodec('audio/pcm')
        fmt.setSampleRate(rate)
        fmt.setChannelCount(2)
        fmt.setSampleSize(32)
        fmt.setSampleType(cls.Float)
        fmt.setByteOrder(cls.LittleEndian)
        return fmt

    def supports(self, rate):
        return bool(self.device.isFormatSupported(self._format(rate)))

    def create(self, rate, parent):
        return self._output_type(self.device, self._format(rate), parent)


class MixdownStreamPlayer(QObject):
    """MixdownPlayer-compatible facade around one output and one pull source.

    ``output_factory`` is injectable and supplies supports(rate) and
    create(rate, parent), returning a QAudioOutput-compatible object. ``reader``
    is injectable for tests; the default decodes WAV on a worker thread.
    Signals and public methods always run on the object's Qt thread.
    """
    supports_sample_aligned_boundary = True
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(int)
    volumeChanged = pyqtSignal(int)
    loopChanged = pyqtSignal(bool)
    loaded = pyqtSignal(str)
    sourceChanged = pyqtSignal(str)
    crossfadeLoading = pyqtSignal(str)
    crossfadeReady = pyqtSignal(str)
    crossfadeStarted = pyqtSignal(str)
    crossfadeProgress = pyqtSignal(float)
    crossfadeFinished = pyqtSignal(str)
    crossfadeCancelled = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    _decoded = pyqtSignal(int, object, str)

    def __init__(self, parent=None, *, output_factory=None, reader=None,
                 clock=None, volume=DEFAULT_VOLUME):
        super().__init__(parent)
        self._reader = reader or _read_wav
        self._clock = clock or time.monotonic
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='vwa-audio-read')
        self._decoded.connect(self._on_decoded, Qt.QueuedConnection)
        self._factory = None
        self._available = False
        self._unavailable_reason = ''
        self._closed = False
        self._generation = 0
        self._future = None
        self._direct = None
        self._active = None
        self._transition = None
        self._output = None
        self._stream = None
        self._state = PlaybackState.StoppedState
        self._master_volume = max(0, min(100, int(round(volume))))
        self._loop = False
        self._play_intent = False
        self._phase_frame = 0
        self._frozen_frame = 0
        self._last_logical_frame = 0
        self._wall_active = 0.0
        self._wall_started = None
        self._idle_since = None
        self._idle_frame = None
        self._needs_start = False
        self._last_position = None
        self._last_duration = None
        self._last_source = None
        self._last_state = None
        self._timer = QTimer(self)
        self._timer.setInterval(DEFAULT_POSITION_NOTIFY_MS)
        self._timer.timeout.connect(self._tick)
        try:
            if np is None or sf is None:
                raise RuntimeError('NumPy/SoundFile unavailable: ' + _DEPENDENCY_ERROR)
            self._factory = output_factory if output_factory is not None else _QtOutputFactory()
            if not any(self._factory.supports(rate) for rate in _RATES):
                raise RuntimeError('device does not support float32 stereo at 44100/48000 Hz')
            self._available = True
        except Exception as error:
            self._unavailable_reason = str(error) or type(error).__name__

    @property
    def available(self):
        return self._available and not self._closed

    @property
    def unavailable_reason(self):
        return 'player is closed' if self._closed else self._unavailable_reason

    @property
    def transitioning(self):
        return self._transition is not None

    @property
    def sample_rate(self):
        """Active source/output rate, or zero until the direct load completes."""
        return self._active.rate if self._active is not None else 0

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value):
        self.set_loop(value)

    def supports_path(self, path):
        """Read WAV metadata only and check the current device; never emit.

        This is a preflight, not a guarantee that decoding or later device
        acquisition will succeed. Call on the player's QObject thread.
        """
        if not self.available:
            return False
        try:
            info = sf.info(_path(path))
            rate = int(info.samplerate)
            return bool(info.format in ('WAV', 'WAVEX', 'RF64') and
                        int(info.frames) > 0 and int(info.channels) in (1, 2) and
                        rate in _RATES and self._factory.supports(rate))
        except Exception:
            return False

    def _owner(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError('player methods must run on the QObject thread')

    def _reject(self, operation):
        self.errorOccurred.emit(operation + ' unavailable: ' +
                                (self.unavailable_reason or 'audio backend unavailable'))
        return False

    def _publish(self, force=False):
        generation = self._generation
        values = ((self.source(), '_last_source', self.sourceChanged),
                  (self.duration(), '_last_duration', self.durationChanged),
                  (self.state(), '_last_state', self.stateChanged),
                  (self.position(), '_last_position', self.positionChanged))
        for value, attribute, signal in values:
            if force or getattr(self, attribute) != value:
                setattr(self, attribute, value)
                signal.emit(value)
                if generation != self._generation:
                    return

    def _new_generation(self):
        self._generation += 1
        pending, self._future = self._future, None
        if pending is not None:
            pending.cancel()  # Running reads finish, but queued stale work need not start.
        return self._generation

    def _submit(self, path, generation):
        reader = self._reader
        ref = weakref.ref(self)
        future = self._executor.submit(_decode, path, reader)
        self._future = future

        def finished(result):
            try:
                value, error = result.result(), ''
            except Exception as exc:
                value, error = None, str(exc) or type(exc).__name__
            obj = ref()
            if obj is not None:
                try:
                    obj._decoded.emit(generation, value, error)
                except RuntimeError:
                    pass  # The QObject has already been destroyed.
        future.add_done_callback(finished)

    def _close_device(self):
        self._timer.stop()
        output, stream = self._output, self._stream
        if output is not None:
            output.reset()  # Do not hold the stream lock across backend calls.
        if stream is not None:
            stream.finish_close()
        if output is not None:
            output.deleteLater()
        if stream is not None:
            stream.deleteLater()
        self._output = self._stream = None
        self._wall_started = None

    def _new_device(self, clip):
        if not self._factory.supports(clip.rate):
            raise ValueError('output device does not support %s Hz float32 stereo' % clip.rate)
        # Construct output before stream: the stream must outlive the output.
        output = self._factory.create(clip.rate, self)
        try:
            output.setVolume(self._master_volume / 100.0)
            stream = TimelineIODevice(clip, self)
        except Exception:
            output.deleteLater()
            raise
        self._output, self._stream = output, stream

    def load(self, path, position_ms=0):
        self._owner()
        if not self.available:
            return self._reject('load')
        try:
            path = _path(path)
            position_ms = max(0, int(position_ms))
            self._cancel_transition(reset_if_buffered=False)
            generation = self._new_generation()
            self._close_device()
            self._active = None
            self._play_intent = False
            self._state = PlaybackState.StoppedState
            self._direct = (path, position_ms)
            self._publish(force=True)
            # Signal handlers may load or stop another source synchronously.
            # Never let this superseded request submit against the newer epoch.
            if generation != self._generation:
                return False
            self.mediaStatusChanged.emit(MediaStatus.LoadingMedia)
            if generation != self._generation:
                return False
            self._submit(path, generation)
            return True
        except Exception as error:
            self._direct = None
            self.errorOccurred.emit('load failed: ' + str(error))
            return False

    def _queue(self, path, position_ms, duration_ms, auto, candidate_origin_frame=None):
        self._owner()
        if not self.available:
            return self._reject('crossfade')
        if self._active is None:
            self.errorOccurred.emit('crossfade requires loaded active media')
            return False
        try:
            path = _path(path)
            position_ms = max(0, int(position_ms))
            duration_ms = max(1, int(duration_ms))
            if self._transition is not None and not self.cancel_crossfade():
                return False
            generation = self._new_generation()
            candidate_origin = (None if candidate_origin_frame is None else
                                self._active.origin + int(candidate_origin_frame))
            self._transition = _Transition(None, path, duration_ms, position_ms,
                                           self._logical_frame(), auto, candidate_origin)
            self.crossfadeLoading.emit(path)
            if self._transition is None or generation != self._generation:
                return False
            self._submit(path, generation)
            return True
        except Exception as error:
            self._transition = None
            self.errorOccurred.emit('crossfade load failed: ' + str(error))
            return False

    def load_crossfade(self, path, position_ms, duration_ms=DEFAULT_CROSSFADE_MS,
                       *, candidate_origin_frame=None):
        """A/B switch; optionally give its exact origin relative to the active clip."""
        return self._queue(path, position_ms, duration_ms, True, candidate_origin_frame)

    def prepare_crossfade(self, path, position_ms, duration_ms=DEFAULT_CROSSFADE_MS):
        return self._queue(path, position_ms, duration_ms, False)

    def _on_decoded(self, generation, clip, error):
        if self._closed or generation != self._generation:
            return
        self._future = None
        if self._direct is not None:
            path, position_ms = self._direct
            self._direct = None
            if error:
                self.mediaStatusChanged.emit(MediaStatus.InvalidMedia)
                self.errorOccurred.emit('load failed: ' + error)
                self._publish(force=True)
                return
            try:
                self._new_device(clip)
                self._active = clip
                frame = min(clip.end, _frames(position_ms, clip.rate))
                self._stream.set_position(frame)
                self._phase_frame = self._frozen_frame = self._last_logical_frame = frame
                self.loaded.emit(path)
                if generation != self._generation or self._active is not clip:
                    return
                self.mediaStatusChanged.emit(MediaStatus.LoadedMedia)
                if generation != self._generation or self._active is not clip:
                    return
                self._publish(force=True)
                if generation == self._generation and self._active is clip and self._play_intent:
                    self.play()
            except Exception as exc:
                self._close_device()
                self._active = None
                self.errorOccurred.emit('load failed: ' + str(exc))
                self.mediaStatusChanged.emit(MediaStatus.InvalidMedia)
                self._publish(force=True)
            return
        transition = self._transition
        if transition is None:
            return
        if not error and clip.rate != self._active.rate:
            error = 'candidate sample rate differs from active media'
        if error:
            self._transition = None
            self.crossfadeCancelled.emit(transition.path)
            self.errorOccurred.emit('crossfade load failed: ' + error)
            return
        transition.clip = clip
        transition.ready = True
        self.loaded.emit(clip.path)
        if self._transition is not transition:
            return
        if transition.auto:
            elapsed = max(0, self._logical_frame() - transition.request_frame)
            offset = _frames(transition.requested_position_ms, clip.rate) + elapsed
            if not self._arm(transition, offset, None, transition.candidate_origin):
                self._cancel_transition(reset_if_buffered=False)
        else:
            self.crossfadeReady.emit(clip.path)

    def start_crossfade(self, position_ms=None, *, boundary_ms=None, boundary_frame=None,
                        candidate_origin_frame=None):
        """Arm a prepared switch.

        With boundary_frame (preferred) or boundary_ms, the boundary is relative
        to the *current clip*, and position_ms is the candidate position at that
        boundary. Call before Qt has read the boundary. Without a boundary,
        position_ms is the candidate position at the current logical playhead;
        the switch is moved to the first not-yet-read frame, advancing the
        candidate by exactly the same number of frames. This preserves alignment
        but delays the audible transition by the prefetch lead.
        ``candidate_origin_frame`` optionally supplies the candidate's original
        start as an exact frame relative to the current clip; it also preserves
        exact source alignment when arming after that origin has passed.
        """
        self._owner()
        transition = self._transition
        if (not self.available or transition is None or not transition.ready or
                transition.auto or transition.plan is not None):
            return False
        if boundary_ms is not None and boundary_frame is not None:
            self.errorOccurred.emit('specify boundary_ms or boundary_frame, not both')
            return False
        try:
            position_ms = (transition.requested_position_ms if position_ms is None
                           else max(0, int(position_ms)))
            offset = _frames(position_ms, self._active.rate)
            if boundary_frame is not None:
                boundary = self._active.origin + int(boundary_frame)
            elif boundary_ms is not None:
                boundary = self._active.origin + _frames(boundary_ms, self._active.rate)
            else:
                boundary = None
            origin = (None if candidate_origin_frame is None else
                      self._active.origin + int(candidate_origin_frame))
            return self._arm(transition, offset, boundary, origin)
        except Exception as error:
            self.errorOccurred.emit('start_crossfade failed: ' + str(error))
            return False

    def _arm(self, transition, offset, boundary, candidate_origin=None):
        try:
            logical = self._logical_frame()
            fade = max(1, _frames(transition.duration_ms, self._active.rate))
            for _ in range(4):
                cursor = self._stream.cursor_bytes()
                first_free = (cursor + _FRAME_BYTES - 1) // _FRAME_BYTES
                switch = first_free if boundary is None else boundary
                if switch < logical or switch < first_free:
                    raise ValueError('switch boundary has already been buffered')
                candidate_offset = (switch - candidate_origin if candidate_origin is not None
                                    else offset + (switch - logical if boundary is None else 0))
                plan = _make_plan(self._active, transition.clip, switch,
                                  candidate_offset, fade)
                if self._stream.install(plan):
                    transition.plan = plan
                    return True
                if boundary is not None:
                    raise ValueError('switch boundary was buffered while preparing the fade')
            raise RuntimeError('audio read head advanced while preparing the fade')
        except Exception as error:
            self.errorOccurred.emit('start_crossfade failed: ' + str(error))
            return False

    def _start_output(self, frame):
        self._stream.set_position(frame)
        self._phase_frame = self._frozen_frame = self._last_logical_frame = frame
        self._wall_active = 0.0
        self._wall_started = None
        self._idle_since = self._idle_frame = None
        self._output.start(self._stream)
        if int(self._output.state()) == 2:  # QAudio.StoppedState
            raise RuntimeError('audio output could not start (error %s)' % self._output.error())
        # Start may synchronously prefill and block. Starting the guard after it
        # returns is conservative and cannot publish early because of prefill.
        self._wall_started = self._clock()
        self._needs_start = False
        self._state = PlaybackState.PlayingState
        self._timer.start()

    def play(self):
        self._owner()
        if not self.available:
            return self._reject('play')
        if self._direct is not None:
            self._play_intent = True
            return True
        if self._active is None:
            return False
        if self._state == PlaybackState.PlayingState:
            return True
        try:
            self._play_intent = True
            if self._state == PlaybackState.PausedState:
                if self._needs_start:
                    self._start_output(self._frozen_frame)
                else:
                    self._output.resume()
                    if int(self._output.state()) == 2:
                        raise RuntimeError('audio output could not resume (error %s)' % self._output.error())
                    self._wall_started = self._clock()
                    self._idle_since = self._idle_frame = None
                    self._state = PlaybackState.PlayingState
                    self._timer.start()
            else:
                frame = self._frozen_frame
                if frame >= self._active.end:
                    frame = self._active.origin
                self._start_output(frame)
            self._publish(force=True)
            return True
        except Exception as error:
            self._play_intent = False
            self.errorOccurred.emit('play failed: ' + str(error))
            return False

    def _logical_frame(self):
        if self._active is None:
            return 0
        if self._state != PlaybackState.PlayingState or self._output is None:
            return self._frozen_frame
        processed = max(0, int(self._output.processedUSecs()))
        wall = self._wall_active
        if self._wall_started is not None:
            wall += max(0.0, self._clock() - self._wall_started)
        elapsed_us = min(processed, int(wall * 1000000))
        # processedUSecs() is truncated to whole microseconds. Flooring again
        # can leave a fractional-frame tail one frame short forever (and thus
        # suppress EndOfMedia), so recover the nearest complete sample frame.
        elapsed_frames = (elapsed_us * self._active.rate + 500000) // 1000000
        read_frame = self._stream.cursor_bytes() // _FRAME_BYTES
        logical = min(self._phase_frame + elapsed_frames, read_frame)
        self._last_logical_frame = max(self._last_logical_frame, logical)
        return self._last_logical_frame

    def pause(self):
        self._owner()
        if not self.available:
            return self._reject('pause')
        self._play_intent = False
        if self._direct is not None or self._active is None:
            return True
        if self._state != PlaybackState.PlayingState:
            return True
        try:
            self._tick()
            if self._state != PlaybackState.PlayingState:
                return True
            self._frozen_frame = self._logical_frame()
            if self._wall_started is not None:
                self._wall_active += max(0.0, self._clock() - self._wall_started)
            self._wall_started = None
            self._output.suspend()
            self._state = PlaybackState.PausedState
            self._timer.stop()
            self._publish(force=True)
            return True
        except Exception as error:
            self.errorOccurred.emit('pause failed: ' + str(error))
            return False

    def _reset_to(self, frame, was_playing):
        self._timer.stop()
        self._output.reset()
        self._stream.set_position(frame)
        self._phase_frame = self._frozen_frame = self._last_logical_frame = frame
        self._wall_active = 0.0
        self._wall_started = None
        self._needs_start = not was_playing
        if was_playing:
            self._start_output(frame)

    def _cancel_transition(self, reset_if_buffered=True):
        transition = self._transition
        if transition is None:
            return False
        plan = transition.plan
        if plan is not None:
            if not reset_if_buffered:
                self._stream.discard(plan)
            elif not self._stream.discard_if_unread(plan):
                frame = min(self._logical_frame(), self._active.end)
                playing = self._state == PlaybackState.PlayingState
                self._output.reset()
                self._stream.discard(plan)
                self._reset_to(frame, playing)
        self._transition = None
        self._new_generation()
        self.crossfadeCancelled.emit(transition.path)
        return True

    def cancel_crossfade(self):
        self._owner()
        try:
            return self._cancel_transition()
        except Exception as error:
            self.errorOccurred.emit('crossfade cancel failed: ' + str(error))
            return False

    def seek(self, position_ms):
        self._owner()
        if not self.available:
            return self._reject('seek')
        if self._active is None:
            return False
        try:
            # Reset discards the backend's already-prefetched samples before
            # dropping a plan, so they cannot reappear after a user seek.
            playing = self._state == PlaybackState.PlayingState
            self._output.reset()
            self._cancel_transition(reset_if_buffered=False)
            frame = self._active.origin + min(self._active.frames,
                                               _frames(position_ms, self._active.rate))
            self._reset_to(frame, playing)
            self._publish(force=True)
            return True
        except Exception as error:
            self.errorOccurred.emit('seek failed: ' + str(error))
            return False

    def stop(self):
        self._owner()
        if not self.available:
            return self._reject('stop')
        try:
            self._new_generation()
            self._close_device()
            transition = self._transition
            self._transition = None
            self._direct = None
            self._active = None
            self._state = PlaybackState.StoppedState
            self._play_intent = False
            self._phase_frame = self._frozen_frame = self._last_logical_frame = 0
            if transition is not None:
                self.crossfadeCancelled.emit(transition.path)
            self._publish(force=True)
            self.mediaStatusChanged.emit(MediaStatus.NoMedia)
            return True
        except Exception as error:
            self.errorOccurred.emit('stop failed: ' + str(error))
            return False

    def close(self):
        """Stop the backend before closing the source; invalidate worker results."""
        self._owner()
        if self._closed:
            return
        if self.available:
            self.stop()
        self._closed = True
        self._new_generation()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _tick(self):
        if self._closed or self._active is None:
            return
        if self._stream.last_error:
            message = self._stream.last_error
            self.stop()
            self.errorOccurred.emit('audio read failed: ' + message)
            return
        if self._state != PlaybackState.PlayingState:
            return
        output_state = int(self._output.state())
        if output_state == 2:
            error = self._output.error()
            self.stop()
            self.errorOccurred.emit('audio output stopped unexpectedly (error %s)' % error)
            return
        frame = self._logical_frame()
        transition = self._transition
        if transition is not None and transition.plan is not None:
            plan = transition.plan
            if frame >= plan.boundary and not transition.started:
                transition.started = True
                self.crossfadeStarted.emit(transition.path)
                if self._transition is not transition:
                    return
            if transition.started:
                progress = min(1.0, max(0.0, (frame - plan.boundary) / plan.fade_frames))
                self.crossfadeProgress.emit(progress)
                if self._transition is not transition:
                    return
            if frame >= plan.end:
                if not self._stream.commit(plan):
                    self.errorOccurred.emit('audio timeline commit failed')
                    return
                self._active = plan.new
                self._transition = None
                self.crossfadeFinished.emit(transition.path)
                if self._active is not plan.new:
                    return
                self._publish(force=True)
        if self._active is None:
            return
        if (self._transition is not None and self._transition.plan is None
                and frame >= self._active.end):
            self._cancel_transition(reset_if_buffered=False)
        if self._active is None:
            return
        if self._transition is None and frame >= self._active.end:
            if self._loop:
                self._reset_to(self._active.origin, True)
            else:
                self._output.stop()
                self._timer.stop()
                self._frozen_frame = self._active.end
                self._state = PlaybackState.StoppedState
                self._play_intent = False
            self.mediaStatusChanged.emit(MediaStatus.EndOfMedia)
            self._publish(force=True)
            return
        # A short IdleState can occur while a device is starting or draining.
        # A sustained idle state with no audible-frame progress, however,
        # otherwise leaves playback stuck forever after a device failure.
        if output_state == 3:  # QAudio.IdleState
            now = self._clock()
            if self._idle_since is None or frame != self._idle_frame:
                self._idle_since, self._idle_frame = now, frame
            elif now - self._idle_since >= _IDLE_STALL_SECONDS:
                self.stop()
                self.errorOccurred.emit('audio output stopped unexpectedly (idle without progress)')
                return
        else:
            self._idle_since = self._idle_frame = None
        self._publish()

    def position(self):
        if self._active is None:
            return 0
        return _milliseconds(self._logical_frame() - self._active.origin, self._active.rate)

    def duration(self):
        return _milliseconds(self._active.frames, self._active.rate) if self._active else 0

    def source(self):
        if self._active:
            return self._active.path
        return self._direct[0] if self._direct else ''

    def state(self):
        return self._state

    def volume(self, value=None):
        return self._master_volume if value is None else self.set_volume(value)

    def set_volume(self, value):
        self._owner()
        value = max(0, min(100, int(round(value))))
        if value != self._master_volume:
            self._master_volume = value
            if self._output is not None:
                self._output.setVolume(value / 100.0)
            self.volumeChanged.emit(value)
        return value

    def set_loop(self, enabled):
        self._owner()
        enabled = bool(enabled)
        if enabled != self._loop:
            self._loop = enabled
            self.loopChanged.emit(enabled)
        return enabled


# Convenient drop-in name for integrations that select a player at import time.
MixdownPlayer = MixdownStreamPlayer

__all__ = ['MixdownStreamPlayer', 'MixdownPlayer', 'PlaybackState', 'MediaStatus',
           'TimelineIODevice', 'DEFAULT_VOLUME', 'DEFAULT_CROSSFADE_MS']
