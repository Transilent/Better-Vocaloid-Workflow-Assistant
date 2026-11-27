"""Package or stage a ready-to-run Windows x64 VWA WebGPU build inside ``build/``.

All build outputs default to the gitignored ``build/`` directory so compiled
executables, staged runtimes, and release archives never mix with source files:
- ``build/VWA/``: standalone ready-to-run folder (double-click ``VWA.exe``)
- ``build/release/``: portable ``.zip`` archive and ``SHA256SUMS.txt``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = (
    'dependencies/vocal2midi/python',
    'dependencies/vocal2midi/application',
    'dependencies/vocal2midi/inference',
    'dependencies/vocal2midi/settings',
    'dependencies/vocal2midi/experiments',
    'dependencies/vocal2midi2',
    'dependencies/export',
    'dependencies/ffmpeg',
    'dependencies/models',
    'models',
    'runtime/qt6',
    'components/native-audio',
)

SKIP_DIR_NAMES = frozenset({'__pycache__', '.git', 'cache', 'jobs', 'work', 'dist', 'build', 'publish-packages'})
SKIP_SUFFIXES = frozenset({'.pyc', '.pyo', '.partial', '.lock', '.log'})
HARDLINK_SUFFIXES = frozenset({'.onnx', '.gguf', '.dll', '.pyd', '.exe', '.bin'})


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _should_skip(rel_parts: tuple[str, ...], suffix: str) -> bool:
    if suffix.lower() in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIR_NAMES for part in rel_parts)


def _resolve_runtime_root(root: Path, runtime_root: Path | None) -> Path:
    if runtime_root is not None:
        return runtime_root.resolve()
    if (root / 'dependencies/vocal2midi/python/python.exe').is_file():
        return root.resolve()
    staged = root / 'build/VWA'
    if (staged / 'dependencies/vocal2midi/python/python.exe').is_file():
        return staged.resolve()
    return root.resolve()


def _find_launcher(root: Path, rt_root: Path) -> Path | None:
    for candidate in (
        root / 'build/VWA/VWA.exe',
        rt_root / 'VWA.exe',
        root / 'VWA.exe',
    ):
        if candidate.is_file():
            return candidate
    return None


def collect_portable_files(
    root: Path,
    *,
    runtime_root: Path | None = None,
    include_experiments: bool = True,
    include_qwen: bool = False,
    include_roformer: bool = False,
) -> dict[str, Path]:
    root = root.resolve()
    rt_root = _resolve_runtime_root(root, runtime_root)
    mapping: dict[str, Path] = {}

    # 1. Application files from packaging/portable-files.json (exact upstream layout)
    portable_manifest = json.loads((root / 'packaging/portable-files.json').read_text(encoding='utf-8'))
    for entry in portable_manifest['entries']:
        dest = entry['file']
        src = root / entry['source']
        if src.is_file():
            mapping[dest] = src

    # 2. Launcher, config, and quick batch entry
    launcher = _find_launcher(root, rt_root)
    if launcher is not None:
        mapping['VWA.exe'] = launcher
    for dest, rel_src in (
        ('Start-Quick.bat', 'tools/portable/Start-Quick.bat'),
        ('config.json', 'packaging/config.example.json'),
        ('dependencies/vocal2midi2/backend-version.json', 'dependencies/vocal2midi2/backend-version.json'),
    ):
        src = root / rel_src
        if not src.is_file():
            src = rt_root / rel_src
        if src.is_file():
            mapping[dest] = src

    # 3. Built runtime payloads (Python 3.12 + onnxruntime-webgpu, Qt 6.10.3, export, FFmpeg, MDX, models, DLLs)
    dirs_to_scan = list(RUNTIME_DIRS)
    if include_roformer:
        dirs_to_scan.append('dependencies/optional/bs-roformer')

    for rel_dir in dirs_to_scan:
        if not include_experiments and rel_dir in ('dependencies/vocal2midi/experiments', 'dependencies/vocal2midi2/models'):
            continue
        folder = rt_root / rel_dir
        if not folder.is_dir():
            folder = root / rel_dir
        if not folder.is_dir():
            continue
        base_root = rt_root if folder.is_relative_to(rt_root) else root
        for file_path in sorted(folder.rglob('*')):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            rel = file_path.relative_to(base_root)
            rel_posix = rel.as_posix()
            if _should_skip(rel.parts, file_path.suffix):
                continue
            # Qwen3-ASR (~4.5 GiB) exceeds GitHub Release's 2 GiB single-asset limit
            # and is downloadable in-app from Models & Components unless --include-qwen is set.
            if not include_qwen and '/Qwen3-ASR-1.7B-dml/' in f'/{rel_posix}':
                continue
            if 'site-packages' in rel.parts and 'tests' in rel.parts and file_path.suffix == '.bin':
                continue
            mapping[rel_posix] = file_path

    return mapping


def validate_required_payloads(mapping: dict[str, Path]) -> None:
    required = (
        'VWA.exe',
        'launch.py',
        'quick_app.py',
        'config.json',
        'dependencies/vocal2midi/python/python.exe',
        'dependencies/vocal2midi/python/Lib/site-packages/onnxruntime/capi/onnxruntime.dll',
        'runtime/qt6/PySide6/plugins/platforms/qwindows.dll',
        'runtime/qt6/WHEELS.json',
        'dependencies/export/libresvip/__init__.py',
        'dependencies/ffmpeg/ffmpeg.exe',
        'dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx',
        'models/UVR_MDXNET_KARA_2.onnx',
    )
    missing = [item for item in required if item not in mapping]
    if missing:
        raise FileNotFoundError(
            'Portable build is missing required runtime files:\n  - ' + '\n  - '.join(missing)
        )


def _materialize_file(src: Path, dst: Path, *, allow_hardlink: bool = True) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if allow_hardlink and src.suffix.lower() in HARDLINK_SUFFIXES:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def stage_directory(
    stage_dir: Path,
    *,
    runtime_root: Path | None = None,
    include_experiments: bool = True,
    include_qwen: bool = True,
    include_roformer: bool = True,
    preserve_config: bool = True,
) -> dict:
    """Materialize a standalone, ready-to-run VWA folder (e.g. ``build/VWA``)."""
    stage_dir = stage_dir.resolve()
    mapping = collect_portable_files(
        ROOT,
        runtime_root=runtime_root,
        include_experiments=include_experiments,
        include_qwen=include_qwen,
        include_roformer=include_roformer,
    )
    validate_required_payloads(mapping)
    stage_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    total_bytes = 0
    for dest, src in sorted(mapping.items()):
        target = stage_dir / dest
        if preserve_config and dest == 'config.json' and target.is_file():
            total_bytes += target.stat().st_size
            continue
        _materialize_file(src, target, allow_hardlink=True)
        written += 1
        total_bytes += target.stat().st_size
    print(f'Staged standalone build: {stage_dir} ({len(mapping)} files, {total_bytes / 1048576:.1f} MiB)')
    return {'stage_dir': str(stage_dir), 'files': len(mapping), 'written': written, 'bytes': total_bytes}


def build_zip(
    output_zip: Path,
    *,
    runtime_root: Path | None = None,
    layout_root: str = 'VWA',
    include_experiments: bool = True,
    include_qwen: bool = False,
    include_roformer: bool = False,
) -> dict:
    mapping = collect_portable_files(
        ROOT,
        runtime_root=runtime_root,
        include_experiments=include_experiments,
        include_qwen=include_qwen,
        include_roformer=include_roformer,
    )
    validate_required_payloads(mapping)

    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = output_zip.with_suffix('.zip.partial')

    total_files = len(mapping)
    total_raw_bytes = 0
    with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as zf:
        for index, (dest, src) in enumerate(sorted(mapping.items()), start=1):
            arcname = f'{layout_root}/{dest}'
            zf.write(src, arcname)
            total_raw_bytes += src.stat().st_size
            if index % 1000 == 0 or index == total_files:
                print(f'Packed {index}/{total_files} files ({total_raw_bytes / 1048576:.1f} MiB raw)...', flush=True)

    tmp_zip.replace(output_zip)
    sha256 = _sha256_file(output_zip)
    zip_bytes = output_zip.stat().st_size
    checksum_path = output_zip.parent / 'SHA256SUMS.txt'
    checksum_path.write_text(f'{sha256}  {output_zip.name}\n', encoding='utf-8')
    print(f'Built portable archive: {output_zip} ({zip_bytes / 1048576:.1f} MiB, sha256={sha256})')
    return {
        'archive': str(output_zip),
        'files': total_files,
        'raw_bytes': total_raw_bytes,
        'zip_bytes': zip_bytes,
        'sha256': sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=None, help='Destination .zip path (default: build/release/VWA-Windows-x64-WebGPU-Portable.zip when --stage-dir is unset)')
    parser.add_argument('--stage-dir', type=Path, default=None, help='Materialize standalone runnable folder (e.g. build/VWA)')
    parser.add_argument('--runtime-root', type=Path, default=None, help='Directory holding downloaded runtime payloads (defaults to repo root or build/VWA)')
    parser.add_argument('--layout-root', default='VWA', help='Top-level directory inside the ZIP')
    parser.add_argument('--no-experiments', action='store_true', help='Omit vocal2midi experiments weights')
    parser.add_argument('--include-qwen', action='store_true', help='Include ~4.5 GiB Qwen3-ASR weights inside the archive')
    parser.add_argument('--include-roformer', action='store_true', help='Include optional BS-RoFormer ONNX component')
    args = parser.parse_args()

    if args.stage_dir is not None:
        stage_directory(
            args.stage_dir,
            runtime_root=args.runtime_root,
            include_experiments=not args.no_experiments,
            include_qwen=True,
            include_roformer=True,
        )
    if args.output is not None or args.stage_dir is None:
        zip_target = args.output or (ROOT / 'build/release/VWA-Windows-x64-WebGPU-Portable.zip')
        build_zip(
            zip_target,
            runtime_root=args.runtime_root,
            layout_root=args.layout_root,
            include_experiments=not args.no_experiments,
            include_qwen=args.include_qwen,
            include_roformer=args.include_roformer,
        )


if __name__ == '__main__':
    main()
