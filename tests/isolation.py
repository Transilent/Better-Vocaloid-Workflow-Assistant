"""Fresh disposable application state for every regression subprocess.

Only program files and dependency configuration are copied. Immutable runtime
payloads use hard links; cache, jobs, settings and account profiles are excluded.
Tests must create dependency-installation fixtures in their own temporary trees.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile


SHARED_DIRECTORIES = ('dependencies', 'models', 'vendor', 'components')
PROGRAM_DIRECTORIES = ('src', 'tests', 'tools', 'assets', 'docs', 'packaging')
PROGRAM_FILES = {'VWA.exe', 'BVWA.exe', 'LICENSE', 'README.md', 'README.zh-CN.md', 'CHANGELOG.md',
                 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.zh-CN.md',
                 'source-files.json', 'portable-files.json', 'config.example.json',
                 'publish-selectors.json', 'version.json', 'requirements-runtime.lock.txt'}


def _native(path):
    value = str(Path(path).absolute())
    return '\\\\?\\' + value if os.name == 'nt' and not value.startswith('\\\\?\\') else value


def _resource_file(source, destination):
    # Settings and installed source must be independently writable. Model/DLL
    # payloads are read-only inputs under the regression-test contract.
    copied = {'.py', '.json', '.ini', '.cfg', '.yaml', '.yml', '.toml', '.txt',
              '.md', '.pth', '.bat', '.cmd', '.ps1'}
    if Path(source).suffix.casefold() in copied:
        return shutil.copy2(source, destination)
    try:
        os.link(source, destination)
    except PermissionError:
        # Managed Windows sessions may deny hard links within owned workspaces.
        # A private copy preserves isolation and the same read-only test inputs.
        return shutil.copy2(source, destination)
    return destination


@contextmanager
def application_copy(source, parent):
    """Yield an isolated APP root without changing dependency path validation."""
    source, parent = Path(source).resolve(), Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix='app-', dir=parent)).absolute()
    if sandbox.parent != parent or not sandbox.name.startswith('app-'):
        raise ValueError('Invalid test sandbox path')
    try:
        for path in source.iterdir():
            if path.is_file() and (path.suffix == '.py' or path.name in PROGRAM_FILES):
                shutil.copy2(path, sandbox / path.name)
        for name in PROGRAM_DIRECTORIES:
            if (source / name).is_dir():
                shutil.copytree(source / name, sandbox / name,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in SHARED_DIRECTORIES:
            target = source / name
            if target.is_dir():
                shutil.copytree(_native(target), _native(sandbox / name), copy_function=_resource_file,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        config = sandbox / 'packaging/config.example.json'
        if not config.is_file():
            config = sandbox / 'config.example.json'
        shutil.copy2(config, sandbox / 'config.json')
        (sandbox / 'work/temp').mkdir(parents=True)
        yield sandbox
    finally:
        if sandbox.resolve().parent != parent or sandbox.is_symlink() or sandbox.is_junction():
            raise RuntimeError('Test sandbox cleanup escaped its owned directory')
        shutil.rmtree(_native(sandbox))


def reset_state(sandbox):
    """Reset only private state in an already isolated program copy."""
    sandbox = Path(sandbox).resolve()
    for name in ('cache', 'jobs', 'publish-packages', 'work'):
        target = sandbox / name
        if not target.exists():
            continue
        if target.resolve().parent != sandbox or target.is_symlink() or target.is_junction():
            raise RuntimeError('Test state contains an escaping directory')
        shutil.rmtree(_native(target))
    for name in ('last_job.txt', 'publish-ui.json', 'launcher.log', 'diagnostics.log', '依赖检查报告.json'):
        (sandbox / name).unlink(missing_ok=True)
    shutil.copy2(sandbox / 'packaging/config.example.json', sandbox / 'config.json')
    (sandbox / 'work/temp').mkdir(parents=True)
    (sandbox / 'work/test-results').mkdir()
