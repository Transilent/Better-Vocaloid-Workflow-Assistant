"""Runtime delta integrity, data preservation, rollback and startup handoff."""
import hashlib,io,json,os,shutil,stat,sys,tempfile,time,zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools import update_apply as apply

@contextmanager
def owned_temp(parent):
    path=Path(tempfile.mkdtemp(dir=parent))
    try:
        yield path
    finally:
        for attempt in range(40):
            def make_writable(function,target,error):
                if function is os.scandir:
                    return
                try:
                    os.chmod(target,stat.S_IWRITE)
                except FileNotFoundError:
                    return
                if function in (os.unlink,os.rmdir):
                    try:
                        function(target)
                    except OSError:
                        pass
            try:
                shutil.rmtree(path,onexc=make_writable)
                break
            except OSError as error:
                if attempt==39 or getattr(error,'winerror',None) not in (2,3,5,32,145):
                    raise
                time.sleep(0.25)

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

with owned_temp(ROOT/'work') as tmp:
    scratch=Path(tmp);root=scratch/'successful';root.mkdir()
    item,download,kept,files,raw=package(root)
    plan=apply.prepare_runtime_upgrade(item,root,downloader=download)
    assert (root/'app.py').read_bytes()==b'old app'
    result=apply.apply_update(plan)
    assert result['runtime_upgraded'] and result['removed']==1
    assert all((root/name).read_bytes()==data for name,data in {**kept,**files}.items())
    assert (root/'models/original.bin').read_bytes()==b'original model'
    assert not (root/'dependencies/runtime/unused.dll').exists()
    # A same-size corruption of a target-manifest entry that this delta does not
    # replace must fail before staging and again before applying any file.
    pre_corrupt=scratch/'pre-corrupt';pre_corrupt.mkdir()
    item,download,kept,files,raw=package(pre_corrupt)
    original=(pre_corrupt/'models/original.bin').read_bytes()
    (pre_corrupt/'models/original.bin').write_bytes(b'corrupt! model')
    assert len((pre_corrupt/'models/original.bin').read_bytes())==len(original)
    try:
        apply.prepare_runtime_upgrade(item,pre_corrupt,downloader=download)
        raise AssertionError('Same-size unchanged corruption accepted during prepare')
    except ValueError as error:
        assert '完整便携包恢复' in str(error)
    assert (pre_corrupt/'models/original.bin').read_bytes()==b'corrupt! model'
    assert (pre_corrupt/'app.py').read_bytes()==b'old app'
    (pre_corrupt/'models/original.bin').unlink()
    try:
        apply.prepare_runtime_upgrade(item,pre_corrupt,downloader=download)
        raise AssertionError('Missing unchanged target accepted during prepare')
    except ValueError as error:
        assert '完整便携包恢复' in str(error)
    assert not (pre_corrupt/'models/original.bin').exists()
    assert (pre_corrupt/'app.py').read_bytes()==b'old app'

    late_corrupt=scratch/'late-corrupt';late_corrupt.mkdir()
    item,download,kept,files,raw=package(late_corrupt)
    plan=apply.prepare_runtime_upgrade(item,late_corrupt,downloader=download)
    untouched={name:(late_corrupt/name).read_bytes() for name in
               ('app.py','version.json','dependencies/manifest.json','dependencies/runtime/engine.py')}
    (late_corrupt/'models/original.bin').write_bytes(b'corrupt! model')
    try:
        apply.apply_update(plan)
        raise AssertionError('Same-size unchanged corruption accepted during apply')
    except ValueError as error:
        assert '完整便携包恢复' in str(error)
    assert all((late_corrupt/name).read_bytes()==data for name,data in untouched.items())
    assert (late_corrupt/'models/original.bin').read_bytes()==b'corrupt! model'
    assert plan.is_file(), 'A rejected apply must retain its verified stage'

    reparse=scratch/'reparse-target';reparse.mkdir()
    item,download,kept,files,raw=package(reparse)
    plan=apply.prepare_runtime_upgrade(item,reparse,downloader=download)
    target_path=reparse/'models/original.bin'
    real_lstat=Path.lstat
    def reparse_lstat(self):
        info=real_lstat(self)
        if Path(self)==target_path:
            return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=1024)
        return info
    with patch.object(Path,'lstat',reparse_lstat):
        try:
            apply.apply_update(plan)
            raise AssertionError('Reparse-point unchanged target accepted')
        except ValueError as error:
            assert '链接或重解析点' in str(error)
    assert (reparse/'app.py').read_bytes()==b'old app'
    assert plan.is_file()

    cancelled=scratch/'cancelled-validation';cancelled.mkdir()
    item,download,kept,files,raw=package(cancelled)
    cancel=[False]
    def report(done,total,message):
        if '正在核对原有运行库' in message: cancel[0]=True
    try:
        apply.prepare_runtime_upgrade(item,cancelled,report,lambda:cancel[0],downloader=download)
        raise AssertionError('Cancellation before unchanged SHA validation was ignored')
    except InterruptedError:
        pass
    assert (cancelled/'models/original.bin').read_bytes()==b'original model'
    failed=scratch/'failed';failed.mkdir()
    item,download,kept,files,raw=package(failed)
    before={p.relative_to(failed).as_posix():p.read_bytes() for p in failed.rglob('*') if p.is_file()}
    plan=apply.prepare_runtime_upgrade(item,failed,downloader=download);replace=Path.replace
    def reject(self,*args,**kwargs):
        if self.name in ('version.json.vwa-update', 'version.json.bvwa-update'):raise PermissionError('test locked file')
        return replace(self,*args,**kwargs)
    with patch.object(Path,'replace',reject):
        try:apply.apply_update(plan);raise AssertionError('Failed install accepted')
        except PermissionError:pass
    assert all((failed/name).read_bytes()==data for name,data in before.items())
    assert not (failed/'dependencies/export/library.py').exists()
    first_backups=set(plan.parent.glob('backup-*'))
    assert len(first_backups)==1
    retried=apply.apply_update(plan)
    assert retried['state']=='passed' and retried['runtime_upgraded']
    assert len(set(plan.parent.glob('backup-*'))-first_backups)==1
    assert all((failed/name).read_bytes()==data for name,data in {**kept,**files}.items())
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
    assert requests[1].full_url.startswith('https://gh-proxy.com/https://github.com/')
    assert all(request.full_url.endswith('/'+variant['name']) and request.headers['Range']==f'bytes={offset}-' for request in requests)
    for bad_name in ('../BVWA-Runtime-Upgrade.zip','other.zip','BVWA-Runtime-Upgrade-v0.4.0.zip/../../secret'):
        try:apply._runtime_name(dict(item,name=bad_name));raise AssertionError('Unsafe delta name accepted')
        except ValueError:pass
    url='https://github.com/example/releases/download/v0.4.1/file.zip'
    assert apply._runtime_routes(url,'auto')==[url,'https://api.gitproxy.dev/'+url.removeprefix('https://'),
                                             'https://gh-proxy.com/'+url,'https://ghfast.top/'+url]
    assert apply._runtime_routes(url,'direct')==[url]
    for name, prefix in (('gh-proxy','https://gh-proxy.com/'),('ghfast','https://ghfast.top/')):
        assert apply._runtime_routes(url,name) == [prefix+url,url]
    for name in ('hf-mirror','pypi-tuna','pypi-aliyun','pytorch-sjtu'):
        assert apply._runtime_routes(url,name)==[url], 'An inapplicable provider must retain direct downloads'
    for private_url in (url+'?token=private',url.replace('github.com','user:secret@github.com')):
        assert apply._runtime_routes(private_url,'mirror')==[private_url]
    # Runtime migration may complete at the same program version only for the
    # signed shape of a verified first-hop handoff. An older target and an
    # unmarked same-version plan are rejected before any installed file changes.
    def version_case(label, installed, marker=None):
        folder=scratch/label;folder.mkdir()
        item,download,kept,files,raw=package(folder)
        plan=apply.prepare_runtime_upgrade(item,folder,downloader=download)
        metadata={'version':installed}
        if marker is not None: metadata.update(marker)
        (folder/'version.json').write_text(json.dumps(metadata),encoding='utf-8')
        return folder,item,plan,metadata

    older,item,older_plan,_=version_case('older-runtime', '0.5.0')
    older_before={name:(older/name).read_bytes() for name in ('app.py','version.json','dependencies/manifest.json')}
    try:
        apply.apply_update(older_plan)
        raise AssertionError('Older runtime target applied')
    except ValueError as error:
        assert '不高于当前安装版本' in str(error)
    assert all((older/name).read_bytes()==data for name,data in older_before.items())
    assert older_plan.is_file()

    same,item,same_plan,_=version_case('same-runtime-unmarked', '0.4.0')
    same_before={name:(same/name).read_bytes() for name in ('app.py','version.json','dependencies/manifest.json')}
    try:
        apply.apply_update(same_plan)
        raise AssertionError('Unmarked same-version runtime target applied')
    except ValueError as error:
        assert '不高于当前安装版本' in str(error)
    assert all((same/name).read_bytes()==data for name,data in same_before.items())

    bootstrap,item,bootstrap_plan,_=version_case(
        'same-runtime-first-hop', '0.4.0',
        {'upgrade_pending':True,'runtime_upgrade':item})
    result=apply.apply_update(bootstrap_plan)
    assert result['state']=='passed' and result['runtime_upgraded']
    assert json.loads((bootstrap/'version.json').read_text())['version']=='0.4.0'
    assert not json.loads((bootstrap/'version.json').read_text()).get('upgrade_pending')

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
    for saved,expected in (('mirror','mirror'),('direct','direct'),('official','direct'),('auto','auto'),('unknown','auto'),
                           ('gh-proxy','gh-proxy'),('ghfast','ghfast'),('hf-mirror','hf-mirror'),
                           ('pypi-tuna','pypi-tuna'),('pypi-aliyun','pypi-aliyun'),('pytorch-sjtu','pytorch-sjtu')):
        (scratch/'config.json').write_text(json.dumps({'download_source':saved}),encoding='utf-8')
        with patch.object(upgrade_bootstrap.QTimer,'singleShot'):
            source_window=upgrade_bootstrap.UpgradeDialog(scratch)
        assert source_window.source.currentData()==expected
        source_window.close()
    for malformed in ([], {'state':'failed','version':item['version'],'error':123}):
        receipt.write_text(json.dumps(malformed), encoding='utf-8')
        with patch.object(upgrade_bootstrap.QTimer, 'singleShot'):
            recovered_window=upgrade_bootstrap.UpgradeDialog(scratch)
            recovered_window.close()
    from tools.build_runtime_upgrade import build_bootstrap
    bootstrap_folder=scratch/'bootstrap';bootstrap_folder.mkdir()
    for filename in ('BVWA-App-Update.zip','BVWA-Program-Update.zip'):
        built=build_bootstrap(bootstrap_folder,variant,filename)
        assert built['name']==filename
        with zipfile.ZipFile(bootstrap_folder/filename) as zipped:
            assert json.loads(zipped.read('version.json'))['runtime_upgrade']['name']==variant['name']
print('PASS: runtime hashes, user/model preservation, rollback, unsafe paths, direct resumed download, startup helper handoff')
