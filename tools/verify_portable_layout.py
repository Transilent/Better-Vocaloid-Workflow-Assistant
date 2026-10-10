"""Test exactly the selected runtime in an ephemeral tree, without creating a delivery archive."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def native(path):
    return '\\\\?\\'+str(Path(path).absolute()) if os.name == 'nt' else str(path)


def main():
    sys.path.insert(0, str(ROOT/'tools'))
    from build_portable import package_sources, checked_path
    work = ROOT/'work'
    work.mkdir(exist_ok=True)
    temporary_base = Path(tempfile.gettempdir()).resolve()
    directory = Path(tempfile.mkdtemp(prefix='bvwa-qa-', dir=temporary_base)).resolve()
    if directory.parent != temporary_base or not directory.name.startswith('bvwa-qa-'):
        raise ValueError('Invalid temporary test directory')
    try:
        mapping = {name: name for name in json.loads((ROOT/'source-files.json').read_text(encoding='utf-8'))['files']}
        mapping.update({e['file']: e['file'] for e in json.loads((ROOT/'dependencies/manifest.json').read_text(encoding='utf-8'))['entries']})
        mapping.update(package_sources())
        # Source-checkout inventories can include files intentionally pruned from the runtime.
        from optimize_inventory import exclusion
        mapping = {name: source for name, source in mapping.items() if not exclusion(name)}
        for name, source_name in mapping.items():
            source = checked_path(ROOT, source_name)
            target = checked_path(directory, name)
            if not source.is_file():
                raise FileNotFoundError(source_name)
            Path(native(target.parent)).mkdir(parents=True, exist_ok=True)
            if source.stat().st_size >= 8*1024**2:
                # Only immutable model/native runtime assets share storage. All editable files are copied.
                os.link(native(source), native(target))
            else:
                shutil.copy2(native(source), native(target))
        interpreter = directory/'dependencies/vocal2midi/python/python.exe'
        print('Testing curated runtime; excluded files are physically absent.', flush=True)
        result = subprocess.run([str(interpreter), '-B', str(directory/'tests/run_tests.py')], cwd=directory,
                                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
        reports = ROOT/'work/test-results'
        reports.mkdir(parents=True, exist_ok=True)
        source_report = directory/'work/test-results/regression.json'
        if source_report.exists():
            shutil.copy2(source_report, reports/'portable-layout-regression.json')
            for log in (directory/'work/test-results').glob('*.log'):
                shutil.copy2(log, reports/('portable-'+log.name))
        return result.returncode
    finally:
        # Verify the final absolute target again immediately before recursive removal.
        if directory.parent != temporary_base or not directory.name.startswith('bvwa-qa-'):
            raise ValueError('Refusing removal of unexpected test directory')
        shutil.rmtree(native(directory))


if __name__ == '__main__':
    sys.exit(main())
