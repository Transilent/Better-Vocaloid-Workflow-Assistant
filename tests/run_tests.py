import json
import os
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
runtime = Path(sys.executable).parent
qt = runtime / 'Lib/site-packages/PyQt5/Qt5'
env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', QT_QPA_PLATFORM='offscreen',
           NUMBA_CACHE_DIR=str(APP / 'work/test-numba-cache'),
           QT_PLUGIN_PATH=str(qt / 'plugins'), QT_QPA_PLATFORM_PLUGIN_PATH=str(qt / 'plugins/platforms'))
env['PATH'] = os.pathsep.join([str(runtime), str(runtime / 'DLLs'), str(qt / 'bin'), str(Path(os.environ['WINDIR']) / 'System32')])
folder = APP / 'work/test-results'
folder.mkdir(parents=True, exist_ok=True)
reports = {}
for name in ('test_core.py', 'test_japanese_midi.py', 'test_gui.py', 'test_local_music.py', 'test_publish_browser.py', 'test_publish_adapters.py', 'test_bilibili_dual_cover.py', 'test_launchers.py', 'test_bootstrap.py'):
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-B', '-u', str(APP / 'tests' / name)], capture_output=True, env=env, timeout=180)
    (folder / (name + '.log')).write_bytes(result.stdout + result.stderr)
    reports[name] = {'exit_code': result.returncode, 'seconds': round(time.monotonic()-started, 2)}
    print(name + (': PASS' if result.returncode == 0 else ': FAIL'), flush=True)
    if result.returncode:
        print((result.stdout + result.stderr).decode('utf-8', errors='replace')[-4000:], flush=True)
(folder / 'regression.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(0 if all(value['exit_code'] == 0 for value in reports.values()) else 1)
