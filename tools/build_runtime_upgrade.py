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
RUNTIME_PATTERN = r'(?:VWA|BVWA)-Runtime-Upgrade(?:-v\d+\.\d+\.\d+)?\.zip'
SOURCE_IDENTITY_FILES = ('app.py', 'updater.py', 'updates_ui.py', 'tools/update_apply.py')
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+')
CANDIDATE_ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
GIT_REF_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,255}')


def _bootstrap_kind(name):
    from tools.update_apply import program_name
    program_name({'name':name})
    match = re.fullmatch(BOOTSTRAP_PATTERN,name)
    if match is None:
        raise ValueError('Invalid first-hop filename')
    return match.groups()


def _git_blob(ref, path):
    """Read one frozen Git object without consulting the worktree."""
    if not isinstance(ref, str) or GIT_REF_PATTERN.fullmatch(ref) is None or '..' in ref:
        raise ValueError('Invalid frozen Git ref')
    try:
        return subprocess.check_output(['git','show',f'{ref}:{path}'], cwd=ROOT, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'Frozen source object is unavailable: {ref}:{path}') from error


def _json_blob(reader, ref, path):
    try:
        raw = reader(ref, path)
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError(f'Frozen JSON source is invalid: {ref}:{path}') from error
    if not isinstance(data, dict):
        raise ValueError(f'Frozen JSON source must be an object: {ref}:{path}')
    return data


def _package_map(data, *, source):
    entries = data.get('entries')
    if not isinstance(entries, list):
        raise ValueError('Portable source inventory lacks entries: ' + source)
    result = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('file'), str) or
                not isinstance(entry.get('source'), str) or not entry['file'] or not entry['source']):
            raise ValueError('Invalid portable source entry: ' + source)
        name = entry['file']
        if name in result:
            raise ValueError('Duplicate portable destination: ' + name)
        result[name] = entry['source']
    return result


def _manifest_records(raw):
    try:
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Frozen runtime manifest is invalid') from error
    entries = data.get('entries') if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError('Frozen runtime manifest lacks entries')
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('file'), str):
            raise ValueError('Frozen runtime manifest contains an invalid record')
        name = entry['file']
        if name in result:
            raise ValueError('Frozen runtime manifest contains duplicate files: ' + name)
        result[name] = entry
    return result


def _source_snapshot(ref, expected_version=None, *, reader=None):
    reader = reader or _git_blob
    version_data = _json_blob(reader, ref, 'packaging/version.json')
    version = version_data.get('version')
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError('Frozen source version is invalid: ' + ref)
    if expected_version is not None and version != expected_version:
        raise ValueError(f'Source ref {ref} contains version {version}, expected {expected_version}')
    runtime_raw = reader(ref, 'dependencies/manifest.json')
    runtime_sha = hashlib.sha256(runtime_raw).hexdigest()
    package_data = _json_blob(reader, ref, 'packaging/portable-files.json')
    return {
        'version': version,
        'ref': ref,
        'runtime_sha256': runtime_sha,
        'runtime_entries': _manifest_records(runtime_raw),
        'package_sources': _package_map(package_data, source=ref),
        'reader': reader,
    }


def _source_policy(snapshot):
    files = {}
    package_sources_map = snapshot['package_sources']
    for installed_name in SOURCE_IDENTITY_FILES:
        source_name = package_sources_map.get(installed_name)
        if not isinstance(source_name, str) or not source_name:
            raise ValueError('Frozen source lacks required identity file: ' + installed_name)
        files[installed_name] = hashlib.sha256(
            snapshot['reader'](snapshot['ref'], source_name)).hexdigest()
    return {
        'version': snapshot['version'],
        'runtime_sha256': snapshot['runtime_sha256'],
        'files': files,
    }


def source_identity(ref, version=None, *, reader=None):
    """Return the fixed installer identity for one frozen distribution source."""
    return _source_policy(_source_snapshot(ref, version, reader=reader))


def _target_snapshot():
    target_path = ROOT/'dependencies/manifest.json'
    runtime_sha = digest(target_path)
    return {
        'runtime_sha256': runtime_sha,
        'runtime_entries': _manifest_records(target_path.read_bytes()),
    }


def _runtime_changes(snapshots, target_entries):
    """Return only changed target records and source-only records."""
    if not snapshots:
        raise ValueError('At least one source manifest is required')
    changed = {name for name,entry in target_entries.items()
               if any(snapshot['runtime_entries'].get(name) != entry for snapshot in snapshots)}
    removed = [entry for name,entry in sorted(snapshots[0]['runtime_entries'].items())
               if name not in target_entries]
    return changed, removed


