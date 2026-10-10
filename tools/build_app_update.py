"""Create a small program-only update after confirmed local user testing."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import file_hash
from tools.update_apply import validate_file


def build(output):
    version = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))['version']
    runtime_sha = file_hash(ROOT / 'dependencies/manifest.json')
    entries = json.loads((ROOT / 'portable-files.json').read_text(encoding='utf-8'))['entries']
    selected = []
    for entry in entries:
        if entry['file'] == 'config.json' or entry['file'].startswith(('dependencies/', 'models/', 'vendor/', 'components/')):
            continue
        source = ROOT / entry['source']
        record = {'file': entry['file'], 'bytes': source.stat().st_size, 'sha256': file_hash(source)}
        validate_file(record)
        selected.append((record, source))
    output = output.resolve()
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
    if args.output.name != 'BVWA-App-Update.zip':
        parser.error('The program update asset must be named BVWA-App-Update.zip.')
    print(json.dumps(build(args.output), indent=2))
