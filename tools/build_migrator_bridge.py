"""Build 2-hop bridge update archives for legacy VWA clients (0.3.x / 0.4.x / 1.x / 2.x).

Old released VWA clients enforce their frozen ROOT_FILES / TOOL_FILES allowlists and
runtime_sha256 checks during Hop 1. This builder produces tiny, self-contained bridge ZIPs
containing ONLY filenames already allowlisted by each historical client generation, with
``full_package_migrator.py`` embedded inside ``launch.py`` (for 2.0.0/2.1.0 Quick clients)
or ``app.py`` (for 0.3.x/0.4.x/1.x Widgets clients).

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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def render_embedded_bridge_entry(target_version: str, *, default_source: str = 'auto') -> bytes:
    """Return a single self-contained Python entry embedding migrator core + Web UI for Hop-2 launch."""
    core_src = (ROOT / 'src/full_package_migrator.py').read_text(encoding='utf-8')
    # Strip the standalone main guard so the embedded wrapper controls entry cleanly
    core_src = core_src.split('\nif __name__ == \'__main__\':', 1)[0]
    ui_src = (ROOT / 'src/full_package_migrator_ui.py').read_text(encoding='utf-8')
    ui_src = ui_src.split('\nif __name__ == \'__main__\':', 1)[0]
    # Remove future import and cross-module import block when inlining into a single file
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
    """Build Hop-1 VWA-Program-Update.zip accepted by frozen v2.0.0 / v2.1.0 clients."""
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


def build_legacy_widgets_bridge(
    output_zip: Path,
    *,
    target_version: str = '3.0.0',
    source_runtime_sha256: str = LEGACY_RUNTIME_SHAS['1.x'],
) -> dict:
    """Build Hop-1 BVWA-App-Update.zip accepted by frozen 0.3.x / 0.4.x / 1.0.0 / 1.1.0 clients."""
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


def build_all_legacy_bridges(output_dir: Path, *, target_version: str = '3.0.0') -> dict:
    """Build Hop-1 bridges for all historical runtime_sha256 cohorts + release-assets fragment."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    v2 = build_quick_v2_bridge(
        out_dir / 'VWA-Program-Update.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['2.x'],
    )
    v1 = build_legacy_widgets_bridge(
        out_dir / 'BVWA-App-Update.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['1.x'],
    )
    v040 = build_legacy_widgets_bridge(
        out_dir / 'BVWA-App-Update-040.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['0.4.0'],
    )
    v03x = build_legacy_widgets_bridge(
        out_dir / 'BVWA-App-Update-03x.zip',
        target_version=target_version,
        source_runtime_sha256=LEGACY_RUNTIME_SHAS['0.3.x'],
    )
    # Ready-to-merge release-assets.json snippet matching frozen v2.x, v1.x, and v0.3.x/0.4.x readers.
    manifest_fragment = {
        'program_update': v2,
        'update': v1,
        'updates': [v1, v040, v03x],
    }
    (out_dir / 'bridge-release-manifest.json').write_text(
        json.dumps(manifest_fragment, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return {
        'quick_v2_bridge': v2,
        'legacy_widgets_bridge': v1,
        'legacy_040_bridge': v040,
        'legacy_03x_bridge': v03x,
        'release_assets_fragment': manifest_fragment,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Build Hop-1 migrator bridge archives for VWA releases.')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--target-version', default='3.0.0')
    args = parser.parse_args(argv)
    summary = build_all_legacy_bridges(args.output_dir, target_version=args.target_version)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
