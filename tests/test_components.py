"""Download failure/resume integrity and safe component extraction."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import tempfile
import zipfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import optional_components as components
(APP / 'work').mkdir(exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix='component-tests-', dir=APP / 'work')
SCRATCH = Path(temporary.name)
DATA = bytes(range(256)) * 10001
ranges = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        header = self.headers.get('Range')
        ranges.append((self.path, header))
        if self.path == '/unavailable':
            self.send_error(503)
            return
        offset = int(header.split('=')[1].split('-')[0]) if header else 0
        if self.path == '/ignore':
            offset = 0
        self.send_response(206 if offset else 200)
        if offset:
            actual = offset + 1 if self.path == '/wrong-range' else offset
            self.send_header('Content-Range', f'bytes {actual}-{len(DATA)-1}/{len(DATA)}')
        self.send_header('Content-Length', str(len(DATA) - offset))
        self.end_headers()
        self.wfile.write(DATA[offset:])

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
passed = []
try:
    for endpoint in ('resume', 'ignore'):
        item = {'name': endpoint + '.whl', 'url': base + '/' + endpoint,
                'sha256': hashlib.sha256(DATA).hexdigest(), 'bytes': len(DATA)}
        (SCRATCH / (item['name'] + '.partial')).write_bytes(DATA[:123456])
        path = components.download(item, SCRATCH, lambda *args: None)
        assert path.read_bytes() == DATA
        assert ('/' + endpoint, 'bytes=123456-') in ranges
        passed.append(endpoint)
    item = {'name': 'bad-range.whl', 'url': base + '/wrong-range',
            'sha256': hashlib.sha256(DATA).hexdigest(), 'bytes': len(DATA)}
    (SCRATCH / (item['name'] + '.partial')).write_bytes(DATA[:123456])
    try:
        components.download(item, SCRATCH, lambda *args: None)
        raise AssertionError('Invalid Range was accepted')
    except ValueError:
        passed.append('invalid-range-rejected')
    item = {'name': 'bad-sha.whl', 'url': base + '/resume', 'sha256': '0' * 64, 'bytes': len(DATA)}
    try:
        components.download(item, SCRATCH, lambda *args: None)
        raise AssertionError('Invalid SHA was accepted')
    except ValueError:
        assert not (SCRATCH / item['name']).exists()
        passed.append('sha-mismatch-rejected')
    item = {'name': 'cancel.whl', 'url': base + '/resume', 'sha256': hashlib.sha256(DATA).hexdigest(), 'bytes': len(DATA)}
    cancel = [False]
    def progress(done, total, text):
        if done >= 1024 * 1024:
            cancel[0] = True
    try:
        components.download(item, SCRATCH, progress, lambda: cancel[0])
        raise AssertionError('Cancellation ignored')
    except InterruptedError:
        assert (SCRATCH / 'cancel.whl.partial').stat().st_size >= 1024 * 1024
    cancel[0] = False
    assert components.download(item, SCRATCH, lambda *args: None).read_bytes() == DATA
    passed.append('cancel-then-resume')
    item = {'name': 'fallback.whl', 'url': base + '/unavailable',
            'sha256': hashlib.sha256(DATA).hexdigest(), 'bytes': len(DATA)}
    (SCRATCH / (item['name'] + '.partial')).write_bytes(DATA[:123456])
    with patch.object(components, 'download_urls', return_value=[base + '/unavailable', base + '/resume']):
        assert components.download(item, SCRATCH, lambda *args: None).read_bytes() == DATA
    assert ('/unavailable', 'bytes=123456-') in ranges
    assert ('/resume', 'bytes=123456-') in ranges
    passed.append('alternate-source-preserves-range-and-hash')
finally:
    server.shutdown()
    server.server_close()

wheel = SCRATCH / 'escaping.whl'
with zipfile.ZipFile(wheel, 'w') as archive:
    archive.writestr('../escaped.py', 'invalid')
try:
    components.extract_wheel(wheel, SCRATCH / 'packages')
    raise AssertionError('Escaping archive was accepted')
except ValueError:
    assert not (SCRATCH / 'escaped.py').exists()
    passed.append('archive-traversal-rejected')
outside = SCRATCH / 'outside'
outside.mkdir(exist_ok=True)
try:
    components.remove_owned_tree(outside, SCRATCH / 'owned')
    raise AssertionError('Out-of-scope cleanup accepted')
except ValueError:
    assert outside.is_dir()
    passed.append('cleanup-boundary')
assert components.status(SCRATCH / 'never-installed') == {'ready': False, 'runtime': None}
print(json.dumps({'state': 'passed', 'checks': passed}))
