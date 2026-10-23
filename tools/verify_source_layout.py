"""Refuse tests/releases when generated application files differ from src/."""
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    catalog = json.loads((root / 'packaging/source-files.json').read_text(encoding='utf-8'))['files']
    pairs = [(name, Path(name).name) for name in catalog
             if name.startswith('src/') and Path(name).suffix == '.py' and len(Path(name).parts) == 2]
    pairs.extend(('packaging/' + name, name) for name in
                 ('source-files.json', 'portable-files.json', 'config.example.json',
                  'publish-selectors.json', 'version.json', 'requirements-runtime.lock.txt'))
    mismatches = [target for source, target in pairs
                  if not (root / target).is_file() or (root / source).read_bytes() != (root / target).read_bytes()]
    if mismatches:
        raise ValueError('Generated application files differ from source: ' + ', '.join(mismatches) +
                         '. Run tools/Prepare-Source.ps1, then repeat validation.')
    return len(pairs)


if __name__ == '__main__':
    print('Verified generated source layout:', verify(Path(__file__).resolve().parents[1]))
