"""Pure functional tests for conservative lyric-to-note alignment."""
from pathlib import Path
import copy
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from context_alignment import KIND, align_lyrics, validate_alignment


def note(lyric, start, pitch=60):
    return {'lyric': lyric, 'start': start, 'end': start + .5,
            'pitch': pitch}


def seq(text, language='zh', reading_units=None):
    notes = [note(char, index) for index, char in enumerate(text)]
    return align_lyrics(text, language, notes,
                        reading_units=reading_units)


def spans(alignment):
    return alignment['note_spans']


# Exact Chinese Unicode positions retain punctuation and whitespace offsets.
text = '你，好\n再 见'
notes = [note('你', 0), note('好', 1, 62), note('再', 2),
         note('见', 3, 64)]
before = copy.deepcopy(notes)
aligned = align_lyrics(text, 'zh', notes)
assert notes == before
assert aligned['schema_version'] == 1 and aligned['kind'] == KIND
assert aligned['source']['notes'] == before
assert aligned['source']['language'] == 'zh'
assert len(aligned['source']['text_sha256']) == 64
assert len(aligned['source']['notes_sha256']) == 64
assert len(aligned['alignment_id']) == 64
assert spans(aligned) == [[0, 1], [2, 3], [4, 5], [6, 7]]
assert aligned['coverage']['status'] == 'complete'
assert aligned['unknowns'] == []
assert json.loads(json.dumps(aligned, ensure_ascii=False)) == aligned

# A single substitution is local and does not erase the whole song.
typo = align_lyrics('你好世界', 'zh',
                    [note('你', 0), note('好', 1),
                     note('事', 2), note('界', 3)])
assert spans(typo) == [[0, 1], [1, 2], None, [3, 4]]
assert typo['coverage']['status'] == 'partial'
assert typo['unknowns'][0]['note_index'] == 2
assert typo['algorithm']['edit_cost'] == 1

# An exact repeated occurrence with several equal locations is unknown.
for text, notes_value in (
        ('啊啊啊', [note('啊', 0)]),
        ('abab', [note('a', 0), note('b', 1)]),
        ('abcabc', [note('a', 0), note('b', 1), note('c', 2)])):
    ambiguous = align_lyrics(text, 'test', notes_value)
    assert spans(ambiguous) == [None] * len(notes_value)

# A complete sequence disambiguates repeats only when the whole sequence
# leaves one optimum; isolated repeated anchors must not be greedily chosen.
repeat_complete = seq('啊啊')
assert spans(repeat_complete) == [[0, 1], [1, 2]]
# A repeated block whose complete sequence is present is unambiguous.
assert spans(seq('abab')) == [[0, 1], [1, 2], [2, 3], [3, 4]]
missing_block = align_lyrics(
    'abcabc', 'test',
    [note('a', 0), note('b', 1), note('c', 2), note('-', 3)])
assert all(span is None for span in spans(missing_block))

# Oracle regression: a later exact match can be tied with an earlier unknown
# note while the only source token is assigned to the later candidate.
case11 = align_lyrics(
    '丙', 'zh',
    [note('乙', 0), note('丙', 1), note('-', 2),
     note('丙', 3), note('-', 4)])
# Both matching source positions are supported by two equal optima, so even
# the later exact note is not safe to confirm. Its sustain cannot inherit an
# unresolved span.
assert spans(case11) == [None, None, None, None, None]
assert case11['note_token_indexes'] == [None, None, None, None, None]

# A word can legitimately be represented by two mora tokens on one source
# span. If only the second token is present, the first is not silently added:
# the expected token log exposes that unknown word-initial position.
partial_word = align_lyrics(
    '僕', 'ja', [note('-', 0), note('ku', 1)],
    reading_units=[{'span': [0, 1], 'text': '僕',
                    'tokens': ['bo', 'ku'], 'source': 'NJD'}])
assert spans(partial_word) == [None, [0, 1]]
assert partial_word['note_token_indexes'] == [None, 1]
assert partial_word['coverage']['status'] == 'partial'

# Sustains inherit an immediately preceding resolved span, but a fixed note
# can never consume source text. '你再好' therefore leaves 再 between anchors.
fixed = align_lyrics('你再好', 'zh',
                     [note('你', 0), note('-', 1), note('好', 2)])
assert spans(fixed) == [[0, 1], [0, 1], [2, 3]]
assert fixed['note_bases'] == ['matched_text', 'sustained', 'matched_text']
orphan = align_lyrics('你再', 'zh', [note('-', 0), note('你', 1)])
assert spans(orphan)[0] is None
assert orphan['note_bases'][0] == 'orphan_sustain'
plus = align_lyrics('你', 'zh', [note('你', 0), note('+', 1)])
assert spans(plus)[1] is None
assert plus['note_bases'][1] == 'ambiguous_plus'

