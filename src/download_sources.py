"""Public download routing shared by component installation and updates.

The router only rewrites unauthenticated public files, including pinned PyPI
and SJTU PyTorch wheel paths.  Authentication headers,
cookies, signed query strings and private APIs must stay on the original route.
"""
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
import urllib.error
import urllib.request

from common import direct_urlopen


# Public routing IDs also consumed by the frozen update/bootstrap tools.
SOURCE_OPTIONS = (
    ('自动选择', 'auto'),
    ('官方源直连', 'direct'),
    ('优先加速源', 'mirror'),
    ('GitProxy 加速', 'gitproxy'),
    ('GH-Proxy 加速', 'gh-proxy'),
    ('ghfast.top 加速', 'ghfast'),
    ('HF-Mirror 镜像', 'hf-mirror'),
    ('TUNA PyPI 加速', 'pypi-tuna'),
    ('阿里云 PyPI 加速', 'pypi-aliyun'),
    ('SJTU PyTorch 加速', 'pytorch-sjtu'),
)
SOURCES = tuple(source for _, source in SOURCE_OPTIONS)
LEGACY_SOURCES = ('auto', 'direct', 'mirror')
GITHUB_SOURCE_IDS = ('gitproxy', 'gh-proxy', 'ghfast')
HF_SOURCE_ID = 'hf-mirror'
PYPI_SOURCE_IDS = ('pypi-tuna', 'pypi-aliyun')
PYTORCH_SOURCE_ID = 'pytorch-sjtu'
PYPI_PREFIXES = {
    # The current TUNA help page uses the /pypi/web path. The older
    # pypi.tuna subdomain is still observed, but the canonical path avoids a
    # redirect hop.
    'pypi-tuna': 'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/',
    'pypi-aliyun': 'https://mirrors.aliyun.com/pypi/',
}
PYTORCH_PREFIX = 'https://mirror.sjtu.edu.cn/pytorch-wheels/'
PYTORCH_VERIFIED_WHEELS = frozenset({
    'torch-2.7.1+cpu-cp312-cp312-win_amd64.whl',
    'torch-2.7.1+cu128-cp312-cp312-win_amd64.whl',
})
SOURCE_LABELS = {source: label for label, source in SOURCE_OPTIONS}

_COMPONENT_PYPI_IDS = ('pypi-tuna', 'pypi-ustc', 'pypi-aliyun')
_USTC_PYPI = 'https://mirrors.ustc.edu.cn/pypi/'
_COMPONENT_STATIONS = (
    ('魔搭权重 + GitHub 配置', 'modelscope'),
    ('GitHub Release（权重和配置）', 'github-release'),
    ('HF-Mirror（可能跳回官方）', HF_SOURCE_ID),
    (SOURCE_LABELS['pypi-tuna'], 'pypi-tuna'),
    ('中科大 PyPI 加速', 'pypi-ustc'),
    (SOURCE_LABELS['pypi-aliyun'], 'pypi-aliyun'),
    (SOURCE_LABELS[PYTORCH_SOURCE_ID], PYTORCH_SOURCE_ID),
)
COMPONENT_SOURCES = (*SOURCES, 'modelscope', 'github-release', 'pypi-ustc')

# Alternate distribution is keyed by the original URL AND its fixed identity.
# Keep install manifests byte-identical: existing receipts bind their hashes.
_ROFORMER_HF = ('https://huggingface.co/becruily/bs-roformer-karaoke/resolve/'
                'f7849ae934209184dc288d1018cd8a76a7fc8b3c/')
_ROFORMER_RELEASE = ('https://github.com/nomadkaraoke/python-audio-separator/'
                     'releases/download/model-configs/')
