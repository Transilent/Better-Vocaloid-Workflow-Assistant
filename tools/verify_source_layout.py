"""Refuse tests/releases when generated application files differ from src/."""
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    catalog = json.loads((root / 'packaging/source-files.json').read_text(encoding='utf-8'))['files']
    if len(catalog) != len(set(catalog)):
        raise ValueError('packaging/source-files.json contains duplicate paths')
    catalogued_sources = {name for name in catalog
                          if name.startswith('src/') and Path(name).suffix == '.py'
                          and len(Path(name).parts) == 2}
    source_directory = root / 'src'
    discovered_sources = ({'src/' + path.name for path in source_directory.glob('*.py')}
                          if source_directory.is_dir() else set())
    unregistered = sorted(discovered_sources - catalogued_sources)
    missing = sorted(catalogued_sources - discovered_sources)
    if unregistered or missing:
        details = []
        if unregistered:
            details.append('unregistered src modules: ' + ', '.join(unregistered))
        if missing:
            details.append('catalogued but absent: ' + ', '.join(missing))
        raise ValueError('Source mapping is incomplete (' + '; '.join(details) +
                         '). Update packaging/source-files.json before preparation.')
    pairs = [(name, Path(name).name) for name in catalogued_sources]
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