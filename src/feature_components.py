"""Repair/download independently optional original model files, with pinned SHA-256."""
import json
from pathlib import Path
import shutil
from common import ROOT, file_hash, dependency_manifest


def specification(feature):
    return json.loads((ROOT / 'components/runtime-features.json').read_text(encoding='utf-8'))['features'][feature]


def install(feature, progress, check=None, source='auto'):
    from optional_components import download
    from pipeline import job_lock
    from task_store import checked_tree
    spec = specification(feature)
    catalog = {e['file']: e for e in dependency_manifest().get('entries', [])}
    for item in spec['files']:
        pinned = catalog.get(item['file'], {})
        if (item['bytes'], item['sha256']) != (pinned.get('bytes'), pinned.get('sha256')):
            raise ValueError('组件清单与原始模型校验信息不一致，已停止安装。')
    folder = ROOT / 'cache/feature-downloads' / feature
    folder.mkdir(parents=True, exist_ok=True)
    checked_tree(folder, ROOT)
    with job_lock(folder):
        required = []
        for item in spec['files']:
            target = ROOT / item['file']
            if not target.resolve().is_relative_to(ROOT): raise ValueError('依赖安装路径超出助手目录。')
            if target.is_file() and target.stat().st_size == item['bytes'] and file_hash(target) == item['sha256']:
                continue
            required.append(item)
        needed = sum(item['bytes'] for item in required) + 64 * 1024**2
        if required and shutil.disk_usage(ROOT).free < needed:
            raise OSError('安装原始模型所需的可用空间不足，请先清理缓存或更换保存位置。')
        total = sum(item['bytes'] for item in spec['files'])
        done = total - sum(item['bytes'] for item in required)
        for item in required:
            if check and check(): raise InterruptedError('已取消下载，已下载部分可以继续使用。')
            def update(current, size, message): progress(done + current, total, message)
            path = download({'name': Path(item['file']).name, **{key:item[key] for key in ('bytes','sha256','url')}},
                            folder, update, check, source=source)
            target = ROOT / item['file']
            target.parent.mkdir(parents=True, exist_ok=True)
            checked_tree(target.parent, ROOT)
            if target.is_symlink(): raise ValueError('依赖安装目标不能是链接。')
            path.replace(target)
            done += item['bytes']; progress(done, total, '原始模型校验通过')
        progress(total, total, '中文原始模型已就绪')
    return {'ready': True, 'feature': feature, 'files': len(spec['files'])}
