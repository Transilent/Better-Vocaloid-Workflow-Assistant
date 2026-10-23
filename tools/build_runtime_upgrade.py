"""Build canonical VWA runtime deltas and optional legacy first-hop aliases."""
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

LAUNCHER_MIGRATION = {'from':'BVWA.exe','to':'VWA.exe','legacy_alias':True}
BOOTSTRAP_PATTERN = r'(VWA|BVWA)-(App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip'


def _bootstrap_kind(name):
    from tools.update_apply import program_name
    program_name({'name':name})
    match = re.fullmatch(BOOTSTRAP_PATTERN,name)
    if match is None:
        raise ValueError('Invalid first-hop filename')
    return match.groups()


def build_bootstrap(folder, item, name=None, *, filename=None):
    if name is not None and filename is not None:
        raise TypeError('Pass either name or filename, not both')
    archive_name = filename if filename is not None else (name or 'VWA-App-Update.zip')
    from tools.update_apply import program_name
    program_name({'name':archive_name})
    folder = Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    version = item['version']
    base_sha = item['source_runtime_sha256']
    first_hop = {'app.py':(ROOT/'src/upgrade_bootstrap.py').read_bytes(),
                 'tools/update_apply.py':(ROOT/'tools/update_apply.py').read_bytes(),
                 'version.json':json.dumps({'version':version,'channel':'stable','upgrade_pending':True,
                                            'runtime_upgrade':item},ensure_ascii=False,indent=2).encode('utf-8')}
    records = [{'file':file_name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
               for file_name,data in first_hop.items()]
    archive = folder/archive_name
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for file_name,data in first_hop.items(): zipped.writestr(file_name,data)
        zipped.writestr('update-inventory.json',json.dumps({'version':version,'runtime_sha256':base_sha,'entries':records},indent=2))
    return {'name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),'runtime_sha256':base_sha}


def build(folder, base_ref='v0.3.0', *, program_variant=None, bootstrap_name=None,
          legacy_bootstrap_name=None):
    from tools.verify_source_layout import verify
    verify(ROOT)
    folder = Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    canonical_name = bootstrap_name or ('VWA-Program-Update.zip' if program_variant else 'VWA-App-Update.zip')
    canonical_prefix, canonical_kind = _bootstrap_kind(canonical_name)
    if canonical_prefix != 'VWA':
        raise ValueError('Canonical first-hop filename must use the VWA protocol prefix')
    if legacy_bootstrap_name is not None:
        legacy_prefix, legacy_kind = _bootstrap_kind(legacy_bootstrap_name)
        if legacy_prefix != 'BVWA':
            raise ValueError('Legacy first-hop alias must use the BVWA protocol prefix')
        if legacy_kind != canonical_kind:
            raise ValueError('Legacy first-hop alias must match the canonical update kind')
        if legacy_bootstrap_name == canonical_name:
            raise ValueError('Legacy first-hop alias must differ from the canonical filename')
    if program_variant is not None:
        if not re.fullmatch(r'\d+\.\d+\.\d+', program_variant):
            raise ValueError('Invalid source version for program bootstrap')
        base_version = json.loads(subprocess.check_output(
            ['git','show',base_ref+':packaging/version.json'],cwd=ROOT))['version']
        if base_version != program_variant:
            raise ValueError('Source ref does not match the bootstrap version')
        from updater import is_newer
        target_version = json.loads((ROOT/'packaging/version.json').read_text(encoding='utf-8'))['version']
        if not is_newer(target_version, program_variant):
            raise ValueError('A bootstrap must upgrade to a newer version than its source client.')
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
    from tools.update_apply import validate_file, RUNTIME_PROGRAM_FILES
    entry_names = {entry['file'] for entry in entries}
    if {'VWA.exe','BVWA.exe'}.issubset(entry_names):
        raise ValueError('A runtime upgrade cannot install both canonical and legacy launchers')
    for entry in entries:
        if entry['file'] not in target and entry['file'] != 'dependencies/manifest.json' and entry['file'] not in RUNTIME_PROGRAM_FILES:
            validate_file(entry)
    version = json.loads((ROOT/'packaging/version.json').read_text(encoding='utf-8'))['version']
    plan = {'format':1,'version':version,'source_runtime_sha256':base_sha,'target_runtime_sha256':target_sha,
            'entries':entries,'removed':[entry for name,entry in base.items() if name not in target]}
    if 'VWA.exe' in entry_names:
        plan['launcher_migration'] = dict(LAUNCHER_MIGRATION)
    runtime = folder/(f'VWA-Runtime-Upgrade-v{program_variant}.zip' if program_variant else 'VWA-Runtime-Upgrade.zip')
    with zipfile.ZipFile(runtime,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for entry in entries: zipped.write(sources[entry['file']],entry['file'])
        zipped.writestr('runtime-upgrade.json',json.dumps(plan,ensure_ascii=False,indent=2))
    item = {'name':runtime.name,'version':version,'bytes':runtime.stat().st_size,'sha256':digest(runtime),
            'source_runtime_sha256':base_sha,'target_runtime_sha256':target_sha}
    update = build_bootstrap(folder,item,filename=canonical_name)
    result = {'update':update,'runtime_upgrade':item,'changed_dependency_files':len(changed),
              'changed_dependency_bytes':sum(entry['bytes'] for entry in changed.values()),
              'removed_files':len(plan['removed'])}
    if legacy_bootstrap_name is not None:
        canonical_archive = folder/update['name'];legacy_archive = folder/legacy_bootstrap_name
        with canonical_archive.open('rb') as source,legacy_archive.open('xb') as target:
            for block in iter(lambda:source.read(1024**2),b''): target.write(block)
        if legacy_archive.stat().st_size != update['bytes'] or digest(legacy_archive) != update['sha256']:
            raise ValueError('Legacy first-hop alias is not byte-identical to the canonical bootstrap')
        result['legacy_update'] = dict(update,name=legacy_archive.name)
    report_name = f'runtime-upgrade-v{program_variant}.build.json' if program_variant else 'runtime-upgrade.build.json'
    (folder/report_name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--base-ref',default='v0.3.0')
    parser.add_argument('--program-variant',help='Source version for the program-update bootstrap, e.g. 0.4.0')
    parser.add_argument('--bootstrap-name',help='Optional versioned canonical VWA first-hop filename')
    parser.add_argument('--legacy-bootstrap-name',help='Optional byte-identical BVWA first-hop alias')
    parser.add_argument('--user-test-confirmed',action='store_true')
    args = parser.parse_args()
    if not args.user_test_confirmed: parser.error('User confirmation is required before building release archives.')
    build(args.output_dir,args.base_ref,program_variant=args.program_variant,bootstrap_name=args.bootstrap_name,
          legacy_bootstrap_name=args.legacy_bootstrap_name)
