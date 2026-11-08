"""Worker-side PCM pump; no Python runs in the device callback.

Both pre-limiter diagnostics and the limited playback graph are accepted.
Every output block is checked against the export ceiling before submission.
"""
from __future__ import annotations

from collections import deque
import copy
import threading
import time
from typing import Any

from audio_mixdown import PEAK_CEILING


class RealtimeSessionError(RuntimeError):
    pass


class RealtimeAuditionSession:
    """Own a prepared renderer and output on one non-realtime worker thread.

    Output methods, source I/O, DSP, telemetry and control are serialized here.
    A separate native callback consumes the bounded output ring. ``silent``
    processes the real graph but sends zeros for unattended device probes.
    Position telemetry describes submitted media, not acoustic playback.
    """

    def __init__(self, renderer, output, *, block_frames=512,
                 target_queue_frames=None, silent=False, numpy=None):
        if numpy is None:
            import numpy
        self._np = numpy
        self._renderer = renderer
        self._output_factory = output if callable(output) else None
        self._output = None if self._output_factory else output
        self.block_frames = int(block_frames)
        if not 64 <= self.block_frames <= 8192:
            raise ValueError('block_frames must be between 64 and 8192')
        self._requested_queue_frames = target_queue_frames
        self.target_queue_frames = self.block_frames * 2
        initial = self._configure_output() if self._output is not None else None
        self.silent = bool(silent)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._done = threading.Event()
        self._closing = threading.Event()
        self._commands: deque = deque()
        self._seek_serial = 0
        self._parameter_serial = 0
        self._thread = None
        self._snapshot: dict[str, Any] = {
            'state': 'prepared', 'silent': self.silent, 'epoch': 0,
            'block_frames': self.block_frames, 'target_queue_frames': self.target_queue_frames,
            'native': initial, 'last_seek': None, 'parameter_revision': 0,
            'rendered_frames': 0, 'accepted_frames': 0, 'render_calls': 0,
            'max_queue_frames': 0, 'error': None,
            'clock_quality': 'native_callback_submitted_media_not_acoustic',
        }

    def _configure_output(self):
        initial = dict(self._output.snapshot())
        if str(initial.get('backend', '')).startswith('wasapi') and self._output_factory is None:
            raise ValueError('WASAPI requires an output factory owned by the DSP worker')
        renderer = self._renderer
        if (int(initial['sample_rate']) != int(renderer.sample_rate)
                or int(initial['channels']) != int(renderer.channels)):
            raise ValueError('renderer/output rate or channel count differs')
        capacity = int(initial['capacity_frames'])
        target = (max(self.block_frames * 2, int(initial.get('period_frames', 0)) * 2)
                  if self._requested_queue_frames is None else int(self._requested_queue_frames))
        target = ((target + self.block_frames - 1) // self.block_frames) * self.block_frames
        if not self.block_frames <= target <= capacity:
            raise ValueError('target queue must fit the native ring and one DSP block')
        self.target_queue_frames = target
        return initial

    def start(self):
        with self._lock:
            if self._thread is not None:
                return self
            if self._closing.is_set():
                raise RealtimeSessionError('session is closing')
            self._thread = threading.Thread(
                target=self._run, name='VWA realtime DSP producer', daemon=True)
            self._thread.start()
        return self

    def _enqueue(self, kind, value=None, *, serial_kind=None):
        with self._lock:
            if self._closing.is_set() or self._done.is_set():
                raise RealtimeSessionError('session is closed')
            if len(self._commands) >= 64:
                raise RealtimeSessionError('control queue is full')
            serial = None
            if serial_kind == 'seek':
                self._seek_serial += 1
                serial = self._seek_serial
                value = (serial, *value)
            elif serial_kind == 'parameters':
                self._parameter_serial += 1
                serial = self._parameter_serial
                value = (serial, value)
            self._commands.append((kind, value, time.perf_counter()))
        self._wake.set()
        return serial

    def seek(self, frame, *, autoplay=None):
        frame = int(frame)
        if not 0 <= frame <= int(self._renderer.duration_frames):
            raise ValueError('seek is outside the prepared song')
        return self._enqueue('seek', (frame, autoplay), serial_kind='seek')

    def set_parameters(self, request):
        value = copy.deepcopy(dict(request))
        before = time.perf_counter()
        prepare = getattr(self._renderer, 'prepare_parameters', None)
        prepared = callable(prepare) and callable(getattr(self._renderer, 'apply_parameters', None))
        if prepared:
            value = prepare(value)
        payload = (prepared, value, (time.perf_counter() - before) * 1000.0)
        return self._enqueue('parameters', payload, serial_kind='parameters')

    def play(self):
        self._enqueue('play')

    def pause(self):
        self._enqueue('pause')

    def set_volume(self, volume):
        value = float(volume)
        if not self._np.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('volume must be between 0 and 100')
        return self._enqueue('volume', value)

    def stop(self):
        return self.seek(0, autoplay=False)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _publish(self, state):
        with self._lock:
            self._snapshot = copy.deepcopy(state)

    def close(self, timeout=5.0):
        if self._done.is_set():
            return
        self._closing.set()
        self._wake.set()
        thread = self._thread
        if thread is None:
            with self._lock:
                if self._done.is_set():
                    return
                try:
                    if self._output is not None:
                        self._output.close()
                finally:
                    try:
                        self._renderer.close()
                    finally:
                        self._snapshot['state'] = 'closed'
                        self._done.set()
            return
        if thread is threading.current_thread():
            raise RealtimeSessionError('cannot join the DSP producer from itself')
        thread.join(timeout)
        if thread.is_alive():
            raise RealtimeSessionError('DSP producer did not close within the deadline')

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()

    def _run(self):
        state = self.snapshot()
        pending = None
        pending_start = pending_offset = 0
        epoch = 1
        intent = actual_playing = False
        prefilling = True
        render_times: deque = deque(maxlen=4096)
        initial_frame = int(self._renderer.position_frame)
        callback_count = None
        callback_at = time.perf_counter()
        try:
            if self._output_factory is not None:
                self._output = self._output_factory()
                state['native'] = self._configure_output()
                state['target_queue_frames'] = self.target_queue_frames
            self._output.set_playing(False)
            self._output.seek(frame=initial_frame, epoch=epoch)
            state.update(state='paused', epoch=epoch)
            while not self._closing.is_set():
                self._wake.clear()
                with self._lock:
                    commands = list(self._commands)
                    self._commands.clear()
                latest_seek = latest_parameters = latest_volume = None
                for kind, value, at in commands:
                    if kind == 'seek':
                        latest_seek = (value, at)
                        if value[2] is not None:
                            intent = bool(value[2])
                    elif kind == 'parameters':
                        latest_parameters = (value, at)
                    elif kind == 'play':
                        intent = True
                    elif kind == 'pause':
                        intent = False
                    elif kind == 'volume':
                        latest_volume = value
                native = dict(self._output.snapshot())
                if native.get('device_error'):
                    raise RealtimeSessionError(str(native['device_error']))
                if str(native.get('backend', '')).startswith('wasapi') and 'callback_count' in native:
                    current_count = int(native['callback_count'])
                    if current_count != callback_count:
                        callback_count, callback_at = current_count, time.perf_counter()
                    elif time.perf_counter() - callback_at > 2.0:
                        raise RealtimeSessionError('WASAPI output callback stopped responding')
                if not intent and actual_playing:
                    self._output.set_playing(False)
                    actual_playing = False
                    native = dict(self._output.snapshot())
                if latest_volume is not None:
                    self._renderer.set_volume(latest_volume)
                if latest_parameters:
                    (revision, (prepared, request, validation_ms)), at = latest_parameters
                    start = time.perf_counter()
                    if prepared:
                        self._renderer.apply_parameters(request)
                    else:
                        self._renderer.set_parameters(request)
                    state.update(parameter_revision=revision,
                                 parameter_validation_ms=validation_ms,
                                 parameter_applied_at=time.perf_counter(),
                                 parameter_requested_at=at,
                                 parameter_processing_ms=(time.perf_counter() - start) * 1000,
                                 parameter_first_frame=int(getattr(
                                     self._renderer, 'parameter_first_frame',
                                     self._renderer.position_frame)))
                    # Paused queues cannot drain. Rebuild the selected location
                    # rather than playing an old parameter snapshot on resume.
                    if not intent and latest_seek is None:
                        latest_seek = ((None, int(native['media_frame']), None), at)
                if latest_seek:
                    (serial, frame, _), requested_at = latest_seek
                    self._output.set_playing(False)
                    actual_playing = False
                    prefilling = True
                    epoch += 1
                    self._output.seek(frame=frame, epoch=epoch)
                    self._renderer.seek(frame)
                    if latest_parameters:
                        state['parameter_first_frame'] = frame
                    pending = None
                    pending_offset = 0
                    state.update(epoch=epoch, state='preparing')
                    if serial is not None:
                        state['last_seek'] = {
                            'seek_id': serial, 'epoch': epoch, 'frame': frame,
                            'requested_at': requested_at, 'prepared_at': time.perf_counter(),
                            'phase': 'prepared', 'clock_quality': state['clock_quality'],
                        }
                    native = dict(self._output.snapshot())
                queued = int(native['queued_frames'])
                state['max_queue_frames'] = max(state['max_queue_frames'], queued)
                at_end = int(self._renderer.position_frame) >= int(self._renderer.duration_frames)
                if (pending is None and queued <= self.target_queue_frames - self.block_frames
                        and not at_end):
                    count = min(self.block_frames,
                                int(self._renderer.duration_frames) - int(self._renderer.position_frame))
                    pending_start = int(self._renderer.position_frame)
                    before = time.perf_counter()
                    pending = self._np.ascontiguousarray(self._renderer.render(count), dtype='float32')
                    render_times.append((time.perf_counter() - before) * 1000.0)
                    if pending.shape != (count, int(self._renderer.channels)):
                        raise RealtimeSessionError('renderer returned an invalid PCM shape')
                    peak = float(self._np.max(self._np.abs(pending))) if count else 0.0
                    if not self._np.isfinite(pending).all() or peak > PEAK_CEILING + 1e-6:
                        raise RealtimeSessionError('PCM exceeds the safe output ceiling')
                    state['sample_peak'] = max(state.get('sample_peak', 0.0), peak)
                    state['block_peak'] = peak
                    if self.silent:
                        pending = self._np.zeros_like(pending)
                    pending_offset = 0
                    state['render_calls'] += 1
                    state['rendered_frames'] += count
                wrote = 0
                if pending is not None:
                    wrote = int(self._output.write(
                        pending[pending_offset:], epoch=epoch,
                        start_frame=pending_start + pending_offset))
                    if not 0 <= wrote <= len(pending) - pending_offset:
                        raise RealtimeSessionError('native output accepted an invalid frame count')
                    pending_offset += wrote
                    state['accepted_frames'] += wrote
                    if pending_offset == len(pending):
                        pending = None
                        pending_offset = 0
                native = dict(self._output.snapshot())
                if pending is not None and not wrote:
                    expected = pending_start + pending_offset
                    if (int(native['epoch']) != epoch or
                            int(native.get('accepted_end_frame', expected)) != expected):
                        raise RealtimeSessionError('native epoch or accepted media position diverged')
                queued = int(native['queued_frames'])
                at_end = int(self._renderer.position_frame) >= int(self._renderer.duration_frames)
                start_threshold = (self.target_queue_frames if prefilling else
                                   max(self.block_frames, int(native.get('period_frames', 0))))
                if intent and not actual_playing and (
                        queued >= start_threshold or (at_end and queued > 0)):
                    self._output.set_playing(True)
                    actual_playing = True
                    prefilling = False
                if actual_playing and at_end and pending is None and queued == 0:
                    self._output.set_playing(False)
                    actual_playing = intent = False
                    state['state'] = 'ended'
                elif actual_playing:
                    state['state'] = 'playing'
                else:
                    state['state'] = ('preparing' if intent else
                                      'ended' if at_end and not queued else 'paused')
                last = state.get('last_seek')
                if last and last['epoch'] == epoch and at_end and not queued and pending is None:
                    last.update(phase='ended')
                if last and last['epoch'] == epoch and last['phase'] == 'prepared' and queued:
                    last.update(phase='queued', first_pcm_queued_at=time.perf_counter())
                    last['request_to_queue_ms'] = (last['first_pcm_queued_at'] - last['requested_at']) * 1000
                if last and last['epoch'] == epoch and int(native['media_frame']) > last['frame']:
                    if last['phase'] != 'submitted':
                        last.update(phase='submitted', submitted_at=time.perf_counter())
                        last['request_to_submit_ms'] = (last['submitted_at'] - last['requested_at']) * 1000
                state.update(native=native, play_intent=intent,
                             max_queue_frames=max(state['max_queue_frames'], queued),
                             renderer_frame=int(self._renderer.position_frame),
                             render_ms=list(render_times))
                self._publish(state)
                if not wrote or queued >= self.target_queue_frames:
                    self._wake.wait(0.002)
        except Exception as error:
            state.update(state='failed', error='%s: %s' % (type(error).__name__, error))
        finally:
            for resource in (self._output, self._renderer):
                if resource is None:
                    continue
                try:
                    resource.close()
                except Exception as error:
                    state.update(state='failed', error='close failed: ' + str(error))
            if state.get('state') != 'failed':
                state['state'] = 'closed'
            self._publish(state)
            self._done.set()
