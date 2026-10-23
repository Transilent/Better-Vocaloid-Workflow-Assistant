"""Synthetic, deterministic tests for lyric-to-note context evidence."""
from pathlib import Path
import copy
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from context_evidence import build_evidence, verify_evidence_fingerprint


def note(lyric, start, end, pitch=60):
    return {'lyric': lyric, 'start': start, 'end': end, 'pitch': pitch}


def codes(evidence):
    return {item['code'] for item in evidence['issues']}


# Original punctuation/whitespace stays verbatim while spans keep its offsets.
text = '你，好\n再 见'
notes = [note('你', 0, 1), note('好', 1, 2), note('再', 2, 3, 62),
         note('见', 3, 4, 64)]
syllables = [{'lyric': '你', 'start': .1, 'end': .9},
             {'lyric': '好', 'start': 1.1, 'end': 1.9}]
provenance = {'ustx_sha256': 'a' * 64, 'quality_sha256': 'b' * 64,
              'quality_path': 'midi/lead/lead.quality.json'}
before_notes = copy.deepcopy(notes)
before_syllables = copy.deepcopy(syllables)
word_spans = [{'span': [2, 3], 'source': 'manual'}]
before_word_spans = copy.deepcopy(word_spans)
before_provenance = copy.deepcopy(provenance)
evidence = build_evidence(text, 'zh', notes, syllables, voice='lead',
                          provenance=provenance, word_spans=word_spans)
assert evidence['schema_version'] == 1
assert evidence['kind'] == 'vwa.context-evidence'
assert evidence['text'] == {'original': text, 'language': 'zh'}
assert evidence['voice'] == 'lead'
assert set(evidence) == {'schema_version', 'kind', 'evidence_id', 'text', 'voice',
                         'notes', 'text_units', 'words', 'syllables', 'issues',
                         'coverage', 'provenance'}
assert [unit['text'] for unit in evidence['text_units']] == list('你好再见')
assert [unit['span'] for unit in evidence['text_units']] == [
    [0, 1], [2, 3], [4, 5], [6, 7]]
assert [unit['line_index'] for unit in evidence['text_units']] == [0, 0, 1, 1]
assert evidence['coverage']['status'] == 'complete'
assert evidence['notes'][0]['syllable_ids'] == [evidence['syllables'][0]['id']]
assert evidence['notes'][1]['syllable_ids'] == [evidence['syllables'][1]['id']]
assert evidence['notes'][2]['syllable_ids'] == evidence['notes'][3]['syllable_ids'] == []
assert evidence['words'] == [{
    'id': evidence['words'][0]['id'], 'text': '好', 'span': [2, 3],
    'source': 'manual', 'unit_ids': [evidence['text_units'][1]['id']],
    'note_ids': [evidence['notes'][1]['id']]}]
assert notes == before_notes and syllables == before_syllables
assert word_spans == before_word_spans and provenance == before_provenance
json.dumps(evidence, ensure_ascii=False, allow_nan=False)
assert len(evidence['evidence_id']) == 64

# The requested punctuation case: raw span [2,3] must bind compact unit 1.
word_case = build_evidence('你，好', 'zh', [note('你', 0, 1), note('好', 1, 2)],
                           word_spans=[{'span': [2, 3], 'source': 'manual'}])
assert word_case['words'][0]['unit_ids'] == [word_case['text_units'][1]['id']]
assert word_case['words'][0]['note_ids'] == [word_case['notes'][1]['id']]
assert word_case['coverage']['words']['note_mapped'] == 1

# Explicit words are absent unless supplied; overlapping spans are reported.
assert build_evidence('你', 'zh', [note('你', 0, 1)])['words'] == []
overlap = build_evidence('你，好', 'zh', [note('你', 0, 1), note('好', 1, 2)],
                         word_spans=[{'span': [0, 1], 'source': 'manual'},
                                     {'span': [0, 2], 'source': 'manual'}])
assert 'word.span_overlap' in codes(overlap)
punct_word = build_evidence('你，好', 'zh', [note('你', 0, 1), note('好', 1, 2)],
                            word_spans=[{'span': [1, 2], 'source': 'manual'}])
assert punct_word['words'][0]['unit_ids'] == []
assert 'word.no_text' in codes(punct_word)

# Duplicate lyrics use the complete ordered sequence, never a fuzzy search.
repeat = build_evidence('啊啊', 'zh', [note('啊', 0, 1), note('啊', 1, 2)])
assert repeat['coverage']['status'] == 'complete'
assert repeat['text_units'][0]['id'] != repeat['text_units'][1]['id']
assert repeat['notes'][0]['text_unit_ids'] == [repeat['text_units'][0]['id']]
assert repeat['notes'][1]['text_unit_ids'] == [repeat['text_units'][1]['id']]

# Both MIDI '-' and USTX '+~' inherit one known preceding text unit.
sustain = build_evidence('中', 'zh', [note('中', 0, 1), note('-', 1, 2),
                                      note('+~', 2, 3)])
