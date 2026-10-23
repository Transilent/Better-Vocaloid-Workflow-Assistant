"""Deterministic evidence linking original lyric text to sung-note data.

The builder records only explicit, reproducible links. It performs no
pronunciation inference, word segmentation, emotion classification, or audio
quality assessment. Original-text spans use Python string indexes: Unicode code
points, zero based, with an exclusive end.
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
KIND = 'vwa.context-evidence'
SUSTAIN_TOKENS = frozenset(('-', '+~'))
_UNKNOWN_LYRIC = '+'
_SINGABLE_CATEGORIES = frozenset(('L', 'N', 'M'))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def verify_evidence_fingerprint(evidence):
    """Check content/revision consistency without authenticating its origin.

    A caller may supply JSON evidence to optional planning APIs. Reusing a
    previous revision after editing its text/notes must not preserve old
    semantic references. Producer/file receipts remain the source authority.
    """
    if not isinstance(evidence, dict):
        raise ValueError('evidence必须为对象。')
    if (type(evidence.get('schema_version')) is not int
            or evidence.get('schema_version') != SCHEMA_VERSION
            or canonical_kind(evidence.get('kind')) != KIND):
        raise ValueError('evidence版本或类型无效。')
    expected = evidence.get('evidence_id')
    if (not isinstance(expected, str) or len(expected) != 64
            or any(char not in '0123456789abcdef' for char in expected)):
        raise ValueError('evidence_id必须为小写SHA-256。')
    try:
        unsigned = {key: value for key, value in evidence.items() if key != 'evidence_id'}
        actual = hashlib.sha256(_canonical(unsigned).encode('utf-8')).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('evidence包含无效JSON内容。') from error
    if actual != expected:
        raise ValueError('evidence内容与evidence_id不一致，请从当前输入重建证据。')
    return actual


def _stable_id(prefix, value, occurrence):
    payload = {'content': value, 'occurrence': occurrence}
    digest = hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()
    return prefix + '_' + digest[:24]


def _finite_number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + '必须是有限数字。')
    return float(value)


def _text_char(char):
    return unicodedata.category(char)[0] in _SINGABLE_CATEGORIES


def _line_starts(text):
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(text) if char == '\n')
    return starts


def _line_at(starts, position):
    low, high = 0, len(starts)
    while low < high:
        middle = (low + high) // 2
        if starts[middle] <= position:
            low = middle + 1
        else:
            high = middle
    return max(0, low - 1)


def _analysis_text(text):
    return ''.join(char for char in text if _text_char(char))


def _text_units(original):
    starts = _line_starts(original)
    units = []
    for position, char in enumerate(original):
        if not _text_char(char):
            continue
        line_index = _line_at(starts, position)
        content = {
            'text': char,
            'span': [position, position + 1],
            'line_index': line_index,
        }
        units.append({
            'id': _stable_id('unit', content, position),
            **content,
        })
    return units


def _issue(code, severity, message, **details):
    return {'code': code, 'severity': severity, 'message': message, **details}


def _normalize_notes(raw_notes):
    if not isinstance(raw_notes, (list, tuple)):
        raise ValueError('notes必须是列表。')
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
        start = _finite_number(raw['start'], prefix + '.start')
        end = _finite_number(raw['end'], prefix + '.end')
        pitch = _finite_number(raw['pitch'], prefix + '.pitch')
        if start < 0 or end < 0:
            raise ValueError(prefix + '的绝对时刻不能为负数。')
        if end == start:
            raise ValueError(prefix + '的时长必须大于零。')
        if end < start:
            raise ValueError(prefix + '.end不能早于start。')
        content = {'lyric': lyric, 'start': start, 'end': end, 'pitch': pitch}
        notes.append({
            'id': _stable_id('note', content, index),
            **content,
            'text_unit_ids': [],
            'syllable_ids': [],
        })
    return notes


def _normalize_syllables(raw_syllables):
    if not isinstance(raw_syllables, (list, tuple)):
        raise ValueError('syllables必须是列表或元组。')
    result = []
    for index, raw in enumerate(raw_syllables):
        prefix = 'syllables[' + str(index) + ']'
        if not isinstance(raw, dict):
            raise ValueError(prefix + '必须是对象。')
        missing = [key for key in ('lyric', 'start', 'end') if key not in raw]
        if missing:
            raise ValueError(prefix + '缺少字段：' + ', '.join(missing))
        lyric = raw['lyric']
        if not isinstance(lyric, str):
            raise ValueError(prefix + '.lyric必须是字符串。')
        start = _finite_number(raw['start'], prefix + '.start')
        end = _finite_number(raw['end'], prefix + '.end')
        if start < 0 or end < 0:
            raise ValueError(prefix + '的绝对时刻不能为负数。')
        if end == start:
            raise ValueError(prefix + '的时长必须大于零。')
        if end < start:
            raise ValueError(prefix + '.end不能早于start。')
        content = {'lyric': lyric, 'start': start, 'end': end}
        result.append({
            'id': _stable_id('syllable', content, index),
            **content,
        })
    return result


def _time_overlaps(items, kind, issues):
    ordered = sorted(enumerate(items),
                     key=lambda row: (row[1]['start'], row[1]['end'], row[0]))
    active = []
    for index, item in ordered:
        active = [other for other in active if other[1]['end'] > item['start']]
        for other_index, other in active:
            issues.append(_issue(
                kind + '.time_overlap', 'warning', kind + '存在重叠时段。',
                first_id=other['id'], second_id=item['id'],
                first_index=other_index, second_index=index))
        active.append((index, item))


def _overlap(left, right):
    return left['start'] < right['end'] and right['start'] < left['end']


def _link_time(notes, syllables, issues):
    _time_overlaps(notes, 'note', issues)
    _time_overlaps(syllables, 'syllable', issues)
    for note in notes:
        note['syllable_ids'] = [
            syllable['id'] for syllable in syllables
            if _overlap(note, syllable)
        ]
        if len(note['syllable_ids']) > 1:
            issues.append(_issue(
                'note.multiple_syllables', 'warning',
                '一个音符与多个音节重叠；不据此确认语义。',
                note_id=note['id'],
                syllable_ids=list(note['syllable_ids'])))
    for syllable in syllables:
        matched = [
            note for note in notes
            if syllable['id'] in note['syllable_ids']
        ]
        if not matched:
            issues.append(_issue(
                'syllable.unmatched', 'warning',
                '音节没有与音符时段重叠。',
                syllable_id=syllable['id']))
        elif len(matched) > 1:
            issues.append(_issue(
                'syllable.multiple_notes', 'warning',
                '一个音节与多个音符重叠；仅保留时间证据。',
                syllable_id=syllable['id'],
                note_ids=[note['id'] for note in matched]))


def _map_text_to_notes(content, notes, issues):
    """Return per-note unit positions only for one complete exact sequence."""
    if not content or not notes:
        for index, note in enumerate(notes):
            issues.append(_issue(
                'note.unknown_text', 'warning',
                '音符没有可关联的非空原文文本。',
                note_id=note['id'], note_index=index))
        return [None] * len(notes), False
    if any(notes[index]['start'] > notes[index + 1]['start']
           for index in range(len(notes) - 1)):
        issues.append(_issue(
            'note.start_out_of_order', 'warning',
            '音符起点不是升序；不确认文本顺序。'))
        for index, note in enumerate(notes):
            issues.append(_issue(
                'note.unknown_text', 'warning',
                '音符顺序未通过验证，未建立文本映射。',
                note_id=note['id'], note_index=index))
        return [None] * len(notes), False
    positions = []
    rendered = []
    cursor = 0
    previous = None
    unknown = False
    for index, note in enumerate(notes):
        lyric = note['lyric']
        if lyric in SUSTAIN_TOKENS:
            if previous is None:
                positions.append(None)
                unknown = True
                issues.append(_issue(
                    'note.orphan_sustain', 'warning',
                    '延音没有可继承的已知前单元。',
                    note_id=note['id'], note_index=index))
            else:
                positions.append(list(previous))
        elif lyric == _UNKNOWN_LYRIC:
            positions.append(None)
            previous = None
            unknown = True
            issues.append(_issue(
                'note.ambiguous_plus', 'warning',
                '单独“+”不是可靠延音，保留为未知。',
                note_id=note['id'], note_index=index))
        elif not lyric or any(not _text_char(char) for char in lyric):
            positions.append(None)
            previous = None
            unknown = True
            issues.append(_issue(
                'note.nontext_lyric', 'warning',
                '音符歌词为空或含有非文本标记，未建立映射。',
                note_id=note['id'], note_index=index))
        else:
            span = list(range(cursor, cursor + len(lyric)))
            positions.append(span)
            rendered.append(lyric)
            cursor += len(lyric)
            previous = span
    if unknown or ''.join(rendered) != content or cursor != len(content):
        issues.append(_issue(
            'text.note_mismatch', 'warning',
            '完整原文与非延音音符文本序列不一致；不建立映射。',
            text_unit_count=len(content), note_count=len(notes)))
        return [None] * len(notes), False
    return positions, True


def _word_span_rows(raw_word_spans, original, units, unit_notes, issues):
    if not isinstance(raw_word_spans, (list, tuple)):
        raise ValueError('word_spans必须是列表或元组。')
    words = []
    earlier = []
    for index, raw in enumerate(raw_word_spans):
        prefix = 'word_spans[' + str(index) + ']'
        if not isinstance(raw, dict):
            raise ValueError(prefix + '必须是对象。')
        span = raw.get('span')
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) is not int for value in span)):
            raise ValueError(prefix + '.span必须是两个整数。')
        start, end = span
        if start < 0 or end <= start or end > len(original):
            raise ValueError(prefix + '.span超出原文范围或为空。')
        source = raw.get('source')
        if not isinstance(source, str) or not source.strip():
            raise ValueError(prefix + '.source必须是非空字符串。')
        selected = [
            (compact_index, unit)
            for compact_index, unit in enumerate(units)
            if start <= unit['span'][0] and unit['span'][1] <= end
        ]
        unit_ids = [unit['id'] for _, unit in selected]
        note_ids = []
        for compact_index, _ in selected:
            for note_id in unit_notes.get(compact_index, []):
                if note_id not in note_ids:
                    note_ids.append(note_id)
        content = {
            'text': original[start:end],
            'span': [start, end],
            'source': source,
            'unit_ids': unit_ids,
            'note_ids': note_ids,
        }
        if not selected:
            issues.append(_issue(
                'word.no_text', 'warning',
                '显式词跨度未包含原文文本单元。',
                word_index=index, span=[start, end]))
        for old_start, old_end, old_index in earlier:
            if start < old_end and old_start < end:
                issues.append(_issue(
                    'word.span_overlap', 'warning',
                    '显式词跨度互相重叠。',
                    first_index=old_index, second_index=index))
        earlier.append((start, end, index))
        words.append({
            'id': _stable_id('word', content, index),
            **content,
        })
    return words


def _fraction(part, whole):
    return part / whole if whole else 0.0


def _coverage(units, unit_notes, notes, syllables, words, status):
    mapped_units = sum(1 for index in range(len(units))
                       if unit_notes.get(index))
    mapped_notes = sum(1 for note in notes if note['text_unit_ids'])
    linked_syllables = {
        syllable_id for note in notes for syllable_id in note['syllable_ids']
    }
    mapped_syllables = sum(
        1 for syllable in syllables if syllable['id'] in linked_syllables)
    mapped_note_syllables = sum(1 for note in notes if note['syllable_ids'])
    return {
        'status': status,
        'text_units': {
            'total': len(units),
            'mapped': mapped_units,
            'fraction': _fraction(mapped_units, len(units)),
        },
        'notes': {
            'total': len(notes),
            'text_mapped': mapped_notes,
            'text_fraction': _fraction(mapped_notes, len(notes)),
            'syllable_mapped': mapped_note_syllables,
            'syllable_fraction': _fraction(mapped_note_syllables, len(notes)),
        },
        'syllables': {
            'total': len(syllables),
            'note_mapped': mapped_syllables,
            'fraction': _fraction(mapped_syllables, len(syllables)),
        },
        'words': {
            'total': len(words),
            'text_mapped': sum(1 for word in words if word['unit_ids']),
            'note_mapped': sum(1 for word in words if word['note_ids']),
        },
    }


def build_evidence(original_text, language, notes, syllables=(), *,
                   voice='vocals', provenance=None, word_spans=(), alignment=None) -> dict:
    """Build deterministic evidence without changing any caller input.

    ``text_units`` contain nonempty Unicode Letter/Number/Mark code points from
    ``original_text``. Punctuation and whitespace remain in the original and in
    spans. Text-to-note mapping requires an exact complete sequence after
    excluding sustain notes. ``'-'`` and USTX ``'+~'`` inherit the immediately
    preceding resolved unit; a lone ``'+'`` remains unknown.
    """
    if not isinstance(original_text, str):
        raise ValueError('original_text必须是字符串。')
    if not isinstance(language, str) or not language:
        raise ValueError('language必须是非空字符串。')
    if not isinstance(voice, str) or not voice:
        raise ValueError('voice必须是非空字符串。')
    if provenance is None:
        provenance = {}
    if not isinstance(provenance, dict):
        raise ValueError('provenance必须是对象。')
    try:
        provenance = copy.deepcopy(provenance)
        _canonical(provenance)
    except (TypeError, ValueError) as error:
        raise ValueError('provenance必须只包含有限 JSON 值。') from error

    issues = []
    units = _text_units(original_text)
    analyzed = _analysis_text(original_text)
    normalized_notes = _normalize_notes(notes)
    normalized_syllables = _normalize_syllables(syllables)
    _link_time(normalized_notes, normalized_syllables, issues)
    if not analyzed:
        issues.append(_issue(
            'text.empty', 'warning', '原文没有可用于对应的歌词文本。'))
    if not normalized_notes:
        issues.append(_issue(
            'notes.empty', 'warning', '音符轨为空。'))

    if alignment is None:
        positions, complete = _map_text_to_notes(analyzed, normalized_notes, issues)
    else:
        # Explicit opt-in; old strict callers keep their exact prior behavior.
        # Validate against the original raw score, not an inferred phonetic text.
        from context_alignment import validate_alignment
        checked = validate_alignment(alignment, original_text, language, notes)
        positions = []
        for span in checked['note_spans']:
            positions.append(None if span is None else [
                index for index, unit in enumerate(units)
                if span[0] <= unit['span'][0] and unit['span'][1] <= span[1]])
        used = {index for span in positions if span for index in span}
        complete = bool(units and positions and all(positions) and len(used) == len(units))
        provenance['alignment'] = {'alignment_id': checked['alignment_id'],
            'method': 'explicit_partial_sequence_alignment',
            'reading_granularity': 'declared_source_span_may_cover_multiple_characters',
            'acoustic_verification': False}
        for index, span in enumerate(positions):
            if not span:
                issues.append(_issue('note.unresolved_alignment', 'warning',
                    '该音符的原词位置未确认，保留为未知。', note_id=normalized_notes[index]['id']))
    unit_ids = [unit['id'] for unit in units]
    unit_notes = {index: [] for index in range(len(units))}
    for note, span in zip(normalized_notes, positions):
        if span:
            note['text_unit_ids'] = [unit_ids[index] for index in span]
            for index in span:
                unit_notes[index].append(note['id'])

    words = _word_span_rows(
        word_spans, original_text, units, unit_notes, issues)

    if complete:
        status = 'complete'
    elif any(unit_notes.values()):
        status = 'partial'
    elif not analyzed or not normalized_notes:
        status = 'missing'
    else:
        status = 'unknown'

    result = {
        'schema_version': SCHEMA_VERSION,
        'kind': KIND,
        'evidence_id': '',
        'text': {'original': original_text, 'language': language},
        'voice': voice,
        'notes': normalized_notes,
        'text_units': [
            {
                'id': unit['id'],
                'text': unit['text'],
                'span': list(unit['span']),
                'line_index': unit['line_index'],
            }
            for unit in units
        ],
        'words': words,
        'syllables': normalized_syllables,
        'issues': issues,
        'coverage': _coverage(
            units, unit_notes, normalized_notes, normalized_syllables,
            words, status),
        'provenance': provenance,
    }
    unsigned = copy.deepcopy(result)
    unsigned.pop('evidence_id')
    result['evidence_id'] = hashlib.sha256(
        _canonical(unsigned).encode('utf-8')).hexdigest()
    return result
