"""Resident realtime mixdown session service.

The UI process only owns the JSONL client in :class:`MixdownRealtimeService`.
The resident worker prepares source media, creates the limited graph, owns the
native output on the DSP worker, and publishes coalesced live snapshots. A
worker error closes that graph/output and never starts a replacement session;
retry is an explicit new ``open`` generation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import copy
import importlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from mixdown_transport import (
    AuditionServiceError,
    ResidentProcessClient,
    PROTOCOL_VERSION,
    ServiceUnavailable,
    TICK_HZ,
    _json_line,
)


REALTIME_SERVICE_SCHEMA = "vwa.audio_mixdown.realtime-service/v1"
MAX_CONTROL_COMMANDS = 64
BLOCK_FRAMES = 512
LIVE_TICK_HZ = TICK_HZ
LIVE_TICK_SECONDS = 1.0 / LIVE_TICK_HZ


class RealtimeServiceError(AuditionServiceError):
    """A realtime session or protocol failure."""


class _StaleControlError(ValueError):
    """A control addressed a generation that is no longer current."""


def _silent_probe_enabled() -> bool:
    """Return the opt-in mode used only for unattended device probes."""
    return os.environ.get("VWA_REALTIME_SILENT_PROBE") == "1"


class MixdownRealtimeService(ResidentProcessClient):
    """Nonblocking client for one resident realtime mixdown worker."""

    _WORKER_MODULE = "mixdown_realtime_service"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._generation_lock = threading.RLock()
        self._generation: str | None = None
        self._control_counter = 0
        self._deferred_events: deque[dict[str, Any]] = deque()
        self._poll_lock = threading.Lock()
        self._live_state_lock = threading.Lock()
        self._live_state: dict[str, Any] | None = None

    @property
    def generation(self) -> str | None:
        with self._generation_lock:
            return self._generation

    def _new_request_id(self) -> str:
        with self._generation_lock:
            self._request_counter += 1
            return "realtime-%d-%d-%s" % (
                os.getpid(), self._request_counter, uuid.uuid4().hex[:8],
            )

    def _send_control(self, command: Mapping[str, Any]) -> str:
        """Create and send one control under the same generation ownership lock."""
        with self._generation_lock:
            generation = self._generation
            if not generation:
                raise ServiceUnavailable("实时试听当前没有打开的会话。")
            self._control_counter += 1
            control_id = "realtime-control-%d-%d-%s" % (
                os.getpid(), self._control_counter, uuid.uuid4().hex[:8],
            )
            payload = dict(command)
            payload["request_id"] = generation
            payload["control_id"] = control_id
            self._send(payload)
            return control_id

    def open(
            self,
            request: Mapping[str, Any],
            *,
            dll_path: str | os.PathLike[str],
            position_ms: float = 0,
            autoplay: bool = False,
            volume: float = 72,
            request_id: str | None = None) -> str:
        """Start a new generation without waiting for source preparation."""
        if not isinstance(request, Mapping):
            raise TypeError("request 必须是映射。")
        if self.cache_dir is None:
            raise ValueError("open 需要服务级 cache_dir。")
        position_value = _nonnegative_number(position_ms, "position_ms")
        volume_value = _volume_number(volume)
        if type(autoplay) is not bool:
            raise TypeError("autoplay 必须是布尔值。")
        try:
            dll_value = os.fspath(dll_path)
        except TypeError as error:
            raise TypeError("dll_path 必须是文件系统路径。") from error
        if not dll_value:
            raise ValueError("dll_path 不能为空。")

        with self._generation_lock:
            identifier = str(request_id or self._new_request_id()).strip()
            if not identifier:
                raise ValueError("request_id 不能为空。")
            previous = self._generation
            self._generation = identifier
            with self._live_state_lock:
                self._live_state = None
            try:
                self._send({
                    "cmd": "open",
                    "request_id": identifier,
                    "request": copy.deepcopy(dict(request)),
                    "cache_dir": self.cache_dir,
                    "dll_path": dll_value,
                    "position_ms": position_value,
                    "autoplay": autoplay,
                    "volume": volume_value,
                })
            except Exception:
                self._generation = previous
                raise
            return identifier

    def parameters(self, request: Mapping[str, Any]) -> str:
        if not isinstance(request, Mapping):
            raise TypeError("request 必须是映射。")
        return self._send_control({
            "cmd": "parameters",
            "request": copy.deepcopy(dict(request)),
        })

    def seek(self, position_ms: float, autoplay: bool | None = None) -> str:
        position_value = _nonnegative_number(position_ms, "position_ms")
        if autoplay is not None and type(autoplay) is not bool:
            raise TypeError("autoplay 必须是布尔值或 None。")
        return self._send_control({
            "cmd": "seek",
            "position_ms": position_value,
            "autoplay": autoplay,
        })

    def play(self) -> str:
        return self._send_control({"cmd": "play"})

    def pause(self) -> str:
        return self._send_control({"cmd": "pause"})

    def stop(self) -> str:
        """Seek to zero and pause through the session's serialized control path."""
        return self._send_control({"cmd": "stop"})

    def set_volume(self, volume: float) -> str:
        return self._send_control({
            "cmd": "set_volume",
            "volume": _volume_number(volume),
        })

    def release(self) -> str:
        """Close the current graph/output while retaining the resident process."""
        return self._send_control({"cmd": "release"})

    def _receive_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "live_state":
            super()._receive_event(event)
            return
        with self._generation_lock:
            generation = self._generation
            event_generation = event.get("request_id")
            if (generation is None or event_generation is None
                    or str(event_generation) != generation):
                return
            with self._live_state_lock:
                self._live_state = dict(event)

    def poll(
            self,
            *,
            timeout: float = 0.0,
            max_events: int | None = None) -> list[dict[str, Any]]:
        """Return current-generation events with latest live states coalesced."""
        with self._poll_lock:
            if self._deferred_events:
                events = list(self._deferred_events)
                self._deferred_events.clear()
                events.extend(super().poll(timeout=0.0))
            else:
                events = super().poll(timeout=timeout)
            with self._generation_lock:
                generation = self._generation
                with self._live_state_lock:
                    last_state = self._live_state
                    self._live_state = None
            if last_state is not None:
                events.append(last_state)
            selected: list[dict[str, Any]] = []
            state_index: dict[str, int] = {}
            for event in events:
                event_generation = event.get("request_id")
                if (generation and event_generation is not None
                        and str(event_generation) != generation):
                    continue
                if event.get("type") == "live_state" and event_generation is not None:
                    key = str(event_generation)
                    if key in state_index:
                        selected[state_index[key]] = event
                        continue
                    state_index[key] = len(selected)
                selected.append(event)
            if max_events is None:
                return selected
            try:
                limit = max(0, int(max_events))
            except (TypeError, ValueError):
                raise TypeError("max_events 必须是非负整数或 None。") from None
            self._deferred_events.extend(selected[limit:])
            return selected[:limit]

    def close(self, timeout: float | None = None) -> int | None:
        returncode = super().close(timeout=timeout)
        with self._generation_lock:
            self._generation = None
            with self._live_state_lock:
                self._live_state = None
        return returncode


