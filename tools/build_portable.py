"""Build a ZIP64 release using explicit source and dependency inventories."""
import argparse
import copy
import hashlib
import json
import struct
import sys
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import native_path
BLOCK = 4 * 1024**2
FORBIDDEN = {'cache', 'jobs', 'publish-packages', 'quality-test', 'work', '.git'}
LAYOUT_ROOTS = ('VWA', 'BVWA')
DEFAULT_LAYOUT_ROOT = 'VWA'


def _layout_root(value):
    if value not in LAYOUT_ROOTS:
        raise ValueError('Unknown portable layout root: ' + str(value))
    return value


def portable_archive_name(variant='full', layout_root=DEFAULT_LAYOUT_ROOT):
    """Return the canonical archive name for a portable variant."""
    if variant not in ('full', 'jpn'):
        raise ValueError('Unknown portable variant: ' + str(variant))
    layout_root = _layout_root(layout_root)
    suffix = '-JPN' if variant == 'jpn' else ''
    return f'{layout_root}-Windows-x64{suffix}.zip'

def digest(path):
    h = hashlib.sha256()
    with Path(native_path(path)).open('rb') as stream:
        for chunk in iter(lambda: stream.read(BLOCK), b''):
            h.update(chunk)
    return h.hexdigest()

def checked_path(root, name):
    if '\\' in name or Path(name).is_absolute():
        raise ValueError('Invalid release path: ' + name)
    path = (root / name).resolve()
    if not path.is_relative_to(root) or set(Path(name).parts) & FORBIDDEN:
        raise ValueError('Private or escaping release path: ' + name)
    return Path(native_path(path))

def package_sources():
    entries = json.loads(inventory_path('portable-files.json').read_text(encoding='utf-8'))['entries']
    if len({entry['file'] for entry in entries}) != len(entries):
        raise ValueError('Duplicate portable destination')
    for entry in entries:
        checked_path(ROOT, entry['file'])
        checked_path(ROOT, entry['source'])
    return {entry['file']: entry['source'] for entry in entries}


def inventory_path(name):
    canonical = ROOT / 'packaging' / name
    return canonical if canonical.is_file() else ROOT / name


def release_notes(version):
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    for section in changelog.split('\n## ')[1:]:
        heading, _, notes = section.partition('\n')
        if heading.split(' ', 1)[0] == version:
            return notes.strip()
    raise ValueError('Missing changelog section for release ' + version)

def package_selection(variant='full'):
    """Select payloads without changing the installed or shipped runtime manifest.

    The manifest remains complete so the pinned Chinese model can be installed
    later and program-only updates retain the same runtime identity.
    """
    if variant not in ('full', 'jpn'):
        raise ValueError('Unknown portable variant: ' + str(variant))
    sources = package_sources()
    dependency = json.loads((ROOT / 'dependencies/manifest.json').read_text(encoding='utf-8'))
    entries = dependency['entries']
    expected = {entry['file']: entry for entry in entries}
    if len(expected) != len(entries):
        raise ValueError('Duplicate dependency destination')
    omitted = {}
    if variant == 'jpn':
        specification = json.loads((ROOT / 'components/runtime-features.json').read_text(encoding='utf-8'))['features']['chinese']
        prefix = 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/'
        for entry in specification['files']:
            name = entry['file']
            pinned = expected.get(name)
            if (not name.startswith(prefix) or name in omitted or not pinned
                    or (entry['bytes'], entry['sha256']) != (pinned['bytes'], pinned['sha256'])):
                raise ValueError('Chinese feature and runtime inventories disagree: ' + name)
            omitted[name] = pinned
        if not omitted or specification['bytes'] != sum(item['bytes'] for item in omitted.values()):
            raise ValueError('Incomplete Chinese feature inventory')
        if any(name.startswith(prefix) and name not in omitted for name in set(sources) | set(expected)):
            raise ValueError('Unreviewed Qwen files in portable inputs')
        for name, source in sources.items():
            if source.startswith(prefix) and name not in omitted:
                raise ValueError('Qwen payload aliased by portable mapping: ' + name)
        sources = {name: source for name, source in sources.items() if name not in omitted}
        expected = {name: entry for name, entry in expected.items() if name not in omitted}
    return sources, expected, list(omitted.values())


