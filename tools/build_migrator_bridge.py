"""Build 2-hop bridge update archives for legacy VWA clients (0.4.0 / 1.0.0 / 1.1.0 / 2.0.0 / 2.1.0).

Old released VWA clients enforce their frozen ROOT_FILES / TOOL_FILES allowlists,
runtime_sha256 checks, and catalog schemas during Hop 1:
- v2.0.0 / v2.1.0 (Quick): reads ``program_update`` (``VWA-Program-Update.zip``)
- v1.0.0 / v1.1.0 (Widgets): reads ``updates`` catalog route with narrow bootstrap
  (``VWA-App-Update-v<ver>.zip`` containing ONLY ``app.py``, ``version.json``,
  ``tools/update_apply.py``) + tiny bridge payload (``VWA-Runtime-Upgrade-v<ver>.zip``)
- v0.4.0 (Widgets): skips ``program_update`` (different runtime_sha256) and falls back to
  ``update`` (``BVWA-App-Update.zip``)

When the user accepts the Hop-1 update and clicks Restart, the old client atomically swaps
in the bridge entry and restarts into the zero-dependency VWA Full-Package Downloader &
Auto-Migrator (local loopback Web UI, works on Windows Embeddable Python with zero Tkinter
or Qt coupling), downloads the verified target split archive, migrates all user data, and
launches the new VWA.exe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

LEGACY_RUNTIME_SHAS = {
    '2.x': '61a99c9f30945dab1ce1c2dd5f64567f909268856544f83b279231a20ea1a23c',
    '1.x': '61a99c9f30945dab1ce1c2dd5f64567f909268856544f83b279231a20ea1a23c',
    '0.4.1': '61a99c9f30945dab1ce1c2dd5f64567f909268856544f83b279231a20ea1a23c',
    '0.4.0': 'a07c667fcd230f09fad192e151360ac20ccdd02c52d3eb499d6d314fc2df5ed6',
    '0.3.x': '531e749d595ac0b550ef69a43b1a8081228ebb4c9b3d8877ae918ada016142d9',
}

# Required filenames validated by v2.0.0 / v2.1.0 validate_quick_program()
V2_REQUIRED_FILES = (
    'launch.py',
    'quick_app.py',
    'quick_runtime.py',
    'updater.py',
    'tools/update_apply.py',
    'quick_ui/Main.qml',
    'version.json',
)

# Self-contained helper embedded inside VWA-App-Update-v<ver>.zip (and identically in
# VWA-Runtime-Upgrade-v<ver>.zip) so frozen v1.0.0 / v1.1.0 prepare_catalog_update()
# can verify, stage, and atomically apply the Hop-1 bridge cleanly.
_CATALOG_BRIDGE_HELPER = '''"""Self-contained Hop-1 catalog bridge helper for frozen v1.0.0 / v1.1.0 VWA clients."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
import urllib.request
import zipfile

ALLOWED_FILES = frozenset({'app.py', 'launch.py', 'version.json', 'tools/update_apply.py'})
REPO = 'Transilent/Better-Vocaloid-Workflow-Assistant'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_rel(name: str) -> PurePosixPath:
    rel = PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or '\\\\' in name or ':' in name or name not in ALLOWED_FILES:
        raise ValueError('桥接更新包含非法文件：' + str(name))
    return rel


