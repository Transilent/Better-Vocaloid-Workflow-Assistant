"""Assemble verified per-runtime migrations and explicit legacy entry points."""
import argparse
import hashlib
import json
from pathlib import PurePosixPath
from pathlib import Path
import re
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_portable import digest
from tools.update_apply import (RUNTIME_PROGRAM_FILES, ROOT_FILES, TOOL_FILES, _runtime_path,
                                program_name, select_update_route, validate_file, validate_destination)

LAUNCHER_MIGRATION = {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True}
COMPATIBILITY_TOOL_NAMES = {'VWA-Updater.exe', 'BVWA-Updater.exe'}
PORTABLE_VARIANTS = {'full', 'jpn'}
LEGACY_BOOTSTRAP_PATTERN = r'BVWA-(?:App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip'
CANONICAL_BOOTSTRAP_PATTERN = r'VWA-(?:App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip'
SOURCE_IDENTITY_FILES = {'app.py', 'updater.py', 'updates_ui.py', 'tools/update_apply.py'}
SOURCE_IDENTITY_ALLOWED_FILES = ROOT_FILES | TOOL_FILES | RUNTIME_PROGRAM_FILES
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+')
CANDIDATE_ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')


def _safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith('/') or name.endswith('/') or
            '\\' in name or ':' in name or any(ord(char) < 32 for char in name)):
        raise ValueError('Invalid runtime path')
    raw_parts = name.split('/')
    if any(not part or part in ('.', '..') or part.endswith((' ', '.')) for part in raw_parts):
        raise ValueError('Invalid runtime path')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or '.' in path.parts or '..' in path.parts:
        raise ValueError('Invalid runtime path')
    return name


def _runtime_record(entry, *, manifest=False, path_only=False):
    """Validate a manifest or removal record without widening program allowlists."""
    if not isinstance(entry, dict) or not isinstance(entry.get('file'), str):
        raise ValueError('Invalid runtime record')
    name = _safe_name(entry['file'])
    if manifest:
        _runtime_path(name)
    if path_only:
        return name
    if (type(entry.get('bytes')) is not int or entry['bytes'] < 0 or
            not isinstance(entry.get('sha256'), str) or
            not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])):
        raise ValueError('Invalid runtime checksum record: ' + name)
    return name


def _program_record(entry):
    name = _runtime_record(entry)
    path = PurePosixPath(name)
    if name in RUNTIME_PROGRAM_FILES:
        # These explicit program support files can live outside runtime roots.
        pass
    elif name == 'dependencies/manifest.json':
        pass
    elif path.parts[0] in ('dependencies', 'models', 'vendor'):
        _runtime_path(name)
    else:
        validate_file(entry)
    return name


def _manifest_records(data):
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise ValueError('Runtime manifest lacks entries')
    records = {}
    for entry in data['entries']:
        name = _runtime_record(entry, manifest=True)
        if name in records:
            raise ValueError('Runtime manifest contains duplicate files: ' + name)
        records[name] = entry
    return records


def _same_record(left, right):
    return (isinstance(left, dict) and left.get('file') == right.get('file') and
            left.get('bytes') == right.get('bytes') and left.get('sha256') == right.get('sha256'))


def _version_key(version):
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError('Invalid source version: ' + str(version))
    return tuple(int(part) for part in version.split('.'))


def _route_sources(route):
    """Read the new multi-source label while accepting historical catalogs."""
    versions = route.get('source_versions')
    legacy = route.get('source_version')
    if versions is None:
        versions = [legacy]
    if (not isinstance(versions, list) or not versions or any(
            not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None for version in versions)):
        raise ValueError('Update route lacks valid source versions')
    if len(versions) != len(set(versions)):
        raise ValueError('Update route contains duplicate source versions')
    if legacy is not None and legacy not in versions:
        raise ValueError('Legacy source_version disagrees with source_versions')
    return sorted(versions, key=_version_key)


