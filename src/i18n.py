"""Explicit display-text translation; stored task data and lyrics are never rewritten.

The UI language is selected before constructing a window. Changing the saved
preference takes effect on the next launch, without rebuilding active workflows.
JSON catalogs use Chinese source text and numbered placeholders. Unknown backend
diagnostics remain intact instead of being guessed or sent to a remote service.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

LANGUAGES = (('简体中文', 'zh-CN'), ('日本語', 'ja'), ('English', 'en'))
_language = 'zh-CN'
_root = Path(__file__).resolve().parent / 'assets' / 'i18n'
if not _root.is_dir():
    _root = Path(__file__).resolve().parent.parent / 'assets' / 'i18n'
_placeholder = re.compile(r'\{(\d+)\}')


def normalize_language(value):
    return value if value in ('zh-CN', 'ja', 'en') else 'zh-CN'


def set_language(value):
    global _language
    _language = normalize_language(value)
    return _language


def current_language():
    return _language


@lru_cache(maxsize=2)
def catalog(language):
    if language == 'zh-CN':
        return {}
    try:
        values = json.loads((_root / (language + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    try:
        additions = json.loads((_root / 'additions.json').read_text(encoding='utf-8'))
        values.update(additions.get(language, {}))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    # Malformed translated placeholders must never hide a functional warning.
    return {key: value for key, value in values.items()
            if isinstance(key, str) and isinstance(value, str) and value
            and sorted(_placeholder.findall(key)) == sorted(_placeholder.findall(value))}


@lru_cache(maxsize=2)
def _patterns(language):
    patterns = []
    for source, target in catalog(language).items():
        matches = list(_placeholder.finditer(source))
        literal_size = len(_placeholder.sub('', source))
        if not matches or literal_size < 4:
            continue
        pieces, offset, fields = [], 0, []
        for match in matches:
            pieces.append(re.escape(source[offset:match.start()]))
            pieces.append(r'([\s\S]*?)')
            fields.append(int(match[1]))
            offset = match.end()
        pieces.append(re.escape(source[offset:]))
        patterns.append((literal_size, re.compile(''.join(pieces)), target, fields))
    return sorted(patterns, key=lambda row: row[0], reverse=True)


@lru_cache(maxsize=4096)
def _translate(language, source):
    translated = catalog(language).get(source)
    if translated is not None:
        return translated
    # Compatibility for formatted messages emitted by existing workers. This
    # runs only at explicitly marked UI display sites, never over user editors.
    if len(source) <= 8192:
        for _, pattern, target, fields in _patterns(language):
            match = pattern.fullmatch(source)
            if match:
                values = [''] * (max(fields) + 1)
                for index, value in zip(fields, match.groups()):
                    values[index] = value
                return target.format(*values)
    # Never recursively translate arbitrary fields in a formatted message:
    # a user's task title can itself be identical to a UI word such as 发布.
    return source


def tr(source, *values):
    """Translate one display message, preserving formatted user data verbatim."""
    if not isinstance(source, str):
        return source
    translated = source if _language == 'zh-CN' else _translate(_language, source)
    if not values:
        return translated
    try:
        return translated.format(*values)
    except (IndexError, KeyError, ValueError):
        return source.format(*values)
