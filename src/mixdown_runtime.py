"""Qt-free runtime discovery and file helpers for simple mixdown."""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable

from audio_mixdown import MixdownCancelled
from i18n import tr as _tr


PREVIEW_PREFIX = 'vwa-mx-'
_PEDALBOARD_VERSION = '0.9.26'
_OPTIONAL_PACKAGES: str | None = None


def check_cancel(check: Callable[[], Any] | None) -> None:
    if check and check():
        raise MixdownCancelled('混音操作已取消。')


def application_root() -> Path | None:
    """Locate the app tree containing component manifest and packaged installer."""
    try:
        from common import ROOT
        candidates = (Path(ROOT), Path(ROOT).parent)
    except Exception:
        candidates = (Path(__file__).resolve().parent,
                      Path(__file__).resolve().parents[1])
    for candidate in candidates:
        if ((candidate / 'components/pedalboard.json').is_file()
                and (candidate / 'tools/install_pedalboard_component.py').is_file()):
            return candidate.resolve()
    return None


def component_manifest() -> tuple[Path | None, dict[str, Any] | None, Path | None]:
    root = application_root()
    if root is None:
        return root, None, None
    manifest = root / 'components/pedalboard.json'
    try:
        value = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return root, None, None
    return root, value, root / 'tools/install_pedalboard_component.py'


def optional_packages(root: Path | None, spec: dict[str, Any] | None,
                      packages: str | os.PathLike[str] | None = None) -> Path | None:
    if packages:
        path = Path(packages).resolve()
    elif root is not None and spec:
        try:
            path = (root / spec['install']['root']
                    / spec['install']['packages']).resolve()
            if not (root / spec['install']['root']).resolve().is_relative_to(root.resolve()):
                return None
        except (KeyError, TypeError, OSError, ValueError):
            return None
    else:
        path = None
    return path if path and path.is_dir() else None


def cached_optional_packages() -> str | None:
    """Return only a still-present package directory from the last success."""
    global _OPTIONAL_PACKAGES
    value = _OPTIONAL_PACKAGES
    if value and Path(value).is_dir():
        return value
    _OPTIONAL_PACKAGES = None
    return None


def set_cached_optional_packages(packages: str | os.PathLike[str] | None) -> None:
    """Record a package path only after its isolated import check succeeds."""
    global _OPTIONAL_PACKAGES
    path = Path(packages).resolve() if packages else None
    _OPTIONAL_PACKAGES = str(path) if path and path.is_dir() else None


def child_python() -> Path:
    """Use a console Python for clean subprocesses, with an explicit fallback."""
    executable = Path(sys.executable).resolve()
    sibling = executable.with_name('python.exe')
    return sibling if sibling.is_file() else executable


