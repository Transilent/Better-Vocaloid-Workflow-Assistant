"""Verified app updates; inference runtimes and user state remain local."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote

from common import ROOT, file_hash, load_config, environment_value
from download_sources import normalize_source, read_small

REPO = 'Transilent/Better-Vocaloid-Workflow-Assistant'
RELEASES = 'https://github.com/' + REPO + '/releases'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'
UPDATE_NAME = 'VWA-App-Update.zip'
PROGRAM_UPDATE_NAME = 'VWA-Program-Update.zip'
MAX_UPDATE_BYTES = 64 * 1024**2


def current_version():
    return json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))['version']


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?', value)
    if not match:
        raise ValueError('发布版本号无效。')
    core = tuple(int(n) for n in match.groups()[:3])
    suffix = match[4]
    return core + (1 if suffix is None else 0,
                   tuple((0, int(n)) if n.isdigit() else (1, n) for n in (suffix or '').split('.')))


def is_newer(candidate, installed=None):
    return version_key(candidate) > version_key(installed or current_version())


def asset_url(version, name):
    return RELEASES + '/download/v' + version.removeprefix('v') + '/' + quote(name, safe='')


def legacy_release(inventory):
    archive = inventory.get('archive') or {}
    if archive.get('name') not in ('VWA-Windows-x64.zip', 'BVWA-Windows-x64.zip') or not inventory.get('parts'):
        raise OSError('暂时无法读取版本信息，请切换下载方式后重试。')
    return {'version': None, 'available': False, 'legacy': True, 'update': None,
            'url': RELEASES + '/latest',
            'notes': '当前发布版尚未提供自动更新信息。请从发布页面查看版本并下载完整便携包。'}


def compatible_update(inventory):
    if 'updates' in inventory:
        from tools.update_apply import select_update_route
        route = select_update_route(inventory, file_hash(ROOT / 'dependencies/manifest.json'))
        if route is None:
            return None
        return dict(route['update'], catalog_route=route,
                    download_bytes=route['update']['bytes'] + route['runtime_upgrade']['bytes'])
    item = inventory.get('program_update')
    if item and item.get('runtime_sha256') == file_hash(ROOT / 'dependencies/manifest.json'):
        return item
    return inventory.get('update')


def _release_from_inventory(inventory):
    version = inventory.get('version')
    if not version:
        return legacy_release(inventory)
    version_key(version)
    return {'version': version, 'notes': inventory.get('release_notes', ''),
            'url': RELEASES + '/tag/v' + version, 'available': is_newer(version),
            'update': compatible_update(inventory)}


def check_release(source='auto', reader=read_small):
    """Read release metadata without routing the public API through a proxy.

    ``auto`` starts with the official API. Explicit accelerator IDs prefer the
    published Release inventory, but ``api.github.com`` itself remains direct so
    update discovery cannot leak cookies or signed metadata queries.
    """
    source = normalize_source(source)
    inventory_url = RELEASES + '/latest/download/release-assets.json'
    accelerated_first = source in ('mirror', 'gitproxy', 'gh-proxy', 'ghfast')
    if accelerated_first:
        try:
            return _release_from_inventory(json.loads(reader(inventory_url, source)))
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    try:
        release = json.loads(reader(API, source))
        if release.get('draft') or release.get('prerelease'):
            raise ValueError('没有可用的正式版本。')
        version = release['tag_name'].removeprefix('v')
        version_key(version)
        result = {'version': version, 'notes': release.get('body', ''),
                  'url': RELEASES + '/tag/v' + version, 'available': is_newer(version), 'update': None}
        if result['available']:
            inventory = json.loads(reader(asset_url(version, 'release-assets.json'), source))
            if inventory.get('version') and inventory['version'] != version:
                raise ValueError('更新清单与发布版本不一致。')
            result['update'] = compatible_update(inventory)
        return result
    except (OSError, KeyError, json.JSONDecodeError):
        # The published inventory is a fallback when the public API cannot be
        # reached. It contains the version and update checksum.
        inventory = json.loads(reader(inventory_url, source))
        return _release_from_inventory(inventory)


def prepare_update(release, source='auto', progress=lambda *args: None, check=None,
                   root=ROOT, downloader=None):
    source = normalize_source(source)
    from optional_components import download
    from tools.update_apply import prepare_program_update, prepare_catalog_update
    root = Path(root).resolve()
    version = release['version']
    version_key(version)
    item = dict(release.get('update') or {})
    def fetch(record, folder, report, cancel, selected_source):
        record = dict(record, url=asset_url(version, record['name']))
        return (downloader or download)(record, folder, report, cancel, source=selected_source)
    route = item.get('catalog_route')
    if route is not None:
        plan = prepare_catalog_update(version, route, root, progress, check, source, fetch)
    else:
        plan = prepare_program_update(version, item, root, progress, check, source, fetch)
    total = item.get('download_bytes', item.get('bytes', 1))
    progress(total, total, '下载完成，已校验全部更新文件')
    return plan


def _installed_version(root):
    data = json.loads((Path(root) / 'version.json').read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('当前程序版本信息无效。')
    version = data.get('version')
    version_key(version)
    return version


def _write_cache_json(root, name, data):
    path = Path(root) / 'cache/updates' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=name + '-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _discard_matching_pending(root, plan, version):
    path = Path(root) / 'cache/updates/pending.json'
    try:
        receipt = json.loads(path.read_text(encoding='utf-8'))
        matches = (isinstance(receipt, dict) and receipt.get('version') == version
                   and isinstance(receipt.get('plan'), str)
                   and Path(receipt['plan']).resolve() == Path(plan).resolve())
    except (OSError, ValueError, KeyError, TypeError):
        matches = False
    if matches:
        path.unlink(missing_ok=True)


def _reject_pending(root, plan, receipt, installed, reason, message):
    target = receipt.get('version') if isinstance(receipt, dict) else None
    # Remove only the receipt that points at this rejected plan. Staged files,
    # archives and a concurrently replaced pending receipt are left untouched.
    _discard_matching_pending(root, plan, target)
    diagnostic = {'state': 'rejected', 'reason': reason, 'plan': str(plan),
                  'version': target, 'installed_version': installed, 'message': message}
    try:
        _write_cache_json(root, 'pending-rejected.json', diagnostic)
        _write_cache_json(root, 'last-result.json', {
            'state': 'failed', 'reason': reason, 'version': target,
            'installed_version': installed, 'error': message})
    except OSError:
        pass


def pending_update(root=ROOT):
    root = Path(root).resolve()
    try:
        receipt = json.loads((root / 'cache/updates/pending.json').read_text(encoding='utf-8'))
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('plan'), str)
                or not isinstance(receipt.get('version'), str)):
            return None
        version_key(receipt['version'])
        plan = Path(receipt['plan']).resolve()
        if not plan.is_relative_to(root / 'cache/updates') or plan.name != 'plan.json' or not plan.is_file():
            return None
        data = json.loads(plan.read_text(encoding='utf-8-sig'))
        if (not isinstance(data, dict) or not isinstance(data.get('root'), str)
                or not Path(data['root']).is_absolute() or Path(data['root']).resolve() != root):
            _reject_pending(root, plan, receipt, None, 'invalid_plan',
                            '更新计划格式或助手目录无效，已忽略该 pending 回执；暂存文件已保留。')
            return None
        if not isinstance(data.get('version'), str):
            _reject_pending(root, plan, receipt, None, 'missing_plan_version',
                            '更新计划缺少目标版本，已忽略该 pending 回执；暂存文件已保留，请重新检查更新。')
            return None
        try:
            version_key(data['version'])
        except ValueError:
            _reject_pending(root, plan, receipt, None, 'invalid_plan_version',
                            '更新计划的目标版本无效，已忽略该 pending 回执；暂存文件已保留。')
            return None
        if data['version'] != receipt['version']:
            _reject_pending(root, plan, receipt, None, 'version_mismatch',
                            '更新回执与更新计划的目标版本不一致，已忽略该 pending 回执；暂存文件已保留。')
            return None
        try:
            installed = _installed_version(root)
        except (OSError, KeyError, TypeError, ValueError):
            _reject_pending(root, plan, receipt, None, 'invalid_installed_version',
                            '无法确认当前安装版本，已忽略该 pending 回执；暂存文件已保留，请重新检查更新。')
            return None
        if not is_newer(receipt['version'], installed):
            _reject_pending(
                root, plan, receipt, installed, 'target_not_newer',
                f'更新目标 {receipt["version"]} 不高于当前安装版本 {installed}，已忽略旧 pending；'
                '暂存文件已保留，请重新检查更新或使用完整便携包恢复。')
            return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def restart_to_apply(plan):
    plan = Path(plan).resolve()
    if not plan.is_relative_to(ROOT / 'cache/updates'):
        raise ValueError('更新计划路径无效。')
    pythonw = Path(load_config()['python']).with_name('pythonw.exe')
    command = [str(pythonw), '-B', str(plan.parent / 'apply_helper.py'), '--plan', str(plan),
               '--parent', str(os.getpid()), '--launcher-parent', environment_value('VWA_LAUNCHER_PID', '0')]
    subprocess.Popen(command, cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                     env=dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1'))