# Explicit reading units expand every mora while retaining the entire word
# source span. Han/kana placement inside a multi-token word is not invented.
reading_text = 'ここです。僕'
reading_units = [
    {'span': [0, 2], 'text': 'ここ', 'tokens': ['ko', 'ko'],
     'source': 'pyopenjtalk:NJD'},
    {'span': [5, 6], 'text': '僕', 'tokens': ['bo', 'ku'],
     'source': 'pyopenjtalk:NJD'},
]
reading_notes = [note('ko', 0), note('ko', 1),
                 note('bo', 2), note('ku', 3)]
reading_before = copy.deepcopy(reading_units)
japanese = align_lyrics(reading_text, 'ja', reading_notes,
                        reading_units=reading_units)
assert reading_units == reading_before
assert spans(japanese) == [[0, 2], [0, 2], [5, 6], [5, 6]]
assert japanese['reading_units'] == reading_units
assert japanese['coverage']['text_units']['total'] == 5
assert japanese['coverage']['text_units']['mapped'] == 3
assert japanese['coverage']['text_units']['fraction'] == .6
assert japanese['coverage']['status'] == 'partial'
assert japanese['note_token_indexes'] == [0, 1, 4, 5]
tokens = japanese['expected_tokens']
assert [token['index'] for token in tokens] == list(range(6))
assert [token['token'] for token in tokens] == [
    'ko', 'ko', 'で', 'す', 'bo', 'ku']
assert [token['span'] for token in tokens] == [
    [0, 2], [0, 2], [2, 3], [3, 4], [5, 6], [5, 6]]
assert [token['source'] for token in tokens] == [
    'reading', 'reading', 'text', 'text', 'reading', 'reading']
assert [token['word_token_index'] for token in tokens] == [0, 1, 0, 0, 0, 1]
assert [tokens[index]['token'] for index in
        japanese['note_token_indexes']] == ['ko', 'ko', 'bo', 'ku']
assert japanese['metadata']['word_span_is_character_reading'] is False
assert 'missing word-initial mora' in japanese['metadata']['note']

# Uncovered text falls back once as one codepoint token, without duplicating
# a source character already represented by a reading unit.
mixed_text = 'ここでる'
mixed = align_lyrics(
    mixed_text, 'ja', [note('ko', 0), note('ko', 1), note('de', 2),
                       note('ru', 3)],
    reading_units=[{'span': [0, 2], 'text': 'ここ',
                    'tokens': ['ko', 'ko'], 'source': 'NJD'}])
assert spans(mixed) == [[0, 2], [0, 2], None, None]
assert mixed['coverage']['text_units'] == {
    'total': 4, 'mapped': 2, 'fraction': .5,
    'basis': 'original_codepoint_set_covered_by_note_spans'}
assert mixed['coverage']['status'] == 'partial'
# Documented romanization aliases normalize; arbitrary sound-alikes do not.
alias = align_lyrics(
    'しちつふじ', 'ja',
    [note('si', 0), note('ti', 1), note('tu', 2),
     note('fu', 3), note('ji', 4)],
    reading_units=[
        {'span': [0, 1], 'text': 'し', 'tokens': ['shi'], 'source': 'NJD'},
        {'span': [1, 2], 'text': 'ち', 'tokens': ['chi'], 'source': 'NJD'},
        {'span': [2, 3], 'text': 'つ', 'tokens': ['tsu'], 'source': 'NJD'},
        {'span': [3, 4], 'text': 'ふ', 'tokens': ['hu'], 'source': 'NJD'},
        {'span': [4, 5], 'text': 'じ', 'tokens': ['zi'], 'source': 'NJD'},
    ])
assert spans(alias) == [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]]
sound_alike = align_lyrics('し', 'ja', [note('sa', 0)],
                           reading_units=[{'span': [0, 1], 'text': 'し',
                                           'tokens': ['shi'],
                                           'source': 'NJD'}])
assert spans(sound_alike) == [None]

