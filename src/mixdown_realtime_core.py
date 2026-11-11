"""Framework-neutral transport for one resident native mix session.

The core owns playback intent, generation ownership, bounded commands and
snapshot acknowledgement without importing a Qt binding. Qt 5 and Qt 6 keep
thin signal adapters around this class; runtime loading, source preparation,
parameter validation and process shutdown stay off the GUI thread.
"""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import uuid


class PlaybackState:
    StoppedState = 0
    PlayingState = 1
    PausedState = 2


class MediaStatus:
    NoMedia = 1
    LoadingMedia = 2
    LoadedMedia = 3
    EndOfMedia = 7
    InvalidMedia = 8


def native_output_path(root=None, environment=None):
    """Resolve an explicit developer DLL or a built component with its digest."""
    env = os.environ if environment is None else environment
    explicit = env.get('VWA_MIXDOWN_NATIVE_DLL', '')
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    root = Path(root) if root else Path(__file__).resolve().parent
    if root.name == 'src':
        root = root.parent
    directory = root / 'components/native-audio'
    try:
        manifest = json.loads((directory / 'native-audio.json').read_text(encoding='utf-8-sig'))
        path = directory / 'vwa_native_output.dll'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        limiter_digest = hashlib.sha256((directory / 'vwa_limiter.dll').read_bytes()).hexdigest()
        if (manifest.get('dll_sha256', '').lower() == digest
                and manifest.get('limiter_dll_sha256', '').lower() == limiter_digest):
            return path
    except (OSError, ValueError, AttributeError):
        pass
    return None


