"""Prepare a fresh source checkout while preserving user data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from tools.verify_source_layout import verify
shell = Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
catalog = json.loads((APP/'packaging/source-files.json').read_text(encoding='utf-8'))['files']
with tempfile.TemporaryDirectory(prefix='source-layout-', dir=APP/'work') as folder:
    checkout = Path(folder)/'checkout with spaces'
    for name in catalog:
        if not name.startswith(('src/', 'packaging/')) and name != 'tools/Prepare-Source.ps1':
            continue
        target = checkout/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APP/name, target)
    (checkout/'config.json').write_bytes(b'KEEP SETTINGS')
    (checkout/'jobs').mkdir()
    (checkout/'jobs/task.json').write_bytes(b'KEEP PROJECT')
    assert not (checkout/'launch.py').exists()
    for attempt in range(2):
        result = subprocess.run([str(shell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(checkout/'tools/Prepare-Source.ps1')], capture_output=True, timeout=30)
        assert result.returncode == 0, (result.stdout+result.stderr).decode(errors='replace')
        assert verify(checkout) > 0
        for source in (checkout/'src').glob('*.py'):
            assert (checkout/source.name).read_bytes() == source.read_bytes()
        assert (checkout/'config.json').read_bytes() == b'KEEP SETTINGS'
        assert (checkout/'jobs/task.json').read_bytes() == b'KEEP PROJECT'
        (checkout/'src/launch.py').write_bytes(b'# Updated developer source\n')
        if attempt == 0:
            try:
                verify(checkout)
                raise AssertionError('Stale generated program passed the release/test gate')
            except ValueError as error:
                assert 'Prepare-Source.ps1' in str(error)
    assert (checkout/'launch.py').read_bytes() == b'# Updated developer source\n'
    (checkout/'config.json').unlink()
    result = subprocess.run([str(shell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(checkout/'tools/Prepare-Source.ps1')], capture_output=True, timeout=30)
    assert result.returncode == 0
    assert (checkout/'config.json').read_bytes() == (checkout/'packaging/config.example.json').read_bytes()
print('PASS: fresh source layout, repeat preparation and user-data preservation')
