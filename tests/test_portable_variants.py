"""Pinned JPN omission, full-manifest identity and separate portable metadata."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes', 'Use the isolated runner'
from common import native_path
from tools import build_portable as builder

def record(name,data):
    return {'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

# Selection uses the real pinned inventory; exact file copies below use tiny
# fixtures. The general test sandbox intentionally omits dependency __pycache__
# entries, so it is not a physical copy of every distributable input.
script=APP/'tools/build_portable.py'
actual_sources,actual_dependencies,actual_omitted=builder.package_selection('jpn')
assert len(actual_omitted)==3 and sum(e['bytes'] for e in actual_omitted)>4*1024**3
assert not any('Qwen3-ASR-1.7B-dml/' in name for name in set(actual_sources)|set(actual_dependencies))
assert any('/romajiASR/' in name for name in actual_dependencies)
output=APP/'work/gated-JPN.zip'
gate=subprocess.run([sys.executable,'-B',str(script),'--output',str(output),'--variant','jpn'],
                    cwd=APP,capture_output=True,timeout=15)
assert gate.returncode!=0 and b'user testing' in gate.stderr.lower() and not output.exists()

with tempfile.TemporaryDirectory(dir=native_path(APP/'work/temp'),prefix='pv-') as folder:
    root=Path(folder)
    prefix='dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/'
    chinese={prefix+name:('pinned '+name).encode() for name in ('encoder.onnx','frontend.onnx','llm.gguf')}
    shared={'dependencies/vocal2midi/experiments/romajiASR/japanese.onnx':b'original japanese',
            'dependencies/vocal2midi/runtime.dll':b'original runtime'}
    payloads={**chinese,**shared}
    for name,data in payloads.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    manifest=json.dumps({'format':1,'entries':[record(n,d) for n,d in payloads.items()]}).encode()
    (root/'dependencies/manifest.json').write_bytes(manifest)
    (root/'components').mkdir()
    spec={'files':[record(n,d) for n,d in chinese.items()],'bytes':sum(map(len,chinese.values()))}
    (root/'components/runtime-features.json').write_text(json.dumps({'features':{'chinese':spec}}))
    (root/'app.py').write_bytes(b'# source')
    (root/'assets').mkdir()
    (root/'assets/日本語.txt').write_bytes('VWA 日本語'.encode('utf-8'))
    (root/'config.example.json').write_bytes(b'{"download_source":"auto"}')
    sources={'app.py':'app.py','config.json':'config.example.json','assets/日本語.txt':'assets/日本語.txt',
             'dependencies/manifest.json':'dependencies/manifest.json',
             'components/runtime-features.json':'components/runtime-features.json'}
    original={name:(root/name).read_bytes() for name in payloads}
    with patch.object(builder,'ROOT',root),patch.object(builder,'package_sources',return_value=sources), \
            patch('tools.verify_source_layout.verify'):
        full_plan=builder.portable_plan()
        jpn_plan=builder.portable_plan('jpn')
        assert full_plan['runtime_sha256']==jpn_plan['runtime_sha256']==hashlib.sha256(manifest).hexdigest()
        assert full_plan['files']-jpn_plan['files']==3
        assert full_plan['bytes']-jpn_plan['bytes']==sum(map(len,chinese.values()))
        try:
            builder.build(root/'wrong.zip',root,variant='jpn')
            raise AssertionError('Missing JPN filename suffix accepted')
        except ValueError:pass
        assert not (root/'wrong.zip').exists()
        full=root/'full.zip';jpn=root/'portable-JPN.zip';reused=root/'reused-JPN.zip'
        full_report=builder.build(full,root)
        jpn_report=builder.build(jpn,root,variant='jpn')
        reuse_report=builder.update_from_zip(reused,full,variant='jpn')
        legacy=root/'legacy.zip'
        migrated=root/'migrated.zip'
        reversed_layout=root/'reversed.zip'
        builder.build(legacy,root,layout_root='BVWA')
        builder.update_from_zip(migrated,legacy)
        builder.update_from_zip(reversed_layout,full,layout_root='BVWA')
        for path in (full,jpn,reused,legacy,migrated,reversed_layout):
            layout='BVWA' if path in (legacy,reversed_layout) else 'VWA'
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                assert all(name.startswith(layout+'/') for name in archive.namelist())
                inventory=json.loads(archive.read(layout+'/release-inventory.json'))
                assert inventory['layout_root']==layout
                names={entry['file'] for entry in inventory['entries']}
                assert archive.read(layout+'/dependencies/manifest.json')==manifest
                assert archive.read(layout+'/assets/日本語.txt')=='VWA 日本語'.encode('utf-8')
                for name,data in shared.items():assert archive.read(layout+'/'+name)==data
                for entry in inventory['entries']:
                    data=archive.read(layout+'/'+entry['file'])
                    assert entry==record(entry['file'],data)
                if path not in (jpn,reused):
                    assert set(chinese).issubset(names)
                else:
                    assert not set(chinese)&names and inventory['variant']=='jpn'
                    assert {entry['file'] for entry in inventory['omitted']}==set(chinese)
        assert reuse_report['reused_compressed_files']==jpn_plan['files']
        assert all((root/name).read_bytes()==data for name,data in original.items())
        assert (root/'dependencies/manifest.json').read_bytes()==manifest
        # Never let JPN metadata silently replace the full/update catalog.
        metadata=builder.write_portable_metadata(jpn_report,root/'JPN.portable.json')
        assert metadata['portable_release']['variant']=='jpn'
        for operation in (lambda:builder.write_release_metadata(jpn_report,root),
                          lambda:builder.write_portable_metadata(jpn_report,root/'release-assets.json')):
            try:operation();raise AssertionError('JPN metadata replaced shared catalog')
            except ValueError:pass
        assert not (root/'release-assets.json').exists()
        # Mismatched model pins and aliases cannot silently leak Qwen into JPN.
        altered=json.loads(json.dumps(spec));altered['files'][0]['sha256']='0'*64
        (root/'components/runtime-features.json').write_text(json.dumps({'features':{'chinese':altered}}))
        try:builder.package_selection('jpn');raise AssertionError('Mismatched model pin accepted')
        except ValueError:pass
        (root/'components/runtime-features.json').write_text(json.dumps({'features':{'chinese':spec}}))
        sources['models/alias.onnx']=next(iter(chinese))
        try:builder.package_selection('jpn');raise AssertionError('Aliased Qwen payload accepted')
        except ValueError:pass

print('PASS: exact JPN omission, original Japanese payloads, unchanged full manifest/pins, archive CRC/SHA, verified ZIP reuse, separate metadata and real build gate')
