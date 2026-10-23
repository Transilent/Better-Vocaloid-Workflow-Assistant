"""Small, isolated WORLD analyzer; no change to the shared Python environment."""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, atomic_json, file_hash

SPEC = {
    'name': 'pyworld-0.3.5-cp312-cp312-win_amd64.whl',
    'url': 'https://files.pythonhosted.org/packages/b6/29/9c9c0dad7575faea3dbc3cbc8b69e0365b35912332068afd79a2b93d822d/pyworld-0.3.5-cp312-cp312-win_amd64.whl',
    'sha256': '59b48961c2ac34fb01efeb1a77d3eda69c41b676858cbc3a82dfb7602c0c762b',
    'bytes': 222700,
}
COMPONENT = ROOT / 'dependencies/optional/vocal-expression'


def _inspect(directory):
    directory = Path(directory)
    receipt = json.loads((directory / 'installed.json').read_text(encoding='utf-8'))
    files = receipt['files']
    if receipt['wheel_sha256'] != SPEC['sha256'] or not files:
        raise ValueError('Component version mismatch')
    resolved_directory = directory.resolve()
    for row in files:
        path = directory / row['file']
        if (not path.resolve().is_relative_to(resolved_directory)
                or not path.is_file()
                or path.stat().st_size != row['bytes']
                or file_hash(path) != row['sha256']):
            raise ValueError('Component checksum mismatch')
    return {'ready': True, 'bytes': sum(row['bytes'] for row in files), 'version': '0.3.5'}


def status(root=COMPONENT):
    try:
        return _inspect(Path(root) / 'current')
    except (OSError, ValueError, KeyError, TypeError):
        return {'ready': False, 'bytes': 0, 'version': '0.3.5'}


def _tree_ready(directory):
    try:
        return _inspect(directory)['ready']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify(root=COMPONENT):
    """Return receipt validation for callers that need an explicit check."""
    return _tree_ready(Path(root) / 'current')


def load():
    if not status(COMPONENT)['ready']:
        raise FileNotFoundError('参考音色组件尚未安装、文件不完整或校验失败，请在“模型与组件”下载／重新安装 WORLD 分析器。')
    packages = str(COMPONENT / 'current/packages')
    if packages not in sys.path:
        sys.path.insert(0, packages)
    directory = COMPONENT / 'current'
    _inspect(directory)
    return importlib.import_module('pyworld')


def install(progress=lambda *args: None, check=None, source='auto', root=COMPONENT):
    from optional_components import (download, extract_wheel, remove_owned_tree, cancelled,
                                     recover_verified_previous, replace_verified_tree)
    from pipeline import job_lock
    if sys.version_info[:2] != (3, 12) or sys.platform != 'win32' or sys.maxsize < 2**32:
        raise RuntimeError('WORLD 组件需要助手自带的 Windows x64 Python 3.12。')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with job_lock(root):
        recover_verified_previous(root, _tree_ready)
        installed = status(root)
        if installed['ready']:
            progress(1, 1, '参考音色组件已就绪')
            return installed
        if shutil.disk_usage(root).free < 16 * 1024**2:
            raise OSError('安装 WORLD 组件需要至少 16 MiB 磁盘空间。')
        stage, destination, backup = root / 'staging', root / 'current', root / 'previous'
        remove_owned_tree(stage, root)
        archive = download(SPEC, root / 'downloads', progress, check, source=source)
        try:
            extract_wheel(archive, stage / 'packages', check)
            shutil.copy2(ROOT / 'components/world-LICENSE.txt', stage / 'WORLD-LICENSE.txt')
            cancelled(check)
            # Test the native ABI in a child; no loaded DLL locks the install folder.
            code = "import sys;sys.path.insert(0,sys.argv[1]);import numpy as n,pyworld as p;x=n.sin(n.arange(4800)*2*n.pi*220/24000);f,t=p.dio(x,24000);assert n.isfinite(p.d4c(x,f,t,24000)).all()"
            probe = subprocess.run([sys.executable, '-B', '-c', code, str(stage / 'packages')],
                                   capture_output=True, timeout=30,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if probe.returncode:
                raise RuntimeError('WORLD 组件运行验证失败：' + probe.stderr.decode('utf-8', errors='replace')[-1000:])
            files = [{'file': path.relative_to(stage).as_posix(), 'bytes': path.stat().st_size, 'sha256': file_hash(path)}
                     for path in sorted(stage.rglob('*')) if path.is_file() and '__pycache__' not in path.parts]
            atomic_json(stage / 'installed.json', {'wheel_sha256': SPEC['sha256'], 'files': files})
            cancelled(check)
            replace_verified_tree(stage, destination, backup, root, _tree_ready)
            # The verified installed payload is sufficient; do not retain a second copy.
            archive.unlink(missing_ok=True)
            progress(1, 1, '参考音色组件已就绪')
            return status(root)
        finally:
            remove_owned_tree(stage, root)


if __name__ == '__main__':
    print(json.dumps(install(lambda done, total, message: print(message, flush=True)), ensure_ascii=False))
