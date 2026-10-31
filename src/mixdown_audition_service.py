"""Resident JSONL service for interactive mixdown audition rendering.

The UI-facing class in this module is pure standard library and never imports
NumPy, SoundFile, Pedalboard or PyQt in the UI process.  A long-lived Python
child process owns the audition runtime, decoded-source cache and EDC cache.
Commands and events are newline-delimited JSON over stdin/stdout.

The service keeps at most one queued request and one running request.  A newer
render/warm request supersedes queued work, cancels the active request and can
deep-merge its request mapping onto the previous specification.  It emits
progress events at approximately 30 Hz while busy.  It never silently restarts
after a crash; the client surfaces a single ``crashed`` event and exposes an
explicit ``start`` call for a deliberate restart.
"""
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


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(patch, Mapping):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in patch.items():
            if key in merged and isinstance(merged[key], Mapping) and isinstance(value, Mapping):
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(patch)


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


class MixdownAuditionService:
    """UI-thread-safe owner of one resident audition worker process.

    ``start``, ``request``, ``warm``, ``poll``, ``cancel`` and ``close`` do not
    block on rendering.  A background thread drains stdout into a Queue, so an
    event loop can call :meth:`poll` with no timeout.  Process restart is never
    automatic.
    """

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
                "import mixdown_audition_service as service; "
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
                    self._events.put(event)
        finally:
            returncode = process.wait()
            self._reader_done.set()
            if not self._closing:
                stderr_thread = self._stderr_thread
                if stderr_thread is not None and stderr_thread is not threading.current_thread():
                    stderr_thread.join(timeout=0.2)
                event_type = 'crashed' if returncode not in (None, 0) else 'exited'
                self._events.put({
                    'type': event_type,
                    'returncode': returncode,
                    'stderr_tail': self.stderr_tail,
                    'auto_restart': False,
                    'restart_count': self.restart_count,
                })

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

    def _new_request_id(self) -> str:
        self._request_counter += 1
        return 'audition-%d-%d-%s' % (
            os.getpid(), self._request_counter, uuid.uuid4().hex[:8],
        )

    def request(
            self,
            request: Mapping[str, Any],
            before_output: str | os.PathLike[str] | None = None,
            after_output: str | os.PathLike[str] | None = None,
            cache_dir: str | os.PathLike[str] | None = None,
            variant: str = 'after',
            *,
            merge: bool = True,
            request_id: str | None = None) -> str:
        """Queue a render request and return its request id without blocking."""
        if not isinstance(request, Mapping):
            raise TypeError('request 必须是映射。')
        selected_cache = cache_dir if cache_dir is not None else self.cache_dir
        if selected_cache is None:
            raise ValueError('request 需要 cache_dir。')
        identifier = str(request_id or self._new_request_id())
        command = {
            'cmd': 'render',
            'request_id': identifier,
            'request': copy.deepcopy(dict(request)),
            'before_output': os.fspath(before_output) if before_output is not None else None,
            'after_output': os.fspath(after_output) if after_output is not None else None,
            'cache_dir': os.fspath(selected_cache),
            'variant': str(variant),
            'merge_with_previous': bool(merge),
        }
        self._send(command)
        return identifier

    def warm(
            self,
            request: Mapping[str, Any],
            cache_dir: str | os.PathLike[str] | None = None,
            *,
            merge: bool = True,
            request_id: str | None = None) -> str:
        """Queue a source-cache warm request and return its id without blocking."""
        if not isinstance(request, Mapping):
            raise TypeError('request 必须是映射。')
        selected_cache = cache_dir if cache_dir is not None else self.cache_dir
        if selected_cache is None:
            raise ValueError('warm 需要 cache_dir。')
        identifier = str(request_id or self._new_request_id())
        self._send({
            'cmd': 'warm',
            'request_id': identifier,
            'request': copy.deepcopy(dict(request)),
            'cache_dir': os.fspath(selected_cache),
            'merge_with_previous': bool(merge),
        })
        return identifier

    def cancel(self, request_id: str | None = None) -> None:
        """Cancel the active request and drop any queued latest request."""
        self._send({'cmd': 'cancel', 'request_id': request_id})

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


