"""Worker-side PCM pump; no Python runs in the device callback.

Both pre-limiter diagnostics and the limited playback graph are accepted.
Every output block is checked against the export ceiling before submission.
"""
from __future__ import annotations

from collections import deque
import copy
import math
import os
import threading
import time
from typing import Any

from audio_mixdown import PEAK_CEILING
from mixdown_sources import AUDITION_SPECTRUM_INTERVAL_MS


TELEMETRY_SCHEMA = 'vwa.audio_mixdown.realtime-telemetry/v1'
TELEMETRY_SOURCE = 'realtime_dsp'
TELEMETRY_HISTORY_BLOCKS = 256


class RealtimeSessionError(RuntimeError):
    pass


_SNAPSHOT_SCALARS = frozenset((int, float, str, bool, type(None)))


def _copy_state(value):
    """Isolate JSON telemetry containers while copying flat numeric arrays once.

    FFT, EDC and timing arrays contain immutable scalars. Deepcopy's per-float
    dispatch and memo bookkeeping on every producer/poller iteration needlessly
    compete with DSP for the GIL. Nested containers still receive independent
    copies; unexpected extension values retain deepcopy's original semantics.
    """
    kind = type(value)
    if kind in _SNAPSHOT_SCALARS:
        return value
    if kind is dict:
        return {key: _copy_state(item) for key, item in value.items()}
    if kind is list:
        if all(type(item) in _SNAPSHOT_SCALARS for item in value):
            return list(value)
        return [_copy_state(item) for item in value]
    return copy.deepcopy(value)


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
        self._telemetry_blocks: deque = deque(maxlen=TELEMETRY_HISTORY_BLOCKS)
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
        self._snapshot['telemetry'] = self._telemetry_snapshot(
            self._snapshot, 0, initial.get('media_frame') if isinstance(initial, dict) else None
        )

    def _configure_output(self):
        initial = dict(self._output.snapshot())
        if str(initial.get('backend', '')).startswith('wasapi') and self._output_factory is None:
            raise ValueError('WASAPI requires an output factory owned by the DSP worker')
        renderer = self._renderer
        if (int(initial['sample_rate']) != int(renderer.sample_rate)
                or int(initial['channels']) != int(renderer.channels)):
            raise ValueError('renderer/output rate or channel count differs')
        capacity = int(initial['capacity_frames'])
        if self._requested_queue_frames is None:
            # The requested callback quantum may be smaller than WASAPI's
            # actual shared-device period. Keep three real device periods,
            # converted to source frames, with a bounded DSP-sized margin.
            actual_rate = int(initial.get('actual_device_sample_rate') or renderer.sample_rate)
            actual_period = int(initial.get('actual_device_period_frames') or
                                initial.get('period_frames') or self.block_frames)
            period = int(math.ceil(actual_period * int(renderer.sample_rate) / actual_rate))
            target = min(capacity // self.block_frames * self.block_frames,
                         max(self.block_frames * 3, period * 3))
        else:
            target = int(self._requested_queue_frames)
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
            return _copy_state(self._snapshot)

    def _publish(self, state):
        with self._lock:
            self._snapshot = _copy_state(state)

    def _telemetry_record_for_frame(self, media_frame, epoch):
        if media_frame is None:
            return None
        frame = int(media_frame)
        if frame <= 0:
            return None
        # Records and the native media cursor are monotonic inside one epoch.
        # Advance the left edge as the callback consumes blocks: total work is
        # amortized O(1), with no scan of the bounded history per snapshot.
        while len(self._telemetry_blocks) > 1:
            head = self._telemetry_blocks[0]
            following = self._telemetry_blocks[1]
            if (int(head.get('session_epoch', -1)) != int(epoch)
                    or int(following.get('session_epoch', -1)) != int(epoch)):
                self._telemetry_blocks.popleft()
                continue
            if int(following.get('frame_start', frame + 1)) < frame:
                self._telemetry_blocks.popleft()
                continue
            break
        if not self._telemetry_blocks:
            return None
        record = self._telemetry_blocks[0]
        if (int(record.get('session_epoch', -1)) != int(epoch)
                or int(record.get('frame_start', frame + 1)) >= frame
                or int(record.get('frame_end', -1)) < frame):
            return None
        return record

    def _telemetry_snapshot(self, state, epoch, media_frame):
        sample_rate = int(self._renderer.sample_rate)
        duration_frames = int(self._renderer.duration_frames)
        record = self._telemetry_record_for_frame(media_frame, epoch)
        if record is None:
            status = ('pending_seek' if state.get('state') == 'preparing'
                      else 'not_submitted' if media_frame is not None else 'unavailable')
            return {
                'schema': TELEMETRY_SCHEMA,
                'source': TELEMETRY_SOURCE,
                'sample_rate': sample_rate,
                'media_frame': None if media_frame is None else int(media_frame),
                'parameter_revision': int(state.get('parameter_revision', 0)),
                'position_ms': (0.0 if media_frame is None else
                                round(int(media_frame) * 1000.0 / sample_rate, 3)),
                'duration_ms': round(duration_frames * 1000.0 / sample_rate, 3),
                'history_epoch': int(epoch),
                'status': status,
                'tracks': [],
                'bus': {'peak_dbfs': None, 'rms_dbfs': None,
                        'limiter_gr_db': None},
                'spectrum': {
                    'freq_hz': [], 'before_dbfs': [], 'after_dbfs': [],
                    'source': 'dsp_fft_pre_post_eq', 'sample_rate': sample_rate,
                    'media_frame': None if media_frame is None else int(media_frame),
                    'window_start_frame': None, 'window_end_frame': None,
                    'interval_ms': AUDITION_SPECTRUM_INTERVAL_MS, 'bins': 0, 'fft_size': None,
                },
                'reverb': {
                    'enabled': False, 'mix': 0.0,
                    'send_rms_dbfs': None, 'send_peak_dbfs': None,
                    'return_rms_dbfs': None, 'return_peak_dbfs': None,
                    'dry_rms_dbfs': None, 'measured_wet_fraction': None,
                    'wet_to_dry_db': None,
                    'kernel_edc': {'available': False, 'measured': False,
                                   'reason': 'no_submitted_telemetry'},
                },
                'dynamics': {},
            }
        frame = int(media_frame)
        bus_source = record.get('bus') if isinstance(record.get('bus'), dict) else {}
        bus = {
            'peak_dbfs': bus_source.get('peak_dbfs'),
            'rms_dbfs': bus_source.get('rms_dbfs'),
            'limiter_gr_db': bus_source.get('limiter_gr_db'),
            'pre_limiter': dict(bus_source.get('pre_limiter') or {}),
            'post_limiter': dict(bus_source.get('post_limiter') or {}),
            'final_output': dict(bus_source.get('final_output') or {}),
        }
        tracks = []
        for row in record.get('tracks') or ():
            if not isinstance(row, dict):
                continue
            tracks.append({
                'role': str(row.get('role', 'vocal')),
                'index': int(row.get('index', 0)),
                'peak_dbfs': row.get('peak_dbfs'),
                'rms_dbfs': row.get('rms_dbfs'),
                'gr_db': row.get('gr_db', 0.0),
                'audible': bool(row.get('audible', False)),
                'stage': row.get('stage'),
            })
        source_spectrum = record.get('spectrum')
        if not isinstance(source_spectrum, dict):
            source_spectrum = {}
        frequencies = source_spectrum.get('freq_hz')
        if frequencies is None:
            frequencies = source_spectrum.get('frequency_hz', [])
        freq_hz = list(frequencies or ())
        spectrum = {
            'freq_hz': freq_hz,
            'before_dbfs': list(source_spectrum.get('before_dbfs') or ()),
            'after_dbfs': list(source_spectrum.get('after_dbfs') or ()),
            'source': str(source_spectrum.get('source') or 'dsp_fft_pre_post_eq'),
            'sample_rate': sample_rate,
            'media_frame': frame,
            'window_start_frame': source_spectrum.get('window_start_frame'),
            'window_end_frame': source_spectrum.get('window_end_frame'),
            'interval_ms': float(source_spectrum.get('interval_ms', AUDITION_SPECTRUM_INTERVAL_MS)),
            'bins': int(source_spectrum.get('bins', len(freq_hz))),
            'fft_size': source_spectrum.get('fft_size'),
        }
        reverb_source = record.get('reverb')
        reverb = dict(reverb_source) if isinstance(reverb_source, dict) else {}
        live = dict(reverb.get('live') or {})
        reverb.update({
            'enabled': bool(reverb.get('enabled', False)),
            'mix': float(reverb.get('mix', 0.0)),
            'send_rms_dbfs': live.get('send_rms_dbfs'),
            'send_peak_dbfs': live.get('send_peak_dbfs'),
            'return_rms_dbfs': live.get('wet_rms_dbfs'),
            'return_peak_dbfs': live.get('wet_peak_dbfs'),
            'dry_rms_dbfs': live.get('dry_rms_dbfs'),
            'measured_wet_fraction': live.get('measured_wet_fraction'),
            'wet_to_dry_db': live.get('wet_to_dry_db'),
        })
        kernel = dict(reverb.get('kernel_edc') or {})
        get_kernel = getattr(self._renderer, 'reverb_measurement', None)
        if callable(get_kernel) and reverb.get('enabled'):
            current_kernel = get_kernel()
            if (isinstance(current_kernel, dict)
                    and current_kernel.get('effects_rebuild_count')
                    == kernel.get('effects_rebuild_count')):
                kernel = dict(current_kernel)
        kernel.setdefault('available', False)
        kernel.setdefault('measured', False)
        if not kernel.get('available'):
            kernel.setdefault('reason', 'reverb_kernel_measurement_pending')
        reverb['kernel_edc'] = kernel
        return {
            'schema': TELEMETRY_SCHEMA,
            'source': TELEMETRY_SOURCE,
            'sample_rate': sample_rate,
            'media_frame': frame,
            'parameter_revision': int(record.get('parameter_revision', 0)),
            'position_ms': round(frame * 1000.0 / sample_rate, 3),
            'duration_ms': round(duration_frames * 1000.0 / sample_rate, 3),
            'history_epoch': int(epoch),
            'status': 'ready',
            'render_frame_start': int(record.get('frame_start', 0)),
            'render_frame_end': int(record.get('frame_end', 0)),
            'tracks': tracks,
            'bus': bus,
            'spectrum': spectrum,
            'reverb': reverb,
            'dynamics': dict(record.get('dynamics') or {}),
        }

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
                    mark_end = getattr(self._output, 'mark_end', None)
                    if callable(mark_end):
                        mark_end(None, epoch=epoch)
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
                    self._telemetry_blocks.clear()
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
                    get_telemetry = getattr(self._renderer, 'telemetry_for_range', None)
                    if callable(get_telemetry):
                        telemetry = get_telemetry(pending_start, pending_start + count)
                        if isinstance(telemetry, dict):
                            telemetry = dict(telemetry)
                            telemetry['parameter_revision'] = int(
                                state.get('parameter_revision', 0)
                            )
                            telemetry['session_epoch'] = int(epoch)
                            self._telemetry_blocks.append(telemetry)
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
                    if int(self._renderer.position_frame) >= int(self._renderer.duration_frames):
                        mark_end = getattr(self._output, 'mark_end', None)
                        if callable(mark_end):
                            mark_end(int(self._renderer.duration_frames), epoch=epoch)
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
                drained = queued == 0 or int(native.get('media_frame', 0)) >= int(self._renderer.duration_frames)
                if actual_playing and at_end and pending is None and drained:
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
                state['telemetry'] = self._telemetry_snapshot(
                    state, epoch, native.get('media_frame')
                )
                state.update(native=native, play_intent=intent,
                             max_queue_frames=max(state['max_queue_frames'], queued),
                             renderer_frame=int(self._renderer.position_frame),
                             render_ms=list(render_times))
                self._publish(state)
                if not wrote or queued >= self.target_queue_frames:
                    if os.name == 'nt':
                        # Event.wait(2ms) rounds to ~15.6ms on the supported
                        # Windows runtime. Its native time.sleep uses a finer
                        # timer; keep the bounded queue supplied without a
                        # persistent system timer-resolution change.
                        if not self._wake.is_set():
                            time.sleep(0.002)
                    else:
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
