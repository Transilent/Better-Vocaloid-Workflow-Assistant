"""Pinned JPN omission, full-manifest identity and separate portable metadata."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
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

def invoke_main(arguments):
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            code=builder.main(arguments)
        except SystemExit as exc:
            return exc.code,captured.getvalue()
    return code,captured.getvalue()

# Selection uses the real pinned inventory; exact file copies below use tiny
# fixtures. The general test sandbox intentionally omits dependency __pycache__
# entries, so it is not a physical copy of every distributable input.
script=APP/'tools/build_portable.py'
actual_sources,actual_dependencies,actual_omitted=builder.package_selection('jpn')
assert len(actual_omitted)==3 and sum(e['bytes'] for e in actual_omitted)>4*1024**3
assert not any('Qwen3-ASR-1.7B-dml/' in name for name in set(actual_sources)|set(actual_dependencies))
assert any('/romajiASR/' in name for name in actual_dependencies)
# These docs are installed at the archive root, not beneath docs/. Resolve
# their links in that installed layout rather than against the source tree.
for installed_readme in ('README.md', 'README.zh-CN.md'):
    text=(APP/actual_sources[installed_readme]).read_text(encoding='utf-8')
    references=re.findall(r'\]\(([^)]+)\)|\bsrc="([^"]+)"', text)
    for pair in references:
        target=next(value for value in pair if value).split('#',1)[0]
        if target and not re.match(r'^(?:https?://|mailto:)',target):
            assert target in actual_sources or target in actual_dependencies, (installed_readme,target)
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
        # Candidate builds require an explicit confirmation mode and isolated
        # output, identify themselves as unaccepted, and cannot create the
        # formal release catalog. The formal gate still creates a normal build.
        refused=root/'candidates/refused.zip'
        code,_=invoke_main(['--output',str(refused)])
        assert code!=0 and not refused.exists()
        outside=root/'plain/VWA-Windows-x64.zip'
        code,_=invoke_main(['--candidate-build','--output',str(outside)])
        assert code!=0 and not outside.exists()
        both=root/'candidates/both.zip'
        code,_=invoke_main(['--candidate-build','--user-test-confirmed','--output',str(both)])
        assert code!=0 and not both.exists()
        formal_catalog=root/'candidates/release-assets.JSON'
        code,_=invoke_main(['--candidate-build','--output',str(root/'candidates/catalog.zip'),
                            '--portable-metadata',str(formal_catalog)])
        assert code!=0 and not formal_catalog.exists()

        candidate=root/'candidates/local/VWA-Windows-x64.zip'
        code,output_text=invoke_main(['--candidate-build','--output',str(candidate),'--split-mib','1'])
        assert code==0,output_text
        candidate_report_path=candidate.with_suffix('.build.json')
        candidate_marker_path=Path(str(candidate)+'.candidate.json')
        candidate_report=json.loads(candidate_report_path.read_text(encoding='utf-8'))
        candidate_marker=json.loads(candidate_marker_path.read_text(encoding='utf-8'))
        assert candidate_marker['build_kind']=='candidate'
        assert candidate_marker['candidate']=={
            'status':'unaccepted','manual_acceptance':'pending','user_test_confirmed':False,
            'published':False,'release_ready':False,'release_assets_manifest_created':False}
        assert 'UNACCEPTED LOCAL CANDIDATE' in candidate_marker['notice']
        assert candidate_report['candidate']==candidate_marker['candidate']
        assert candidate_marker['parts'][0]['name']==candidate.name+'.001'
        assert candidate_marker['build_report']['sha256']==hashlib.sha256(candidate_report_path.read_bytes()).hexdigest()
        assert not (candidate.parent/'release-assets.json').exists()

        formal=root/'releases/v1.1.0/VWA-Windows-x64.zip'
        code,_=invoke_main(['--user-test-confirmed','--output',str(formal)])
        assert code==0 and formal.is_file()
        assert not Path(str(formal)+'.candidate.json').exists()
        formal_build=json.loads(formal.with_suffix('.build.json').read_text(encoding='utf-8'))
        assert 'candidate' not in formal_build and 'build_kind' not in formal_build

        # A supplied digest reuses a complete base without a sidecar and does
        # not alter the historical source directory. The earlier reuse above
        # covers the compatible sidecar-only route.
        base_sha=hashlib.sha256(full.read_bytes()).hexdigest()
        full_sidecar=full.with_suffix(full.suffix+'.sha256')
        assert full_sidecar.is_file()
        full_sidecar.unlink()
        from_base=root/'candidates/from-1.0-JPN.zip'
        code,output_text=invoke_main(['--candidate-build','--output',str(from_base),
                                      '--variant','jpn','--base-zip',str(full),
                                      '--base-sha256',base_sha.upper()])
        assert code==0,output_text
        from_base_marker=json.loads(Path(str(from_base)+'.candidate.json').read_text(encoding='utf-8'))
        assert from_base_marker['base_sha256']==base_sha
        assert not full_sidecar.exists()

        bad_hash=root/'candidates/bad-base-JPN.zip'
        try:
            invoke_main(['--candidate-build','--output',str(bad_hash),'--variant','jpn',
                         '--base-zip',str(full),'--base-sha256','0'*64])
            raise AssertionError('Mismatched base SHA-256 accepted')
        except ValueError as exc:
            assert 'checksum mismatch' in str(exc).lower()
        assert not bad_hash.exists()

        malformed=root/'candidates/malformed-base-JPN.zip'
        code,_=invoke_main(['--candidate-build','--output',str(malformed),'--variant','jpn',
                            '--base-zip',str(full),'--base-sha256','0'*63])
        assert code!=0 and not malformed.exists()

        # Mismatched model pins and aliases cannot silently leak Qwen into JPN.
        altered=json.loads(json.dumps(spec));altered['files'][0]['sha256']='0'*64
        (root/'components/runtime-features.json').write_text(json.dumps({'features':{'chinese':altered}}))
        try:builder.package_selection('jpn');raise AssertionError('Mismatched model pin accepted')
        except ValueError:pass
        (root/'components/runtime-features.json').write_text(json.dumps({'features':{'chinese':spec}}))
        sources['models/alias.onnx']=next(iter(chinese))
        try:builder.package_selection('jpn');raise AssertionError('Aliased Qwen payload accepted')
        except ValueError:pass

print('PASS: exact JPN omission, original Japanese payloads, unchanged full manifest/pins, archive CRC/SHA, verified ZIP reuse with sidecar or explicit SHA-256, separate metadata, candidate/formal split and confirmation gates')
