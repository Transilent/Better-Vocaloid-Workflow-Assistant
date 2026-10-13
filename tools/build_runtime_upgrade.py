"""Build the 0.3.x-compatible first hop and the verified runtime delta."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_portable import package_sources, digest


def build_bootstrap(folder, item, name='BVWA-App-Update.zip'):
    if name not in ('BVWA-App-Update.zip', 'BVWA-Program-Update.zip'):
        raise ValueError('Unsupported bootstrap asset name')
    version = item['version']
    base_sha = item['source_runtime_sha256']
    first_hop = {'app.py':(ROOT/'src/upgrade_bootstrap.py').read_bytes(),
                 'tools/update_apply.py':(ROOT/'tools/update_apply.py').read_bytes(),
                 'version.json':json.dumps({'version':version,'channel':'stable','upgrade_pending':True,
                                            'runtime_upgrade':item},ensure_ascii=False,indent=2).encode('utf-8')}
    records = [{'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in first_hop.items()]
    archive = folder/name
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for name,data in first_hop.items(): zipped.writestr(name,data)
        zipped.writestr('update-inventory.json',json.dumps({'version':version,'runtime_sha256':base_sha,'entries':records},indent=2))
    return {'name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),'runtime_sha256':base_sha}


def build(folder, base_ref='v0.3.0', *, program_variant=None):
    folder = Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    if program_variant is not None:
        if not re.fullmatch(r'\d+\.\d+\.\d+', program_variant):
            raise ValueError('Invalid source version for program bootstrap')
        base_version = json.loads(subprocess.check_output(
            ['git','show',base_ref+':packaging/version.json'],cwd=ROOT))['version']
        if base_version != program_variant:
            raise ValueError('Source ref does not match the bootstrap version')
    base_raw = subprocess.check_output(['git','show',base_ref+':dependencies/manifest.json'],cwd=ROOT)
    target_path = ROOT/'dependencies/manifest.json'
    base_sha = hashlib.sha256(base_raw).hexdigest()
    target_sha = digest(target_path)
    base = {entry['file']:entry for entry in json.loads(base_raw)['entries']}
    target = {entry['file']:entry for entry in json.loads(target_path.read_text(encoding='utf-8'))['entries']}
    changed = {name:entry for name,entry in target.items() if base.get(name)!=entry}
    sources = {name:ROOT/source for name,source in package_sources().items()
               if name!='config.json' and (name not in target or name in changed)}
    sources.update({name:ROOT/name for name in changed})
    if program_variant:
        # The first hop replaces app.py with the migration window; always
        # restore the real app and commit version/manifest at the second hop.
        required = {'app.py','version.json','dependencies/manifest.json'} | set(changed)
        minimal = {}
        for name,path in sources.items():
            if name not in required:
                previous = subprocess.run(['git','show',base_ref+':'+path.relative_to(ROOT).as_posix()],
                                          cwd=ROOT,capture_output=True)
                if previous.returncode == 0 and hashlib.sha256(previous.stdout).hexdigest() == digest(path):
                    continue
            minimal[name] = path
        sources = minimal
    entries = [{'file':name,'bytes':path.stat().st_size,'sha256':digest(path)} for name,path in sorted(sources.items())]
    version = json.loads((ROOT/'packaging/version.json').read_text(encoding='utf-8'))['version']
    plan = {'format':1,'version':version,'source_runtime_sha256':base_sha,'target_runtime_sha256':target_sha,
            'entries':entries,'removed':[entry for name,entry in base.items() if name not in target]}
    runtime = folder/(f'BVWA-Runtime-Upgrade-v{program_variant}.zip' if program_variant else 'BVWA-Runtime-Upgrade.zip')
    with zipfile.ZipFile(runtime,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for entry in entries: zipped.write(sources[entry['file']],entry['file'])
        zipped.writestr('runtime-upgrade.json',json.dumps(plan,ensure_ascii=False,indent=2))
    item = {'name':runtime.name,'version':version,'bytes':runtime.stat().st_size,'sha256':digest(runtime),
            'source_runtime_sha256':base_sha,'target_runtime_sha256':target_sha}
    # Both old clients accept the narrow first hop. Each runtime receives its
    # own inventory/hash, then downloads only its matching dependency delta.
    update = build_bootstrap(folder, item, 'BVWA-Program-Update.zip' if program_variant else 'BVWA-App-Update.zip')
    result = {'update':update,'runtime_upgrade':item,'changed_dependency_files':len(changed),
              'changed_dependency_bytes':sum(entry['bytes'] for entry in changed.values()),
              'removed_files':len(plan['removed'])}
    report_name = f'runtime-upgrade-v{program_variant}.build.json' if program_variant else 'runtime-upgrade.build.json'
    (folder/report_name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--base-ref',default='v0.3.0')
    parser.add_argument('--program-variant',help='Source version for the program-update bootstrap, e.g. 0.4.0')
    parser.add_argument('--user-test-confirmed',action='store_true')
    args = parser.parse_args()
    if not args.user_test_confirmed: parser.error('User confirmation is required before building release archives.')
    build(args.output_dir,args.base_ref,program_variant=args.program_variant)
