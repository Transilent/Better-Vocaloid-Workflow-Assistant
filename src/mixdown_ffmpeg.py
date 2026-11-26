"""Shared cancellable FFmpeg runner and error types for mixdown and loudness modules."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable

_LOG = logging.getLogger(__name__)


class MixdownError(RuntimeError):
    """A handled mixdown failure suitable for display to the user."""


class MixdownCancelled(InterruptedError):
    """Raised when a caller-provided cancellation callback requests a stop."""


def _check_cancel(cancel: Callable[[], Any] | None) -> None:
    if cancel is None:
        return
    requested = cancel()
    if requested:
        raise MixdownCancelled("混音已取消。")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    try:
        from process_control import terminate_tree
        terminate_tree(process)
    except Exception:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _run_ffmpeg(argv: list[str], log_path: Path, cancel: Callable[[], Any] | None,
                failure_message: str, *, timeout: float | None = None,
                allow_failure: bool = False) -> tuple[int, str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + timeout if timeout is not None else None
            while True:
                try:
                    return_code = process.wait(timeout=0.10)
                    break
                except subprocess.TimeoutExpired:
                    try:
                        _check_cancel(cancel)
                    except BaseException:
                        _terminate_process(process)
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        _terminate_process(process)
                        raise MixdownError(failure_message + " FFmpeg 处理超时。") from None
    except OSError as error:
        raise MixdownError(failure_message + f" 无法启动 FFmpeg：{error}") from error

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if return_code and not allow_failure:
        detail = text.strip()
        if len(detail) > 2500:
            detail = detail[-2500:]
        suffix = f" FFmpeg：{detail}" if detail else f" FFmpeg 返回代码 {return_code}。"
        raise MixdownError(failure_message + suffix)
    return return_code, text


def _ffmpeg_executable() -> Path:
    candidates: list[Path] = []
    try:
        from common import load_config
        candidates.append(Path(load_config()["ffmpeg"]))
    except Exception as exc:
        _LOG.debug("load_config ffmpeg lookup skipped: %s", exc)

    source_root = Path(__file__).resolve().parent
    for root in (source_root, source_root.parent):
        config_path = root / "config.json"
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            value = data.get("ffmpeg")
            if value:
                candidate = Path(str(value)).expanduser()
                candidates.append(candidate if candidate.is_absolute() else root / candidate)
        except (OSError, ValueError, TypeError):
            pass

    executable = shutil.which("ffmpeg")
    if executable:
        candidates.append(Path(executable))

    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate.is_file():
            return candidate
    raise MixdownError("找不到应用配置的 FFmpeg，请先检查 VWA 的运行时依赖。")
