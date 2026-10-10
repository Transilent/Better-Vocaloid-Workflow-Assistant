"""Public download routing shared by component installation and updates."""
from urllib.parse import urlsplit, urlunsplit
import urllib.error
import urllib.request

SOURCES = ('auto', 'direct', 'mirror')


def accelerated_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return url
    if parsed.netloc in ('github.com', 'raw.githubusercontent.com'):
        return 'https://api.gitproxy.dev/' + url.removeprefix('https://')
    if parsed.netloc == 'huggingface.co':
        return urlunsplit(('https', 'hf-mirror.com', parsed.path, parsed.query, ''))
    return url


def download_urls(url, source='auto'):
    if source not in SOURCES:
        raise ValueError('请选择自动、官方源或加速源。')
    mirror = accelerated_url(url)
    candidates = [url] if source == 'direct' else ([mirror, url] if source == 'mirror' else [url, mirror])
    return list(dict.fromkeys(candidates))


def source_name(url):
    host = urlsplit(url).hostname
    return {'api.gitproxy.dev': 'GitProxy', 'hf-mirror.com': 'HF-Mirror'}.get(host, '官方源')


def read_small(url, source='auto', limit=2 * 1024**2, opener=urllib.request.urlopen):
    """Bounded metadata requests; no platform cookies or credentials are sent."""
    last_error = None
    for candidate in download_urls(url, source):
        request = urllib.request.Request(candidate, headers={'User-Agent': 'BVWA-Updater', 'Accept': 'application/json'})
        try:
            with opener(request, timeout=12) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('更新信息超过大小限制。')
                return data
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
    raise OSError('无法连接下载源，请切换下载方式后重试。') from last_error
