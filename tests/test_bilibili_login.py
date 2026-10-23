"""Download auth, local encryption, cookie scope, UI persistence and retries."""
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'vendor')]
import bilibili_session as session
import pipeline
import common
import yt_dlp

def cookie(name='SESSDATA', value='fixture-session', domain='.bilibili.com', expires=-1):
    return dict(name=name, value=value, domain=domain, path='/', expires=expires, secure=True, httpOnly=True)

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='download-auth-') as folder:
    root = Path(folder)
    with patch.object(session, 'ROOT', root):
        (root / 'config.json').write_text('{"ffmpeg":"relative/ffmpeg.exe","other":42}')
        session.save_options(use_login=True, timeout=50, retries=5)
        data = json.loads((root / 'config.json').read_text())
        assert data['ffmpeg'] == 'relative/ffmpeg.exe' and data['other'] == 42
        assert session.options(data) == {'use_login': True, 'timeout': 50, 'retries': 5}
        assert session.save_cookies([cookie(), cookie(domain='evilbilibili.com'), cookie(domain='.example.com')])
        raw = session.session_path().read_text()
        assert 'fixture-session' not in raw and 'SESSDATA' not in raw
        assert session.read_cookies() == [cookie()]
        assert session.download_cookies({'use_login': False}) == []
        assert session.download_cookies({'use_login': True}) == [cookie()]
        assert session.cookie_header(session.read_cookies(), 'https://api.bilibili.com/') == 'SESSDATA=fixture-session'
        assert session.cookie_header(session.read_cookies(), 'https://evilbilibili.com/') is None
        assert session.cookie_header(session.read_cookies(), 'http://www.bilibili.com/') is None
        session.session_path().write_text('{"version":1,"encryption":"windows-dpapi","payload":"not-valid"}')
        try:
            session.read_cookies()
            raise AssertionError('Corrupted session accepted')
        except ValueError as error:
            assert '重新登录' in str(error) and 'not-valid' not in str(error)
        assert not session.save_cookies([]) and not session.session_path().exists()
        with patch.object(session, 'refresh_from_profile') as refresh:
            try:
                session.download_cookies({'use_login': True})
                raise AssertionError('Opted-in download silently lost authentication')
            except ValueError as error:
                assert '登录' in str(error)
            refresh.assert_called_once()
        assert session.filtered([cookie(expires=1), cookie(value='bad\r\nheader')]) == []

    for bad in ({'use_login': 'yes'}, {'timeout': 0}, {'retries': 50}):
        try:
            session.options({'bilibili_download': bad})
            raise AssertionError('Invalid network settings accepted')
        except ValueError:
            pass

    tools = {'bilibili_download': {'use_login': True, 'timeout': 50, 'retries': 2}}
    payload = json.dumps({'code': 0, 'data': {'title': 'Fixture'}}).encode()
    with patch.object(session, 'download_cookies', return_value=[cookie()]), \
         patch.object(pipeline, 'direct_urlopen', side_effect=[urllib.error.URLError('offline'), io.BytesIO(payload)]) as opening, \
         patch.object(pipeline.time, 'sleep'):
        assert pipeline.get_view('BV1000000000', tools)['title'] == 'Fixture'
        assert opening.call_count == 2 and opening.call_args.kwargs['timeout'] == 50
        request = opening.call_args.args[0]
        assert request.get_header('Cookie') == 'SESSDATA=fixture-session'
        assert 'Cookie' in request.unredirected_hdrs and 'Cookie' not in request.headers

    opts, calls = {}, []
    class Downloader:
        def __init__(self, settings):
            opts.update(settings)
            self.cookiejar = http.cookiejar.CookieJar()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            calls.append(url)
            assert any(c.name == 'SESSDATA' for c in self.cookiejar)
            (root / 'video/source.mp4').write_bytes(b'fixture' * 200)
            return {'title': 'Fixture', 'requested_formats': [{'height': 1080}, {'acodec': 'aac'}]}
    with patch.object(session, 'download_cookies', return_value=[cookie()]), \
         patch.object(yt_dlp, 'YoutubeDL', Downloader), \
         patch.object(session, 'verify_login', return_value=True) as login_checked, \
         patch.object(pipeline, 'resolve_tools', return_value={'ffmpeg': 'fixture'}):
        pipeline.download(root, {'url': 'https://www.bilibili.com/video/BV1000000000/', 'tools': tools})
    login_checked.assert_called_once()
    assert opts['proxy'] == '' and opts['socket_timeout'] == 50 and opts['retries'] == opts['fragment_retries'] == 2
    report = json.loads((root / 'download-report.json').read_text())
    assert report['authenticated'] and report['formats'][0]['height'] == 1080
    assert 'fixture-session' not in (root / 'download-report.json').read_text()
    report['authenticated'] = False
    (root/'download-report.json').write_text(json.dumps(report), encoding='utf-8')
    with patch.object(session, 'download_cookies', return_value=[cookie()]), \
         patch.object(yt_dlp, 'YoutubeDL', Downloader), patch.object(session, 'verify_login', return_value=True), \
         patch.object(pipeline, 'resolve_tools', return_value={'ffmpeg': 'fixture'}):
        pipeline.download(root, {'url': 'https://www.bilibili.com/video/BV1000000000/', 'tools': tools})
    assert len(calls) == 2, 'An anonymous cache must not suppress an authenticated download'
    for code, logged_in in ((0, True), (-101, False)):
        data = {'code': code, 'data': {'isLogin': logged_in}}
        with patch.object(common, 'direct_urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            if logged_in:
                assert session.verify_login([cookie()])
            else:
                try:
                    session.verify_login([cookie()])
                    raise AssertionError('Expired server session accepted')
                except ValueError as error:
                    assert '重新登录' in str(error)

    # Real headless Chromium: profile cookies -> DPAPI snapshot, no network or UI.
    from playwright.sync_api import sync_playwright
    from publish_browser import browser_options
    with patch.object(session, 'ROOT', root), sync_playwright() as browser:
        profile = root / 'cache/publish-browser/bilibili'
        profile.mkdir(parents=True)
        context = browser.chromium.launch_persistent_context(str(profile), **browser_options(True))
        context.add_cookies([cookie(expires=int(time.time())+86400)])
        assert session.capture(context)
        context.close()
        session.session_path().unlink()
    with patch.object(session, 'ROOT', root):
        session.refresh_from_profile()
        assert any(c['name'] == 'SESSDATA' for c in session.read_cookies())

print('PASS: opt-in Bilibili auth, DPAPI, scoped cookies, timeout/retries, authenticated downloader and real headless profile reuse')
