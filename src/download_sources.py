"""Public download routing shared by component installation and updates.

The router only rewrites unauthenticated public files, including pinned PyPI
and SJTU PyTorch wheel paths.  Authentication headers,
cookies, signed query strings and private APIs must stay on the original route.
"""
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
import urllib.error
import urllib.request

from common import direct_urlopen


# Public UI contract shared by the component and update pages.  The first three
# values intentionally preserve the historical priority modes.
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
        'files.pythonhosted.org': 'PyPI',
        'mirror.sjtu.edu.cn': 'SJTU PyTorch',
        'download.pytorch.org': 'PyTorch',
        'download-r2.pytorch.org': 'PyTorch',
        'github.com': 'GitHub',
        'raw.githubusercontent.com': 'GitHub Raw',
        'huggingface.co': 'Hugging Face',
    }.get(host, '官方源')


def read_small(url, source='auto', limit=2 * 1024**2, opener=direct_urlopen):
    """Read bounded public metadata, trying approved routes without secrets."""
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError('读取大小限制无效。')
    last_error = None
    oversized = False
    for candidate in download_urls(url, source):
        request = urllib.request.Request(candidate, headers={
            'User-Agent': 'VWA-Updater',
            'Accept': 'application/json',
            'Accept-Encoding': 'identity',
        })
        try:
            with opener(request, timeout=12) as response:
                declared = response.headers.get('Content-Length')
                if declared:
                    try:
                        if int(declared) > limit:
                            oversized = True
                            continue
                    except ValueError:
                        pass
                data = response.read(limit + 1)
                if len(data) > limit:
                    oversized = True
                    continue
                return data
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
    if oversized:
        raise ValueError('更新信息超过大小限制。')
    raise OSError('无法连接下载源，请切换下载方式后重试。') from last_error
