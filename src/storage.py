"""Explicit disposable locations. User state, sessions, models and results are protected."""
import shutil
from pathlib import Path
from common import ROOT, job_directories, app_path, file_hash, native_path
from task_store import size, checked_tree, locked, read_json

CACHE_NAMES = ('numba', 'browser-temp', 'v2m-temp', 'component-probe','qwen-runtime','export-app','video-mixdown','video-preview')


def publication_candidates(references=None):
    """Offer only unreferenced, unedited generated bundles, never finished media."""
    parent=ROOT/'publish-packages'
    if not parent.is_dir() or parent.is_symlink() or parent.is_junction(): return []
    protected=[]
    def protect(value):
        if isinstance(value,str) and value.strip():
            try: protected.append(app_path(value))
            except (OSError,ValueError): pass
    settings=read_json(ROOT/'publish-ui.json')
    for key in ('bundle','video','job'): protect(settings.get(key))
    inputs=settings.get('inputs',{})
    if isinstance(inputs,dict):
        for key in ('video','cover_file','job'): protect(inputs.get(key))
    for platform in ('bilibili','xiaohongshu'):
        active=read_json(ROOT/'cache/publish-browser'/platform/'active.json')
        for key in ('bundle','control_bundle'): protect(active.get(key))
    bundles=[]
    for bundle in parent.iterdir():
        if not bundle.is_dir() or bundle.is_symlink() or bundle.is_junction(): continue
        data=read_json(bundle/'manifest.json')
        protect(data.get('source_job'))
        for field,key in (('video','path'),('cover','source')):
            value=data.get(field,{})
            if isinstance(value,dict): protect(value.get(key))
        bundles.append(bundle)
    rows=[]
    if references is None:
        from task_contract import protected_references
        references = protected_references(job_directories())
    protected.extend(path for path, _, _ in references)
    for bundle in bundles:
        if any(path.is_relative_to(bundle.resolve()) for path in protected): continue
        inventory=read_json(bundle/'cleanup-inventory.json')
        entries=inventory.get('entries')
        if (inventory.get('version')!=1 or inventory.get('kind')!='publication-generated'
                or not isinstance(entries,list) or not 1<=len(entries)<=32): continue
        try:
            checked_tree(bundle,parent)
            names=[]
            for entry in entries:
                name=entry['file']
                if (not isinstance(name,str) or Path(name).name!=name or name in ('.','..','cleanup-inventory.json')
                        or Path(name).suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.json','.txt')):
                    raise ValueError('Invalid generated publication file')
                names.append(name)
                path=bundle/name
                if path.stat().st_size!=entry['bytes'] or file_hash(path)!=entry['sha256']:
                    raise ValueError('Publication files were edited')
            if (len(names)!=len(set(names)) or 'manifest.json' not in names
                    or {path.name for path in bundle.iterdir()}!=set(names)|{'cleanup-inventory.json'}): continue
        except (OSError,ValueError,KeyError,TypeError): continue
        rows.append({'kind':'publication','title':'未使用的发布资料 · '+bundle.name,'path':bundle,'bytes':size(bundle)})
    return rows


def candidates(include_protected=False):
    rows = []
    jobs = job_directories()
    from task_contract import protected_references
    references = protected_references(jobs)
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
    downloads = ROOT / 'cache/feature-downloads'
    if downloads.exists() and not any(locked(p) for p in downloads.iterdir() if p.is_dir()):
        rows.append({'kind':'component','title':'语言模型下载缓存','path':downloads,'bytes':size(downloads)})
    for job in jobs:
        temp = job/'temp'
        if temp.exists() and not locked(job):
            rows.append({'kind': 'temp', 'title': '任务临时文件 · '+job.name, 'path': temp, 'bytes': size(temp)})
    rows.extend(publication_candidates(references))
    preview=ROOT/'cache/publish-preview.jpg'
    if preview.is_file():
        settings=read_json(ROOT/'publish-ui.json');inputs=settings.get('inputs',{})
        chosen=inputs.get('cover_file') if isinstance(inputs,dict) else None
        if not chosen or app_path(chosen)!=preview.resolve():
            rows.append({'kind':'cache','title':'发布封面预览','path':preview,'bytes':size(preview)})
    for name in ('test-results', 'test-numba-cache'):
        path = ROOT/'work'/name
        if path.is_dir() and not path.is_symlink() and not path.is_junction():
            rows.append({'kind':'cache','title':'临时运行缓存 · '+name,'path':path,'bytes':size(path)})
    # A custom output directory can be placed anywhere. Never offer a cache
    # ancestor of a registered task for recursive cleanup.
    result = []
    for row in rows:
        target = row['path'].resolve()
        reason = next(('任务目录仍在使用此位置' for job in jobs if job.is_relative_to(target)), None)
        for path, owner, role in references:
            if path == target or path.is_relative_to(target) or target.is_relative_to(path):
                reason = f'任务 {owner.name} 仍引用此位置（{role}）'
                break
        if reason:
            if include_protected: result.append({**row, 'protected': reason})
        else:
            result.append(row)
    return result


def require_idle():
    previews=ROOT/'cache/video-preview'
    if previews.is_dir() and any(locked(path) for path in previews.iterdir() if path.is_dir()):
        raise ValueError('视频预览仍在读取素材，请停止预览后再清理缓存。')
    renders=ROOT/'cache/video-mixdown'
    if renders.is_dir() and any(locked(path) for path in renders.iterdir() if path.is_dir()):
        raise ValueError('视频正在合成，请停止合成后再清理缓存。')
    if any(locked(job) for job in job_directories()) or locked(ROOT/'dependencies/optional/bs-roformer'):
        raise ValueError('仍有任务或模型组件在其他窗口中运行，请完成后再清理缓存。')
    downloads = ROOT / 'cache/feature-downloads'
    if downloads.exists() and any(locked(p) for p in downloads.iterdir() if p.is_dir()):
        raise ValueError('仍有语言模型在下载，请完成后再清理缓存。')
    profiles = ROOT / 'cache/publish-browser'
    if any(locked(profiles / platform) for platform in ('bilibili','xiaohongshu')):
        raise ValueError('上传或登录窗口仍在使用缓存，请关闭这些窗口后再清理。')


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
        if row['path'].is_dir(): shutil.rmtree(native_path(row['path']))
        else: row['path'].unlink()
        count += row['bytes']
    return count
