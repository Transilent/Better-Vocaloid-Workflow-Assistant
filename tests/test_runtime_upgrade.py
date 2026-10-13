"""Runtime delta integrity, data preservation, rollback and startup handoff."""
import hashlib,io,json,sys,tempfile,zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools import update_apply as apply

def record(name,data):
    return {'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

def package(root,extra=None):
    unchanged={'models/original.bin':b'original model'}
    old={'dependencies/runtime/engine.py':b'old engine','dependencies/runtime/unused.dll':b'old unused'}
    kept={'config.json':b'private settings','jobs/song.mid':b'tuned midi','cache/publish-browser/login':b'login',
          'dependencies/optional/model.bin':b'optional model'}
    for name,data in {**unchanged,**old,**kept,'app.py':b'old app','version.json':b'{"version":"0.3.0"}'}.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    base=json.dumps({'entries':[record(name,data) for name,data in {**unchanged,**old}.items()]}).encode()
    (root/'dependencies/manifest.json').write_bytes(base)
    changes={'dependencies/runtime/engine.py':b'new engine','dependencies/export/library.py':b'new export library'}
    target=json.dumps({'entries':[record(name,data) for name,data in {**unchanged,**changes}.items()]}).encode()
    files={**changes,'app.py':b'new app','version.json':b'{"version":"0.4.0","channel":"stable"}',
           'dependencies/manifest.json':target,**(extra or {})}
    plan={'version':'0.4.0','source_runtime_sha256':hashlib.sha256(base).hexdigest(),
          'target_runtime_sha256':hashlib.sha256(target).hexdigest(),
          'entries':[record(name,data) for name,data in files.items()],
          'removed':[record('dependencies/runtime/unused.dll',old['dependencies/runtime/unused.dll'])]}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as zipped:
        for name,data in files.items():zipped.writestr(name,data)
        zipped.writestr('runtime-upgrade.json',json.dumps(plan))
    raw=buffer.getvalue()
    item={'name':apply.RUNTIME_UPDATE_NAME,'version':'0.4.0','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
          'source_runtime_sha256':plan['source_runtime_sha256'],'target_runtime_sha256':plan['target_runtime_sha256']}
    def download(item,folder,*args):
        folder.mkdir(parents=True,exist_ok=True);path=folder/item['name'];path.write_bytes(raw);return path
    return item,download,kept,files,raw

with tempfile.TemporaryDirectory(dir=ROOT/'work') as tmp:
    scratch=Path(tmp);root=scratch/'successful';root.mkdir()
    item,download,kept,files,raw=package(root)
    plan=apply.prepare_runtime_upgrade(item,root,downloader=download)
    assert (root/'app.py').read_bytes()==b'old app'
    result=apply.apply_update(plan)
    assert result['runtime_upgraded'] and result['removed']==1
    assert all((root/name).read_bytes()==data for name,data in {**kept,**files}.items())
    assert (root/'models/original.bin').read_bytes()==b'original model'
    assert not (root/'dependencies/runtime/unused.dll').exists()
    failed=scratch/'failed';failed.mkdir()
    item,download,kept,files,raw=package(failed)
    before={p.relative_to(failed).as_posix():p.read_bytes() for p in failed.rglob('*') if p.is_file()}
    plan=apply.prepare_runtime_upgrade(item,failed,downloader=download);replace=Path.replace
    def reject(self,*args,**kwargs):
        if self.name=='version.json.bvwa-update':raise PermissionError('test locked file')
        return replace(self,*args,**kwargs)
    with patch.object(Path,'replace',reject):
        try:apply.apply_update(plan);raise AssertionError('Failed install accepted')
        except PermissionError:pass
    assert all((failed/name).read_bytes()==data for name,data in before.items())
    assert not (failed/'dependencies/export/library.py').exists()
    for index,extra in enumerate(({'config.json':b'wrong settings'},{'../outside.py':b'escape'},
                                  {'dependencies/optional/model.bin':b'wrong model'})):
        bad=scratch/str(index);bad.mkdir();item,download,kept,files,raw=package(bad,extra)
        try:apply.prepare_runtime_upgrade(item,bad,downloader=download);raise AssertionError('Unsafe file accepted')
        except ValueError:pass
        assert all((bad/name).read_bytes()==data for name,data in kept.items())
    assert not (scratch/'outside.py').exists()
    folder=scratch/'download';folder.mkdir();offset=len(raw)//2
    (folder/(apply.RUNTIME_UPDATE_NAME+'.partial')).write_bytes(raw[:offset])
    response=io.BytesIO(raw[offset:]);response.status=206
    response.headers={'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'}
    opener=Mock();opener.open.return_value=response
    with patch.object(apply.urllib.request,'build_opener',return_value=opener), \
         patch.object(apply.urllib.request,'ProxyHandler',wraps=apply.urllib.request.ProxyHandler) as proxies:
        received=apply._runtime_download(item,folder,lambda *args:None,None,'official')
        proxies.assert_called_once_with({})
        assert received.read_bytes()==raw
        assert opener.open.call_args.args[0].headers['Range']==f'bytes={offset}-'
    # Runtime-specific assets have separate names, caches and resumable ranges.
    variant = dict(item, name='BVWA-Runtime-Upgrade-v0.4.0.zip')
    mirror_folder = scratch/'mirror-download';mirror_folder.mkdir()
    (mirror_folder/(variant['name']+'.partial')).write_bytes(raw[:offset])
    response=io.BytesIO(raw[offset:]);response.status=206
    response.headers={'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'}
    opener=Mock();opener.open.side_effect=[OSError('mirror interrupted'),response]
    with patch.object(apply.urllib.request,'build_opener',return_value=opener):
        received=apply._runtime_download(variant,mirror_folder,lambda *args:None,None,'mirror')
    assert received.name==variant['name'] and received.read_bytes()==raw
    requests=[call.args[0] for call in opener.open.call_args_list]
    assert requests[0].full_url.startswith('https://api.gitproxy.dev/github.com/')
    assert requests[1].full_url.startswith('https://github.com/')
    assert all(request.full_url.endswith('/'+variant['name']) and request.headers['Range']==f'bytes={offset}-' for request in requests)
    for bad_name in ('../BVWA-Runtime-Upgrade.zip','other.zip','BVWA-Runtime-Upgrade-v0.4.0.zip/../../secret'):
        try:apply._runtime_name(dict(item,name=bad_name));raise AssertionError('Unsafe delta name accepted')
        except ValueError:pass
    url='https://github.com/example/releases/download/v0.4.1/file.zip'
    assert apply._runtime_routes(url,'auto')==[url,'https://api.gitproxy.dev/'+url.removeprefix('https://')]
    assert apply._runtime_routes(url,'direct')==[url]
    from PyQt5.QtWidgets import QApplication
    import upgrade_bootstrap
    app=QApplication([])
    (scratch/'version.json').write_text(json.dumps({'runtime_upgrade':item}),encoding='utf-8')
    with patch.object(upgrade_bootstrap.QTimer,'singleShot'):
        window=upgrade_bootstrap.UpgradeDialog(scratch)
    assert window.source.currentData()=='auto'
    with patch.object(upgrade_bootstrap.subprocess,'Popen') as spawn:
        window.install(plan);assert window.install_started and spawn.called
    quitting=Mock()
    with patch.object(upgrade_bootstrap.QApplication,'instance',return_value=SimpleNamespace(quit=quitting)):
        window.finished();quitting.assert_called_once()
    window.close()
    receipt=scratch/'cache/updates/last-result.json';receipt.parent.mkdir(parents=True,exist_ok=True)
    receipt.write_text(json.dumps({'state':'failed','version':'0.4.0','error':'test locked file'}),encoding='utf-8')
    with patch.object(upgrade_bootstrap.QTimer,'singleShot') as schedule:
        failed_window=upgrade_bootstrap.UpgradeDialog(scratch)
        assert not schedule.called and failed_window.retry.isEnabled()
        assert '上次安装未完成' in failed_window.message.text()
        failed_window.close()
    for saved,expected in (('mirror','mirror'),('direct','direct'),('official','direct'),('auto','auto'),('unknown','auto')):
        (scratch/'config.json').write_text(json.dumps({'download_source':saved}),encoding='utf-8')
        with patch.object(upgrade_bootstrap.QTimer,'singleShot'):
            source_window=upgrade_bootstrap.UpgradeDialog(scratch)
        assert source_window.source.currentData()==expected
        source_window.close()
    from tools.build_runtime_upgrade import build_bootstrap
    bootstrap_folder=scratch/'bootstrap';bootstrap_folder.mkdir()
    for filename in ('BVWA-App-Update.zip','BVWA-Program-Update.zip'):
        built=build_bootstrap(bootstrap_folder,variant,filename)
        assert built['name']==filename
        with zipfile.ZipFile(bootstrap_folder/filename) as zipped:
            assert json.loads(zipped.read('version.json'))['runtime_upgrade']['name']==variant['name']
print('PASS: runtime hashes, user/model preservation, rollback, unsafe paths, direct resumed download, startup helper handoff')
