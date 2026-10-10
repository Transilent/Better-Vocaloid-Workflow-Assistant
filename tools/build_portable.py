"""Build a ZIP64 release using explicit source and dependency inventories."""
import argparse
import copy
import hashlib
import json
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCK = 4 * 1024**2
FORBIDDEN = {'cache', 'jobs', 'publish-packages', 'quality-test', 'work', '.git'}

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(BLOCK), b''):
            h.update(chunk)
    return h.hexdigest()

def checked_path(root, name):
    if '\\' in name or Path(name).is_absolute():
        raise ValueError('Invalid release path: ' + name)
    path = (root / name).resolve()
    if not path.is_relative_to(root) or set(Path(name).parts) & FORBIDDEN:
        raise ValueError('Private or escaping release path: ' + name)
    return path

def package_sources():
    entries = json.loads((ROOT / 'portable-files.json').read_text(encoding='utf-8'))['entries']
    if len({entry['file'] for entry in entries}) != len(entries):
        raise ValueError('Duplicate portable destination')
    for entry in entries:
        checked_path(ROOT, entry['file'])
        checked_path(ROOT, entry['source'])
    return {entry['file']: entry['source'] for entry in entries}


def release_notes(version):
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    for section in changelog.split('\n## ')[1:]:
        heading, _, notes = section.partition('\n')
        if heading.split(' ', 1)[0] == version:
            return notes.strip()
    raise ValueError('Missing changelog section for release ' + version)

def build(output, runtime, compression=6):
    source_names = package_sources()
    dependency = json.loads((ROOT / 'dependencies/manifest.json').read_text(encoding='utf-8'))
    names = sorted(set(source_names) | {e['file'] for e in dependency['entries']})
    expected = {e['file']: e for e in dependency['entries']}
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
            info = zipfile.ZipInfo.from_file(path, arcname='BVWA/' + name)
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
        archive.writestr('BVWA/release-inventory.json', json.dumps({'format': 1, 'entries': inventory}, ensure_ascii=False, indent=2).encode('utf-8'))
    sha = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(sha + '  ' + output.name + '\n', encoding='ascii')
    report = {'archive': {'name': output.name, 'bytes': output.stat().st_size, 'sha256': sha},
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


def update_from_zip(output, base, compression=1):
    """Reuse verified compressed runtime members when only source changes.

    Copy complete local records and let ZipFile rebuild the central directory,
    including new ZIP64 offsets. The source ZIP must match its SHA256 sidecar.
    """
    expected_base = base.with_suffix(base.suffix + '.sha256').read_text(encoding='ascii').split()[0]
    if digest(base) != expected_base:
        raise ValueError('Base ZIP checksum mismatch')
    source_names = package_sources()
    dependency = json.loads((ROOT / 'dependencies/manifest.json').read_text(encoding='utf-8'))
    dependencies = {e['file']: e for e in dependency['entries']}
    names = sorted(set(source_names) | set(dependencies))
    if output.exists():
        raise FileExistsError(output)
    started = time.monotonic()
    inventory = []
    reused = changed = 0
    with zipfile.ZipFile(base) as original, zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=compression, allowZip64=True) as destination:
        old_entries = {e['file']: e for e in json.loads(original.read('BVWA/release-inventory.json'))['entries']}
        records = original.infolist()
        ends = {info.filename: records[i+1].header_offset if i+1 < len(records) else original.start_dir for i, info in enumerate(records)}
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
                source_info = original.getinfo('BVWA/' + name)
                info = copy.copy(source_info)
                destination.fp.seek(destination.start_dir)
                info.header_offset = destination.fp.tell()
                destination._writecheck(info)
                destination._didModify = True
                original.fp.seek(source_info.header_offset)
                remaining = ends[source_info.filename] - source_info.header_offset
                while remaining:
                    chunk = original.fp.read(min(BLOCK, remaining))
                    if not chunk:
                        raise ValueError('Incomplete ZIP local record')
                    destination.fp.write(chunk)
                    remaining -= len(chunk)
                destination.filelist.append(info)
                destination.NameToInfo[info.filename] = info
                destination.start_dir = destination.fp.tell()
                reused += 1
            else:
                if name not in source_names:
                    raise ValueError('New binary dependency requires a complete build: ' + name)
                destination.write(path, arcname='BVWA/' + name)
                changed += 1
            inventory.append(new)
        destination.writestr('BVWA/release-inventory.json', json.dumps({'format': 1, 'entries': inventory}, ensure_ascii=False, indent=2).encode('utf-8'))
    sha = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(sha + '  ' + output.name + '\n', encoding='ascii')
    report = {'archive': {'name': output.name, 'bytes': output.stat().st_size, 'sha256': sha},
              'files': len(inventory)+1, 'uncompressed_bytes': sum(e['bytes'] for e in inventory),
              'reused_compressed_files': reused, 'updated_source_files': changed,
              'seconds': round(time.monotonic()-started, 2)}
    output.with_suffix('.build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--runtime-dir', type=Path, default=ROOT)
    parser.add_argument('--base-zip', type=Path, help='Reuse unchanged members from a checksum-verified previous ZIP')
    parser.add_argument('--compression', type=int, choices=range(10), default=6)
    parser.add_argument('--split-mib', type=int, default=0)
    parser.add_argument('--user-test-confirmed', action='store_true', help='Use only after explicit user testing confirmation')
    args = parser.parse_args()
    if not args.user_test_confirmed:
        parser.error('Local user testing must be confirmed before creating a portable release archive.')
    report = update_from_zip(args.output.resolve(), args.base_zip.resolve(), args.compression) if args.base_zip else build(args.output, args.runtime_dir.resolve(), args.compression)
    if args.split_mib:
        if not 1 <= args.split_mib < 2048:
            raise ValueError('Release parts must be between 1 and 2047 MiB')
        report['parts'] = split(args.output.resolve(), args.split_mib)
        from build_app_update import build as build_app_update
        update = build_app_update(args.output.resolve().parent / 'BVWA-App-Update.zip')
        version = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))['version']
        notes = release_notes(version)
        public = {'version': version, 'archive': report['archive'], 'parts': report['parts'],
                  'update': update, 'release_notes': notes}
        (args.output.resolve().parent / 'release-assets.json').write_text(json.dumps(public, indent=2), encoding='utf-8')