_ROFORMER_WEIGHT = 'bs_roformer_karaoke_frazer_becruily.ckpt'
_ROFORMER_ALTERNATES = {
    (_ROFORMER_HF + _ROFORMER_WEIGHT, 204436907,
     'eb90ee24c1154d83fbcfd27e96182f19e061557cc6e4746953125e08c29389f9'): (
        _ROFORMER_RELEASE + _ROFORMER_WEIGHT,
        'https://modelscope.cn/models/CCYellowStar/bs_roformer_karaoke_frazer_becruily/'
        'resolve/17b19a737fbf3dec88b6b770aec5778297b42423/' + _ROFORMER_WEIGHT,
    ),
    (_ROFORMER_HF + 'config_karaoke_frazer_becruily.yaml', 1903,
     '1d3b58b473025183d0d3c91e7a444cb5f1418d9f34e8f46b8d2fb10d2cc8ab34'): (
        _ROFORMER_RELEASE + 'config_bs_roformer_karaoke_frazer_becruily.yaml', None,
    ),
}

# Prefixes documented by the service operators.  Only public GitHub
# release/raw/archive URLs are ever appended to these prefixes.
_GITHUB_PREFIXES = {
    'gitproxy': 'https://api.gitproxy.dev/',
    'gh-proxy': 'https://gh-proxy.com/',
    'ghfast': 'https://ghfast.top/',
}
_GITHUB_ACCELERATOR_ORDER = ('gitproxy', 'gh-proxy', 'ghfast')
_ALIASES = {
    'automatic': 'auto',
    'official': 'direct',
    'github-direct': 'direct',
    'accelerated': 'mirror',
    'git-proxy': 'gitproxy',
    'api.gitproxy.dev': 'gitproxy',
    'ghproxy': 'gh-proxy',
    'gh_proxy': 'gh-proxy',
    'gh-proxy.com': 'gh-proxy',
    'ghfast.top': 'ghfast',
    'gh_fast': 'ghfast',
    'hfmirror': 'hf-mirror',
    'hf_mirror': 'hf-mirror',
    'hf-mirror.com': 'hf-mirror',
    'huggingface-mirror': 'hf-mirror',
    'tuna': 'pypi-tuna',
    'pypi.tuna.tsinghua.edu.cn': 'pypi-tuna',
    'pypi_tuna': 'pypi-tuna',
    'pypi-tuna': 'pypi-tuna',
    'aliyun': 'pypi-aliyun',
    'mirrors.aliyun.com': 'pypi-aliyun',
    'pypi_aliyun': 'pypi-aliyun',
    'pypi-aliyun': 'pypi-aliyun',
    'sjtu': 'pytorch-sjtu',
    'mirror.sjtu.edu.cn': 'pytorch-sjtu',
    'pytorch-sjtu': 'pytorch-sjtu',
}
_PUBLIC_QUERY_KEYS = {'download'}
_PUBLIC_DOWNLOAD_VALUES = {'', '1', 'true', 'yes'}


def source_options():
    """Return the canonical UI choices without exposing a mutable singleton."""
    return SOURCE_OPTIONS


def update_source_options():
    """Only strategies and GitHub stations can change a program update route."""
    return tuple(row for row in SOURCE_OPTIONS if row[1] in (*LEGACY_SOURCES, *GITHUB_SOURCE_IDS))


def normalize_source(source='auto'):
    """Normalize a saved/UI source ID, including the legacy ``official`` value."""
    if source is None:
        return 'auto'
    value = str(source).strip().casefold()
    value = _ALIASES.get(value, value)
    if value not in SOURCES:
        raise ValueError('请选择自动、官方源或加速源。')
    return value


def legacy_source(source='auto'):
    """Return an old three-mode value, or fail instead of losing a station ID.

    Old clients may intentionally support only ``auto/direct/mirror``.  A
    new-station preference must not be silently converted to ``mirror``; the
    caller should use a helper that knows ``SOURCES`` instead.
    """
    value = normalize_source(source)
    if value not in LEGACY_SOURCES:
        raise ValueError('此旧入口不支持扩展下载源 ID，请使用新版路由。')
    return value


def source_label(source='auto'):
    return SOURCE_LABELS[normalize_source(source)]


def _public_query(query):
    """Allow only the benign Hugging Face ``download`` hint through a proxy."""
    if not query:
        return True
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    if len(pairs) != 1:
        return False
    key, value = pairs[0]
    return key.casefold() in _PUBLIC_QUERY_KEYS and value.casefold() in _PUBLIC_DOWNLOAD_VALUES


