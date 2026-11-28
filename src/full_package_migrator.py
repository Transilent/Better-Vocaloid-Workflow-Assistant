"""Standalone 2-hop Full-Package Downloader & Automatic User-Data Migrator for VWA.

Designed to run cleanly from ANY historical VWA installation (0.3.x / 0.4.x / 1.x / 2.x / 3.x)
using Python's standard library + Tkinter GUI (zero Qt version coupling).

Workflow:
1. Detects the current installation's variant (Full vs JPN) and user data footprint.
2. Downloads the target release's full or JPN portable archive (supporting 1900 MiB split parts
   with HTTP Range resume and Direct / System Proxy / GitProxy mirrors).
3. Verifies exact size and SHA-256 of every part and the merged ZIP archive.
4. Safely extracts into a clean staging directory with strict Zip Slip protection.
5. Non-destructively migrates user state (config.json, jobs/, user-presets/, cache/accounts/,
   publish-packages/, dependencies/optional/, and separately installed Qwen weights).
6. Launches the new VWA.exe and keeps the old folder untouched as an instant rollback backup.
"""
from __future__ import annotations

import argparse
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
SHA256_RE = re.compile(r'^[a-f0-9]{64}$')

MIRROR_PREFIXES: dict[str, tuple[str, ...]] = {
    'auto': (
        '',
        'https://ghfast.top/',
        'https://gh-proxy.com/',
        'https://mirror.ghproxy.com/',
    ),
    'direct': ('',),
    'mirror': (
        'https://ghfast.top/',
        'https://gh-proxy.com/',
        'https://mirror.ghproxy.com/',
        '',
    ),
}

QWEN_WEIGHT_FILES = (
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_1.7b_q4_k_m.gguf',
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/audio_encoder_int8.onnx',
    'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/token_embeddings.npy',
)

MIGRATED_DIRS = (
    'jobs',
    'user-presets',
    'publish-packages',
    'cache/accounts',
    'dependencies/optional',
)