def portable_plan(variant='full', layout_root=DEFAULT_LAYOUT_ROOT):
    """Read-only exact payload plan; no archives, stages or metadata are written."""
    layout_root = _layout_root(layout_root)
    from tools.verify_source_layout import verify
    verify(ROOT)
    sources, dependencies, omitted = package_selection(variant)
    entries = []
    for name in sorted(set(sources) | set(dependencies)):
        path = checked_path(ROOT, sources.get(name, name))
        if not path.is_file():
            raise FileNotFoundError(name)
        entries.append({'file': name, 'source': sources.get(name, name), 'bytes': path.stat().st_size})
    return {'variant': variant, 'layout_root': layout_root, 'files': len(entries),
            'bytes': sum(item['bytes'] for item in entries),
            'runtime_sha256': digest(ROOT / 'dependencies/manifest.json'),
            'omitted': omitted, 'omitted_bytes': sum(item['bytes'] for item in omitted), 'entries': entries}


def _check_output_variant(output, variant, layout_root=DEFAULT_LAYOUT_ROOT):
    _layout_root(layout_root)
    if variant == 'jpn' and (output.suffix.lower() != '.zip' or not output.stem.upper().endswith('-JPN')):
        raise ValueError('The JPN archive filename must end with -JPN.zip')


def _portable_inventory(entries, variant, omitted, layout_root=DEFAULT_LAYOUT_ROOT):
    result = {'format': 1, 'layout_root': _layout_root(layout_root), 'entries': entries}
    if variant != 'full':
        result.update(variant=variant, omitted=omitted)
    return result


def _portable_metadata(report):
    variant = report.get('variant', 'full')
    layout_root = _layout_root(report.get('layout_root', DEFAULT_LAYOUT_ROOT))
    release = {'variant': variant, 'layout_root': layout_root}
    result = {'variant': variant, 'archive': report['archive'],
              'parts': report.get('parts', []), 'portable_release': release,
              'layout_root': layout_root}
    if 'runtime_sha256' in report:
        result['runtime_sha256'] = report['runtime_sha256']
    return result


def write_portable_metadata(report, target):
    """Write portable metadata separately from program update catalogs."""
    target = Path(target).resolve()
    if target.name == 'release-assets.json':
        raise ValueError('Portable metadata must not replace release-assets.json')
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    download = _portable_metadata(report)
    data = dict(download)
    data['portable_downloads'] = {report.get('variant', 'full'): download}
    with target.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    return data


