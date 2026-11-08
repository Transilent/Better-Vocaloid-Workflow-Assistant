"""Bounded JSONL process transport for the single continuous mixer service."""
from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
import uuid


SERVICE_SCHEMA = "vwa.audio_mixdown.audition-service/v1"
PROTOCOL_VERSION = 1
TICK_HZ = 30.0
TICK_SECONDS = 1.0 / TICK_HZ
MAX_SEEN_REQUEST_IDS = 256


class AuditionServiceError(RuntimeError):
    """Base class for resident audition service failures."""


class ServiceStartError(AuditionServiceError):
    """The resident worker did not become ready."""


class ServiceUnavailable(AuditionServiceError):
    """The resident worker is not running or has exited."""


class ServiceProtocolError(AuditionServiceError):
    """The resident worker returned malformed protocol data."""


def _json_line(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"




def _normalized_paths(values: Any) -> list[str]:
    paths = []
    seen = set()
    for value in values:
        if not value:
            continue
        text = str(Path(value).expanduser())
        key = os.path.normcase(os.path.abspath(text))
        if key not in seen:
            seen.add(key)
            paths.append(text)
    return paths


class ResidentProcessClient:
    """UI-thread-safe owner of one resident audition worker process.

    Subclasses may replace ``_WORKER_MODULE`` with another module exposing the
    same ``_worker_main`` protocol; process transport remains shared here.

    ``start``, ``poll`` and ``close`` own only the worker transport.  A background thread drains stdout into a Queue, so an
    event loop can call :meth:`poll` with no timeout.  Process restart is never
    automatic.
    """

    _WORKER_MODULE = 'mixdown_realtime_service'

    def __init__(
            self,
            cache_dir: str | os.PathLike[str] | None = None,
            *,
            python_executable: str | os.PathLike[str] | None = None,
            extra_paths: Any = (),
            environment: Mapping[str, str] | None = None,
            startup_timeout: float = 15.0,
            shutdown_timeout: float = 8.0,
            stderr_lines: int = 120) -> None:
        self.cache_dir = (
            str(Path(cache_dir).expanduser().resolve()) if cache_dir is not None else None
        )
        if python_executable is not None:
            selected_python = Path(python_executable)
        else:
            selected_python = Path(sys.executable).resolve()
            console_python = selected_python.with_name('python.exe')
            if console_python.is_file():
                selected_python = console_python
        self.python_executable = str(selected_python)
        if isinstance(extra_paths, (str, bytes, os.PathLike)):
            extra_paths = (extra_paths,)
        self.extra_paths = tuple(str(path) for path in extra_paths if path)
        self.environment = dict(environment or {})
        self.startup_timeout = max(0.1, float(startup_timeout))
        self.shutdown_timeout = max(0.1, float(shutdown_timeout))
        self._stderr_limit = max(10, int(stderr_lines))
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=self._stderr_limit)
        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._reader_done = threading.Event()
        self._ready: dict[str, Any] | None = None
        self._closing = False
        self._start_count = 0
        self._request_counter = 0

    @property
    def pid(self) -> int | None:
        process = self._process
        if process is None or process.poll() is not None:
            return None
        return process.pid

    @property
    def returncode(self) -> int | None:
        process = self._process
        return process.poll() if process is not None else None

    @property
    def is_alive(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None)

    @property
    def ready(self) -> dict[str, Any] | None:
        return dict(self._ready) if self._ready is not None else None

    @property
    def restart_count(self) -> int:
        return max(0, self._start_count - 1)

    @property
    def stderr_tail(self) -> str:
        with self._state_lock:
            return "\n".join(self._stderr)

    def _worker_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.environment)
        inherited = env.get('VWA_MIXDOWN_EXTRA_PATHS', '')
        test_package = env.get('VWA_MIX_TEST_PEDALBOARD_PATH', '')
        python_path = env.get('PYTHONPATH', '')
        paths = [
            str(Path(__file__).resolve().parent),
            *self.extra_paths,
            *[part for part in inherited.split(os.pathsep) if part],
            *([test_package] if test_package else []),
            *[part for part in python_path.split(os.pathsep) if part],
        ]
        ordered = _normalized_paths(paths)
        env['PYTHONUTF8'] = '1'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        env['VWA_MIXDOWN_EXTRA_PATHS'] = os.pathsep.join(ordered)
        env['PYTHONPATH'] = os.pathsep.join(ordered)
        return env

    def start(self) -> dict[str, Any]:
        """Start the resident worker and wait for its runtime-ready event."""
        with self._state_lock:
            if self.is_alive and self._ready is not None:
                return dict(self._ready)
            if self._process is not None:
                self._discard_dead_process()
            self._events = queue.Queue()
            with self._state_lock:
                self._stderr.clear()
            self._ready_event = threading.Event()
            self._reader_done = threading.Event()
            self._ready = None
            self._closing = False
            bootstrap = (
                "import os,sys; "
                "paths=[p for p in "
                "os.environ.get('VWA_MIXDOWN_EXTRA_PATHS','').split(os.pathsep) if p]; "
                "[sys.path.insert(0,p) for p in reversed(paths)]; "
                f"import {self._WORKER_MODULE} as service; "
                "raise SystemExit(service._worker_main())"
            )
            command = [
                self.python_executable, '-B', '-u', '-c', bootstrap,
            ]
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    env=self._worker_environment(),
                    cwd=str(Path(__file__).resolve().parents[1]),
                    creationflags=creationflags,
                    start_new_session=(os.name != 'nt'),
                )
            except OSError as error:
                raise ServiceStartError("无法启动常驻试听进程：%s" % error) from error
            self._process = process
            self._start_count += 1
            self._stdout_thread = threading.Thread(
                target=self._stdout_loop,
                args=(process,),
                name='mixdown-audition-stdout',
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                args=(process,),
                name='mixdown-audition-stderr',
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._ready_event.wait(min(0.05, max(0.0, deadline - time.monotonic()))):
                break
            if process.poll() is not None:
                break
        if self._ready is None:
            detail = self.stderr_tail
            fatal_detail = ''
            while True:
                try:
                    pending_event = self._events.get_nowait()
                except queue.Empty:
                    break
                if pending_event.get('type') in ('fatal', 'protocol_error'):
                    fatal_detail = str(
                        pending_event.get('message') or pending_event.get('raw') or ''
                    )
            if fatal_detail and fatal_detail not in detail:
                detail = fatal_detail + (("\n" + detail) if detail else "")
            self._force_stop()
            raise ServiceStartError(
                '常驻试听进程未就绪（returncode=%r）%s'
                % (process.returncode, (': ' + detail) if detail else '')
            )
        return dict(self._ready)

    def _stdout_loop(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        try:
            if stream is not None:
                for line in stream:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except ValueError:
                        event = {
                            'type': 'protocol_error',
                            'message': 'invalid JSON from audition service',
                            'raw': text[:500],
                        }
                    if not isinstance(event, dict):
                        event = {
                            'type': 'protocol_error',
                            'message': 'audition service emitted a non-object event',
                        }
                    if event.get('type') == 'ready':
                        self._ready = dict(event)
                        self._ready_event.set()
                        continue
                    self._receive_event(event)
        finally:
            returncode = process.wait()
            self._reader_done.set()
            if not self._closing:
                stderr_thread = self._stderr_thread
                if stderr_thread is not None and stderr_thread is not threading.current_thread():
                    stderr_thread.join(timeout=0.2)
                event_type = 'crashed' if returncode not in (None, 0) else 'exited'
                self._receive_event({
                    'type': event_type,
                    'returncode': returncode,
                    'stderr_tail': self.stderr_tail,
                    'auto_restart': False,
                    'restart_count': self.restart_count,
                })

    def _receive_event(self, event: dict[str, Any]) -> None:
        """Queue one parsed worker event; subclasses may coalesce telemetry."""
        self._events.put(event)

    def _stderr_loop(self, process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            if text:
                with self._state_lock:
                    self._stderr.append(text)

    def _send(self, command: Mapping[str, Any]) -> None:
        payload = dict(command)
        payload.setdefault('protocol', PROTOCOL_VERSION)
        with self._send_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise ServiceUnavailable('常驻试听进程未运行。')
            try:
                process.stdin.write(_json_line(payload))
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                raise ServiceUnavailable('常驻试听进程连接已断开。') from error





    def poll(self, *, timeout: float = 0.0,
             max_events: int | None = None) -> list[dict[str, Any]]:
        """Return already-received worker events; UI threads should use timeout=0."""
        events: list[dict[str, Any]] = []
        limit = max_events if max_events is not None else None
        timeout = max(0.0, float(timeout))
        if timeout > 0.0:
            try:
                events.append(self._events.get(timeout=timeout))
            except queue.Empty:
                return events
        while limit is None or len(events) < limit:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def close(self, timeout: float | None = None) -> int | None:
        """Gracefully stop the worker, force-kill its tree on timeout, and join."""
        with self._state_lock:
            process = self._process
            if process is None:
                return None
            if process.poll() is not None:
                self._join_threads()
                self._cleanup_process(process)
                return process.returncode
            self._closing = True
        try:
            self._send({'cmd': 'shutdown'})
        except ServiceUnavailable:
            pass
        wait_seconds = self.shutdown_timeout if timeout is None else max(0.0, float(timeout))
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
        self._join_threads()
        self._cleanup_process(process)
        return process.returncode

    def _join_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=2.0)

    def _cleanup_process(self, process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        with self._state_lock:
            if self._process is process:
                self._ready = None

    def _discard_dead_process(self) -> None:
        process = self._process
        if process is None:
            return
        self._join_threads()
        self._cleanup_process(process)
        self._process = None

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == 'nt':
            try:
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

    def _force_stop(self) -> None:
        process = self._process
        if process is None:
            return
        with self._state_lock:
            self._closing = True
        if process.poll() is None:
            self._terminate_process_tree(process)
        self._join_threads()
        self._cleanup_process(process)
