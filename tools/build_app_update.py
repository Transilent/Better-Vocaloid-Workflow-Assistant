"""Create a small program-only update after confirmed local user testing."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.update_apply import (MAX_QUICK_PROGRAM_FILES, desktop_identity, program_name,
                                validate_file, validate_quick_program)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory_path(name):
    canonical = ROOT / 'packaging' / name
    return canonical if canonical.is_file() else ROOT / name


def build(output):
    from tools.verify_source_layout import verify
    output = Path(output).resolve()
    program_name({'name':output.name})
    verify(ROOT)
    version_info = json.loads(inventory_path('version.json').read_text(encoding='utf-8'))
    version, identity = version_info['version'], desktop_identity(version_info)
    if not identity:
        raise ValueError('This tool creates program updates for the Qt Quick desktop.')
    runtime_sha = file_hash(ROOT / 'dependencies/manifest.json')
    entries = json.loads(inventory_path('portable-files.json').read_text(encoding='utf-8'))['entries']
    selected = []
    for entry in entries:
        if entry['file'] == 'config.json' or entry['file'].startswith(('dependencies/', 'models/', 'vendor/')):
            continue
        if (entry['file'].startswith('components/')
                and entry['file'] != 'components/world-LICENSE.txt'
                and not entry['file'].startswith('components/pedalboard')):
            continue
        source = ROOT / entry['source']
        record = {'file': entry['file'], 'bytes': source.stat().st_size, 'sha256': file_hash(source)}
        validate_file(record)
        selected.append((record, source))
    validate_quick_program(record['file'] for record, _ in selected)
    if len(selected) + 1 > MAX_QUICK_PROGRAM_FILES:  # Includes update-inventory.json.
        raise ValueError('Quick update contains too many program entries')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for entry, source in selected:
            zipped.write(source, entry['file'])
        zipped.writestr('update-inventory.json', json.dumps({'version': version, 'runtime_sha256': runtime_sha, **identity,
                         'entries': [entry for entry, _ in selected]}, ensure_ascii=False, indent=2))
    return {'name': output.name, 'bytes': output.stat().st_size, 'sha256': file_hash(output),
            'runtime_sha256': runtime_sha, **identity}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--user-test-confirmed', action='store_true')
    args = parser.parse_args()
    if not args.user_test_confirmed:
        parser.error('Local user testing must be confirmed before creating a distributable update archive.')
    try:
        program_name({'name':args.output.name})
    except ValueError:
        parser.error('Use VWA-App-Update.zip or VWA-Program-Update.zip for the program update asset '
                     '(BVWA-App-Update.zip and BVWA-Program-Update.zip are accepted for legacy releases).')
    print(json.dumps(build(args.output), indent=2))