class RealtimePreviewPlayerCore:
    """Own one realtime transport independently of the GUI toolkit.

    ``event_sink`` receives ``(signal_name, *arguments)`` in the same order as
    the existing Qt adapter signals. Polling remains the caller's responsibility
    so service events continue to be interpreted on the UI thread.
    """

    def __init__(self, dll_path, *, cache_dir, service_factory=None,
                 service_options=None, event_sink=None):
        if service_factory is None:
            from mixdown_realtime_service import MixdownRealtimeService
            service_factory = MixdownRealtimeService
        self._event_sink = event_sink
        self._service = service_factory(cache_dir, **(service_options or {}))
        self.dll_path = str(dll_path)
        self.available = True
        self.unavailable_reason = ''
        self.transitioning = False
        self.failed = False
        self.loading = False
        self._ready = False
        self._generation = None
        self._position = self._duration = 0
        self._state = PlaybackState.StoppedState
        self._intent = False
        self._volume = 72
        self._snapshot = {}
        self._pending_seek = None
        self._pending_parameters = None
        self._serial = 0
        self.selected_key = 'after'
        self._loop = False
        self._loop_start = self._loop_end = 0
        self._end_reported = False
        self._condition = threading.Condition()
        self._commands = deque()
        self._messages = queue.Queue()
        self._closing = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name='VWA live transport', daemon=True)
        self._thread.start()

    def _emit(self, name, *arguments):
        if self._event_sink is not None:
            self._event_sink(name, *arguments)

    @property
    def ready(self):
        return self._ready and not self.failed

    @property
    def generation(self):
        return self._generation

    @property
    def play_intent(self):
        return self._intent

    @property
    def seek_pending(self):
        return self._pending_seek is not None

    def set_play_intent(self, enabled):
        """Stage intent before a dirty request is submitted; sends no command."""
        self._intent = bool(enabled)

    @property
    def is_alive(self):
        return not self._done.is_set()

    @property
    def snapshot(self):
        return copy.deepcopy(self._snapshot)

    def _queue(self, kind, value=None, token=None):
        with self._condition:
            if self._closing:
                return False
            if kind in ('open', 'release'):
                self._commands.clear()
            elif kind in ('parameters', 'seek', 'volume'):
                # Coalesce only within the current generation; play/pause order
                # is retained in the remaining queue.
                self._commands = deque(row for row in self._commands
                                       if not (row[0] == kind and row[2] == self._generation))
            if len(self._commands) >= 64:
                self._messages.put(('error', self._generation, '连续试听控制队列已满'))
                return False
            self._commands.append((kind, value, self._generation, token))
            self._condition.notify()
        return True

    def _run(self):
        started = False
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closing or self._commands)
                    if self._closing:
                        break
                    kind, value, generation, token = self._commands.popleft()
                if generation != self._generation and kind != 'release':
                    continue
                try:
                    if kind == 'open':
                        self._service.start()
                        started = True
                        if generation != self._generation:
                            continue
                        self._service.open(value['request'], dll_path=self.dll_path,
                                           position_ms=value['position'],
                                           autoplay=value['autoplay'], volume=self._volume,
                                           request_id=generation)
                    elif started:
                        if kind == 'seek':
                            identifier = self._service.seek(value[0], autoplay=value[1])
                            self._messages.put(('seek_sent', generation, (token, identifier)))
                        elif kind == 'parameters':
                            identifier = self._service.parameters(value)
                            self._messages.put(('parameters_sent', generation, (token, identifier)))
                        elif kind == 'volume':
                            self._service.set_volume(value)
                        else:
                            getattr(self._service, kind)()
                except Exception as error:
                    self._messages.put(('error', generation, str(error)))
        finally:
            try:
                self._service.close(timeout=1.5)
            except Exception as error:
                self._messages.put(('error', self._generation, str(error)))
            self._done.set()

    def open(self, request, *, position_ms=0, autoplay=False, key='after'):
        self._generation = uuid.uuid4().hex
        self._ready = False
        self.failed = False
        self.loading = True
        self._pending_seek = self._pending_parameters = None
        self._position = max(0, int(position_ms))
        self._intent = bool(autoplay)
        self.selected_key = key
        self._end_reported = False
        self._emit('mediaStatusChanged', MediaStatus.LoadingMedia)
        return self._queue('open', dict(request=copy.deepcopy(request),
                                        position=self._position, autoplay=self._intent))

    def parameters(self, request, *, key='after'):
        self._serial += 1
        self._pending_parameters = dict(token=self._serial, control_id=None, key=key)
        return self._queue('parameters', copy.deepcopy(request), self._serial)

    def seek(self, position_ms, *, autoplay=None):
        self._serial += 1
        target = max(0, min(int(position_ms), self._duration))
        if autoplay is not None:
            self._intent = bool(autoplay)
        self._pending_seek = dict(token=self._serial, control_id=None, target=target)
        self._position = target
        self._end_reported = False
        return self._queue('seek', (target, self._intent), self._serial)

    def play(self):
        self._intent = True
        if self._ready and self._position >= self._duration:
            return self.seek(self._loop_start if self._loop else 0, autoplay=True)
        return self._queue('play')

    def pause(self):
        self._intent = False
        return self._queue('pause')

    def stop(self):
        self._intent = False
        return self.seek(0, autoplay=False) if self._ready else self._queue('stop')

    def release(self):
        result = self._queue('release')
        self._generation = None
        self._ready = self.loading = self._intent = False
        self._pending_seek = self._pending_parameters = None
        self._position = self._duration = 0
        self._set_state(PlaybackState.StoppedState)
        return result

    def set_volume(self, volume):
        self._volume = max(0, min(100, int(volume)))
        return self._queue('volume', self._volume)

    def set_loop(self, enabled):
        self._loop = bool(enabled)

    def set_loop_range(self, start_ms, end_ms):
        self._loop_start = max(0, int(start_ms))
        self._loop_end = max(self._loop_start, int(end_ms))

    def position(self):
        return self._position

    def duration(self):
        return self._duration

    def state(self):
        return self._state

    def source(self):
        return self._generation if self.ready else None

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self._emit('stateChanged', state)

    def _fail(self, message):
        if self.failed:
            return
        self.failed = True
        self.loading = self._ready = self._intent = False
        self._pending_seek = self._pending_parameters = None
        # A rejected control request can leave the worker's previous graph
        # alive. Stop its playback intent before presenting a stopped UI.
        self._queue('pause')
        self._set_state(PlaybackState.StoppedState)
        self._emit('errorOccurred', str(message))

    def poll(self):
        # First bind command ids, then inspect snapshots. Reversed arrival of a
        # subprocess event and its send receipt must not acknowledge an old seek.
        while True:
            try:
                kind, generation, data = self._messages.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            if kind == 'error':
                self._fail(data)
            else:
                pending = self._pending_seek if kind == 'seek_sent' else self._pending_parameters
                if pending and pending['token'] == data[0]:
                    pending['control_id'] = data[1]
        for event in self._service.poll(timeout=0, max_events=64):
            if event.get('type') in ('crashed', 'exited', 'fatal', 'protocol_error'):
                self._fail(event.get('message') or event.get('stderr_tail') or '连续试听服务已退出')
                continue
            if event.get('request_id') != self._generation or self.failed:
                continue
            kind = event.get('type')
            if kind == 'error':
                self._fail(event.get('message', '连续试听失败'))
            elif kind == 'live_ready':
                self.loading = False
                self._ready = True
                self._duration = int(round(int(event['duration_frames']) * 1000 / int(event['sample_rate'])))
                self._emit('durationChanged', self._duration)
                self._emit('sourceChanged', self._generation)
                self._emit('mediaStatusChanged', MediaStatus.LoadedMedia)
                self._emit('prepared')
            elif kind == 'live_state':
                self._accept_snapshot(dict(event.get('snapshot') or {}))

    def _accept_snapshot(self, snapshot):
        self._snapshot = snapshot
        state = snapshot.get('state')
        if state == 'failed':
            self._fail(snapshot.get('error') or '连续试听设备不可用')
            return
        native = snapshot.get('native') or {}
        rate = int(snapshot.get('sample_rate') or native.get('sample_rate') or 0)
        if not self.ready or rate <= 0:
            return
        duration = int(round(int(snapshot.get('duration_frames', 0)) * 1000 / rate))
        if duration > 0 and duration != self._duration:
            self._duration = duration
            self._emit('durationChanged', duration)
        if native.get('media_frame') is None:
            return
        frame = int(native.get('media_frame', 0))
        pending = self._pending_seek
        if pending:
            last = snapshot.get('last_seek') or {}
            acknowledged = (pending['control_id'] is not None
                            and snapshot.get('seek_control_id') == pending['control_id']
                            and last.get('phase') in ('queued', 'submitted', 'ended')
                            and abs(int(last.get('frame', -1)) * 1000 / rate - pending['target']) <= 1)
            if not acknowledged:
                return
            self._pending_seek = None
            self._emit('seekConfirmed')
        self._position = min(self._duration, max(0, int(frame * 1000 // rate)))
        self._set_state(PlaybackState.PlayingState if state == 'playing' else
                        PlaybackState.StoppedState if state == 'ended' else PlaybackState.PausedState)
        pending = self._pending_parameters
        if (pending and pending['control_id'] is not None
                and snapshot.get('parameter_control_id') == pending['control_id']
                and (frame > int(snapshot.get('parameter_first_frame', frame)) or
                     (state == 'paused' and int(native.get('queued_frames', 0)) > 0))):
            self.selected_key = pending['key']
            self._pending_parameters = None
            self._emit('parametersApplied', self.selected_key)
        self._emit('positionChanged', self._position)
        self._emit('telemetryChanged', snapshot)
        end = min(self._duration, self._loop_end or self._duration)
        if self._loop and self._intent and self._position >= end and not self._pending_seek:
            self.seek(min(self._loop_start, max(0, self._duration - 1)), autoplay=True)
        elif state == 'ended' and not self._end_reported:
            self._intent = False
            self._end_reported = True
            self._emit('mediaStatusChanged', MediaStatus.EndOfMedia)

    def close(self, timeout=3.0):
        with self._condition:
            self._closing = True
            self._commands.clear()
            self._condition.notify()
        # Called by the dialog's shutdown worker, never by an audio callback.
        # Interrupt an in-flight runtime startup before joining its actor.
        self._service.close(timeout=min(float(timeout), 1.5))
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise RuntimeError('连续试听进程尚未停止')


RealtimePreviewCore = RealtimePreviewPlayerCore
