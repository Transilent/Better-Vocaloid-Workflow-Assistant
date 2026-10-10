"""Task metadata, bounded deletion and stale-run detection."""
import json
import os
import shutil
from pathlib import Path
from common import ROOT, atomic_json, job_directories, app_path, workspace_state


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def locked(job):
    import msvcrt
    path = Path(job) / 'run.lock'
    if not path.exists():
        return False
    try:
        with path.open('r+b') as stream:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True


def records():
    rows = []
    for path in job_directories():
        request, state, info = read_json(path/'request.json'), read_json(path/'status.json'), read_json(path/'source-info.json')
        if not request:
            continue
        status = state.get('state', 'ready')
        if status == 'running' and not locked(path):
            status = 'interrupted'
        rows.append({'path': path, 'title': read_json(path/'task.json').get('name') or info.get('title') or request.get('source_title') or request.get('bv') or path.name,
                     'state': status, 'request': request, 'status': state,
                     'created': request.get('created_at') or path.name.split('_')[-1],
                     'modified': (path/'status.json').stat().st_mtime if (path/'status.json').exists() else path.stat().st_mtime})
    return sorted(rows, key=lambda r: r['modified'], reverse=True)


def checked_tree(path, boundary):
    """Refuse links/junctions before any recursive operation; never follow them."""
    path, boundary = Path(path).absolute(), Path(boundary).resolve()
    if path == boundary or not path.resolve().is_relative_to(boundary):
        raise ValueError('清理目标超出允许的目录。')
    for candidate in (path, *path.parents):
        if candidate == boundary:
            break
        if candidate.is_symlink() or (candidate.exists() and getattr(candidate.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('目录包含链接或重解析点，已跳过清理。')
    if path.is_dir():
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                candidate = Path(directory)/name
                if candidate.is_symlink() or getattr(candidate.lstat(), 'st_file_attributes', 0) & 1024:
                    raise ValueError('目录包含链接或重解析点，已跳过清理。')
                if not candidate.resolve().is_relative_to(path.resolve()):
                    raise ValueError('清理目标包含外部文件。')
    return path


def size(path):
    total = 0
    path = Path(path)
    if not path.exists() or path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    for directory, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory)/name).is_symlink() and not getattr((Path(directory)/name).lstat(), 'st_file_attributes', 0) & 1024]
        for name in files:
            item = Path(directory)/name
            try:
                if not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                pass
    return total


def known_job(job):
    job = Path(job).resolve()
    if job not in job_directories() or not (job/'request.json').is_file():
        raise ValueError('只能管理任务列表中已登记的任务。')
    if locked(job):
        raise ValueError('任务正在运行，请停止后再操作。')
    return job


def rename(job, name):
    job = known_job(job)
    if not name.strip() or len(name.strip()) > 120:
        raise ValueError('任务名应为 1 到 120 个字符。')
    atomic_json(job/'task.json', {'name': name.strip()})


def delete(job):
    job = known_job(job)
    checked_tree(job, job.parent)
    shutil.rmtree(job)
    last = ROOT/'last_job.txt'
    if last.exists() and app_path(last.read_text(encoding='utf-8').strip()) == job:
        last.unlink()


def format_size(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024
