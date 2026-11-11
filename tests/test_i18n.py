"""Catalog integrity for the shared translation assets."""
import json
import os
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
assert os.environ.get('BVWA_TESTING') == '1'

import i18n


def _unique_json_object(pairs):
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, 'Duplicate JSON keys: ' + ', '.join(duplicates)
    return dict(pairs)


ADDITIONS = json.loads(
    (APP / 'assets/i18n/additions.json').read_text(encoding='utf-8'),
    object_pairs_hook=_unique_json_object,
)
assert set(ADDITIONS) == {'en', 'ja'}
assert set(ADDITIONS['en']) == set(ADDITIONS['ja'])
_FORMAT_PLACEHOLDER = re.compile(r'\{(\d+)(?::[^}]*|![^}]*)?\}')
_PRINTF_PLACEHOLDER = re.compile(r'%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?[sdgfr]')


def _placeholder_shape(text):
    return (sorted(_FORMAT_PLACEHOLDER.findall(text)),
            sorted(_PRINTF_PLACEHOLDER.findall(text)))


for language in ('en', 'ja'):
    for source, target in ADDITIONS[language].items():
        assert isinstance(target, str) and target.strip(), (language, source)
        assert _placeholder_shape(source) == _placeholder_shape(target), (language, source, target)
    merged = i18n.catalog(language)
    for source, target in ADDITIONS[language].items():
        assert merged.get(source) == target, (language, source)
    raw = json.loads((APP / 'assets/i18n' / (language + '.json')).read_text(encoding='utf-8'))
    assert len(raw) >= 1200
    for source, target in raw.items():
        assert isinstance(target, str) and target.strip(), (language, source)
        assert sorted(re.findall(r'\{\d+\}', source)) == sorted(re.findall(r'\{\d+\}', target)), (language, source)
    assert len(i18n.catalog(language)) >= len(raw)

print('PASS: i18n catalogs and placeholder shapes')
