"""The shareable journal is bounded, local, and does not copy raw error text."""
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import URLError

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from common import direct_urlopen
import operation_diagnostics as diag
from optional_components import download
from preparation_commands import progress_view


PRIVATE = 'LYRICS_AND_ACCOUNT_MUST_NOT_APPEAR'
WEIGHT = 'bs_roformer_karaoke_frazer_becruily.ckpt'


class LocalJournal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='op-', dir=APP / 'work')
        self.root = Path(self.temp.name)
        self.journal = diag.Journal(self.root, max_bytes=4096, version='2.0.0')

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def test_rotation_and_structured_whitelist(self):
        for i in range(70):
            self.journal.event('finished', kind='component', id=f'{i:012x}', route='components',
                               state='failed', code='network_timeout', file=WEIGHT, elapsed=i,
                               source='modelscope', component='roformer', runtime='cpu',
                               origin='modelscope.cn', target='cdn-lfs-cn-1.modelscope.cn',
                               redirects=[PRIVATE, 'cdn-lfs-cn-1.modelscope.cn'],
                               message=PRIVATE, path=r'C:\Users\private\mix.wav')
        self.journal.close()
        files = list((self.root / diag.LOG_DIR).glob('operations.jsonl*'))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(p.stat().st_size <= 4096 for p in files))
        for path in files:
            self.assertNotIn(PRIVATE, path.read_text())
            self.assertNotIn('mix.wav', path.read_text())
        history = diag.Journal(self.root, max_bytes=4096).history()
        self.assertEqual(history[-1]['elapsed'], 69)
        self.assertEqual(history[-1]['redirects'], ['cdn-lfs-cn-1.modelscope.cn'])
        self.assertEqual(history[-1]['source'], 'modelscope')

    def test_shareable_read_sanitizes_invalid_records(self):
        folder = self.root / diag.LOG_DIR
        folder.mkdir(parents=True)
        file = folder / diag.LOG_NAME
        file.write_text('{"schema":1,"event":[]}\n{"schema":1,"event":"finished","state":"failed",'
                        '"file":"private.wav","message":"'+PRIVATE+'"}\n{"unfinished":', encoding='utf-8')
        self.assertEqual(self.journal.history(), [{'schema': 1, 'event': 'finished', 'state': 'failed'}])

    def test_closing_failure_hint_does_not_resurface_old_failures(self):
        for i in range(2):
            self.journal.event('finished', kind='component', id=f'{i:012x}', component='roformer',
                               state='failed', code='network_timeout')
        issue = self.journal.latest_failure()
        self.assertEqual(issue['id'], '000000000001')
        self.assertFalse(self.journal.latest_failure(issue['id']))
        self.journal.event('finished', kind='component', id='000000000002', component='roformer',
                           state='succeeded')
        self.assertFalse(self.journal.latest_failure())

    def test_structured_network_retry_remembers_cached_bytes(self):
        import hashlib
        from io import BytesIO
        item = {'name': WEIGHT, 'bytes': 4, 'sha256': hashlib.sha256(b'abcd').hexdigest(),
                'url': 'https://huggingface.co/repo/resolve/a/asset'}
        class Reply(BytesIO):
            status = 206
            headers = {'Content-Range': 'bytes 2-3/4'}
            def geturl(self):
                return 'https://release-assets.githubusercontent.com/file?token='+PRIVATE
        def open_request(request, timeout):
            if request.host == 'huggingface.co':
                error = TimeoutError(PRIVATE)
                error.winerror = 10060
                raise URLError(error)
            self.assertEqual(request.get_header('Range'), 'bytes=2-')
            return Reply(b'cd')
        (self.root / (WEIGHT+'.partial')).write_bytes(b'ab')
        token = diag.bind(self.journal, {'id': 'aabbcc112233', 'kind': 'component', 'route': 'components'})
        try:
            with patch('optional_components.component_download_urls', return_value=[
                    item['url'], 'https://github.com/owner/repo/releases/download/tag/file']):
                target = download(item, self.root, lambda *_: None, workers=1, opener=open_request)
                self.assertEqual(target.read_bytes(), b'abcd')
        finally:
            diag.unbind(token)
        failed, succeeded = self.journal.current()
        self.assertEqual((failed['code'], failed['winerror'], failed['cached_bytes']), ('network_timeout', 10060, 2))
        self.assertEqual(succeeded['target'], 'release-assets.githubusercontent.com')
        self.assertNotIn(PRIVATE, json.dumps(self.journal.current()))

    def test_direct_redirect_trace_carries_target_even_on_failure(self):
        # A local HTTP fixture exercises urllib's redirect handler. Unknown
        # hosts are intentionally replaced with "other" in shareable evidence.
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                if self.path == '/first':
                    self.send_response(302)
                    self.send_header('Location', '/fail?token='+PRIVATE)
                    self.end_headers()
                else:
                    self.send_error(503)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        token = diag.bind(self.journal, {'id': 'aabbcc112233', 'kind': 'component'})
        try:
            from urllib.request import Request
            url = f'http://127.0.0.1:{server.server_port}/first'
            trace = diag.DownloadTrace(direct_urlopen, url, {'name': WEIGHT}, 'parallel')
            try:
                trace.open(Request(url), timeout=2)
                self.fail('HTTP 503 was not propagated')
            except OSError as error:
                trace.record('failed', 17, error)
            row = self.journal.current()[-1]
            self.assertEqual((row['http'], row['cached_bytes'], row['redirects']), (503, 17, ['other']))
            self.assertNotIn(PRIVATE, json.dumps(row))
        finally:
            diag.unbind(token)
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_progress_from_old_attempt_is_not_exposed(self):
        status = {'state': 'running', 'run_started': 800, 'current_step': 'separation',
                  'planned_steps': ['separation'], 'steps': {}}
        (self.root/'status.json').write_text(json.dumps(status))
        record = {'stage': 'separation', 'updated': 700, 'detail': 'old', 'done': 8, 'total': 8}
        (self.root/'progress.json').write_text(json.dumps(record))
        with patch('task_store.locked', return_value=True), patch('progress_state.time.time', return_value=900):
            self.assertFalse(progress_view(self.root)['stage_progress'])
            record.update(updated=850, detail='now', done=2)
            (self.root/'progress.json').write_text(json.dumps(record))
            self.assertEqual(progress_view(self.root)['stage_progress'], {'done': 2, 'total': 8, 'unit': 'items'})

    def test_disk_and_permission_are_not_network_failures(self):
        self.assertEqual(diag.failure_fields(OSError(errno.ENOSPC, 'cannot write'))['code'], 'disk_full')
        self.assertEqual(diag.failure_fields(PermissionError('cannot write'))['code'], 'permission')


if __name__ == '__main__':
    unittest.main()
