"""Standalone 2-hop Full-Package Downloader & Auto-Migrator for VWA (100% stdlib, zero Qt/Tkinter)."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = 'Transilent/Better-Vocaloid-Workflow-Assistant'
RELEASES_BASE = f'https://github.com/{REPO}/releases'
HANDOFF_REL = Path('cache/updates/full-migrator-handoff.json')
RECEIPT_REL = Path('cache/updates/migration-receipt.json')
BLOCK_SIZE = 2 * 1024 * 1024
CHUNK_SIZE = 16 * 1024 * 1024
PARALLEL_MIN_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r'^[a-f0-9]{64}$')
USER_AGENT = 'VWA-FullMigrator/3.0'

MIRROR_PREFIXES: dict[str, tuple[str, ...]] = {
    'auto': ('', 'https://api.gitproxy.dev/', 'https://gh-proxy.com/', 'https://ghfast.top/'),
    'direct': ('',),
    'mirror': ('https://api.gitproxy.dev/', 'https://gh-proxy.com/', 'https://ghfast.top/', ''),
    'gitproxy': ('https://api.gitproxy.dev/', 'https://gh-proxy.com/', 'https://ghfast.top/', ''),
    'gh-proxy': ('https://gh-proxy.com/', 'https://api.gitproxy.dev/', 'https://ghfast.top/', ''),
    'ghfast': ('https://ghfast.top/', 'https://api.gitproxy.dev/', 'https://gh-proxy.com/', ''),
}
SOURCE_ALIASES = {
    'automatic': 'auto', 'official': 'direct', 'github-direct': 'direct',
    'accelerated': 'mirror', 'git-proxy': 'gitproxy', 'ghproxy': 'gh-proxy',
    'gh_proxy': 'gh-proxy', 'gh_fast': 'ghfast',
}
QWEN_WEIGHT_FILES = (
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_1.7b_q4_k_m.gguf',
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/audio_encoder_int8.onnx',
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/token_embeddings.npy',
)
MIGRATED_DIRS = ('jobs', 'user-presets', 'publish-packages', 'cache/accounts', 'dependencies/optional')
_INTERNAL_CONFIG_KEYS = frozenset({
    'python', 'vocal2midi', 'ffmpeg', 'mdx_model', 'mdx_metadata',
    'chorus_model', 'chorus_metadata', 'publish_browser_executable',
})


def _native_path(path: Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return ('\\\\?\\UNC\\' + value[2:]) if value.startswith('\\\\') else ('\\\\?\\' + value)


def normalize_source(source: str | None) -> str:
    raw = SOURCE_ALIASES.get(str(source or 'auto').strip().lower(), str(source or 'auto').strip().lower())
    return raw if raw in MIRROR_PREFIXES else 'auto'


def load_network_preferences(source_root: Path) -> dict:
    """Read proxy, download_source, and download_workers from existing config.json."""
    prefs = {'source': 'auto', 'proxy_mode': 'system', 'proxy_url': '', 'workers': 4}
    cfg_path = Path(source_root).resolve() / 'config.json'
    if not cfg_path.is_file():
        return prefs
    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return prefs
    if not isinstance(cfg, dict):
        return prefs
    prefs['source'] = normalize_source(cfg.get('download_source'))
    mode = str(cfg.get('network_proxy_mode') or 'system').strip().lower()
    if mode in ('direct', 'system', 'custom'):
        prefs['proxy_mode'] = mode
    proxy_url = str(cfg.get('network_proxy_url') or '').strip()
    if proxy_url and urllib.parse.urlsplit(proxy_url).scheme in ('http', 'https'):
        prefs['proxy_url'] = proxy_url
    try:
        prefs['workers'] = max(1, min(8, int(cfg.get('download_workers', 4))))
    except (TypeError, ValueError):
        pass
    return prefs


def build_opener(proxy_mode: str = 'system', proxy_url: str = '') -> urllib.request.OpenerDirector:
    mode = (proxy_mode or 'system').strip().lower()
    if mode == 'direct':
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if mode == 'custom' and proxy_url:
        return urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url}))
    return urllib.request.build_opener()


def _sha256_file(path: Path, check_cancel: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with open(_native_path(path), 'rb') as stream:
        while True:
            if check_cancel and check_cancel():
                raise InterruptedError('已取消校验。')
            chunk = stream.read(BLOCK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _release_asset_url(version: str, filename: str) -> str:
    return f'{RELEASES_BASE}/download/v{version.removeprefix("v")}/{urllib.parse.quote(filename, safe="")}'


def candidate_urls(url: str, source: str = 'auto') -> list[str]:
    prefixes = MIRROR_PREFIXES.get(normalize_source(source), MIRROR_PREFIXES['auto'])
    urls: list[str] = []
    for prefix in prefixes:
        full = (prefix + url) if prefix else url
        if full not in urls:
            urls.append(full)
    return urls


def _route_label(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if 'gitproxy' in host:
        return 'GitProxy'
    if 'gh-proxy' in host:
        return 'GH-Proxy'
    if 'ghfast' in host:
        return 'ghfast.top'
    return 'GitHub 直连'


def _probe_single(url: str, opener: urllib.request.OpenerDirector, timeout: float) -> tuple[bool, bool]:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Range': 'bytes=0-0'})
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, 'status', 200)
            supports_range = (status == 206) or ('bytes' in str(resp.headers.get('Accept-Ranges') or '').lower())
            return status in (200, 206), supports_range
    except (OSError, urllib.error.URLError):
        return False, False


def _probe_timed(url: str, opener: urllib.request.OpenerDirector, timeout: float) -> tuple[bool, float, bool]:
    t0 = time.monotonic()
    ok, ranges = _probe_single(url, opener, timeout)
    return ok, time.monotonic() - t0, ranges


def probe_urls_fastest_first(
    urls: list[str], opener: urllib.request.OpenerDirector, *, timeout: float = 5.0,
) -> list[tuple[str, bool]]:
    """Race candidate URLs with a Range=0-0 probe; return [(url, supports_range)] fastest first."""
    if len(urls) <= 1:
        if not urls:
            return []
        ok, ranges = _probe_single(urls[0], opener, timeout)
        return [(urls[0], ranges if ok else False)]
    ranked: list[tuple[float, str, bool]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
        futs = {pool.submit(_probe_timed, u, opener, timeout): u for u in urls}
        done, pending = wait(futs, timeout=timeout, return_when=FIRST_COMPLETED)
        if done and pending:
            more, _ = wait(pending, timeout=0.6)
            done |= more
        for fut in done:
            try:
                ok, elapsed, ranges = fut.result()
                if ok:
                    ranked.append((elapsed, futs[fut], ranges))
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                continue
    ranked.sort(key=lambda item: item[0])
    ordered = [(u, r) for _, u, r in ranked]
    seen = {u for u, _ in ordered}
    ordered.extend((u, False) for u in urls if u not in seen)
    return ordered


def detect_install_variant(source_root: Path) -> str:
    root = Path(source_root).resolve()
    feat = root / 'components/runtime-features.json'
    if feat.is_file():
        try:
            data = json.loads(feat.read_text(encoding='utf-8-sig'))
            variant = str(data.get('variant') or data.get('portable_variant') or '').strip().lower()
            if variant in ('jpn', 'full'):
                return variant
        except (OSError, ValueError):
            pass
    present = sum(1 for rel in QWEN_WEIGHT_FILES if (root / rel).is_file())
    return 'jpn' if present == 0 and (root / 'dependencies/manifest.json').is_file() else 'full'


def installed_version(source_root: Path) -> str:
    path = Path(source_root).resolve() / 'version.json'
    if not path.is_file():
        return 'unknown'
    try:
        return str(json.loads(path.read_text(encoding='utf-8-sig')).get('version') or 'unknown')
    except (OSError, ValueError):
        return 'unknown'


def default_target_directory(source_root: Path, target_version: str) -> Path:
    root = Path(source_root).resolve()
    ver = target_version.removeprefix('v')
    cand = root.parent / f'VWA-{ver}'
    return (root.parent / f'VWA-{ver}-new') if cand == root else cand


def write_handoff(source_root: Path, target_version: str, source: str = 'auto') -> Path:
    path = Path(source_root).resolve() / HANDOFF_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': 1, 'target_version': target_version.removeprefix('v'),
        'source': normalize_source(source), 'requested_at': datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(_native_path(tmp), _native_path(path))
    return path


def clear_handoff(source_root: Path) -> None:
    (Path(source_root).resolve() / HANDOFF_REL).unlink(missing_ok=True)


def spawn_migrator_process(source_root: Path, target_version: str, source: str = 'auto') -> None:
    """Spawn full_package_migrator_ui.py detached so the main Qt app can close cleanly."""
    root = Path(source_root).resolve()
    script = root / 'full_package_migrator_ui.py'
    if not script.is_file():
        script = root / 'src/full_package_migrator_ui.py'
    env = dict(os.environ)
    env.pop('VWA_LAUNCHER_PID', None)
    env.pop('BVWA_LAUNCHER_PID', None)
    env['PYTHONUTF8'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    subprocess.Popen(
        [sys.executable, '-B', str(script), '--source-root', str(root),
         '--target-version', target_version.removeprefix('v'), '--source', normalize_source(source)],
        cwd=str(root), env=env,
    )


def _read_json_url(
    url: str, source: str = 'auto', timeout: int = 25, opener: urllib.request.OpenerDirector | None = None,
) -> dict:
    op = opener or build_opener('system')
    last_err: Exception | None = None
    for target in candidate_urls(url, source):
        try:
            req = urllib.request.Request(target, headers={'User-Agent': USER_AGENT})
            with op.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8-sig'))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_err = exc
    raise OSError(f'无法读取发布清单 {url}：{last_err}')


def fetch_portable_plan(
    target_version: str, variant: str = 'full', *, source: str = 'auto',
    reader: Callable[[str, str], dict] | None = None, opener: urllib.request.OpenerDirector | None = None,
) -> dict:
    if variant not in ('full', 'jpn'):
        raise ValueError(f'无效的发行版本类型：{variant}')
    fetch_json = reader or (lambda u, s: _read_json_url(u, s, opener=opener))
    meta_name = f'portable-metadata-{variant}.json'
    try:
        meta = fetch_json(_release_asset_url(target_version, meta_name), source)
    except OSError:
        inv = fetch_json(_release_asset_url(target_version, 'release-assets.json'), source)
        variants = inv.get('portable_variants') or inv.get('variants') or {}
        if isinstance(variants, dict) and isinstance(variants.get(variant), dict):
            meta = variants[variant]
        elif variant == 'full' and isinstance(inv.get('archive'), dict):
            meta = inv
        else:
            raise

    archive = meta.get('archive') or {}
    name, total_bytes = str(archive.get('name') or ''), archive.get('bytes')
    sha256 = str(archive.get('sha256') or '').lower()
    if not name.endswith('.zip') or type(total_bytes) is not int or total_bytes <= 0 or not SHA256_RE.match(sha256):
        raise ValueError(f'发布包清单 {meta_name} 缺少有效的归档校验信息。')

    parts: list[dict] = []
    for item in (meta.get('parts') or []):
        p_name, p_bytes, p_sha = str(item.get('name') or ''), item.get('bytes'), str(item.get('sha256') or '').lower()
        if not p_name or '/' in p_name or '\\' in p_name or type(p_bytes) is not int or p_bytes <= 0 or not SHA256_RE.match(p_sha):
            raise ValueError(f'分卷校验信息无效：{p_name!r}')
        parts.append({'name': p_name, 'bytes': p_bytes, 'sha256': p_sha,
                      'url': _release_asset_url(target_version, p_name)})
    if parts:
        if sum(p['bytes'] for p in parts) != total_bytes:
            raise ValueError('分卷总字节数与完整归档大小不一致。')
    else:
        parts.append({'name': name, 'bytes': total_bytes, 'sha256': sha256,
                      'url': _release_asset_url(target_version, name)})
    return {'version': target_version.removeprefix('v'), 'variant': variant,
            'archive': {'name': name, 'bytes': total_bytes, 'sha256': sha256}, 'parts': parts}


def _download_chunked(
    urls: list[str], partial: Path, state_path: Path, expected_bytes: int, expected_sha: str,
    *, opener: urllib.request.OpenerDirector, workers: int, progress_cb: Callable[[int], None],
    check_cancel: Callable[[], bool] | None, timeout: int = 45, chunk_size: int = CHUNK_SIZE,
) -> None:
    """Multi-threaded HTTP Range download with persistent chunk bitmap resume & mirror failover."""
    chunks = [(i, s, min(expected_bytes - 1, s + chunk_size - 1))
              for i, s in enumerate(range(0, expected_bytes, chunk_size))]
    completed: set[int] = set()
    if state_path.is_file() and partial.is_file() and partial.stat().st_size == expected_bytes:
        try:
            saved = json.loads(state_path.read_text(encoding='utf-8'))
            if saved.get('bytes') == expected_bytes and saved.get('sha256') == expected_sha:
                completed = {int(i) for i in saved.get('completed', []) if 0 <= int(i) < len(chunks)}
        except (OSError, ValueError):
            completed = set()
    if not partial.is_file() or partial.stat().st_size != expected_bytes:
        with open(_native_path(partial), 'wb') as f:
            f.truncate(expected_bytes)
        completed = set()

    lock = threading.Lock()
    done_bytes = sum((e - s + 1) for i, s, e in chunks if i in completed)
    progress_cb(done_bytes)
    native_part = _native_path(partial)

    def _save_state() -> None:
        tmp = state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'bytes': expected_bytes, 'sha256': expected_sha,
                                   'completed': sorted(completed)}), encoding='utf-8')
        os.replace(_native_path(tmp), _native_path(state_path))

    def _fetch_chunk(c_idx: int, start: int, end: int) -> None:
        nonlocal done_bytes
        want = end - start + 1
        last_err: Exception | None = None
        for attempt in range(4):
            url = urls[attempt % len(urls)]
            if check_cancel and check_cancel():
                raise InterruptedError('下载已取消。')
            try:
                req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Range': f'bytes={start}-{end}'})
                with opener.open(req, timeout=timeout) as resp:
                    if getattr(resp, 'status', 200) != 206:
                        raise OSError(f'源站不支持分片续传（HTTP {getattr(resp, "status", 200)}）')
                    buf = bytearray()
                    while len(buf) < want:
                        if check_cancel and check_cancel():
                            raise InterruptedError('下载已取消。')
                        piece = resp.read(min(256 * 1024, want - len(buf)))
                        if not piece:
                            break
                        buf.extend(piece)
                        with lock:
                            done_bytes += len(piece)
                            progress_cb(min(expected_bytes, done_bytes))
                    if len(buf) != want:
                        with lock:
                            done_bytes = max(0, done_bytes - len(buf))
                        raise OSError(f'分片 {c_idx} 长度不符（{len(buf)} != {want}）')
                with lock:
                    with open(native_part, 'r+b') as out:
                        out.seek(start)
                        out.write(buf)
                    completed.add(c_idx)
                    _save_state()
                return
            except InterruptedError:
                raise
            except (OSError, urllib.error.URLError) as exc:
                last_err = exc
                time.sleep(min(2.5, 0.3 * (2 ** attempt)))
        raise OSError(f'分片 {c_idx} 下载失败：{last_err}')

    pending = [c for c in chunks if c[0] not in completed]
    if not pending:
        return
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(pending)))) as pool:
        for fut in as_completed([pool.submit(_fetch_chunk, i, s, e) for i, s, e in pending]):
            fut.result()


def _download_stream(
    urls: list[str], partial: Path, expected_bytes: int, *, opener: urllib.request.OpenerDirector,
    progress_cb: Callable[[int], None], check_cancel: Callable[[], bool] | None, timeout: int = 45,
) -> None:
    last_err: Exception | None = None
    for url in urls:
        if check_cancel and check_cancel():
            raise InterruptedError('下载已取消。')
        existing = partial.stat().st_size if partial.is_file() else 0
        if existing > expected_bytes:
            partial.unlink(missing_ok=True)
            existing = 0
        headers = {'User-Agent': USER_AGENT}
        if existing > 0:
            headers['Range'] = f'bytes={existing}-'
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                if existing > 0 and getattr(resp, 'status', 200) != 206:
                    existing = 0
                written = existing
                progress_cb(written)
                with open(_native_path(partial), 'ab' if existing > 0 else 'wb') as out:
                    while True:
                        if check_cancel and check_cancel():
                            raise InterruptedError('下载已取消。')
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        written += len(chunk)
                        progress_cb(written)
            if partial.stat().st_size != expected_bytes:
                raise OSError(f'大小不符（{partial.stat().st_size} != {expected_bytes}）')
            return
        except InterruptedError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            last_err = exc
    raise OSError(f'所有下载线路均失败：{last_err}')


def _download_one_file(
    item: dict, dest: Path, *, source: str, opener: urllib.request.OpenerDirector, workers: int,
    progress_cb: Callable[[int], None], route_cb: Callable[[str], None] | None = None,
    check_cancel: Callable[[], bool] | None = None, timeout: int = 45,
) -> Path:
    expected_bytes, expected_sha = int(item['bytes']), str(item['sha256']).lower()
    if dest.is_file() and dest.stat().st_size == expected_bytes:
        if _sha256_file(dest, check_cancel) == expected_sha:
            progress_cb(expected_bytes)
            return dest
        dest.unlink(missing_ok=True)

    partial = dest.with_name(dest.name + '.part')
    state_path = dest.with_name(dest.name + '.part.state.json')
    dest.parent.mkdir(parents=True, exist_ok=True)

    probed = probe_urls_fastest_first(candidate_urls(item['url'], source), opener)
    ordered_urls = [u for u, _ in probed]
    supports_range = bool(probed and probed[0][1])
    if route_cb and ordered_urls:
        route_cb(_route_label(ordered_urls[0]))

    used_chunked = False
    if supports_range and workers > 1 and expected_bytes >= PARALLEL_MIN_BYTES:
        try:
            _download_chunked(
                ordered_urls, partial, state_path, expected_bytes, expected_sha,
                opener=opener, workers=workers, progress_cb=progress_cb,
                check_cancel=check_cancel, timeout=timeout,
            )
            used_chunked = True
        except InterruptedError:
            raise
        except OSError:
            state_path.unlink(missing_ok=True)

    if not used_chunked:
        _download_stream(ordered_urls, partial, expected_bytes, opener=opener,
                         progress_cb=progress_cb, check_cancel=check_cancel, timeout=timeout)

    if _sha256_file(partial, check_cancel) != expected_sha:
        partial.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        raise ValueError(f'{item["name"]} SHA-256 校验失败。')
    state_path.unlink(missing_ok=True)
    os.replace(_native_path(partial), _native_path(dest))
    return dest


def download_and_merge_package(
    plan: dict, cache_dir: Path, *, source: str = 'auto', opener: urllib.request.OpenerDirector | None = None,
    workers: int = 4, progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    route_cb: Callable[[str], None] | None = None, check_cancel: Callable[[], bool] | None = None,
    downloader: Callable[[dict, Path, Callable[[int], None]], Path] | None = None,
) -> Path:
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_meta, parts = plan['archive'], plan['parts']
    total_bytes = int(archive_meta['bytes'])

    free_bytes = shutil.disk_usage(cache_dir).free
    need_bytes = int(total_bytes * 2.2) + 256 * 1024 * 1024
    if free_bytes < need_bytes:
        raise OSError(
            f'磁盘剩余空间不足（当前可用 {free_bytes / (1024**3):.2f} GiB，建议至少 {need_bytes / (1024**3):.2f} GiB）。'
        )

    op = opener or build_opener('system')
    part_paths: list[Path] = []
    completed_before = 0
    for idx, part in enumerate(parts, 1):
        dest = cache_dir / part['name']
        def _on_part(done: int, base: int = completed_before, name: str = part['name']) -> None:
            progress(min(total_bytes, base + done), total_bytes, f'正在下载 ({idx}/{len(parts)}) {name}')

        if downloader is not None:
            got = downloader(part, dest, _on_part)
            if got.stat().st_size != part['bytes'] or _sha256_file(got, check_cancel) != part['sha256']:
                raise ValueError(f'{part["name"]} 校验失败。')
            part_paths.append(got)
        else:
            part_paths.append(_download_one_file(
                part, dest, source=source, opener=op, workers=workers,
                progress_cb=_on_part, route_cb=route_cb, check_cancel=check_cancel,
            ))
        completed_before += int(part['bytes'])

    if len(part_paths) == 1 and part_paths[0].name == archive_meta['name']:
        return part_paths[0]

    merged = cache_dir / archive_meta['name']
    if merged.is_file() and merged.stat().st_size == total_bytes:
        if _sha256_file(merged, check_cancel) == archive_meta['sha256']:
            progress(total_bytes, total_bytes, '已复用校验通过的完整安装包')
            return merged
        merged.unlink(missing_ok=True)

    tmp_merged = merged.with_name(merged.name + '.merging')
    progress(0, total_bytes, f'正在合并 {len(part_paths)} 个分卷并核验 SHA-256…')
    digest, written = hashlib.sha256(), 0
    try:
        with open(_native_path(tmp_merged), 'wb') as out:
            for part_path in part_paths:
                with open(_native_path(part_path), 'rb') as inp:
                    while True:
                        if check_cancel and check_cancel():
                            raise InterruptedError('合并已取消。')
                        chunk = inp.read(BLOCK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        progress(written, total_bytes, '正在合并分卷并核验完整包 SHA-256…')
        if written != total_bytes or digest.hexdigest() != archive_meta['sha256']:
            raise ValueError('合并后的完整安装包 SHA-256 校验失败。')
        os.replace(_native_path(tmp_merged), _native_path(merged))
        return merged
    finally:
        tmp_merged.unlink(missing_ok=True)


def _safe_zip_member(name: str) -> PurePosixPath:
    if '\\' in name or name.startswith('/') or ':' in name:
        raise ValueError(f'安装包包含非法路径：{name}')
    rel = PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError(f'安装包包含越界路径：{name}')
    return rel


def extract_verified_archive(
    archive_path: Path, target_dir: Path, *, expected_version: str | None = None,
    progress: Callable[[int, int, str], None] = lambda d, t, m: None, check_cancel: Callable[[], bool] | None = None,
) -> Path:
    target_dir = Path(target_dir).resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(f'目标安装目录非空：{target_dir}，请选择新的空目录以避免覆盖旧数据。')

    staging = target_dir.parent / f'.{target_dir.name}.staging-{os.getpid()}'
    shutil.rmtree(_native_path(staging), ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(_native_path(archive_path), 'r') as zf:
            infos = zf.infolist()
            total = max(1, len(infos))
            top_roots = {rel.parts[0] for info in infos if (rel := _safe_zip_member(info.filename)).parts}
            strip_prefix = next(iter(top_roots)) if len(top_roots) == 1 and next(iter(top_roots)) in ('VWA', 'BVWA') else None
            for idx, info in enumerate(infos, 1):
                if check_cancel and check_cancel():
                    raise InterruptedError('解压已取消。')
                rel = _safe_zip_member(info.filename)
                parts = rel.parts[1:] if (strip_prefix and rel.parts and rel.parts[0] == strip_prefix) else rel.parts
                if not parts:
                    continue
                dest = staging.joinpath(*parts)
                if not dest.resolve().is_relative_to(staging.resolve()):
                    raise ValueError(f'安装包路径越界：{info.filename}')
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, 'r') as src_f, open(_native_path(dest), 'wb') as dst_f:
                        shutil.copyfileobj(src_f, dst_f, length=1024 * 1024)
                if idx % 25 == 0 or idx == total:
                    progress(idx, total, f'正在解压安装包 ({idx}/{total})…')
        ver_file = staging / 'version.json'
        if not ver_file.is_file():
            raise ValueError('解压后的安装包缺少 version.json。')
        if expected_version:
            got_ver = str(json.loads(ver_file.read_text(encoding='utf-8-sig')).get('version') or '')
            if got_ver != expected_version.removeprefix('v'):
                raise ValueError(f'解压后的安装包版本不匹配（期望 {expected_version}，实际 {got_ver}）。')
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(_native_path(staging), _native_path(target_dir))
        return target_dir
    except Exception:
        shutil.rmtree(_native_path(staging), ignore_errors=True)
        raise


def _rewrite_config_paths(obj, old_root: Path, new_root: Path):
    if isinstance(obj, dict):
        return {k: _rewrite_config_paths(v, old_root, new_root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_config_paths(v, old_root, new_root) for v in obj]
    if isinstance(obj, str) and obj and not obj.startswith('@/'):
        try:
            p = Path(obj)
            if p.is_absolute() and p.resolve().is_relative_to(old_root):
                return str((new_root / p.resolve().relative_to(old_root)).resolve())
        except OSError:
            pass
    return obj


def _copy_tree_merge(src: Path, dst: Path, check_cancel: Callable[[], bool] | None = None) -> int:
    copied = 0
    for root_dir, _dirs, files in os.walk(_native_path(src)):
        target_sub = dst / Path(root_dir).relative_to(Path(_native_path(src)))
        target_sub.mkdir(parents=True, exist_ok=True)
        for fname in files:
            if check_cancel and check_cancel():
                raise InterruptedError('数据迁移已取消。')
            d_file = target_sub / fname
            if not d_file.exists():
                shutil.copy2(_native_path(Path(root_dir) / fname), _native_path(d_file))
                copied += 1
    return copied


def migrate_user_data(
    source_root: Path, new_root: Path, *,
    progress: Callable[[int, int, str], None] = lambda d, t, m: None, check_cancel: Callable[[], bool] | None = None,
) -> dict:
    old_root, dst_root = Path(source_root).resolve(), Path(new_root).resolve()
    migrated: dict[str, object] = {'directories': {}, 'config_migrated': False, 'qwen_weights_migrated': 0}
    total_steps, step = len(MIGRATED_DIRS) + 2, 0

    old_cfg_path, new_cfg_path, new_example = old_root / 'config.json', dst_root / 'config.json', dst_root / 'config.example.json'
    if old_cfg_path.is_file():
        try:
            old_cfg = json.loads(old_cfg_path.read_text(encoding='utf-8-sig'))
            base_cfg = (json.loads(new_example.read_text(encoding='utf-8-sig')) if new_example.is_file()
                        else (json.loads(new_cfg_path.read_text(encoding='utf-8-sig')) if new_cfg_path.is_file() else {}))
            if isinstance(old_cfg, dict) and isinstance(base_cfg, dict):
                merged = dict(base_cfg)
                for key, val in old_cfg.items():
                    if key not in _INTERNAL_CONFIG_KEYS:
                        merged[key] = _rewrite_config_paths(val, old_root, dst_root)
                new_cfg_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                migrated['config_migrated'] = True
        except (OSError, ValueError):
            pass
    step += 1
    progress(step, total_steps, '已迁移个人设置与配置')

    for rel in MIGRATED_DIRS:
        if (old_root / rel).is_dir():
            migrated['directories'][rel] = _copy_tree_merge(old_root / rel, dst_root / rel, check_cancel)
        step += 1
        progress(step, total_steps, f'已迁移 {rel}')

    qwen_copied = 0
    for rel in QWEN_WEIGHT_FILES:
        src_w, dst_w = old_root / rel, dst_root / rel
        if src_w.is_file() and not dst_w.is_file():
            dst_w.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_native_path(src_w), _native_path(dst_w))
            qwen_copied += 1
    migrated['qwen_weights_migrated'] = qwen_copied
    progress(total_steps, total_steps, '用户数据迁移完成')

    receipt = {
        'schema_version': 1, 'migrated_at': datetime.now(timezone.utc).isoformat(),
        'source_root': str(old_root), 'source_version': installed_version(old_root),
        'target_root': str(dst_root), 'target_version': installed_version(dst_root), 'summary': migrated,
    }
    receipt_path = dst_root / RECEIPT_REL
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def launch_migrated_app(new_root: Path) -> bool:
    root = Path(new_root).resolve()
    env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1')
    env.pop('VWA_LAUNCHER_PID', None)
    env.pop('BVWA_LAUNCHER_PID', None)
    if (root / 'VWA.exe').is_file():
        subprocess.Popen([str(root / 'VWA.exe')], cwd=str(root), env=env)
        return True
    if (root / 'launch.py').is_file():
        subprocess.Popen([sys.executable, '-B', str(root / 'launch.py'), '--desktop'], cwd=str(root), env=env)
        return True
    return False


def run_migration(
    source_root: Path, target_version: str, target_dir: Path, *, variant: str = 'auto', source: str = 'auto',
    auto_launch: bool = True, progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    route_cb: Callable[[str], None] | None = None, check_cancel: Callable[[], bool] | None = None,
    reader: Callable[[str, str], dict] | None = None,
    downloader: Callable[[dict, Path, Callable[[int], None]], Path] | None = None,
) -> dict:
    old_root = Path(source_root).resolve()
    prefs = load_network_preferences(old_root)
    resolved_source = normalize_source(source if source else prefs['source'])
    opener = build_opener(prefs['proxy_mode'], prefs['proxy_url'])
    resolved_variant = detect_install_variant(old_root) if variant == 'auto' else variant
    progress(0, 100, f'正在读取 VWA {target_version} ({resolved_variant.upper()}) 发布清单…')
    plan = fetch_portable_plan(target_version, resolved_variant, source=resolved_source, reader=reader, opener=opener)
    cache_dir = old_root / 'cache/updates' / f'full-{plan["version"]}-{resolved_variant}'
    archive = download_and_merge_package(
        plan, cache_dir, source=resolved_source, opener=opener, workers=prefs['workers'],
        progress=progress, route_cb=route_cb, check_cancel=check_cancel, downloader=downloader,
    )
    new_root = extract_verified_archive(
        archive, target_dir, expected_version=plan['version'], progress=progress, check_cancel=check_cancel,
    )
    receipt = migrate_user_data(old_root, new_root, progress=progress, check_cancel=check_cancel)
    clear_handoff(old_root)
    receipt['launched'] = launch_migrated_app(new_root) if auto_launch else False
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='VWA Full-Package Downloader & Data Migrator (CLI)')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--target-version', required=True)
    parser.add_argument('--target-dir', type=Path, default=None)
    parser.add_argument('--variant', choices=('auto', 'full', 'jpn'), default='auto')
    parser.add_argument('--source', default='auto')
    parser.add_argument('--no-launch', action='store_true')
    args = parser.parse_args(argv)
    target_dir = args.target_dir or default_target_directory(args.source_root, args.target_version)
    receipt = run_migration(
        args.source_root, args.target_version, target_dir, variant=args.variant, source=args.source,
        auto_launch=not args.no_launch,
        progress=lambda d, t, m: print(f'[{int(d * 100 / max(1, t)):3d}%] {m}', flush=True),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
