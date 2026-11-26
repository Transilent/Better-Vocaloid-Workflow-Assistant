"""Shared source URL rules used by the GUI and preparation boundaries."""
from urllib.parse import parse_qsl, urlsplit
import re


BILIBILI_SUBPAGE_QUERY_KEYS = frozenset({'cid', 'p', 'page'})
UNSUPPORTED_BILIBILI_SUBPAGE = 'unsupported_bilibili_subpage'
_BVID_PREFIX = re.compile(r'^BV1[A-Za-z0-9]{9}(?:[/?#]|$)')
_URL = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)


class SourceContractError(ValueError):
    """A source declaration rejected for a stable, caller-visible reason."""

    def __init__(self, message, *, code, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def bilibili_query_pairs(query):
    """Parse a Bilibili query while rejecting unsupported subpage selectors."""
    pairs = parse_qsl(query, keep_blank_values=True)
    subpage_keys = sorted({
        key.casefold() for key, _ in pairs
        if key.casefold() in BILIBILI_SUBPAGE_QUERY_KEYS
    })
    if subpage_keys:
        names = '、'.join(subpage_keys)
        raise SourceContractError(
            f'Bilibili 链接包含子页参数（{names}）；当前准备管线只保存 BV '
            '默认分 P，无法保留并核验目标子页。请提供指向目标内容的独立视频 '
            'BV/URL；只有确认目标就是默认分 P 时，才可移除这些参数后重试。',
            code=UNSUPPORTED_BILIBILI_SUBPAGE,
            details={'parameters': subpage_keys, 'supported_page': 'default'},
        )
    return pairs


def reject_unsupported_bilibili_subpage(source):
    """Reject subpage selectors in GUI-style Bilibili input.

    Non-Bilibili text is left to the existing extract_bv behaviour so this
    helper only tightens the source contract that could otherwise lose a page.
    """
    if not isinstance(source, str):
        return
    text = source.strip().strip('"')
    if not text:
        return
    candidates = _URL.findall(text)
    if not candidates and _BVID_PREFIX.match(text):
        candidates = [text]
    for candidate in candidates:
        parts = urlsplit(candidate)
        host = (parts.hostname or '').casefold().rstrip('.')
        is_bilibili_url = (parts.scheme.casefold() in ('http', 'https')
                           and (host == 'bilibili.com' or host.endswith('.bilibili.com')))
        is_bare_bv = not parts.scheme and bool(_BVID_PREFIX.match(candidate))
        if is_bilibili_url or is_bare_bv:
            bilibili_query_pairs(parts.query)


def normalize_source_url(value):
    """Canonicalize a caller declaration without accessing the network."""
    if (not isinstance(value, str) or not value or len(value) > 2048
            or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("source_url 必须是有效的 Bilibili HTTPS 视频链接。")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("source_url 端口无效。") from error
    if (parts.scheme.lower() != 'https' or (parts.hostname or '').lower() not in
            ('bilibili.com', 'www.bilibili.com') or parts.username is not None
            or parts.password is not None or port not in (None, 443) or parts.fragment):
        raise ValueError("source_url 仅支持 Bilibili HTTPS 视频链接。")
    bilibili_query_pairs(parts.query)
    match = re.fullmatch(r'/video/(BV1[A-Za-z0-9]{9})/?', parts.path)
    if not match:
        raise ValueError("source_url 必须指向 /video/BV… 视频。")
    return 'https://www.bilibili.com/video/' + match.group(1) + '/'