def _safe_parts(path):
    if not isinstance(path, str) or not path.startswith('/') or path == '/':
        return None
    parts = path[1:].split('/')
    if any(not part for part in parts):
        return None
    for raw in parts:
        decoded = unquote(raw)
        if (decoded in ('.', '..') or '/' in decoded or '\\' in decoded
                or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
            return None
    return parts


def _public_download(url):
    """Return ``(kind, fragmentless_url)`` for an allowlisted public file."""
    if not isinstance(url, str) or url != url.strip():
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or port not in (None, 443) or parsed.fragment):
        return None
    if not _public_query(parsed.query):
        return None
    parts = _safe_parts(parsed.path)
    if not parts:
        return None
    host = parsed.hostname.casefold()
    kind = None
    if host == 'github.com' and len(parts) >= 4:
        if (len(parts) >= 6 and parts[2] == 'releases' and parts[3] == 'download'
                and parts[4] and parts[5]):
            kind = 'github-release'
        elif (len(parts) >= 6 and parts[2] == 'releases' and parts[3] == 'latest'
              and parts[4] == 'download' and parts[5]):
            kind = 'github-release'
        elif parts[2] == 'raw':
            kind = 'github-raw'
        elif parts[2] == 'archive':
            kind = 'github-archive'
    elif host == 'raw.githubusercontent.com' and len(parts) >= 4:
        kind = 'github-raw'
    elif host == 'huggingface.co':
        if len(parts) >= 5 and parts[2] == 'resolve':
            kind = 'hf-resolve'
        elif (len(parts) >= 6 and parts[0] in ('datasets', 'spaces')
              and parts[3] == 'resolve'):
            kind = 'hf-resolve'
    elif (host == 'files.pythonhosted.org' and len(parts) >= 4 and parts[0] == 'packages'
          and unquote(parts[-1]).casefold().endswith('.whl')):
        kind = 'pypi-wheel'
    elif host in ('download.pytorch.org', 'download-r2.pytorch.org'):
        if (len(parts) >= 3 and parts[0] == 'whl'
                and unquote(parts[-1]).casefold() in PYTORCH_VERIFIED_WHEELS):
            kind = 'pytorch-wheel'
    if kind is None:
        return None
    clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
    return kind, clean


def public_download_kind(url):
    """Expose the allowlist classification for fixtures and diagnostics."""
    item = _public_download(url)
    return item[0] if item else None


def _legacy_repository_url(url):
    """Recognize the old two-segment GitHub repository validation URL.

    The prior API accepted repository pages.  Keep that compatibility path for
    callers that only validate a source, but never use it for a file transfer.
    """
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username
            or parsed.password or port not in (None, 443) or parsed.fragment
            or not _public_query(parsed.query)):
        return None
    parts = _safe_parts(parsed.path)
    if parts and len(parts) == 2:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
    return None


def _accelerated_url(url, source):
    if source in _GITHUB_PREFIXES:
        prefix = _GITHUB_PREFIXES[source]
        return prefix + (url[len('https://'):] if source == 'gitproxy' else url)
    if source == 'hf-mirror':
        parsed = urlsplit(url)
        return urlunsplit(('https', 'hf-mirror.com', parsed.path, parsed.query, ''))
    if source in PYPI_PREFIXES:
        parsed = urlsplit(url)
        return PYPI_PREFIXES[source] + parsed.path.lstrip('/')
    if source == PYTORCH_SOURCE_ID:
        parsed = urlsplit(url)
        parts = _safe_parts(parsed.path)
        if parts and parts[0] == 'whl' and len(parts) >= 3:
            return PYTORCH_PREFIX + '/'.join(parts[1:])
    return url


