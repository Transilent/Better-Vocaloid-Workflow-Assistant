"""Repair entry backend, active tasks, direct metadata and actual no-console EXE."""
import contextlib
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from unittest.mock import Mock,patch

APP=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(APP/'tools'),str(APP)]
import update_bridge as bridge
import update_apply as helper
from tools.build_update_bridge import build
from common import native_path

with tempfile.TemporaryDirectory(dir=APP/'work',prefix='bridge-') as directory:
    folder=Path(directory);root=folder/'installed';(root/'dependencies').mkdir(parents=True)
    raw=b'{"entries":[]}';(root/'dependencies/manifest.json').write_bytes(raw)
    (root/'version.json').write_text('{"version":"0.3.1"}',encoding='utf-8')
    fingerprint=hashlib.sha256(raw).hexdigest()
    item={'name':'BVWA-App-Update-v0.3.0.zip','bytes':123,'sha256':'1'*64,'runtime_sha256':fingerprint}
    runtime={'name':'BVWA-Runtime-Upgrade-v0.3.0.zip','version':'9.9.9','bytes':321,'sha256':'2'*64,
             'source_runtime_sha256':fingerprint,'target_runtime_sha256':'3'*64}
    catalog={'version':'9.9.9','updates':[{'source_version':'0.3.0','runtime_sha256':fingerprint,'update':item,'runtime_upgrade':runtime}]}
    checked=bridge.check_installation(root,reader=lambda source:catalog)
    assert checked['available'] and checked['route']['source_version']=='0.3.0', 'Runtime identity must cover patch versions too'
    with patch.object(helper,'installation_busy',return_value='fixture active task'),patch.object(helper,'prepare_catalog_update') as preparing:
        try:bridge.prepare(checked);raise AssertionError('Active installation accepted')
        except RuntimeError as error:assert 'active task' in str(error)
        preparing.assert_not_called()
    (root/'version.json').write_text('{"version":"9.9.9"}',encoding='utf-8')
    assert not bridge.check_installation(root,reader=lambda source:catalog)['available']
    (root/'version.json').write_text('{"version":"9.9.9","upgrade_pending":true}',encoding='utf-8')
    assert bridge.check_installation(root,reader=lambda source:catalog)['available'], 'A half-finished bootstrap must remain recoverable'
    (root/'version.json').write_text('[]',encoding='utf-8')
    assert bridge.check_installation(root,reader=lambda source:catalog)['available'], 'The identified runtime can repair a bad version record'
    (root/'version.json').write_text('{"version":"10.0.0"}',encoding='utf-8')
    try:bridge.check_installation(root,reader=lambda source:catalog);raise AssertionError('Downgrade accepted')
    except ValueError:pass
    response=io.BytesIO(json.dumps(catalog).encode());opener=Mock(return_value=response)
    result=helper.read_release_catalog('mirror',opener=opener)
    assert result['version']=='9.9.9'
    assert opener.call_args.args[0].full_url.startswith('https://api.gitproxy.dev/github.com/')
    response=io.BytesIO(json.dumps(catalog).encode());network=Mock();network.open.return_value=response
    with patch.object(helper.urllib.request,'build_opener',return_value=network), \
         patch.object(helper.urllib.request,'ProxyHandler',wraps=helper.urllib.request.ProxyHandler) as proxy:
        helper.read_release_catalog('direct');proxy.assert_called_once_with({})
    # An actual byte-range lock on a remembered custom task prevents installation.
    custom=folder/'custom jobs';job=custom/'song';job.mkdir(parents=True)
    (job/'request.json').write_text('{}',encoding='utf-8')
    (root/'cache').mkdir(exist_ok=True)
    (root/'cache/workspace.json').write_text(json.dumps({'job_roots':[str(custom)]}),encoding='utf-8')
    import msvcrt
    with (job/'run.lock').open('w+b') as lock:
        lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        assert '音乐任务' in helper.installation_busy(root)
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    assert helper.installation_busy(root)==''

    executable=folder/'VWA-Updater.exe';info=build(executable,'9.9.9')
    assert info['bytes']<4*1024**2
    data=executable.read_bytes();offset=struct.unpack_from('<I',data,0x3c)[0]
    assert struct.unpack_from('<H',data,offset+24+68)[0]==2
    # Spaces and Unicode in the selected folder exercise the real C# quoting.
    # Keep the installation root short enough for the old native Python/Qt
    # loader; long update/cache paths are tested in test_update_catalog.py.
    ui_temporary=tempfile.TemporaryDirectory(prefix='界面 空格-',dir=APP.parent)
    ui_root=Path(ui_temporary.name)
    link=ui_root/'dependencies'
    import _winapi
    linked=True
    try:
        _winapi.CreateJunction(str(APP/'dependencies'),str(link))
    except PermissionError:
        # Restricted Windows sessions can deny junction creation. Use an owned
        # runtime copy so the same real EXE/Unicode-path check still runs.
        linked=False
        shutil.copytree(native_path(APP/'dependencies/vocal2midi/python'),
                        native_path(link/'vocal2midi/python'))
        shutil.copy2(APP/'dependencies/manifest.json',link/'manifest.json')
    (ui_root/'version.json').write_text('{"version":"0.3.1"}',encoding='utf-8')
    (ui_root/'config.json').write_text('{"download_source":"mirror"}',encoding='utf-8')
    try:
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',BVWA_TESTING='1')
        for flag in ('--check','--smoke-test'):
            completed=subprocess.run([str(executable),'--root',str(ui_root),flag],env=env,timeout=60)
            log=(ui_root/'cache/updates/compatibility.log').read_text(encoding='utf-8')
            assert completed.returncode==0,log[-5000:]
        assert '"console_attached": false' in log and '"network_requested": false' in log
        assert 'Compatibility updater GUI ready; no network request.' in log
        assert not list((ui_root/'cache/updates').glob('ct-*')), 'The launcher must remove its extracted payload'
        preview=ui_root/'cache/updates/compatibility-preview.png'
        assert preview.is_file()
        target=APP/'work/test-results/compatibility-updater.png';target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(preview,target)
        assert (ui_root/'config.json').read_text(encoding='utf-8')=='{"download_source":"mirror"}'
    finally:
        if linked:
            assert link.is_junction() and link.resolve()==(APP/'dependencies').resolve()
            os.rmdir(link)
        else:
            assert not link.is_symlink() and link.resolve().is_relative_to(ui_root.resolve())
            shutil.rmtree(native_path(ui_root))
        ui_temporary.cleanup()
    # A delivery executable still requires the user's testing confirmation.
    result=subprocess.run([sys.executable,'-B',str(APP/'tools/build_update_bridge.py'),'--output',str(folder/'not-built.exe')],capture_output=True,timeout=15)
    assert result.returncode==2 and not (folder/'not-built.exe').exists()
print('PASS: legacy repair selection, incomplete migration recovery, no downgrade, proxy-free metadata, real task lock, embedded GUI EXE, Unicode/spaces, no console/network and release gate')