def _validate_source_policy(item, sources, runtime_sha, *, required=False):
    policy = item.get('source_policy') if isinstance(item, dict) else None
    if policy is None:
        if required:
            raise ValueError('Runtime migration lacks its source policy')
        return None
    if (not isinstance(policy, dict) or type(policy.get('format')) is not int or policy.get('format') != 1 or
            not isinstance(policy.get('allowed_sources'), list) or not policy['allowed_sources'] or
            len(policy['allowed_sources']) > 32):
        raise ValueError('Runtime migration source policy is invalid')
    allowed, seen = {}, set()
    for source in policy['allowed_sources']:
        if not isinstance(source, dict):
            raise ValueError('Runtime source policy contains an invalid source')
        version = source.get('version')
        if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None or version in seen:
            raise ValueError('Runtime source policy contains an invalid or duplicate version')
        if source.get('runtime_sha256') != runtime_sha:
            raise ValueError('Runtime source policy version targets a different runtime')
        files = source.get('files')
        if (not isinstance(files, dict) or not SOURCE_IDENTITY_FILES.issubset(files)
                or len(files) > 64):
            raise ValueError('Runtime source policy lacks required program identity files')
        for name, checksum in files.items():
            _safe_name(name)
            if (name not in SOURCE_IDENTITY_ALLOWED_FILES or not isinstance(checksum, str)
                    or re.fullmatch(r'[a-f0-9]{64}', checksum) is None):
                raise ValueError('Runtime source policy has an invalid program hash: ' + name)
        seen.add(version); allowed[version] = source
    if sorted(allowed, key=_version_key) != sorted(sources, key=_version_key):
        raise ValueError('Runtime source policy allowed_sources disagree with its catalog route')
    return allowed


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


def _runtime_zip_members(zipped, plan, target_records):
    """Require a complete, unique and safe runtime deltas inventory."""
    infos = zipped.infolist()
    names = [info.filename for info in infos]
    if not infos or len(names) != len(set(names)):
        raise ValueError('Runtime archive contains missing or duplicate members')
    if any(info.is_dir() or ((info.external_attr >> 16) & 0o170000) == 0o120000 for info in infos):
        raise ValueError('Runtime archive contains a directory or symbolic link')
    if 'runtime-upgrade.json' not in names:
        raise ValueError('Runtime archive lacks runtime-upgrade.json')
    for name in names:
        _safe_name(name)
    entries, removed = plan.get('entries'), plan.get('removed')
    if not isinstance(entries, list) or not isinstance(removed, list):
        raise ValueError('Runtime migration lacks entries or removed records')
    records, removals = {}, {}
    for entry in entries:
        name = _program_record(entry) if isinstance(entry, dict) else ''
        if name in records:
            raise ValueError('Runtime migration contains duplicate entries: ' + name)
        if name == 'runtime-upgrade.json':
            raise ValueError('Runtime migration cannot replace its own plan')
        records[name] = entry
    for entry in removed:
        name = _runtime_record(entry)
        _runtime_path(name)
        if name in removals:
            raise ValueError('Runtime migration contains duplicate removals: ' + name)
        if name in records:
            raise ValueError('Runtime file is both replaced and removed: ' + name)
        if name in target_records:
            raise ValueError('Target manifest still contains a removed runtime file')
        removals[name] = entry
    if set(names) != set(records) | {'runtime-upgrade.json'}:
        raise ValueError('Runtime archive members do not match the migration plan')
    if not {'app.py', 'version.json', 'dependencies/manifest.json'}.issubset(records):
        raise ValueError('Runtime migration lacks required program files')
    migration = plan.get('launcher_migration')
    if migration is not None and (not isinstance(migration, dict) or migration != LAUNCHER_MIGRATION or
                                 type(migration.get('legacy_alias')) is not bool):
        raise ValueError('Runtime launcher migration record is invalid')
    if ('VWA.exe' in records) != (migration is not None):
        raise ValueError('Runtime launcher migration must be present exactly when VWA.exe is in the payload')
    if 'VWA.exe' in records and 'BVWA.exe' in records:
        raise ValueError('Runtime payload cannot contain both canonical and legacy launchers')
    for name in records:
        path = PurePosixPath(name)
        if (name not in target_records and name != 'dependencies/manifest.json' and
                name not in RUNTIME_PROGRAM_FILES and path.parts[0] in ('dependencies', 'models', 'vendor')):
            raise ValueError('Runtime migration changes a runtime file absent from its target manifest')
    for name, entry in records.items():
        info = zipped.getinfo(name)
        if info.file_size != entry['bytes']:
            raise ValueError('Runtime member size mismatch: ' + name)
        member_hash = hashlib.sha256()
        with zipped.open(info) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                member_hash.update(block)
        if member_hash.hexdigest() != entry['sha256']:
            raise ValueError('Runtime member checksum mismatch: ' + name)
        if name in target_records and not _same_record(entry, target_records[name]):
            raise ValueError('Runtime member differs from target manifest: ' + name)
    return records, removals