def _nonnegative_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(name + " 必须是数字。")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(name + " 必须是有限非负数。")
    return result


def _volume_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("volume 必须是数字。")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError("volume 必须介于 0 和 100。")
    return result


@dataclass(frozen=True)
class _OpenSpec:
    generation: str
    request: dict[str, Any]
    cache_dir: str
    dll_path: str
    position_ms: float
    autoplay: bool
    volume: float


@dataclass(frozen=True)
class _PendingControl:
    generation: str
    command: str
    control_id: str
    payload: dict[str, Any]
    received_at: float


@dataclass(frozen=True)
class _SeekBinding:
    generation: str
    serial: int | None
    frame: int
    autoplay: bool | None
    control_id: str | None


_RUNTIME_GRAPH: Any = None
_RUNTIME_SESSION: Any = None
_RUNTIME_NATIVE_OUTPUT: Any = None


def _load_worker_runtime() -> None:
    """Load only the resident realtime runtime and its DSP dependencies."""
    import numpy  # noqa: F401 - loaded before graph construction
    import soundfile  # noqa: F401 - loaded before source preparation
    import audio_mixdown as backend

    backend._load_pedalboard()
    global _RUNTIME_GRAPH, _RUNTIME_SESSION, _RUNTIME_NATIVE_OUTPUT
    _RUNTIME_GRAPH = importlib.import_module("mixdown_realtime_graph")
    _RUNTIME_SESSION = importlib.import_module("mixdown_realtime_session")
    _RUNTIME_NATIVE_OUTPUT = importlib.import_module("mixdown_native_output")


def _resolve_worker_ffmpeg() -> Path:
    backend = importlib.import_module("audio_mixdown")
    return backend._ffmpeg_executable()

