"""Verified app updates; inference runtimes and user state remain local."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import zipfile

from common import ROOT, atomic_json, file_hash, load_config
from download_sources import read_small

REPO = 'Transilent/Better-Vocaloid-Workflow-Assistant'
RELEASES = 'https://github.com/' + REPO + '/releases'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'
UPDATE_NAME = 'BVWA-App-Update.zip'
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
    if archive.get('name') != 'BVWA-Windows-x64.zip' or not inventory.get('parts'):
        raise OSError('暂时无法读取版本信息，请切换下载方式后重试。')
    return {'version': None, 'available': False, 'legacy': True, 'update': None,
            'url': RELEASES + '/latest',
            'notes': '当前发布版尚未提供自动更新信息。请从发布页面查看版本并下载完整便携包。'}


def check_release(source='auto', reader=read_small):
    if source == 'mirror':
        try:
            inventory = json.loads(reader(RELEASES + '/latest/download/release-assets.json', source))
            version = inventory.get('version')
            if version:
                version_key(version)
                return {'version': version, 'notes': inventory.get('release_notes', ''),
                        'url': RELEASES + '/tag/v' + version, 'available': is_newer(version),
                        'update': inventory.get('update')}
        except (OSError, json.JSONDecodeError):
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
            result['update'] = inventory.get('update')
        return result
    except (OSError, KeyError, json.JSONDecodeError):
        # Published inventory is also available through GitProxy when the API
        # cannot be reached. It contains the version and update checksum.
        inventory = json.loads(reader(RELEASES + '/latest/download/release-assets.json', source))
        version = inventory.get('version')
        if not version:
            return legacy_release(inventory)
        version_key(version)
        return {'version': version, 'notes': inventory.get('release_notes', ''),
                'url': RELEASES + '/tag/v' + version,
                'available': is_newer(version), 'update': inventory.get('update')}


def prepare_update(release, source='auto', progress=lambda *args: None, check=None,
                   root=ROOT, downloader=None):
    from optional_components import download, remove_owned_tree
    from tools.update_apply import validate_file, validate_destination
    root = Path(root).resolve()
    version = release['version']
    version_key(version)
    item = dict(release.get('update') or {})
    if item.get('name') != UPDATE_NAME:
        raise ValueError('此版本未提供程序更新包，请从发布页面下载完整版。')
    if not isinstance(item.get('bytes'), int) or not 0 < item['bytes'] <= MAX_UPDATE_BYTES:
        raise ValueError('程序更新包大小无效。')
    if not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
        raise ValueError('程序更新包缺少有效校验信息。')
    runtime_sha = file_hash(root / 'dependencies/manifest.json')
    if item.get('runtime_sha256') != runtime_sha:
        raise ValueError('此版本需要新的基础运行库，请从发布页面下载完整版。现有任务可以继续保留。')
    cache = root / 'cache/updates'
    cache.mkdir(parents=True, exist_ok=True)
    item['url'] = asset_url(version, UPDATE_NAME)
    archive = (downloader or download)(item, cache / 'downloads', progress, check, source=source)
    if archive.stat().st_size != item['bytes'] or file_hash(archive) != item['sha256']:
        raise ValueError('程序更新包校验失败。')
    stage = Path(tempfile.mkdtemp(prefix='stage-', dir=cache))
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            if len(infos) > 200 or sum(i.file_size for i in infos) > MAX_UPDATE_BYTES:
                raise ValueError('更新包内容超过大小限制。')
            if len({i.filename for i in infos}) != len(infos):
                raise ValueError('更新包包含重复文件。')
            inventory = json.loads(zipped.read('update-inventory.json'))
            if inventory['version'] != version or inventory['runtime_sha256'] != runtime_sha:
                raise ValueError('更新包版本或运行库不匹配。')
            entries = inventory['entries']
            if len(entries) != len({e['file'] for e in entries}):
                raise ValueError('更新清单包含重复文件。')
            if not {'app.py', 'version.json'}.issubset({e['file'] for e in entries}):
                raise ValueError('更新包缺少必要的程序文件。')
            if {i.filename for i in infos} != {'update-inventory.json'} | {e['file'] for e in entries}:
                raise ValueError('更新包与文件清单不一致。')
            for entry in entries:
                if check and check():
                    raise InterruptedError('已取消更新下载。')
                validate_file(entry)
                validate_destination(root, entry['file'])
                info = zipped.getinfo(entry['file'])
                if info.file_size != entry['bytes'] or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('更新包文件大小或类型无效。')
                target = stage / 'files' / entry['file']
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(info) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if file_hash(target) != entry['sha256']:
                    raise ValueError('更新文件校验失败：' + entry['file'])
        if json.loads((stage / 'files/version.json').read_text(encoding='utf-8'))['version'] != version:
            raise ValueError('程序版本信息不匹配。')
        plan = stage / 'plan.json'
        atomic_json(plan, {'root': str(root), 'version': version, 'runtime_sha256': runtime_sha,
                           'entries': entries, 'state': 'ready'})
        shutil.copy2(ROOT / 'tools/update_apply.py', stage / 'apply_helper.py')
        atomic_json(cache / 'pending.json', {'plan': str(plan), 'version': version})
        progress(item['bytes'], item['bytes'], '下载完成，已校验全部更新文件')
        return plan
    except BaseException:
        remove_owned_tree(stage, cache)
        raise


def pending_update(root=ROOT):
    root = Path(root).resolve()
    try:
        receipt = json.loads((root / 'cache/updates/pending.json').read_text(encoding='utf-8'))
        plan = Path(receipt['plan']).resolve()
        if not plan.is_relative_to(root / 'cache/updates') or not plan.is_file():
            return None
        return receipt
    except (OSError, ValueError, KeyError):
        return None


def restart_to_apply(plan):
    plan = Path(plan).resolve()
    if not plan.is_relative_to(ROOT / 'cache/updates'):
        raise ValueError('更新计划路径无效。')
    pythonw = Path(load_config()['python']).with_name('pythonw.exe')
    command = [str(pythonw), '-B', str(plan.parent / 'apply_helper.py'), '--plan', str(plan),
               '--parent', str(os.getpid()), '--launcher-parent', os.environ.get('BVWA_LAUNCHER_PID', '0')]
    subprocess.Popen(command, cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                     env=dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1'))
