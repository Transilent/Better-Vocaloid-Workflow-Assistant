"""Standalone update transaction, executed only after the desktop exits."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

ROOT_FILES = {'app.py', 'check_dependencies.py', 'common.py', 'components_ui.py',
              'workflow.py', 'workflow_ui.py', 'progress_state.py', 'error_recovery.py',
              'task_store.py', 'results_ui.py', 'storage.py', 'storage_ui.py', 'subtitles.py', 'subtitles_ui.py',
              'cover_art.py', 'desktop_theme.py', 'download_sources.py', 'launch.py',
              'mdx.py', 'midi_bridge.py', 'midi_checks.py', 'midi_merge.py',
              'optional_components.py', 'pipeline.py', 'publication.py', 'publish_browser.py',
              'publishing_layout.py', 'publishing_ui.py', 'roformer.py', 'updater.py',
              'updates_ui.py', 'v2m_runtime.py', 'workspace_ui.py', 'BVWA.exe',
              'publish-selectors.json', 'version.json', 'README.md', 'README.zh-CN.md',
              'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.zh-CN.md'}
TOOL_FILES = {'tools/update_apply.py', 'tools/Start-Diagnostics.bat',
              'tools/Check-Dependencies.bat', 'tools/Use-CPU.bat', 'tools/Use-GPU.bat',
              'tools/Install-Runtime.ps1', 'tools/set_device.py'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def validate_file(entry):
    name = entry['file']
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('更新文件路径无效。')
    allowed_asset = len(path.parts) == 2 and path.parts[0] == 'assets' and path.suffix in ('.svg', '.ico', '.png')
    if name not in ROOT_FILES | TOOL_FILES and not allowed_asset:
        raise ValueError('更新包试图修改程序文件以外的内容：' + name)
    if not isinstance(entry.get('bytes'), int) or not 0 <= entry['bytes'] <= 32 * 1024**2:
        raise ValueError('更新文件大小无效。')
    if not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')):
        raise ValueError('更新文件校验信息无效。')


def validate_destination(root, name):
    root = Path(root).resolve()
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError('更新目标位于助手目录之外。')
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
                raise ValueError('更新目标包含链接或重解析点。')
    return path


def apply_update(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    root = Path(plan['root']).resolve()
    if not plan_path.is_relative_to(root / 'cache/updates') or plan_path.name != 'plan.json':
        raise ValueError('更新计划不属于此助手目录。')
    if sha(root / 'dependencies/manifest.json') != plan['runtime_sha256']:
        raise ValueError('基础运行库版本已改变，请重新下载更新。')
    stage, entries = plan_path.parent, plan['entries']
    if len(entries) != len({e['file'] for e in entries}):
        raise ValueError('更新计划包含重复文件。')
    for entry in entries:
        validate_file(entry)
        validate_destination(root, entry['file'])
        source = validate_destination(stage / 'files', entry['file'])
        if source.stat().st_size != entry['bytes'] or sha(source) != entry['sha256']:
            raise ValueError('待安装的更新文件已改变：' + entry['file'])
    backup = stage / 'backup'
    backup.mkdir(exist_ok=False)
    touched = []
    try:
        for entry in entries:
            destination = validate_destination(root, entry['file'])
            saved = backup / entry['file']
            existed = destination.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            touched.append((destination, saved, existed))
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.bvwa-update')
            validate_destination(root, temporary.relative_to(root).as_posix())
            if temporary.exists():
                raise ValueError('发现未完成的更新临时文件，请先检查更新日志。')
            try:
                shutil.copy2(stage / 'files' / entry['file'], temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        (root / 'cache/updates/pending.json').unlink(missing_ok=True)
        return {'state': 'passed', 'version': plan['version'], 'files': len(entries)}
    except BaseException:
        for destination, saved, existed in reversed(touched):
            if existed:
                shutil.copy2(saved, destination)
            else:
                destination.unlink(missing_ok=True)
        (root / 'cache/updates/pending.json').unlink(missing_ok=True)
        raise


def wait_for_exit(pid, timeout=90):
    if not pid:
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return
    try:
        if kernel.WaitForSingleObject(handle, timeout * 1000) != 0:
            raise TimeoutError('助手未能退出，更新尚未安装。')
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--parent', type=int, default=0)
    parser.add_argument('--launcher-parent', type=int, default=0)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    root = Path(plan['root']).resolve()
    try:
        wait_for_exit(args.parent)
        wait_for_exit(args.launcher_parent)
        result = apply_update(args.plan)
    except Exception as exc:
        result = {'state': 'failed', 'error': str(exc), 'version': plan['version']}
        (root / 'cache/updates/pending.json').unlink(missing_ok=True)
    (root / 'cache/updates/last-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    launcher = root / 'BVWA.exe'
    if launcher.is_file():
        subprocess.Popen([str(launcher)], cwd=root, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0 if result['state'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
