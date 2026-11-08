"""Standalone tests; synthetic WAVs are held in memory and no files are written.

Run with the bundled Python: ``python -B tests/test_mixdown_stream_player.py``.
Add ``--device`` for the optional, muted default-device probe.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PyQt5.QtCore import QCoreApplication, QObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import mixdown_stream_player as sp


RATE = 44100
FRAME_BYTES = 8
APP = QCoreApplication.instance() or QCoreApplication([])


def wait_for(predicate, label, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError('timed out waiting for ' + label)


def fixtures():
    """Distinct correlated signals expose both misalignment and bad weights."""
    n = 2 * RATE
    starts = (0, RATE, 2 * RATE)
    clips, wavs = [], {}
    for index, start in enumerate(starts):
        t = (np.arange(n, dtype=np.float64) + start) / RATE
        left = 0.18 * np.sin(2 * np.pi * 233 * t) + index * 0.025
        right = 0.14 * np.cos(2 * np.pi * 317 * t) - index * 0.015
        data = np.column_stack((left, right)).astype(np.float32)
        path = os.path.abspath('synthetic-stream-%d.wav' % index)
        buffer = io.BytesIO()
        sf.write(buffer, data, RATE, format='WAV', subtype='FLOAT')
        wavs[path] = buffer.getvalue()
        clips.append((path, data))
    return clips, wavs


def reader_for(wavs):
    def read(path):
        return sf.read(io.BytesIO(wavs[path]), dtype='float32', always_2d=True)
    return read


def oracle(clips, starts, boundaries, fade):
    """Independent frame oracle, including every sample in each fade."""
    end = starts[-1] + len(clips[-1])
    out = np.empty((end, 2), dtype=np.float32)
    segment = 0
    for frame in range(end):
        while segment < len(boundaries) and frame >= boundaries[segment] + fade:
            segment += 1
        if segment < len(boundaries) and boundaries[segment] <= frame:
            t = (frame - boundaries[segment]) / fade
            w = np.float32(t * t * (3.0 - 2.0 * t))
            old = clips[segment][frame - starts[segment]]
            new = clips[segment + 1][frame - starts[segment + 1]]
            out[frame] = old * np.float32(1.0 - w) + new * w
        else:
            out[frame] = clips[segment][frame - starts[segment]]
    return out


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class FakeOutput(QObject):
    """A pull output with a real queued byte buffer and controllable clock."""
    def __init__(self, rate, parent, clock, lead_frames, processed_lead=0):
        super().__init__(parent)
        self.rate, self.clock = rate, clock
        self.lead = lead_frames
        self.processed_lead = processed_lead
        self.device_state = 2
        self.fifo = bytearray()
        self.played = bytearray()
        self.stream = None
        self.consumed_frames = 0
        self.resets = 0
        self.reset_snapshots = []
        self.starts = 0
        self.level = None

    def setVolume(self, value):
        self.level = value

    def _fill(self):
        need = max(0, self.lead * FRAME_BYTES - len(self.fifo))
        if need:
            self.fifo.extend(bytes(self.stream.read(need)))

    def start(self, stream):
        self.stream = stream
        self.starts += 1
        self.consumed_frames = 0
        self.fifo.clear()
        self.device_state = 0
        self._fill()  # Real backends may call readData before start returns.

    def reset(self):
        self.resets += 1
        stream = self.stream
        self.reset_snapshots.append((
            stream.cursor_bytes() if stream is not None else None,
            stream._plan if stream is not None else None))
        self.fifo.clear()
        self.device_state = 2

    def stop(self):
        self.fifo.clear()
        self.device_state = 2

    def suspend(self):
        self.device_state = 1

    def resume(self):
        if self.device_state == 1:
            self.device_state = 0

    def state(self):
        return self.device_state

    def error(self):
        return 0

    def processedUSecs(self):
        return (self.consumed_frames + self.processed_lead) * 1000000 // self.rate

    def advance(self, frames):
        remaining = int(frames)
        while remaining and self.device_state == 0:
            self._fill()
            count = min(remaining, 353, len(self.fifo) // FRAME_BYTES)
            if not count:
                self.device_state = 3
                break
            size = count * FRAME_BYTES
            self.played.extend(self.fifo[:size])
            del self.fifo[:size]
            self.consumed_frames += count
            self.clock.value += count / self.rate
            remaining -= count
        if self.device_state == 0:
            self._fill()
        return int(frames) - remaining


class FakeFactory:
    def __init__(self, clock, supported=(RATE,), lead_ms=200, processed_lead=0):
        self.clock = clock
        self.supported = supported
        self.lead_ms = lead_ms
        self.processed_lead = processed_lead
        self.outputs = []

    def supports(self, rate):
        return rate in self.supported

    def create(self, rate, parent):
        output = FakeOutput(rate, parent, self.clock, rate * self.lead_ms // 1000,
                            self.processed_lead)
        self.outputs.append(output)
        return output


def make_player(wavs, *, reader=None, processed_lead=0):
    clock = FakeClock()
    factory = FakeFactory(clock, processed_lead=processed_lead)
    player = sp.MixdownStreamPlayer(output_factory=factory,
                                    reader=reader or reader_for(wavs), clock=clock)
    errors = []
    player.errorOccurred.connect(errors.append)
    return player, factory, clock, errors


def open_playing(player, path):
    assert player.load(path)
    assert player.play()  # Pending loads remember the play intent.
    wait_for(lambda: player.state() == sp.PlaybackState.PlayingState, 'initial play')


def prepare(player, path, duration=80):
    ready = []
    player.crossfadeReady.connect(ready.append)
    assert player.prepare_crossfade(path, 0, duration)
    wait_for(lambda: path in ready, 'candidate ready')


def advance(player, output, frames, block=997):
    left = frames
    while left > 0:
        count = min(left, block)
        done = output.advance(count)
        assert done == count, ('unexpected starvation', done, count, output.state())
        left -= count
        player._tick()


def test_full_three_segment_stream_and_signals():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs, processed_lead=RATE // 5)
    paths = [x[0] for x in clips]
    started, finished, positions = [], [], []
    player.crossfadeStarted.connect(lambda path: started.append((path, player._logical_frame())))
    player.crossfadeFinished.connect(lambda path: finished.append((path, player._logical_frame())))
    player.positionChanged.connect(lambda position: positions.append((player.source(), position)))
    open_playing(player, paths[0])
    assert player.supports_sample_aligned_boundary and player.sample_rate == RATE
    output = factory.outputs[0]
    assert output.level == .72 and len(output.fifo) == RATE // 5 * FRAME_BYTES
    assert player.volume(120) == 100 and output.level == 1.0
    assert player.set_volume(72) == 72 and player.volume() == 72
    prepare(player, paths[1])
    assert player.start_crossfade(0, boundary_frame=RATE, candidate_origin_frame=RATE)
    assert player._transition.plan.new.origin == RATE
    assert not started and player.position() == 0  # Device already pulled 200 ms.
    advance(player, output, int(1.15 * RATE))
    assert started and started[0][1] >= RATE
    assert finished and finished[0][1] >= RATE + int(.08 * RATE)
    assert player.source() == paths[1] and not player.transitioning
    prepare(player, paths[2])
    assert player.start_crossfade(0, boundary_frame=RATE, candidate_origin_frame=RATE)
    assert player._transition.plan.new.origin == 2 * RATE
    advance(player, output, 4 * RATE - int(1.15 * RATE))
    clock.value += 0.1
    player._tick()
    assert [p for p, _ in started] == paths[1:]
    assert [p for p, _ in finished] == paths[1:]
    assert started[1][1] >= 2 * RATE
    assert finished[1][1] >= 2 * RATE + int(.08 * RATE)
    assert player.state() == sp.PlaybackState.StoppedState
    assert len(factory.outputs) == 1
    expected = oracle([x[1] for x in clips], (0, RATE, 2 * RATE),
                      (RATE, 2 * RATE), int(.08 * RATE))
    got = np.frombuffer(output.played, dtype='<f4').reshape(-1, 2)
    assert got.shape == expected.shape
    np.testing.assert_array_equal(got.view('u4'), expected.view('u4'))
    assert not errors, errors
    assert any(source == paths[1] and 0 <= pos < 200 for source, pos in positions)
    print('three-segment sample oracle:', len(got), 'frames, 0 differing float32 words')
    player.close()


def test_odd_byte_chunks_and_late_candidate_alignment():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs, processed_lead=RATE // 5)
    paths = [x[0] for x in clips]
    open_playing(player, paths[0])
    output = factory.outputs[0]
    advance(player, output, 3 * RATE // 4)
    prepare(player, paths[1])
    # An exact boundary which is already inside the Qt buffer must be refused.
    assert not player.start_crossfade(0, boundary_frame=RATE * 7 // 10)
    assert 'buffered' in errors[-1]
    origin = RATE * 7 // 10
    assert player.start_crossfade(50, candidate_origin_frame=origin)
    plan = player._transition.plan
    assert plan.new.origin == origin
    assert plan.boundary >= 3 * RATE // 4 + RATE // 5
    fade = plan.fade_frames
    advance(player, output, 2 * RATE - 3 * RATE // 4)
    expected = oracle([x[1] for x in clips[:2]], (0, origin), (plan.boundary,), fade)
    got = np.frombuffer(output.played, dtype='<f4').reshape(-1, 2)
    np.testing.assert_array_equal(got.view('u4'), expected[:len(got)].view('u4'))
    player.close()

    # Exercise the actual QIODevice with requests crossing every byte/frame,
    # boundary and fade edge. No read may drop or duplicate even one byte.
    decoded = [sp._decode(path, reader_for(wavs)) for path in paths]
    stream = sp.TimelineIODevice(decoded[0])
    first = sp._make_plan(decoded[0], decoded[1], RATE, 0, int(.08 * RATE))
    assert stream.install(first)
    chunks = [1, 7, 8, 31, 97, 511, 2049, 8193]
    data = bytearray()
    i = 0
    while stream.cursor_bytes() < int(1.3 * RATE) * FRAME_BYTES:
        data.extend(bytes(stream.read(chunks[i % len(chunks)])))
        i += 1
    assert stream.commit(first)
    second = sp._make_plan(first.new, decoded[2], 2 * RATE, 0, int(.08 * RATE))
    assert stream.install(second)
    while True:
        block = bytes(stream.read(chunks[i % len(chunks)]))
        if not block:
            break
        data.extend(block)
        i += 1
    expected = oracle([x[1] for x in clips], (0, RATE, 2 * RATE),
                      (RATE, 2 * RATE), int(.08 * RATE))
    assert bytes(data) == expected.astype('<f4', copy=False).tobytes()
    stream.finish_close()
    print('odd byte requests: exact across both switches')


def test_pause_seek_cancel_ab_loop_and_end():
    clips, wavs = fixtures()
    paths = [x[0] for x in clips]
    player, factory, clock, errors = make_player(wavs, processed_lead=RATE // 5)
    cancelled, ended = [], []
    player.crossfadeCancelled.connect(cancelled.append)
    player.mediaStatusChanged.connect(lambda s: ended.append(s) if s == sp.MediaStatus.EndOfMedia else None)
    open_playing(player, paths[0])
    out = factory.outputs[0]
    prepare(player, paths[1])
    assert player.start_crossfade(0, boundary_frame=RATE)
    advance(player, out, 9 * RATE // 10)
    assert out.stream.cursor_bytes() > RATE * FRAME_BYTES
    before = player.position()
    assert player.cancel_crossfade() and cancelled == [paths[1]]
    assert player.source() == paths[0] and not player.transitioning
    assert out.resets >= 1 and abs(player.position() - before) <= 1
    assert player.pause()
    paused = player.position()
    reads = out.stream.read_calls
    clock.value += 3.0
    player._tick()
    assert player.position() == paused and out.stream.read_calls == reads
    reset_count = out.resets
    assert player.seek(500)
    assert out.resets == reset_count + 1
    assert player.state() == sp.PlaybackState.PausedState and player.position() == 500
    starts = out.starts
    assert player.play() and out.starts == starts + 1  # reset while paused needs start, not resume
    advance(player, out, RATE // 10)
    assert 599 <= player.position() <= 600
    # A/B: relative candidate position is advanced by the buffered lead.
    assert player.load_crossfade(paths[1], player.position(), 80,
                                 candidate_origin_frame=0)
    wait_for(lambda: player._transition is not None and player._transition.plan is not None,
             'automatic A/B transition')
    plan = player._transition.plan
    assert plan.new.origin == player._active.origin
    advance(player, out, RATE // 2)
    assert player.source() == paths[1]
    player.set_loop(True)
    assert player.loop and player.set_loop(False) is False
    # User seek cancels any armed plan and drops already-queued bytes.
    prepare(player, paths[2])
    assert player.start_crossfade(0, boundary_frame=RATE * 18 // 10)
    assert player.seek(1900)
    assert not player.transitioning and cancelled[-1] == paths[2]
    advance(player, out, RATE // 10)
    clock.value += .1
    player._tick()
    assert ended and player.state() == sp.PlaybackState.StoppedState
    assert player.position() == 2000
    assert not errors, errors
    player.close()


def test_legacy_ab_delayed_decode_uses_current_position_and_buffer_lead():
    clips, wavs = fixtures()
    entered, release = threading.Event(), threading.Event()
    reader = reader_for(wavs)
    def delayed(path):
        if path == clips[1][0]:
            entered.set()
            assert release.wait(3)
        return reader(path)
    player, factory, clock, errors = make_player(wavs, reader=delayed)
    open_playing(player, clips[0][0])
    out = factory.outputs[0]
    advance(player, out, 3 * RATE // 10)
    request_position = player.position()
    assert player.load_crossfade(clips[1][0], request_position, 80)
    request_frame = player._transition.request_frame
    assert entered.wait(2)
    advance(player, out, 123 * RATE // 1000)
    assert player.source() == clips[0][0]
    release.set()
    wait_for(lambda: player._transition is not None and player._transition.plan is not None,
             'delayed A/B plan')
    plan = player._transition.plan
    # The user position refers to the playhead at request time. Decode delay and
    # then the queued lead must be added, once each, to the candidate offset.
    expected_origin = request_frame - sp._frames(request_position, RATE)
    assert plan.new.origin == expected_origin
    assert plan.boundary * FRAME_BYTES >= out.stream.cursor_bytes()
    advance(player, out, RATE // 2)
    assert player.source() == clips[1][0] and not errors
    player.close()


def test_seek_updates_pending_load_and_preserves_play_intent():
    clips, wavs = fixtures()
    path = clips[0][0]
    entered, release = threading.Event(), threading.Event()
    base_reader = reader_for(wavs)
    def delayed(candidate):
        if candidate == path:
            entered.set()
            assert release.wait(3)
        return base_reader(candidate)
    player, factory, clock, errors = make_player(wavs, reader=delayed)
    loaded = []
    player.loaded.connect(loaded.append)
    assert player.load(path, 100)
    assert player.play()  # Pending loads retain this intent across seek.
    assert entered.wait(2)
    assert player.seek(1200)
    assert player.seek(1350)  # The latest pending-load target wins.
    release.set()
    wait_for(lambda: player.state() == sp.PlaybackState.PlayingState,
             'seeked pending load')
    target = sp._frames(1350, RATE)
    assert loaded == [path] and player.position() == 1350
    output = factory.outputs[0]
    before = len(output.played)
    advance(player, output, 1000)
    played = np.frombuffer(output.played[before:], dtype='<f4').reshape(-1, 2)
    np.testing.assert_array_equal(played, clips[0][1][target:target + 1000])
    assert not errors
    player.close()


def test_seek_supersedes_delayed_crossfade_and_ignores_late_decode():
    clips, wavs = fixtures()
    candidate = clips[1][0]
    entered, release = threading.Event(), threading.Event()
    base_reader = reader_for(wavs)
    def delayed(path):
        if path == candidate:
            entered.set()
            assert release.wait(3)
        return base_reader(path)
    player, factory, clock, errors = make_player(wavs, reader=delayed)
    open_playing(player, clips[0][0])
    loaded, ready, cancelled = [], [], []
    player.loaded.connect(loaded.append)
    player.crossfadeReady.connect(ready.append)
    player.crossfadeCancelled.connect(cancelled.append)
    output = factory.outputs[0]
    advance(player, output, 300 * RATE // 1000)
    assert player.prepare_crossfade(candidate, 0)
    assert entered.wait(2)
    future = player._future
    resets = output.resets
    assert player.seek(1200)
    assert output.resets == resets + 1 and not player.transitioning
    assert cancelled == [candidate] and player.position() == 1200
    release.set()
    wait_for(future.done, 'superseded crossfade decode')
    for _ in range(20):
        APP.processEvents()
        time.sleep(.002)
    assert not player.transitioning and player.source() == clips[0][0]
    assert ready == [] and loaded == [] and player.position() == 1200
    before = len(output.played)
    advance(player, output, 1000)
    played = np.frombuffer(output.played[before:], dtype='<f4').reshape(-1, 2)
    target = sp._frames(1200, RATE)
    np.testing.assert_array_equal(played, clips[0][1][target:target + 1000])
    assert not errors
    player.close()


def test_seek_flushes_device_once_before_discarding_buffered_transition():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs)
    open_playing(player, clips[0][0])
    output = factory.outputs[0]
    prepare(player, clips[1][0])
    assert player.start_crossfade(0, boundary_frame=RATE)
    plan = player._transition.plan
    advance(player, output, 900 * RATE // 1000)
    assert output.stream._plan is plan
    assert output.stream.cursor_bytes() > plan.boundary * FRAME_BYTES
    resets = output.resets
    before = len(output.played)
    assert player.seek(500)
    assert output.resets == resets + 1
    cursor, installed = output.reset_snapshots[-1]
    assert installed is plan and cursor > plan.boundary * FRAME_BYTES
    assert output.stream._plan is None and not player.transitioning
    target = sp._frames(500, RATE)
    advance(player, output, 1000)
    played = np.frombuffer(output.played[before:], dtype='<f4').reshape(-1, 2)
    np.testing.assert_array_equal(played, clips[0][1][target:target + 1000])
    assert not errors
    player.close()


def test_install_from_ready_signal_with_absolute_source_times():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs)
    open_playing(player, clips[0][0])
    current_start = 123456 / RATE
    next_start = (123456 + RATE) / RATE
    installed = []
    def on_ready(path):
        boundary = round(next_start * player.sample_rate) - round(current_start * player.sample_rate)
        installed.append(player.start_crossfade(
            0, boundary_frame=boundary, candidate_origin_frame=boundary))
    player.crossfadeReady.connect(on_ready)
    assert player.prepare_crossfade(clips[1][0], 0, 80)
    wait_for(lambda: bool(installed), 'install in ready callback')
    assert installed == [True] and player._transition.plan.new.origin == RATE
    assert not errors
    player.close()


def test_reentrant_load_keeps_latest_source():
    clips, wavs = fixtures()
    first, second = clips[0][0], clips[1][0]
    base_reader = reader_for(wavs)
    entered, release = threading.Event(), threading.Event()
    reads = []
    def delayed(path):
        reads.append(path)
        if path == second:
            entered.set()
            assert release.wait(3)
        return base_reader(path)
    player, factory, clock, errors = make_player(wavs, reader=delayed)
    nested = []
    def supersede(path):
        if path == first:
            nested.append(player.load(second))
    player.sourceChanged.connect(supersede)
    # The original load is superseded while it is publishing LoadingMedia.
    assert player.load(first) is False
    assert entered.wait(2)
    assert player._active is None and reads == [second]
    release.set()
    wait_for(lambda: player._active is not None, 'latest reentrant source load')
    assert nested == [True]
    assert player.source() == second and player._active.path == second
    np.testing.assert_array_equal(player._active.samples, clips[1][1])
    assert reads == [second] and not errors
    player.close()


def test_long_fixed_multisegment_mixed_channels_and_fractional_end():
    rate = RATE
    segment_count, step, clip_frames = 5, 30000, 36000
    fade = int(.08 * rate)
    paths, wavs, decoded = [], {}, []
    for index in range(segment_count):
        start = index * step
        seconds = (np.arange(clip_frames, dtype=np.float64) + start) / rate
        left = .16 * np.sin(2 * np.pi * (233 + index) * seconds) + .00001 * index
        right = .12 * np.cos(2 * np.pi * (317 + index) * seconds) - .00001 * index
        if index % 2:
            samples = ((left + right) * .5).astype(np.float32)[:, None]
            rendered = np.repeat(samples, 2, axis=1)
        else:
            samples = np.column_stack((left, right)).astype(np.float32)
            rendered = samples
        path = os.path.abspath('synthetic-long-%d.wav' % index)
        buffer = io.BytesIO()
        sf.write(buffer, samples, rate, format='WAV', subtype='FLOAT')
        paths.append(path)
        wavs[path] = buffer.getvalue()
        decoded.append(rendered)

    player, factory, clock, errors = make_player(wavs)
    started, finished, read_ahead, ends = [], [], [], []
    def on_started(path):
        started.append(path)
        read_ahead.append(out.stream.cursor_bytes() // FRAME_BYTES - player._logical_frame())
    player.crossfadeStarted.connect(on_started)
    player.crossfadeFinished.connect(finished.append)
    player.mediaStatusChanged.connect(
        lambda status: ends.append(status) if status == sp.MediaStatus.EndOfMedia else None)
    open_playing(player, paths[0])
    out = factory.outputs[0]
    for index in range(1, segment_count):
        prepare(player, paths[index])
        assert player.start_crossfade(
            0, boundary_frame=step, candidate_origin_frame=step)
        assert player._transition.plan.boundary == index * step
        advance(player, out, step + fade + 1, block=(17, 353, 997, 4096)[index % 4])
        assert started[-1] == finished[-1] == paths[index]

    target_frames = (segment_count - 1) * step + clip_frames
    advance(player, out, target_frames - out.consumed_frames, block=333)
    clock.value += .1
    player._tick()
    played = np.frombuffer(out.played, dtype='<f4').reshape(-1, 2)
    expected = oracle(decoded, tuple(index * step for index in range(segment_count)),
                      tuple(index * step for index in range(1, segment_count)), fade)
    assert played.shape == expected.shape == (target_frames, 2)
    np.testing.assert_array_equal(played.view('u4'), expected.view('u4'))
    assert started == paths[1:] and finished == paths[1:]
    assert all(frames > 0 for frames in read_ahead)
    assert ends == [sp.MediaStatus.EndOfMedia] and not errors
    assert player.state() == sp.PlaybackState.StoppedState
    assert player.source() == paths[-1] and len(factory.outputs) == 1
    player.close()


def test_idle_stall_recovers_or_reports_failure_once():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs)
    open_playing(player, clips[0][0])
    output = factory.outputs[0]
    advance(player, output, RATE // 4)

    # A transient output idle must not interrupt the current selection.
    output.device_state = 3
    player._tick()
    clock.value += 1.0
    player._tick()
    assert not errors and player.state() == sp.PlaybackState.PlayingState
    output.device_state = 0
    player._tick()
    advance(player, output, RATE // 10)
    assert not errors

    # A subsequent prolonged idle without progress must surface exactly one
    # fatal device error, which the dialog uses for compatibility fallback.
    output.device_state = 3
    player._tick()
    clock.value += sp._IDLE_STALL_SECONDS + .01
    player._tick()
    assert len(errors) == 1 and 'audio output stopped unexpectedly' in errors[0]
    assert player.state() == sp.PlaybackState.StoppedState
    player._tick()
    assert len(errors) == 1
    player.close()


def test_loop_and_rejections():
    clips, wavs = fixtures()
    player, factory, clock, errors = make_player(wavs)
    open_playing(player, clips[0][0])
    out = factory.outputs[0]
    cancelled = []
    player.crossfadeCancelled.connect(cancelled.append)
    prepare(player, clips[1][0])  # A ready but unarmed candidate cannot suppress EOF/loop.
    player.loop = True
    advance(player, out, 2 * RATE)
    clock.value += .1
    player._tick()
    assert out.starts == 2 and player.state() == sp.PlaybackState.PlayingState
    assert player.position() == 0
    assert cancelled == [clips[1][0]] and not player.transitioning
    player.close()

    clock = FakeClock()
    unavailable = sp.MixdownStreamPlayer(output_factory=FakeFactory(clock, supported=()))
    assert not unavailable.available and 'support' in unavailable.unavailable_reason
    assert not unavailable.load('x.wav')
    unavailable.close()

    invalid = dict(wavs)
    multichannel = io.BytesIO()
    sf.write(multichannel, np.zeros((100, 3), dtype=np.float32), RATE,
             format='WAV', subtype='FLOAT')
    invalid[clips[1][0]] = multichannel.getvalue()
    player, factory, clock, errors = make_player(invalid)
    open_playing(player, clips[0][0])
    assert player.prepare_crossfade(clips[1][0], 0)
    wait_for(lambda: bool(errors), 'multichannel rejection')
    assert 'channel' in errors[-1] and player.source() == clips[0][0]
    player.close()

    for badrate in (22050, 48000):
        bad = io.BytesIO()
        sf.write(bad, np.zeros((200, 2), dtype=np.float32), badrate, format='WAV', subtype='FLOAT')
        source = os.path.abspath('unsupported-rate.wav')
        player, factory, clock, errors = make_player({source: bad.getvalue()})
        assert player.load(source)
        wait_for(lambda: bool(errors), 'sample rate rejection')
        assert 'support' in errors[-1]
        assert not player.source()
        player.close()

    # 48 kHz stereo is accepted only when the selected device supports it.
    forty_eight = io.BytesIO()
    sf.write(forty_eight, np.zeros((4800, 2), dtype=np.float32), 48000,
             format='WAV', subtype='FLOAT')
    source = os.path.abspath('supported-48000.wav')
    clock = FakeClock()
    factory = FakeFactory(clock, supported=(48000,))
    player = sp.MixdownStreamPlayer(output_factory=factory,
                                    reader=reader_for({source: forty_eight.getvalue()}),
                                    clock=clock)
    open_playing(player, source)
    assert player.sample_rate == 48000 and player.duration() == 100
    player.close()


def test_supports_path_is_header_only_and_does_not_mutate():
    clips, wavs = fixtures()
    def make_wav(channels, rate, format='WAV'):
        mem = io.BytesIO()
        sf.write(mem, np.zeros((100, channels), dtype=np.float32), rate,
                 format=format)
        return mem.getvalue()
    paths = {name: os.path.abspath('header-' + name + '.wav') for name in
             ('mono', 'bad-rate', 'unsupported-device', 'other-format', 'broken')}
    wavs[paths['mono']] = make_wav(1, RATE)
    wavs[paths['bad-rate']] = make_wav(2, 22050)
    wavs[paths['unsupported-device']] = make_wav(2, 48000)
    wavs[paths['other-format']] = make_wav(2, RATE, 'FLAC')
    wavs[paths['broken']] = b'not an audio file'
    player, factory, clock, errors = make_player(wavs)
    real_info = sf.info
    info_paths = []
    def virtual_info(path):
        info_paths.append(path)
        return real_info(io.BytesIO(wavs[path]))
    before = (player._generation, player._direct, player._active,
              player._output, player._state, player.transitioning)
    with patch.object(sp.sf, 'info', side_effect=virtual_info):
        assert player.supports_path(clips[0][0])
        assert player.supports_path(paths['mono'])
        assert not player.supports_path(paths['bad-rate'])
        assert not player.supports_path(paths['unsupported-device'])
        assert not player.supports_path(paths['other-format'])
        assert not player.supports_path(paths['broken'])
        assert not player.supports_path('missing.wav')
        assert not player.supports_path(None)
    after = (player._generation, player._direct, player._active,
             player._output, player._state, player.transitioning)
    assert before == after and not errors and len(info_paths) == 7
    player.close()
    assert not player.supports_path(clips[0][0])


def test_mono_and_supported_16khz_render_on_one_stream():
    rate = 16000
    values = (0.2 * np.sin(2 * np.pi * 347 * np.arange(rate) / rate)).astype(np.float32)
    wav = io.BytesIO()
    sf.write(wav, values, rate, format='WAV', subtype='FLOAT')
    path = os.path.abspath('synthetic-mono-16k.wav')
    clock = FakeClock()
    factory = FakeFactory(clock, supported=(rate,))
    player = sp.MixdownStreamPlayer(output_factory=factory,
                                    reader=reader_for({path: wav.getvalue()}),
                                    clock=clock)
    errors = []
    player.errorOccurred.connect(errors.append)
    open_playing(player, path)
    output = factory.outputs[0]
    advance(player, output, len(values))
    played = np.frombuffer(output.played, dtype='<f4').reshape(-1, 2)
    np.testing.assert_array_equal(played[:, 0], values)
    np.testing.assert_array_equal(played[:, 1], values)
    assert not errors
    player.close()


def test_stale_worker_and_concurrent_close():
    clips, wavs = fixtures()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    base_reader = reader_for(wavs)
    read_threads, signal_threads = [], []
    def blocked(path):
        read_threads.append(threading.get_ident())
        if path == clips[1][0]:
            entered.set()
            assert release.wait(3)
        result = base_reader(path)
        if path == clips[1][0]:
            completed.set()
        return result
    player, factory, clock, errors = make_player(wavs, reader=blocked)
    player.loaded.connect(lambda _: signal_threads.append(threading.get_ident()))
    open_playing(player, clips[0][0])
    assert read_threads[0] != threading.get_ident()
    assert signal_threads == [threading.get_ident()]
    assert player.prepare_crossfade(clips[1][0], 0)
    assert entered.wait(2)
    assert player.cancel_crossfade()
    release.set()
    wait_for(completed.is_set, 'stale worker completion')
    APP.processEvents()
    assert player.source() == clips[0][0]
    stream = factory.outputs[0].stream
    failures = []
    def read_until_closed():
        try:
            while stream.readData(17):
                pass
        except Exception as error:
            failures.append(error)
    worker = threading.Thread(target=read_until_closed)
    worker.start()
    player.close()
    worker.join(timeout=3)
    assert not worker.is_alive() and not failures
    assert stream.readData(17) == b'' and not player.available
    APP.processEvents()


def test_cancelled_queued_decodes_do_not_run():
    clips, wavs = fixtures()
    third = os.path.abspath('synthetic-stream-queued.wav')
    wavs[third] = wavs[clips[1][0]]
    entered = {path: threading.Event() for path in (clips[1][0], clips[2][0])}
    release = threading.Event()
    finished = {path: threading.Event() for path in entered}
    seen = []
    reader = reader_for(wavs)
    def blocked(path):
        seen.append(path)
        if path in entered:
            entered[path].set()
            assert release.wait(3)
        value = reader(path)
        if path in finished:
            finished[path].set()
        return value
    player, factory, clock, errors = make_player(wavs, reader=blocked)
    open_playing(player, clips[0][0])
    for path in entered:
        assert player.prepare_crossfade(path, 0)
        assert entered[path].wait(2)
        assert player.cancel_crossfade()
    assert player.prepare_crossfade(third, 0)
    assert player.cancel_crossfade()  # Both workers are occupied; cancel queued work.
    release.set()
    wait_for(lambda: all(event.is_set() for event in finished.values()), 'running reads')
    APP.processEvents()
    assert third not in seen and not player.transitioning and not errors
    player.close()


def run_real_device_probe():
    clips, wavs = fixtures()
    player = sp.MixdownStreamPlayer(reader=reader_for(wavs), volume=0)
    assert player.available, player.unavailable_reason
    started, finished, errors, states, ends = [], [], [], [], []
    def on_started(path):
        lead_frames = player._stream.cursor_bytes() // FRAME_BYTES - player._logical_frame()
        started.append((path, player.position(), round(lead_frames * 1000 / RATE, 2)))
    player.crossfadeStarted.connect(on_started)
    player.crossfadeFinished.connect(lambda p: finished.append((p, player.position())))
    player.stateChanged.connect(states.append)
    player.errorOccurred.connect(errors.append)
    player.mediaStatusChanged.connect(lambda s: ends.append(s) if s == sp.MediaStatus.EndOfMedia else None)
    open_playing(player, clips[0][0])
    out = player._output
    buffer_bytes, period_bytes = int(out.bufferSize()), int(out.periodSize())
    assert out.volume() == 0.0
    prepare(player, clips[1][0])
    assert player.start_crossfade(0, boundary_frame=RATE)
    wait_for(lambda: player.position() >= 350, 'real device before pause')
    assert player.pause()
    frozen, reads = player.position(), player._stream.read_calls
    until = time.monotonic() + .16
    while time.monotonic() < until:
        APP.processEvents()
        time.sleep(.002)
    assert player.position() == frozen and player._stream.read_calls == reads
    assert player.play()
    wait_for(lambda: len(finished) == 1, 'real first boundary')
    prepare(player, clips[2][0])
    assert player.start_crossfade(0, boundary_frame=RATE)
    wait_for(lambda: len(finished) == 2, 'real second boundary')
    assert player.seek(1300) and player.state() == sp.PlaybackState.PlayingState
    wait_for(lambda: player.position() >= 1350, 'real seek playback')
    seek_position = player.position()
    assert player.seek(1900)
    wait_for(lambda: bool(ends), 'real end of media', timeout=3)
    info = {'qt_state_after_end': int(out.state()), 'buffer_bytes': buffer_bytes,
            'period_bytes': period_bytes, 'read_calls': player._stream.read_calls,
            'reader_threads': sorted(player._stream.reader_threads),
            'main_thread': threading.get_ident(), 'started': started,
            'finished': finished, 'states': states, 'seek_position_ms': seek_position,
            'end_position_ms': player.position(), 'end_events': len(ends),
            'errors': errors, 'muted': out.volume() == 0.0}
    assert not errors, errors
    assert len(started) == len(finished) == 2
    assert all(position >= 1000 for _, position, _ in started)
    assert any(lead_ms > 0 for _, _, lead_ms in started)
    player.close()
    print('real muted device:', info)


def main():
    test_full_three_segment_stream_and_signals()
    test_odd_byte_chunks_and_late_candidate_alignment()
    test_pause_seek_cancel_ab_loop_and_end()
    test_legacy_ab_delayed_decode_uses_current_position_and_buffer_lead()
    test_seek_updates_pending_load_and_preserves_play_intent()
    test_seek_supersedes_delayed_crossfade_and_ignores_late_decode()
    test_seek_flushes_device_once_before_discarding_buffered_transition()
    test_install_from_ready_signal_with_absolute_source_times()
    test_reentrant_load_keeps_latest_source()
    test_long_fixed_multisegment_mixed_channels_and_fractional_end()
    test_idle_stall_recovers_or_reports_failure_once()
    test_loop_and_rejections()
    test_supports_path_is_header_only_and_does_not_mutate()
    test_mono_and_supported_16khz_render_on_one_stream()
    test_stale_worker_and_concurrent_close()
    test_cancelled_queued_decodes_do_not_run()
    if '--device' in sys.argv:
        run_real_device_probe()
    print('test_mixdown_stream_player: PASS')


if __name__ == '__main__':
    main()