def _download_fallback(record: dict, folder: Path, progress, check, source: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / record['name']
    if dest.is_file() and dest.stat().st_size == record['bytes'] and _sha256(dest) == record['sha256']:
        return dest
    url = record['url']
    prefixes = ('', 'https://api.gitproxy.dev/', 'https://gh-proxy.com/', 'https://ghfast.top/')
    if source in ('mirror', 'gitproxy', 'gh-proxy', 'ghfast'):
        prefixes = ('https://api.gitproxy.dev/', 'https://gh-proxy.com/', 'https://ghfast.top/', '')
    elif source == 'direct':
        prefixes = ('',)
    last_err = None
    for prefix in prefixes:
        if check and check():
            raise InterruptedError('已取消更新下载。')
        try:
            req = urllib.request.Request(prefix + url, headers={'User-Agent': 'VWA-BridgeHelper/3.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read(record['bytes'] + 1)
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('桥接负载校验失败。')
            tmp = dest.with_suffix('.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            if progress:
                progress(record['bytes'], record['bytes'], '桥接迁移程序已下载并校验')
            return dest
        except Exception as exc:
            last_err = exc
    raise OSError(f'无法下载桥接迁移包：{last_err}')


def prepare_runtime_upgrade(
    item, root, progress=lambda *args: None, check=None, source='auto', downloader=None, *,
    register_pending=False,
):
    """Download & verify tiny bridge payload ZIP and stage a standard Hop-1 plan.json."""
    root = Path(root).resolve()
    version = str(item['version'])
    runtime_sha = _sha256(root / 'dependencies/manifest.json')
    if item.get('source_runtime_sha256') != runtime_sha:
        raise ValueError('当前运行库指纹与桥接更新不匹配。')

    cache = root / 'cache/updates'
    downloads = cache / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    selected = dict(item)
    selected['url'] = (
        f'https://github.com/{REPO}/releases/download/v{quote(version, safe="")}/'
        f'{quote(item["name"], safe="")}'
    )
    if downloader is not None:
        archive = Path(downloader(selected, downloads, progress, check, source))
    else:
        archive = _download_fallback(selected, downloads, progress, check, source)

    if archive.stat().st_size != item['bytes'] or _sha256(archive) != item['sha256']:
        raise ValueError('桥接负载包 SHA-256 校验失败。')

    stage = Path(tempfile.mkdtemp(prefix='stage-bridge-', dir=cache))
    try:
        files_dir = stage / 'files'
        files_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'r') as zf:
            manifest = json.loads(zf.read('bridge-payload.json').decode('utf-8-sig'))
            if manifest.get('version') != version or manifest.get('source_runtime_sha256') != runtime_sha:
                raise ValueError('桥接负载版本或运行库不匹配。')
            entries = manifest.get('entries') or []
            if {e['file'] for e in entries} != set(ALLOWED_FILES):
                raise ValueError('桥接负载文件清单不完整。')
            for entry in entries:
                _validate_rel(entry['file'])
                raw = zf.read(entry['file'])
                if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                    raise ValueError('桥接文件校验失败：' + entry['file'])
                dest = files_dir / entry['file']
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
        helper_src = files_dir / 'tools/update_apply.py'
        shutil.copy2(helper_src, stage / 'apply_helper.py')
        plan = {
            'kind': 'bridge_program',
            'root': str(root),
            'version': version,
            'runtime_sha256': runtime_sha,
            'entries': entries,
        }
        plan_path = stage / 'plan.json'
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        if register_pending:
            pending_path = cache / 'pending.json'
            raw_plan = plan_path.read_bytes()
            pending_path.write_text(
                json.dumps({
                    'plan': str(plan_path),
                    'version': version,
                    'plan_sha256': hashlib.sha256(raw_plan).hexdigest(),
                }, ensure_ascii=False),
                encoding='utf-8',
            )
        return plan_path
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _wait_for_pid(pid: int, timeout: float = 45.0) -> None:
    if not pid or pid <= 0 or os.name != 'nt':
        return
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def apply_update(plan_path: Path) -> dict:
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
    root = Path(plan['root']).resolve()
    stage = plan_path.parent
    entries = plan['entries']
    if _sha256(root / 'dependencies/manifest.json') != plan['runtime_sha256']:
        raise ValueError('基础运行库版本已改变，请重新下载更新。')
    for entry in entries:
        _validate_rel(entry['file'])
        src = stage / 'files' / entry['file']
        if not src.is_file() or src.stat().st_size != entry['bytes'] or _sha256(src) != entry['sha256']:
            raise ValueError('待安装桥接文件校验失败：' + entry['file'])

    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=stage))
    touched: list[tuple[Path, Path, bool]] = []
    try:
        for entry in sorted(entries, key=lambda item: (item['file'] == 'version.json', item['file'])):
            dst = root / entry['file']
            saved = backup / entry['file']
            existed = dst.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, saved)
            touched.append((dst, saved, existed))
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + '.vwa-update')
            try:
                shutil.copy2(stage / 'files' / entry['file'], tmp)
                os.replace(tmp, dst)
            finally:
                tmp.unlink(missing_ok=True)
    except Exception:
        for dst, saved, existed in reversed(touched):
            try:
                if existed and saved.exists():
                    shutil.copy2(saved, dst)
                elif not existed and dst.exists():
                    dst.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {'state': 'passed', 'version': plan['version']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--parent', type=int, default=0)
    parser.add_argument('--launcher-parent', type=int, default=0)
    parser.add_argument('--no-relaunch', action='store_true')
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
    root = Path(plan['root']).resolve()
    parents_exited = False
    try:
        _wait_for_pid(args.parent)
        _wait_for_pid(args.launcher_parent)
        parents_exited = True
        result = apply_update(args.plan)
    except Exception as exc:
        result = {'state': 'failed', 'error': str(exc), 'version': plan.get('version')}
    pending = root / 'cache/updates/pending.json'
    try:
        if pending.is_file():
            rec = json.loads(pending.read_text(encoding='utf-8-sig'))
            if Path(rec.get('plan', '')).resolve() == args.plan.resolve():
                pending.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
    try:
        res_path = root / 'cache/updates/last-result.json'
        res_path.parent.mkdir(parents=True, exist_ok=True)
        res_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass
    launcher = next((root / n for n in ('VWA.exe', 'BVWA.exe') if (root / n).is_file()), root / 'VWA.exe')
    if parents_exited and not args.no_relaunch and launcher.is_file():
        subprocess.Popen([str(launcher)], cwd=root, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0 if result.get('state') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
'''


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _default_target_runtime_sha256() -> str:
    manifest = ROOT / 'dependencies/manifest.json'
    if manifest.is_file():
        return _sha256_file(manifest)
    return LEGACY_RUNTIME_SHAS['2.x']


def render_embedded_bridge_entry(target_version: str, *, default_source: str = 'auto') -> bytes:
    """Return a single self-contained Python entry embedding migrator core + Web UI for Hop-2 launch."""
    core_src = (ROOT / 'src/full_package_migrator.py').read_text(encoding='utf-8')
    core_src = core_src.split("\nif __name__ == '__main__':", 1)[0]
    ui_src = (ROOT / 'src/full_package_migrator_ui.py').read_text(encoding='utf-8')
    ui_src = ui_src.split("\nif __name__ == '__main__':", 1)[0]
    ui_lines: list[str] = []
    skipping = False
    for line in ui_src.splitlines():
        if line.strip() == 'from __future__ import annotations':
            continue
        if '# BRIDGE_IMPORT_START' in line:
            skipping = True
            continue
        if '# BRIDGE_IMPORT_END' in line:
            skipping = False
            continue
        if not skipping:
            ui_lines.append(line)
    ui_body = '\n'.join(ui_lines)
    wrapper = (
        f'# VWA {target_version} 2-hop Full-Package Downloader & Auto-Migrator bridge entry.\n'
        + core_src
        + '\n\n# --- Embedded zero-dependency local Web UI ---\n'
        + ui_body
        + '\n\n'
        + f'BRIDGE_TARGET_VERSION = {target_version!r}\n'
        + f'BRIDGE_DEFAULT_SOURCE = {default_source!r}\n\n'
        + 'def _bridge_main():\n'
        + '    source_root = Path(__file__).resolve().parent\n'
        + '    prefs = load_network_preferences(source_root)\n'
        + '    src = prefs.get("source") or BRIDGE_DEFAULT_SOURCE\n'
        + '    return run_gui(source_root, BRIDGE_TARGET_VERSION, src, bridge_mode=True)\n\n'
        + 'if __name__ == "__main__":\n'
        + '    raise SystemExit(_bridge_main())\n'
    )
    return wrapper.encode('utf-8')


def build_quick_v2_bridge(
    output_zip: Path,
    *,
    target_version: str = '3.0.0',
    source_runtime_sha256: str = LEGACY_RUNTIME_SHAS['2.x'],
    qt_version: str = '6.10.3',
) -> dict:
    """Build Hop-1 VWA-Program-Update.zip accepted by frozen v2.0.0 / v2.1.0 Quick clients."""
    output_zip = Path(output_zip).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    bridge_bytes = render_embedded_bridge_entry(target_version)
    version_doc = json.dumps(
        {
            'version': target_version,
            'channel': 'stable',
            'desktop_frontend': 'quick',
            'qt_version': qt_version,
            'bridge_migrator': True,
        },
        ensure_ascii=False,
        indent=2,
    ).encode('utf-8')

    payload: dict[str, bytes] = {
        'launch.py': bridge_bytes,
        'version.json': version_doc,
        'quick_app.py': b'from launch import _bridge_main as main\n',
        'quick_runtime.py': (ROOT / 'src/quick_runtime.py').read_bytes(),
        'updater.py': (ROOT / 'src/updater.py').read_bytes(),
        'tools/update_apply.py': (ROOT / 'tools/update_apply.py').read_bytes(),
        'quick_ui/Main.qml': b'import QtQuick\nItem {}\n',
    }

    entries = [
        {'file': name, 'bytes': len(data), 'sha256': _sha256_bytes(data)}
        for name, data in sorted(payload.items())
    ]
    inventory = {
        'version': target_version,
        'runtime_sha256': source_runtime_sha256,
        'desktop_frontend': 'quick',
        'qt_version': qt_version,
        'bridge_migrator': True,
        'entries': entries,
    }

    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in sorted(payload.items()):
            zf.writestr(name, data)
        zf.writestr('update-inventory.json', json.dumps(inventory, ensure_ascii=False, indent=2))

    return {
        'name': output_zip.name,
        'bytes': output_zip.stat().st_size,
        'sha256': _sha256_file(output_zip),
        'runtime_sha256': source_runtime_sha256,
        'desktop_frontend': 'quick',
        'qt_version': qt_version,
        'bridge_migrator': True,
        'target_version': target_version,
    }


def build_v1_catalog_bridge(
    output_dir: Path,
    *,
    target_version: str = '3.0.0',
    source_runtime_sha256: str = LEGACY_RUNTIME_SHAS['1.x'],
    target_runtime_sha256: str | None = None,
) -> dict:
    """Build exact Hop-1 catalog route (VWA-App-Update-v<ver>.zip + VWA-Runtime-Upgrade-v<ver>.zip)
    accepted by frozen v1.0.0 and v1.1.0 ``select_update_route()`` + ``prepare_catalog_update()``.
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ver = target_version.removeprefix('v')
    target_sha = target_runtime_sha256 or _default_target_runtime_sha256()

    bridge_bytes = render_embedded_bridge_entry(ver)
    helper_bytes = _CATALOG_BRIDGE_HELPER.encode('utf-8')
    final_version_doc = json.dumps(
        {'version': ver, 'channel': 'stable', 'bridge_migrator': True},
        ensure_ascii=False,
        indent=2,
    ).encode('utf-8')

    # 1. Tiny bridge payload ZIP (named VWA-Runtime-Upgrade-v<ver>.zip to satisfy _runtime_name())
    runtime_zip = out_dir / f'VWA-Runtime-Upgrade-v{ver}.zip'
    runtime_files: dict[str, bytes] = {
        'app.py': bridge_bytes,
        'launch.py': bridge_bytes,
        'version.json': final_version_doc,
        'tools/update_apply.py': helper_bytes,
    }
    runtime_entries = [
        {'file': name, 'bytes': len(data), 'sha256': _sha256_bytes(data)}
        for name, data in sorted(runtime_files.items())
    ]
    bridge_payload_manifest = {
        'version': ver,
        'source_runtime_sha256': source_runtime_sha256,
        'target_runtime_sha256': target_sha,
        'bridge_migrator': True,
        'entries': runtime_entries,
    }
    with zipfile.ZipFile(runtime_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in sorted(runtime_files.items()):
            zf.writestr(name, data)
        zf.writestr('bridge-payload.json', json.dumps(bridge_payload_manifest, ensure_ascii=False, indent=2))

    runtime_record = {
        'name': runtime_zip.name,
        'bytes': runtime_zip.stat().st_size,
        'sha256': _sha256_file(runtime_zip),
        'version': ver,
        'source_runtime_sha256': source_runtime_sha256,
        'target_runtime_sha256': target_sha,
    }

    # 2. Narrow bootstrap ZIP (EXACTLY {'app.py', 'version.json', 'tools/update_apply.py'} + update-inventory.json)
    bootstrap_zip = out_dir / f'VWA-App-Update-v{ver}.zip'
    bootstrap_version_doc = json.dumps(
        {
            'version': ver,
            'channel': 'stable',
            'upgrade_pending': True,
            'runtime_upgrade': runtime_record,
        },
        ensure_ascii=False,
        indent=2,
    ).encode('utf-8')
    bootstrap_files: dict[str, bytes] = {
        'app.py': bridge_bytes,
        'version.json': bootstrap_version_doc,
        'tools/update_apply.py': helper_bytes,
    }
    bootstrap_entries = [
        {'file': name, 'bytes': len(data), 'sha256': _sha256_bytes(data)}
        for name, data in sorted(bootstrap_files.items())
    ]
    bootstrap_inventory = {
        'version': ver,
        'runtime_sha256': source_runtime_sha256,
        'entries': bootstrap_entries,
    }
    with zipfile.ZipFile(bootstrap_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in sorted(bootstrap_files.items()):
            zf.writestr(name, data)
        zf.writestr('update-inventory.json', json.dumps(bootstrap_inventory, ensure_ascii=False, indent=2))

    bootstrap_record = {
        'name': bootstrap_zip.name,
        'bytes': bootstrap_zip.stat().st_size,
        'sha256': _sha256_file(bootstrap_zip),
        'runtime_sha256': source_runtime_sha256,
    }

    return {
        'source_versions': ['1.0.0', '1.1.0'],
        'runtime_sha256': source_runtime_sha256,
        'update': bootstrap_record,
        'runtime_upgrade': runtime_record,
    }


def build_legacy_widgets_bridge(
    output_zip: Path,
    *,
    target_version: str = '3.0.0',
    source_runtime_sha256: str = LEGACY_RUNTIME_SHAS['0.4.0'],
) -> dict:
    """Build Hop-1 BVWA-App-Update.zip accepted by frozen v0.4.0 Widgets clients."""
    output_zip = Path(output_zip).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    bridge_bytes = render_embedded_bridge_entry(target_version)
    version_doc = json.dumps(
        {'version': target_version, 'channel': 'stable', 'bridge_migrator': True},
        ensure_ascii=False,
        indent=2,
    ).encode('utf-8')

    payload: dict[str, bytes] = {
        'app.py': bridge_bytes,
        'launch.py': bridge_bytes,
        'version.json': version_doc,
    }
    entries = [
        {'file': name, 'bytes': len(data), 'sha256': _sha256_bytes(data)}
        for name, data in sorted(payload.items())
    ]
    inventory = {
        'version': target_version,
        'runtime_sha256': source_runtime_sha256,
        'bridge_migrator': True,
        'entries': entries,
    }

    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in sorted(payload.items()):
            zf.writestr(name, data)
        zf.writestr('update-inventory.json', json.dumps(inventory, ensure_ascii=False, indent=2))

    return {
        'name': output_zip.name,
        'bytes': output_zip.stat().st_size,
        'sha256': _sha256_file(output_zip),
        'runtime_sha256': source_runtime_sha256,
        'bridge_migrator': True,
        'target_version': target_version,
    }


def build_all_legacy_bridges(
    output_dir: Path, *, target_version: str = '3.0.0', target_runtime_sha256: str | None = None,
) -> dict:
    """Build Hop-1 bridges + exact release-assets.json fragment for v2.x, v1.x, and v0.4.0."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_sha = target_runtime_sha256 or _default_target_runtime_sha256()

    v2 = build_quick_v2_bridge(
        out_dir / 'VWA-Program-Update.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['2.x'],
    )
    v1_route = build_v1_catalog_bridge(
        out_dir,
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['1.x'],
        target_runtime_sha256=target_sha,
    )
    v040 = build_legacy_widgets_bridge(
        out_dir / 'BVWA-App-Update.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['0.4.0'],
    )
    manifest_fragment = {
        'program_update': v2,
        'updates': [v1_route],
        'update': v040,
    }
    (out_dir / 'bridge-release-manifest.json').write_text(
        json.dumps(manifest_fragment, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return {
        'quick_v2_bridge': v2,
        'v1_catalog_route': v1_route,
        'legacy_040_bridge': v040,
        'release_assets_fragment': manifest_fragment,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Build Hop-1 migrator bridge archives for VWA releases.')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--target-version', default='3.0.0')
    parser.add_argument('--target-runtime-sha256', default=None)
    args = parser.parse_args(argv)
    summary = build_all_legacy_bridges(
        args.output_dir,
        target_version=args.target_version,
        target_runtime_sha256=args.target_runtime_sha256,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
