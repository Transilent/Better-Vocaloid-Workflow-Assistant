"""A failed or abruptly exited test cannot change the installed user's state."""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation import application_copy

APP = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='isolation-contract-') as folder:
    root = Path(folder)
    source, parent = root / 'installed', root / 'sandboxes'
    (source / 'packaging').mkdir(parents=True)
    (source / 'packaging/config.example.json').write_bytes(b'{"default": true}')
    (source / 'app.py').write_bytes(b'# program')
    kept = {'config.json': b'PRIVATE CONFIG', 'cache/workspace.json': b'PRIVATE PRESETS',
            'publish-ui.json': b'PRIVATE PUBLICATION', 'last_job.txt': b'PRIVATE TASK',
            'cache/publish-browser/login': b'PRIVATE ACCOUNT', 'docs/images/test.png': b'ORIGINAL IMAGE',
            'dependencies/model.bin': b'ORIGINAL MODEL'}
    for name, data in kept.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    try:
        with application_copy(source, parent) as sandbox:
            assert (sandbox / 'config.json').read_bytes() == b'{"default": true}'
            assert not (sandbox / 'cache').exists() and not (sandbox / 'publish-ui.json').exists()
            assert (sandbox / 'dependencies/model.bin').read_bytes() == b'ORIGINAL MODEL'
            script = ("from pathlib import Path; import os; "
                      "Path('config.json').write_bytes(b'TEST CONFIG'); "
                      "Path('cache').mkdir(); Path('cache/workspace.json').write_bytes(b'TEST'); "
                      "Path('docs/images/test.png').write_bytes(b'TEST IMAGE'); os._exit(17)")
            assert subprocess.run([sys.executable, '-B', '-c', script], cwd=sandbox, timeout=15).returncode == 17
            raise ValueError('fixture assertion failure')
    except ValueError as error:
        assert str(error) == 'fixture assertion failure'
    assert not list(parent.iterdir()), 'Finished test copies must not accumulate'
    with application_copy(source, parent) as sandbox:
        assert not (sandbox / 'cache').exists(), 'The next test must start clean'
        assert (sandbox / 'config.json').read_bytes() == b'{"default": true}'
    assert all((source / name).read_bytes() == data for name, data in kept.items())
print('PASS: private-state exclusion, per-test reset, crash/failure cleanup and unchanged shared model')
