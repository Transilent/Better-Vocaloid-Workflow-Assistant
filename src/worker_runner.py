"""Unified, Job-Object-managed subprocess worker runner with cooperative cancellation.

Standardizes subprocess execution across VWA workers (project conversion, audio
utilities, model probes):
  - Always attaches a zero-dependency ctypes Win32JobObject(kill_on_close=True)
  - Injects UTF-8 / no-bytecode environment flags and CREATE_NO_WINDOW
  - Supports cooperative cancellation, hard timeout, and optional JSON-lines stdout callbacks
  - Guarantees atomic child + grandchild process tree cleanup on exit, cancel, or timeout
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from process_control import Win32JobObject, terminate_tree
from vwa_paths import ROOT


@dataclass(frozen=True)
class WorkerResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


def _kill_process(proc: subprocess.Popen, job_obj: Win32JobObject) -> None:
    if job_obj.assigned:
        job_obj.terminate(1)
    else:
        terminate_tree(proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_worker_process(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 180.0,
    on_stdout_line: Callable[[str], None] | None = None,
    on_json_event: Callable[[dict[str, Any]], None] | None = None,
    cancel_message: str = "已取消后台工作进程。",
    timeout_message: str = "后台工作进程超时，已自动停止。",
) -> WorkerResult:
    """Run *argv* in a bounded, Job-Object-supervised subprocess."""
    run_env = dict(os.environ if env is None else env)
    run_env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    if extra_env:
        run_env.update(extra_env)

    started = time.monotonic()
    deadline = started + max(0.1, float(timeout))
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT if cwd is None else cwd),
        env=run_env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain_stdout() -> None:
        assert proc.stdout is not None
        for raw_line in iter(proc.stdout.readline, b""):
            text = raw_line.decode("utf-8", errors="replace")
            stdout_chunks.append(text)
            stripped = text.strip()
            if not stripped:
                continue
            if on_stdout_line is not None:
                on_stdout_line(stripped)
            if on_json_event is not None and stripped.startswith("{") and stripped.endswith("}"):
                try:
                    event = json.loads(stripped)
                    if isinstance(event, dict):
                        on_json_event(event)
                except ValueError:
                    pass

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        data = proc.stderr.read()
        if data:
            stderr_chunks.append(data.decode("utf-8", errors="replace"))

    t_out = threading.Thread(target=_drain_stdout, daemon=True)
    t_err = threading.Thread(target=_drain_stderr, daemon=True)

    with Win32JobObject(kill_on_close=True) as job_obj:
        job_obj.assign(proc)
        t_out.start()
        t_err.start()
        try:
            while proc.poll() is None:
                if cancel is not None and cancel():
                    _kill_process(proc, job_obj)
                    raise InterruptedError(cancel_message)
                if time.monotonic() > deadline:
                    _kill_process(proc, job_obj)
                    raise TimeoutError(timeout_message)
                time.sleep(0.03)
        finally:
            if proc.poll() is None:
                _kill_process(proc, job_obj)
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    return WorkerResult(
        returncode=int(proc.returncode if proc.returncode is not None else -1),
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
