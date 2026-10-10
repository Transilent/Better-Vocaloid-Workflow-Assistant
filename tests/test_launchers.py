"""Run the actual Windows launcher and device switches with bundled Python."""
import json
import os
import subprocess
import struct
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
config = APP / 'config.json'
before = config.read_bytes()
workspace = APP/'cache/workspace.json'
before_workspace = workspace.read_bytes() if workspace.exists() else None
log = APP / 'launcher.log'
before_log = log.read_bytes() if log.exists() else None
env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1')

def run(name, *args):
    launcher = APP / name
    if not launcher.is_file():
        launcher = APP / 'tools' / name
    result = subprocess.run([str(Path(os.environ['WINDIR']) / 'System32/cmd.exe'),
                             '/d', '/c', str(launcher), *args], cwd=APP, env=env,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout.decode('utf-8', errors='replace')[-2000:]

try:
    assert not list(APP.glob('*.cmd')), 'The application must have one startup entry.'
    executable = APP / 'BVWA.exe'
    if executable.is_file():
        data = executable.read_bytes()
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        assert data[pe:pe + 4] == b'PE\x00\x00'
        assert struct.unpack_from('<H', data, pe + 24 + 68)[0] == 2, 'The launcher must use the Windows GUI subsystem'
        result = subprocess.run([str(executable), '--smoke-test'], cwd=APP, env=env, timeout=60)
        assert result.returncode == 0
        text = log.read_text(encoding='utf-8')
        assert 'GUI ready; startup smoke test passed' in text
        assert 'console attached: False' in text
    run('Start-Diagnostics.bat', '--check')
    assert 'dependencies ready' in log.read_text(encoding='utf-8')
    original_state = json.loads(before_workspace) if before_workspace else {}
    from workflow import default_graph, DEFAULT_OPTIONS
    fixture_state = {**original_state, 'workflow': {'graph': default_graph(), 'options': dict(DEFAULT_OPTIONS), 'presets': {}, 'preset': '自定义'}}
    workspace.parent.mkdir(exist_ok=True)
    workspace.write_text(json.dumps(fixture_state),encoding='utf-8')
    for launcher, device in [('Use-CPU.bat', 'cpu'), ('Use-GPU.bat', 'dml')]:
        run(launcher)
        actual = json.loads(config.read_text(encoding='utf-8'))
        assert actual['separation_device'] == actual['midi_device'] == device
        actual_state = json.loads(workspace.read_text(encoding='utf-8'))
        assert actual_state['workflow']['options']['separation_device'] == actual_state['workflow']['options']['midi_device'] == device
        assert {k:v for k,v in actual_state.items() if k != 'workflow'} == {k:v for k,v in original_state.items() if k != 'workflow'}
        original = json.loads(before)
        assert {k: v for k, v in actual.items() if k not in {'separation_device', 'midi_device'}} == {
            k: v for k, v in original.items() if k not in {'separation_device', 'midi_device'}}
finally:
    config.write_bytes(before)
    if before_workspace is None:
        workspace.unlink(missing_ok=True)
    else:
        workspace.write_bytes(before_workspace)
    if before_log is None:
        log.unlink(missing_ok=True)
    else:
        log.write_bytes(before_log)
print('PASS: actual batch launcher and CPU/GPU settings; configuration restored')