def _load_manifest(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    records = _manifest_records(data)
    return hashlib.sha256(raw).hexdigest(), records, data


def _target_manifest(target_manifest, target_sha, external_reason):
    local_raw = (ROOT / 'dependencies/manifest.json').read_bytes()
    local_sha = hashlib.sha256(local_raw).hexdigest()
    if not isinstance(target_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', target_sha):
        raise ValueError('A migration must name its target runtime manifest')
    if target_manifest is None:
        if target_sha != local_sha:
            raise ValueError('The migration target is not the current runtime manifest')
        if external_reason:
            raise ValueError('An external release reason requires --target-manifest')
        return local_sha, _manifest_records(json.loads(local_raw)), None
    if not isinstance(external_reason, str) or not external_reason.strip():
        raise ValueError('A non-default target manifest requires an explicit external release reason')
    digest_value, records, data = _load_manifest(target_manifest)
    if digest_value != target_sha:
        raise ValueError('Explicit target manifest digest does not match the migration target')
    return digest_value, records, None if digest_value == local_sha else external_reason.strip()
def _verified_asset(folder, asset):
    if not isinstance(asset, dict):
        raise ValueError('Invalid release asset record')
    name = asset.get('name')
    _safe_name(name)
    if len(PurePosixPath(name).parts) != 1:
        raise ValueError('Invalid release asset name')
    if (type(asset.get('bytes')) is not int or not 0 < asset['bytes'] or
            not isinstance(asset.get('sha256'), str) or
            not re.fullmatch(r'[a-f0-9]{64}', asset['sha256'])):
        raise ValueError('Invalid release asset checksum record: ' + name)
    path = validate_destination(folder, name)
    if (not path.is_file() or path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']):
        raise ValueError('Release asset checksum mismatch: ' + name)
    return path


def _legacy_projection(route):
    """Return the frozen-client asset, or None when only a canonical route exists."""
    update = route.get('update') if isinstance(route, dict) else None
    if not isinstance(update, dict):
        raise ValueError('Update route lacks its canonical bootstrap')
    canonical_name = program_name(update)
    legacy = route.get('legacy_update')
    if legacy is None:
        if re.fullmatch(LEGACY_BOOTSTRAP_PATTERN, canonical_name):
            return update
        return None
    if not isinstance(legacy, dict):
        raise ValueError('Legacy update alias must be an asset record')
    legacy_name = program_name(legacy)
    if not re.fullmatch(LEGACY_BOOTSTRAP_PATTERN, legacy_name):
        raise ValueError('Legacy update alias must use the BVWA protocol prefix')
    if not re.fullmatch(CANONICAL_BOOTSTRAP_PATTERN, canonical_name):
        raise ValueError('A legacy update alias requires a canonical VWA bootstrap')
    for key in ('bytes', 'sha256'):
        if legacy.get(key) != update.get(key):
            raise ValueError('Legacy update alias differs from its canonical bootstrap: ' + key)
    if legacy.get('runtime_sha256') != route.get('runtime_sha256'):
        raise ValueError('Legacy update alias targets a different runtime')
    return legacy


def _portable_variant_record(folder, variant, metadata, target_sha):
    if not isinstance(metadata, dict):
        raise ValueError('Portable variant metadata must be an object: ' + variant)
    downloads = metadata.get('portable_downloads')
    if downloads is not None:
        if not isinstance(downloads, dict) or not isinstance(downloads.get(variant), dict):
            raise ValueError('Portable metadata lacks its requested variant: ' + variant)
        selected = downloads[variant]
        for key in ('archive', 'parts', 'portable_release', 'layout_root'):
            if key in metadata and metadata[key] != selected.get(key):
                raise ValueError('Portable metadata wrapper disagrees with its variant: ' + variant)
        metadata = selected
    reported = []
    if 'variant' in metadata:
        reported.append(metadata['variant'])
    release = metadata.get('portable_release')
    if release is not None:
        if not isinstance(release, dict):
            raise ValueError('Invalid portable release metadata: ' + variant)
        if 'variant' in release:
            reported.append(release['variant'])
        if 'layout_root' in release and release['layout_root'] not in ('VWA', 'BVWA'):
            raise ValueError('Invalid portable layout root: ' + variant)
    if 'layout_root' in metadata and metadata['layout_root'] not in ('VWA', 'BVWA'):
        raise ValueError('Invalid portable layout root: ' + variant)
    if any(value != variant for value in reported):
        raise ValueError('Portable metadata is under the wrong variant: ' + variant)
    archive = metadata.get('archive')
    if not isinstance(archive, dict):
        raise ValueError('Portable variant lacks an archive record: ' + variant)
    _verified_asset(folder, archive)
    parts = metadata.get('parts', [])
    if not isinstance(parts, list):
        raise ValueError('Portable variant parts must be a list: ' + variant)
    names = [archive['name']]
    part_bytes = 0
    combined = hashlib.sha256()
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError('Invalid portable part record: ' + variant)
        part_path = _verified_asset(folder, part)
        with part_path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                combined.update(block)
        names.append(part['name'])
        part_bytes += part['bytes']
    if len(names) != len(set(names)):
        raise ValueError('Portable variant contains duplicate asset names: ' + variant)
    if parts and (part_bytes != archive['bytes'] or combined.hexdigest() != archive['sha256']):
        raise ValueError('Portable parts do not reconstruct the archive: ' + variant)
    if 'runtime_sha256' in metadata and metadata['runtime_sha256'] != target_sha:
        raise ValueError('Portable variant targets a different runtime: ' + variant)
    return dict(metadata)


def _validate_portable_variants(folder, variants, target_sha):
    if variants is None:
        return {}
    if not isinstance(variants, dict):
        raise ValueError('portable_variants must be an object keyed by full and jpn')
    unknown = set(variants) - PORTABLE_VARIANTS
    if unknown:
        raise ValueError('Unknown portable variants: ' + ', '.join(sorted(unknown)))
    result, asset_names = {}, set()
    for variant, metadata in variants.items():
        record = _portable_variant_record(folder, variant, metadata, target_sha)
        result[variant] = record
        names = {record['archive']['name']}
        names.update(part['name'] for part in record.get('parts', []))
        if asset_names & names:
            raise ValueError('Portable variants share a release asset name')
        asset_names.update(names)
    return result


def _verify_gui_executable(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('Compatibility tool must be a Windows GUI executable')
        offset = struct.unpack_from('<I', header, 0x3c)[0]
        if not 64 <= offset <= path.stat().st_size - 96:
            raise ValueError('Compatibility executable has an invalid PE header')
        stream.seek(offset)
        pe = stream.read(96)
        if (pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 4)[0] != 0x8664
                or struct.unpack_from('<H', pe, 24)[0] != 0x20b
                or struct.unpack_from('<H', pe, 24 + 68)[0] != 2):
            raise ValueError('Compatibility tool must be a 64-bit Windows GUI executable')


def verify_catalog_assets(catalog, folder, *, target_manifest=None, external_reason=None,
                          require_source_policy=False):
    """Recheck all migration files and bind them to one target runtime manifest."""
    folder = Path(folder).resolve()
    if not isinstance(catalog, dict):
        raise ValueError('Catalog must be an object')
    require_source_policy = require_source_policy or catalog.get('version') == '1.1.0'
    routes = catalog.get('updates')
    if not isinstance(routes, list) or not routes:
        raise ValueError('Missing runtime update routes')
    target_sha, target_records, _ = _target_manifest(
        target_manifest, catalog.get('target_runtime_sha256'), external_reason)
    seen_runtimes = set()
    for route in routes:
        route_sha = route.get('runtime_sha256') if isinstance(route, dict) else None
        if route_sha in seen_runtimes:
            raise ValueError('Update catalog contains duplicate runtime routes')
        seen_runtimes.add(route_sha)
        select_update_route(catalog, route_sha)
        route_sources = _route_sources(route)
        runtime_item = route.get('runtime_upgrade')
        _validate_source_policy(runtime_item, route_sources, route_sha,
                                required=require_source_policy)
        bootstrap = _verified_asset(folder, route['update'])
        legacy = route.get('legacy_update')
        if legacy is not None:
            _legacy_projection(route)
            legacy_path = _verified_asset(folder, legacy)
            if (legacy_path.stat().st_size != bootstrap.stat().st_size or
                    digest(legacy_path) != digest(bootstrap)):
                raise ValueError('Legacy update alias is not byte-identical to its canonical bootstrap')
        runtime = _verified_asset(folder, route['runtime_upgrade'])
        with zipfile.ZipFile(bootstrap) as zipped:
            inventory = json.loads(zipped.read('update-inventory.json'))
            info = json.loads(zipped.read('version.json'))
            entries = inventory['entries']
            if (inventory['version'] != catalog['version'] or inventory['runtime_sha256'] != route['runtime_sha256']
                    or len(entries) != 3
                    or {e['file'] for e in entries} != {'app.py', 'version.json', 'tools/update_apply.py'}
                    or len(zipped.infolist()) != 4
                    or set(zipped.namelist()) != {e['file'] for e in entries} | {'update-inventory.json'}
                    or info.get('runtime_upgrade') != route['runtime_upgrade'] or not info.get('upgrade_pending')
                    or info.get('version') != catalog['version']):
                raise ValueError('Bootstrap and runtime migration disagree')
            for entry in entries:
                data = zipped.read(entry['file'])
                if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise ValueError('Bootstrap member checksum mismatch: ' + entry['file'])
        bootstrap_helper = next(entry for entry in entries if entry['file'] == 'tools/update_apply.py')
        with zipfile.ZipFile(runtime) as zipped:
            try:
                plan = json.loads(zipped.read('runtime-upgrade.json'))
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError('Runtime archive lacks a valid migration plan') from error
            if not isinstance(plan, dict) or any(
                    plan.get(key) != route['runtime_upgrade'].get(key)
                    for key in ('version', 'source_runtime_sha256', 'target_runtime_sha256')):
                raise ValueError('Runtime archive and its release record disagree')
            if plan.get('source_policy') != runtime_item.get('source_policy'):
                raise ValueError('Runtime archive and release record use different source policies')
            _validate_source_policy(plan, route_sources, route_sha,
                                    required=require_source_policy)
            if plan.get('target_runtime_sha256') != target_sha:
                raise ValueError('Runtime archive targets a different checkout')
            records, _ = _runtime_zip_members(zipped, plan, target_records)
            embedded = _manifest_records(json.loads(zipped.read('dependencies/manifest.json')))
            if set(embedded) != set(target_records) or any(
                    not _same_record(embedded[name], target_records[name]) for name in embedded):
                raise ValueError('Runtime archive target manifest and bound manifest disagree')
            if records['dependencies/manifest.json']['sha256'] != target_sha:
                raise ValueError('Runtime manifest member digest does not match target')
            helper_record = records.get('tools/update_apply.py')
            if helper_record is None or not _same_record(helper_record, bootstrap_helper):
                raise ValueError('Bootstrap helper and runtime payload helper disagree')
    # Old clients can only read these two fields. Select the explicit legacy alias
    # when it exists; historical fixture catalogs used the old route update field.
    projected = []
    for field, name in (('update', 'BVWA-App-Update.zip'), ('program_update', 'BVWA-Program-Update.zip')):
        asset = catalog.get(field)
        if asset is None:
            continue
        if not isinstance(asset, dict) or asset.get('name') != name:
            raise ValueError('Invalid legacy update projection: ' + field)
        matches = [route for route in routes if _legacy_projection(route) == asset]
        if len(matches) != 1:
            raise ValueError('Legacy update projection does not match a verified route')
        projected.append(matches[0])
    if 'update' in catalog:
        direct = next(route for route in projected
                      if _legacy_projection(route).get('name') == 'BVWA-App-Update.zip')
        if catalog.get('runtime_upgrade') != direct['runtime_upgrade']:
            raise ValueError('Legacy runtime migration does not match its bootstrap')
    elif catalog.get('runtime_upgrade') is not None:
        matches = [route for route in routes if route['runtime_upgrade'] == catalog['runtime_upgrade']]
        if len(matches) != 1:
            raise ValueError('Legacy runtime migration does not match one verified route')
    covered = {route['runtime_sha256'] for route in projected}
    uncovered = [version for route in routes if route['runtime_sha256'] not in covered
                 for version in _route_sources(route) if _version_key(version) < (1,0,0)]
    uncovered.sort(key=_version_key)
    tool = catalog.get('compatibility_tool')
    if uncovered and (not isinstance(tool, dict) or tool.get('required_for') != uncovered):
        raise ValueError('Legacy runtimes need the one-time repair tool: ' + ', '.join(uncovered))
    if tool:
        if (tool.get('name') not in COMPATIBILITY_TOOL_NAMES or type(tool.get('bytes')) is not int
                or not 0 < tool['bytes'] <= 4 * 1024**2):
            raise ValueError('Invalid compatibility tool asset')
        _verify_gui_executable(_verified_asset(folder, tool))
    if catalog.get('portable_downloads') is not None:
        _validate_portable_variants(folder, catalog['portable_downloads'], target_sha)

def _migration_sources(entry):
    versions = entry.get('source_versions')
    if versions is None:
        versions = [entry.get('source_version')]
    if (not isinstance(versions, list) or not versions or any(
            not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None for version in versions)):
        raise ValueError('Migration report lacks valid source versions')
    if len(versions) != len(set(versions)):
        raise ValueError('Migration report contains duplicate source versions')
    return sorted(versions, key=_version_key)


def build(folder, version, migrations, *, legacy_update=None, legacy_program=None, compatibility_tool=None,
          notes='', output='release-assets.json', portable=None, portable_variants=None,
          target_manifest=None, external_reason=None, require_source_policy=False,
          include_legacy=True, candidate=None):
    folder = Path(folder).resolve()
    require_source_policy = require_source_policy or version == '1.1.0'
    routes, route_by_runtime, labels, targets, manifests = [], {}, {}, set(), []
    candidate_id = candidate.get('candidate_id') if isinstance(candidate, dict) else None
    if candidate is not None:
        validated_candidate = _candidate_metadata(folder, candidate_id)
        if candidate != validated_candidate:
            raise ValueError('Candidate catalog metadata does not match its explicit output directory')
    for entry in migrations:
        update, runtime = entry['update'], entry['runtime_upgrade']
        requested_sources = _migration_sources(entry)
        route_sha = runtime.get('source_runtime_sha256')
        if not isinstance(route_sha, str) or re.fullmatch(r'[a-f0-9]{64}', route_sha) is None:
            raise ValueError('Migration has an invalid source runtime')
        if runtime.get('version') != version:
            raise ValueError('Migration targets a different release')
        allowed = _validate_source_policy(runtime, requested_sources, route_sha,
                                          required=require_source_policy)
        final_sources = sorted(allowed, key=_version_key) if allowed is not None else requested_sources
        if not set(requested_sources).issubset(final_sources):
            raise ValueError('Migration label is absent from its source policy')
        existing = route_by_runtime.get(route_sha)
        if existing is not None:
            if (existing['update'] != update or existing['runtime_upgrade'] != runtime or
                    existing.get('legacy_update') != entry.get('legacy_update')):
                raise ValueError('Duplicate runtime routes use different assets or source policies')
            raise ValueError('Duplicate runtime route must be represented by one multi-source report')
        for source in final_sources:
            if source in labels:
                raise ValueError('Duplicate source version: ' + source)
        target_sha, target_records, reason = _target_manifest(
            target_manifest, runtime['target_runtime_sha256'], external_reason)
        manifests.append((target_sha, target_records, reason))
        route = {'source_versions': final_sources, 'runtime_sha256': route_sha,
                 'update': update, 'runtime_upgrade': runtime}
        if len(final_sources) == 1:
            route['source_version'] = final_sources[0]
        if entry.get('legacy_update') is not None:
            route['legacy_update'] = entry['legacy_update']
        routes.append(route); route_by_runtime[route_sha] = route
        for source in final_sources:
            labels[source] = route
        targets.add(runtime['target_runtime_sha256'])
        report_candidate = entry.get('candidate_build')
        if report_candidate is not None:
            if candidate is None or not isinstance(report_candidate, dict) or report_candidate != candidate:
                raise ValueError('Candidate migration report cannot be used for formal release metadata')
    if not routes:
        raise ValueError('At least one migration is required')
    if candidate is None and (folder/'CANDIDATE-BUILD.json').exists():
        raise ValueError('Candidate build assets cannot be used for formal release metadata')
    if len(targets) != 1:
        raise ValueError('Migrations must share one target runtime')
    if len({manifest[0] for manifest in manifests}) != 1:
        raise ValueError('Migrations must share one target manifest')
    target_sha, target_records, target_reason = manifests[0]
    catalog = {'version': version, 'update_format': 2, 'release_notes': notes, 'updates': routes,
               'target_runtime_sha256': target_sha}
    if candidate is not None:
        catalog['candidate_build'] = dict(candidate)
        catalog['build_kind'] = 'candidate'
        catalog['candidate'] = dict(candidate)
        catalog['notice'] = candidate['notice']
        catalog['release_status'] = 'candidate_unverified'
    for route in routes:
        select_update_route(catalog, route['runtime_sha256'])
    if include_legacy and legacy_update is None and '0.4.1' in labels:
        legacy_update = '0.4.1'
    if include_legacy and legacy_program is None and '0.4.0' in labels:
        legacy_program = '0.4.0'
    projected = []
    if legacy_update is not None:
        if legacy_update not in labels:
            raise ValueError('The legacy App projection has no matching migration')
        asset = _legacy_projection(labels[legacy_update])
        if not isinstance(asset, dict) or asset.get('name') != 'BVWA-App-Update.zip':
            raise ValueError('Legacy App clients require BVWA-App-Update.zip')
        catalog['update'] = asset
        catalog['runtime_upgrade'] = labels[legacy_update]['runtime_upgrade']
        projected.append(labels[legacy_update])
    if legacy_program is not None:
        if legacy_program not in labels:
            raise ValueError('The legacy Program projection has no matching migration')
        asset = _legacy_projection(labels[legacy_program])
        if not isinstance(asset, dict) or asset.get('name') != 'BVWA-Program-Update.zip':
            raise ValueError('Legacy Program clients require BVWA-Program-Update.zip')
        catalog['program_update'] = asset
        projected.append(labels[legacy_program])
    covered = {route['runtime_sha256'] for route in projected}
    uncovered = [source for route in routes if route['runtime_sha256'] not in covered
                 for source in _route_sources(route) if _version_key(source) < (1,0,0)]
    uncovered.sort(key=_version_key)
    if uncovered:
        if not compatibility_tool:
            raise ValueError('Legacy runtimes need the one-time repair tool: ' + ', '.join(uncovered))
        catalog['compatibility_tool'] = dict(compatibility_tool, required_for=uncovered)
    if target_reason:
        catalog['target_runtime_manifest'] = {
            'sha256': target_sha, 'external_build_reason': target_reason}
    if portable:
        for name in ('archive', 'parts', 'portable_release'):
            if name in portable: catalog[name] = portable[name]
    normalized_portable = _validate_portable_variants(folder, portable_variants, target_sha)
    if normalized_portable:
        catalog['portable_downloads'] = normalized_portable
    if Path(output).name != output:
        raise ValueError('Catalog output must stay in its release folder')
    if candidate is not None and output.casefold() == 'release-assets.json':
        raise ValueError('Candidate catalogs must use a candidate-specific filename')
    target = validate_destination(folder, output)
    verify_catalog_assets(catalog, folder, target_manifest=target_manifest, external_reason=external_reason,
                          require_source_policy=require_source_policy)
    from common import atomic_json
    atomic_json(target, catalog)
    if candidate is not None:
        sidecar = folder/'CANDIDATE-CATALOG.json'
        with sidecar.open('x', encoding='utf-8') as stream:
            json.dump({'candidate_build': candidate, 'catalog': target.name,
                       'sha256': digest(target), 'status': 'unverified'},
                      stream, ensure_ascii=False, indent=2)
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--migration', action='append', required=True,
                        help='SOURCE_VERSION[,SOURCE_VERSION...]=BUILD_REPORT.json')
    parser.add_argument('--legacy-update', help='Compatibility App projection source (default: 0.4.1 when present)')
    parser.add_argument('--legacy-program', help='Compatibility Program projection source (default: 0.4.0 when present)')
    parser.add_argument('--no-legacy-projections', action='store_true',
                        help='Emit a canonical catalog without optional 0.4 compatibility slots')
    parser.add_argument('--output', help='Catalog filename inside --folder')
    parser.add_argument('--compatibility-tool', type=Path)
    parser.add_argument('--notes', type=Path)
    parser.add_argument('--portable-metadata', type=Path,
                        help='Legacy top-level archive/parts metadata for frozen runtime installers')
    parser.add_argument('--portable-full-metadata', type=Path,
                        help='Full portable metadata stored under portable_downloads.full')
    parser.add_argument('--portable-jpn-metadata', type=Path,
                        help='JPN portable metadata stored under portable_downloads.jpn')
    parser.add_argument('--target-manifest', type=Path, help='Explicit target manifest for an external checkout')
    parser.add_argument('--external-build-reason', help='Why this catalog targets a non-current checkout')
    confirmation = parser.add_mutually_exclusive_group(required=True)
    confirmation.add_argument('--user-test-confirmed', action='store_true',
                              help='Required only after real user testing has actually completed')
    confirmation.add_argument('--candidate-build', action='store_true',
                              help='Create explicitly unverified metadata in an absolute candidates/<id> directory')
    parser.add_argument('--candidate-id', help='Leaf folder name ending an absolute candidates/<id> path')
    args = parser.parse_args()
    if args.candidate_build and not args.candidate_id:
        parser.error('--candidate-build requires --candidate-id')
    if not args.candidate_build and args.candidate_id:
        parser.error('--candidate-id is only valid with --candidate-build')
    if args.no_legacy_projections and (args.legacy_update or args.legacy_program):
        parser.error('--no-legacy-projections cannot be combined with explicit legacy sources')
    if not args.candidate_build and args.output not in (None, 'release-assets.json'):
        parser.error('Formal update catalogs must be named release-assets.json')
    candidate = None
    if args.candidate_build:
        try:
            candidate = _candidate_metadata(args.folder, args.candidate_id)
        except ValueError as error:
            parser.error(str(error))
    version = json.loads((ROOT / 'packaging/version.json').read_text(encoding='utf-8'))['version']
    output = args.output or ('candidate-assets.json' if candidate is not None else 'release-assets.json')
    loaded, source_labels = {}, {}
    for spec in args.migration:
        sources, path = spec.split('=', 1)
        labels = [source.strip() for source in sources.split(',')]
        if any(not label for label in labels):
            parser.error('--migration requires at least one source version')
        key = Path(path).resolve()
        if key not in loaded:
            loaded[key] = json.loads(key.read_text(encoding='utf-8'))
            source_labels[key] = set()
        source_labels[key].update(labels)
    migrations = []
    for path, entry in loaded.items():
        report = dict(entry)
        report['source_versions'] = sorted(source_labels[path], key=_version_key)
        migrations.append(report)
    tool = json.loads(args.compatibility_tool.read_text(encoding='utf-8')) if args.compatibility_tool else None
    portable_variants = {}
    for variant, metadata in (('full', args.portable_full_metadata), ('jpn', args.portable_jpn_metadata)):
        if metadata:
            portable_variants[variant] = json.loads(metadata.read_text(encoding='utf-8'))
    result = build(args.folder, version, migrations, legacy_update=args.legacy_update,
                   legacy_program=args.legacy_program, compatibility_tool=tool,
                   notes=args.notes.read_text(encoding='utf-8') if args.notes else '',
                   portable=json.loads(args.portable_metadata.read_text(encoding='utf-8')) if args.portable_metadata else None,
                   portable_variants=portable_variants or None,
                   target_manifest=args.target_manifest, external_reason=args.external_build_reason,
                   require_source_policy=True, include_legacy=not args.no_legacy_projections,
                   candidate=candidate, output=output)
    print('Verified update routes:', len(result['updates']))
