"""Pinned, resumable installation for optional runtime model features."""
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from common import ROOT, atomic_json, dependency_manifest, native_path

_SHA256 = re.compile(r'[a-f0-9]{64}')
_REVISION = re.compile(r'[a-f0-9]{40}')
_FEATURE = re.compile(r'[a-z0-9_-]+')


def _state_path(root, feature):
    return Path(root) / 'cache/feature-state' / (feature + '.json')


def _state_parent(root):
    folder = Path(root) / 'cache/feature-state'
    for candidate in (Path(root) / 'cache', folder):
        if candidate.exists() and _is_link(candidate):
            raise ValueError('组件状态目录不能经过链接或重解析点。')
    return folder


def _load_state(root, feature, spec):
    try:
        _state_parent(root)
    except ValueError:
        return {}
    path = _state_path(root, feature)
    if _is_link(path) or not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    if (not isinstance(state, dict) or state.get('schema') != 1
            or state.get('revision') != spec.get('revision')
            or not isinstance(state.get('failed_files'), list)):
        return {}
    return state


def _save_state(root, feature, spec, failed_files):
    failed_files = sorted({str(value) for value in failed_files if isinstance(value, str)})
    state = {'schema': 1, 'revision': spec.get('revision'), 'failed_files': failed_files}
    folder = _state_parent(root)
    folder.mkdir(parents=True, exist_ok=True)
    from task_store import checked_tree
    checked_tree(folder, Path(root).resolve())
    atomic_json(_state_path(root, feature), state)


def specification(feature, root=None):
    if not isinstance(feature, str) or not _FEATURE.fullmatch(feature):
        raise ValueError('未知的组件标识。')
    root = Path(ROOT if root is None else root)
    data = json.loads((root / 'components/runtime-features.json').read_text(encoding='utf-8'))
    try:
        return data['features'][feature]
    except (KeyError, TypeError) as exc:
        raise ValueError('组件清单中没有此功能。') from exc


