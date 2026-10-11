"""Public metadata and downloads must ignore an unavailable inherited proxy."""
import io
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.error
import urllib.request

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'vendor')]
import common
import cover_art
import pipeline
import yt_dlp
import download_sources
import optional_components
from error_recovery import explain


class PublicFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'public fixture'
        self.send_response(403 if self.path == '/restricted' else 200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class DirectNetworkTests(unittest.TestCase):
    def test_closed_proxy_does_not_block_direct_requests(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), PublicFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # A bound socket without listen() guarantees a refused proxy connection.
        with socket.socket() as closed_proxy:
            closed_proxy.bind(('127.0.0.1', 0))
            proxy = 'http://127.0.0.1:' + str(closed_proxy.getsockname()[1])
            environment = {key: proxy for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                                  'http_proxy', 'https_proxy', 'all_proxy')}
            environment.update(NO_PROXY='', no_proxy='')
            try:
                with patch.dict(os.environ, environment):
                    original_environment = dict(os.environ)
                    url = 'http://127.0.0.1:' + str(server.server_port)
                    with self.assertRaises(urllib.error.URLError):
                        urllib.request.build_opener().open(url, timeout=2)
                    with common.direct_urlopen(url, timeout=2) as response:
                        self.assertEqual(response.read(), b'public fixture')
                    self.assertEqual(download_sources.read_small(url, source='direct'), b'public fixture')
                    with tempfile.TemporaryDirectory(dir=APP / 'work') as folder:
                        item = {'name': 'component.bin', 'url': url, 'bytes': len(b'public fixture'),
                                'sha256': hashlib.sha256(b'public fixture').hexdigest()}
                        downloaded = optional_components.download(item, folder, lambda *args: None)
                        self.assertEqual(downloaded.read_bytes(), b'public fixture')
                    # Direct routing must still report HTTP access restrictions.
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        common.direct_urlopen(url + '/restricted', timeout=2)
                    self.assertEqual(failure.exception.code, 403)
                    self.assertEqual(dict(os.environ), original_environment)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_direct_opener_does_not_read_registry_or_environment_proxies(self):
        # On Windows, getproxies() also reads Internet Settings from the registry.
        with patch('urllib.request.getproxies', side_effect=AssertionError('Proxy lookup forbidden')), \
             patch('urllib.request.OpenerDirector.open', return_value='response') as opening:
            self.assertEqual(common.direct_urlopen('https://api.bilibili.com/', timeout=7), 'response')
            self.assertEqual(opening.call_args.kwargs['timeout'], 7)

    def test_metadata_and_original_cover_use_direct_opener(self):
        data = {'code': 0, 'data': {'bvid': 'BV1000000000', 'title': 'Fixture'}}
        with patch.object(pipeline, 'direct_urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as opening:
            self.assertEqual(pipeline.get_view('BV1000000000'), data['data'])
            self.assertIn('bvid=BV1000000000', opening.call_args.args[0].full_url)
        with tempfile.TemporaryDirectory(dir=APP / 'work') as folder, \
             patch.object(cover_art, 'direct_urlopen', return_value=io.BytesIO(b'\xff\xd8\xfffixture')) as opening:
            image, record = cover_art.original_cover({'bvid': 'BV1000000000',
                'pic': 'https://i0.hdslb.com/bfs/archive/fixture.jpg'}, folder)
            self.assertTrue(image.is_file())
            self.assertEqual(record['kind'], 'original')
            self.assertEqual(opening.call_args.kwargs['timeout'], 30)

    def test_downloader_uses_direct_connection(self):
        options = {}
        class Downloader:
            def __init__(self, settings):
                options.update(settings)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download):
                (job / 'video/source.mp4').write_bytes(b'fixture' * 200)
                return {'title': 'Fixture', 'duration': 1}

        with tempfile.TemporaryDirectory(dir=APP / 'work') as folder, \
             patch.object(yt_dlp, 'YoutubeDL', Downloader), \
             patch.object(pipeline, 'resolve_tools', return_value={'ffmpeg': 'fixture'}):
            job = Path(folder)
            pipeline.download(job, {'url': 'https://www.bilibili.com/video/BV1000000000/', 'tools': {}})
        self.assertEqual(options['proxy'], '')

    def test_failure_guidance_matches_current_stage(self):
        error = '<urlopen error [WinError 10061] Connection refused>'
        hint = explain(error, 'metadata')
        self.assertEqual(hint['title'], 'B站连接未完成')
        self.assertIn('直连', hint['message'])
        self.assertEqual(hint['action'], 'resume')
        self.assertEqual(explain(error, 'components')['title'], '网络请求未完成')


if __name__ == '__main__':
    unittest.main()
