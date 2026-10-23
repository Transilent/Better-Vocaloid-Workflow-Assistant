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
import tempfile
import urllib.request
from urllib.parse import quote
import zipfile

ROOT_FILES = {'process_control.py', 'feature_components.py', 'dependency_profiles.py', 'runtime_devices.py', 'artifact_cache.py', 'workflow_widgets.py', 'LICENSE', 'lyrics_dialog.py', 'lyrics_sources.py', 'parallel_download.py', 'processing_ui.py', 'processing_workflow.py', 'project_exports.py', 'app.py', 'check_dependencies.py', 'common.py', 'components_ui.py',
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
RUNTIME_UPDATE_NAME = 'BVWA-Runtime-Upgrade.zip'
MAX_RUNTIME_BYTES = 256 * 1024**2
RUNTIME_PROGRAM_FILES = {'components/roformer.json', 'components/runtime-features.json', 'models/来源说明.md'}


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
    if plan.get('kind') == 'runtime_upgrade':
        return apply_runtime_upgrade(plan_path)
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


def _runtime_path(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or
            not path.parts or path.parts[0] not in ('dependencies', 'models', 'vendor')):
        raise ValueError('运行库升级路径无效：' + name)
    if name.startswith('dependencies/optional/'):
        raise ValueError('基础运行库升级不能修改可选模型。')


def _manifest_entries(path):
    data = json.loads(path.read_text(encoding='utf-8'))['entries']
    entries = {entry['file']: entry for entry in data}
    if len(entries) != len(data):
        raise ValueError('运行库清单包含重复文件。')
    for name, entry in entries.items():
        _runtime_path(name)
        if not isinstance(entry['bytes'], int) or entry['bytes'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']):
            raise ValueError('运行库清单校验信息无效。')
    return entries


class _RuntimeDestinations:
    """Verify shared parent directories once per inactive update transaction."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.parents = set()

    def checked(self, name):
        parts = PurePosixPath(name)
        if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise ValueError('运行库升级路径无效。')
        path = self.root / name
        if path.parent not in self.parents:
            validate_destination(self.root, path.parent.relative_to(self.root).as_posix())
            if path.parent.is_dir():
                self.parents.add(path.parent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('运行库升级目标包含链接或重解析点。')
        return path


def _validate_runtime_stage(root, stage, plan):
    if sha(root / 'dependencies/manifest.json') != plan['source_runtime_sha256']:
        raise ValueError('当前运行库与升级包不匹配，请使用完整便携包。')
    target_path = stage / 'files/dependencies/manifest.json'
    if sha(target_path) != plan['target_runtime_sha256']:
        raise ValueError('目标运行库清单校验失败。')
    base = _manifest_entries(root / 'dependencies/manifest.json')
    target = _manifest_entries(target_path)
    root_paths, stage_paths = _RuntimeDestinations(root), _RuntimeDestinations(stage/'files')
    changed = {name for name, entry in target.items() if base.get(name) != entry}
    for name in set(target) - changed:
        path = root / name
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = None
        if size != target[name]['bytes']:
            raise ValueError('原有运行库文件缺失或大小不符，请使用完整便携包：' + name)
    records = {entry['file']: entry for entry in plan['entries']}
    if len(records) != len(plan['entries']) or not changed.issubset(records):
        raise ValueError('运行库升级清单不完整或包含重复文件。')
    if not {'app.py', 'version.json', 'dependencies/manifest.json'}.issubset(records):
        raise ValueError('运行库升级缺少必要程序文件。')
    for name, entry in records.items():
        if name in target:
            if entry != target[name] or name not in changed:
                raise ValueError('依赖升级与目标清单不一致：' + name)
        elif name == 'dependencies/manifest.json':
            if entry['sha256'] != plan['target_runtime_sha256']:
                raise ValueError('目标清单摘要不匹配。')
        elif name not in RUNTIME_PROGRAM_FILES:
            validate_file(entry)
        root_paths.checked(name)
        source = stage_paths.checked(name)
        if source.stat().st_size != entry['bytes'] or sha(source) != entry['sha256']:
            raise ValueError('升级文件校验失败：' + name)
    removals = {entry['file']: entry for entry in plan['removed']}
    if len(removals) != len(plan['removed']) or set(removals) != set(base) - set(target):
        raise ValueError('待清理的运行库文件与原清单不一致。')
    for name, entry in removals.items():
        if entry != base[name]:
            raise ValueError('待清理文件的原始校验信息不一致。')
        root_paths.checked(name)
    version = json.loads((stage / 'files/version.json').read_text(encoding='utf-8'))
    if version['version'] != plan['version'] or version.get('runtime_upgrade'):
        raise ValueError('目标程序版本信息无效。')
    return records, removals


def _runtime_download(item, folder, progress, check, source):
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / RUNTIME_UPDATE_NAME
    pending = archive.with_suffix('.zip.partial')
    if archive.is_file() and archive.stat().st_size == item['bytes'] and sha(archive) == item['sha256']:
        return archive
    base_url = 'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v'
    url = base_url + quote(item['version'], safe='') + '/' + RUNTIME_UPDATE_NAME
    routes = [url] if source == 'official' else ['https://api.gitproxy.dev/' + url.removeprefix('https://')] if source == 'mirror' else [url, 'https://api.gitproxy.dev/' + url.removeprefix('https://')]
    last_error = None
    for route in routes:
        try:
            if check and check():
                raise InterruptedError('升级下载已取消，下次启动可继续。')
            offset = pending.stat().st_size if pending.is_file() else 0
            if offset >= item['bytes']:
                if offset == item['bytes'] and sha(pending) == item['sha256']:
                    pending.replace(archive)
                    return archive
                pending.unlink(); offset = 0
            headers = {'User-Agent': 'BVWA-Runtime-Upgrade', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(urllib.request.Request(route, headers=headers), timeout=20) as response:
                if response.status == 206:
                    expected = f'bytes {offset}-{item["bytes"]-1}/{item["bytes"]}'
                    if response.headers.get('Content-Range') != expected:
                        raise ValueError('下载服务器返回了错误的续传范围。')
                else:
                    offset = 0
                with pending.open('ab' if offset else 'wb') as stream:
                    while True:
                        if check and check():
                            raise InterruptedError('升级下载已取消，下次启动可继续。')
                        block = response.read(1024**2)
                        if not block:
                            break
                        offset += len(block)
                        if offset > item['bytes']:
                            raise ValueError('升级文件大小超过发布清单。')
                        stream.write(block)
                        progress(offset, item['bytes'], '正在下载新增依赖与程序文件…')
            if offset != item['bytes'] or sha(pending) != item['sha256']:
                pending.unlink(missing_ok=True)
                raise ValueError('升级文件校验失败，请重试下载。')
            pending.replace(archive)
            return archive
        except InterruptedError:
            raise
        except (OSError, ValueError) as exc:
            last_error = exc
    raise OSError('升级下载未完成，请检查网络或切换下载源后重试。') from last_error


def prepare_runtime_upgrade(item, root, progress=lambda *args: None, check=None, source='official', downloader=None):
    root = Path(root).resolve()
    if (item.get('name') != RUNTIME_UPDATE_NAME or not re.fullmatch(r'\d+\.\d+\.\d+', item.get('version', '')) or
            not isinstance(item.get('bytes'), int) or not 0 < item['bytes'] <= MAX_RUNTIME_BYTES or
            any(not re.fullmatch(r'[a-f0-9]{64}', item.get(key, '')) for key in
                ('sha256', 'source_runtime_sha256', 'target_runtime_sha256'))):
        raise ValueError('运行库升级信息无效，请使用完整便携包。')
    if sha(root / 'dependencies/manifest.json') != item['source_runtime_sha256']:
        raise ValueError('当前运行库与升级包不匹配，请使用完整便携包。')
    cache = validate_destination(root, 'cache/updates')
    cache.mkdir(parents=True, exist_ok=True)
    folder = validate_destination(root, 'cache/updates/runtime-downloads')
    for name in (RUNTIME_UPDATE_NAME, RUNTIME_UPDATE_NAME + '.partial'):
        validate_destination(root, 'cache/updates/runtime-downloads/' + name)
    archive = (downloader or _runtime_download)(item, folder, progress, check, source)
    if archive.stat().st_size != item['bytes'] or sha(archive) != item['sha256']:
        raise ValueError('运行库升级包校验失败。')
    stage = Path(tempfile.mkdtemp(prefix='runtime-stage-', dir=cache))
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            if len(infos) > 20000 or sum(info.file_size for info in infos) > MAX_RUNTIME_BYTES:
                raise ValueError('运行库升级包内容超过限制。')
            if len({info.filename for info in infos}) != len(infos):
                raise ValueError('运行库升级包含重复文件。')
            plan = json.loads(zipped.read('runtime-upgrade.json'))
            if any(plan.get(key) != item[key] for key in ('version', 'source_runtime_sha256', 'target_runtime_sha256')):
                raise ValueError('运行库升级包与发布信息不一致。')
            if {info.filename for info in infos} != {'runtime-upgrade.json'} | {entry['file'] for entry in plan['entries']}:
                raise ValueError('运行库升级包与文件清单不一致。')
            needed = sum(info.file_size for info in infos) * 2 + 64 * 1024**2
            if shutil.disk_usage(root).free < needed:
                raise OSError('升级临时空间不足，请清理磁盘后重试。')
            stage_paths = _RuntimeDestinations(stage/'files')
            for index, entry in enumerate(plan['entries']):
                if check and check():
                    raise InterruptedError('升级已取消，下次启动可继续。')
                info = zipped.getinfo(entry['file'])
                if info.file_size != entry['bytes'] or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('升级文件大小或类型无效。')
                destination = stage_paths.checked(entry['file'])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(info) as src, destination.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if index % 128 == 0:
                    progress(item['bytes'], item['bytes'], f'正在展开并校验升级文件 {index + 1}/{len(plan["entries"])}…')
            progress(item['bytes'], item['bytes'], '正在核对原有运行库与新增依赖…')
            _validate_runtime_stage(root, stage, plan)
        plan.update(root=str(root), kind='runtime_upgrade', state='ready')
        plan_path = stage / 'plan.json'
        plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        shutil.copy2(Path(__file__), stage / 'apply_helper.py')
        progress(item['bytes'], item['bytes'], '校验完成，正在重启并安装 ' + item['version'] + '…')
        return plan_path
    except BaseException:
        if stage.resolve().is_relative_to(cache.resolve()) and stage.name.startswith('runtime-stage-'):
            shutil.rmtree(stage)
        raise


def apply_runtime_upgrade(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    root = Path(plan['root']).resolve()
    if not plan_path.is_relative_to(root / 'cache/updates') or plan_path.name != 'plan.json':
        raise ValueError('运行库升级计划不属于此助手目录。')
    stage = plan_path.parent
    records, removals = _validate_runtime_stage(root, stage, plan)
    backup = stage / 'backup'
    backup.mkdir(exist_ok=False)
    touched = []
    skipped = []
    root_paths = _RuntimeDestinations(root)
    try:
        # Commit version and manifest only after all program/runtime files.
        names = sorted(records, key=lambda name: (name in ('version.json', 'dependencies/manifest.json'), name))
        for name in names + sorted(removals):
            destination = root_paths.checked(name)
            if name in removals and (not destination.is_file() or sha(destination) != removals[name]['sha256']):
                skipped.append(name)
                continue
            saved = backup / name
            existed = destination.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            touched.append((destination, saved, existed))
            if name in removals:
                destination.unlink()
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            pending = destination.with_name(destination.name + '.bvwa-update')
            root_paths.checked(pending.relative_to(root).as_posix())
            if pending.exists():
                raise ValueError('发现未完成的升级临时文件，请检查更新日志。')
            try:
                shutil.copy2(stage / 'files' / name, pending)
                pending.replace(destination)
            finally:
                pending.unlink(missing_ok=True)
        return {'state': 'passed', 'version': plan['version'], 'files': len(records), 'removed': len(removals)-len(skipped),
                'kept_modified_runtime_files': skipped, 'runtime_upgraded': True}
    except BaseException:
        for destination, saved, existed in reversed(touched):
            if existed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved, destination)
            else:
                destination.unlink(missing_ok=True)
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
