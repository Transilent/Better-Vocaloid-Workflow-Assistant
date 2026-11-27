"""Cancellable isolated model jobs and Windows-resilient temp dirs shared by chorus workers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time


def _clear_readonly(func, path, _exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def safe_rmtree(path, *, retries: int = 6, base_delay: float = 0.04) -> bool:
    """Remove a directory tree with bounded retry for transient Windows AV/handle locks."""
    target = Path(path)
    if not target.exists():
        return True
    for attempt in range(max(1, int(retries))):
        try:
            shutil.rmtree(target, onerror=_clear_readonly)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 >= retries:
                return False
            time.sleep(min(0.4, base_delay * (2 ** attempt)))
    return not target.exists()


def sweep_stale_tempdirs(parent, prefixes: tuple[str, ...], *, max_age_seconds: float = 900.0) -> int:
    """Best-effort cleanup of old leftover worker temp directories."""
    root = Path(parent)
    if not root.is_dir():
        return 0
    now = time.time()
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_dir():
            continue
        if prefixes and not any(entry.name.startswith(prefix) for prefix in prefixes):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age >= max_age_seconds and safe_rmtree(entry, retries=1, base_delay=0.0):
            removed += 1
    return removed


@contextmanager
def resilient_tempdir(parent, prefix: str = 'vwa-', *, stale_seconds: float = 900.0):
    """Temporary directory that tolerates transient Windows file locks during cleanup."""
    root = Path(parent)
    root.mkdir(parents=True, exist_ok=True)
    if stale_seconds > 0:
        sweep_stale_tempdirs(root, (prefix,), max_age_seconds=stale_seconds)
    folder = Path(tempfile.mkdtemp(dir=root, prefix=prefix))
    try:
        yield folder
    finally:
        safe_rmtree(folder)


@contextmanager
def model_batch(script, spec, cache, check, progress, labels, failure):
    from mixdown_runtime import child_python, worker_environment
    from process_control import Win32JobObject, terminate_tree

    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with resilient_tempdir(cache, 'model-') as folder:
        request = folder / 'request.json'
        request.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        log_path = folder / 'worker.log'
        with Win32JobObject(kill_on_close=True) as job:
            with log_path.open('w+b') as log:
                proc = subprocess.Popen(
                    [str(child_python()), '-B', '-u', str(Path(script).resolve()), str(request)],
                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    close_fds=True, env=worker_environment(),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                job.assign(proc)
                step = folder / 'progress.txt'
                seen = -1
                try:
                    while proc.poll() is None:
                        check()
                        try:
                            index = int(step.read_text(encoding='utf-8'))
                        except (OSError, ValueError):
                            index = -1
                        if index != seen and 0 <= index < len(labels):
                            seen = index
                            progress(index, labels[index])
                        try:
                            proc.wait(timeout=.15)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if proc.poll() is None:
                        try:
                            if job.assigned:
                                job.terminate(1)
                            else:
                                terminate_tree(proc)
                        finally:
                            if proc.poll() is None:
                                proc.kill()
                            try:
                                proc.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                if proc.returncode:
                    log.flush()
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 4096))
                    tail = log.read().decode('utf-8', errors='replace').strip().splitlines()
                    raise RuntimeError(f'{failure}：{tail[-1][:320]}' if tail else failure)
        check()
        yield folder
