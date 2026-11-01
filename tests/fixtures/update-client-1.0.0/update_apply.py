"""Standalone update transaction, executed only after the desktop exits."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from urllib.parse import quote, urlsplit
import zipfile

ROOT_FILES = {'style_presets.py', 'context_project.py', 'context_reading.py', 'context_adapter.py', 'context_alignment.py', 'context_structure.py', 'gesture_catalog.py', 'context_semantics.py', 'context_planner.py', 'context_evidence.py', 'context_plan.py', 'context_workflow.py', 'process_control.py', 'feature_components.py', 'dependency_profiles.py', 'runtime_devices.py', 'artifact_cache.py', 'workflow_widgets.py', 'LICENSE', 'lyrics_dialog.py', 'lyrics_sources.py', 'parallel_download.py', 'processing_ui.py', 'processing_workflow.py', 'project_exports.py', 'app.py', 'check_dependencies.py', 'common.py', 'components_ui.py',
              'components/world-LICENSE.txt', 'bilibili_session.py', 'video_mixdown.py', 'video_mixdown_ui.py', 'expression_calibration.py', 'calibration_refinement.py', 'calibration_workflow.py', 'calibration_session.py', 'calibration_ui.py',
              'workflow.py', 'workflow_ui.py', 'progress_state.py', 'error_recovery.py',
              'task_store.py', 'task_contract.py', 'task_contract_ui.py', 'results_ui.py', 'storage.py', 'storage_ui.py', 'subtitles.py', 'subtitles_ui.py',
              'cover_art.py', 'desktop_theme.py', 'download_sources.py', 'launch.py',
              'mdx.py', 'midi_bridge.py', 'midi_checks.py', 'midi_merge.py', 'music_timing.py',
              'optional_components.py', 'pipeline.py', 'publication.py', 'publish_browser.py',
              'publishing_layout.py', 'publishing_ui.py', 'roformer.py', 'vocal_partition.py', 'updater.py',
              'updates_ui.py', 'v2m_runtime.py', 'workspace_ui.py', 'vocal_expression.py', 'vocal_timbre.py', 'expression_component.py', 'pitch_accuracy.py', 'transcription_quality.py', 'BVWA.exe',
              'publish-selectors.json', 'version.json', 'README.md', 'README.zh-CN.md',
              'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.zh-CN.md'}
ROOT_FILES.update({'style_pitch.py','ui_input.py'})
ROOT_FILES.update({'tuning_engine.py','tuning_workflow.py','tuning_ui.py'})
# VWA presentation modules; internal update/lock identifiers remain compatible.
ROOT_FILES.update({'media_preview.py', 'ui_preferences.py', 'ui_components.py', 'ui_materials.py'})
ROOT_FILES.update({'i18n.py', 'strength_control.py', 'assets/i18n/en.json', 'assets/i18n/ja.json', 'assets/i18n/additions.json'})
ROOT_FILES.update({'docs/InterfaceLanguages.en.md', 'docs/InterfaceLanguages.zh-CN.md',
                   'docs/DownloadSources.en.md', 'docs/DownloadSources.zh-CN.md'})
TOOL_FILES = {'tools/update_apply.py', 'tools/Start-Diagnostics.bat',
              'tools/Check-Dependencies.bat', 'tools/Use-CPU.bat', 'tools/Use-GPU.bat',
              'tools/Install-Runtime.ps1', 'tools/set_device.py'}
RUNTIME_UPDATE_NAME = 'VWA-Runtime-Upgrade.zip'
MAX_RUNTIME_BYTES = 256 * 1024**2
MAX_PROGRAM_BYTES = 64 * 1024**2
RUNTIME_PROGRAM_FILES = {'components/roformer.json', 'components/runtime-features.json', 'models/来源说明.md'}
ROOT_FILES.add('VWA.exe')
LAUNCHER_MIGRATION = {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True}
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_NORMAL = 0x80


def launcher_parent_id():
    return os.environ.get('VWA_LAUNCHER_PID', os.environ.get('BVWA_LAUNCHER_PID', '0'))


class _ApplicationLock:
    def __init__(self, locks):
        self._locks = locks

    def unlock(self):
        for lock in reversed(self._locks): lock.unlock()

    def isLocked(self):
        return all(lock.isLocked() for lock in self._locks)

    def __getattr__(self, key):
        return getattr(self._locks[-1], key)

    def __del__(self):
        try:
            self.unlock()
        except RuntimeError:
            pass


def application_lock(root):
    """Interlock canonical and released launchers without moving a live lock."""
    from PyQt5.QtCore import QLockFile
    folder = Path(root) / 'cache'
    folder.mkdir(parents=True, exist_ok=True)
    held = []
    for name in ('BVWA.lock', 'VWA.lock'):
        lock = QLockFile(str(folder / name))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            for previous in reversed(held): previous.unlock()
            return None
        held.append(lock)
    return _ApplicationLock(held)


def _file_attributes(path):
    if os.name != 'nt': return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFileAttributesW.argtypes = (ctypes.c_wchar_p,)
    value = kernel.GetFileAttributesW(_native(path))
    if value == -1: raise ctypes.WinError(ctypes.get_last_error())
    return value


def _set_file_attributes(path, attributes):
    if os.name != 'nt' or attributes is None: return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetFileAttributesW.argtypes = (ctypes.c_wchar_p, ctypes.c_ulong)
    if not kernel.SetFileAttributesW(_native(path), attributes):
        raise ctypes.WinError(ctypes.get_last_error())


def _native(path):
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'): return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def sha(path):
    h = hashlib.sha256()
    with open(_native(path), 'rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def _version_key(value):
    if not isinstance(value, str):
        raise ValueError('发布版本号无效。')
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?', value)
    if not match:
        raise ValueError('发布版本号无效。')
    core = tuple(int(number) for number in match.groups()[:3])
    suffix = match[4]
    return core + (1 if suffix is None else 0,
                   tuple((0, int(part)) if part.isdigit() else (1, part)
                         for part in (suffix or '').split('.')))


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


def _plan_root(plan_path):
    """Derive the installation from its staging location, never from JSON."""
    plan_path = Path(plan_path).absolute()
    stage, updates = plan_path.parent, plan_path.parent.parent
    if (plan_path.name != 'plan.json' or updates.name != 'updates' or updates.parent.name != 'cache'
            or not stage.name.startswith(('stage-', 'runtime-stage-'))):
        raise ValueError('更新计划不属于助手的暂存目录。')
    root = updates.parent.parent.resolve()
    validate_destination(root, plan_path.relative_to(root).as_posix())
    return root


def _read_plan(plan_path):
    root = _plan_root(plan_path)
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    if (not isinstance(plan, dict) or not isinstance(plan.get('root'), str)
            or not Path(plan['root']).is_absolute() or Path(plan['root']).resolve() != root):
        raise ValueError('更新计划中的助手目录不匹配。')
    if not isinstance(plan.get('version'), str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', plan['version']):
        raise ValueError('更新计划中的版本号无效。')
    return root, plan


def _clear_pending(root, plan_path):
    path = validate_destination(root, 'cache/updates/pending.json')
    try:
        receipt = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return
    except ValueError:
        receipt = None
    # A later download may have prepared a different plan. Preserve it.
    if (not isinstance(receipt, dict) or not isinstance(receipt.get('plan'), str)
            or Path(receipt['plan']).resolve() == Path(plan_path).resolve()):
        path.unlink(missing_ok=True)


def _write_result(root, result):
    path = validate_destination(root, 'cache/updates/last-result.json')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='last-result-', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(result, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _installed_metadata(root):
    data = json.loads((Path(root) / 'version.json').read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('当前程序版本信息无效。')
    _version_key(data.get('version'))
    return data


def _is_bootstrap_runtime_migration(plan, installed):
    marker = installed.get('runtime_upgrade')
    return (plan.get('kind') == 'runtime_upgrade'
            and installed.get('upgrade_pending') is True
            and isinstance(marker, dict)
            and marker.get('version') == plan.get('version')
            and marker.get('source_runtime_sha256') == plan.get('source_runtime_sha256')
            and marker.get('target_runtime_sha256') == plan.get('target_runtime_sha256'))


def _reject_target_version(root, plan_path, plan, installed, message):
    _clear_pending(root, plan_path)
    try:
        _write_result(root, {'state': 'failed', 'reason': 'target_not_newer',
                             'version': plan.get('version'), 'installed_version': installed,
                             'error': message})
    except OSError:
        pass
    raise ValueError(message)


def _require_forward_target(root, plan, plan_path):
    try:
        metadata = _installed_metadata(root)
        installed = metadata['version']
        target = plan['version']
        target_key, installed_key = _version_key(target), _version_key(installed)
    except (OSError, KeyError, TypeError, ValueError):
        _reject_target_version(root, plan_path, plan, None,
                               '无法确认当前安装版本，已拒绝更新；暂存文件已保留，请重新检查更新或使用完整便携包恢复。')
    if target_key > installed_key:
        return
    if target_key == installed_key and _is_bootstrap_runtime_migration(plan, metadata):
        # The verified first hop installs the new helper at the target version
        # before completing the runtime half of the migration.
        return
    _reject_target_version(
        root, plan_path, plan, installed,
        f'更新目标 {target} 不高于当前安装版本 {installed}，已拒绝回放；'
        '暂存文件已保留，请重新检查更新或使用完整便携包恢复。')


def apply_update(plan_path):
    plan_path = Path(plan_path).resolve()
    root, plan = _read_plan(plan_path)
    if plan.get('kind') == 'runtime_upgrade':
        return apply_runtime_upgrade(plan_path)
    _require_forward_target(root, plan, plan_path)
    if reason := installation_busy(root):
        raise RuntimeError(reason)
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
    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=_native(stage)))
    touched = []
    try:
        for entry in sorted(entries, key=lambda item: (item['file'] == 'version.json', item['file'])):
            destination = validate_destination(root, entry['file'])
            saved = backup / entry['file']
            existed = destination.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            touched.append((destination, saved, existed))
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.vwa-update')
            validate_destination(root, temporary.relative_to(root).as_posix())
            if temporary.exists() or destination.with_name(destination.name + '.bvwa-update').exists():
                raise ValueError('发现未完成的更新临时文件，请先检查更新日志。')
            try:
                shutil.copy2(stage / 'files' / entry['file'], temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        _clear_pending(root, plan_path)
        return {'state': 'passed', 'version': plan['version'], 'files': len(entries)}
    except BaseException:
        for destination, saved, existed in reversed(touched):
            if existed:
                shutil.copy2(saved, destination)
            else:
                destination.unlink(missing_ok=True)
        _clear_pending(root, plan_path)
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


def _validate_runtime_stage(root, stage, plan, check=None):
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
        if check and check():
            raise InterruptedError('升级已取消，下次启动可继续。')
        try:
            path = root_paths.checked(name)
        except ValueError as error:
            raise ValueError(f'{error} 请使用完整便携包恢复：{name}') from error
        try:
            size = path.stat().st_size
            digest = sha(path) if size == target[name]['bytes'] else None
        except OSError as error:
            raise ValueError('原有运行库文件缺失、无法读取或校验不符，请使用完整便携包恢复：' + name) from error
        if size != target[name]['bytes'] or digest != target[name]['sha256']:
            raise ValueError('原有运行库文件缺失或校验不符，请使用完整便携包恢复：' + name)
    records = {entry['file']: entry for entry in plan['entries']}
    if len(records) != len(plan['entries']) or not changed.issubset(records):
        raise ValueError('运行库升级清单不完整或包含重复文件。')
    migration = plan.get('launcher_migration')
    if ('VWA.exe' in records) != (migration is not None):
        raise ValueError('启动入口迁移声明必须与 VWA.exe 载荷一致。')
    if migration is not None and (
            not isinstance(migration, dict) or migration != LAUNCHER_MIGRATION
            or type(migration.get('legacy_alias')) is not bool
            or 'BVWA.exe' in records):
        raise ValueError('启动入口迁移信息无效。')
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


def _runtime_name(item):
    name = item.get('name', '')
    if not isinstance(name, str) or not re.fullmatch(r'(?:VWA|BVWA)-Runtime-Upgrade(?:-v\d+\.\d+\.\d+)?\.zip', name):
        raise ValueError('运行库升级包名称无效。')
    return name


def program_name(item):
    name = item.get('name', '')
    if (not isinstance(name, str) or len(name) > 100 or not re.fullmatch(
            r'(?:VWA|BVWA)-(?:App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip', name)):
        raise ValueError('程序更新包名称无效。')
    return name


def _asset_record(item, *, runtime=False):
    if not isinstance(item, dict):
        raise ValueError('更新文件信息无效。')
    (_runtime_name if runtime else program_name)(item)
    limit = MAX_RUNTIME_BYTES if runtime else MAX_PROGRAM_BYTES
    if (type(item.get('bytes')) is not int or not 0 < item['bytes'] <= limit
            or not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])):
        raise ValueError('更新文件大小或哈希无效。')


def select_update_route(inventory, runtime_sha):
    """Select by manifest identity; never guess a runtime from a version label."""
    if not isinstance(inventory, dict):
        raise ValueError('发布清单格式无效。')
    routes = inventory.get('updates')
    if routes is None:
        # Released clients expose two slots. Their original archives are still
        # usable by the repair tool, which reads the embedded migration item.
        for key in ('program_update', 'update'):
            item = inventory.get(key)
            if isinstance(item, dict) and item.get('runtime_sha256') == runtime_sha:
                _asset_record(item)
                return {'runtime_sha256': runtime_sha, 'update': dict(item)}
        return None
    if inventory.get('update_format', 2) != 2 or not isinstance(routes, list) or not 1 <= len(routes) <= 32:
        raise ValueError('多运行库更新清单格式无效。')
    seen, chosen = set(), None
    version = inventory.get('version')
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError('更新分流记录无效。')
        base = route.get('runtime_sha256')
        if not isinstance(base, str) or not re.fullmatch(r'[a-f0-9]{64}', base) or base in seen:
            raise ValueError('更新分流包含无效或重复的运行库。')
        seen.add(base)
        bootstrap, runtime = route.get('update'), route.get('runtime_upgrade')
        _asset_record(bootstrap)
        _asset_record(runtime, runtime=True)
        if (bootstrap.get('runtime_sha256') != base or runtime.get('source_runtime_sha256') != base
                or runtime.get('version') != version
                or not isinstance(runtime.get('target_runtime_sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', runtime['target_runtime_sha256'])
                or inventory.get('target_runtime_sha256', runtime['target_runtime_sha256']) != runtime['target_runtime_sha256']):
            raise ValueError('更新分流与程序版本或运行库不一致。')
        if base == runtime_sha:
            chosen = route
    return dict(chosen) if chosen is not None else None


DOWNLOAD_SOURCE_OPTIONS = (
    ('自动选择', 'auto'), ('官方源直连', 'direct'), ('优先加速源', 'mirror'),
    ('GitProxy', 'gitproxy'), ('GH-Proxy', 'gh-proxy'), ('ghfast.top', 'ghfast'),
    ('HF-Mirror', 'hf-mirror'),
    ('TUNA PyPI', 'pypi-tuna'), ('Aliyun PyPI', 'pypi-aliyun'),
    ('SJTU PyTorch', 'pytorch-sjtu'),
)


def _runtime_routes(url, source):
    """Standalone first-hop routing, without importing installed client code."""
    source = 'direct' if source == 'official' else source
    if source not in {value for _, value in DOWNLOAD_SOURCE_OPTIONS}:
        raise ValueError('请选择自动、官方源或加速源。')
    parsed = urlsplit(url)
    public_release = (parsed.scheme == 'https' and parsed.netloc == 'github.com'
                      and not parsed.username and not parsed.password and not parsed.query
                      and not parsed.fragment and '\\' not in parsed.path
                      and all(part not in ('.', '..') for part in parsed.path.split('/'))
                      and ('/releases/download/' in parsed.path or '/releases/latest/download/' in parsed.path))
    if not public_release or source in ('direct', 'hf-mirror', 'pypi-tuna', 'pypi-aliyun', 'pytorch-sjtu'):
        return [url]
    routes = {
        'gitproxy': 'https://api.gitproxy.dev/' + url.removeprefix('https://'),
        'gh-proxy': 'https://gh-proxy.com/' + url,
        'ghfast': 'https://ghfast.top/' + url,
    }
    if source in routes:
        return [routes[source], url]
    mirrors = list(routes.values())
    return [*mirrors, url] if source == 'mirror' else [url, *mirrors]


def read_release_catalog(source='auto', opener=None):
    url = 'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/latest/download/release-assets.json'
    last_error = None
    for route in _runtime_routes(url, source):
        try:
            client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
            request = urllib.request.Request(route, headers={'User-Agent':'VWA-Compatibility-Updater','Accept':'application/json'})
            with client(request, timeout=12) as response:
                data = response.read(2 * 1024**2 + 1)
            if len(data) > 2 * 1024**2:
                raise ValueError('发布清单超过大小限制。')
            inventory = json.loads(data)
            if (not isinstance(inventory, dict) or not isinstance(inventory.get('version'), str)
                    or not re.fullmatch(r'\d+\.\d+\.\d+', inventory['version'])):
                raise ValueError('没有可识别的正式版更新清单。')
            return inventory
        except OSError as error:
            last_error = error
    raise OSError('无法读取发布清单，请检查网络或切换下载源。') from last_error


def running_application_pids(root):
    """Find only this installation's Windows launcher, without closing it."""
    if os.name != 'nt': return []
    root = Path(root).resolve()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('size',ctypes.c_ulong),('usage',ctypes.c_ulong),('pid',ctypes.c_ulong),
                   ('heap',ctypes.c_size_t),('module',ctypes.c_ulong),('threads',ctypes.c_ulong),
                   ('parent',ctypes.c_ulong),('priority',ctypes.c_long),('flags',ctypes.c_ulong),('name',ctypes.c_wchar*260)]
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(Entry))
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong))
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    snapshot = kernel.CreateToolhelp32Snapshot(2,0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError('无法检查助手运行状态，请关闭助手后重试。')
    try:
        excluded = {os.getpid()} | {int(os.environ.get(key, '0')) for key in ('VWA_LAUNCHER_PID', 'BVWA_LAUNCHER_PID', 'VWA_COMPAT_LAUNCHER_PID')}
    except ValueError:
        excluded = {os.getpid()}
    found = []
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while more:
            if entry.name.casefold() in ('vwa.exe', 'bvwa.exe') and entry.pid not in excluded:
                handle = kernel.OpenProcess(0x1000,False,entry.pid)
                if handle:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768); length = ctypes.c_ulong(len(buffer))
                        if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):
                            if Path(buffer.value).resolve() in (root/'VWA.exe', root/'BVWA.exe'): found.append(entry.pid)
                    finally: kernel.CloseHandle(handle)
            more = kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally: kernel.CloseHandle(snapshot)
    return found