@dataclass(frozen=True)
class _ServiceJob:
    request_id: str
    kind: str
    request: dict[str, Any]
    cache_dir: str
    before_output: str | None
    after_output: str | None
    variant: str
    preexisting_outputs: frozenset[str]
    created_at: float


_RUNTIME_AUDITION: Any = None
_RUNTIME_BACKEND: Any = None
_RUNTIME_NUMPY: Any = None
_RUNTIME_SOUNDFILE: Any = None


def _load_worker_runtime() -> None:
    global _RUNTIME_AUDITION, _RUNTIME_BACKEND
    global _RUNTIME_NUMPY, _RUNTIME_SOUNDFILE
    _RUNTIME_NUMPY = importlib.import_module('numpy')
    _RUNTIME_SOUNDFILE = importlib.import_module('soundfile')
    _RUNTIME_BACKEND = importlib.import_module('audio_mixdown')
    _RUNTIME_AUDITION = importlib.import_module('audio_mixdown_audition')
    _RUNTIME_BACKEND._load_pedalboard()


class _WorkerServer:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._output_lock = threading.Lock()
        self._pending: _ServiceJob | None = None
        self._active: _ServiceJob | None = None
        self._active_cancel: threading.Event | None = None
        self._active_cancel_reason = 'cancel_requested'
        self._last_render_spec: dict[str, Any] | None = None
        self._last_warm_spec: dict[str, Any] | None = None
        self._seen_ids: deque[str] = deque(maxlen=MAX_SEEN_REQUEST_IDS)
        self._stop = False
        self._stdout_broken = False
        self._ticker_stop = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name='mixdown-audition-render',
            daemon=True,
        )
        self._ticker = threading.Thread(
            target=self._ticker_loop,
            name='mixdown-audition-ticker',
            daemon=True,
        )

    def emit(self, event: Mapping[str, Any]) -> None:
        payload = {
            'schema': SERVICE_SCHEMA,
            'protocol': PROTOCOL_VERSION,
            'ts': time.time(),
        }
        payload.update(event)
        line = _json_line(payload)
        with self._output_lock:
            if self._stdout_broken:
                return
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._stdout_broken = True

    def run(self) -> int:
        self._worker.start()
        self._ticker.start()
        self.emit({
            'type': 'ready',
            'pid': os.getpid(),
            'tick_hz': TICK_HZ,
            'service_schema': SERVICE_SCHEMA,
            'python': sys.version.split()[0],
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
                    self.emit({
                        'type': 'error',
                        'request_id': None,
                        'error_type': 'ProtocolError',
                        'message': 'command must be one JSON object per line',
                    })
                    continue
                if not isinstance(command, dict):
                    self.emit({
                        'type': 'error',
                        'request_id': None,
                        'error_type': 'ProtocolError',
                        'message': 'command must be a JSON object',
                    })
                    continue
                command_name = command.get('cmd')
                if command_name == 'shutdown':
                    shutdown_requested = True
                    break
                self._handle_command(command)
                if self._stdout_broken:
                    break
        finally:
            self._request_stop('shutdown' if shutdown_requested else 'input_closed')
            self._worker.join(timeout=10.0)
            self._ticker_stop.set()
            self._ticker.join(timeout=1.0)
            self.emit({
                'type': 'shutdown',
                'status': 'ok' if not self._worker.is_alive() else 'timeout',
                'worker_alive': self._worker.is_alive(),
            })
        return 0 if not self._worker.is_alive() else 3

    def _handle_command(self, command: Mapping[str, Any]) -> None:
        name = command.get('cmd')
        try:
            if name in ('render', 'warm'):
                self._accept_job(command, str(name))
            elif name == 'cancel':
                self._cancel(command.get('request_id'))
            elif name == 'ping':
                with self._condition:
                    active_id = self._active.request_id if self._active else None
                self.emit({
                    'type': 'pong',
                    'request_id': command.get('request_id'),
                    'active_request_id': active_id,
                })
            else:
                raise ValueError('unknown command: %r' % name)
        except Exception as error:
            self.emit({
                'type': 'error',
                'request_id': command.get('request_id'),
                'error_type': type(error).__name__,
                'message': str(error),
            })

    def _accept_job(self, command: Mapping[str, Any], kind: str) -> None:
        request_id = command.get('request_id')
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError('request_id must be a non-empty string')
        request_id = request_id.strip()
        incoming_request = command.get('request', {})
        if not isinstance(incoming_request, Mapping):
            raise ValueError('request must be an object')
        merge = command.get('merge_with_previous', True)
        if type(merge) is not bool:
            raise ValueError('merge_with_previous must be a boolean')

        with self._condition:
            if request_id in self._seen_ids:
                raise ValueError('duplicate request_id: ' + request_id)
            self._seen_ids.append(request_id)
            base = self._last_render_spec if kind == 'render' else self._last_warm_spec
            incoming = {'request': copy.deepcopy(dict(incoming_request))}
            for key in ('cache_dir', 'variant', 'before_output', 'after_output'):
                if key in command:
                    incoming[key] = command.get(key)
            effective = _deep_merge(base or {}, incoming) if merge else incoming
            job = self._job_from_spec(request_id, kind, effective)
            if kind == 'render':
                self._last_render_spec = copy.deepcopy(effective)
            else:
                self._last_warm_spec = copy.deepcopy(effective)
            replaced = self._pending
            self._pending = job
            if self._active is not None and self._active_cancel is not None:
                self._active_cancel_reason = 'superseded'
                self._active_cancel.set()
            self._condition.notify_all()

        self.emit({
            'type': 'accepted',
            'request_id': request_id,
            'kind': kind,
            'merged': bool(merge and base is not None),
            'variant': job.variant if kind == 'render' else None,
        })
        if replaced is not None:
            self.emit({
                'type': 'cancelled',
                'request_id': replaced.request_id,
                'kind': replaced.kind,
                'reason': 'superseded',
                'was_running': False,
            })

    def _job_from_spec(self, request_id: str, kind: str,
                       spec: Mapping[str, Any]) -> _ServiceJob:
        request_value = spec.get('request', {})
        if not isinstance(request_value, Mapping):
            raise ValueError('request must be an object')
        cache_value = spec.get('cache_dir')
        if not cache_value:
            raise ValueError('cache_dir is required')
        cache_dir = str(Path(os.fspath(cache_value)).expanduser().resolve())
        variant = str(spec.get('variant') or 'after').strip().lower()
        if kind == 'render' and variant not in ('before', 'after', 'both'):
            raise ValueError('variant must be before, after or both')
        before_output = (
            None if spec.get('before_output') is None
            else str(Path(os.fspath(spec['before_output'])).expanduser().resolve())
        )
        after_output = (
            None if spec.get('after_output') is None
            else str(Path(os.fspath(spec['after_output'])).expanduser().resolve())
        )
        if kind == 'render':
            if variant in ('before', 'both') and not before_output:
                raise ValueError('before_output is required for this variant')
            if variant in ('after', 'both') and not after_output:
                raise ValueError('after_output is required for this variant')
        selected = []
        if kind == 'render':
            if variant in ('before', 'both'):
                selected.append(before_output)
            if variant in ('after', 'both'):
                selected.append(after_output)
        preexisting = frozenset(
            path for path in selected if path and os.path.lexists(path)
        )
        return _ServiceJob(
            request_id=request_id,
            kind=kind,
            request=copy.deepcopy(dict(request_value)),
            cache_dir=cache_dir,
            before_output=before_output,
            after_output=after_output,
            variant=variant,
            preexisting_outputs=preexisting,
            created_at=time.monotonic(),
        )

    def _cancel(self, request_id: Any = None) -> None:
        target = None if request_id in (None, '') else str(request_id)
        cancelled_pending = []
        active_matched = False
        with self._condition:
            pending = self._pending
            if pending is not None and (target is None or pending.request_id == target):
                cancelled_pending.append(pending)
                self._pending = None
            active = self._active
            if active is not None and (target is None or active.request_id == target):
                active_matched = True
                self._active_cancel_reason = 'cancel_requested'
                if self._active_cancel is not None:
                    self._active_cancel.set()
            self._condition.notify_all()
        for job in cancelled_pending:
            self.emit({
                'type': 'cancelled',
                'request_id': job.request_id,
                'kind': job.kind,
                'reason': 'cancel_requested',
                'was_running': False,
            })
        self.emit({
            'type': 'cancel_ack',
            'request_id': target,
            'active_matched': active_matched,
            'pending_cancelled': len(cancelled_pending),
        })

    def _request_stop(self, reason: str) -> None:
        cancelled_pending = []
        with self._condition:
            self._stop = True
            if self._pending is not None:
                cancelled_pending.append(self._pending)
                self._pending = None
            if self._active is not None and self._active_cancel is not None:
                self._active_cancel_reason = reason
                self._active_cancel.set()
            self._condition.notify_all()
        for job in cancelled_pending:
            self.emit({
                'type': 'cancelled',
                'request_id': job.request_id,
                'kind': job.kind,
                'reason': reason,
                'was_running': False,
            })

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._stop and self._pending is None:
                    self._condition.wait(timeout=0.5)
                if self._stop and self._pending is None:
                    return
                if self._pending is None:
                    continue
                job = self._pending
                self._pending = None
                self._active = job
                cancel_event = threading.Event()
                self._active_cancel = cancel_event
                self._active_cancel_reason = 'cancel_requested'
                self._condition.notify_all()
            self.emit({
                'type': 'started',
                'request_id': job.request_id,
                'kind': job.kind,
                'started_after_ms': round(
                    max(0.0, time.monotonic() - job.created_at) * 1000.0, 3
                ),
            })
            try:
                if job.kind == 'render':
                    report = _RUNTIME_AUDITION.render_audition_pair(
                        job.request,
                        job.before_output,
                        job.after_output,
                        job.cache_dir,
                        cancel=cancel_event.is_set,
                        variant=job.variant,
                    )
                else:
                    report = self._run_warm_job(job, cancel_event)
            except InterruptedError:
                with self._condition:
                    reason = self._active_cancel_reason
                    self._clear_active(job)
                self._cleanup_outputs(job)
                self.emit({
                    'type': 'cancelled',
                    'request_id': job.request_id,
                    'kind': job.kind,
                    'reason': reason,
                    'was_running': True,
                })
                continue
            except Exception as error:
                with self._condition:
                    self._clear_active(job)
                self._cleanup_outputs(job)
                self.emit({
                    'type': 'error',
                    'request_id': job.request_id,
                    'kind': job.kind,
                    'error_type': type(error).__name__,
                    'message': str(error),
                })
                continue

            with self._condition:
                cancelled = cancel_event.is_set() or self._stop
                reason = self._active_cancel_reason if cancelled else None
                self._clear_active(job)
            if cancelled:
                self._cleanup_outputs(job)
                self.emit({
                    'type': 'cancelled',
                    'request_id': job.request_id,
                    'kind': job.kind,
                    'reason': reason or 'shutdown',
                    'was_running': True,
                })
            else:
                self.emit({
                    'type': 'result',
                    'request_id': job.request_id,
                    'kind': job.kind,
                    'report': report,
                })

    def _run_warm_job(self, job: _ServiceJob,
                      cancel_event: threading.Event) -> dict[str, Any]:
        report = dict(_RUNTIME_AUDITION.warm_audition(
            job.request,
            job.cache_dir,
            cancel=cancel_event.is_set,
        ))
        try:
            settings = _RUNTIME_BACKEND._settings(job.request)
            options = _RUNTIME_AUDITION._v3_options(job.request, settings)
            pedalboard = _RUNTIME_BACKEND._load_pedalboard()
            edc = _RUNTIME_AUDITION._measure_reverb_edc(
                pedalboard,
                settings,
                int(report['sample_rate']),
                Path(job.cache_dir),
                _RUNTIME_NUMPY,
                cancel_event.is_set,
                predelay_ms=options.reverb_predelay_ms,
            )
            report['reverb_edc'] = edc
            report['analysis_warm'] = {
                'decode': True,
                'reverb_edc': bool(edc.get('measured')),
            }
        except InterruptedError:
            raise
        except Exception as error:
            warnings = list(report.get('warnings') or [])
            warnings.append('混响 EDC 预热不可用：%s' % (str(error) or type(error).__name__))
            report['warnings'] = warnings
            report['reverb_edc'] = {
                'schema': _RUNTIME_AUDITION.REVERB_EDC_SCHEMA,
                'measured': False,
                'profile': settings.reverb_profile if 'settings' in locals() else None,
                'predelay_ms': options.reverb_predelay_ms if 'options' in locals() else None,
                'sample_rate': int(report.get('sample_rate') or 0),
                'cache_hit': False,
                'reason': str(error) or type(error).__name__,
            }
            report['analysis_warm'] = {'decode': True, 'reverb_edc': False}
        return report

    def _clear_active(self, job: _ServiceJob) -> None:
        if self._active is job:
            self._active = None
            self._active_cancel = None
            self._active_cancel_reason = 'cancel_requested'
            self._condition.notify_all()

    def _cleanup_outputs(self, job: _ServiceJob) -> None:
        if job.kind != 'render':
            return
        paths = []
        if job.variant in ('before', 'both') and job.before_output:
            paths.append(job.before_output)
        if job.variant in ('after', 'both') and job.after_output:
            paths.append(job.after_output)
        for path in paths:
            if path in job.preexisting_outputs:
                continue
            try:
                Path(path).unlink()
            except OSError:
                pass

    def _ticker_loop(self) -> None:
        next_tick = time.perf_counter()
        sequence = 0
        while not self._ticker_stop.is_set():
            next_tick += TICK_SECONDS
            delay = max(0.0, next_tick - time.perf_counter())
            if self._ticker_stop.wait(delay):
                return
            with self._condition:
                active = self._active
                cancel = self._active_cancel
                if active is None or (cancel is not None and cancel.is_set()):
                    continue
                sequence += 1
                payload = {
                    'type': 'progress',
                    'request_id': active.request_id,
                    'kind': active.kind,
                    'state': 'running',
                    'elapsed_ms': round(
                        max(0.0, time.monotonic() - active.created_at) * 1000.0, 3
                    ),
                    'tick_sequence': sequence,
                    'tick_hz': TICK_HZ,
                }
            self.emit(payload)

def _worker_main() -> int:
    """Resident worker entry point used by the subprocess bootstrap."""
    try:
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    server = _WorkerServer()
    try:
        _load_worker_runtime()
    except BaseException as error:
        server.emit({
            'type': 'fatal',
            'error_type': type(error).__name__,
            'message': str(error),
            'traceback': '',
        })
        return 2
    return server.run()


if __name__ == '__main__':
    raise SystemExit(_worker_main())


__all__ = [
    'AuditionServiceError',
    'MixdownAuditionService',
    'PROTOCOL_VERSION',
    'SERVICE_SCHEMA',
    'ServiceProtocolError',
    'ServiceStartError',
    'ServiceUnavailable',
    'TICK_HZ',
]