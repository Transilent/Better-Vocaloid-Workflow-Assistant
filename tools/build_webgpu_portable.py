"""Package a complete, ready-to-run Windows x64 VWA WebGPU portable ZIP.

Unlike a source archive, the produced ZIP contains everything required to
extract and double-click ``VWA.exe`` with zero setup:
- ``VWA.exe`` (compiled Windows GUI launcher) & ``Start-Quick.bat``
- Prepared application Python modules, Qt Quick QML UI, assets, and vendor libs
- ``config.json`` (defaulting to ``webgpu``)
- Embedded Python 3.12 + ``onnxruntime-webgpu==1.27.0`` runtime packages
- Isolated Qt Quick 6.10.3 runtime (``runtime/qt6``)
- LibreSVIP export runtime (``dependencies/export``)
- FFmpeg binaries (``dependencies/ffmpeg``)
- UVR MDX separation models (``dependencies/models`` & ``models``)
- Compiled native audio DLLs (``components/native-audio``) when present
- Optional Vocal2Midi weights (``dependencies/vocal2midi/experiments``) when present
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = (
    'dependencies/vocal2midi/python',
    'dependencies/vocal2midi/settings',
    'dependencies/vocal2midi/experiments',
    'dependencies/vocal2midi2/models',
    'dependencies/export',
    'dependencies/ffmpeg',
    'dependencies/models',
    'models',
    'runtime/qt6',
    'components/native-audio',
)

SKIP_DIR_NAMES = frozenset({'__pycache__', '.git', 'cache', 'jobs', 'work', 'dist', 'build', 'publish-packages'})
SKIP_SUFFIXES = frozenset({'.pyc', '.pyo', '.partial'})


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


def collect_portable_files(
    root: Path,
    *,
    include_experiments: bool = True,
    include_qwen: bool = False,
) -> dict[str, Path]:
    root = root.resolve()
    mapping: dict[str, Path] = {}

    # 1. Application files from packaging/portable-files.json (exact upstream layout)
    portable_manifest = json.loads((root / 'packaging/portable-files.json').read_text(encoding='utf-8'))
    for entry in portable_manifest['entries']:
        dest = entry['file']
        src = root / entry['source']
        if src.is_file():
            mapping[dest] = src

    # 2. Launcher, config, and quick batch entry
    for dest, rel_src in (
        ('VWA.exe', 'VWA.exe'),
        ('Start-Quick.bat', 'tools/portable/Start-Quick.bat'),
        ('config.json', 'packaging/config.example.json'),
        ('dependencies/vocal2midi2/backend-version.json', 'dependencies/vocal2midi2/backend-version.json'),
    ):
        src = root / rel_src
        if src.is_file():
            mapping[dest] = src

    # 3. Built runtime payloads (Python 3.12 + onnxruntime-webgpu, Qt 6.10.3, export, FFmpeg, MDX, native DLLs)
    for rel_dir in RUNTIME_DIRS:
        if not include_experiments and rel_dir in ('dependencies/vocal2midi/experiments', 'dependencies/vocal2midi2/models'):
            continue
        folder = root / rel_dir
        if not folder.is_dir():
            continue
        for file_path in sorted(folder.rglob('*')):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            rel = file_path.relative_to(root)
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


def build_zip(
    output_zip: Path,
    *,
    layout_root: str = 'VWA',
    include_experiments: bool = True,
    include_qwen: bool = False,
) -> dict:
    mapping = collect_portable_files(
        ROOT, include_experiments=include_experiments, include_qwen=include_qwen
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
    parser.add_argument('--output', type=Path, required=True, help='Destination .zip path')
    parser.add_argument('--layout-root', default='VWA', help='Top-level directory inside the ZIP')
    parser.add_argument('--no-experiments', action='store_true', help='Omit vocal2midi experiments weights')
    parser.add_argument('--include-qwen', action='store_true', help='Include ~4.5 GiB Qwen3-ASR weights inside the archive')
    args = parser.parse_args()
    build_zip(
        args.output,
        layout_root=args.layout_root,
        include_experiments=not args.no_experiments,
        include_qwen=args.include_qwen,
    )


if __name__ == '__main__':
    main()