def installation_busy(root):
    """Read task/session locks; never stop a user's active process."""
    root = Path(root).resolve()
    if running_application_pids(root): return '请先关闭此目录中已经打开的助手。'
    def locked(directory):
        path = Path(directory)/'run.lock'
        if not path.exists(): return False
        try:
            import msvcrt
            with path.open('r+b') as stream:
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            return False
        except OSError: return True
    if locked(root/'cache/updates'): return '另一个更新安装器正在运行。'
    if locked(root/'dependencies/optional/bs-roformer'): return '分离组件仍在安装或使用中。'
    for name in ('bilibili','xiaohongshu'):
        if locked(root/'cache/publish-browser'/name): return '请先关闭上传和登录窗口。'
    roots = {root/'jobs'}
    try:
        state = json.loads((root/'cache/workspace.json').read_text(encoding='utf-8'))
        saved = state.get('job_roots',[]) if isinstance(state,dict) else []
        for value in saved if isinstance(saved,list) else []:
            if not isinstance(value,str): continue
            path = (root/value[2:]).resolve() if value.startswith('@/') else Path(value)
            if value.startswith('@/') and not path.is_relative_to(root): continue
            if path.is_absolute(): roots.add(path)
    except (OSError,ValueError): pass
    for parent in roots:
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and not getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&1024:
                        if (Path(entry.path)/'request.json').is_file() and locked(entry.path):
                            return '仍有音乐任务在运行，请完成或停止后再安装更新。'
        except FileNotFoundError: pass
    downloads = root/'cache/feature-downloads'
    if downloads.exists() and any(locked(path) for path in downloads.iterdir() if path.is_dir()):
        return '仍有语言模型在下载。'
    return ''