# Reading records and caller inputs must not be forged or overlap.
for bad in (
    [{'span': [0, 2], 'text': 'こ', 'tokens': ['ko'], 'source': 'NJD'}],
    [{'span': [0, 1], 'text': 'こ', 'tokens': ['ko'], 'source': ''}],
    [{'span': [0, 2], 'text': 'ここ', 'tokens': ['ko'], 'source': 'NJD'},
     {'span': [1, 2], 'text': 'こ', 'tokens': ['ko'], 'source': 'NJD'}],
    [{'span': [0, 1], 'text': 'こ', 'tokens': [],
      'source': 'NJD'}],
):
    try:
        align_lyrics('ここ', 'ja', [note('ko', 0)], reading_units=bad)
    except ValueError:
        pass
    else:
        raise AssertionError(('invalid reading accepted', bad))

# Argument validation covers finite timings, ordering, and duration.
for bad in (
    [{'lyric': 'a', 'start': float('nan'), 'end': 1, 'pitch': 60}],
    [{'lyric': 'a', 'start': True, 'end': 1, 'pitch': 60}],
    [{'lyric': 'a', 'start': 1, 'end': 1, 'pitch': 60}],
    [note('a', 1), note('b', 0)],
):
    try:
        align_lyrics('ab', 'x', bad)
    except ValueError:
        pass
    else:
        raise AssertionError(('invalid notes accepted', bad))

# Empty inputs return a normal, auditable result.
empty = align_lyrics('', 'zh', [])
assert empty['coverage']['status'] == 'missing'
assert spans(empty) == []
empty_text = align_lyrics('', 'zh', [note('+', 0)])
assert spans(empty_text) == [None]

# Validation recomputes against the supplied text/language/notes, rejects
# edited content and stale input, and returns an independent deep copy.
clean_text = '你好世界'
clean_notes = [note('你', 0), note('好', 1), note('世', 2), note('界', 3)]
current_aligned = align_lyrics(clean_text, 'zh', clean_notes)
snapshot = copy.deepcopy(current_aligned)
checked = validate_alignment(current_aligned, clean_text, 'zh', clean_notes)
assert checked == snapshot and checked is not current_aligned
assert checked['source']['notes'] is not current_aligned['source']['notes']

# A released BVWA record is still authorized by its legacy-kind digest.
legacy_aligned = copy.deepcopy(current_aligned)
legacy_aligned['kind'] = 'bvwa.context-alignment'
legacy_aligned.pop('alignment_id')
legacy_aligned['alignment_id'] = hashlib.sha256(json.dumps(
    legacy_aligned, ensure_ascii=False, sort_keys=True,
    separators=(',', ':')).encode('utf-8')).hexdigest()
legacy_snapshot = copy.deepcopy(legacy_aligned)
legacy_checked = validate_alignment(legacy_aligned, clean_text, 'zh', clean_notes)
assert legacy_checked == legacy_snapshot and legacy_checked is not legacy_aligned
assert legacy_checked['kind'] == 'bvwa.context-alignment'
assert legacy_aligned == legacy_snapshot
for mutate in (
        lambda value: value.update({'alignment_id': '0' * 64}),
        lambda value: value['note_spans'][0].__setitem__(1, 2),
        lambda value: value['source'].update({'language': 'ja'}),
        lambda value: value.update({'algorithm': {'method': 'greedy'}}),
):
    forged = copy.deepcopy(current_aligned)
    mutate(forged)
    try:
        validate_alignment(forged, clean_text, 'zh', clean_notes)
    except ValueError:
        pass
    else:
        raise AssertionError('edited alignment accepted')
for supplied_text, supplied_language, supplied_notes in (
        ('你好世界!', 'zh', clean_notes),
        (clean_text, 'ja', clean_notes),
        (clean_text, 'zh', [note('你', 0), note('好', 1), note('世', 2),
                                note('界', 3, 65)]),
):
    try:
        validate_alignment(current_aligned, supplied_text, supplied_language,
                           supplied_notes)
    except ValueError:
        pass
    else:
        raise AssertionError('stale alignment accepted')
for bad in (None, [], {}, {'schema_version': True, 'kind': KIND}):
    try:
        validate_alignment(bad, clean_text, 'zh', clean_notes)
    except ValueError:
        pass
    else:
        raise AssertionError('malformed alignment accepted')

# Same inputs yield the same audit identifiers; no timestamp or mutable input
# is embedded by reference.
current_text = '你，好\n再 见'
current_notes = [note('你', 0), note('好', 1, 62), note('再', 2),
                 note('见', 3, 64)]
again = align_lyrics(current_text, 'zh', current_notes)
assert again == aligned
assert again['alignment_id'] == aligned['alignment_id']
assert aligned['reading_units'] == []
assert typo['alignment_id'] != aligned['alignment_id']

print('PASS: context alignment local errors, all-optimum ties, reading words, sustains, G2P aliases and validation')