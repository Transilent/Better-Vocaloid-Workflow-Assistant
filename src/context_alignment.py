"""Conservative, auditable alignment of lyric positions to sung notes.

This module is local and deterministic. It performs no reading inference,
model call, file access, or network request. Unknown material stays unknown;
in particular it never chooses one of several equally good locations for a
repeated lyric merely because a greedy matcher happened to find it.
"""
from __future__ import annotations


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value



import copy
import hashlib
import json
import math
import unicodedata

SCHEMA_VERSION = 1
KIND = 'vwa.context-alignment'
SUSTAIN_TOKENS = frozenset(('-', '+~'))
AMBIGUOUS_PLUS = '+'
_SINGABLE_CATEGORIES = frozenset(('L', 'N', 'M'))
_MAX_CELLS = 2000000
_ALIASES = {
    'shi': 'si', 'chi': 'ti', 'tsu': 'tu', 'fu': 'hu', 'ji': 'zi',
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + '必须是有限数字。')
    return float(value)


def _plain_text(char):
    return unicodedata.category(char)[0] in _SINGABLE_CATEGORIES


def _compare_token(token):
    """Normalize case/width and documented full romanization aliases only."""
    token = unicodedata.normalize('NFKC', token).casefold()
    token = token.replace("'", '').replace('\u2019', '')
    return _ALIASES.get(token, token)


def _note_token(lyric):
    if not lyric or any(not _plain_text(char) for char in lyric):
        return None
    return _compare_token(lyric)


def _normalized_notes(raw_notes):
    if not isinstance(raw_notes, (list, tuple)):
        raise ValueError('notes必须是列表或元组。')
    notes = []
    for index, raw in enumerate(raw_notes):
        prefix = 'notes[' + str(index) + ']'
        if not isinstance(raw, dict):
            raise ValueError(prefix + '必须是对象。')
        missing = [key for key in ('lyric', 'start', 'end', 'pitch')
                   if key not in raw]
        if missing:
            raise ValueError(prefix + '缺少字段：' + ', '.join(missing))
        lyric = raw['lyric']
        if not isinstance(lyric, str):
            raise ValueError(prefix + '.lyric必须是字符串。')
        start = _finite(raw['start'], prefix + '.start')
        end = _finite(raw['end'], prefix + '.end')
        pitch = _finite(raw['pitch'], prefix + '.pitch')
        if start < 0 or end < 0:
            raise ValueError(prefix + '的绝对时刻不能为负数。')
        if end <= start:
            raise ValueError(prefix + '.end必须晚于start。')
        notes.append({'lyric': lyric, 'start': start, 'end': end,
                      'pitch': pitch})
    if any(notes[index]['start'] > notes[index + 1]['start']
           for index in range(len(notes) - 1)):
        raise ValueError('notes必须按start升序排列。')
    return notes


