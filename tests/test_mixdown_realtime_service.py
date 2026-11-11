"""Contracts for the resident realtime mixdown session service."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from mixdown_realtime_service import (  # noqa: E402
    MAX_CONTROL_COMMANDS,
    MixdownRealtimeService,
    _RealtimeWorkerServer,
    _silent_probe_enabled,
)


def wait_until(predicate, *, timeout=3.0, message="condition was not reached"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.005)
    raise AssertionError("%s; last=%r" % (message, last))


def wait_event(events, predicate, *, timeout=3.0):
    return wait_until(
        lambda: next((item for item in list(events) if predicate(item)), None),
        timeout=timeout,
        message="event was not emitted",
    )


def latest_state(events, generation):
    states = [
        item for item in list(events)
        if item.get("type") == "live_state"
        and item.get("request_id") == generation
    ]
    return states[-1] if states else None


def wait_state(events, generation, predicate, *, timeout=3.0):
    return wait_until(
        lambda: (
            latest_state(events, generation)
            if latest_state(events, generation)
            and predicate(latest_state(events, generation)["snapshot"])
            else None
        ),
        timeout=timeout,
        message="live state did not satisfy the predicate",
    )["snapshot"]


class FakeGraph:
    def __init__(self, request, cache_dir, dll_path, *, ffmpeg=None):
        self.request = copy.deepcopy(dict(request))
        self.cache_dir = str(cache_dir)
        self.dll_path = str(dll_path)
        self.ffmpeg = ffmpeg
        self.sample_rate = 8000
        self.channels = 2
        self.duration_frames = 8000
        self.position_frame = 0
        self.close_count = 0
        self.control_threads = []

    def close(self):
        self.close_count += 1


class GraphFactory:
    def __init__(self):
        self.created = []
        self.slow_entered = threading.Event()
        self.slow_release = threading.Event()
        self.fail_sources = set()

    def __call__(self, request, cache_dir, dll_path, *, ffmpeg=None):
        graph = FakeGraph(request, cache_dir, dll_path, ffmpeg=ffmpeg)
        self.created.append(graph)
        source = str(request.get("source") or "")
        if source == "slow":
            self.slow_entered.set()
            if not self.slow_release.wait(5.0):
                raise RuntimeError("slow graph test was not released")
        if source == "fail" or source in self.fail_sources:
            graph.close()
            raise RuntimeError("source decoding failed: " + source)
        return graph


class FakeSession:
    def __init__(self, graph, output_factory, *, block_frames, silent):
        self.graph = graph
        self.output_factory = output_factory
        self.block_frames = block_frames
        self.silent = silent
        self.started = False
        self.closed = 0
        self.media_frame = 0
        self.last_seek = None
        self.apply_requests = []
        self.seek_calls = []
        self.play_calls = 0
        self.pause_calls = 0
        self.stop_calls = 0
        self.volume_calls = []
        self.parameter_accepted = 0
        self.parameter_revision_applied = 0
        self._pending_seek = None
        self._pending_parameter = None
        self.control_threads = []
        self.render_ms = []
        self.start_error = (
            "start failed" if graph.request.get("source") == "session-start-fail" else None
        )

    def start(self):
        self.control_threads.append(threading.get_ident())
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.started = True
        return self

    def set_volume(self, volume):
        self.control_threads.append(threading.get_ident())
        self.volume_calls.append(float(volume))

    def seek(self, frame, *, autoplay=None):
        self.control_threads.append(threading.get_ident())
        serial = len(self.seek_calls) + 1
        self.seek_calls.append((serial, int(frame), autoplay))
        self._pending_seek = (serial, int(frame), autoplay)
        return serial

    def apply_seek(self, *, phase="prepared"):
        if self._pending_seek is None:
            raise AssertionError("no seek is pending")
        serial, frame, autoplay = self._pending_seek
        self._pending_seek = None
        self.media_frame = frame
        self.graph.position_frame = frame
        self.last_seek = {
            "seek_id": serial,
            "frame": frame,
            "autoplay": autoplay,
            "phase": phase,
        }

    def set_parameters(self, request):
        self.control_threads.append(threading.get_ident())
        self.parameter_accepted += 1
        self._pending_parameter = (
            self.parameter_accepted, copy.deepcopy(dict(request)),
        )
        return self.parameter_accepted

    def apply_parameters(self):
        if self._pending_parameter is None:
            raise AssertionError("no parameters are pending")
        revision, request = self._pending_parameter
        self._pending_parameter = None
        self.apply_requests.append((revision, request))
        self.parameter_revision_applied = revision

    def play(self):
        self.control_threads.append(threading.get_ident())
        self.play_calls += 1

    def pause(self):
        self.control_threads.append(threading.get_ident())
        self.pause_calls += 1

    def stop(self):
        self.control_threads.append(threading.get_ident())
        self.stop_calls += 1
        return self.seek(0, autoplay=False)

    def snapshot(self):
        return copy.deepcopy({
            "state": "paused",
            "native": {
                "backend": "test",
                "epoch": 1,
                "media_frame": self.media_frame,
                "queued_frames": 0,
            },
            "renderer_frame": self.graph.position_frame,
            "last_seek": copy.deepcopy(self.last_seek),
            "parameter_revision": self.parameter_revision_applied,
            "play_intent": False,
            "error": None,
            "render_ms": copy.deepcopy(self.render_ms),
        })

    def close(self, timeout=5.0):
        self.closed += 1
        self.graph.close()


class SessionFactory:
    def __init__(self):
        self.created = []

    def __call__(self, graph, output_factory, *, block_frames, silent):
        session = FakeSession(
            graph, output_factory, block_frames=block_frames, silent=silent,
        )
        self.created.append(session)
        return session


class _GraphRenderer:
    sample_rate = 8000
    channels = 2
    duration_frames = 4000

    def __init__(self):
        self.position_frame = 0
        self.close_count = 0
        self.volume = 1.0

    def render(self, frames):
        import numpy as np
        result = np.zeros((int(frames), self.channels), dtype=np.float32)
        self.position_frame += int(frames)
        return result

    def telemetry_for_range(self, start, end):
        return {
            'schema': 'vwa.audio_mixdown.realtime-telemetry/v1',
            'source': 'realtime_dsp',
            'frame_start': int(start), 'frame_end': int(end),
            'frame_count': int(end) - int(start),
            'sample_rate': self.sample_rate, 'channels': self.channels,
            'tracks': [{'role': 'backing', 'index': 0,
                        'peak_dbfs': -144.0, 'rms_dbfs': -144.0,
                        'gr_db': 0.0, 'audible': True}],
            'bus': {'peak_dbfs': -144.0, 'rms_dbfs': -144.0,
                    'limiter_gr_db': 0.0,
                    'pre_limiter': {'peak_dbfs': -144.0, 'rms_dbfs': -144.0},
                    'post_limiter': {'peak_dbfs': -144.0, 'rms_dbfs': -144.0},
                    'final_output': {'peak_dbfs': -144.0, 'rms_dbfs': -144.0}},
            'dynamics': {},
            'spectrum': {'frequency_hz': [], 'before_dbfs': [], 'after_dbfs': [],
                         'source': 'dsp_fft_pre_post_eq', 'bins': 0,
                         'window_start_frame': None, 'window_end_frame': None,
                         'interval_ms': 100.0, 'fft_size': 256},
            'reverb': {'enabled': False, 'mix': 0.0,
                       'live': {}, 'kernel_edc': {'available': False,
                                                 'measured': False}},
        }

    def seek(self, frame, preroll_frames=None):
        self.position_frame = int(frame)

    def prepare_parameters(self, request):
        return copy.deepcopy(dict(request))

    def apply_parameters(self, request):
        return None

    def set_parameters(self, request):
        return None

    def set_volume(self, volume):
        self.volume = float(volume) / 100.0

    def close(self):
        self.close_count += 1


class _GraphLimiter:
    def __init__(self, *_args, **_kwargs):
        self.latency_frames = 0
        self.frames_received = 0
        self.frames_emitted = 0
        self.close_count = 0

    def process(self, source):
        import numpy as np
        self.frames_received += len(source)
        self.frames_emitted += len(source)
        return np.ascontiguousarray(source, dtype=np.float32)

    def finish(self):
        import numpy as np
        return np.empty((0, 2), dtype=np.float32)

    def reset(self):
        return None

    def close(self):
        self.close_count += 1


class _GraphOutput:
    def __init__(self, sample_rate=8000, channels=2):
        self.rate = int(sample_rate)
        self.channel_count = int(channels)
        self.epoch = 1
        self.media_frame = 0
        self.tail = 0
        self.queued = []
        self.playing = False
        self.closed = 0

    def snapshot(self):
        return {
            "backend": "test_sink",
            "sample_rate": self.rate,
            "channels": self.channel_count,
            "capacity_frames": 2048,
            "period_frames": 32,
            "epoch": self.epoch,
            "media_frame": self.media_frame,
            "queued_frames": len(self.queued),
            "device_error": None,
        }

    def write(self, samples, *, epoch, start_frame):
        if int(epoch) != self.epoch:
            raise AssertionError("stale output epoch")
        if int(start_frame) != self.tail:
            raise AssertionError("non-contiguous output write")
        count = min(len(samples), 2048 - len(self.queued))
        self.queued.extend(samples[:count].copy())
        self.tail += count
        return count

    def seek(self, *, frame, epoch):
        self.queued.clear()
        self.epoch = int(epoch)
        self.media_frame = int(frame)
        self.tail = int(frame)

    def set_playing(self, playing):
        self.playing = bool(playing)

    def close(self):
        self.closed += 1

class RealtimeServerTests(unittest.TestCase):
    def make_server(self, *, graph_factory=None, session_factory=None, output_factory_factory=None, silent=False):
        events = []
        graphs = graph_factory or GraphFactory()
        sessions = session_factory or SessionFactory()
        server = _RealtimeWorkerServer(
            graph_factory=graphs,
            session_factory=sessions,
            output_factory_factory=(
                output_factory_factory
                or (lambda _graph, _spec: (lambda: object()))
            ),
            ffmpeg_resolver=lambda: Path("ffmpeg-test.exe"),
            silent_probe=silent,
            event_sink=events.append,
            tick_seconds=0.005,
        )
        server.start()

        def cleanup():
            server._request_stop()
            for thread in (server._worker_thread, server._ticker_thread):
                if thread is not threading.current_thread():
                    thread.join(timeout=2.0)

        self.addCleanup(cleanup)
        return server, events, graphs, sessions

    def open_command(self, generation, *, source="normal", position_ms=0, autoplay=False):
        return {
            "cmd": "open",
            "request_id": generation,
            "request": {"source": source, "vocals": [], "instrumental_path": ""},
            "cache_dir": str(APP / "cache"),
            "dll_path": str(APP / "vwa_output.dll"),
            "position_ms": position_ms,
            "autoplay": autoplay,
            "volume": 72,
        }

    def test_actual_graph_and_session_no_device_baseline(self):
        try:
            import numpy as np
            import mixdown_realtime_graph as graph_module
            import mixdown_realtime_session as session_module
        except ImportError as error:
            self.skipTest("realtime graph dependencies unavailable: " + str(error))

        outputs = []
        sessions = []

        def graph_factory(request, cache_dir, dll_path, *, ffmpeg=None):
            return graph_module.LimitedMixSession(
                request,
                cache_dir,
                dll_path,
                ffmpeg=ffmpeg,
                renderer_factory=lambda *_args, **_kwargs: _GraphRenderer(),
                limiter_factory=lambda *_args, **_kwargs: _GraphLimiter(),
            )

        def output_factory_factory(graph, _spec):
            def create_output():
                output = _GraphOutput(graph.sample_rate, graph.channels)
                outputs.append(output)
                return output
            return create_output

        def session_factory(graph, output_factory, *, block_frames, silent):
            session = session_module.RealtimeAuditionSession(
                graph,
                output_factory,
                block_frames=block_frames,
                silent=True,
                numpy=np,
            )
            sessions.append(session)
            return session

        server, events, _graphs, _sessions = self.make_server(
            graph_factory=graph_factory,
            session_factory=session_factory,
            output_factory_factory=output_factory_factory,
            silent=True,
        )
        generation = "actual-graph-generation"
        server.handle_command(self.open_command(generation))
        ready = wait_event(events, lambda item: (
            item.get("type") == "live_ready"
            and item.get("request_id") == generation
        ))
        self.assertEqual(
            (ready["sample_rate"], ready["channels"], ready["duration_frames"]),
            (8000, 2, 4000),
        )
        state = wait_state(
            events, generation,
            lambda snapshot: (
                snapshot.get("sample_rate") == 8000
                and snapshot.get("duration_frames") == 4000
                and isinstance(snapshot.get("native"), dict)
            ),
        )
        self.assertEqual(state["generation"], generation)
        self.assertEqual(state["telemetry"]["schema"],
                         "vwa.audio_mixdown.realtime-telemetry/v1")
        self.assertEqual(state["telemetry"]["status"], "not_submitted")
        self.assertEqual(state["telemetry"]["media_frame"], 0)

        seek_id = "actual-graph-seek"
        server.handle_command({
            "cmd": "seek",
            "request_id": generation,
            "control_id": seek_id,
            "position_ms": 250,
            "autoplay": False,
        })
        seeked = wait_state(
            events, generation,
            lambda snapshot: (
                snapshot.get("seek_control_id") == seek_id
                and snapshot.get("media_frame") is not None
                and (snapshot.get("last_seek") or {}).get("phase") in ("queued", "submitted", "ended")
            ),
        )
        self.assertEqual(seeked["media_frame"], 2000)
        self.assertEqual(seeked["telemetry"]["status"], "not_submitted")
        self.assertEqual(seeked["telemetry"]["media_frame"], 2000)
        parameter_id = "actual-graph-parameters"
        server.handle_command({
            "cmd": "parameters",
            "request_id": generation,
            "control_id": parameter_id,
            "request": {"bypass": True},
        })
        wait_state(
            events, generation,
            lambda snapshot: snapshot.get("parameter_control_id") == parameter_id,
        )
        server.handle_command({
            "cmd": "pause",
            "request_id": generation,
            "control_id": "actual-graph-pause",
        })
        eof_id = "actual-graph-eof-seek"
        server.handle_command({
            "cmd": "seek",
            "request_id": generation,
            "control_id": eof_id,
            "position_ms": 500,
            "autoplay": False,
        })
        ended = wait_state(
            events, generation,
            lambda snapshot: (
                snapshot.get("seek_control_id") == eof_id
                and snapshot.get("seek_phase") == "ended"
            ),
        )
        self.assertEqual(ended["media_frame"], 4000)
        server.handle_command({
            "cmd": "release",
            "request_id": generation,
            "control_id": "actual-graph-release",
        })
        wait_event(events, lambda item: item.get("type") == "live_released")
        wait_until(lambda: outputs and outputs[0].closed == 1)
        self.assertEqual(sessions[0].snapshot().get("state"), "closed")

    def test_live_contract_control_ownership_and_release(self):
        server, events, graphs, sessions = self.make_server()
        generation = "gen-gui-1"
        server.handle_command(self.open_command(generation))
        ready = wait_event(events, lambda item: (
            item.get("type") == "live_ready" and item.get("request_id") == generation
        ))
        self.assertEqual(ready["sample_rate"], 8000)
        self.assertEqual(ready["channels"], 2)
        self.assertEqual(ready["duration_frames"], 8000)
        self.assertNotIn("audio_file", ready)
        first_state = wait_event(events, lambda item: (
            item.get("type") == "live_state" and item.get("request_id") == generation
        ))
        self.assertEqual(first_state["snapshot"]["sample_rate"], 8000)
        self.assertEqual(first_state["snapshot"]["duration_frames"], 8000)
        wait_until(lambda: sessions.created, message="session was not created")
        session = sessions.created[-1]
        self.assertEqual(session.volume_calls[0], 72.0)
        session.render_ms = list(range(4096))
        telemetry = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("render_ms_count") == 4096,
        )
        self.assertNotIn("render_ms", telemetry)
        self.assertEqual(telemetry["render_ms_max"], 4095.0)
        self.assertIsInstance(telemetry["render_ms_p95"], float)

        parameter_id = "parameter-control-1"
        server.handle_command({
            "cmd": "parameters",
            "request_id": generation,
            "control_id": parameter_id,
            "request": {"reverb_mix": 0.25},
        })
        wait_until(lambda: session.parameter_accepted == 1)
        # The map contains the accepted revision, but the snapshot still reports
        # revision zero. The GUI acknowledgement must follow the applied revision.
        accepted_state = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("parameter_revision") == 0,
        )
        self.assertIsNone(accepted_state["parameter_control_id"])
        session.apply_parameters()
        applied_state = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("parameter_revision") == 1,
        )
        self.assertEqual(applied_state["parameter_control_id"], parameter_id)

        seek_id = "seek-control-1"
        server.handle_command({
            "cmd": "seek",
            "request_id": generation,
            "control_id": seek_id,
            "position_ms": 500,
            "autoplay": False,
        })
        wait_until(lambda: session.seek_calls)
        self.assertEqual(session.seek_calls[-1][1], 4000)
        queued = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("seek_control_id") == seek_id,
        )
        self.assertEqual(queued["seek_phase"], "queued")
        self.assertIsNone(queued["media_frame"])
        self.assertIsNone(queued["native"]["media_frame"])
        self.assertEqual(queued["last_seek"]["frame"], 4000)
        session.apply_seek(phase="queued")
        applied_seek = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("media_frame") == 4000,
        )
        self.assertEqual(applied_seek["seek_control_id"], seek_id)
        self.assertEqual(applied_seek["seek_phase"], "queued")

        eof_seek_id = "seek-control-at-eof"
        server.handle_command({
            "cmd": "seek",
            "request_id": generation,
            "control_id": eof_seek_id,
            "position_ms": 1000,
            "autoplay": False,
        })
        wait_until(lambda: len(session.seek_calls) >= 2)
        session.apply_seek(phase="ended")
        ended_seek = wait_state(
            events, generation,
            lambda snapshot: snapshot.get("seek_phase") == "ended",
        )
        self.assertEqual(ended_seek["seek_control_id"], eof_seek_id)
        self.assertEqual(ended_seek["media_frame"], 8000)

        for command, control_id in (
            ({"cmd": "play"}, "play-control"),
            ({"cmd": "pause"}, "pause-control"),
            ({"cmd": "set_volume", "volume": 55}, "volume-control"),
            ({"cmd": "stop"}, "stop-control"),
        ):
            command.update(request_id=generation, control_id=control_id)
            server.handle_command(command)
        wait_until(lambda: session.stop_calls == 1)
        self.assertEqual((session.play_calls, session.pause_calls), (1, 1))
        self.assertEqual(session.volume_calls[-1], 55.0)
        self.assertEqual(session.seek_calls[-1][1], 0)
        self.assertIs(session.seek_calls[-1][2], False)
        self.assertEqual(len(set(session.control_threads)), 1)

        calls_before_stale = len(session.seek_calls)
        server.handle_command({
            "cmd": "seek",
            "request_id": "stale-generation",
            "control_id": "stale-seek",
            "position_ms": 100,
            "autoplay": False,
        })
        wait_until(lambda: any(
            item.get("type") in ("stale_control", "error")
            and item.get("rejected_generation") == "stale-generation"
            for item in list(events)
        ))
        time.sleep(0.03)
        self.assertEqual(len(session.seek_calls), calls_before_stale)
        for event in list(events):
            if event.get("request_id") is not None:
                self.assertEqual(event["request_id"], generation)

        release_id = "release-control-1"
        server.handle_command({
            "cmd": "release",
            "request_id": generation,
            "control_id": release_id,
        })
        released = wait_event(events, lambda item: item.get("type") == "live_released")
        self.assertEqual(released["request_id"], generation)
        self.assertEqual(released["control_id"], release_id)
        self.assertGreaterEqual(session.closed, 1)
        self.assertGreaterEqual(session.graph.close_count, 1)
        self.assertTrue(server._worker_thread.is_alive())
        self.assertTrue(server._ticker_thread.is_alive())

        replacement = "gen-gui-2"
        server.handle_command(self.open_command(replacement, source="replacement"))
        wait_event(events, lambda item: (
            item.get("type") == "live_ready" and item.get("request_id") == replacement
        ))
        self.assertEqual(len(sessions.created), 2)
        self.assertEqual(sessions.created[0].closed, 1)

    def test_latest_open_discards_slow_preparation_before_start(self):
        graphs = GraphFactory()
        sessions = SessionFactory()
        server, events, _graphs, _sessions = self.make_server(
            graph_factory=graphs, session_factory=sessions,
        )
        server.handle_command(self.open_command("slow-generation", source="slow"))
        self.assertTrue(graphs.slow_entered.wait(2.0))
        server.handle_command(self.open_command("latest-generation", source="latest"))
        graphs.slow_release.set()
        wait_event(events, lambda item: (
            item.get("type") == "live_ready"
            and item.get("request_id") == "latest-generation"
        ))
        self.assertFalse(any(
            item.get("type") == "live_ready"
            and item.get("request_id") == "slow-generation"
            for item in list(events)
        ))
        slow_graph = next(
            graph for graph in graphs.created
            if graph.request.get("source") == "slow"
        )
        self.assertEqual(slow_graph.close_count, 1)
        self.assertEqual(len(sessions.created), 1)
        self.assertEqual(
            sessions.created[0].graph.request.get("source"), "latest",
        )
        server.handle_command({
            "cmd": "seek",
            "request_id": "slow-generation",
            "control_id": "stale-control-after-open",
            "position_ms": 100,
        })
        time.sleep(0.03)
        self.assertEqual(sessions.created[0].seek_calls, [])

    def test_control_queue_is_bounded_and_coalesces_latest_values(self):
        events = []
        server = _RealtimeWorkerServer(
            graph_factory=GraphFactory(),
            session_factory=SessionFactory(),
            output_factory_factory=lambda _graph, _spec: (lambda: object()),
            event_sink=events.append,
            tick_seconds=0.05,
        )
        server.handle_command(self.open_command("queued-generation"))
        for index in range(100):
            server.handle_command({
                "cmd": "parameters",
                "request_id": "queued-generation",
                "control_id": "parameter-%d" % index,
                "request": {"value": index},
            })
            server.handle_command({
                "cmd": "seek",
                "request_id": "queued-generation",
                "control_id": "seek-%d" % index,
                "position_ms": index,
            })
        with server._condition:
            queued = list(server._controls)
        self.assertLessEqual(len(queued), MAX_CONTROL_COMMANDS)
        self.assertEqual([item.control_id for item in queued if item.command == "parameters"],
                         ["parameter-99"])
        self.assertEqual([item.control_id for item in queued if item.command in ("seek", "stop")],
                         ["seek-99"])

        server.handle_command({
            "cmd": "play",
            "request_id": "queued-generation",
            "control_id": "play-ordered",
        })
        server.handle_command({
            "cmd": "pause",
            "request_id": "queued-generation",
            "control_id": "pause-ordered",
        })
        server.handle_command({
            "cmd": "play",
            "request_id": "queued-generation",
            "control_id": "play-final",
        })
        with server._condition:
            ordered = [item.control_id for item in server._controls]
        self.assertLess(
            ordered.index("play-ordered"), ordered.index("pause-ordered"),
        )
        self.assertLess(
            ordered.index("pause-ordered"), ordered.index("play-final"),
        )

    def test_open_position_and_autoplay_use_serialized_seek_intent(self):
        server, events, _graphs, sessions = self.make_server()
        server.handle_command(self.open_command(
            "position-generation", position_ms=125, autoplay=True,
        ))
        wait_event(events, lambda item: (
            item.get("type") == "live_ready"
            and item.get("request_id") == "position-generation"
        ))
        wait_until(lambda: sessions.created)
        session = sessions.created[-1]
        self.assertEqual(session.seek_calls[0][1], 1000)
        self.assertIs(session.seek_calls[0][2], True)
        queued = wait_state(
            events, "position-generation",
            lambda snapshot: snapshot.get("media_frame") is None,
        )
        self.assertEqual(queued["seek_phase"], "queued")
        self.assertEqual(queued["last_seek"]["frame"], 1000)
        session.apply_seek()
        applied = wait_state(
            events, "position-generation",
            lambda snapshot: snapshot.get("media_frame") is None,
        )
        self.assertIsNone(applied["seek_control_id"])

        server.handle_command({
            "cmd": "release",
            "request_id": "position-generation",
            "control_id": "release-position",
        })
        wait_event(events, lambda item: item.get("type") == "live_released")

        server.handle_command(self.open_command(
            "autoplay-zero-generation", position_ms=0, autoplay=True,
        ))
        wait_event(events, lambda item: (
            item.get("type") == "live_ready"
            and item.get("request_id") == "autoplay-zero-generation"
        ))
        wait_until(lambda: len(sessions.created) == 2)
        self.assertEqual(sessions.created[-1].seek_calls[0][1:], (0, True))

    def test_runtime_error_closes_graph_and_requires_explicit_new_open(self):
        graphs = GraphFactory()
        graphs.fail_sources.add("fail")
        server, events, _graphs, sessions = self.make_server(graph_factory=graphs)
        server.handle_command(self.open_command("failed-generation", source="fail"))
        error = wait_event(events, lambda item: (
            item.get("type") == "error"
            and item.get("request_id") == "failed-generation"
        ))
        self.assertEqual(error["generation"], "failed-generation")
        self.assertFalse(error["auto_restart"])
        self.assertFalse(sessions.created)
        failed_graph = next(
            graph for graph in graphs.created
            if graph.request.get("source") == "fail"
        )
        self.assertGreaterEqual(failed_graph.close_count, 1)
        time.sleep(0.03)
        self.assertEqual(sum(
            item.get("type") == "error"
            and item.get("request_id") == "failed-generation"
            for item in list(events)
        ), 1)

        server.handle_command(self.open_command("retry-generation"))
        wait_event(events, lambda item: (
            item.get("type") == "live_ready"
            and item.get("request_id") == "retry-generation"
        ))
        self.assertEqual(len(sessions.created), 1)

    def test_session_start_error_closes_output_owner_and_graph(self):
        session_factory = SessionFactory()
        server, events, _graphs, sessions = self.make_server(
            session_factory=session_factory,
        )
        server.handle_command(self.open_command(
            "start-failed-generation", source="session-start-fail",
        ))
        error = wait_event(events, lambda item: (
            item.get("type") == "error"
            and item.get("request_id") == "start-failed-generation"
        ))
        self.assertIn("start failed", error["message"])
        session = sessions.created[0]
        self.assertGreaterEqual(session.closed, 1)
        self.assertGreaterEqual(session.graph.close_count, 1)
        self.assertFalse(any(
            item.get("type") == "live_ready"
            and item.get("request_id") == "start-failed-generation"
            for item in list(events)
        ))

    def test_shutdown_closes_current_session_and_graph(self):
        server, events, _graphs, sessions = self.make_server()
        server.handle_command(self.open_command("shutdown-generation"))
        wait_event(events, lambda item: item.get("type") == "live_ready")
        session = sessions.created[-1]
        server._request_stop()
        server._worker_thread.join(2.0)
        server._ticker_thread.join(1.0)
        self.assertFalse(server._worker_thread.is_alive())
        self.assertGreaterEqual(session.closed, 1)
        self.assertGreaterEqual(session.graph.close_count, 1)


class RecordingRealtimeService(MixdownRealtimeService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent = []
        self._record_lock = threading.Lock()

    def _send(self, command):
        with self._record_lock:
            self.sent.append(copy.deepcopy(dict(command)))


class BlockingRealtimeService(RecordingRealtimeService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seek_entered = threading.Event()
        self.release_seek = threading.Event()
        self._blocked = False

    def _send(self, command):
        should_block = (
            command.get("cmd") == "seek"
            and not self._blocked
        )
        if should_block:
            self._blocked = True
            with self._record_lock:
                self.sent.append(copy.deepcopy(dict(command)))
            self.seek_entered.set()
            if not self.release_seek.wait(3.0):
                raise AssertionError("blocked seek was not released")
            return
        super()._send(command)


class ClientTests(unittest.TestCase):
    def test_client_signatures_generation_ownership_and_release(self):
        service = RecordingRealtimeService(cache_dir=str(APP / "cache"))
        generation = service.open(
            {"source": "test"},
            dll_path=str(APP / "vwa_output.dll"),
            position_ms=125,
            autoplay=True,
            volume=66,
            request_id="preassigned-gui-generation",
        )
        self.assertEqual(generation, "preassigned-gui-generation")
        parameter_id = service.parameters({"reverb_mix": 0.2})
        seek_id = service.seek(250, False)
        play_id = service.play()
        pause_id = service.pause()
        volume_id = service.set_volume(44)
        stop_id = service.stop()
        release_id = service.release()
        self.assertTrue(all(isinstance(value, str) and value for value in (
            parameter_id, seek_id, play_id, pause_id, volume_id, stop_id, release_id,
        )))
        self.assertEqual([item["cmd"] for item in service.sent], [
            "open", "parameters", "seek", "play", "pause", "set_volume",
            "stop", "release",
        ])
        self.assertTrue(all(
            item["request_id"] == generation for item in service.sent
        ))
        self.assertEqual(service.sent[0]["cache_dir"], str(APP / "cache"))
        self.assertEqual(service.sent[0]["position_ms"], 125.0)
        self.assertIs(service.sent[0]["autoplay"], True)

    def test_open_waits_for_inflight_control_generation_boundary(self):
        service = BlockingRealtimeService(cache_dir=str(APP / "cache"))
        service.open(
            {"source": "old"},
            dll_path=str(APP / "vwa_output.dll"),
            request_id="gen-old",
        )
        seek_thread = threading.Thread(target=lambda: service.seek(10), daemon=True)
        seek_thread.start()
        self.assertTrue(service.seek_entered.wait(1.0))
        open_thread = threading.Thread(
            target=lambda: service.open(
                {"source": "new"},
                dll_path=str(APP / "vwa_output.dll"),
                request_id="gen-new",
            ),
            daemon=True,
        )
        open_thread.start()
        time.sleep(0.03)
        self.assertTrue(open_thread.is_alive())
        service.release_seek.set()
        seek_thread.join(2.0)
        open_thread.join(2.0)
        self.assertFalse(any(
            item.get("cmd") == "seek" and item.get("request_id") == "gen-old"
            for item in service.sent[service.sent.index(next(
                item for item in service.sent
                if item.get("cmd") == "open" and item.get("request_id") == "gen-new"
            )) + 1:]
        ))

    def test_live_telemetry_is_single_slot_without_reordering_lifecycle(self):
        service = RecordingRealtimeService(cache_dir=str(APP / "cache"))
        generation = service.open(
            {"source": "telemetry"},
            dll_path=str(APP / "vwa_output.dll"),
            request_id="telemetry-generation",
        )
        for index in range(200):
            service._receive_event({
                "type": "live_state",
                "request_id": generation,
                "snapshot": {"state": "playing", "tick": index},
            })
        service._receive_event({
            "type": "live_ready",
            "request_id": generation,
            "sample_rate": 8000,
            "channels": 2,
            "duration_frames": 8000,
        })
        service._receive_event({
            "type": "error",
            "request_id": generation,
            "generation": generation,
            "message": "late failure",
        })
        self.assertEqual(service._events.qsize(), 2)
        with service._live_state_lock:
            self.assertEqual(service._live_state["snapshot"]["tick"], 199)
        events = service.poll(timeout=0, max_events=64)
        self.assertEqual(
            [event["type"] for event in events],
            ["live_ready", "error", "live_state"],
        )
        self.assertEqual(events[-1]["snapshot"]["tick"], 199)
        service._receive_event({
            "type": "live_state",
            "request_id": "telemetry-generation",
            "snapshot": {"state": "playing", "tick": 200},
        })
        service.open(
            {"source": "replacement"},
            dll_path=str(APP / "vwa_output.dll"),
            request_id="replacement-generation",
        )
        old_state = {
            "type": "live_state",
            "request_id": "telemetry-generation",
            "snapshot": {"state": "playing", "tick": 201},
        }
        service._receive_event(old_state)
        with service._live_state_lock:
            self.assertIsNone(service._live_state)
    def test_realtime_silent_probe_is_explicit_environment_opt_in(self):
        with mock.patch.dict(os.environ, {"VWA_REALTIME_SILENT_PROBE": "0"}):
            self.assertFalse(_silent_probe_enabled())
            self.assertFalse(_RealtimeWorkerServer()._silent_probe)
        with mock.patch.dict(os.environ, {"VWA_REALTIME_SILENT_PROBE": "1"}):
            self.assertTrue(_silent_probe_enabled())
            self.assertTrue(_RealtimeWorkerServer()._silent_probe)


class MockTransportService(MixdownRealtimeService):
    worker_module = "mixdown_realtime_mock_transport_worker"


class SubprocessTransportTests(unittest.TestCase):
    def test_shared_transport_bootstrap_uses_subclass_worker_module(self):
        with tempfile.TemporaryDirectory(dir=str(APP / "work")) as directory:
            root = Path(directory)
            module_name = "mixdown_realtime_mock_transport_worker_%d" % os.getpid()
            script = textwrap.dedent('''
                import json
                import os
                import sys
                import time

                def emit(value):
                    payload = dict(value, pid=os.getpid(), ts=time.time())
                    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
                    sys.stdout.flush()

                def _worker_main():
                    emit({"type": "ready"})
                    current = None
                    for line in sys.stdin:
                        command = json.loads(line)
                        name = command.get("cmd")
                        if name == "shutdown":
                            emit({"type": "shutdown", "status": "ok"})
                            return 0
                        if name == "open":
                            current = command["request_id"]
                            emit({
                                "type": "live_ready",
                                "request_id": current,
                                "sample_rate": 8000,
                                "channels": 2,
                                "duration_frames": 4000,
                            })
                            emit({
                                "type": "live_state",
                                "request_id": current,
                                "snapshot": {
                                    "state": "paused",
                                    "sample_rate": 8000,
                                    "channels": 2,
                                    "duration_frames": 4000,
                                    "media_frame": 0,
                                },
                            })
                        elif name == "release":
                            emit({
                                "type": "live_released",
                                "request_id": current,
                                "control_id": command["control_id"],
                            })
                        else:
                            emit({
                                "type": "control_accepted",
                                "request_id": current,
                                "control_id": command.get("control_id"),
                                "cmd": name,
                            })
                    return 0
            ''')
            (root / (module_name + ".py")).write_text(script, encoding="utf-8")
            service = type(
                "MockSubprocessClient",
                (MixdownRealtimeService,),
                {"_WORKER_MODULE": module_name},
            )(
                cache_dir=str(root),
                extra_paths=(root,),
                startup_timeout=5.0,
                shutdown_timeout=3.0,
            )
            self.addCleanup(service.close, 1.0)
            ready = service.start()
            self.assertEqual(ready["type"], "ready")
            pid = service.pid
            generation = service.open(
                {"source": "mock"},
                dll_path=str(root / "mock.dll"),
                request_id="preassigned-subprocess-generation",
            )
            received = []

            def collect(predicate, message):
                def check():
                    received.extend(service.poll())
                    return next((item for item in received if predicate(item)), None)
                return wait_until(check, message=message)

            live_ready = collect(
                lambda item: (
                    item.get("type") == "live_ready"
                    and item.get("request_id") == generation
                ),
                "subprocess live_ready was not received",
            )
            self.assertEqual(live_ready["sample_rate"], 8000)
            state = collect(
                lambda item: (
                    item.get("type") == "live_state"
                    and item.get("request_id") == generation
                ),
                "subprocess live_state was not received",
            )
            self.assertEqual(state["snapshot"]["duration_frames"], 4000)
            service.set_volume(33)
            service.release()
            released = wait_until(
                lambda: next((item for item in service.poll() if (
                    item.get("type") == "live_released"
                    and item.get("request_id") == generation
                )), None),
                message="subprocess release was not received",
            )
            self.assertEqual(released["request_id"], generation)
            self.assertEqual(service.pid, pid)
            self.assertTrue(service.is_alive)
            self.assertEqual(service.close(timeout=3.0), 0)
            self.assertFalse(service.is_alive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
