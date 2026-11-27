"""Pure Python browser-session lifecycle & status reader for QuickPublication (Qt-free)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping

from common import ROOT, load_config

BILIBILI = 'bilibili'
BROWSER_STATUS_FILENAME = BILIBILI + '_上传状态.json'
_MAX_BROWSER_STATUS_BYTES = 64 * 1024


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ''


def _read_json(path: Path, limit: int = 2 * 1024 * 1024) -> Any:
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f'{path.name} 文件过大。')
    return json.loads(path.read_text(encoding='utf-8'))


@dataclass
class _BrowserSession:
    process: Any = field(default=None, repr=False, compare=False)
    log_stream: Any = field(default=None, repr=False, compare=False)
    mode: str = ''
    output: str = ''
    control_bundle: str = ''
    log_path: str = ''
    state: str = 'idle'
    message: str = '尚未启动 B站填写会话。'
    updated_at: str = ''
    checks: dict[str, bool] = field(default_factory=dict)
    returncode: int | None = None
    started_ns: int = 0


def browser_root_path(owner: Any) -> Path:
    override = getattr(owner, 'quick_publication_browser_root', None)
    return Path(override or (Path(ROOT) / 'cache/publish-browser')).resolve()


def browser_profile_path(owner: Any) -> Path:
    root = browser_root_path(owner)
    return (root if root.name == BILIBILI else root / BILIBILI).resolve()


def read_active_browser_record(owner: Any):
    path = browser_profile_path(owner) / 'active.json'
    try:
        value = _read_json(path, limit=_MAX_BROWSER_STATUS_BYTES)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def browser_output_allowed(owner: Any, state: Any, path: Path) -> bool:
    candidate = Path(path).resolve()
    profile = browser_profile_path(owner)
    if candidate == profile:
        return True
    if state.bundle:
        try:
            if candidate == Path(state.bundle).resolve():
                return True
        except (OSError, ValueError):
            pass
    try:
        return candidate.is_relative_to((Path(ROOT) / 'publish-packages').resolve())
    except (OSError, ValueError):
        return False


def browser_profile_locked(owner: Any) -> bool:
    profile = browser_profile_path(owner)
    try:
        from task_store import locked
        return bool(locked(profile))
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def browser_live(owner: Any, state: Any) -> bool:
    process = state.browser.process
    if process is not None:
        try:
            return process.poll() is None
        except OSError:
            return True
    return read_active_browser_record(owner) is not None and browser_profile_locked(owner)


def storage_busy(owner: Any) -> bool:
    storage = getattr(owner, 'storage', None)
    if storage is None:
        support = getattr(owner, 'support', None)
        storage = getattr(support, 'storage', None)
    if isinstance(storage, Mapping):
        return bool(storage.get('busy'))
    busy = getattr(storage, 'busy', None)
    try:
        return bool(callable(busy) and busy())
    except Exception:
        return True


def launch_browser(
    owner: Any,
    state: Any,
    *,
    login_only: bool,
    bundle: Path | None,
    script_dir: Path | None = None,
) -> None:
    if browser_live(owner, state):
        raise ValueError('B站填写会话已经打开。')
    if storage_busy(owner):
        raise ValueError('缓存管理正在进行，请稍后再打开 B站。')
    base_dir = Path(script_dir) if script_dir is not None else Path(__file__).resolve().parent
    script = (base_dir / 'publish_browser.py').resolve()
    if not script.is_file():
        raise ValueError('找不到现有 B站填写服务 publish_browser.py。')
    override = getattr(owner, 'quick_publication_python', '')
    try:
        if override:
            python = Path(str(override)).resolve()
        else:
            python = Path(str(load_config().get('python') or sys.executable)).resolve()
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('无法确定 Python 运行环境。') from exc
    if not python.is_file():
        raise ValueError('配置的 Python 运行环境不存在。')
    profile = browser_profile_path(owner)
    profile.mkdir(parents=True, exist_ok=True)
    output = profile if login_only else Path(bundle).resolve()
    if not login_only and (bundle is None or not output.is_dir()):
        raise ValueError('请先生成并核对当前发布资料。')
    log_path = output / (BILIBILI + '_浏览器.log')
    log = log_path.open('a', encoding='utf-8')
    argv = [str(python), '-B', '-u', str(script), '--platform', BILIBILI]
    if login_only:
        argv.append('--login')
    else:
        argv.extend(['--bundle', str(output)])
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(
            argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, env=env, creationflags=flags, close_fds=True,
        )
    except Exception:
        log.close()
        raise
    old = state.browser
    if old.log_stream is not None:
        try:
            old.log_stream.close()
        except OSError:
            pass
    state.browser = _BrowserSession(
        process=process, log_stream=log, mode='login' if login_only else 'fill',
        output=str(output), control_bundle=str(output), log_path=str(log_path),
        state='starting',
        message=('正在打开隔离登录窗口…' if login_only else '正在打开 B站填写会话…'),
        started_ns=time.time_ns(),
    )
    state.message = ('正在打开 B站隔离登录会话；登录后请关闭窗口。'
                     if login_only else
                     '正在打开 B站填写会话；登录、核对与最终发布由你完成。')


def status_candidates(state: Any) -> list[Path]:
    session = state.browser
    paths = []
    for value in (session.output, session.control_bundle):
        if value:
            path = Path(value).resolve() / BROWSER_STATUS_FILENAME
            if path not in paths:
                paths.append(path)
    return paths


def browser_log_path(owner: Any, state: Any):
    session = state.browser
    profile = browser_profile_path(owner)
    for directory in (session.output, state.bundle, str(profile)):
        if not directory:
            continue
        try:
            folder = Path(directory).resolve(strict=True)
            if not browser_output_allowed(owner, state, folder):
                continue
            path = folder / (BILIBILI + '_浏览器.log')
            if (path.is_file() and path.resolve(strict=True) == path and not path.is_symlink()
                    and not getattr(path.lstat(), 'st_file_attributes', 0) & 1024):
                return path
        except (OSError, ValueError):
            continue
    return None


def refresh_browser(owner: Any, state: Any) -> None:
    session = state.browser
    active = read_active_browser_record(owner)
    process = session.process
    own_live = False
    if process is not None:
        try:
            own_live = process.poll() is None
        except OSError:
            own_live = True
    if process is None and active is not None and browser_profile_locked(owner):
        output = Path(_text(active.get('bundle'))).resolve()
        if not browser_output_allowed(owner, state, output):
            return
        state.browser = _BrowserSession(
            mode='login' if output == browser_profile_path(owner) else 'fill',
            output=str(output),
            control_bundle=str(Path(_text(active.get('control_bundle')) or output).resolve()),
            state='active',
            message='已发现现有 B站填写会话，正在读取回执…',
        )
        session = state.browser
        own_live = True
    live = own_live
    candidates = []
    for path in status_candidates(state):
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        if session.started_ns and modified < session.started_ns:
            continue
        candidates.append((modified, path))
    if candidates:
        path = max(candidates)[1]
        try:
            status = _read_json(path, limit=_MAX_BROWSER_STATUS_BYTES)
        except (OSError, ValueError, TypeError):
            status = None
        if isinstance(status, dict):
            state_name = _text(status.get('state')) or 'active'
            checks = status.get('checks') if isinstance(status.get('checks'), dict) else {}
            session.state = state_name
            session.message = _text(status.get('message')) or state_name
            session.updated_at = _text(status.get('updated_at'))
            session.checks = {key: value for key, value in checks.items() if type(value) is bool}
            control = status.get('control_bundle')
            if isinstance(control, str) and control.strip():
                session.control_bundle = str(Path(control).resolve())
    if not live and session.process is not None:
        try:
            returncode = session.process.poll()
        except OSError:
            returncode = None
        session.returncode = returncode
        stream, session.log_stream = session.log_stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if session.state not in ('failed', 'cancelled', 'login_window_closed', 'needs_manual'):
            session.state = 'closed'
            session.message = ('B站填写会话已关闭；最终发布状态必须由你在网页确认。'
                               if returncode in (None, 0) else
                               'B站填写会话异常结束，请查看填写日志。')


def browser_view(owner: Any, state: Any, active_kind: str) -> dict[str, Any]:
    session = state.browser
    live = bool(browser_live(owner, state))
    selected = BILIBILI in state.selected_platforms
    verified = bool(
        selected and state.verified and not state.bundle_stale
        and state.handoffs.get(BILIBILI)
    )
    return {
        'state': session.state,
        'message': session.message,
        'active': live,
        'mode': session.mode,
        'pid': int(getattr(session.process, 'pid', 0) or 0),
        'updatedAt': session.updated_at,
        'checks': copy.deepcopy(session.checks),
        'output': session.output,
        'controlBundle': session.control_bundle,
        'returncode': session.returncode,
        'finalPublish': 'human_only',
        'hasLog': browser_log_path(owner, state) is not None,
        'canLogin': bool(not live and not state.busy and not active_kind),
        'canOpen': bool(verified and not live and not state.busy and not active_kind),
        'canContinue': bool(verified and live and session.mode == 'fill'
                            and session.state != 'stopping'
                            and session.checks.get('video_selected')
                            and not state.busy and not active_kind),
        'canStop': bool(live and session.state != 'stopping'
                        and not state.busy and not active_kind),
    }


def browser_signature(owner: Any, state: Any, active_kind: str) -> tuple:
    browser = browser_view(owner, state, active_kind)
    return (browser['state'], browser['message'], browser['active'], browser['mode'],
            tuple(sorted(browser['checks'].items())), browser['updatedAt'],
            browser['controlBundle'], browser['returncode'])