def _normalize_reading_units(original, raw_units):
    if raw_units is None:
        return []
    if not isinstance(raw_units, (list, tuple)):
        raise ValueError('reading_units必须是列表或元组。')
    units = []
    for index, raw in enumerate(raw_units):
        prefix = 'reading_units[' + str(index) + ']'
        if not isinstance(raw, dict):
            raise ValueError(prefix + '必须是对象。')
        missing = [key for key in ('span', 'text', 'tokens', 'source')
                   if key not in raw]
        if missing:
            raise ValueError(prefix + '缺少字段：' + ', '.join(missing))
        span = raw['span']
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) is not int for value in span)):
            raise ValueError(prefix + '.span必须是两个整数。')
        start, end = span
        if start < 0 or end <= start or end > len(original):
            raise ValueError(prefix + '.span超出原文范围或为空。')
        text = raw['text']
        if not isinstance(text, str) or text != original[start:end]:
            raise ValueError(prefix + '.text必须与原文span逐字一致。')
        source = raw['source']
        if not isinstance(source, str) or not source.strip():
            raise ValueError(prefix + '.source必须是非空字符串。')
        tokens = raw['tokens']
        if not isinstance(tokens, (list, tuple)) or not tokens:
            raise ValueError(prefix + '.tokens必须是非空列表或元组。')
        normalized_tokens = []
        for token_index, token in enumerate(tokens):
            if not isinstance(token, str) or not token.strip():
                raise ValueError(prefix + '.tokens[' + str(token_index)
                                 + ']必须是非空字符串。')
            normalized_tokens.append(token)
        units.append({'span': [start, end], 'text': text,
                      'tokens': normalized_tokens, 'source': source})
    ordered = sorted(units, key=lambda item: (item['span'][0],
                                              item['span'][1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current['span'][0] < previous['span'][1]:
            raise ValueError('reading_units的span不能重叠。')
    return ordered


def _text_units(original, reading_units):
    """Expand each explicit token into an expected sequence element.

    Every token retains its whole source word span and its ordinal inside that
    word. A multi-mora word does not prove which character owns each mora.
    Uncovered text is added exactly once as a codepoint fallback.
    """
    rows = []
    covered = set()
    for word_index, reading in enumerate(reading_units):
        start, end = reading['span']
        covered.update(range(start, end))
        for word_token_index, token in enumerate(reading['tokens']):
            rows.append(((start, end, 0, word_index, word_token_index), {
                'token': token,
                'normalized_token': _compare_token(token),
                'span': list(reading['span']),
                'source': 'reading',
                'word_token_index': word_token_index,
            }))
    for index, char in enumerate(original):
        if index in covered or not _plain_text(char):
            continue
        rows.append(((index, index + 1, 1, index, 0), {
            'token': char,
            'normalized_token': _compare_token(char),
            'span': [index, index + 1],
            'source': 'text',
            'word_token_index': 0,
        }))
    result = [unit for _, unit in sorted(rows, key=lambda row: row[0])]
    for index, unit in enumerate(result):
        unit['index'] = index
    return result

def _note_tokens(notes):
    return [{
        'lyric': note['lyric'],
        'token': _note_token(note['lyric']),
        'fixed': (note['lyric'] in SUSTAIN_TOKENS
                  or note['lyric'] == AMBIGUOUS_PLUS
                  or _note_token(note['lyric']) is None),
    } for note in notes]


def _best_links(note_tokens, text_units):
    """Return exact links that are mandatory across every optimum.

    A fixed sign may only skip its own note row. It cannot consume source on a
    diagonal. A normal note may match (0), substitute/remain unknown (1), or
    delete source (1). An emitted link must be the only exact candidate for
    that note and the best optimum that leaves the note unknown must be worse.
    """
    n, m = len(note_tokens), len(text_units)
    if (n + 1) * (m + 1) > _MAX_CELLS:
        raise ValueError('对齐规模过大，拒绝产生不可审计的全曲歧义矩阵。')

    forward = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        forward[i][0] = forward[i - 1][0] + (
            0 if note_tokens[i - 1]['fixed'] else 1)
    for j in range(1, m + 1):
        forward[0][j] = j
    for i in range(1, n + 1):
        fixed = note_tokens[i - 1]['fixed']
        token = note_tokens[i - 1]['token']
        for j in range(1, m + 1):
            choices = [forward[i - 1][j] + (0 if fixed else 1)]
            if not fixed:
                choices.extend((
                    forward[i][j - 1] + 1,
                    forward[i - 1][j - 1] + (
                        0 if text_units[j - 1]['normalized_token'] == token
                        else 1),
                ))
            forward[i][j] = min(choices)

    backward = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        backward[i][m] = backward[i + 1][m] + (
            0 if note_tokens[i]['fixed'] else 1)
    for j in range(m - 1, -1, -1):
        backward[n][j] = backward[n][j + 1] + 1
    for i in range(n - 1, -1, -1):
        fixed = note_tokens[i]['fixed']
        token = note_tokens[i]['token']
        for j in range(m - 1, -1, -1):
            choices = [backward[i + 1][j] + (0 if fixed else 1)]
            if not fixed:
                choices.extend((
                    backward[i][j + 1] + 1,
                    backward[i + 1][j + 1] + (
                        0 if text_units[j]['normalized_token'] == token
                        else 1),
                ))
            backward[i][j] = min(choices)

    optimum = forward[n][m]
    if backward[0][0] != optimum:
        raise AssertionError('alignment dynamic program is inconsistent')

    pairs = []
    for i, note in enumerate(note_tokens, 1):
        if note['fixed']:
            continue
        # Any exact edge can be the sole candidate, but a cheaper exact edge
        # would make a substitution unnecessary. Collect same-cost candidates.
        candidates = []
        alts = []
        for j in range(0, m + 1):
            # Unknown route requires a note skip regardless of source suffix.
            # j=0 matters when a later note, not this one, owns the sole source.
            alts.append(forward[i - 1][j] + 1 + backward[i][j])
            if j == 0:
                continue
            if text_units[j - 1]['normalized_token'] != note['token']:
                # A mismatch diagonal is also a valid unknown result.
                alts.append(forward[i - 1][j - 1] + 1 + backward[i][j])
                continue
            through = forward[i - 1][j - 1] + backward[i][j]
            if through == optimum:
                candidates.append(j - 1)
        # A candidate is mandatory only if every alternative is worse. This
        # deliberately includes all j and all mismatch diagonals, so a local
        # exact edge cannot hide a same-cost unknown path elsewhere.
        if len(candidates) != 1 or not alts or min(alts) <= optimum:
            continue
        token_index = candidates[0]
        # A mandatory exact edge cannot share its source token with another
        # mandatory note in a one-to-one monotone path. Rechecking every other
        # note here adds cubic work without strengthening that condition.
        pairs.append((i - 1, token_index))
    return optimum, sorted(pairs)

def _format_alignment(original, language, notes, reading_units, pairs, cost):
    expected_tokens = _text_units(original, reading_units)
    pair_by_note = dict(pairs)
    note_spans = []
    note_token_indexes = []
    bases = []
    for index, note in enumerate(notes):
        lyric = note['lyric']
        if index in pair_by_note:
            token_index = pair_by_note[index]
            note_spans.append(list(expected_tokens[token_index]['span']))
            note_token_indexes.append(token_index)
            bases.append('matched_text')
        elif lyric in SUSTAIN_TOKENS and index > 0 and note_spans[index - 1]:
            note_spans.append(list(note_spans[index - 1]))
            note_token_indexes.append(note_token_indexes[index - 1])
            bases.append('sustained')
        else:
            note_spans.append(None)
            note_token_indexes.append(None)
            bases.append('orphan_sustain' if lyric in SUSTAIN_TOKENS
                         else 'ambiguous_plus' if lyric == AMBIGUOUS_PLUS
                         else 'nontext_lyric' if _note_token(lyric) is None
                         else 'local_unknown')
    source_positions = {index for index, char in enumerate(original)
                        if _plain_text(char)}
    mapped_positions = {
        position
        for span in note_spans if span is not None
        for position in range(span[0], span[1])
        if position in source_positions
    }
    mapped_notes = sum(1 for span in note_spans if span is not None)
    unmapped_notes = len(notes) - mapped_notes
    if (mapped_notes and mapped_positions == source_positions
            and unmapped_notes == 0):
        status = 'complete'
    elif mapped_notes:
        status = 'partial'
    elif notes:
        status = 'unknown'
    else:
        status = 'missing'
    coverage = {
        'text_units': {
            'total': len(source_positions),
            'mapped': len(mapped_positions),
            'fraction': (len(mapped_positions) / len(source_positions)
                         if source_positions else 0.0),
            'basis': 'original_codepoint_set_covered_by_note_spans',
        },
        'notes': {
            'total': len(notes), 'mapped': mapped_notes,
            'fraction': mapped_notes / len(notes) if notes else 0.0,
        },
        'status': status,
    }
    unknowns = [{'note_index': index, 'lyric': note['lyric'],
                 'reason': bases[index]}
                for index, span in enumerate(note_spans) if span is None]
    unsigned = {
        'schema_version': SCHEMA_VERSION,
        'kind': KIND,
        'source': {
            'text_sha256': hashlib.sha256(original.encode('utf-8')).hexdigest(),
            'language': language,
            'notes': copy.deepcopy(notes),
            'notes_sha256': _hash(notes),
        },
        'reading_units': copy.deepcopy(reading_units),
        'expected_tokens': expected_tokens,
        'note_spans': note_spans,
        'note_token_indexes': note_token_indexes,
        'note_bases': bases,
        'coverage': coverage,
        'metadata': {
            'span_coordinate': 'python_codepoint_half_open',
            'word_span_is_character_reading': False,
            'note': ('A word span identifies source ownership only. It does '
                     'not validate the pronunciation of each character or '
                     'prove that a missing word-initial mora exists.'),
            'matching': 'only links shared by all minimum-cost alignments',
        },
        'unknowns': unknowns,
        'algorithm': {
            'method': 'minimum_edit_all_optima',
            'edit_cost': cost,
        },
    }
    return unsigned


def align_lyrics(original_text, language, notes, *, reading_units=None):
    """Align lyric text codepoint spans to notes without changing inputs."""
    if not isinstance(original_text, str):
        raise ValueError('original_text必须是字符串。')
    if not isinstance(language, str) or not language.strip():
        raise ValueError('language必须是非空字符串。')
    normalized_notes = _normalized_notes(notes)
    normalized_units = _normalize_reading_units(original_text, reading_units)
    text_units = _text_units(original_text, normalized_units)
    cost, pairs = _best_links(_note_tokens(normalized_notes), text_units)
    result = _format_alignment(original_text, language, normalized_notes,
                               normalized_units, pairs, cost)
    result['alignment_id'] = _hash(result)
    return result


def validate_alignment(alignment, original_text, language, notes):
    """Recompute input/content consistency and return a deep copy.

    This proves deterministic content agreement only; it does not establish
    that the supplied pronunciation evidence is factually correct.
    """
    if not isinstance(alignment, dict):
        raise ValueError('alignment必须为对象。')
    if ('schema_version' not in alignment
            or type(alignment['schema_version']) is not int
            or alignment['schema_version'] != SCHEMA_VERSION
            or canonical_kind(alignment.get('kind')) != KIND):
        raise ValueError('alignment版本或类型无效。')
    expected = align_lyrics(original_text, language, notes,
                            reading_units=alignment.get('reading_units'))
    if alignment['kind'] != expected['kind']:
        expected['kind'] = alignment['kind']
        expected.pop('alignment_id')
        expected['alignment_id'] = _hash(expected)
    if alignment != expected:
        raise ValueError('alignment内容、输入或算法版本不一致。')
    return copy.deepcopy(alignment)