def _program_difference_union(target_sources, snapshots):
    """Return target program files needed by any allowed source.

    Runtime files are handled separately. Generated launchers cannot be read
    from Git, so VWA.exe is always refreshed through the explicit launcher
    migration and BVWA.exe is retained as its compatibility alias.
    """
    include, source_only = set(), set()
    for snapshot in snapshots:
        source_sources = snapshot['package_sources']
        for name, target_source in target_sources.items():
            if name == 'config.json':
                continue
            source_source = source_sources.get(name)
            if name == 'VWA.exe':
                include.add(name)
                continue
            if source_source is None:
                include.add(name)
                continue
            try:
                source_data = snapshot['reader'](snapshot['ref'], source_source)
            except ValueError:
                raise ValueError('Cannot verify frozen distribution file: ' + name)
            target_path = ROOT/target_source
            if not target_path.is_file():
                raise ValueError('Target distribution file is missing: ' + target_source)
            if hashlib.sha256(source_data).hexdigest() != digest(target_path):
                include.add(name)
        source_only.update(set(source_sources)-set(target_sources))
    unsupported = sorted(source_only-{'BVWA.exe'})
    if unsupported:
        raise ValueError('Runtime payload cannot safely remove legacy program files: ' + ', '.join(unsupported))
    return include


def _source_ref_pairs(source_refs, base_ref, program_variant):
    if source_refs is not None and (base_ref is not None or program_variant is not None):
        raise ValueError('Use source_refs or base_ref/program_variant, not both')
    if source_refs is None:
        return [(program_variant, base_ref or 'v0.3.0')]
    if isinstance(source_refs, dict):
        pairs = list(source_refs.items())
    else:
        pairs = list(source_refs)
    if not pairs:
        raise ValueError('At least one frozen source is required')
    result, seen_versions, seen_refs = [], set(), set()
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Frozen sources must be VERSION=REF pairs')
        version, ref = pair
        if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
            raise ValueError('Invalid source version: ' + str(version))
        if (not isinstance(ref, str) or GIT_REF_PATTERN.fullmatch(ref) is None or '..' in ref):
            raise ValueError('Invalid frozen source ref for ' + version)
        if version in seen_versions or ref in seen_refs:
            raise ValueError('Duplicate frozen source: ' + version)
        seen_versions.add(version); seen_refs.add(ref)
        result.append((version, ref))
    return sorted(result, key=lambda pair: tuple(int(part) for part in pair[0].split('.')))


def _candidate_metadata(folder, candidate_id):
    if not isinstance(candidate_id, str) or CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
        raise ValueError('Candidate id must use 1-64 ASCII letters, digits, dot, underscore or hyphen')
    requested = Path(folder)
    if not requested.is_absolute():
        raise ValueError('Candidate output must be an explicit absolute path')
    candidate = requested.resolve()
    if candidate.name != candidate_id or not any(
            part.casefold() == 'candidates' for part in candidate.parent.parts):
        raise ValueError('Candidate output must be inside a candidates directory and end with candidate-id')
    return {
        'format': 1,
        'candidate_id': candidate_id,
        'status': 'unverified',
        'user_test_confirmed': False,
        'published': False,
        'release_ready': False,
        'release_assets_manifest_created': False,
        'purpose': 'local candidate build; not approved for release',
        'notice': 'UNVERIFIED LOCAL UPDATE CANDIDATE: not user-tested and not published.',
    }


