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


def records(check=None):
    index_path = ROOT / 'cache/task-index.json'
    old = read_json(index_path).get('entries', {})
    if not isinstance(old, dict): old = {}
    entries, changed = {}, False
    rows = []
    for path in job_directories():
        if check and check(): return []
        try:
            stamps = {}
            for name in ('request.json','status.json','source-info.json','task.json'):
                try:
                    info = (path / name).stat()
                    stamps[name] = [info.st_size, info.st_mtime_ns]
                except FileNotFoundError: stamps[name] = None
            saved = old.get(str(path), {})
            cached = saved.get('row', {})
            if (saved.get('stamps') == stamps and isinstance(cached, dict)
                    and isinstance(cached.get('request'), dict) and isinstance(cached.get('status'), dict)
                    and isinstance(cached.get('title'), str) and isinstance(cached.get('modified'), (int, float))):
                row = dict(saved['row'])
            else:
                request, state, info = read_json(path/'request.json'), read_json(path/'status.json'), read_json(path/'source-info.json')
                if not request: continue
                row = {'title': read_json(path/'task.json').get('name') or info.get('title') or request.get('source_title') or request.get('bv') or path.name,
                       'request': request, 'status': state, 'created': request.get('created_at') or path.name.split('_')[-1],
                       'modified': stamps['status.json'][1]/1e9 if stamps['status.json'] else path.stat().st_mtime}
                changed = True
            entries[str(path)] = {'stamps': stamps, 'row': row}
        except OSError:
            continue  # A deleted/moved task must not fail the entire library.
        state = row['status']
        status = state.get('state', 'ready')
        if status == 'running' and not locked(path):
            status = 'interrupted'
        rows.append({**row, 'path': path, 'state': status})
    if changed or set(entries) != set(old):
        atomic_json(index_path, {'version': 1, 'entries': entries})
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
    if not path.exists() or path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 1024:
        return 0
    if path.is_file():
        return path.stat().st_size
    pending = [path]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024: continue
                        if entry.is_dir(follow_symlinks=False): pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False): total += info.st_size
                    except OSError: pass
        except OSError: pass
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