def accelerated_url(url, source='gitproxy'):
    """Legacy single-mirror API; defaults to GitProxy / HF-Mirror as before."""
    item = _public_download(url)
    if item is None:
        return url
    kind, clean = item
    if kind.startswith('github-'):
        selected = normalize_source(source)
        return _accelerated_url(clean, selected if selected in GITHUB_SOURCE_IDS else 'gitproxy')
    if kind == 'hf-resolve':
        return _accelerated_url(clean, HF_SOURCE_ID)
    if kind == 'pypi-wheel':
        selected = normalize_source(source)
        return _accelerated_url(clean, selected) if selected in PYPI_SOURCE_IDS else url
    if kind == 'pytorch-wheel':
        selected = normalize_source(source)
        return _accelerated_url(clean, selected) if selected == PYTORCH_SOURCE_ID else url
    return url


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def download_urls(url, source='auto'):
    """Return ordered official/mirror candidates for unauthenticated public files.

    ``auto`` is always direct-first.  ``mirror`` preserves the historical
    GitProxy-first behavior and retains a direct fallback.  An explicit station
    ID means "prefer that station, then use official direct" and never sends an
    unapproved URL to a third party.
    """
    source = normalize_source(source)
    if source == 'direct':
        return [url]
    public = _public_download(url)
    if public is None:
        legacy = _legacy_repository_url(url)
        if legacy and source in ('auto', 'mirror'):
            gitproxy = _accelerated_url(legacy, 'gitproxy')
            return _unique([legacy, gitproxy] if source == 'auto' else [gitproxy, legacy])
        return [url]
    kind, clean = public
    if kind.startswith('github-'):
        if source == 'gitproxy':
            return _unique([_accelerated_url(clean, 'gitproxy'), clean])
        if source == 'gh-proxy':
            return _unique([_accelerated_url(clean, 'gh-proxy'), clean])
        if source == 'ghfast':
            return _unique([_accelerated_url(clean, 'ghfast'), clean])
        if source in ('hf-mirror', 'pypi-tuna', 'pypi-aliyun', PYTORCH_SOURCE_ID):
            return [clean]
        mirrors = [_accelerated_url(clean, station) for station in _GITHUB_ACCELERATOR_ORDER]
        if source == 'mirror':
            return _unique([mirrors[0], mirrors[1], clean, mirrors[2]])
        return _unique([clean, *mirrors])
    if kind == 'hf-resolve':
        mirror = _accelerated_url(clean, HF_SOURCE_ID)
        if source in ('gitproxy', 'gh-proxy', 'ghfast', 'pypi-tuna', 'pypi-aliyun', PYTORCH_SOURCE_ID):
            return [clean]
        if source in ('mirror', 'hf-mirror'):
            return _unique([mirror, clean])
        return _unique([clean, mirror])
    if kind == 'pypi-wheel':
        if source in ('gitproxy', 'gh-proxy', 'ghfast', 'hf-mirror', PYTORCH_SOURCE_ID):
            return [clean]
        if source in PYPI_SOURCE_IDS:
            return _unique([_accelerated_url(clean, source), clean])
        mirrors = [_accelerated_url(clean, station) for station in PYPI_SOURCE_IDS]
        if source == 'mirror':
            return _unique([*mirrors, clean])
        return _unique([clean, *mirrors])
    if kind == 'pytorch-wheel':
        if source in ('gitproxy', 'gh-proxy', 'ghfast', 'hf-mirror', 'pypi-tuna', 'pypi-aliyun'):
            return [clean]
        mirror = _accelerated_url(clean, PYTORCH_SOURCE_ID)
        if source in ('mirror', PYTORCH_SOURCE_ID):
            return _unique([mirror, clean])
        return _unique([clean, mirror])
    return [url]


def normalize_component_source(source='auto'):
    if isinstance(source, str) and source.strip().casefold() in COMPONENT_SOURCES:
        return source.strip().casefold()
    return normalize_source(source)


def _component_alternate(item):
    return _ROFORMER_ALTERNATES.get((item.get('url'), item.get('bytes'), item.get('sha256')))


def component_stations(item):
    """Which explicit choices can change this pinned file's route."""
    if _component_alternate(item):
        return ('modelscope', 'github-release')
    kind = public_download_kind(item.get('url'))
    return {
        'hf-resolve': (HF_SOURCE_ID,),
        'pypi-wheel': _COMPONENT_PYPI_IDS,
        'pytorch-wheel': (PYTORCH_SOURCE_ID,),
    }.get(kind, ())


