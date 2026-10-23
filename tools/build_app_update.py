"""Create a small program-only update after confirmed local user testing."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.update_apply import program_name, validate_file


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory_path(name):
    canonical = ROOT / 'packaging' / name
    return canonical if canonical.is_file() else ROOT / name


def require_compatible_clients(records):
    """A new installer's allowlist cannot grant capabilities to old clients."""
    clients = json.loads(inventory_path('update-clients.json').read_text(encoding='utf-8'))['clients']
    for client in clients:
        allowed = set(client['ROOT_FILES']) | set(client['TOOL_FILES'])
        unsupported = []
        for record in records:
            name = record['file']
            path = PurePosixPath(name)
            asset = len(path.parts) == 2 and path.parts[0] == 'assets' and path.suffix in ('.svg', '.ico', '.png')
            if name not in allowed and not asset:
                unsupported.append(name)
        if unsupported:
            raise ValueError('Client '+client['version']+' cannot accept these direct-update files: '+', '.join(unsupported)+
                             '. Build the compatible first hop with build_runtime_upgrade.py --base-ref '+client['commit']+
                             ' --program-variant '+client['version']+'; install the new helper before new modules.')


def build(output):
    from tools.verify_source_layout import verify
    output = Path(output).resolve()
    program_name({'name':output.name})
    verify(ROOT)
    version = json.loads(inventory_path('version.json').read_text(encoding='utf-8'))['version']
    runtime_sha = file_hash(ROOT / 'dependencies/manifest.json')
    entries = json.loads(inventory_path('portable-files.json').read_text(encoding='utf-8'))['entries']
    selected = []
    for entry in entries:
        if entry['file'] == 'config.json' or entry['file'].startswith(('dependencies/', 'models/', 'vendor/')):
            continue
        if entry['file'].startswith('components/') and entry['file'] != 'components/world-LICENSE.txt':
            continue
        source = ROOT / entry['source']
        record = {'file': entry['file'], 'bytes': source.stat().st_size, 'sha256': file_hash(source)}
        validate_file(record)
        selected.append((record, source))
    require_compatible_clients([record for record, _ in selected])
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for entry, source in selected:
            zipped.write(source, entry['file'])
        zipped.writestr('update-inventory.json', json.dumps({'version': version, 'runtime_sha256': runtime_sha,
                         'entries': [entry for entry, _ in selected]}, ensure_ascii=False, indent=2))
    return {'name': output.name, 'bytes': output.stat().st_size, 'sha256': file_hash(output), 'runtime_sha256': runtime_sha}


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
