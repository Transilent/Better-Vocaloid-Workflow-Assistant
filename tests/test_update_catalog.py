"""Three runtime identities, legacy entry points and one-restart staging."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import updater
from tools import update_apply as helper
from tools.build_runtime_upgrade import build_bootstrap
from tools.build_update_catalog import build as build_catalog
from tools.build_update_bridge import build as build_bridge
from tools import build_portable
from tools import build_update_catalog


def record(name, data):
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(root, files):
    for name, data in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)


clients = {client['version']: client for client in json.loads((APP/'packaging/update-clients.json').read_text(encoding='utf-8'))['clients']}
version = '9.9.9'
with tempfile.TemporaryDirectory(dir=APP/'work', prefix='catalog-') as directory:
    scratch = Path(directory); assets = scratch/'assets'; assets.mkdir()
    preserved_model = b'unchanged original model'
    runtime_name = 'dependencies/runtime/engine.txt'
    target_engine = b'new engine'
    target_manifest = json.dumps({'entries': [record(runtime_name, target_engine), record('models/keep.bin', preserved_model)]}).encode()
    target_manifest_path = scratch/'dependencies/manifest.json'
    write(scratch, {'dependencies/manifest.json': target_manifest})
    target_sha = hashlib.sha256(target_manifest).hexdigest()
    installer = (APP/'tools/update_apply.py').read_bytes()
    payload = {'app.py': b'# new application', 'version.json': b'{"version":"9.9.9","channel":"stable"}',
               'tools/update_apply.py': installer, 'pitch_accuracy.py': b'# new module',
               'dependencies/manifest.json': target_manifest, runtime_name: target_engine}
    # Real release deltas include these explicitly allowed support files outside
    # dependencies/. Nearby component paths must not gain the same permission.
    payload.update({'components/roformer.json': b'{"fixture":"roformer"}',
                    'components/runtime-features.json': b'{"fixture":"features"}',
                    'models/来源说明.md': b'Original model provenance fixture'})
    for forbidden in ('components/unreviewed.json', 'components/../roformer.json'):
        try:
            build_update_catalog._program_record(record(forbidden, b'unsafe'))
            raise AssertionError('Unlisted component support file accepted')
        except ValueError:
            pass
    manifests, migrations = {}, []
    for source, bootstrap_name in [('0.3.0','BVWA-App-Update-v0.3.0.zip'),('0.4.0','BVWA-Program-Update.zip'),('0.4.1','BVWA-App-Update.zip')]:
        before = source.encode()
        manifest = json.dumps({'entries':[record(runtime_name, before), record('models/keep.bin', preserved_model)]}).encode()
        manifests[source] = manifest
        base_sha = hashlib.sha256(manifest).hexdigest()
        runtime = assets/('BVWA-Runtime-Upgrade-v'+source+'.zip')
        plan = {'format':1,'version':version,'source_runtime_sha256':base_sha,
                'target_runtime_sha256':target_sha, 'entries':[record(name,data) for name,data in payload.items()], 'removed':[]}
        with zipfile.ZipFile(runtime,'x',compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, data in payload.items(): zipped.writestr(name, data)
            zipped.writestr('runtime-upgrade.json',json.dumps(plan))
        item = {'name':runtime.name,'version':version,'bytes':runtime.stat().st_size,'sha256':hashlib.sha256(runtime.read_bytes()).hexdigest(),
                'source_runtime_sha256':base_sha,'target_runtime_sha256':target_sha}
        bootstrap = build_bootstrap(assets,item,bootstrap_name)
        migrations.append({'source_version':source,'update':bootstrap,'runtime_upgrade':item})
    try:
        build_catalog(assets, version, migrations, legacy_update='0.4.1', legacy_program='0.4.0', target_manifest=target_manifest_path, external_reason='catalog fixture target')
        raise AssertionError('A release silently dropped the third legacy runtime')
    except ValueError as error:
        assert 'repair tool' in str(error)
    tool = assets/'BVWA-Updater.exe'
    tool_record = build_bridge(tool, version)
    inventory = build_catalog(assets, version, migrations, legacy_update='0.4.1', legacy_program='0.4.0', compatibility_tool=tool_record, target_manifest=target_manifest_path, external_reason='catalog fixture target')
    assert inventory['compatibility_tool']['required_for'] == ['0.3.0']
    assert helper.select_update_route(inventory, 'f'*64) is None
    # Completing portable metadata preserves every verified route and legacy
    # entry point; it must neither replace the catalog nor build a wrong package.
    report={'archive':{'name':'fixture.zip','bytes':1,'sha256':'a'*64},'parts':[]}
    original=(assets/'release-assets.json').read_bytes()
    with patch.object(build_portable,'ROOT',scratch), patch.object(build_update_catalog,'ROOT',scratch), patch.object(build_portable,'inventory_path',return_value=scratch/'version.json'), \
         patch.object(build_portable,'release_notes',return_value='Fixture notes'), \
         patch('tools.build_app_update.build',side_effect=AssertionError('Wrong fast package created')):
        write(scratch,{'version.json':json.dumps({'version':version}).encode(),'dependencies/manifest.json':target_manifest})
        combined=build_portable.write_release_metadata(report,assets)
        assert combined['updates']==inventory['updates'] and combined['compatibility_tool']==inventory['compatibility_tool']
        assert combined['update']==inventory['update'] and combined['program_update']==inventory['program_update']
        assert combined['archive']==report['archive'] and combined['release_notes']=='Fixture notes'
        # Tampered migration files fail without replacing the existing catalog.
        before=(assets/'release-assets.json').read_bytes()
        raw=tool.read_bytes();tool.write_bytes(b'MZ invalid executable')
        try:build_portable.write_release_metadata(report,assets);raise AssertionError('Tampered tool accepted')
        except ValueError:pass
        assert (assets/'release-assets.json').read_bytes()==before
        tool.write_bytes(raw)
    (assets/'release-assets.json').write_bytes(original)
    fake=assets/'bad.exe';fake.write_bytes(b'MZ invalid executable')
    bad_record=record('BVWA-Updater.exe',fake.read_bytes());bad_record['name']=bad_record.pop('file')
    raw=tool.read_bytes();tool.write_bytes(fake.read_bytes())
    try:
        build_catalog(assets,version,migrations,legacy_update='0.4.1',legacy_program='0.4.0',compatibility_tool=bad_record,target_manifest=target_manifest_path,external_reason='catalog fixture target')
        raise AssertionError('A matching checksum alone cannot make a file a GUI executable')
    except ValueError as error:assert 'executable' in str(error)
    finally:tool.write_bytes(raw)
    assert (assets/'release-assets.json').read_bytes()==original

    def download(item, folder, *args, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets/item['name'], folder/item['name'])
        return folder/item['name']

    def installation(source, label):
        root = scratch/label; root.mkdir()
        legacy = clients[source]['fixtures']['tools/update_apply.py']
        kept = {'config.json':b'private settings','last_job.txt':b'real task', 'jobs/song/tuned.mid':b'USER MIDI',
                'cache/publish-browser/login':b'ACCOUNT','dependencies/optional/user.bin':b'OPTIONAL MODEL',
                'models/keep.bin':preserved_model}
        write(root, {**kept, 'app.py':b'# old application', 'version.json':json.dumps({'version':source}).encode(),
                     'dependencies/manifest.json':manifests[source], runtime_name:source.encode(),
                     'tools/update_apply.py':(APP/legacy['fixture']).read_bytes()})
        return root, kept

    for source in manifests:
        root, kept = installation(source, 'modern-'+source)
        before = {name:(root/name).read_bytes() for name in ('app.py','version.json','tools/update_apply.py','dependencies/manifest.json')}
        with patch.object(updater, 'ROOT', root):
            selected = updater.compatible_update(inventory)
        assert selected['catalog_route']['source_version'] == source
        release = {'version':version,'update':selected}
        plan = updater.prepare_update(release, root=root, downloader=download)
        assert all((root/name).read_bytes()==data for name,data in before.items()), 'Preparation must not install the bootstrap'
        assert updater.pending_update(root)['plan'] == str(plan)
        assert len(list((root/'cache/updates').glob('stage-*'))) == 0, 'The unused bootstrap stage must be removed'
        staged = module('staged_'+source.replace('.','_'),plan.parent/'apply_helper.py')
        assert staged.apply_update(plan)['state']=='passed'
        assert all((root/name).read_bytes()==data for name,data in {**kept,**payload}.items())
        assert updater.pending_update(root) is None
        helper._remove_stage(plan.parent,root/'cache/updates')

    # Real old client code can use only its original two slots. The uncovered
    # 0.3.x branch is explicitly repaired through the new route rather than a
    # mismatched download or a full portable package.
    for source in manifests:
        client = clients[source]
        for item in client['fixtures'].values():
            assert hashlib.sha256((APP/item['fixture']).read_bytes()).hexdigest()==item['sha256']
        old = module('old_client_'+source.replace('.','_'),APP/client['fixtures']['src/updater.py']['fixture'])
        old_helper = module('old_installer_'+source.replace('.','_'),APP/client['fixtures']['tools/update_apply.py']['fixture'])
        root, kept = installation(source,'legacy-'+source)
        def reader(url, selected_source):
            if url==old.API:
                return json.dumps({'tag_name':'v'+version,'draft':False,'prerelease':False,'body':'fixture'}).encode()
            return json.dumps(inventory).encode()
        with patch.object(old,'ROOT',root), patch.dict(sys.modules,{'tools.update_apply':old_helper}):
            released=old.check_release('direct',reader=reader)
            if source=='0.3.0':
                try:old.prepare_update(released,root=root,downloader=download);raise AssertionError('Wrong legacy runtime accepted')
                except ValueError:pass
            else:
                plan=old.prepare_update(released,root=root,downloader=download)
                assert old_helper.apply_update(plan)['state']=='passed'
                new=module('new_installer_'+source.replace('.','_'),root/'tools/update_apply.py')
                item=json.loads((root/'version.json').read_text())['runtime_upgrade']
                full=new.prepare_runtime_upgrade(item,root,downloader=download)
                assert new.apply_update(full)['state']=='passed'
        if source=='0.3.0':
            full=helper.prepare_catalog_update(version,helper.select_update_route(inventory,hashlib.sha256(manifests[source]).hexdigest()),root,downloader=download)
            assert helper.apply_update(full)['state']=='passed'
        assert all((root/name).read_bytes()==data for name,data in {**kept,**payload}.items())
        for stage in (root/'cache/updates').glob('runtime-stage-*'):
            helper._remove_stage(stage,root/'cache/updates')

    # Fail closed on ambiguous routes, wrong formats and mismatched bootstrap
    # metadata. Cancellation between the two downloads preserves old pending state.
    for bad in ({**inventory,'updates':inventory['updates']*2},{**inventory,'update_format':99},
                {**inventory,'updates':[dict(inventory['updates'][0],runtime_sha256='x')]}):
        try:helper.select_update_route(bad,'f'*64);raise AssertionError('Invalid route map accepted')
        except ValueError:pass
    root, kept=installation('0.3.0','cancelled')
    route=helper.select_update_route(inventory,hashlib.sha256(manifests['0.3.0']).hexdigest())
    write(root,{'cache/updates/pending.json':b'{"keep":"previous verified plan"}'})
    cancel, fetched=[False],[]
    def report(done,total,text):
        if '兼容安装器已校验' in text:cancel[0]=True
    def count_download(item,*args,**kwargs):
        fetched.append(item['name']);return download(item,*args,**kwargs)
    try:helper.prepare_catalog_update(version,route,root,report,lambda:cancel[0],downloader=count_download);raise AssertionError('Cancellation ignored')
    except InterruptedError:pass
    assert fetched==[route['update']['name']]
    assert (root/'cache/updates/pending.json').read_bytes()==b'{"keep":"previous verified plan"}'
    assert all((root/name).read_bytes()==data for name,data in kept.items())
    assert not list((root/'cache/updates').glob('stage-*'))
print('PASS: three runtime routes, original legacy clients, explicit repair coverage, one-restart staging, private/model preservation and cancel-before-large-download')
