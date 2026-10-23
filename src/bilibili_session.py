"""Opt-in download authentication from VWA's own Bilibili browser profile."""
import base64
import http.cookiejar
import json
import math
import time
from pathlib import Path

from common import ROOT, atomic_json, load_config


def options(tools=None):
    values = (load_config() if tools is None else tools).get('bilibili_download', {})
    if not isinstance(values, dict):
        raise ValueError('B站下载设置无效。')
    result = {'use_login': values.get('use_login', False),
              'timeout': values.get('timeout', 30), 'retries': values.get('retries', 3)}
    if (type(result['use_login']) is not bool or type(result['timeout']) is not int
            or not 5 <= result['timeout'] <= 120 or type(result['retries']) is not int
            or not 0 <= result['retries'] <= 10):
        raise ValueError('B站下载超时应为 5–120 秒，重试应为 0–10 次。')
    return result


def save_options(**values):
    result = options({'bilibili_download': values})
    path = ROOT / 'config.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    data['bilibili_download'] = result
    atomic_json(path, data)


def task_options(tools):
    """Network preferences can change between retries without rewriting a job."""
    current = load_config()
    return options(current if 'bilibili_download' in current else tools)


def filtered(cookies):
    """Keep only valid cookies scoped to bilibili.com; never log their values."""
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = cookie.get('domain', '').lower()
        host = domain.lstrip('.')
        if host != 'bilibili.com' and not host.endswith('.bilibili.com'):
            continue
        name, value, path = cookie.get('name'), cookie.get('value'), cookie.get('path', '/')
        expires = cookie.get('expires', -1)
        if (not isinstance(name, str) or not name or not isinstance(value, str)
                or not isinstance(path, str) or not path.startswith('/')
                or any(c in name + value + path + domain for c in '\r\n\x00')
                or type(expires) not in (int, float) or not math.isfinite(expires)
                or (expires > 0 and expires <= time.time())):
            continue
        result.append({**cookie, 'domain': domain})
    return result


def session_path():
    return ROOT / 'cache/accounts/bilibili-session.json'


def save_cookies(cookies):
    """DPAPI encryption ties a session to this Windows user and computer."""
    import win32crypt
    cookies = filtered(cookies)
    path = session_path()
    if not any(c['name'] == 'SESSDATA' and c['value'] for c in cookies):
        path.unlink(missing_ok=True)
        return False
    encoded = json.dumps(cookies, separators=(',', ':')).encode('utf-8')
    # Avoid rewriting an unchanged session on every browser polling cycle.
    if path.is_file():
        try:
            if read_cookies() == cookies:
                return True
        except ValueError:
            pass
    encrypted = win32crypt.CryptProtectData(encoded, 'VWA Bilibili', None, None, None, 1)
    atomic_json(path, {'version': 1, 'encryption': 'windows-dpapi',
                       'payload': base64.b64encode(encrypted).decode('ascii')})
    return True


def read_cookies():
    import win32crypt
    path = session_path()
    if not path.is_file():
        return []
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['version'] != 1 or record['encryption'] != 'windows-dpapi':
            raise ValueError('session format')
        encrypted = base64.b64decode(record['payload'], validate=True)
        raw = win32crypt.CryptUnprotectData(encrypted, None, None, None, 1)[1]
        cookies = json.loads(raw)
        if not isinstance(cookies, list):
            raise ValueError('session format')
        return filtered(cookies)
    except Exception:
        raise ValueError('B站登录状态不可读取，请在助手中重新登录。') from None


def capture(context):
    return save_cookies(context.cookies(['https://www.bilibili.com/',
                                         'https://member.bilibili.com/']))


def refresh_from_profile():
    """Import an existing dedicated profile once, without opening a visible UI."""
    from pipeline import job_lock
    from publish_browser import browser_options, restore_migrated_session
    from playwright.sync_api import sync_playwright
    profile = ROOT / 'cache/publish-browser/bilibili'
    if not (profile / 'Default').is_dir():
        raise ValueError('请先点击“登录 B站”，在专用窗口完成登录后关闭窗口。')
    try:
        with job_lock(profile), sync_playwright() as browser:
            context = browser.chromium.launch_persistent_context(str(profile), **browser_options(True))
            try:
                restore_migrated_session(context, profile)
                capture(context)
            finally:
                context.close()
    except ValueError:
        raise
    except Exception:
        raise ValueError('无法读取助手的B站登录状态。请关闭登录或上传窗口后重试，必要时重新登录。') from None


def download_cookies(settings):
    if not settings['use_login']:
        return []
    cookies = read_cookies()
    if not any(c['name'] == 'SESSDATA' and c['value'] for c in cookies):
        refresh_from_profile()
        cookies = read_cookies()
    if not any(c['name'] == 'SESSDATA' and c['value'] for c in cookies):
        raise ValueError('B站登录已失效或尚未完成，请重新登录后继续；也可取消“使用登录状态”。')
    return cookies


def cookie_jar(cookies):
    jar = http.cookiejar.CookieJar()
    for c in filtered(cookies):
        expiry = c.get('expires', -1)
        jar.set_cookie(http.cookiejar.Cookie(0, c['name'], c['value'], None, False,
            c['domain'], c['domain'].startswith('.'), c['domain'].startswith('.'),
            c.get('path', '/'), True, bool(c.get('secure', True)),
            int(expiry) if expiry > 0 else None, expiry <= 0, None, None, {}, False))
    return jar


def cookie_header(cookies, url):
    import urllib.request
    request = urllib.request.Request(url)
    cookie_jar(cookies).add_cookie_header(request)
    return request.get_header('Cookie')


def configure_downloader(downloader, cookies):
    for c in cookie_jar(cookies):
        downloader.cookiejar.set_cookie(c)


def verify_login(cookies, timeout=30):
    import urllib.request
    from common import direct_urlopen
    url = 'https://api.bilibili.com/x/web-interface/nav'
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    if value := cookie_header(cookies, url):
        request.add_unredirected_header('Cookie', value)
    with direct_urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(1024**2))
    if payload.get('code') != 0 or not payload.get('data', {}).get('isLogin'):
        raise ValueError('B站登录已失效，请重新登录后继续；也可取消“下载使用B站登录状态”。')
    return True
