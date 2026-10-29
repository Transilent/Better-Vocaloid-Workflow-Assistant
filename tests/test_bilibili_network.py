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
import agent_errors
import bilibili_session
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

    def test_bilibili_backup_variants_keep_selected_video_and_audio_pair(self):
        play_info = {
            'support_formats': [{'quality': 80, 'new_description': '1080P'}],
            'dash': {
                'video': [{
                    'id': 80, 'baseUrl': 'https://base-v.example/video.m4s?token=secret',
                    'backupUrl': ['https://backup-v.example/video.m4s?token=secret'],
                    'mimeType': 'video/mp4', 'codecs': 'avc1.64001f', 'bandwidth': 1000000,
                    'width': 1920, 'height': 1080, 'frameRate': '30',
                }],
                'audio': [{
                    'id': 30280, 'baseUrl': 'https://base-a.example/audio.m4s?token=secret',
                    'backupUrl': ['https://backup-a.example/audio.m4s?token=secret'],
                    'mimeType': 'audio/mp4', 'codecs': 'mp4a.40.2', 'bandwidth': 192000,
                }],
            },
        }
        options = {
            'quiet': True,
            'format': ('bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/'
                       'bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best'),
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            from yt_dlp.extractor.bilibili import BiliBiliIE
            formats = BiliBiliIE(downloader).extract_formats(play_info)
            variants = pipeline._bilibili_mirror_variants(formats, play_info)
            metadata = {'_type': 'video', 'id': 'BV1000000000', 'title': 'Fixture', 'duration': 1,
                        'extractor': 'BiliBili', 'extractor_key': 'BiliBili'}
            request = {'bv': 'BV1000000000', 'url': 'https://www.bilibili.com/video/BV1000000000/'}
            selected = downloader.process_ie_result(
                pipeline._bilibili_route_info(metadata, request, variants[1]), download=False)
        self.assertEqual(len(variants), 2)
        self.assertEqual(
            {item['url'] for item in selected['requested_formats']},
            {'https://backup-v.example/video.m4s?token=secret',
             'https://backup-a.example/audio.m4s?token=secret'})

    def test_retry_media_uses_task_cid_and_latest_playback_response(self):
        captured = {}
        play_info = {'dash': {'video': [], 'audio': []}}
        formats = [{'format_id': 'fixture', 'url': 'https://cdn.example/media'}]

        class Extractor:
            def __init__(self, downloader):
                captured['downloader'] = downloader

            def _download_playinfo(self, bvid, cid, headers, query, fatal):
                captured.update({'bvid': bvid, 'cid': cid, 'headers': headers,
                                 'query': query, 'fatal': fatal})
                return play_info

            def extract_formats(self, value):
                self.value = value
                return formats

        with tempfile.TemporaryDirectory(dir=APP / 'work') as folder:
            job = Path(folder)
            (job / 'source-info.json').write_text(json.dumps({
                'bvid': 'BV1000000000', 'cid': 456,
                'pages': [{'cid': 456, 'page': 1}],
            }), encoding='utf-8')
            with patch('yt_dlp.extractor.bilibili.BiliBiliIE', Extractor):
                actual_play, actual_formats = pipeline._bilibili_retry_media(
                    object(), job, {'bv': 'BV1000000000',
                                    'url': 'https://www.bilibili.com/video/BV1000000000/'})
        self.assertIs(actual_play, play_info)
        self.assertEqual(actual_formats, formats)
        self.assertEqual(captured['bvid'], 'BV1000000000')
        self.assertEqual(captured['cid'], 456)
        self.assertEqual(captured['query'], {'try_look': 1})
        self.assertFalse(captured['fatal'])

    def test_unequal_backup_counts_do_not_return_to_primary(self):
        video = 'https://base.example/v'
        audio = 'https://base.example/a'
        formats = [{'url': video}, {'url': audio}]
        play = {'dash': {'video': [{'baseUrl': video, 'backupUrl': ['https://backup.example/v']}],
                         'audio': [{'baseUrl': audio, 'backupUrl': ['https://backup.example/a1',
                                                                     'https://backup.example/a2']}]}}
        variants = pipeline._bilibili_mirror_variants(formats, play)
        self.assertEqual([item['url'] for item in variants[2]],
                         ['https://backup.example/v', 'https://backup.example/a2'])

    def test_transient_base_cdn_failure_uses_backup_url(self):
        calls = []
        options_seen = []
        video_base = 'https://base-video.example/video.m4s?token=secret&upsig=sig'
        audio_base = 'https://base-audio.example/audio.m4s?token=secret&upsig=sig'
        video_backup_1 = 'https://backup1-video.example/video.m4s?token=secret&upsig=sig'
        video_backup_2 = 'https://backup2-video.example/video.m4s?token=secret&upsig=sig'
        audio_backup_1 = 'https://backup1-audio.example/audio.m4s?token=secret&upsig=sig'
        audio_backup_2 = 'https://backup2-audio.example/audio.m4s?token=secret&upsig=sig'
        formats = [
            {'format_id': 'video', 'url': video_base, 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'audio', 'url': audio_base, 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a'},
        ]
        metadata = {'_type': 'video', 'id': 'BV1000000000', 'title': 'Fixture', 'duration': 1,
                    'extractor': 'BiliBili', 'extractor_key': 'BiliBili',
                    'http_headers': {'Referer': 'https://www.bilibili.com/video/BV1000000000/'},
                    'formats': formats}

        class Downloader:
            def __init__(self, settings):
                options_seen.append(dict(settings))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download):
                if download:
                    raise yt_dlp.DownloadError('SSL: TLS connection reset while fetching ' + video_base)
                return metadata

            def process_ie_result(self, info, download):
                urls = [fmt['url'] for fmt in info['formats']]
                calls.append(urls)
                if video_backup_2 not in urls:
                    raise yt_dlp.DownloadError('TLS connection reset for ' + urls[0])
                (job / 'video/source.mp4').write_bytes(b'fixture' * 200)
                return {**info, 'requested_formats': info['formats']}

        settings = {'use_login': False, 'timeout': 5, 'retries': 1}
        with tempfile.TemporaryDirectory(dir=APP / 'work') as folder:
            job = Path(folder)
            play_info = {'dash': {
                'video': [{'baseUrl': video_base, 'backupUrl': [video_backup_1, video_backup_2]}],
                'audio': [{'baseUrl': audio_base, 'backupUrl': [audio_backup_1, audio_backup_2]}],
            }}
            with patch.object(yt_dlp, 'YoutubeDL', Downloader), \
                 patch.object(pipeline, '_bilibili_retry_media', return_value=(play_info, formats)), \
                 patch.object(pipeline, 'resolve_tools', return_value={'ffmpeg': 'fixture'}), \
                 patch.object(bilibili_session, 'task_options', return_value=settings), \
                 patch.object(bilibili_session, 'download_cookies', return_value=[]):
                pipeline.download(job, {'source_kind': 'bilibili', 'bv': 'BV1000000000',
                                        'url': 'https://www.bilibili.com/video/BV1000000000/', 'tools': {}})
            report = json.loads((job / 'download-report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['used_fallback'])
        self.assertEqual([row['route'] for row in report['download_attempts']],
                         ['baseUrl', 'baseUrl', 'backupUrl[1]', 'backupUrl[2]'])
        self.assertEqual(calls[0][0], video_base)
        self.assertEqual(calls[-1][0], video_backup_2)
        self.assertTrue(all(settings['proxy'] == '' for settings in options_seen))

    def test_exhausted_cdn_retries_return_safe_retryable_guidance(self):
        secret = 'never-expose-this-token'
        video_base = f'https://base-video.example/video.m4s?upsig={secret}'
        audio_base = f'https://base-audio.example/audio.m4s?upsig={secret}'
        video_backup = f'https://backup-video.example/video.m4s?upsig={secret}'
        audio_backup = f'https://backup-audio.example/audio.m4s?upsig={secret}'
        formats = [
            {'format_id': 'video', 'url': video_base, 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'audio', 'url': audio_base, 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a'},
        ]
        metadata = {'_type': 'video', 'id': 'BV1000000000', 'title': 'Fixture', 'duration': 1,
                    'extractor': 'BiliBili', 'extractor_key': 'BiliBili', 'formats': formats}

        class Downloader:
            def __init__(self, settings):
                self.settings = dict(settings)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download):
                if download:
                    raise yt_dlp.DownloadError('SSL EOF while fetching ' + video_base)
                return metadata

            def process_ie_result(self, info, download):
                raise yt_dlp.DownloadError('TLS connection reset for ' + info['formats'][0]['url'])

        play_info = {'dash': {
            'video': [{'baseUrl': video_base, 'backupUrl': [video_backup]}],
            'audio': [{'baseUrl': audio_base, 'backupUrl': [audio_backup]}],
        }}
        settings = {'use_login': False, 'timeout': 5, 'retries': 1}
        with tempfile.TemporaryDirectory(dir=APP / 'work') as folder:
            job = Path(folder)
            with patch.object(yt_dlp, 'YoutubeDL', Downloader), \
                 patch.object(pipeline, '_bilibili_retry_media', return_value=(play_info, formats)), \
                 patch.object(pipeline, 'resolve_tools', return_value={'ffmpeg': 'fixture'}), \
                 patch.object(bilibili_session, 'task_options', return_value=settings), \
                 patch.object(bilibili_session, 'download_cookies', return_value=[]):
                with self.assertRaises(pipeline.BilibiliDownloadError) as raised:
                    pipeline.download(job, {'source_kind': 'bilibili', 'bv': 'BV1000000000',
                                            'url': 'https://www.bilibili.com/video/BV1000000000/', 'tools': {}})
        error = raised.exception
        payload = agent_errors.error_payload(error)
        self.assertEqual(payload['code'], 'bilibili_download_failed')
        self.assertTrue(payload['retryable'])
        self.assertEqual(payload['details']['next_action'], 'retry_preparation')
        self.assertTrue(payload['details']['local_import']['supported'])
        self.assertEqual([row['route'] for row in payload['details']['attempted_routes']],
                         ['baseUrl', 'baseUrl', 'backupUrl[1]', 'baseUrl', 'backupUrl[1]'])
        self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))

    def test_failure_guidance_matches_current_stage(self):
        error = '<urlopen error [WinError 10061] Connection refused>'
        hint = explain(error, 'metadata')
        self.assertEqual(hint['title'], 'B站连接未完成')
        self.assertIn('直连', hint['message'])
        self.assertEqual(hint['action'], 'resume')
        self.assertEqual(explain(error, 'components')['title'], '网络请求未完成')


if __name__ == '__main__':
    unittest.main()