def _artifact_download(item, folder, progress, check, source, *, runtime):
    _asset_record(item, runtime=runtime)
    name = _runtime_name(item) if runtime else program_name(item)
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / name
    pending = archive.with_suffix('.zip.partial')
    if archive.is_file() and archive.stat().st_size == item['bytes'] and sha(archive) == item['sha256']:
        return archive
    base_url = 'https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v'
    url = base_url + quote(item['version'], safe='') + '/' + quote(name, safe='')
    routes = _runtime_routes(url, source)
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
            headers = {'User-Agent': 'VWA-Runtime-Upgrade', 'Accept-Encoding': 'identity'}
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
                        provider = 'GitProxy' if route.startswith('https://api.gitproxy.dev/') else 'GitHub'
                        progress(offset, item['bytes'], f'正在通过 {provider} 下载升级文件…')
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


def _runtime_download(item, folder, progress, check, source):
    return _artifact_download(item, folder, progress, check, source, runtime=True)


def prepare_program_update(version, item, root, progress=lambda *args: None, check=None,
                           source='auto', downloader=None, *, register_pending=True):
    """One program staging implementation for the GUI and legacy repair tool."""
    _asset_record(item)
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('更新版本号无效。')
    root = Path(root).resolve()
    runtime_sha = sha(root / 'dependencies/manifest.json')
    if item.get('runtime_sha256') != runtime_sha:
        raise ValueError('此更新包与当前运行库不匹配，请使用兼容更新工具。')
    cache = validate_destination(root, 'cache/updates')
    cache.mkdir(parents=True, exist_ok=True)
    folder = validate_destination(root, 'cache/updates/downloads')
    selected = dict(item, version=version)
    selected['url'] = ('https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/releases/download/v'
                       + quote(version, safe='') + '/' + quote(program_name(item), safe=''))
    if downloader:
        archive = downloader(selected, folder, progress, check, source)
    else:
        archive = _artifact_download(selected, folder, progress, check, source, runtime=False)
    if archive.stat().st_size != item['bytes'] or sha(archive) != item['sha256']:
        raise ValueError('程序更新包校验失败。')
    stage = Path(tempfile.mkdtemp(prefix='stage-', dir=cache))
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            if (len(infos) > 200 or sum(info.file_size for info in infos) > MAX_PROGRAM_BYTES
                    or len({info.filename for info in infos}) != len(infos)):
                raise ValueError('程序更新包内容过大或包含重复文件。')
            inventory = json.loads(zipped.read('update-inventory.json'))
            if inventory['version'] != version or inventory['runtime_sha256'] != runtime_sha:
                raise ValueError('程序更新包版本或运行库不匹配。')
            entries = inventory['entries']
            names = {entry['file'] for entry in entries}
            if (len(entries) != len(names) or not {'app.py', 'version.json'}.issubset(names)
                    or {info.filename for info in infos} != names | {'update-inventory.json'}):
                raise ValueError('程序更新包与文件清单不一致。')
            for entry in entries:
                if check and check():
                    raise InterruptedError('已取消更新下载。')
                validate_file(entry)
                validate_destination(root, entry['file'])
                info = zipped.getinfo(entry['file'])
                if info.file_size != entry['bytes'] or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('程序更新文件大小或类型无效。')
                target = validate_destination(stage / 'files', entry['file'])
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(info) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if sha(target) != entry['sha256']:
                    raise ValueError('程序更新文件校验失败：' + entry['file'])
        if json.loads((stage / 'files/version.json').read_text(encoding='utf-8'))['version'] != version:
            raise ValueError('更新程序版本信息不匹配。')
        plan = stage / 'plan.json'
        plan.write_text(json.dumps({'root': str(root), 'version': version, 'runtime_sha256': runtime_sha,
                                    'entries': entries, 'state': 'ready'}, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.copy2(Path(__file__), stage / 'apply_helper.py')
        if register_pending:
            _write_pending(root, plan, version)
        return plan
    except BaseException:
        _remove_stage(stage, cache)
        raise


def _remove_stage(stage, cache):
    stage, cache = Path(stage).absolute(), Path(cache).resolve()
    if (stage.resolve().parent != cache or not stage.name.startswith(('stage-', 'runtime-stage-'))
            or stage.is_symlink() or stage.is_junction()):
        raise ValueError('更新暂存目录超出清理范围。')
    shutil.rmtree(_native(stage))


def _write_pending(root, plan, version):
    path = validate_destination(root, 'cache/updates/pending.json')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='pending-', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            json.dump({'plan': str(plan), 'version': version}, stream)
        os.replace(temporary, path)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def prepare_catalog_update(version, route, root, progress=lambda *args: None, check=None,
                           source='auto', downloader=None):
    """Verify a narrow bootstrap, then stage the full update with its new helper.

    No installed code is replaced between these steps. This also lets a repair
    tool with an older allowlist hand off to the release's verified installer.
    """
    root = Path(root).resolve()
    if route.get('runtime_sha256') != sha(root / 'dependencies/manifest.json'):
        raise ValueError('更新分流与当前运行库不匹配。')
    bootstrap = prepare_program_update(version, route['update'], root, progress, check,
                                       source, downloader, register_pending=False)
    cache, module_name = root / 'cache/updates', '_vwa_update_' + uuid.uuid4().hex
    try:
        plan = json.loads(bootstrap.read_text(encoding='utf-8'))
        if {item['file'] for item in plan['entries']} != {'app.py', 'version.json', 'tools/update_apply.py'}:
            raise ValueError('兼容引导包必须只包含入口、安装器和版本信息。')
        metadata = json.loads((bootstrap.parent / 'files/version.json').read_text(encoding='utf-8'))
        item = metadata.get('runtime_upgrade')
        _asset_record(item, runtime=True)
        if (not metadata.get('upgrade_pending') or item.get('version') != version
                or item.get('source_runtime_sha256') != route['runtime_sha256']
                or (route.get('runtime_upgrade') is not None and item != route['runtime_upgrade'])):
            raise ValueError('兼容引导包与发布清单的升级目标不一致。')
        if check and check():
            raise InterruptedError('已取消更新下载。')
        helper = bootstrap.parent / 'files/tools/update_apply.py'
        spec = importlib.util.spec_from_file_location(module_name, helper)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        prepare = getattr(module, 'prepare_runtime_upgrade', None)
        if not callable(prepare):
            raise ValueError('升级包中的安装器不支持运行库迁移。')
        progress(0, item['bytes'], '兼容安装器已校验，正在准备所需的增量文件…')
        full = Path(prepare(item, root, progress, check, source, downloader)).resolve()
        if (_plan_root(full) != root or full.name != 'plan.json'
                or json.loads(full.read_text(encoding='utf-8')).get('version') != version):
            raise ValueError('增量安装计划与当前助手不匹配。')
        install = json.loads(full.read_text(encoding='utf-8'))
        helper_entry = next((entry for entry in install['entries'] if entry['file'] == 'tools/update_apply.py'), None)
        expected = sha(helper)
        if helper_entry is None:
            if not (root / 'tools/update_apply.py').is_file() or sha(root / 'tools/update_apply.py') != expected:
                _remove_stage(full.parent, cache)
                raise ValueError('增量包缺少本次升级需要的安装器。')
        elif helper_entry['sha256'] != expected:
            _remove_stage(full.parent, cache)
            raise ValueError('引导包与增量包使用了不同的安装器。')
        _write_pending(root, full, version)
        return full
    finally:
        sys.modules.pop(module_name, None)
        _remove_stage(bootstrap.parent, cache)


def prepare_runtime_upgrade(item, root, progress=lambda *args: None, check=None, source='official', downloader=None):
    if check and check():
        raise InterruptedError('升级已取消，下次启动可继续。')
    root = Path(root).resolve()
    name = _runtime_name(item)
    if (not re.fullmatch(r'\d+\.\d+\.\d+', item.get('version', '')) or
            not isinstance(item.get('bytes'), int) or not 0 < item['bytes'] <= MAX_RUNTIME_BYTES or
            any(not re.fullmatch(r'[a-f0-9]{64}', item.get(key, '')) for key in
                ('sha256', 'source_runtime_sha256', 'target_runtime_sha256'))):
        raise ValueError('运行库升级信息无效，请使用完整便携包。')
    if sha(root / 'dependencies/manifest.json') != item['source_runtime_sha256']:
        raise ValueError('当前运行库与升级包不匹配，请使用完整便携包。')
    cache = validate_destination(root, 'cache/updates')
    cache.mkdir(parents=True, exist_ok=True)
    folder = validate_destination(root, 'cache/updates/runtime-downloads')
    for filename in (name, name + '.partial'):
        validate_destination(root, 'cache/updates/runtime-downloads/' + filename)
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
            _validate_runtime_stage(root, stage, plan, check)
        plan.update(root=str(root), kind='runtime_upgrade', state='ready')
        plan_path = stage / 'plan.json'
        plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        shutil.copy2(Path(__file__), stage / 'apply_helper.py')
        progress(item['bytes'], item['bytes'], '校验完成，增量文件已准备。')
        return plan_path
    except BaseException:
        if stage.resolve().is_relative_to(cache.resolve()) and stage.name.startswith('runtime-stage-'):
            shutil.rmtree(stage)
        raise


def apply_runtime_upgrade(plan_path):
    plan_path = Path(plan_path).resolve()
    root, plan = _read_plan(plan_path)
    _require_forward_target(root, plan, plan_path)
    if reason := installation_busy(root):
        raise RuntimeError(reason)
    stage = plan_path.parent
    records, removals = _validate_runtime_stage(root, stage, plan)
    source_names = {name: name for name in records}
    alias_attributes = {}
    if plan.get('launcher_migration') is not None:
        legacy = _RuntimeDestinations(root).checked('BVWA.exe')
        if legacy.exists() and not legacy.is_file():
            raise ValueError('旧启动入口不是普通文件，已停止迁移。')
        if legacy.is_file():
            records = dict(records)
            records['BVWA.exe'] = dict(records['VWA.exe'], file='BVWA.exe')
            source_names['BVWA.exe'] = 'VWA.exe'
            alias_attributes['BVWA.exe'] = _file_attributes(legacy)
    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=_native(stage)))
    touched = []
    skipped = []
    root_paths = _RuntimeDestinations(root)
    try:
        # Commit version and manifest only after all program/runtime files.
        names = sorted(records, key=lambda name: (name in ('version.json', 'dependencies/manifest.json'),
                                                  name in alias_attributes, name))
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
            pending = destination.with_name(destination.name + '.vwa-update')
            root_paths.checked(pending.relative_to(root).as_posix())
            if pending.exists() or destination.with_name(destination.name + '.bvwa-update').exists():
                raise ValueError('发现未完成的升级临时文件，请检查更新日志。')
            try:
                shutil.copy2(stage / 'files' / source_names[name], pending)
                pending.replace(destination)
                if name in alias_attributes:
                    attributes = _file_attributes(destination)
                    _set_file_attributes(destination, (attributes & ~FILE_ATTRIBUTE_NORMAL) | FILE_ATTRIBUTE_HIDDEN if attributes is not None else None)
            finally:
                pending.unlink(missing_ok=True)
        _clear_pending(root, plan_path)
        return {'state': 'passed', 'version': plan['version'], 'files': len(records), 'removed': len(removals)-len(skipped),
                'kept_modified_runtime_files': skipped, 'runtime_upgraded': True,
                'launcher_migrated': bool(alias_attributes)}
    except BaseException:
        for destination, saved, existed in reversed(touched):
            if existed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                name = destination.relative_to(root).as_posix()
                if name in alias_attributes:
                    attributes = _file_attributes(destination)
                    _set_file_attributes(
                        destination, attributes & ~(FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_NORMAL)
                        if attributes is not None else None)
                shutil.copy2(saved, destination)
                if name in alias_attributes:
                    _set_file_attributes(destination, alias_attributes[name])
            else:
                destination.unlink(missing_ok=True)
        _clear_pending(root, plan_path)
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
    try:
        root = _plan_root(args.plan)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    plan, parents_exited = {}, False
    try:
        wait_for_exit(args.parent)
        wait_for_exit(args.launcher_parent)
        parents_exited = True
        _, plan = _read_plan(args.plan)
        result = apply_update(args.plan)
    except Exception as exc:
        version = plan.get('version')
        if version is None:
            try:
                pending = json.loads((root / 'cache/updates/pending.json').read_text(encoding='utf-8-sig'))
                if Path(pending['plan']).resolve() == args.plan.resolve():
                    version = pending.get('version')
            except (OSError, ValueError, KeyError, TypeError):
                pass
        result = {'state': 'failed', 'error': str(exc), 'version': version}
    _clear_pending(root, args.plan)
    _write_result(root, result)
    launcher = next((root / name for name in ('VWA.exe', 'BVWA.exe') if (root / name).is_file()), root / 'VWA.exe')
    if parents_exited and launcher.is_file():
        subprocess.Popen([str(launcher)], cwd=root, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0 if result['state'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
