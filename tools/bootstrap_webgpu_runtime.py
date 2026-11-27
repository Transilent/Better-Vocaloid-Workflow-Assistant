"""Bootstrap a standalone Windows x64 WebGPU runtime matching VWA's exact directory layout.

Supports two modes:
1. ``--standalone`` (default): builds the complete runnable layout from public
   upstream packages with ZERO dependency on an upstream DirectML portable zip:
     - ``dependencies/vocal2midi/python/`` (CPython 3.12 embeddable +
       ``packaging/requirements-runtime.lock.txt`` including ``onnxruntime-webgpu==1.27.0``)
     - ``runtime/qt6/`` (hash-locked PySide6 Essentials 6.10.3 + shiboken6 6.10.3 + ``WHEELS.json``)
     - ``dependencies/export/`` (export packages from ``packaging/export-runtime.lock.json``)
     - ``dependencies/ffmpeg/ffmpeg.exe``
     - ``dependencies/models/`` + ``models/`` (UVR MDX models & registry)
     - ``config.json`` (defaulting to ``webgpu``) + ``tools/Prepare-Source.ps1``
2. ``--from-portable <dir>``: imports runtime/models/Qt6 from an existing VWA
   portable directory without overwriting this fork's WebGPU inference sources,
   replaces ``onnxruntime-directml`` with ``onnxruntime-webgpu==1.27.0``,
   migrates ``config.json``, and verifies startup.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]

PYTHON_EMBED_VERSION = '3.12.10'
PYTHON_EMBED_URL = f'https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/python-{PYTHON_EMBED_VERSION}-embed-amd64.zip'
GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'
QT6_VERSION = '6.10.3'
ORT_WEBGPU_SPEC = 'onnxruntime-webgpu==1.27.0'

PYPI_MIRRORS = {
    'direct': 'https://pypi.org/simple',
    'tuna': 'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple',
    'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
    'ustc': 'https://mirrors.ustc.edu.cn/pypi/simple',
}

FFMPEG_URLS = (
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip',
)

MDX_ASSETS = (
    (
        'dependencies/models/mdx-model-data.json',
        (
            'https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/model_data.json',
            'https://ghfast.top/https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/model_data.json',
        ),
    ),
    (
        'dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx',
        (
            'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Inst_HQ_3.onnx',
            'https://ghfast.top/https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Inst_HQ_3.onnx',
        ),
    ),
    (
        'models/UVR_MDXNET_KARA_2.onnx',
        (
            'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR_MDXNET_KARA_2.onnx',
            'https://ghfast.top/https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR_MDXNET_KARA_2.onnx',
        ),
    ),
)

_PORTABLE_COPY_PATHS = (
    'dependencies/vocal2midi/python',
    'dependencies/vocal2midi/experiments',
    'dependencies/vocal2midi/settings',
    'dependencies/vocal2midi2/models',
    'dependencies/ffmpeg',
    'dependencies/models',
    'dependencies/export',
    'dependencies/browser',
    'models',
    'runtime/qt6',
    'components/native-audio',
)


def _run(argv: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print('>', ' '.join(str(a) for a in argv), flush=True)
    return subprocess.run(argv, cwd=ROOT, env=env, check=check)


def _download(urls: tuple[str, ...] | list[str], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.partial')
    last_exc: Exception | None = None
    for url in urls:
        print(f'Downloading {dest.name} <- {url}', flush=True)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'VWA-WebGPU-Bootstrap/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp, tmp.open('wb') as out:
                while chunk := resp.read(1024 * 1024):
                    out.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:
            last_exc = exc
            tmp.unlink(missing_ok=True)
            print(f'  mirror failed ({exc}); trying next...', flush=True)
    raise RuntimeError(f'Failed to download {dest.name}: {last_exc}')


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _pip_env() -> dict[str, str]:
    env = dict(os.environ)
    env['PYTHONUTF8'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    return env


def ensure_embedded_python(mirror: str) -> Path:
    py_dir = ROOT / 'dependencies/vocal2midi/python'
    py_exe = py_dir / 'python.exe'
    if not py_exe.is_file():
        py_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='vwa-py312-') as tmp:
            archive = _download([PYTHON_EMBED_URL], Path(tmp) / 'python-embed.zip')
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(py_dir)
        # Enable site-packages & project imports in python312._pth
        for pth in py_dir.glob('python*._pth'):
            lines = pth.read_text(encoding='utf-8').splitlines()
            updated = [line[1:].strip() if line.strip() == '#import site' else line for line in lines]
            if 'import site' not in updated:
                updated.append('import site')
            if 'Lib/site-packages' not in updated:
                updated.append('Lib/site-packages')
            pth.write_text('\n'.join(updated) + '\n', encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix='vwa-pip-') as tmp:
            get_pip = _download([GET_PIP_URL], Path(tmp) / 'get-pip.py')
            _run([str(py_exe), str(get_pip), '--no-warn-script-location'], env=_pip_env())
    return py_exe


def install_runtime_packages(py_exe: Path, mirror: str) -> None:
    env = _pip_env()
    probe = subprocess.run(
        [str(py_exe), '-c',
         'import numpy, scipy, librosa, soundfile, onnxruntime, pyopenjtalk, yaml, textgrid; '
         'assert "WebGpuExecutionProvider" in onnxruntime.get_available_providers()'],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        print('Base WebGPU Python runtime packages already installed.')
        return
    lock = ROOT / 'packaging/requirements-runtime.lock.txt'
    index_url = PYPI_MIRRORS[mirror]
    # Ensure legacy DirectML is absent before installing WebGPU.
    _run([str(py_exe), '-m', 'pip', 'uninstall', '-y', 'onnxruntime-directml'], env=env, check=False)
    # Windows embeddable Python ignores PYTHONPATH when python312._pth exists, which breaks
    # pip's isolated PEP-517 build env for pure-Python sdists (e.g. TextGrid, jyutping).
    # Install binary build backends first and pass --no-build-isolation.
    _run([
        str(py_exe), '-m', 'pip', 'install',
        '--no-warn-script-location', '--only-binary=:all:',
        '-i', index_url,
        'setuptools', 'wheel', 'hatchling', 'flit-core',
    ], env=env)
    # pyopenjtalk==0.4.1 only publishes an sdist on PyPI (no cp312 win_amd64 wheel);
    # pyopenjtalk-plus ships a prebuilt cp312-win_amd64 wheel providing `import pyopenjtalk`.
    reqs: list[str] = []
    for raw in lock.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.lower().startswith('pyopenjtalk=='):
            reqs.append('pyopenjtalk-plus==0.4.1.post10')
        elif line.lower().startswith(('pip==', 'setuptools==', 'wheel==')):
            continue
        else:
            reqs.append(line)
    _run([
        str(py_exe), '-m', 'pip', 'install',
        '--no-warn-script-location', '--no-build-isolation',
        '-i', index_url,
        *reqs,
    ], env=env)


def install_qt6_runtime(py_exe: Path, mirror: str) -> None:
    qt_dir = ROOT / 'runtime/qt6'
    qwindows = qt_dir / 'PySide6/plugins/platforms/qwindows.dll'
    receipt_path = qt_dir / 'WHEELS.json'
    if qwindows.is_file() and receipt_path.is_file():
        print('Qt 6.10.3 runtime already present:', qt_dir)
        return
    qt_dir.mkdir(parents=True, exist_ok=True)
    lock = ROOT / 'packaging/requirements-quick.lock.txt'
    index_url = PYPI_MIRRORS[mirror]
    with tempfile.TemporaryDirectory(prefix='vwa-qt6-wheels-') as tmp:
        wheel_dir = Path(tmp)
        _run([
            str(py_exe), '-m', 'pip', 'download',
            '--only-binary=:all:',
            '--platform', 'win_amd64',
            '--python-version', '3.12',
            '-i', index_url,
            '-r', str(lock),
            '-d', str(wheel_dir),
        ], env=_pip_env())
        wheels_meta = []
        for whl in sorted(wheel_dir.glob('*.whl')):
            with zipfile.ZipFile(whl) as zf:
                zf.extractall(qt_dir)
            wheels_meta.append({
                'filename': whl.name,
                'bytes': whl.stat().st_size,
                'sha256': _sha256(whl),
            })
        receipt_path.write_text(
            json.dumps({'version': QT6_VERSION, 'wheels': wheels_meta}, indent=2) + '\n',
            encoding='utf-8',
        )


def install_export_runtime(py_exe: Path, mirror: str) -> None:
    export_dir = ROOT / 'dependencies/export'
    if (export_dir / 'libresvip/__init__.py').is_file():
        print('Export runtime already present:', export_dir)
        return
    lock_path = ROOT / 'packaging/export-runtime.lock.json'
    if not lock_path.is_file():
        return
    spec = json.loads(lock_path.read_text(encoding='utf-8'))
    pkgs = [f"{item['name']}=={item['version']}" for item in spec.get('packages', ())]
    if not pkgs:
        return
    export_dir.mkdir(parents=True, exist_ok=True)
    index_url = PYPI_MIRRORS[mirror]
    env = _pip_env()
    _run([
        str(py_exe), '-m', 'pip', 'install',
        '--no-warn-script-location', '--only-binary=:all:',
        '-i', index_url,
        'hatchling', 'flit-core', 'poetry-core',
    ], env=env)
    _run([
        str(py_exe), '-m', 'pip', 'install',
        '--no-deps', '--upgrade', '--no-build-isolation',
        '--target', str(export_dir),
        '-i', index_url,
        *pkgs,
    ], env=env)


def install_ffmpeg() -> None:
    target = ROOT / 'dependencies/ffmpeg/ffmpeg.exe'
    if target.is_file():
        print('FFmpeg already present:', target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vwa-ffmpeg-') as tmp:
        archive = _download(FFMPEG_URLS, Path(tmp) / 'ffmpeg.zip')
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                base = Path(member).name.lower()
                if base in ('ffmpeg.exe', 'ffprobe.exe'):
                    out_path = target.parent / base
                    with zf.open(member) as src, out_path.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
    if not target.is_file():
        raise RuntimeError('Downloaded FFmpeg archive did not contain ffmpeg.exe')


UPSTREAM_JPN_RELEASE_URLS = (
    'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v2.1.0/VWA-Windows-x64-JPN.zip',
    'https://ghfast.top/https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v2.1.0/VWA-Windows-x64-JPN.zip',
)

_UPSTREAM_MODEL_PREFIXES = (
    'VWA/dependencies/vocal2midi/experiments/',
    'VWA/dependencies/vocal2midi/inference/qwen3asr_dml/bin/',
    'VWA/components/native-audio/',
)


class _HttpRangeFile(io.RawIOBase):
    """Minimal seekable HTTP Range reader for selective ZIP extraction."""

    def __init__(self, url: str):
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'VWA-WebGPU-Bootstrap/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.final_url = resp.geturl()
            self.size = int(resp.headers['Content-Length'])
        self.pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.size + offset
        self.pos = max(0, min(self.size, self.pos))
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if self.pos >= self.size:
            return b''
        end = self.size - 1 if n is None or n < 0 else min(self.size - 1, self.pos + n - 1)
        req = urllib.request.Request(
            self.final_url,
            headers={'Range': f'bytes={self.pos}-{end}', 'User-Agent': 'VWA-WebGPU-Bootstrap/1.0'},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        self.pos += len(data)
        return data


    def readinto(self, b) -> int:
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n


def install_upstream_models() -> None:
    """Selectively fetch Vocal2Midi ONNX weights, llama.cpp DLLs, and native audio DLLs via HTTP Range."""
    sentinel_paths = (
        ROOT / 'dependencies/vocal2midi/experiments/GAME-1.0.3-medium-onnx/encoder.onnx',
        ROOT / 'dependencies/vocal2midi/experiments/1218_hfa_model_new_dict/model.onnx',
        ROOT / 'dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx',
        ROOT / 'dependencies/vocal2midi/experiments/romajiASR/model.onnx',
        ROOT / 'dependencies/vocal2midi/inference/qwen3asr_dml/bin/llama.dll',
    )
    if all(p.is_file() for p in sentinel_paths):
        print('Upstream Vocal2Midi ONNX models & llama.cpp runtime already present.')
        return
    last_exc: Exception | None = None
    for url in UPSTREAM_JPN_RELEASE_URLS:
        print(f'Fetching Vocal2Midi ONNX models via HTTP Range <- {url}', flush=True)
        try:
            rf = io.BufferedReader(_HttpRangeFile(url), buffer_size=16 * 1024 * 1024)
            with zipfile.ZipFile(rf, 'r') as zf:
                targets = [
                    info for info in zf.infolist()
                    if not info.is_dir() and info.filename.startswith(_UPSTREAM_MODEL_PREFIXES)
                ]
                for idx, info in enumerate(targets, 1):
                    rel = info.filename[len('VWA/'):]
                    dest = ROOT / rel
                    if dest.is_file() and dest.stat().st_size == info.file_size:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_suffix(dest.suffix + '.partial')
                    with zf.open(info, 'r') as src, tmp.open('wb') as dst:
                        shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                    tmp.replace(dest)
                    if info.file_size >= 10 * 1024 * 1024 or idx == len(targets):
                        print(f'  [{idx}/{len(targets)}] {rel} ({info.file_size / 1048576:.1f} MiB)', flush=True)
            return
        except Exception as exc:
            last_exc = exc
            print(f'  mirror failed ({exc}); trying next...', flush=True)
    raise RuntimeError(f'Failed to fetch upstream Vocal2Midi models: {last_exc}')


def install_mdx_models() -> None:
    for rel, urls in MDX_ASSETS:
        dest = ROOT / rel
        if not dest.is_file():
            _download(urls, dest)
    # Mirror mdx-model-data.json into models/ as expected by config.example.json
    src_meta = ROOT / 'dependencies/models/mdx-model-data.json'
    dst_meta = ROOT / 'models/mdx-model-data.json'
    if src_meta.is_file() and not dst_meta.is_file():
        dst_meta.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_meta, dst_meta)


def ensure_config_and_source_layout() -> None:
    cfg_path = ROOT / 'config.json'
    example = ROOT / 'packaging/config.example.json'
    if not cfg_path.is_file():
        shutil.copyfile(example, cfg_path)
    else:
        data = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
        changed = False
        for key in ('separation_device', 'midi_device'):
            if data.get(key) in ('dml', 'directml', 'cuda'):
                data[key] = 'webgpu'
                changed = True
        if changed:
            cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Ensure baseline metadata files exist so startup layout checks stay clean
    v2_ver = ROOT / 'dependencies/vocal2midi2/backend-version.json'
    if not v2_ver.is_file():
        v2_ver.write_text(json.dumps({'backend': 'vocal2midi2', 'runtime': 'webgpu'}, indent=2) + '\n', encoding='utf-8')
    v1_ini = ROOT / 'dependencies/vocal2midi/settings/vocal2midi.ini'
    if not v1_ini.is_file():
        v1_ini.parent.mkdir(parents=True, exist_ok=True)
        v1_ini.write_text('[inference]\ndevice=webgpu\n', encoding='utf-8')

    prepare = ROOT / 'tools/Prepare-Source.ps1'
    _run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(prepare)])


def import_from_portable(portable_dir: Path, mirror: str) -> Path:
    portable_dir = portable_dir.resolve()
    if not (portable_dir / 'dependencies/vocal2midi/python/python.exe').is_file():
        raise FileNotFoundError(f'Not a valid VWA portable directory: {portable_dir}')
    for rel in _PORTABLE_COPY_PATHS:
        src = portable_dir / rel
        dst = ROOT / rel
        if not src.exists():
            continue
        print(f'Copying {rel} ...', flush=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    py_exe = ROOT / 'dependencies/vocal2midi/python/python.exe'
    index_url = PYPI_MIRRORS[mirror]
    _run([str(py_exe), '-m', 'pip', 'uninstall', '-y', 'onnxruntime-directml'], env=_pip_env(), check=False)
    _run([str(py_exe), '-m', 'pip', 'install', '--no-warn-script-location', '-i', index_url, ORT_WEBGPU_SPEC], env=_pip_env())
    return py_exe


def build_native_audio() -> None:
    if shutil.which('cmake') is None:
        print('CMake not found on PATH; skipping optional native audio DLL build.')
        return
    build_dir = ROOT / 'build/native'
    _run(['cmake', '-S', str(ROOT / 'src/native'), '-B', str(build_dir), '-DCMAKE_BUILD_TYPE=Release'])
    _run(['cmake', '--build', str(build_dir), '--parallel'])
    out_dir = ROOT / 'components/native-audio'
    out_dir.mkdir(parents=True, exist_ok=True)
    for dll in build_dir.rglob('vwa_*.dll'):
        shutil.copyfile(dll, out_dir / dll.name)


def verify_workspace(py_exe: Path) -> None:
    env = _pip_env()
    _run([str(py_exe), '-B', str(ROOT / 'tools/verify_source_layout.py')], env=env)
    _run([str(py_exe), '-B', str(ROOT / 'launch.py'), '--check'], env=env)
    env['VWA_TESTING'] = '1'
    env['BVWA_TESTING'] = '1'
    _run([str(py_exe), '-B', str(ROOT / 'tests/test_webgpu_backend.py')], env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-portable', type=Path, help='Import runtime/models/Qt6 from an existing VWA portable folder')
    parser.add_argument('--pypi-mirror', choices=tuple(PYPI_MIRRORS), default='tuna')
    parser.add_argument('--with-chinese', action='store_true', help='Also download Qwen3-ASR (~4.4 GiB) via feature_components')
    parser.add_argument('--with-native', action='store_true', help='Compile optional src/native audio DLLs via CMake')
    parser.add_argument('--skip-models', action='store_true', help='Skip downloading MDX models in standalone mode')
    parser.add_argument('--skip-verify', action='store_true', help='Skip launch.py --check and WebGPU probe')
    args = parser.parse_args()

    if args.from_portable:
        py_exe = import_from_portable(args.from_portable, args.pypi_mirror)
    else:
        py_exe = ensure_embedded_python(args.pypi_mirror)
        install_runtime_packages(py_exe, args.pypi_mirror)
        install_qt6_runtime(py_exe, args.pypi_mirror)
        install_export_runtime(py_exe, args.pypi_mirror)
        install_ffmpeg()
        if not args.skip_models:
            install_mdx_models()
            install_upstream_models()

    if args.with_native:
        build_native_audio()

    ensure_config_and_source_layout()

    if args.with_chinese:
        _run([str(py_exe), '-B', '-c',
              'import feature_components; feature_components.install("chinese", lambda d,t,m: print(f"{d}/{t} {m}", flush=True))'],
             env=_pip_env())

    if not args.skip_verify:
        verify_workspace(py_exe)
    print('\nWebGPU standalone workspace is ready. Run: dependencies\\vocal2midi\\python\\python.exe -B launch.py')


if __name__ == '__main__':
    main()