def _native_path(path: Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    if value.startswith('\\\\'):
        return '\\\\?\\UNC\\' + value[2:]
    return '\\\\?\\' + value


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
    tag = 'v' + version.removeprefix('v')
    return f'{RELEASES_BASE}/download/{tag}/{urllib.parse.quote(filename, safe="")}'


def candidate_urls(url: str, source: str = 'auto') -> list[str]:
    prefixes = MIRROR_PREFIXES.get(source, MIRROR_PREFIXES['auto'])
    urls: list[str] = []
    for prefix in prefixes:
        full = (prefix + url) if prefix else url
        if full not in urls:
            urls.append(full)
    return urls


def detect_install_variant(source_root: Path) -> str:
    """Detect whether an existing installation is 'jpn' or 'full'."""
    root = Path(source_root).resolve()
    feat_path = root / 'components/runtime-features.json'
    if feat_path.is_file():
        try:
            data = json.loads(feat_path.read_text(encoding='utf-8-sig'))
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
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return str(data.get('version') or 'unknown')
    except (OSError, ValueError):
        return 'unknown'


def default_target_directory(source_root: Path, target_version: str) -> Path:
    """Choose a clean sibling directory so the old installation stays intact as backup."""
    root = Path(source_root).resolve()
    ver = target_version.removeprefix('v')
    candidate = root.parent / f'VWA-{ver}'
    if candidate == root:
        candidate = root.parent / f'VWA-{ver}-new'
    return candidate


def _read_json_url(url: str, source: str = 'auto', timeout: int = 30) -> dict:
    last_err: Exception | None = None
    for target in candidate_urls(url, source):
        try:
            req = urllib.request.Request(target, headers={'User-Agent': 'VWA-FullMigrator/3.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8-sig'))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_err = exc
    raise OSError(f'无法读取发布清单 {url}：{last_err}')


def fetch_portable_plan(
    target_version: str,
    variant: str = 'full',
    *,
    source: str = 'auto',
    reader: Callable[[str, str], dict] | None = None,
) -> dict:
    """Load portable-metadata-{variant}.json (or fallback release-assets.json) for target_version."""
    if variant not in ('full', 'jpn'):
        raise ValueError(f'无效的发行版本类型：{variant}')
    fetch_json = reader or _read_json_url
    meta_name = f'portable-metadata-{variant}.json'
    meta_url = _release_asset_url(target_version, meta_name)
    try:
        meta = fetch_json(meta_url, source)
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
    name = str(archive.get('name') or '')
    total_bytes = archive.get('bytes')
    sha256 = str(archive.get('sha256') or '').lower()
    if not name.endswith('.zip') or type(total_bytes) is not int or total_bytes <= 0 or not SHA256_RE.match(sha256):
        raise ValueError(f'发布包清单 {meta_name} 缺少有效的归档校验信息。')

    raw_parts = meta.get('parts') or []
    parts: list[dict] = []
    if raw_parts:
        for item in raw_parts:
            p_name = str(item.get('name') or '')
            p_bytes = item.get('bytes')
            p_sha = str(item.get('sha256') or '').lower()
            if not p_name or '/' in p_name or '\\' in p_name:
                raise ValueError(f'分卷文件名无效：{p_name!r}')
            if type(p_bytes) is not int or p_bytes <= 0 or not SHA256_RE.match(p_sha):
                raise ValueError(f'分卷校验信息无效：{p_name}')
            parts.append({
                'name': p_name,
                'bytes': p_bytes,
                'sha256': p_sha,
                'url': _release_asset_url(target_version, p_name),
            })
        if sum(p['bytes'] for p in parts) != total_bytes:
            raise ValueError('分卷总字节数与完整归档大小不一致。')
    else:
        parts.append({
            'name': name,
            'bytes': total_bytes,
            'sha256': sha256,
            'url': _release_asset_url(target_version, name),
        })
    return {
        'version': target_version.removeprefix('v'),
        'variant': variant,
        'archive': {'name': name, 'bytes': total_bytes, 'sha256': sha256},
        'parts': parts,
    }


def _download_one_file(
    item: dict,
    dest: Path,
    *,
    source: str,
    progress_cb: Callable[[int], None],
    check_cancel: Callable[[], bool] | None,
    timeout: int = 45,
) -> Path:
    """Download one file with Range resume and strict SHA-256 verification."""
    expected_bytes = int(item['bytes'])
    expected_sha = str(item['sha256']).lower()
    if dest.is_file() and dest.stat().st_size == expected_bytes:
        if _sha256_file(dest, check_cancel) == expected_sha:
            progress_cb(expected_bytes)
            return dest
        dest.unlink(missing_ok=True)

    partial = dest.with_name(dest.name + '.part')
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None

    for url in candidate_urls(item['url'], source):
        if check_cancel and check_cancel():
            raise InterruptedError('下载已取消。')
        existing = partial.stat().st_size if partial.is_file() else 0
        if existing > expected_bytes:
            partial.unlink(missing_ok=True)
            existing = 0
        headers = {'User-Agent': 'VWA-FullMigrator/3.0'}
        if existing > 0:
            headers['Range'] = f'bytes={existing}-'
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, 'status', 200)
                if existing > 0 and status != 206:
                    existing = 0
                mode = 'ab' if existing > 0 else 'wb'
                written = existing
                progress_cb(written)
                with open(_native_path(partial), mode) as out:
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
                raise OSError(f'{item["name"]} 大小不符（{partial.stat().st_size} != {expected_bytes}）')
            actual_sha = _sha256_file(partial, check_cancel)
            if actual_sha != expected_sha:
                partial.unlink(missing_ok=True)
                raise ValueError(f'{item["name"]} SHA-256 校验失败。')
            os.replace(_native_path(partial), _native_path(dest))
            return dest
        except InterruptedError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_err = exc
    raise OSError(f'无法下载 {item["name"]}：{last_err}')


