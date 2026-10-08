"""Build a ZIP64 release using explicit source and dependency inventories."""
import argparse
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

def build(output, runtime, compression=1):
    source_names = json.loads((ROOT / 'source-files.json').read_text(encoding='utf-8'))['files']
    dependency = json.loads((ROOT / 'dependencies/manifest.json').read_text(encoding='utf-8'))
    names = sorted(set(source_names) | {e['file'] for e in dependency['entries']})
    expected = {e['file']: e for e in dependency['entries']}
    sources = []
    total = 0
    for name in names:
        path = checked_path(ROOT, name)
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

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--runtime-dir', type=Path, default=ROOT)
    parser.add_argument('--compression', type=int, choices=range(10), default=1)
    parser.add_argument('--split-mib', type=int, default=0)
    args = parser.parse_args()
    report = build(args.output, args.runtime_dir.resolve(), args.compression)
    if args.split_mib:
        if not 1 <= args.split_mib < 2048:
            raise ValueError('Release parts must be between 1 and 2047 MiB')
        report['parts'] = split(args.output.resolve(), args.split_mib)
        (args.output.resolve().parent / 'release-assets.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
