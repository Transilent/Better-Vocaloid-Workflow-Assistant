"""Cancellable isolated model jobs shared by the chorus analyzers."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


@contextmanager
def model_batch(script, spec, cache, check, progress, labels, failure):
    from mixdown_runtime import child_python, worker_environment
    from process_control import terminate_tree

    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=cache, prefix='model-') as temporary:
        folder = Path(temporary)
        request = folder / 'request.json'
        request.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        with (folder / 'worker.log').open('w+b') as log:
            proc = subprocess.Popen([str(child_python()), '-B', '-u', str(Path(script).resolve()), str(request)],
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=worker_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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
                        terminate_tree(proc)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait()
            if proc.returncode:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 4096))
                tail = log.read().decode('utf-8', errors='replace').strip().splitlines()
                raise RuntimeError(f'{failure}：{tail[-1][:320]}' if tail else failure)
        check()
        yield folder