class _RealtimeWorkerServer:
    """Serial session owner plus a responsive, bounded JSONL command reader."""

    def __init__(
            self,
            *,
            graph_factory: Callable[..., Any] | None = None,
            session_factory: Callable[..., Any] | None = None,
            output_factory_factory: Callable[..., Callable[[], Any]] | None = None,
            ffmpeg_resolver: Callable[[], Path] | None = None,
            silent_probe: bool | None = None,
            event_sink: Callable[[dict[str, Any]], None] | None = None,
            tick_seconds: float = LIVE_TICK_SECONDS,
            block_frames: int = BLOCK_FRAMES) -> None:
        self._graph_factory = graph_factory
        self._session_factory = session_factory
        self._output_factory_factory = output_factory_factory
        self._ffmpeg_resolver = ffmpeg_resolver
        self._silent_probe = _silent_probe_enabled() if silent_probe is None else bool(silent_probe)
        self._event_sink = event_sink
        self._tick_seconds = max(0.001, float(tick_seconds))
        self._block_frames = int(block_frames)

        self._condition = threading.Condition(threading.RLock())
        self._output_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stop = False
        self._started = False
        self._pending_open: _OpenSpec | None = None
        self._controls: deque[_PendingControl] = deque()
        self._desired_generation: str | None = None
        self._accepting_controls = False
        self._seen_generations: deque[str] = deque(maxlen=256)
        self._session: Any = None
        self._graph: Any = None
        self._session_generation: str | None = None
        self._failure_pending: tuple[str, str, str] | None = None
        self._parameter_control_by_revision: dict[int, str] = {}
        self._seek_control_by_serial: dict[int, str] = {}
        self._latest_seek: _SeekBinding | None = None
        self._latest_control_id: str | None = None
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="mixdown-realtime-worker",
            daemon=True,
        )
        self._ticker_thread = threading.Thread(
            target=self._ticker_loop,
            name="mixdown-realtime-ticker",
            daemon=True,
        )

    def emit(self, event: Mapping[str, Any]) -> None:
        payload = {
            "schema": REALTIME_SERVICE_SCHEMA,
            "protocol": PROTOCOL_VERSION,
            "ts": time.time(),
        }
        payload.update(event)
        with self._output_lock:
            if self._event_sink is not None:
                self._event_sink(dict(payload))
                return
            try:
                sys.stdout.write(_json_line(payload))
                sys.stdout.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._stop_event.set()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            if self._stop:
                raise RealtimeServiceError("realtime worker is stopping")
            self._started = True
        self._worker_thread.start()
        self._ticker_thread.start()

    def run(self) -> int:
        self.start()
        self.emit({
            "type": "ready",
            "pid": os.getpid(),
            "tick_hz": LIVE_TICK_HZ,
            "service_schema": REALTIME_SERVICE_SCHEMA,
            "python": sys.version.split()[0],
            "silent_probe": self._silent_probe,
        })
        shutdown_requested = False
        try:
            for line in sys.stdin:
                text = line.strip()
                if not text:
                    continue
                try:
                    command = json.loads(text)
                except ValueError:
                    self._emit_protocol_error("command must be one JSON object per line")
                    continue
                if not isinstance(command, dict):
                    self._emit_protocol_error("command must be a JSON object")
                    continue
                if command.get("cmd") == "shutdown":
                    shutdown_requested = True
                    break
                self.handle_command(command)
        finally:
            self._request_stop()
            self._worker_thread.join(timeout=5.0)
            self._stop_event.set()
            self._ticker_thread.join(timeout=1.0)
            self.emit({
                "type": "shutdown",
                "status": "ok" if not self._worker_thread.is_alive() else "timeout",
                "worker_alive": self._worker_thread.is_alive(),
            })
        return 0 if not self._worker_thread.is_alive() else 3

    def _emit_protocol_error(self, message: str) -> None:
        with self._condition:
            generation = self._desired_generation
            self.emit({
                "type": "error",
                "request_id": generation,
                "generation": generation,
                "error_type": "ProtocolError",
                "message": message,
                "auto_restart": False,
            })

    def handle_command(self, command: Mapping[str, Any]) -> None:
        name = command.get("cmd")
        try:
            if name == "open":
                self._accept_open(command)
            elif name == "release":
                self._accept_release(command)
            elif name in ("parameters", "seek", "play", "pause", "stop", "set_volume"):
                self._accept_control(command, str(name))
            elif name == "ping":
                with self._condition:
                    generation = self._desired_generation
                    self.emit({
                        "type": "pong",
                        "request_id": generation,
                        "requested_generation": command.get("request_id"),
                    })
            else:
                raise ValueError("unknown command: %r" % name)
        except _StaleControlError:
            return
        except Exception as error:
            with self._condition:
                current = self._desired_generation
                self.emit({
                    "type": "error",
                    "request_id": current,
                    "generation": current,
                    "rejected_generation": command.get("request_id"),
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "auto_restart": False,
                })

    def _accept_open(self, command: Mapping[str, Any]) -> None:
        generation = command.get("request_id")
        if not isinstance(generation, str) or not generation.strip():
            raise ValueError("request_id must be a non-empty string")
        generation = generation.strip()
        request = command.get("request")
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object")
        cache_value = command.get("cache_dir")
        if not cache_value:
            raise ValueError("cache_dir is required")
        dll_value = command.get("dll_path")
        if not dll_value:
            raise ValueError("dll_path is required")
        position_ms = _nonnegative_number(command.get("position_ms", 0), "position_ms")
        autoplay = command.get("autoplay", False)
        if type(autoplay) is not bool:
            raise ValueError("autoplay must be a boolean")
        volume = _volume_number(command.get("volume", 72))
        spec = _OpenSpec(
            generation=generation,
            request=copy.deepcopy(dict(request)),
            cache_dir=str(Path(os.fspath(cache_value)).expanduser().resolve()),
            dll_path=str(Path(os.fspath(dll_value)).expanduser().resolve()),
            position_ms=position_ms,
            autoplay=autoplay,
            volume=volume,
        )
        replaced_generation = None
        with self._condition:
            if generation in self._seen_generations:
                raise ValueError("duplicate request_id: " + generation)
            self._seen_generations.append(generation)
            if self._pending_open is not None:
                replaced_generation = self._pending_open.generation
            self._pending_open = spec
            self._desired_generation = generation
            self._accepting_controls = True
            self._controls.clear()
            self._failure_pending = None
            self._parameter_control_by_revision.clear()
            self._seek_control_by_serial.clear()
            self._latest_seek = None
            self._latest_control_id = None
            self.emit({
                "type": "opening",
                "request_id": generation,
                "phase": "queued",
                "replaced_generation": replaced_generation,
            })
            self._condition.notify_all()

    def _accept_release(self, command: Mapping[str, Any]) -> None:
        generation, control_id = self._control_identity(command)
        with self._condition:
            self._validate_generation_locked(generation)
            if not self._accepting_controls:
                raise ValueError("the current generation is already released or failed")
            self._accepting_controls = False
            self._pending_open = None
            self._controls.clear()
            self._controls.append(_PendingControl(
                generation, "release", control_id, {}, time.monotonic(),
            ))
            self._latest_control_id = control_id
            self.emit({
                "type": "releasing",
                "request_id": generation,
                "control_id": control_id,
            })
            self._condition.notify_all()

    def _accept_control(self, command: Mapping[str, Any], name: str) -> None:
        generation, control_id = self._control_identity(command)
        payload: dict[str, Any] = {}
        if name == "parameters":
            request = command.get("request")
            if not isinstance(request, Mapping):
                raise ValueError("parameters requires a request object")
            payload["request"] = copy.deepcopy(dict(request))
        elif name in ("seek", "stop"):
            payload["position_ms"] = (
                0.0 if name == "stop"
                else _nonnegative_number(command.get("position_ms"), "position_ms")
            )
            autoplay = False if name == "stop" else command.get("autoplay")
            if autoplay is not None and type(autoplay) is not bool:
                raise ValueError("autoplay must be a boolean or null")
            payload["autoplay"] = autoplay
        elif name == "set_volume":
            payload["volume"] = _volume_number(command.get("volume"))
        pending = _PendingControl(
            generation, name, control_id, payload, time.monotonic(),
        )
        coalesced: list[str] = []
        with self._condition:
            self._validate_generation_locked(generation)
            if not self._accepting_controls:
                raise ValueError("the current generation has no live session")
            if name in ("parameters", "seek", "stop", "set_volume"):
                kept: deque[_PendingControl] = deque()
                for item in self._controls:
                    same_latest = (
                        item.command == name
                        or (name in ("seek", "stop") and item.command in ("seek", "stop"))
                    )
                    if item.generation == generation and same_latest:
                        coalesced.append(item.control_id)
                    else:
                        kept.append(item)
                self._controls = kept
            self._controls.append(pending)
            while len(self._controls) > MAX_CONTROL_COMMANDS:
                self._controls.popleft()
            self._latest_control_id = control_id
            self.emit({
                "type": "control_accepted",
                "request_id": generation,
                "control_id": control_id,
                "cmd": name,
                "coalesced_control_ids": coalesced,
            })
            self._condition.notify_all()

    def _control_identity(self, command: Mapping[str, Any]) -> tuple[str, str]:
        generation = command.get("request_id")
        if not isinstance(generation, str) or not generation.strip():
            raise ValueError("request_id must be a non-empty string")
        control_id = command.get("control_id")
        if not isinstance(control_id, str) or not control_id.strip():
            raise ValueError("control_id must be a non-empty string")
        return generation.strip(), control_id.strip()

    def _validate_generation_locked(self, generation: str) -> None:
        if not self._desired_generation or generation != self._desired_generation:
            current = self._desired_generation
            self.emit({
                "type": "stale_control",
                "request_id": current,
                "generation": current,
                "rejected_generation": generation,
            })
            raise _StaleControlError("control belongs to a stale generation")

    def _request_stop(self) -> None:
        with self._condition:
            self._stop = True
            self._accepting_controls = False
            self._pending_open = None
            self._controls.clear()
            self._condition.notify_all()
        self._stop_event.set()

    def _worker_loop(self) -> None:
        session = graph = None
        while True:
            with self._condition:
                while not self._stop and not (
                        self._failure_pending is not None
                        or self._pending_open is not None
                        or self._controls):
                    self._condition.wait(timeout=0.5)
                if self._stop:
                    session, graph = self._detach_session_locked()
                    self._pending_open = None
                    self._controls.clear()
                    self._condition.notify_all()
                    break
                if self._failure_pending is not None:
                    failure = self._failure_pending
                    self._failure_pending = None
                    session, graph = self._detach_session_locked()
                    spec = None
                    pending = None
                    self._condition.notify_all()
                elif self._pending_open is not None:
                    spec = self._pending_open
                    self._pending_open = None
                    failure = None
                    pending = None
                else:
                    pending = self._controls.popleft()
                    spec = None
                    failure = None
                    session = graph = None
            if failure is not None:
                try:
                    self._close_resources(session, graph)
                except Exception as close_error:
                    generation, error_type, message = failure
                    failure = (
                        generation,
                        error_type,
                        message + "; close failed: " + str(close_error),
                    )
                self._emit_failure(*failure)
            elif spec is not None:
                self._process_open(spec)
            elif pending.command == "release":
                self._process_release(pending)
            else:
                self._process_control(pending)
        try:
            self._close_resources(session, graph)
        except Exception:
            pass

    def _detach_session_locked(self) -> tuple[Any, Any]:
        session, graph = self._session, self._graph
        self._session = None
        self._graph = None
        self._session_generation = None
        self._accepting_controls = False
        return session, graph

    def _process_open(self, spec: _OpenSpec) -> None:
        with self._condition:
            if self._stop or self._desired_generation != spec.generation:
                return
            old_session, old_graph = self._detach_session_locked()
            # Receipt of this latest open re-enables controls for its generation.
            self._accepting_controls = self._desired_generation == spec.generation
        if old_session is not None or old_graph is not None:
            try:
                self._close_resources(old_session, old_graph)
            except Exception as error:
                self._fail_current(
                    spec.generation,
                    "PreviousSessionCloseError",
                    str(error) or type(error).__name__,
                )
                return
        with self._condition:
            if (self._stop or self._desired_generation != spec.generation
                    or not self._accepting_controls):
                return
            self.emit({
                "type": "opening",
                "request_id": spec.generation,
                "phase": "preparing_source",
            })
        graph = None
        session = None
        try:
            graph_factory = self._graph_factory
            if graph_factory is None:
                graph_factory = _RUNTIME_GRAPH.LimitedMixSession
            ffmpeg = None
            if self._ffmpeg_resolver is not None:
                ffmpeg = self._ffmpeg_resolver()
            graph = graph_factory(
                spec.request,
                spec.cache_dir,
                spec.dll_path,
                ffmpeg=ffmpeg,
            )
            sample_rate = int(getattr(graph, "sample_rate"))
            channels = int(getattr(graph, "channels"))
            duration_frames = int(getattr(graph, "duration_frames"))
            if sample_rate <= 0 or channels <= 0 or duration_frames < 0:
                raise ValueError("graph returned invalid stream metadata")
            with self._condition:
                if (self._stop or self._desired_generation != spec.generation
                        or self._pending_open is not None
                        or not self._accepting_controls):
                    self._close_graph(graph)
                    current = self._desired_generation
                    if current is not None:
                        self.emit({
                            "type": "open_discarded",
                            "request_id": current,
                            "discarded_generation": spec.generation,
                        })
                    return

            factory_factory = self._output_factory_factory
            if factory_factory is None:
                factory_factory = self._default_output_factory_factory
            output_factory = factory_factory(graph, spec)
            factory = self._session_factory
            if factory is None:
                factory = _RUNTIME_SESSION.RealtimeAuditionSession
            session = factory(
                graph,
                output_factory,
                block_frames=self._block_frames,
                silent=self._silent_probe,
            )
            session.set_volume(spec.volume)
            position_frame = int(round(spec.position_ms * sample_rate / 1000.0))
            if not 0 <= position_frame <= duration_frames:
                raise ValueError("open position is outside the prepared song")
            open_seek_serial = None
            if position_frame or spec.autoplay:
                open_seek_serial = session.seek(position_frame, autoplay=spec.autoplay)

            with self._condition:
                if (self._stop or self._desired_generation != spec.generation
                        or self._pending_open is not None
                        or not self._accepting_controls):
                    self._close_resources(session, graph)
                    current = self._desired_generation
                    if current is not None:
                        self.emit({
                            "type": "open_discarded",
                            "request_id": current,
                            "discarded_generation": spec.generation,
                        })
                    return
                self._session = session
                self._graph = graph
                self._session_generation = spec.generation
                self._seek_control_by_serial.clear()
                self._parameter_control_by_revision.clear()
                self._latest_seek = _SeekBinding(
                    generation=spec.generation,
                    serial=(
                        None if open_seek_serial is None else int(open_seek_serial)
                    ),
                    frame=position_frame,
                    autoplay=spec.autoplay,
                    control_id=None,
                )
                session.start()
                # Keep generation ownership through ready publication so a
                # newer open cannot trail an old live_ready event.
                self.emit({
                    "type": "live_ready",
                    "request_id": spec.generation,
                    "generation": spec.generation,
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "duration_frames": duration_frames,
                })
        except Exception as error:
            with self._condition:
                owns_generation = (
                    self._desired_generation == spec.generation
                    and self._accepting_controls
                )
                if self._session is session:
                    self._session = None
                    self._graph = None
                    self._session_generation = None
                self._accepting_controls = False
            try:
                self._close_resources(session, graph)
            except Exception:
                pass
            if owns_generation:
                self._emit_failure(
                    spec.generation,
                    type(error).__name__,
                    str(error) or type(error).__name__,
                )

    def _process_release(self, pending: _PendingControl) -> None:
        with self._condition:
            if self._desired_generation != pending.generation:
                return
            session, graph = self._detach_session_locked()
        try:
            self._close_resources(session, graph)
        except Exception as error:
            if self._desired_generation == pending.generation:
                self._emit_failure(
                    pending.generation,
                    "ReleaseError",
                    "session release failed: %s" % (str(error) or type(error).__name__),
                )
            return
        with self._condition:
            if self._desired_generation == pending.generation:
                self.emit({
                    "type": "live_released",
                    "request_id": pending.generation,
                    "control_id": pending.control_id,
                })

    def _process_control(self, pending: _PendingControl) -> None:
        with self._condition:
            if (self._desired_generation != pending.generation
                    or self._session_generation != pending.generation
                    or not self._accepting_controls):
                return
            session = self._session
            graph = self._graph
            if session is None or graph is None:
                return
        try:
            if pending.command == "parameters":
                revision = session.set_parameters(pending.payload["request"])
                with self._condition:
                    if self._desired_generation == pending.generation:
                        self._parameter_control_by_revision[int(revision)] = pending.control_id
            elif pending.command in ("seek", "stop"):
                position_ms = float(pending.payload["position_ms"])
                sample_rate = int(getattr(graph, "sample_rate"))
                duration_frames = int(getattr(graph, "duration_frames"))
                frame = int(round(position_ms * sample_rate / 1000.0))
                if not 0 <= frame <= duration_frames:
                    raise ValueError("seek position is outside the prepared song")
                autoplay = pending.payload.get("autoplay")
                serial = (
                    session.stop()
                    if pending.command == "stop"
                    else session.seek(frame, autoplay=autoplay)
                )
                with self._condition:
                    if self._desired_generation == pending.generation:
                        if serial is not None:
                            self._seek_control_by_serial[int(serial)] = pending.control_id
                        self._latest_seek = _SeekBinding(
                            generation=pending.generation,
                            serial=None if serial is None else int(serial),
                            frame=frame,
                            autoplay=autoplay,
                            control_id=pending.control_id,
                        )
            elif pending.command == "play":
                session.play()
            elif pending.command == "pause":
                session.pause()
            elif pending.command == "set_volume":
                session.set_volume(pending.payload["volume"])
            else:
                raise ValueError("unsupported control: " + pending.command)
        except Exception as error:
            self._fail_current(
                pending.generation,
                type(error).__name__,
                str(error) or type(error).__name__,
            )

    def _default_output_factory_factory(
            self, graph: Any, spec: _OpenSpec) -> Callable[[], Any]:
        native_output = _RUNTIME_NATIVE_OUTPUT.NativeOutput
        sample_rate = int(graph.sample_rate)
        channels = int(graph.channels)

        def create_output() -> Any:
            return native_output(
                spec.dll_path,
                sample_rate=sample_rate,
                channels=channels,
                capacity_frames=4096,
                period_frames=256,
                mode="wasapi",
            )

        return create_output

    def _fail_current(self, generation: str, error_type: str, message: str) -> None:
        with self._condition:
            if self._desired_generation != generation:
                return
            session, graph = self._detach_session_locked()
        try:
            self._close_resources(session, graph)
        except Exception as close_error:
            message = message + "; close failed: " + str(close_error)
        self._emit_failure(generation, error_type, message)

    def _emit_failure(self, generation: str, error_type: str, message: str) -> None:
        with self._condition:
            if self._desired_generation != generation:
                return
            self._accepting_controls = False
            self.emit({
                "type": "error",
                "request_id": generation,
                "generation": generation,
                "error_type": error_type,
                "message": message,
                "auto_restart": False,
            })

    def _close_graph(self, graph: Any) -> None:
        if graph is None:
            return
        try:
            graph.close()
        except Exception:
            pass

    def _close_resources(self, session: Any, graph: Any) -> None:
        if session is None:
            self._close_graph(graph)
            return
        try:
            session.close()
        except Exception:
            # The session normally closes its renderer. If that failed, make a
            # best effort to release the graph before surfacing the error.
            self._close_graph(graph)
            raise

    def _ticker_loop(self) -> None:
        next_tick = time.perf_counter()
        while not self._stop_event.is_set():
            next_tick += self._tick_seconds
            delay = max(0.0, next_tick - time.perf_counter())
            if self._stop_event.wait(delay):
                return
            with self._condition:
                generation = self._desired_generation
                session = self._session
                if (generation is None or session is None
                        or self._session_generation != generation
                        or not self._accepting_controls):
                    continue
            try:
                snapshot = session.snapshot()
            except Exception as error:
                self._mark_failure(generation, type(error).__name__, str(error))
                continue
            if not isinstance(snapshot, Mapping):
                self._mark_failure(
                    generation, "ProtocolError", "session snapshot is not an object",
                )
                continue
            if snapshot.get("state") == "failed":
                self._mark_failure(
                    generation,
                    "SessionError",
                    str(snapshot.get("error") or "realtime session failed"),
                )
                continue
            with self._condition:
                if (self._desired_generation != generation
                        or self._session is not session):
                    continue
                enriched = self._enrich_snapshot_locked(
                    generation, snapshot,
                )
                # Publish under generation ownership so a newer open cannot
                # receive a trailing live_state from the old session.
                self.emit({
                    "type": "live_state",
                    "request_id": generation,
                    "snapshot": enriched,
                })

    def _mark_failure(self, generation: str, error_type: str, message: str) -> None:
        with self._condition:
            if (self._desired_generation != generation
                    or self._session_generation != generation):
                return
            if self._failure_pending is None:
                self._failure_pending = (
                    generation, error_type, message or error_type,
                )
                self._accepting_controls = False
                self._controls.clear()
                self._condition.notify_all()

    def _enrich_snapshot_locked(
            self,
            generation: str,
            raw_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(dict(raw_snapshot))
        graph = self._graph
        sample_rate = int(getattr(graph, "sample_rate", snapshot.get("sample_rate") or 0))
        channels = int(getattr(graph, "channels", snapshot.get("channels") or 0))
        duration_frames = int(getattr(
            graph, "duration_frames", snapshot.get("duration_frames") or 0,
        ))
        snapshot["generation"] = generation
        snapshot["sample_rate"] = sample_rate
        snapshot["channels"] = channels
        snapshot["duration_frames"] = duration_frames

        render_timings = snapshot.pop("render_ms", None)
        render_values = []
        if isinstance(render_timings, (list, tuple)):
            for value in render_timings:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                numeric = float(value)
                if math.isfinite(numeric):
                    render_values.append(numeric)
        snapshot["render_ms_count"] = len(render_values)
        if render_values:
            ordered = sorted(render_values)
            index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
            snapshot["render_ms_p95"] = round(ordered[index], 3)
            snapshot["render_ms_max"] = round(ordered[-1], 3)
        else:
            snapshot["render_ms_p95"] = None
            snapshot["render_ms_max"] = None

        native = snapshot.get("native")
        media_frame = None
        if isinstance(native, Mapping):
            native = dict(native)
            snapshot["native"] = native
            media_frame = native.get("media_frame")
        if media_frame is None:
            media_frame = snapshot.get("renderer_frame")
        if media_frame is None:
            media_frame = getattr(graph, "position_frame", None)
        if media_frame is not None:
            media_frame = int(media_frame)
        snapshot["media_frame"] = media_frame

        last_seek = snapshot.get("last_seek")
        applied_serial = None
        if isinstance(last_seek, Mapping) and last_seek.get("seek_id") is not None:
            try:
                applied_serial = int(last_seek["seek_id"])
            except (TypeError, ValueError):
                applied_serial = None
        latest = self._latest_seek
        if latest is not None and latest.generation == generation:
            snapshot["seek_control_id"] = latest.control_id
            pending_target = (
                latest.serial is not None and applied_serial != latest.serial
            )
            if pending_target:
                snapshot["seek_phase"] = "queued"
                snapshot["media_frame"] = None
                if isinstance(native, dict):
                    native["media_frame"] = None
                snapshot["last_seek"] = {
                    "seek_id": latest.control_id,
                    "frame": latest.frame,
                    "autoplay": latest.autoplay,
                    "phase": "queued",
                }
            elif isinstance(last_seek, Mapping):
                snapshot["seek_phase"] = str(last_seek.get("phase") or "prepared")
            else:
                snapshot["seek_phase"] = None
        else:
            control_id = (
                self._seek_control_by_serial.get(applied_serial)
                if applied_serial is not None else None
            )
            snapshot["seek_control_id"] = control_id
            snapshot["seek_phase"] = (
                str(last_seek.get("phase") or "prepared")
                if isinstance(last_seek, Mapping) else None
            )

        revision = snapshot.get("parameter_revision")
        try:
            revision_value = int(revision)
        except (TypeError, ValueError):
            revision_value = None
        snapshot["parameter_control_id"] = (
            self._parameter_control_by_revision.get(revision_value)
            if revision_value is not None else None
        )
        telemetry = snapshot.get("telemetry")
        if isinstance(telemetry, Mapping):
            telemetry = dict(telemetry)
            snapshot["telemetry"] = telemetry
            submitted_media = (
                native.get("media_frame") if isinstance(native, Mapping) else None
            )
            telemetry["media_frame"] = (
                None if submitted_media is None else int(submitted_media)
            )
            try:
                telemetry_revision = int(telemetry.get("parameter_revision"))
            except (TypeError, ValueError):
                telemetry_revision = None
            telemetry["parameter_control_id"] = (
                self._parameter_control_by_revision.get(telemetry_revision)
                if telemetry_revision is not None else None
            )
            spectrum = telemetry.get("spectrum")
            if isinstance(spectrum, Mapping):
                spectrum = dict(spectrum)
                spectrum["media_frame"] = telemetry["media_frame"]
                telemetry["spectrum"] = spectrum
            if submitted_media is None:
                telemetry["status"] = (
                    "pending_seek" if snapshot.get("media_frame") is None
                    else "unavailable"
                )
        snapshot["control_id"] = self._latest_control_id
        return snapshot


def _worker_main() -> int:
    """Resident worker entry point used by the shared subprocess bootstrap."""
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    server = _RealtimeWorkerServer(ffmpeg_resolver=_resolve_worker_ffmpeg)
    try:
        _load_worker_runtime()
    except BaseException as error:
        server.emit({
            "type": "fatal",
            "error_type": type(error).__name__,
            "message": str(error),
            "traceback": "",
        })
        return 2
    return server.run()


if __name__ == "__main__":
    raise SystemExit(_worker_main())


__all__ = [
    "LIVE_TICK_HZ",
    "MAX_CONTROL_COMMANDS",
    "MixdownRealtimeService",
    "REALTIME_SERVICE_SCHEMA",
    "RealtimeServiceError",
]