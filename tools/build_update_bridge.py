"""Build the small no-console compatibility updater; no inference runtime included."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAMES = {'VWA-Updater.exe', 'BVWA-Updater.exe'}


def build(output, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid compatibility-tool version')
    output = Path(output).resolve()
    if output.name not in TOOL_NAMES:
        raise ValueError('Compatibility updater must be named VWA-Updater.exe (legacy: BVWA-Updater.exe)')
    if output.exists(): raise FileExistsError(output)
    compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bridge-build-',dir=output.parent) as folder:
        source = Path(folder)/'UpdateBridge.cs'
        source.write_text((ROOT/'tools/windows/UpdateBridge.cs').read_text(encoding='utf-8').replace('__VERSION__',version+'.0'),encoding='utf-8')
        subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/utf8output','/codepage:65001',
            '/reference:System.Windows.Forms.dll','/win32icon:'+str(ROOT/'assets/app.ico'),
            '/resource:'+str(ROOT/'tools/update_bridge.py')+',BridgeScript',
            '/resource:'+str(ROOT/'tools/update_apply.py')+',UpdateInstaller','/out:'+str(output),str(source)],check=True,timeout=60)
    return {'name':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--user-test-confirmed',action='store_true')
    args=parser.parse_args()
    if not args.user_test_confirmed:parser.error('User confirmation is required before building a delivery executable.')
    if args.output.name not in TOOL_NAMES:
        parser.error('Use VWA-Updater.exe for the compatibility updater (BVWA-Updater.exe is accepted for legacy releases).')
    version=json.loads((ROOT/'packaging/version.json').read_text(encoding='utf-8'))['version']
    info=build(args.output,version)
    args.output.with_suffix('.build.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(info))
