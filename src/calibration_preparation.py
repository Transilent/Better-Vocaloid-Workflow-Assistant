"""Run calibration input changes away from the desktop's native runtime."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, file_hash, load_config
from process_control import terminate_tree


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _child(control):
    from calibration_workflow import prepare_copy

    def cancel():
        if (control / 'cancel').exists():
            raise InterruptedError('已停止准备校准副本。')

    def progress(message):
        atomic_json(control / 'progress.json', {'message': message})

    try:
        cancel()
        result = prepare_copy(**_read(control / 'request.json'), cancel=cancel, progress=progress)
    except Exception as error:
        atomic_json(control / 'done.json', {'error': str(error), 'cancelled': isinstance(error, InterruptedError)})
        return 1
    atomic_json(control / 'done.json', {'result': result})
    return 0


def prepare_in_worker(project, reference, destination, *, stop, progress, track_index, template=None,
                      repair_pitch=False, language=None):
    """Wait for one owned child; return only a checked, committed copy."""
    if stop.is_set():
        raise InterruptedError('已停止准备校准副本。')
    dest = Path(destination).resolve()
    if dest.exists():
        raise FileExistsError('准备目录已存在，请换用新的目录。')
    cache = ROOT / 'cache/v2m-temp'
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='cal-', dir=cache) as folder:
        control = Path(folder)
        atomic_json(control / 'request.json', {
            'project': str(project), 'reference': str(reference), 'destination': str(dest),
            'track_index': track_index, 'template': str(template) if template else None,
            'repair_pitch': repair_pitch, 'language': language,
        })
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'TMP': str(control), 'TEMP': str(control)}
        with (control / 'output.log').open('wb') as log:
            process = subprocess.Popen(
                [load_config()['python'], '-B', '-u', str(Path(__file__).resolve()), str(control)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            cancel_at, last = None, None
            try:
                while process.poll() is None:
                    if stop.is_set():
                        if cancel_at is None:
                            (control / 'cancel').touch()
                            cancel_at = monotonic()
                        elif monotonic() - cancel_at > 2:
                            terminate_tree(process)
                            break
                    try:
                        message = _read(control / 'progress.json')['message']
                    except (OSError, ValueError, KeyError):
                        message = None
                    if message and message != last:
                        progress(message)
                        last = message
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    terminate_tree(process)
                    process.wait(timeout=5)
        # A cancellation may arrive just after the commit. Adopt a verified
        # copy if it exists and never retry by overwriting its destination.
        receipt, output = dest / 'preparation.json', dest / 'prepared.svp'
        if receipt.is_file():
            result = _read(receipt)
            if (result.get('project') != str(output) or result.get('source') != str(Path(project).resolve())
                    or result.get('track_index') != track_index or result.get('repair_pitch') is not repair_pitch
                    or not output.is_file() or result.get('sha256') != file_hash(output)):
                raise ValueError('校准副本回执与本次请求不一致。')
            return result
        if stop.is_set():
            raise InterruptedError('已停止准备校准副本。')
        try:
            failure = _read(control / 'done.json')
        except (OSError, ValueError):
            failure = {}
        detail = failure.get('error') or f'校准准备进程退出（{process.returncode}）。'
        raise ValueError(str(detail)[:1200])


if __name__ == '__main__':
    sys.exit(_child(Path(sys.argv[1]).resolve(strict=True)))