assert sustain['coverage']['status'] == 'complete'
assert all(item['text_unit_ids'] == [sustain['text_units'][0]['id']]
           for item in sustain['notes'])
assert sustain['coverage']['text_units']['mapped'] == 1

# A lone '+' is unknown and prevents a false complete mapping.
plus = build_evidence('中', 'zh', [note('中', 0, 1), note('+', 1, 2)])
assert plus['coverage']['status'] == 'unknown'
assert 'note.ambiguous_plus' in codes(plus)
assert all(not item['text_unit_ids'] for item in plus['notes'])

# Romanization cannot be converted back into Hanzi.
roman = build_evidence('你好', 'zh', [note('ni', 0, 1), note('hao', 1, 2)])
assert roman['coverage']['status'] == 'unknown'
assert 'text.note_mismatch' in codes(roman)

# Empty text/notes are missing, including the both-empty case.
empty = build_evidence('', 'zh', [])
assert empty['coverage']['status'] == 'missing'
assert 'text.empty' in codes(empty) and 'notes.empty' in codes(empty)
assert build_evidence('你', 'zh', [])['coverage']['status'] == 'missing'
assert build_evidence('，\n', 'zh', [note('你', 0, 1)])['coverage']['status'] == 'missing'

# Time overlap is evidence only; it never supplies or changes lyric text.
overlap_time = build_evidence(
    '你你', 'zh', [note('你', 0, 1), note('你', .5, 1.5)],
    syllables=[{'lyric': '你', 'start': .2, 'end': .7},
               {'lyric': '你', 'start': .8, 'end': 1.2},
               {'lyric': '好', 'start': 2, 'end': 3}])
assert overlap_time['coverage']['status'] == 'complete'
assert 'note.time_overlap' in codes(overlap_time)
assert 'note.multiple_syllables' in codes(overlap_time)
assert 'syllable.multiple_notes' in codes(overlap_time)
assert 'syllable.unmatched' in codes(overlap_time)
assert all(item['lyric'] in ('你',) for item in overlap_time['notes'])

# Invalid numeric/time inputs fail clearly, including bools and non-finite data.
for bad in (float('nan'), float('inf'), -float('inf'), True, '1'):
    try:
        build_evidence('你', 'zh', [note('你', bad, 1)])
    except ValueError:
        pass
    else:
        raise AssertionError(('invalid start accepted', bad))
try:
    build_evidence('你', 'zh', [note('你', 1, 1)])
except ValueError:
    pass
else:
    raise AssertionError('zero duration accepted')
try:
    build_evidence('你', 'zh', [note('你', 1, .5)])
except ValueError:
    pass
else:
    raise AssertionError('reversed note accepted')
try:
    build_evidence('你', 'zh', [note('你', 0, 1, True)])
except ValueError:
    pass
else:
    raise AssertionError('bool pitch accepted')
try:
    build_evidence('你', 'zh', [note('你', 0, 1)],
                   syllables=[{'lyric': '你', 'start': 0, 'end': 0}])
except ValueError:
    pass
else:
    raise AssertionError('zero-duration syllable accepted')

# Descending note starts retain time data but do not confirm text order.
descending = build_evidence('你好', 'zh', [note('你', 1, 2), note('好', 0, 1)])
assert descending['coverage']['status'] == 'unknown'
assert 'note.start_out_of_order' in codes(descending)
assert all(not item['text_unit_ids'] for item in descending['notes'])

# Stable IDs include content/source facts and contain no timestamp.
again = build_evidence(text, 'zh', notes, syllables, voice='lead',
                       provenance=provenance, word_spans=word_spans)
assert again == evidence
changed = build_evidence(text, 'zh', notes, syllables, voice='lead',
                         provenance={**provenance, 'quality_sha256': 'c' * 64},
                         word_spans=word_spans)
assert changed['evidence_id'] != evidence['evidence_id']
assert changed['notes'][0]['id'] == evidence['notes'][0]['id']

# Spans count Unicode code points, not UTF-16 code units.
emoji = build_evidence('😀你', 'zh', [])
assert emoji['text_units'][0]['span'] == [1, 2]

# Editing content cannot keep an old revision valid at optional planner APIs.
snapshot = copy.deepcopy(evidence)
assert verify_evidence_fingerprint(evidence) == evidence['evidence_id']
assert evidence == snapshot
for field in ('text', 'notes'):
    forged = copy.deepcopy(evidence)
    if field == 'text':
        forged['text']['original'] = '再见'
    else:
        forged['notes'][0]['pitch'] += 1
    try:
        verify_evidence_fingerprint(forged)
    except ValueError:
        pass
    else:
        raise AssertionError('Edited content retained old evidence identity')
malformed = copy.deepcopy(evidence)
malformed['schema_version'] = True
try:
    verify_evidence_fingerprint(malformed)
except ValueError:
    pass
else:
    raise AssertionError('Boolean evidence schema accepted')

print('PASS: context evidence mappings, sustains, words, time overlap, validation, content fingerprint and stable IDs')