def _is_link(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def _safe_target(root, item):
    relative = PurePosixPath(str(item.get('file', '')))
    if (not relative.parts or relative.is_absolute() or '\\' in str(relative)
            or any(part in ('', '.', '..') or ':' in part or '\x00' in part
                   for part in relative.parts)):
        raise ValueError('组件安装路径无效。')
    target = Path(root).joinpath(*relative.parts)
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError('依赖安装路径超出助手目录。')
    current = Path(root)
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError('依赖安装目标不能经过链接或重解析点。')
    return target


def _validated(feature, root=None):
    root = Path(ROOT if root is None else root).resolve()
    spec = specification(feature, root)
    if not isinstance(spec, dict) or not isinstance(spec.get('files'), list) or not spec['files']:
        raise ValueError('组件清单中的文件列表无效。')
    revision = spec.get('revision')
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError('组件清单缺少固定的版本 SHA。')
    catalog = {entry.get('file'): entry for entry in dependency_manifest().get('entries', [])
               if isinstance(entry, dict)}
    seen = set()
    items = []
    total = 0
    for raw in spec['files']:
        if not isinstance(raw, dict):
            raise ValueError('组件清单中的文件记录无效。')
        item = dict(raw)
        relative = str(item.get('file', ''))
        target = _safe_target(root, item)
        name = Path(relative).name
        size = item.get('bytes')
        digest = item.get('sha256')
        url = item.get('url')
        if relative in seen or not name or name in ('.', '..'):
            raise ValueError('组件清单包含重复或无效的文件名。')
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError('组件清单中的文件大小无效。')
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError('组件清单中的 SHA-256 无效。')
        if not isinstance(url, str):
            raise ValueError('组件下载地址无效。')
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError('组件下载地址无效。') from exc
        if (parsed.scheme != 'https' or parsed.hostname != 'huggingface.co'
                or parsed.username or parsed.password or port not in (None, 443)
                or parsed.fragment or parsed.query):
            raise ValueError('组件下载地址必须是固定的公开 HTTPS 文件。')
        if '/resolve/' + revision + '/' not in parsed.path or parsed.path.rsplit('/', 1)[-1] != name:
            raise ValueError('组件下载地址没有固定到清单版本。')
        pinned = catalog.get(relative, {})
        if (item.get('bytes'), item.get('sha256')) != (pinned.get('bytes'), pinned.get('sha256')):
            raise ValueError('组件清单与原始模型校验信息不一致，已停止安装。')
        # Validate the selected route before creating any cache or target files.
        from download_sources import download_urls
        download_urls(url, 'auto')
        item['file'] = relative
        item['name'] = name
        item['target'] = target
        items.append(item)
        seen.add(relative)
        total += size
    if spec.get('bytes') != total:
        raise ValueError('组件总大小与文件清单不一致。')
    return spec, items


def _read_segment_bytes(folder, item):
    stage = folder / (item['name'] + '.segments')
    if not stage.is_dir() or _is_link(stage):
        return 0
    data = stage / 'data.part'
    receipt = stage / 'segments.json'
    if not data.is_file() or _is_link(data) or not receipt.is_file() or _is_link(receipt):
        return 0
    try:
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        lengths = saved.get('lengths')
        if (saved.get('bytes') != item['bytes'] or saved.get('sha256') != item['sha256']
                or not isinstance(lengths, list)
                or any(not isinstance(value, int) or value < 0 for value in lengths)):
            return 0
        return min(item['bytes'], sum(lengths))
    except (OSError, ValueError, TypeError):
        return 0


def _cached_bytes(folder, item):
    """Count only resumable bytes owned by this feature's download cache."""
    if _is_link(folder):
        return 0
    completed = folder / item['name']
    if completed.is_file() and not _is_link(completed) and completed.stat().st_size == item['bytes']:
        return item['bytes']
    partial = folder / (item['name'] + '.partial')
    if partial.is_file() and not _is_link(partial):
        return min(item['bytes'], max(0, partial.stat().st_size))
    return _read_segment_bytes(folder, item)


def _hash_file(path, check=None, progress=None, base=0, total=None):
    digest = hashlib.sha256()
    read = 0
    last_report = 0
    with open(native_path(path), 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            if check and check():
                raise InterruptedError('已取消操作；已完成的中文模型文件会保留。')
            digest.update(block)
            read += len(block)
            if progress and (read - last_report >= 8 * 1024**2):
                progress(base + read, total)
                last_report = read
    if progress:
        progress(base + read, total)
    return digest.hexdigest()


def _raise_if_cancelled(check):
    if check and check():
        raise InterruptedError('已取消下载；已下载部分会保留，可稍后继续。')


def status(feature, verify=False, check=None, root=None):
    """Return a fast size-based state; verify=True performs a cancellable SHA pass."""
    root = Path(ROOT if root is None else root).resolve()
    try:
        spec, items = _validated(feature, root)
    except InterruptedError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'feature': feature, 'ready': False, 'verified': False, 'state': 'error',
                'error': str(exc), 'bytes': 0, 'files': 0, 'missing': [], 'missing_files': [],
                'invalid_files': [], 'complete_files': 0, 'installed_files': 0,
                'present_files': 0, 'installed': False,
                'complete_bytes': 0, 'partial_bytes': 0, 'resumable_bytes': 0,
                'downloaded_bytes': 0}
    folder = root / 'cache/feature-downloads' / feature
    state = _load_state(root, feature, spec)
    failed = set(state.get('failed_files', ()))
    missing, invalid = [], []
    complete = 0
    present = 0
    complete_bytes = 0
    cache_bytes = 0
    for item in items:
        _raise_if_cancelled(check)
        target = item['target']
        try:
            valid = target.is_file() and not _is_link(target) and target.stat().st_size == item['bytes']
        except OSError:
            valid = False
        if valid and verify:
            valid = _hash_file(target, check) == item['sha256']
        if valid:
            present += 1
        if valid and item['file'] in failed and not verify:
            valid = False
        if valid:
            complete += 1
            complete_bytes += item['bytes']
        else:
            missing.append(item['file'])
            if target.exists() or item['file'] in failed:
                invalid.append(item['file'])
            try:
                cache_bytes += _cached_bytes(folder, item)
            except OSError:
                pass
    downloaded = min(spec['bytes'], complete_bytes + cache_bytes)
    ready = complete == len(items)
    if ready:
        state = 'ready'
    elif invalid:
        state = 'damaged'
    elif complete or cache_bytes:
        state = 'partial'
    else:
        state = 'missing'
    installed = present == len(items)
    return {'feature': feature, 'name': spec.get('name', feature), 'revision': spec.get('revision'),
            'ready': ready, 'installed': installed, 'verified': bool(verify and ready), 'state': state,
            'present_files': present,
            'bytes': spec['bytes'], 'files': len(items), 'missing': missing,
            'missing_files': missing, 'invalid_files': invalid, 'complete_files': complete,
            'installed_files': complete, 'complete_bytes': complete_bytes,
            'partial_bytes': cache_bytes, 'resumable_bytes': cache_bytes,
            'downloaded_bytes': downloaded,
            'progress': round(downloaded * 100 / max(spec['bytes'], 1))}


def verify(feature, progress=None, check=None, root=None):
    state = status(feature, verify=True, check=check, root=root)
    if progress:
        progress(state['downloaded_bytes'], state['bytes'], '中文模型校验通过' if state['ready']
                 else '中文模型校验未完成')
    if not state['ready']:
        raise ValueError('中文模型缺失或文件校验失败，请点击“校验 / 修复”恢复。')
    try:
        root = Path(ROOT if root is None else root).resolve()
        spec = specification(feature, root)
        if _load_state(root, feature, spec).get('failed_files'):
            _save_state(root, feature, spec, ())
    except (OSError, ValueError, TypeError):
        pass
    return state


def install(feature, progress, check=None, source='auto'):
    """Install into root-level targets through the original pinned downloader.

    Each target is activated only after the returned cache file has the exact
    pinned size and SHA-256. A later failure leaves earlier valid targets and
    the remaining resumable cache in place; success is reported only after the
    complete feature passes a fresh size check.
    """
    from optional_components import download
    from download_sources import download_urls
    from pipeline import job_lock
    from task_store import checked_tree

    root = Path(ROOT).resolve()
    spec, items = _validated(feature, root)
    # Reject an unsupported source before creating the cache directory.
    for item in items:
        download_urls(item['url'], source)
    folder = root / 'cache/feature-downloads' / feature
    folder.mkdir(parents=True, exist_ok=True)
    checked_tree(folder, root)
    total = spec['bytes']
    with job_lock(folder):
        _raise_if_cancelled(check)
        progress(0, total, '正在校验中文模型')
        state = _load_state(root, feature, spec)
        failed = set(state.get('failed_files', ()))
        required = []
        done = 0
        for index, item in enumerate(items, 1):
            _raise_if_cancelled(check)
            target = item['target']
            try:
                valid = target.is_file() and not _is_link(target) and target.stat().st_size == item['bytes']
            except OSError:
                valid = False
            if valid:
                progress(done, total, f'正在检查中文模型 · {index}/{len(items)}')
                valid = _hash_file(
                    target, check,
                    lambda current, size, base=done, index=index: progress(
                        min(total, current), total, f'正在检查中文模型 · {index}/{len(items)}'),
                ) == item['sha256']
                _raise_if_cancelled(check)
            if valid:
                failed.discard(item['file'])
                done += item['bytes']
                progress(done, total, f'已校验中文模型 · {index}/{len(items)}')
            else:
                if target.exists():
                    failed.add(item['file'])
                required.append(item)
        _save_state(root, feature, spec, failed)
        needed = sum(item['bytes'] for item in required) + 64 * 1024**2
        if required and shutil.disk_usage(root).free < needed:
            raise OSError('安装中文模型所需的可用空间不足，已保留现有下载；请先清理缓存。')

        for item in required:
            _raise_if_cancelled(check)
            start = done

            def update(current, size, message):
                try:
                    current = max(0, min(int(current), item['bytes']))
                except (TypeError, ValueError):
                    current = 0
                progress(min(total, start + current), total, message)

            item_for_download = {key: item[key] for key in ('name', 'bytes', 'sha256', 'url')}
            downloaded = download(item_for_download, folder, update, check, source=source)
            # Cancellation is checked at the activation boundary. A stopped
            # download remains in cache and is reused on the next attempt.
            _raise_if_cancelled(check)
            try:
                downloaded = Path(downloaded)
                resolved = downloaded.resolve(strict=True)
            except OSError as exc:
                raise ValueError('下载器未返回可读取的中文模型文件。') from exc
            if (not resolved.is_relative_to(folder.resolve()) or resolved.is_symlink()
                    or not resolved.is_file() or resolved.stat().st_size != item['bytes']
                    or _hash_file(
                        resolved, check,
                        lambda current, size: progress(min(total, start + current), total,
                                                       '正在确认中文模型文件')) != item['sha256']):
                if resolved.is_relative_to(folder.resolve()) and resolved.is_file() and not resolved.is_symlink():
                    resolved.unlink(missing_ok=True)
                raise ValueError('下载的中文模型 SHA-256 校验失败，未激活文件。')
            _raise_if_cancelled(check)
            target = item['target']
            target.parent.mkdir(parents=True, exist_ok=True)
            checked_tree(target.parent, root)
            if target.exists() and _is_link(target):
                raise ValueError('依赖安装目标不能是链接。')
            resolved.replace(target)
            failed.discard(item['file'])
            _save_state(root, feature, spec, failed)
            done += item['bytes']
            progress(done, total, '中文模型文件校验通过')

        # Once every target is verified and atomically activated, a late stop
        # request cannot turn a complete installation into a partial result.
        final = status(feature, verify=False, root=root)
        if not final['ready']:
            raise OSError('中文模型安装尚未完成；已校验文件会保留，可继续下载。')
        _save_state(root, feature, spec, ())
        progress(total, total, '中文模型已就绪，可离线使用')
    return {'ready': True, 'verified': True, 'feature': feature, 'files': len(items),
            'bytes': total, 'repaired': bool(required)}
