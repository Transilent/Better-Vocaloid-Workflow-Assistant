"""Read the bundled CMU lexicon and produce VOCALOID English phonemes."""

import os
from functools import lru_cache
from pathlib import Path
import re

from common import ROOT, native_path
from preparation_languages import ENGLISH_DICTIONARY, HUBERTFA_MODEL, lyric_continuation, normalize_english_punctuation


DICTIONARY_PATH = Path('dependencies/vocal2midi') / HUBERTFA_MODEL / ENGLISH_DICTIONARY
_VOWELS = {
    'aa': 'Q', 'ae': '{', 'ah': 'V', 'ao': 'O:', 'aw': 'aU', 'ax': '@', 'ay': 'aI',
    'eh': 'e', 'er': '@r', 'ey': 'eI', 'ih': 'I', 'iy': 'i:', 'ow': '@U', 'oy': 'OI',
    'uh': 'U', 'uw': 'u:',
}
_RHOTIC = {
    'aa': 'Q@', 'ao': 'O@', 'eh': 'e@', 'ih': 'I@', 'iy': 'I@', 'uh': 'U@', 'uw': 'U@',
    'ah': '@r', 'ax': '@r',
}
_NATIVE_VOWELS = frozenset((*_VOWELS.values(), *_RHOTIC.values()))
_PHONES = {
    **{phone: phone for phone in 'b d f g k l m n p r s t v w z'.split()},
    **_VOWELS,
    'hh': 'h', 'ch': 'tS', 'jh': 'dZ', 'dh': 'D', 'th': 'T', 'sh': 'S', 'zh': 'Z',
    'ng': 'N', 'y': 'j', 'dx': 'd', '_r': 'r',
}
_CLUSTERS = frozenset(
    tuple(cluster.split()) for cluster in (
        'p l', 'p r', 'b l', 'b r', 'k l', 'k r', 'g l', 'g r', 'f l', 'f r',
        't r', 'd r', 'th r', 'sh r', 'ch r', 'jh r', 't w', 'd w', 'k w', 'g w',
        's l', 's m', 's n', 's p', 's t', 's k', 's w', 's p l', 's p r', 's t r', 's k r', 's k w',
        'p y', 'b y', 'm y', 'f y', 'v y', 'k y', 'g y', 'hh y', 'n y', 't y', 'd y',
    )
)
_WORD = r"[a-z]+(?:['-][a-z]+)*"


@lru_cache(maxsize=2)
def _dictionary(path, mtime_ns, size):
    words = {}
    with open(native_path(path), encoding='utf-8-sig') as stream:
        for line in stream:
            word, tab, pronunciation = line.strip().partition('\t')
            if tab and (match := re.fullmatch(rf'({_WORD})(?:\([0-9]+\))?', word)):
                phones = tuple(pronunciation.split())
                if phones and all(phone in _PHONES for phone in phones):
                    words.setdefault(match[1], []).append(phones)
    if not words:
        raise ValueError('随包的英文发音词典为空。')
    return words


def _syllables(phones):
    nuclei = [index for index, phone in enumerate(phones) if phone in _VOWELS]
    if not nuclei:
        return ()
    starts = [0]
    for left, right in zip(nuclei, nuclei[1:]):
        for index in range(max(left + 1, right - 3), right):
            onset = tuple('r' if phone == '_r' else phone for phone in phones[index:right])
            if onset in _CLUSTERS or (len(onset) == 1 and onset[0] not in ('ng', 'dx')):
                starts.append(index)
                break
        else:
            starts.append(right)

    syllables = []
    for start, end, vowel in zip(starts, [*starts[1:], len(phones)], nuclei):
        onset = tuple('l0' if phone == 'l' else _PHONES[phone] for phone in phones[start:vowel])
        nucleus, coda = _PHONES[phones[vowel]], phones[vowel + 1:end]
        if coda and coda[0] == 'r' and phones[vowel] in _RHOTIC:
            nucleus, coda = _RHOTIC[phones[vowel]], coda[1:]
        syllables.append((onset, nucleus, tuple(_PHONES[phone] for phone in coda)))
    return tuple(syllables)


def resolve_dictionary_path() -> Path | None:
    candidates = [ROOT / DICTIONARY_PATH]
    explicit = os.environ.get('VWA_EXPORT_RUNTIME', '').strip()
    if explicit:
        candidates.append(Path(explicit).parent.parent / DICTIONARY_PATH)
    for path in candidates:
        if path.is_file():
            return path
    return None


def syllables_for(lyric, count=None):
    """Return onset, vowel and coda for each syllable, using an attested variant."""
    word = normalize_english_punctuation(lyric).strip().casefold()
    if not re.fullmatch(_WORD, word):
        return ()
    path = resolve_dictionary_path() or (ROOT / DICTIONARY_PATH)
    metadata = path.stat()
    dictionary = _dictionary(path, metadata.st_mtime_ns, metadata.st_size)
    variants = dictionary.get(word)
    if variants:
        readings = tuple(reading for phones in variants if (reading := _syllables(phones)))
        return next((reading for reading in readings if len(reading) == count), readings[0] if readings else ())
    if '-' in word:
        chunks = [syllables_for(part) for part in word.split('-')]
        if all(chunks):
            return tuple(syllable for chunk in chunks for syllable in chunk)
    return ()


def _phones(syllable):
    onset, vowel, coda = syllable
    return (*onset, vowel, *coda)


def pronounce_word(lyric):
    return ' '.join(phone for syllable in syllables_for(lyric) for phone in _phones(syllable))


def pronounce_notes(notes):
    """Map ordered (tick, duration, lyric) notes to native phones and review items."""
    phonemes, issues = [], []
    index = 0
    while index < len(notes):
        tick, duration, lyric = notes[index]
        if lyric_continuation(lyric):
            phonemes.append('@')
            issues.append({'tick': tick, 'lyric': lyric, 'reason': 'orphan_continuation'})
            index += 1
            continue
        end = tick + duration
        groups = [[index]]
        cursor = index + 1
        while cursor < len(notes):
            next_tick, length, next_lyric = notes[cursor]
            kind = lyric_continuation(next_lyric)
            if not kind or not 0 <= next_tick - end <= 1:
                break
            if kind == 'hold':
                groups[-1].append(cursor)
            else:
                groups.append([cursor])
            if kind == 'anchor':
                issues.append({'tick': next_tick, 'lyric': next_lyric, 'reason': 'numbered_continuation'})
            end = next_tick + length
            cursor += 1

        reading = syllables_for(lyric, len(groups))
        if not reading:
            issues.append({'tick': tick, 'lyric': lyric, 'reason': 'unknown_word'})
        elif len(reading) != len(groups):
            issues.append({'tick': tick, 'lyric': lyric, 'reason': 'syllable_count'})
        for group_index, group in enumerate(groups):
            if group_index >= len(reading):
                phones = ('@',)
            elif group_index == len(groups) - 1:
                phones = tuple(phone for syllable in reading[group_index:] for phone in _phones(syllable))
            else:
                phones = _phones(reading[group_index])
            if len(group) == 1:
                phonemes.append(' '.join(phones))
                continue
            vowel_end = max(i for i, phone in enumerate(phones) if phone in _NATIVE_VOWELS) + 1
            phonemes.append(' '.join(phones[:vowel_end]))
            phonemes.extend(['-'] * (len(group) - 2))
            # Yamaha's English demo places the closing consonants after the
            # last held vowel, for example "r u:" / "-" / "- m".
            phonemes.append(' '.join(('-', *phones[vowel_end:])))
        index = cursor
    return phonemes, issues