def component_source_options(items):
    """Build the menu from the files in the current install plan."""
    stations = {station for item in items for station in component_stations(item)}
    if not stations:
        return ()
    return (*SOURCE_OPTIONS[:2], *(row for row in _COMPONENT_STATIONS if row[1] in stations))


def component_download_urls(item, source='auto'):
    """Route only verified component files; resolve unrelated files by their own source."""
    source = normalize_component_source(source)
    url = item['url']
    if source == 'direct':
        return [url]
    alternate = _component_alternate(item)
    if alternate:
        github, modelscope = alternate
        # The mirror hosts weights only. The pinned YAML comes from the same
        # GitHub release as the backup weight, with a verified relay as fallback.
        gh_relay = _accelerated_url(github, 'gh-proxy')
        if source == 'github-release':
            mirrors = [github, gh_relay, modelscope]
        else:
            mirrors = [modelscope, github, gh_relay]
        if source in ('modelscope', 'github-release'):
            # A user who chose these routes can be on a network where Hugging
            # Face is unreachable. Do not send these requests back there.
            return _unique(mirrors)
        return _unique([url, *mirrors] if source == 'auto' else [*mirrors, url])
    stations = component_stations(item)
    if not stations:
        # Updates reuse the pinned-file downloader, but their GitHub provider
        # preferences are separate from the component source menu.
        if source in SOURCES and (public_download_kind(url) or '').startswith('github-'):
            return download_urls(url, source)
        return [url]
    if stations == _COMPONENT_PYPI_IDS:
        def wheel_url(station):
            if station == 'pypi-ustc':
                return _USTC_PYPI + urlsplit(url).path.lstrip('/')
            return _accelerated_url(url, station)
        order = _unique([source, *stations]) if source in stations else stations
        mirrors = [wheel_url(station) for station in order]
        return [url, *mirrors] if source == 'auto' else [*mirrors, url]
    return download_urls(url, source if source == 'auto' or source in stations else 'mirror')


def source_name(url):
    host = (urlsplit(url).hostname or '').casefold()
    return {
        'api.gitproxy.dev': 'GitProxy',
        'gh-proxy.com': 'GH-Proxy',
        'ghfast.top': 'ghfast.top',
        'hf-mirror.com': 'HF-Mirror',
        'mirrors.tuna.tsinghua.edu.cn': 'TUNA PyPI',
        'pypi.tuna.tsinghua.edu.cn': 'TUNA PyPI',
        'mirrors.aliyun.com': 'Aliyun PyPI',
        'mirrors.ustc.edu.cn': 'USTC PyPI',
        'files.pythonhosted.org': 'PyPI',
        'mirror.sjtu.edu.cn': 'SJTU PyTorch',
        'download.pytorch.org': 'PyTorch',
        'download-r2.pytorch.org': 'PyTorch',
        'github.com': 'GitHub',
        'modelscope.cn': 'ModelScope',
        'raw.githubusercontent.com': 'GitHub Raw',
        'huggingface.co': 'Hugging Face',
    }.get(host, '官方源')


def read_small(url, source='auto', limit=2 * 1024**2, opener=direct_urlopen, *, timeout=12, check=None):
    """Read bounded public metadata, trying approved routes without secrets."""
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError('读取大小限制无效。')
    last_error = None
    oversized = False
    for candidate in download_urls(url, source):
        if check is not None and check():
            raise InterruptedError('已取消更新检查。')
        request = urllib.request.Request(candidate, headers={
            'User-Agent': 'VWA-Updater',
            'Accept': 'application/json',
            'Accept-Encoding': 'identity',
        })
        try:
            with opener(request, timeout=timeout) as response:
                declared = response.headers.get('Content-Length')
                if declared:
                    try:
                        if int(declared) > limit:
                            oversized = True
                            continue
                    except ValueError:
                        pass
                data = response.read(limit + 1)
                if check is not None and check():
                    raise InterruptedError('已取消更新检查。')
                if len(data) > limit:
                    oversized = True
                    continue
                return data
        except InterruptedError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
    if oversized:
        raise ValueError('更新信息超过大小限制。')
    raise OSError('无法连接下载源，请切换下载方式后重试。') from last_error
