"""Bounded HTTP ranges written directly into one resumable, verified file."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
import urllib.request

from common import atomic_json, direct_urlopen, file_hash
from download_sources import download_urls, source_name


class RangeUnsupported(OSError):
    pass


class ChecksumMismatch(ValueError):
    pass


def sequential_partial(item, folder):
    """Reuse the contiguous prefix when a server cannot honor parallel ranges."""
    folder = Path(folder).resolve()
    stage = folder / (item['name'] + '.segments')
    if not (stage / 'data.part').is_file(): return
    from task_store import checked_tree
    checked_tree(stage, folder)
    saved = json.loads((stage / 'segments.json').read_text(encoding='utf-8'))
    if saved.get('bytes') != item['bytes'] or saved.get('sha256') != item['sha256']:
        raise ValueError('分段缓存清单不一致。')
    prefix = 0
    for size in saved['lengths']:
        prefix += size
        if size < saved['segment_bytes']: break
    data = stage / 'data.part'
    with data.open('r+b') as stream: stream.truncate(prefix)
    partial = folder / (item['name'] + '.partial')
    if partial.exists(): raise ValueError('存在其他下载缓存，请清理后重试。')
    data.replace(partial)
    shutil.rmtree(stage)


def download(item, folder, progress, check=None, source='auto', workers=4,
             opener=direct_urlopen, segment_bytes=32 * 1024**2):
    """Every segment must match its range; unverified bytes never become a target."""
    if not 1 <= workers <= 8 or segment_bytes < 1024:
        raise ValueError('下载连接数应为 1–8。')
    folder = Path(folder).resolve()
    name = item['name']
    if name in ('', '.', '..') or Path(name).name != name or any(c in name for c in ('/', '\\', ':')):
        raise ValueError('Invalid download filename')
    total = item['bytes']
    if not isinstance(total, int) or total < 1 or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
        raise ValueError('下载清单无效。')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.is_symlink() or (target.exists() and getattr(target.lstat(), 'st_file_attributes', 0) & 1024):
        raise ValueError('下载目标不能是链接。')
    if target.is_file() and target.stat().st_size == total and file_hash(target) == item['sha256']:
        return target
    stage = folder / (name + '.segments')
    if stage.is_symlink() or (stage.exists() and getattr(stage.lstat(), 'st_file_attributes', 0) & 1024):
        raise ValueError('下载缓存不能是链接目录。')
    stage.mkdir(exist_ok=True)
    metadata = {'bytes': total, 'sha256': item['sha256'], 'segment_bytes': segment_bytes}
    receipt = stage / 'segments.json'
    saved = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {}
    if saved and any(saved.get(key) != value for key, value in metadata.items()):
        raise ValueError('下载缓存与当前清单不一致，请先清理下载缓存。')
    old_assembly = stage / 'assembled.partial'
    if old_assembly.exists():
        if old_assembly.is_symlink() or getattr(old_assembly.lstat(),'st_file_attributes',0)&1024:
            raise ValueError('下载缓存不能包含链接。')
        if not saved: raise ValueError('旧下载缓存缺少清单，无法迁移。')
        old_assembly.unlink()  # Legacy assembly duplicates the retained pieces.
    count = (total + segment_bytes - 1) // segment_bytes
    data = stage / 'data.part'
    if data.is_symlink() or (data.exists() and getattr(data.lstat(), 'st_file_attributes', 0) & 1024):
        raise ValueError('下载缓存不能包含链接。')
    lengths = saved.get('lengths', [0] * count) if data.exists() else [0] * count
    if (not isinstance(lengths, list) or len(lengths) != count or
            any(not isinstance(n, int) or not 0 <= n <= min(segment_bytes, total-i*segment_bytes) for i,n in enumerate(lengths))):
        raise ValueError('下载续传记录无效，请清理该文件的下载缓存。')
    legacy = [stage / f'{index:06d}.part' for index in range(count)]
    for index, path in enumerate(legacy):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('下载缓存不能包含链接。')
    if data.exists() and data.stat().st_size != total:
        raise ValueError('下载缓存大小异常，请清理该文件的下载缓存。')
    if not data.exists():
        with data.open('wb') as stream:
            stream.truncate(total)
    # Migrate older per-segment caches without throwing away downloaded bytes.
    with data.open('r+b', buffering=0) as output:
        for index, path in enumerate(legacy):
            if not path.is_file(): continue
            size = path.stat().st_size
            if size <= min(segment_bytes, total-index*segment_bytes):
                output.seek(index * segment_bytes)
                with path.open('rb') as reader:
                    while block := reader.read(1024**2): output.write(block)
                lengths[index] = size
            path.unlink()
    def save():
        atomic_json(receipt, {**metadata, 'version': 2, 'lengths': lengths})
    save()
    last_error = None
    for route in download_urls(item['url'], source):
        abort = threading.Event()
        def cancelled():
            if check and check():
                raise InterruptedError('已取消下载，分段缓存已保留。')
            if abort.is_set():
                raise RangeUnsupported('分段下载已停止。')
        try:
            cancelled()
            probe = urllib.request.Request(route, headers={'User-Agent':'VWA-Downloader', 'Range':'bytes=0-0', 'Accept-Encoding':'identity'})
            with opener(probe, timeout=12) as response:
                if response.status != 206 or response.headers.get('Content-Range') != f'bytes 0-0/{total}' or len(response.read(2)) != 1:
                    raise RangeUnsupported('服务器不支持分段下载，将使用单连接。')
                validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
            initial = sum(lengths)
            lock = threading.Lock()
            started, notified, persisted = time.monotonic(), [0.0], [0.0]
            def notify(force=False):
                now = time.monotonic()
                if force or now - notified[0] >= .15:
                    done = sum(lengths)
                    rate = max(0, done - initial) / max(.01, now - started) / 1024**2
                    progress(done, total, f'下载 · {source_name(route)} · {workers} 连接 · {rate:.1f} MB/s')
                    notified[0] = now
                if force or now - persisted[0] >= 1:
                    save()
                    persisted[0] = now
            def fetch(index):
                start, end = index*segment_bytes, min(total, (index+1)*segment_bytes)-1
                offset = start + lengths[index]
                if offset > end:
                    return
                for attempt in range(3):
                    cancelled()
                    offset = start + lengths[index]
                    headers = {'User-Agent':'VWA-Downloader','Accept-Encoding':'identity', 'Range':f'bytes={offset}-{end}'}
                    if validator:
                        headers['If-Range'] = validator
                    try:
                        with opener(urllib.request.Request(route, headers=headers), timeout=12) as response:
                            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {offset}-{end}/{total}':
                                raise RangeUnsupported('服务器返回的分段范围不正确。')
                            with data.open('r+b', buffering=0) as output:
                                output.seek(offset)
                                while block := response.read(256*1024):
                                    cancelled()
                                    if lengths[index] + len(block) > end-start+1:
                                        raise RangeUnsupported('下载分段超出范围。')
                                    output.write(block)
                                    with lock:
                                        lengths[index] += len(block)
                                        notify()
                        if lengths[index] != end-start+1:
                            raise OSError('下载分段未完成。')
                        return
                    except RangeUnsupported:
                        raise
                    except OSError:
                        if attempt == 2:
                            raise
            notify(True)
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(fetch, index) for index in range(count)]
                    try:
                        for future in as_completed(futures):
                            future.result()
                    except BaseException:
                        abort.set()
                        for future in futures: future.cancel()
                        raise
            finally:
                save()
            cancelled()
            digest = hashlib.sha256()
            with data.open('rb') as reader:
                while block := reader.read(1024**2):
                    cancelled()
                    digest.update(block)
            if data.stat().st_size != total or digest.hexdigest() != item['sha256']:
                lengths[:] = [0] * count
                save()
                raise ChecksumMismatch('分段下载 SHA-256 校验失败，正在尝试其他下载源。')
            from task_store import checked_tree
            checked_tree(stage, folder)
            data.replace(target)
            shutil.rmtree(stage)
            progress(total, total, '下载完成，SHA-256 校验通过')
            return target
        except InterruptedError:
            raise
        except (OSError, ChecksumMismatch) as exc:
            last_error = exc
    if isinstance(last_error, ChecksumMismatch):
        raise ChecksumMismatch('所有下载源均未提供符合 SHA-256 的文件，请稍后重试。') from last_error
    raise RangeUnsupported('无法使用分段下载，将尝试单连接续传。') from last_error
