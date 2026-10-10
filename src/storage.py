"""Explicit disposable locations. User state, sessions, models and results are protected."""
import shutil
from pathlib import Path
from common import ROOT, job_directories
from task_store import size, checked_tree, locked

CACHE_NAMES = ('numba', 'browser-temp', 'v2m-temp', 'component-probe')


def candidates():
    rows = []
    for name in CACHE_NAMES:
        path = ROOT/'cache'/name
        if path.exists():
            rows.append({'kind': 'cache', 'title': '运行缓存 · '+name, 'path': path, 'bytes': size(path)})
    update = ROOT/'cache/updates'
    if update.exists() and not (update/'pending.json').exists():
        rows.append({'kind': 'cache', 'title': '未使用的更新下载', 'path': update, 'bytes': size(update)})
    downloads = ROOT/'dependencies/optional/bs-roformer/downloads'
    if downloads.exists() and not locked(downloads.parent):
        rows.append({'kind': 'component', 'title': '组件安装下载缓存', 'path': downloads, 'bytes': size(downloads)})
    for job in job_directories():
        temp = job/'temp'
        if temp.exists() and not locked(job):
            rows.append({'kind': 'temp', 'title': '任务临时文件 · '+job.name, 'path': temp, 'bytes': size(temp)})
    return rows


def require_idle():
    if any(locked(job) for job in job_directories()) or locked(ROOT/'dependencies/optional/bs-roformer'):
        raise ValueError('仍有任务或模型组件在其他窗口中运行，请完成后再清理缓存。')


def clean(paths):
    require_idle()
    allowed = {str(row['path'].resolve()): row for row in candidates()}
    checked = []
    for value in paths:
        key = str(Path(value).resolve())
        if key not in allowed:
            raise ValueError('清理目标已变化或不属于可清理缓存，请重新扫描。')
        row = allowed[key]
        boundary = row['path'].parent
        checked_tree(row['path'], boundary)
        checked.append(row)
    count = 0
    for row in checked:
        shutil.rmtree(row['path'])
        count += row['bytes']
    return count