def _record_candidate_runtime(folder, candidate, report_name):
    marker = folder/'CANDIDATE-BUILD.json'
    data = {'candidate_build': dict(candidate), 'reports': []}
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('Candidate build marker is invalid') from error
        if data.get('candidate_build') != candidate or not isinstance(data.get('reports'), list):
            raise ValueError('Candidate build marker belongs to a different candidate')
    if report_name not in data['reports']:
        data['reports'].append(report_name)
    temporary = marker.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(marker)


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
        zipped.writestr('update-inventory.json',json.dumps({'version':version,'runtime_sha256':base_sha,
                                                          'entries':records},indent=2))
    return {'name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),'runtime_sha256':base_sha}


def build(folder, base_ref=None, *, program_variant=None, source_refs=None, artifact_version=None,
          runtime_name=None, bootstrap_name=None, legacy_bootstrap_name=None, candidate=None):
    from tools.verify_source_layout import verify
    verify(ROOT)
    folder = Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    if candidate is not None:
        validated_candidate = _candidate_metadata(folder, candidate.get('candidate_id'))
        if candidate != validated_candidate:
            raise ValueError('Candidate build metadata does not match its explicit output directory')
    pairs = _source_ref_pairs(source_refs, base_ref, program_variant)
    legacy_unversioned = source_refs is None and base_ref is None and program_variant is None
    if legacy_unversioned:
        snapshots = []
        for _, ref in pairs:
            runtime_raw = _git_blob(ref, 'dependencies/manifest.json')
            snapshots.append({
                'version': None, 'ref': ref,
                'runtime_sha256': hashlib.sha256(runtime_raw).hexdigest(),
                'runtime_entries': _manifest_records(runtime_raw),
                'package_sources': {}, 'reader': _git_blob,
            })
        source_versions = []
    else:
        snapshots = [_source_snapshot(ref, version, reader=_git_blob) for version, ref in pairs]
        source_versions = [snapshot['version'] for snapshot in snapshots]
    source_shas = {snapshot['runtime_sha256'] for snapshot in snapshots}
    if len(source_shas) != 1:
        raise ValueError('One runtime route must contain sources with one identical runtime SHA')
    base_sha = next(iter(source_shas))
    if artifact_version is None and source_versions:
        artifact_version = source_versions[-1]
    if artifact_version is not None and (
            not isinstance(artifact_version, str) or VERSION_PATTERN.fullmatch(artifact_version) is None):
        raise ValueError('Invalid artifact version')
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
    target_version = json.loads((ROOT/'packaging/version.json').read_text(encoding='utf-8'))['version']
    if target_version == '1.1.0' and not source_versions:
        raise ValueError('VWA 1.1.0 runtime builds require explicit frozen source versions')
    from updater import is_newer
    if any(not is_newer(target_version, source_version) for source_version in source_versions):
        raise ValueError('Every source version must be older than the target release')
    target = _target_snapshot()
    target_sha = target['runtime_sha256']
    target_sources = package_sources()
    target_entries = target['runtime_entries']
    changed, removed = _runtime_changes(snapshots, target_entries)
    changed_program = _program_difference_union(target_sources, snapshots)
    changed_program.update(name for name in ('app.py','version.json','tools/update_apply.py')
                           if name in target_sources)
    sources = {name:ROOT/target_sources[name] for name in changed_program}
    sources.update({name:ROOT/name for name in changed})
    sources['dependencies/manifest.json'] = ROOT/'dependencies/manifest.json'
    for required in ('app.py','version.json','dependencies/manifest.json','tools/update_apply.py'):
        if required not in sources:
            raise ValueError('Runtime migration lacks required target file: ' + required)
    entries = [{'file':name,'bytes':path.stat().st_size,'sha256':digest(path)}
               for name,path in sorted(sources.items())]
    from tools.update_apply import validate_file, RUNTIME_PROGRAM_FILES
    entry_names = {entry['file'] for entry in entries}
    if {'VWA.exe','BVWA.exe'}.issubset(entry_names):
        raise ValueError('A runtime upgrade cannot install both canonical and legacy launchers')
    for entry in entries:
        if (entry['file'] not in target_entries and entry['file'] != 'dependencies/manifest.json'
                and entry['file'] not in RUNTIME_PROGRAM_FILES):
            validate_file(entry)
    helper_entry = next((entry for entry in entries if entry['file'] == 'tools/update_apply.py'), None)
    if helper_entry is None or helper_entry['sha256'] != digest(ROOT/'tools/update_apply.py'):
        raise ValueError('Runtime payload and first-hop bootstrap must use the same helper bytes')
    plan = {'format':1,'version':target_version,'source_runtime_sha256':base_sha,
            'target_runtime_sha256':target_sha,
            'entries':entries,'removed':removed}
    item = {'version':target_version,'source_runtime_sha256':base_sha,
            'target_runtime_sha256':target_sha}
    if source_versions:
        policy = {'format':1,'allowed_sources':[_source_policy(snapshot) for snapshot in snapshots]}
        plan['source_policy'] = policy
        item['source_policy'] = policy
    if 'VWA.exe' in entry_names:
        plan['launcher_migration'] = dict(LAUNCHER_MIGRATION)
    if runtime_name is None and artifact_version is not None:
        runtime_name = f'VWA-Runtime-Upgrade-v{artifact_version}.zip'
    elif runtime_name is None:
        runtime_name = 'VWA-Runtime-Upgrade.zip'
    if not isinstance(runtime_name, str) or re.fullmatch(RUNTIME_PATTERN, runtime_name) is None:
        raise ValueError('Invalid runtime upgrade filename')
    runtime = folder/runtime_name
    with zipfile.ZipFile(runtime,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for entry in entries: zipped.write(sources[entry['file']],entry['file'])
        zipped.writestr('runtime-upgrade.json',json.dumps(plan,ensure_ascii=False,indent=2))
    item.update(name=runtime.name, bytes=runtime.stat().st_size, sha256=digest(runtime))
    update = build_bootstrap(folder,item,filename=canonical_name)
    result = {'update':update,'runtime_upgrade':item,'source_versions':source_versions,
              'changed_program_files':len(changed_program),
              'changed_dependency_files':len(changed),
              'changed_dependency_bytes':sum(target_entries[name]['bytes'] for name in changed),
              'removed_files':len(removed)}
    report_name = (f'runtime-upgrade-v{artifact_version}.build.json'
                   if artifact_version is not None else 'runtime-upgrade.build.json')
    if candidate is not None:
        result['candidate_build'] = dict(candidate)
        result['build_kind'] = 'candidate'
        result['notice'] = candidate['notice']
    if legacy_bootstrap_name is not None:
        canonical_archive = folder/update['name'];legacy_archive = folder/legacy_bootstrap_name
        with canonical_archive.open('rb') as source,legacy_archive.open('xb') as target_file:
            for block in iter(lambda:source.read(1024**2),b''): target_file.write(block)
        if legacy_archive.stat().st_size != update['bytes'] or digest(legacy_archive) != update['sha256']:
            raise ValueError('Legacy first-hop alias is not byte-identical to the canonical bootstrap')
        result['legacy_update'] = dict(update,name=legacy_archive.name)
    report = folder/report_name
    with report.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    if candidate is not None:
        _record_candidate_runtime(folder, candidate, report_name)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return result


def _parse_cli_sources(values):
    result = []
    for value in values or []:
        if '=' not in value:
            raise ValueError('--source-ref requires VERSION=REF')
        version, ref = value.split('=',1)
        result.append((version, ref))
    return result or None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--base-ref')
    parser.add_argument('--source-ref',action='append',help='Frozen source VERSION=REF; repeat equal-runtime sources')
    parser.add_argument('--program-variant',help='Legacy source version for the program-update bootstrap, e.g. 0.4.0')
    parser.add_argument('--artifact-version',help='Version label used in canonical artifact filenames')
    parser.add_argument('--runtime-name',help='Optional canonical runtime upgrade filename')
    parser.add_argument('--bootstrap-name',help='Optional versioned canonical VWA first-hop filename')
    parser.add_argument('--legacy-bootstrap-name',help='Optional byte-identical BVWA first-hop alias')
    confirmation = parser.add_mutually_exclusive_group(required=True)
    confirmation.add_argument('--user-test-confirmed',action='store_true',
                              help='Required only after real user testing has actually completed')
    confirmation.add_argument('--candidate-build',action='store_true',
                              help='Create an explicitly unverified local candidate in an absolute candidates/<id> directory')
    parser.add_argument('--candidate-id',help='Leaf folder name ending an absolute candidates/<id> path')
    args = parser.parse_args()
    if args.source_ref is None and args.base_ref is None and args.program_variant is None:
        parser.error('A release source is required: pass --source-ref or --base-ref')
    candidate = None
    if args.candidate_build:
        if not args.candidate_id:
            parser.error('--candidate-build requires --candidate-id')
        try:
            candidate = _candidate_metadata(args.output_dir,args.candidate_id)
        except ValueError as error:
            parser.error(str(error))
    elif args.candidate_id:
        parser.error('--candidate-id is only valid with --candidate-build')
    try:
        sources = _parse_cli_sources(args.source_ref)
    except ValueError as error:
        parser.error(str(error))
    if sources is not None and (args.base_ref is not None or args.program_variant is not None):
        parser.error('--source-ref cannot be combined with --base-ref or --program-variant')
    build(args.output_dir,args.base_ref,program_variant=args.program_variant,source_refs=sources,
          artifact_version=args.artifact_version,runtime_name=args.runtime_name,
          bootstrap_name=args.bootstrap_name,legacy_bootstrap_name=args.legacy_bootstrap_name,
          candidate=candidate)