def download_and_merge_package(
    plan: dict,
    cache_dir: Path,
    *,
    source: str = 'auto',
    progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    check_cancel: Callable[[], bool] | None = None,
    downloader: Callable[[dict, Path, Callable[[int], None]], Path] | None = None,
) -> Path:
    """Download all parts, verify SHA-256, merge if split, and return verified ZIP path."""
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_meta = plan['archive']
    total_bytes = int(archive_meta['bytes'])
    parts = plan['parts']

    # Preflight disk space (need archive + extracted tree headroom)
    free_bytes = shutil.disk_usage(cache_dir).free
    need_bytes = int(total_bytes * 2.2) + 256 * 1024 * 1024
    if free_bytes < need_bytes:
        raise OSError(
            f'磁盘剩余空间不足（当前可用 {free_bytes / (1024**3):.2f} GiB，建议至少 {need_bytes / (1024**3):.2f} GiB）。'
        )

    part_paths: list[Path] = []
    completed_before = 0
    for idx, part in enumerate(parts, 1):
        dest = cache_dir / part['name']

        def _on_part_progress(part_done: int, base: int = completed_before, name: str = part['name']) -> None:
            progress(min(total_bytes, base + part_done), total_bytes, f'正在下载 ({idx}/{len(parts)}) {name}')

        if downloader is not None:
            got = downloader(part, dest, _on_part_progress)
            if got.stat().st_size != part['bytes'] or _sha256_file(got, check_cancel) != part['sha256']:
                raise ValueError(f'{part["name"]} 校验失败。')
            part_paths.append(got)
        else:
            part_paths.append(
                _download_one_file(part, dest, source=source, progress_cb=_on_part_progress, check_cancel=check_cancel)
            )
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
    digest = hashlib.sha256()
    written = 0
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
    archive_path: Path,
    target_dir: Path,
    *,
    expected_version: str | None = None,
    progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    check_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Extract verified ZIP safely into staging dir and move cleanly into target_dir."""
    target_dir = Path(target_dir).resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(f'目标安装目录非空：{target_dir}，请选择新的空目录以避免覆盖旧数据。')

    staging = target_dir.parent / f'.{target_dir.name}.staging-{os.getpid()}'
    if staging.exists():
        shutil.rmtree(_native_path(staging), ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(_native_path(archive_path), 'r') as zf:
            infos = zf.infolist()
            total = max(1, len(infos))
            top_roots: set[str] = set()
            for info in infos:
                rel = _safe_zip_member(info.filename)
                if rel.parts:
                    top_roots.add(rel.parts[0])
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
                rel = p.resolve().relative_to(old_root)
                return str((new_root / rel).resolve())
        except OSError:
            pass
    return obj


def _copy_tree_merge(src: Path, dst: Path, check_cancel: Callable[[], bool] | None = None) -> int:
    copied = 0
    for root_dir, _dirs, files in os.walk(_native_path(src)):
        rel_dir = Path(root_dir).relative_to(Path(_native_path(src)))
        target_sub = dst / rel_dir
        target_sub.mkdir(parents=True, exist_ok=True)
        for fname in files:
            if check_cancel and check_cancel():
                raise InterruptedError('数据迁移已取消。')
            s_file = Path(root_dir) / fname
            d_file = target_sub / fname
            if not d_file.exists():
                shutil.copy2(_native_path(s_file), _native_path(d_file))
                copied += 1
    return copied


def migrate_user_data(
    source_root: Path,
    new_root: Path,
    *,
    progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    check_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Non-destructively migrate config, jobs, presets, accounts, optional models & Qwen weights."""
    old_root = Path(source_root).resolve()
    dst_root = Path(new_root).resolve()
    migrated: dict[str, object] = {'directories': {}, 'config_migrated': False, 'qwen_weights_migrated': 0}

    total_steps = len(MIGRATED_DIRS) + 2
    step = 0

    # 1. Merge config.json (keep new defaults for new keys, preserve user overrides, rewrite old root paths)
    old_cfg_path = old_root / 'config.json'
    new_cfg_path = dst_root / 'config.json'
    new_example = dst_root / 'config.example.json'
    if old_cfg_path.is_file():
        try:
            old_cfg = json.loads(old_cfg_path.read_text(encoding='utf-8-sig'))
            base_cfg = (
                json.loads(new_example.read_text(encoding='utf-8-sig'))
                if new_example.is_file()
                else (json.loads(new_cfg_path.read_text(encoding='utf-8-sig')) if new_cfg_path.is_file() else {})
            )
            if isinstance(old_cfg, dict) and isinstance(base_cfg, dict):
                merged_cfg = dict(base_cfg)
                for key, val in old_cfg.items():
                    # Never carry over stale internal tool paths from old installation root
                    if key in ('python', 'vocal2midi', 'ffmpeg', 'mdx_model', 'mdx_metadata',
                               'chorus_model', 'chorus_metadata', 'publish_browser_executable'):
                        continue
                    merged_cfg[key] = _rewrite_config_paths(val, old_root, dst_root)
                new_cfg_path.write_text(json.dumps(merged_cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                migrated['config_migrated'] = True
        except (OSError, ValueError):
            pass
    step += 1
    progress(step, total_steps, '已迁移个人设置与配置')

    # 2. Copy user directories & optional downloaded components (never overwriting new runtime files)
    for rel in MIGRATED_DIRS:
        src_dir = old_root / rel
        if src_dir.is_dir():
            count = _copy_tree_merge(src_dir, dst_root / rel, check_cancel)
            migrated['directories'][rel] = count
        step += 1
        progress(step, total_steps, f'已迁移 {rel}')

    # 3. Preserve manually installed Chinese Qwen weights when upgrading JPN -> JPN
    qwen_copied = 0
    for rel in QWEN_WEIGHT_FILES:
        src_w = old_root / rel
        dst_w = dst_root / rel
        if src_w.is_file() and not dst_w.is_file():
            dst_w.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_native_path(src_w), _native_path(dst_w))
            qwen_copied += 1
    migrated['qwen_weights_migrated'] = qwen_copied
    step += 1
    progress(step, total_steps, '用户数据迁移完成')

    receipt = {
        'schema_version': 1,
        'migrated_at': datetime.now(timezone.utc).isoformat(),
        'source_root': str(old_root),
        'source_version': installed_version(old_root),
        'target_root': str(dst_root),
        'target_version': installed_version(dst_root),
        'summary': migrated,
    }
    receipt_path = dst_root / RECEIPT_REL
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def launch_migrated_app(new_root: Path) -> bool:
    """Launch VWA.exe (or fallback launch.py) in the newly migrated directory."""
    root = Path(new_root).resolve()
    exe = root / 'VWA.exe'
    env = dict(os.environ)
    env.pop('VWA_LAUNCHER_PID', None)
    env.pop('BVWA_LAUNCHER_PID', None)
    env['PYTHONUTF8'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    if exe.is_file():
        subprocess.Popen([str(exe)], cwd=str(root), env=env)
        return True
    launch_py = root / 'launch.py'
    if launch_py.is_file():
        subprocess.Popen([sys.executable, '-B', str(launch_py), '--desktop'], cwd=str(root), env=env)
        return True
    return False


def clear_handoff(source_root: Path) -> None:
    (Path(source_root).resolve() / HANDOFF_REL).unlink(missing_ok=True)


def run_migration(
    source_root: Path,
    target_version: str,
    target_dir: Path,
    *,
    variant: str = 'auto',
    source: str = 'auto',
    auto_launch: bool = True,
    progress: Callable[[int, int, str], None] = lambda d, t, m: None,
    check_cancel: Callable[[], bool] | None = None,
    reader: Callable[[str, str], dict] | None = None,
    downloader: Callable[[dict, Path, Callable[[int], None]], Path] | None = None,
) -> dict:
    """Execute the full download -> verify -> extract -> migrate -> relaunch pipeline."""
    old_root = Path(source_root).resolve()
    resolved_variant = detect_install_variant(old_root) if variant == 'auto' else variant
    progress(0, 100, f'正在读取 VWA {target_version} ({resolved_variant.upper()}) 发布清单…')
    plan = fetch_portable_plan(target_version, resolved_variant, source=source, reader=reader)
    cache_dir = old_root / 'cache/updates' / f'full-{plan["version"]}-{resolved_variant}'
    archive = download_and_merge_package(
        plan, cache_dir, source=source, progress=progress, check_cancel=check_cancel, downloader=downloader,
    )
    new_root = extract_verified_archive(
        archive, target_dir, expected_version=plan['version'], progress=progress, check_cancel=check_cancel,
    )
    receipt = migrate_user_data(old_root, new_root, progress=progress, check_cancel=check_cancel)
    clear_handoff(old_root)
    launched = launch_migrated_app(new_root) if auto_launch else False
    receipt['launched'] = launched
    return receipt


def run_gui(source_root: Path, target_version: str, default_source: str = 'auto') -> int:
    """Interactive Tkinter GUI downloader & migrator (zero Qt coupling, works on all old versions)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    old_root = Path(source_root).resolve()
    detected = detect_install_variant(old_root)
    old_ver = installed_version(old_root)

    root = tk.Tk()
    root.title(f'VWA {target_version} 全量更新与数据迁移器')
    root.geometry('620x420')
    root.resizable(False, False)

    cancel_flag = threading.Event()
    running = {'active': False}

    frm = ttk.Frame(root, padding=18)
    frm.pack(fill='both', expand=True)

    ttk.Label(
        frm,
        text=f'升级到 VWA {target_version}（当前版本：{old_ver}）',
        font=('Microsoft YaHei UI', 13, 'bold'),
    ).pack(anchor='w')
    ttk.Label(
        frm,
        text='下载官方校验完整包并自动迁移你的工程任务、个人设置、预设库、登录会话及已下载的可选模型；原目录保持不变以便回退。',
        wraplength=580,
    ).pack(anchor='w', pady=(6, 12))

    row1 = ttk.Frame(frm)
    row1.pack(fill='x', pady=4)
    ttk.Label(row1, text='安装版本：', width=12).pack(side='left')
    var_variant = tk.StringVar(value=detected)
    ttk.Radiobutton(row1, text='Full 完整版（含中文模型）', variable=var_variant, value='full').pack(side='left', padx=(0, 12))
    ttk.Radiobutton(row1, text='JPN 精简版（不含中文模型）', variable=var_variant, value='jpn').pack(side='left')

    row2 = ttk.Frame(frm)
    row2.pack(fill='x', pady=4)
    ttk.Label(row2, text='下载来源：', width=12).pack(side='left')
    var_source = tk.StringVar(value=default_source if default_source in MIRROR_PREFIXES else 'auto')
    ttk.Combobox(row2, textvariable=var_source, values=('auto', 'mirror', 'direct'), state='readonly', width=18).pack(side='left')

    row3 = ttk.Frame(frm)
    row3.pack(fill='x', pady=4)
    ttk.Label(row3, text='新安装目录：', width=12).pack(side='left')
    var_target = tk.StringVar(value=str(default_target_directory(old_root, target_version)))
    ttk.Entry(row3, textvariable=var_target).pack(side='left', fill='x', expand=True, padx=(0, 6))

    def _browse():
        chosen = filedialog.askdirectory(initialdir=str(old_root.parent))
        if chosen:
            var_target.set(str(Path(chosen) / f'VWA-{target_version.removeprefix("v")}'))

    ttk.Button(row3, text='浏览…', command=_browse).pack(side='left')

    pbar = ttk.Progressbar(frm, mode='determinate', maximum=100)
    pbar.pack(fill='x', pady=(18, 6))
    status_var = tk.StringVar(value='准备就绪，点击下方按钮开始下载并自动迁移。')
    ttk.Label(frm, textvariable=status_var, wraplength=580).pack(anchor='w')

    btns = ttk.Frame(frm)
    btns.pack(fill='x', side='bottom', pady=(14, 0))
    btn_start = ttk.Button(btns, text=f'下载 VWA {target_version} 并自动迁移')
    btn_cancel = ttk.Button(btns, text='取消', state='disabled')
    btn_skip = ttk.Button(btns, text='暂不升级（启动旧版）')
    btn_start.pack(side='left')
    btn_cancel.pack(side='left', padx=8)
    btn_skip.pack(side='right')

    result_code = {'code': 0}

    def _ui_progress(done: int, total: int, msg: str) -> None:
        pct = int(min(100, (done * 100) / max(1, total)))
        root.after(0, lambda: (pbar.configure(value=pct), status_var.set(msg)))

    def _worker() -> None:
        try:
            receipt = run_migration(
                old_root,
                target_version,
                Path(var_target.get()),
                variant=var_variant.get(),
                source=var_source.get(),
                auto_launch=True,
                progress=_ui_progress,
                check_cancel=cancel_flag.is_set,
            )
            def _ok():
                pbar.configure(value=100)
                status_var.set(f'迁移完成！已启动 VWA {target_version}：{receipt["target_root"]}')
                messagebox.showinfo('升级完成', f'VWA {target_version} 已安装并完成数据迁移，正在启动新版本。')
                root.destroy()
            root.after(0, _ok)
        except InterruptedError:
            def _cancelled():
                running['active'] = False
                btn_start.configure(state='normal')
                btn_cancel.configure(state='disabled')
                btn_skip.configure(state='normal')
                status_var.set('已取消；已下载分卷保留在缓存中，下次可断点续传。')
            root.after(0, _cancelled)
        except Exception as exc:
            err_msg = str(exc)
            def _fail():
                running['active'] = False
                btn_start.configure(state='normal')
                btn_cancel.configure(state='disabled')
                btn_skip.configure(state='normal')
                status_var.set(f'升级失败：{err_msg}')
                messagebox.showerror('升级失败', err_msg)
            root.after(0, _fail)

    def _start():
        if running['active']:
            return
        running['active'] = True
        cancel_flag.clear()
        btn_start.configure(state='disabled')
        btn_cancel.configure(state='normal')
        btn_skip.configure(state='disabled')
        threading.Thread(target=_worker, daemon=True).start()

    def _cancel():
        cancel_flag.set()
        status_var.set('正在取消…')

    def _skip():
        clear_handoff(old_root)
        result_code['code'] = 2
        root.destroy()

    btn_start.configure(command=_start)
    btn_cancel.configure(command=_cancel)
    btn_skip.configure(command=_skip)
    root.mainloop()
    return result_code['code']


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='VWA Full-Package Downloader & Data Migrator')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--target-version', required=True, help='Target VWA release version (e.g. 3.0.0)')
    parser.add_argument('--target-dir', type=Path, default=None, help='Destination folder for the new version')
    parser.add_argument('--variant', choices=('auto', 'full', 'jpn'), default='auto')
    parser.add_argument('--source', choices=('auto', 'mirror', 'direct'), default='auto')
    parser.add_argument('--headless', action='store_true', help='Run without GUI (CLI mode)')
    parser.add_argument('--no-launch', action='store_true', help='Do not launch VWA.exe after migration')
    args = parser.parse_args(argv)

    if not args.headless:
        return run_gui(args.source_root, args.target_version, args.source)

    target_dir = args.target_dir or default_target_directory(args.source_root, args.target_version)
    receipt = run_migration(
        args.source_root,
        args.target_version,
        target_dir,
        variant=args.variant,
        source=args.source,
        auto_launch=not args.no_launch,
        progress=lambda d, t, m: print(f'[{int(d * 100 / max(1, t)):3d}%] {m}', flush=True),
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