def worker_environment(packages: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Build the isolated worker import path, reusing the last successful package."""
    selected = (optional_packages(None, None, packages) if packages
                else cached_optional_packages())
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    paths = []
    if selected:
        paths.append(str(Path(selected).resolve()))
    app_root = application_root()
    if app_root:
        paths.append(str(app_root))
        source = app_root / 'src'
        if source.is_dir():
            paths.append(str(source))
    paths.append(str(Path(__file__).resolve().parent))
    paths.extend(str(path) for path in sys.path if path)
    previous = env.get('PYTHONPATH')
    if previous:
        paths.extend(part for part in previous.split(os.pathsep) if part)
    ordered = list(dict.fromkeys(paths))
    # Embedded Python ignores PYTHONPATH because python312._pth is present.
    # Child bootstraps explicitly insert this custom list before imports.
    env['VWA_MIXDOWN_EXTRA_PATHS'] = os.pathsep.join(ordered)
    env['PYTHONPATH'] = os.pathsep.join(ordered)
    return env


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        from process_control import terminate_tree
        terminate_tree(process)
    except Exception:
        try:
            process.kill()
        except OSError:
            pass


def activate_pedalboard(packages: str | os.PathLike[str] | None = None,
                        check: Callable[[], Any] | None = None) -> tuple[bool, str]:
    """Verify Pedalboard in a clean Python process and cache only success."""
    candidate = None
    if packages:
        candidate = optional_packages(None, None, packages)
        if candidate is None:
            return False, '已安装的 Pedalboard 组件目录不存在。'
    else:
        cached = cached_optional_packages()
        candidate = Path(cached) if cached else None
    check_cancel(check)
    fake = sys.modules.get('pedalboard')
    if fake is not None and not getattr(fake, '__file__', None):
        version = str(getattr(fake, '__version__', ''))
        if version != _PEDALBOARD_VERSION:
            return False, ('Pedalboard 版本不符：需要 {0}，当前为 {1}。'
                           .format(_PEDALBOARD_VERSION, version or '未知'))
        if candidate is not None:
            set_cached_optional_packages(candidate)
        return True, version
    code = (
        "import os,sys; "
        "[sys.path.insert(0,p) for p in reversed(os.environ.get('VWA_MIXDOWN_EXTRA_PATHS','').split(os.pathsep)) if p]; "
        "import pedalboard; "
        "v=str(getattr(pedalboard,'__version__','')); "
        "assert v=='0.9.26', '版本不符: '+v; "
        "assert all(hasattr(pedalboard,n) for n in ('Pedalboard','HighpassFilter','PeakFilter','Compressor','Reverb')), '组件不完整'; "
        "print(v)"
    )
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(
            [str(child_python()), '-B', '-c', code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=worker_environment(candidate),
            creationflags=creationflags,
        )
    except OSError as error:
        return False, 'Pedalboard 组件检查失败：' + str(error)
    deadline = time.monotonic() + 20.0
    stdout = stderr = ''
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.10)
                break
            except subprocess.TimeoutExpired:
                check_cancel(check)
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    return False, 'Pedalboard 组件检查失败：检查超时。'
    except BaseException:
        _terminate_process(process)
        raise
    if process.returncode:
        detail = (stderr or '').strip().splitlines()
        return False, 'Pedalboard 组件检查失败：' + (detail[-1] if detail else '未知错误')
    version = (stdout or '').strip() or _PEDALBOARD_VERSION
    if candidate is not None:
        set_cached_optional_packages(candidate)
    return True, version


def run_component_installer(action: str, root: Path, installer: Path,
                            check: Callable[[], Any] | None,
                            force: bool = False) -> dict[str, Any]:
    """Run the packaged component installer without a shell or PowerShell."""
    if not installer or not Path(installer).is_file():
        raise FileNotFoundError('混音组件安装器未随当前版本提供。')
    python = child_python()
    argv = [str(python), '-B', str(installer), action, '--app-root', str(root)]
    if action == 'install':
        argv.extend(['--python', str(python)])
    if action == 'install' and force:
        argv.append('--force')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        while process.poll() is None:
            check_cancel(check)
            time.sleep(0.10)
        stdout, stderr = process.communicate()
    finally:
        if process.poll() is None:
            _terminate_process(process)
            process.wait(timeout=5)
    if process.returncode:
        detail = (stderr or stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else '混音组件安装器返回失败。')
    try:
        value = json.loads(stdout)
    except ValueError:
        raise ValueError('混音组件安装器没有返回可读状态。')
    if not isinstance(value, dict):
        raise ValueError('混音组件安装器返回状态无效。')
    return value


def file_sha256(path: str | os.PathLike[str],
                check: Callable[[], Any] | None = None) -> str:
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            check_cancel(check)
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def normal_path_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def same_path(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def discard_preview_directory(path: Any) -> None:
    if not path:
        return
    try:
        candidate = Path(path).absolute()
        root = Path(tempfile.gettempdir()).resolve()
        if (candidate.parent.resolve() != root
                or not (candidate.name.startswith(PREVIEW_PREFIX)
                        or candidate.name.startswith('vwa-audio-mixdown-'))):
            return
        if candidate.is_symlink() or candidate.is_junction():
            return
        shutil.rmtree(candidate, ignore_errors=True)
    except (OSError, ValueError):
        pass


def task_source(job: Any, names: Iterable[str]) -> Path | None:
    if not job:
        return None
    job = Path(job)
    for name in names:
        path = job / name
        if path.is_file():
            return path.resolve()
    return None


def suggested_output(job: Any) -> Path:
    directory = (Path(job) / 'rendered') if job else Path.cwd()
    stem = 'simple-mixdown-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    path = directory / (stem + '.wav')
    index = 2
    while path.exists():
        path = directory / (stem + '-' + str(index) + '.wav')
        index += 1
    return path


def report_detail(report: dict[str, Any]) -> str:
    parts = []
    try:
        parts.append(_tr('时长：{0:.3f} 秒').format(float(report['duration_seconds'])))
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('时长：后端未报告'))
    try:
        peak = float(report['peak_dbfs'])
        value = (_tr('静音') if not math.isfinite(peak) or peak <= -143.0
                 else '{:.2f} dBFS'.format(peak))
        parts.append(_tr('峰值：') + value)
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('峰值：后端未报告'))
    return '；'.join(parts)


__all__ = [
    'MixdownCancelled',
    'activate_pedalboard',
    'application_root',
    'cached_optional_packages',
    'check_cancel',
    'child_python',
    'component_manifest',
    'discard_preview_directory',
    'file_sha256',
    'normal_path_key',
    'optional_packages',
    'report_detail',
    'run_component_installer',
    'same_path',
    'set_cached_optional_packages',
    'suggested_output',
    'task_source',
    'worker_environment',
]