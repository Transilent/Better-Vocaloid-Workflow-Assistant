"""Cancellable isolated model jobs and Windows-resilient temp dirs shared by chorus workers."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

from vwa_io import resilient_tempdir, safe_rmtree, sweep_stale_tempdirs

__all__ = ('model_batch', 'resilient_tempdir', 'safe_rmtree', 'sweep_stale_tempdirs')


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
