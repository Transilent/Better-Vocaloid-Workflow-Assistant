"""Optional, offline Japanese surface-word readings for explicit alignment.

No ASR, synthesis, network fallback, dictionary installation, or emotion model.
Surface spans remain word-level when kanji reading ownership is ambiguous.
"""
from pathlib import Path
import hashlib
import unicodedata


def phones_to_moras(phones):
    """Group OpenJTalk phones; preserve N and closure as distinct tokens."""
    if not isinstance(phones, (list, tuple)):
        raise ValueError('Japanese phones must be a sequence.')
    result, consonant = [], ''
    for phone in phones:
        if not isinstance(phone, str):
            raise ValueError('Japanese phone must be text.')
        if phone in ('sil', 'pau'):
            if consonant:
                raise ValueError('Unfinished consonant in reading.')
            continue
        if phone in ('N', 'cl'):
            if consonant:
                raise ValueError('Unfinished consonant in reading.')
            result.append('n' if phone == 'N' else 'cl')
        elif phone.lower() in ('a', 'i', 'u', 'e', 'o'):
            result.append(consonant + phone.lower())
            consonant = ''
        elif phone.isascii() and phone.isalpha():
            consonant += phone.lower()
        else:
            raise ValueError('Unsupported Japanese phone: ' + phone)
    if consonant:
        raise ValueError('Unfinished consonant in reading.')
    return result


def reading_units_from_frontend(text, rows, phone_reader):
    """Build verifiable original spans without searching past a mismatch."""
    if not isinstance(text, str) or not isinstance(rows, list):
        raise ValueError('Invalid Japanese frontend input.')
    units, words, issues = [], [], []
    cursor, previous_vowel = 0, None
    for index, row in enumerate(rows):
        surface = row.get('string') if isinstance(row, dict) else None
        if not isinstance(surface, str) or not surface:
            raise ValueError('Frontend surface is missing.')
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if not text.startswith(surface, cursor):
            issues.append({'code': 'frontend_surface_mismatch', 'row': index,
                           'cursor': cursor, 'surface': surface,
                           'remaining_text_unassigned': True})
            break
        start, end = cursor, cursor + len(surface)
        cursor = end
        if not any(unicodedata.category(c)[0] in 'LNM' for c in surface):
            continue
        pron = row.get('pron', '')
        if not pron or pron == '*':
            issues.append({'code': 'reading_unavailable', 'span': [start, end]})
            continue
        if pron.startswith('ー'):
            # NJD sometimes emits a separate auxiliary as a long-vowel mark.
            # Keep its own source span; derive only the known preceding vowel.
            if previous_vowel is None:
                issues.append({'code': 'orphan_long_vowel', 'span': [start, end]})
                continue
            pron = {'a':'ア','i':'イ','u':'ウ','e':'エ','o':'オ'}[previous_vowel] + pron[1:]
        try:
            tokens = phones_to_moras(phone_reader(pron))
        except ValueError as error:
            issues.append({'code': 'reading_unsupported', 'span': [start, end],
                           'reason': str(error)})
            continue
        if not tokens:
            continue
        previous_vowel = tokens[-1][-1] if tokens[-1][-1] in 'aiueo' else None
        units.append({'span': [start, end], 'text': surface, 'tokens': tokens,
                      'source': 'local_pyopenjtalk_frontend_word'})
        words.append({'span': [start, end], 'source': 'local_pyopenjtalk_frontend_word'})
    if any(not c.isspace() for c in text[cursor:]):
        issues.append({'code': 'frontend_unassigned_tail', 'span': [cursor, len(text)]})
    return {'units': units, 'words': words, 'issues': issues,
            'metadata': {'backend': 'local_pyopenjtalk', 'network_calls': False,
                         'granularity': 'surface_word_not_individual_kanji',
                         'reading_status': 'dictionary_hypothesis_not_acoustic_verification'}}


def _local_dictionary(dictionary=None):
    import pyopenjtalk
    folder = (Path(dictionary) if dictionary is not None else
              Path(pyopenjtalk.__file__).parent / 'open_jtalk_dic_utf_8-1.11')
    if not folder.is_dir() or not (folder/'sys.dic').is_file():
        raise FileNotFoundError('本机日语读音字典不可用；请提供显式reading_units，不自动下载。')
    return pyopenjtalk, folder


def local_reading_identity(*, dictionary=None):
    """Bind cached readings to installed frontend/dictionary bytes."""
    pyopenjtalk, folder = _local_dictionary(dictionary)
    files = [p for name in ('sys.dic','matrix.bin','char.bin','unk.dic','dicrc')
             if (p := folder/name).is_file()]
    files.extend(Path(pyopenjtalk.__file__).parent.glob('*.pyd'))
    return {'backend': 'local_pyopenjtalk',
            'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def japanese_reading_units(text, *, dictionary=None):
    """Use only an already installed dictionary; never invoke auto-download."""
    pyopenjtalk, folder = _local_dictionary(dictionary)
    engine = pyopenjtalk.OpenJTalk(dn_mecab=str(folder).encode('utf-8'))
    rows = engine.run_frontend(text)
    result = reading_units_from_frontend(text, rows, lambda pron: engine.g2p(pron, join=False))
    result['metadata']['runtime_identity'] = local_reading_identity(dictionary=folder)
    return result