def build(output, runtime, compression=6, variant='full', layout_root=DEFAULT_LAYOUT_ROOT):
    layout_root = _layout_root(layout_root)
    from tools.verify_source_layout import verify
    verify(ROOT)
    _check_output_variant(output, variant, layout_root)
    source_names, expected, omitted = package_selection(variant)
    names = sorted(set(source_names) | set(expected))
    sources = []
    total = 0
    for name in names:
        path = checked_path(ROOT, source_names.get(name, name))
        if not path.is_file():
            path = checked_path(runtime, name)
        if not path.is_file():
            raise FileNotFoundError(name)
        sources.append((name, path))
        total += path.stat().st_size
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    inventory = []
    done = 0
    last = started = time.monotonic()
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=compression, allowZip64=True) as archive:
        for name, path in sources:
            h = hashlib.sha256()
            size = 0
            info = zipfile.ZipInfo.from_file(path, arcname=layout_root + '/' + name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info._compresslevel = compression
            with path.open('rb') as src, archive.open(info, 'w', force_zip64=True) as dst:
                for chunk in iter(lambda: src.read(BLOCK), b''):
                    h.update(chunk)
                    size += len(chunk)
                    dst.write(chunk)
            entry = {'file': name, 'bytes': size, 'sha256': h.hexdigest()}
            if name in expected and (entry['bytes'], entry['sha256']) != (expected[name]['bytes'], expected[name]['sha256']):
                raise ValueError('Dependency changed: ' + name)
            inventory.append(entry)
            done += size
            if time.monotonic() - last > 15:
                print(f'ZIP {done / 1024**3:.2f}/{total / 1024**3:.2f} GiB', flush=True)
                last = time.monotonic()
        archive.writestr(layout_root + '/release-inventory.json', json.dumps(_portable_inventory(inventory, variant, omitted, layout_root), ensure_ascii=False, indent=2).encode('utf-8'))
    sha = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(sha + '  ' + output.name + '\n', encoding='ascii')
    report = {'archive': {'name': output.name, 'bytes': output.stat().st_size, 'sha256': sha},
              'variant': variant, 'layout_root': layout_root,
              'runtime_sha256': digest(ROOT / 'dependencies/manifest.json'),
              'omitted_bytes': sum(item['bytes'] for item in omitted),
              'files': len(inventory) + 1, 'uncompressed_bytes': total, 'seconds': round(time.monotonic()-started, 2)}
    output.with_suffix('.build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return report

def split(archive, size_mib):
    parts = []
    with archive.open('rb') as source:
        number = 1
        while True:
            remaining = size_mib * 1024**2
            first = source.read(min(remaining, BLOCK))
            if not first:
                break
            path = archive.with_name(archive.name + f'.{number:03d}')
            if path.exists():
                raise FileExistsError(path)
            h = hashlib.sha256()
            written = 0
            with path.open('xb') as target:
                chunk = first
                while chunk:
                    target.write(chunk)
                    h.update(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
                    chunk = source.read(min(remaining, BLOCK)) if remaining else b''
            parts.append({'name': path.name, 'bytes': written, 'sha256': h.hexdigest()})
            print(f'Split part {number}: {written / 1024**2:.1f} MiB', flush=True)
            number += 1
    return parts


def write_release_metadata(report, folder, migration=None):
    """Finalize existing archives without rebuilding the large portable ZIP."""
    if report.get('variant', 'full') != 'full':
        raise ValueError('JPN portable metadata must stay separate from the full update catalog')
    folder = Path(folder).resolve()
    version = json.loads(inventory_path('version.json').read_text(encoding='utf-8'))['version']
    runtime_sha = digest(ROOT / 'dependencies/manifest.json')
    public = {'version': version, 'archive': report['archive'], 'parts': report['parts'],
              'release_notes': release_notes(version), 'layout_root': report.get('layout_root', DEFAULT_LAYOUT_ROOT)}
    from tools.update_apply import validate_destination
    destination = validate_destination(folder, 'release-assets.json')
    existing = json.loads(destination.read_text(encoding='utf-8')) if destination.is_file() else None
    if isinstance(existing, dict) and 'updates' in existing:
        # Adding portable downloads must not silently replace a verified
        # multi-runtime catalog with the older two-slot format.
        from tools.build_update_catalog import verify_catalog_assets
        if existing.get('version') != version or existing.get('target_runtime_sha256') != runtime_sha:
            raise ValueError('Existing update catalog does not match this release.')
        verify_catalog_assets(existing, folder)
        if migration:
            data = json.loads(Path(migration).read_text(encoding='utf-8'))
            if not any(data.get('update') == route['update'] and data.get('runtime_upgrade') == route['runtime_upgrade']
                       for route in existing['updates']):
                raise ValueError('Migration metadata is absent from the verified update catalog.')
        combined = dict(existing, **public)
        from common import atomic_json
        atomic_json(destination, combined)
        return combined
    from tools.build_app_update import build as build_app_update
    if migration:
        data = json.loads(Path(migration).read_text(encoding='utf-8'))
        item = data['runtime_upgrade']
        if item['version'] != version or item['target_runtime_sha256'] != runtime_sha:
            raise ValueError('Runtime migration does not match this release.')
        for asset in (data['update'], item):
            path = folder / asset['name']
            if path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
                raise ValueError('Migration archive checksum mismatch: ' + asset['name'])
        public.update(update=data['update'], runtime_upgrade=item)
        name = 'BVWA-Program-Update.zip'
    else:
        name = 'BVWA-App-Update.zip'
    program = folder / name
    if program.is_file():
        with zipfile.ZipFile(program) as zipped:
            inventory = json.loads(zipped.read('update-inventory.json'))
        if inventory['version'] != version or inventory['runtime_sha256'] != runtime_sha:
            raise ValueError('Existing program archive does not match this release.')
        update = {'name':name,'bytes':program.stat().st_size,'sha256':digest(program),'runtime_sha256':runtime_sha}
    else:
        update = build_app_update(program)
    public['program_update' if migration else 'update'] = update
    from common import atomic_json
    atomic_json(destination, public)
    return public


def _extra_has_id(extra, wanted):
    cursor = 0
    while cursor < len(extra):
        if len(extra) - cursor < 4:
            raise ValueError('Corrupt ZIP extra field')
        field_id, size = struct.unpack_from('<HH', extra, cursor)
        cursor += 4
        end = cursor + size
        if end > len(extra):
            raise ValueError('Corrupt ZIP extra field')
        if field_id == wanted:
            return True
        cursor = end
    return False


def _rewrite_portable_extra(extra, filename):
    """Remove stale ZIP64 values and refresh an Info-ZIP Unicode path."""
    encoded = filename.encode('utf-8')
    rewritten = []
    cursor = 0
    while cursor < len(extra):
        if len(extra) - cursor < 4:
            raise ValueError('Corrupt ZIP extra field')
        field_id, size = struct.unpack_from('<HH', extra, cursor)
        payload_start = cursor + 4
        end = payload_start + size
        if end > len(extra):
            raise ValueError('Corrupt ZIP extra field')
        payload = extra[payload_start:end]
        if field_id == 0x0001:
            pass
        elif field_id == 0x7075 and payload and payload[0] == 1:
            payload = b'\x01' + struct.pack('<L', zlib.crc32(encoded) & 0xffffffff) + encoded
            rewritten.append(struct.pack('<HH', field_id, len(payload)) + payload)
        else:
            rewritten.append(struct.pack('<HH', field_id, size) + payload)
        cursor = end
    return b''.join(rewritten)


def _read_portable_base(original):
    names = original.namelist()
    found = []
    for root in LAYOUT_ROOTS:
        member = root + '/release-inventory.json'
        matches = [name for name in names if name == member]
        if len(matches) > 1:
            raise ValueError('Duplicate portable inventory in base ZIP')
        if matches:
            found.append((root, member))
    if len(found) != 1:
        raise ValueError('Base ZIP must contain exactly one VWA or BVWA portable root')
    root, member = found[0]
    data = json.loads(original.read(member))
    if data.get('format') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('Invalid portable inventory in base ZIP')
    declared = data.get('layout_root')
    if declared is not None and declared != root:
        raise ValueError('Portable inventory layout root does not match its path')
    entries = data['entries']
    if any(not isinstance(entry, dict) or not isinstance(entry.get('file'), str) for entry in entries):
        raise ValueError('Invalid portable inventory entry in base ZIP')
    if len({entry['file'] for entry in entries}) != len(entries):
        raise ValueError('Duplicate portable inventory entry in base ZIP')
    return root, entries


def _local_records(original):
    records = {}
    offsets = set()
    infos = sorted(original.infolist(), key=lambda info: info.header_offset)
    for index, info in enumerate(infos):
        if info.filename in records or info.header_offset in offsets:
            raise ValueError('Duplicate or overlapping portable ZIP record')
        end = infos[index + 1].header_offset if index + 1 < len(infos) else original.start_dir
        if end < info.header_offset:
            raise ValueError('Invalid portable ZIP local record order')
        records[info.filename] = (info, end)
        offsets.add(info.header_offset)
    return records


def _local_data_offset(original, info):
    original.fp.seek(info.header_offset)
    header = original.fp.read(30)
    if len(header) != 30:
        raise ValueError('Incomplete ZIP local header')
    fields = struct.unpack('<4s5H3L2H', header)
    signature, _, flags, compress_type, _, _, _, compress_size, file_size, name_len, extra_len = fields
    if signature != b'PK\x03\x04' or compress_type != info.compress_type:
        raise ValueError('Invalid ZIP local header')
    if flags & 0x0001:
        raise ValueError('Encrypted portable ZIP members are not supported')
    raw_name = original.fp.read(name_len)
    local_extra = original.fp.read(extra_len)
    if len(raw_name) != name_len or len(local_extra) != extra_len:
        raise ValueError('Incomplete ZIP local header')
    local_name = raw_name.decode('utf-8' if flags & 0x0800 else 'cp437')
    cursor = 0
    while cursor + 4 <= len(local_extra):
        field_id, size = struct.unpack_from('<HH', local_extra, cursor)
        payload_end = cursor + 4 + size
        if payload_end > len(local_extra):
            raise ValueError('Corrupt ZIP local extra field')
        if field_id == 0x7075:
            payload = local_extra[cursor + 4:payload_end]
            if len(payload) >= 5 and payload[0] == 1 and struct.unpack_from('<L', payload, 1)[0] == (
                    zlib.crc32(raw_name) & 0xffffffff):
                local_name = payload[5:].decode('utf-8')
        cursor = payload_end
    if local_name != info.filename:
        raise ValueError('ZIP local and central filenames disagree')
    zip64 = (compress_size == 0xffffffff or file_size == 0xffffffff
             or _extra_has_id(local_extra, 0x0001))
    return flags, zip64, info.header_offset + 30 + name_len + extra_len


def _copy_compressed_record(original, destination, source_info, record_end, new_name):
    flags, zip64, data_start = _local_data_offset(original, source_info)
    data_end = data_start + source_info.compress_size
    if record_end < data_end:
        raise ValueError('Incomplete compressed ZIP member')
    info = copy.copy(source_info)
    info.filename = new_name
    if hasattr(info, 'orig_filename'):
        info.orig_filename = new_name
    info.extra = _rewrite_portable_extra(info.extra, new_name)
    info.flag_bits = flags
    destination.fp.seek(destination.start_dir)
    info.header_offset = destination.fp.tell()
    destination._writecheck(info)
    destination._didModify = True
    destination.fp.write(info.FileHeader(zip64))
    original.fp.seek(data_start)
    remaining = record_end - data_start
    while remaining:
        chunk = original.fp.read(min(BLOCK, remaining))
        if not chunk:
            raise ValueError('Incomplete ZIP local record')
        destination.fp.write(chunk)
        remaining -= len(chunk)
    destination.filelist.append(info)
    destination.NameToInfo[new_name] = info
    destination.start_dir = destination.fp.tell()


def update_from_zip(output, base, compression=1, variant='full', layout_root=DEFAULT_LAYOUT_ROOT, *, base_layout_root=None):
    if base_layout_root is not None:
        _layout_root(base_layout_root)
    """Reuse verified compressed runtime members when only source changes.

    Compressed data and data descriptors are copied byte-for-byte. Local and
    central filename records are rebuilt when the VWA/BVWA root changes so the
    archive remains valid for UTF-8 names and ZIP64 offsets.
    """
    layout_root = _layout_root(layout_root)
    from tools.verify_source_layout import verify
    verify(ROOT)
    _check_output_variant(output, variant, layout_root)
    expected_base = base.with_suffix(base.suffix + '.sha256').read_text(encoding='ascii').split()[0]
    if digest(base) != expected_base:
        raise ValueError('Base ZIP checksum mismatch')
    source_names, dependencies, omitted = package_selection(variant)
    names = sorted(set(source_names) | set(dependencies))
    if output.exists():
        raise FileExistsError(output)
    started = time.monotonic()
    inventory = []
    reused = changed = 0
    with zipfile.ZipFile(base) as original, zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=compression, allowZip64=True) as destination:
        base_root, base_entries = _read_portable_base(original)
        if base_layout_root is not None and base_root != base_layout_root:
            raise ValueError('Base ZIP layout root does not match --base-layout-root')
        old_entries = {entry['file']: entry for entry in base_entries}
        records = _local_records(original)
        for name in names:
            old = old_entries.get(name)
            if name in source_names:
                path = checked_path(ROOT, source_names[name])
                new = {'file': name, 'bytes': path.stat().st_size, 'sha256': digest(path)}
            else:
                new = dict(dependencies[name])
            if name in dependencies and (new['bytes'], new['sha256']) != (dependencies[name]['bytes'], dependencies[name]['sha256']):
                raise ValueError('Frozen dependency source changed: ' + name)
            if old == new:
                old_member = base_root + '/' + name
                if old_member not in records:
                    raise ValueError('Portable inventory entry is missing from base ZIP: ' + name)
                source_info, record_end = records[old_member]
                _copy_compressed_record(original, destination, source_info, record_end,
                                        layout_root + '/' + name)
                reused += 1
            else:
                if name not in source_names:
                    raise ValueError('New binary dependency requires a complete build: ' + name)
                destination.write(path, arcname=layout_root + '/' + name)
                changed += 1
            inventory.append(new)
        destination.writestr(layout_root + '/release-inventory.json', json.dumps(_portable_inventory(inventory, variant, omitted, layout_root), ensure_ascii=False, indent=2).encode('utf-8'))
    sha = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(sha + '  ' + output.name + '\n', encoding='ascii')
    report = {'archive': {'name': output.name, 'bytes': output.stat().st_size, 'sha256': sha},
              'variant': variant, 'layout_root': layout_root,
              'runtime_sha256': digest(ROOT / 'dependencies/manifest.json'),
              'omitted_bytes': sum(item['bytes'] for item in omitted),
              'files': len(inventory)+1, 'uncompressed_bytes': sum(e['bytes'] for e in inventory),
              'reused_compressed_files': reused, 'updated_source_files': changed,
              'seconds': round(time.monotonic()-started, 2)}
    output.with_suffix('.build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return report
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan-profile',choices=('complete','music','japanese'),help='Inspect dependency space without creating an archive')
    parser.add_argument('--variant', choices=('full', 'jpn'), default='full')
    parser.add_argument('--layout-root', choices=LAYOUT_ROOTS, default=DEFAULT_LAYOUT_ROOT)
    parser.add_argument('--print-default-name', action='store_true',
                        help='Print the default archive name for the selected variant and layout root')
    parser.add_argument('--default-name', choices=tuple(LAYOUT_ROOTS), help=argparse.SUPPRESS)
    parser.add_argument('--plan-only', action='store_true', help='Print exact portable inputs without creating an archive')
    parser.add_argument('--portable-metadata', type=Path, help='Write separate portable metadata; never build a program update implicitly')
    parser.add_argument('--runtime-dir', type=Path, default=ROOT)
    parser.add_argument('--base-zip', type=Path, help='Reuse unchanged members from a checksum-verified previous ZIP')
    parser.add_argument('--base-layout-root', choices=LAYOUT_ROOTS,
                        help='Require the base ZIP to use this VWA or BVWA root')
    parser.add_argument('--compression', type=int, choices=range(10), default=6)
    parser.add_argument('--split-mib', type=int, default=0)
    parser.add_argument('--migration-metadata', type=Path, help='Verified runtime-upgrade.build.json for legacy online upgrades')
    parser.add_argument('--user-test-confirmed', action='store_true', help='Use only after explicit user testing confirmation')
    args = parser.parse_args()
    if args.default_name is not None or args.print_default_name:
        selected_root = args.default_name or args.layout_root
        print(portable_archive_name(args.variant, selected_root))
        sys.exit(0)
    if args.plan_only:
        print(json.dumps(portable_plan(args.variant, args.layout_root), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.plan_profile:
        from dependency_profiles import installation_plan
        print(json.dumps(installation_plan(args.plan_profile),indent=2))
        sys.exit(0)
    if args.output is None:
        args.output = ROOT / portable_archive_name(args.variant, args.layout_root)
    if not args.user_test_confirmed:
        parser.error('Local user testing must be confirmed before creating a portable release archive.')
    if args.split_mib and not 1 <= args.split_mib < 2048:
        parser.error('Release parts must be between 1 and 2047 MiB')
    if args.variant == 'jpn' and args.split_mib and not args.portable_metadata:
        parser.error('Split JPN archives require separate --portable-metadata')
    if args.portable_metadata and (args.portable_metadata.name == 'release-assets.json' or args.portable_metadata.exists()):
        parser.error('Choose a new portable metadata file distinct from release-assets.json')
    _check_output_variant(args.output, args.variant, args.layout_root)
    report = (update_from_zip(args.output.resolve(), args.base_zip.resolve(), args.compression, args.variant,
                                  args.layout_root, base_layout_root=args.base_layout_root)
              if args.base_zip else build(args.output, args.runtime_dir.resolve(), args.compression, args.variant, args.layout_root))
    report['layout_root'] = args.layout_root
    if args.split_mib:
        report['parts'] = split(args.output.resolve(), args.split_mib)
        if not args.portable_metadata:
            write_release_metadata(report, args.output.resolve().parent, args.migration_metadata)
    if args.portable_metadata:
        write_portable_metadata(report, args.portable_metadata)
