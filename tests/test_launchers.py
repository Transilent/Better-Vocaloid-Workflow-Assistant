"""Run the actual Windows launcher and device switches with bundled Python."""
import json
import os
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
config = APP / 'config.json'
before = config.read_bytes()
log = APP / 'launcher.log'
before_log = log.read_bytes() if log.exists() else None
env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1')

def run(name, *args):
    result = subprocess.run([str(Path(os.environ['WINDIR']) / 'System32/cmd.exe'),
                             '/d', '/c', name, *args], cwd=APP, env=env,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout.decode('utf-8', errors='replace')[-2000:]

try:
    assert not list(APP.glob('*.cmd')), 'The application must have one startup entry.'
    run('Start-Assistant.bat', '--check')
    assert 'dependencies ready' in log.read_text(encoding='utf-8')
    for launcher, device in [('Use-CPU.bat', 'cpu'), ('Use-GPU.bat', 'dml')]:
        run(launcher)
        actual = json.loads(config.read_text(encoding='utf-8'))
        assert actual['separation_device'] == actual['midi_device'] == device
        original = json.loads(before)
        assert {k: v for k, v in actual.items() if k not in {'separation_device', 'midi_device'}} == {
            k: v for k, v in original.items() if k not in {'separation_device', 'midi_device'}}
finally:
    config.write_bytes(before)
    if before_log is None:
        log.unlink(missing_ok=True)
    else:
        log.write_bytes(before_log)
print('PASS: actual batch launcher and CPU/GPU settings; configuration restored')
