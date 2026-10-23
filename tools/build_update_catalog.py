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
from tools.update_apply import (RUNTIME_PROGRAM_FILES, _runtime_path, program_name, select_update_route,
                                validate_file, validate_destination)

LAUNCHER_MIGRATION = {'from': 'BVWA.exe', 'to': 'VWA.exe', 'legacy_alias': True}
COMPATIBILITY_TOOL_NAMES = {'VWA-Updater.exe', 'BVWA-Updater.exe'}
PORTABLE_VARIANTS = {'full', 'jpn'}
LEGACY_BOOTSTRAP_PATTERN = r'BVWA-(?:App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip'
CANONICAL_BOOTSTRAP_PATTERN = r'VWA-(?:App|Program)-Update(?:-v\d+\.\d+\.\d+)?\.zip'


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


def verify_catalog_assets(catalog, folder, *, target_manifest=None, external_reason=None):
    """Recheck all migration files and bind them to one target runtime manifest."""
    folder = Path(folder).resolve()
    if not isinstance(catalog, dict):
        raise ValueError('Catalog must be an object')
    routes = catalog.get('updates')
    if not isinstance(routes, list) or not routes:
        raise ValueError('Missing runtime update routes')
    target_sha, target_records, _ = _target_manifest(
        target_manifest, catalog.get('target_runtime_sha256'), external_reason)
    for route in routes:
        select_update_route(catalog, route['runtime_sha256'])
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
        with zipfile.ZipFile(runtime) as zipped:
            try:
                plan = json.loads(zipped.read('runtime-upgrade.json'))
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError('Runtime archive lacks a valid migration plan') from error
            if not isinstance(plan, dict) or any(
                    plan.get(key) != route['runtime_upgrade'].get(key)
                    for key in ('version', 'source_runtime_sha256', 'target_runtime_sha256')):
                raise ValueError('Runtime archive and its release record disagree')
            if plan.get('target_runtime_sha256') != target_sha:
                raise ValueError('Runtime archive targets a different checkout')
            records, _ = _runtime_zip_members(zipped, plan, target_records)
            embedded = _manifest_records(json.loads(zipped.read('dependencies/manifest.json')))
            if set(embedded) != set(target_records) or any(
                    not _same_record(embedded[name], target_records[name]) for name in embedded):
                raise ValueError('Runtime archive target manifest and bound manifest disagree')
            if records['dependencies/manifest.json']['sha256'] != target_sha:
                raise ValueError('Runtime manifest member digest does not match target')
    # Old clients can only read these two fields. Select the explicit legacy alias
    # when it exists; historical fixture catalogs used the old route update field.
    projected = []
    for field, name in (('update', 'BVWA-App-Update.zip'), ('program_update', 'BVWA-Program-Update.zip')):
        asset = catalog.get(field)
        if not isinstance(asset, dict) or asset.get('name') != name:
            raise ValueError('Missing legacy update projection: ' + field)
        matches = [route for route in routes if _legacy_projection(route) == asset]
        if len(matches) != 1:
            raise ValueError('Legacy update projection does not match a verified route')
        projected.append(matches[0])
    if catalog.get('runtime_upgrade') != projected[0]['runtime_upgrade']:
        raise ValueError('Legacy runtime migration does not match its bootstrap')
    covered = {route['runtime_sha256'] for route in projected}
    uncovered = [route['source_version'] for route in routes if route['runtime_sha256'] not in covered]
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

def build(folder, version, migrations, *, legacy_update, legacy_program, compatibility_tool=None,
          notes='', output='release-assets.json', portable=None, portable_variants=None,
          target_manifest=None, external_reason=None):
    folder = Path(folder).resolve()
    routes, labels, targets, manifests = [], {}, set(), []
    for entry in migrations:
        update, runtime = entry['update'], entry['runtime_upgrade']
        source = entry['source_version']
        if source in labels:
            raise ValueError('Duplicate source version: ' + source)
        if runtime['version'] != version:
            raise ValueError('Migration targets a different release')
        target_sha, target_records, reason = _target_manifest(target_manifest, runtime['target_runtime_sha256'], external_reason)
        manifests.append((target_sha, target_records, reason))
        route = {'source_version': source, 'runtime_sha256': runtime['source_runtime_sha256'],
                 'update': update, 'runtime_upgrade': runtime}
        if entry.get('legacy_update') is not None:
            route['legacy_update'] = entry['legacy_update']
        routes.append(route); labels[source] = route; targets.add(runtime['target_runtime_sha256'])
    if len(targets) != 1:
        raise ValueError('Migrations must share one target runtime')
    if len({manifest[0] for manifest in manifests}) != 1:
        raise ValueError('Migrations must share one target manifest')
    target_sha, target_records, target_reason = manifests[0]
    catalog = {'version': version, 'update_format': 2, 'release_notes': notes, 'updates': routes,
               'target_runtime_sha256': target_sha}
    for route in routes:
        select_update_route(catalog, route['runtime_sha256'])
    try:
        direct = labels[legacy_update]; program = labels[legacy_program]
    except KeyError as error:
        raise ValueError('A legacy projection has no matching migration') from error
    direct_legacy = _legacy_projection(direct)
    program_legacy = _legacy_projection(program)
    if (not isinstance(direct_legacy, dict) or direct_legacy.get('name') != 'BVWA-App-Update.zip' or
            not isinstance(program_legacy, dict) or program_legacy.get('name') != 'BVWA-Program-Update.zip'):
        raise ValueError('Unmodified clients require BVWA-App-Update.zip and BVWA-Program-Update.zip aliases')
    covered = {direct['runtime_sha256'], program['runtime_sha256']}
    uncovered = [route['source_version'] for route in routes if route['runtime_sha256'] not in covered]
    if uncovered:
        if not compatibility_tool:
            raise ValueError('Legacy runtimes need the one-time repair tool: ' + ', '.join(uncovered))
        catalog['compatibility_tool'] = dict(compatibility_tool, required_for=uncovered)
    catalog.update(update=direct_legacy, program_update=program_legacy,
                   runtime_upgrade=direct['runtime_upgrade'])
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
    target = validate_destination(folder, output)
    verify_catalog_assets(catalog, folder, target_manifest=target_manifest, external_reason=external_reason)
    from common import atomic_json
    atomic_json(target, catalog)
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--migration', action='append', required=True, help='SOURCE_VERSION=BUILD_REPORT.json')
    parser.add_argument('--legacy-update', default='0.4.1')
    parser.add_argument('--legacy-program', default='0.4.0')
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
    parser.add_argument('--user-test-confirmed', action='store_true')
    args = parser.parse_args()
    if not args.user_test_confirmed:
        parser.error('User confirmation is required before preparing release metadata.')
    version = json.loads((ROOT / 'packaging/version.json').read_text(encoding='utf-8'))['version']
    migrations = []
    for spec in args.migration:
        source, path = spec.split('=', 1)
        migrations.append(dict(json.loads(Path(path).read_text(encoding='utf-8')), source_version=source))
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
                   target_manifest=args.target_manifest, external_reason=args.external_build_reason)
    print('Verified update routes:', len(result['updates']))
