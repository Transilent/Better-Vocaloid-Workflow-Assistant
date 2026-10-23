"""Literal positions and conservative surface cues; no semantic inference."""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import hashlib
import json
import re

from context_evidence import verify_evidence_fingerprint

SCHEMA_VERSION = 1
KIND = 'vwa.context-structure'
LONG_NOTE_SECONDS = .8
REST_SECONDS = .15
_BOUNDARIES = frozenset('，,。.!！？?；;：:\n\r')
_CONTINUATIONS = frozenset(('-', '+~'))


def _id(prefix, evidence_id, value):
    data = json.dumps([evidence_id, value], ensure_ascii=False, sort_keys=True)
    return prefix + '_' + hashlib.sha256(data.encode('utf-8')).hexdigest()[:20]


def _trim(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _position(index, count):
    if count == 1:
        return 'single'
    return 'start' if index == 0 else 'end' if index == count - 1 else 'middle'


def _within(span, bounds):
    return bounds[0] <= span[0] and span[1] <= bounds[1]


def _overlap(left, right):
    return left[0] < right[1] and right[0] < left[1]


def analyze_structure(evidence):
    """Return deterministic facts/hints from current content-hashed evidence.

    Spans count original Unicode code points. Rest intervals are score facts;
    cue matches have unknown semantic scope. Partial word inputs never become
    an inferred complete segmentation.
    """
    evidence_id = verify_evidence_fingerprint(evidence)
    text = evidence['text']['original']
    language = evidence['text']['language']
    voice = evidence['voice']
    units = evidence['text_units']
    notes = sorted(evidence['notes'], key=lambda row: (row['start'], row['end'], row['id']))

    def has_units(span):
        return any(_within(unit['span'], span) for unit in units)

    def linked(span):
        wanted = {unit['id'] for unit in units if _within(unit['span'], span)}
        return [note['id'] for note in notes if wanted.intersection(note['text_unit_ids'])]

    stanzas = []
    cursor = 0
    for end, resume in [(match.start(), match.end()) for match in re.finditer(r'\n[ \t\r]*\n', text)] + [(len(text), len(text))]:
        span = list(_trim(text, cursor, end))
        if has_units(span):
            stanzas.append({'id': _id('stanza', evidence_id, span), 'span': span,
                            'text': text[span[0]:span[1]], 'phrase_ids': []})
        cursor = resume

    phrases = []
    cursor = 0
    for index in list(i + 1 for i, char in enumerate(text) if char in _BOUNDARIES) + [len(text)]:
        span = list(_trim(text, cursor, index))
        cursor = index
        if not has_units(span):
            continue
        stanza = next((row for row in stanzas if _overlap(row['span'], span)), None)
        phrase = {'id': _id('phrase', evidence_id, span), 'span': span,
                  'text': text[span[0]:span[1]], 'note_ids': linked(span),
                  'stanza_id': stanza['id'] if stanza else None, 'features': [],
                  'repeated_occurrence': 1, 'repeat_count': 1}
        phrases.append(phrase)
        if stanza:
            stanza['phrase_ids'].append(phrase['id'])
    repeated = {}
    for phrase in phrases:
        key = ''.join(unit['text'] for unit in units if _within(unit['span'], phrase['span']))
        repeated.setdefault(key, []).append(phrase)
    for group in repeated.values():
        for index, phrase in enumerate(group):
            phrase['repeated_occurrence'] = index + 1
            phrase['repeat_count'] = len(group)

    words = []
    for word in evidence['words']:
        memberships = [phrase for phrase in phrases if _overlap(word['span'], phrase['span'])]
        position = 'unknown'
        if len(memberships) > 1:
            position = 'cross_phrase'
        elif memberships:
            bounds = [unit['span'] for unit in units if _within(unit['span'], memberships[0]['span'])]
            if bounds:
                at_start = word['span'][0] <= bounds[0][0]
                at_end = word['span'][1] >= bounds[-1][1]
                position = 'single' if at_start and at_end else 'start' if at_start else 'end' if at_end else 'middle'
        words.append({'word_id': word['id'], 'span': list(word['span']), 'text': word['text'],
                      'note_ids': list(word['note_ids']),
                      'phrase_ids': [row['id'] for row in memberships], 'position': position})

    structural_notes = []
    previous_release = 0.
    for index, note in enumerate(notes):
        memberships = [phrase for phrase in phrases if note['id'] in phrase['note_ids']]
        phrase_position = 'unknown'
        if len(memberships) > 1:
            phrase_position = 'cross_phrase'
        elif memberships:
            ids = memberships[0]['note_ids']
            phrase_position = _position(ids.index(note['id']), len(ids))
        word_rows = [word for word in words if note['id'] in word['note_ids']]
        word_positions = [{'word_id': word['word_id'],
                           'position': _position(word['note_ids'].index(note['id']), len(word['note_ids']))}
                          for word in word_rows]
        before = max(0., note['start'] - previous_release)
        previous_release = max(previous_release, note['end'])
        after = max(0., notes[index + 1]['start'] - previous_release) if index + 1 < len(notes) else None
        features = set()
        if phrase_position in ('single', 'start', 'cross_phrase'):
            features.add('phrase_start')
        if phrase_position in ('single', 'end', 'cross_phrase'):
            features.add('phrase_end')
        for row in word_positions:
            if row['position'] in ('single', 'start'):
                features.add('word_start')
            if row['position'] in ('single', 'end'):
                features.add('word_end')
        if note['end'] - note['start'] >= LONG_NOTE_SECONDS:
            features.add('long_note')
        if before >= REST_SECONDS:
            features.add('rest_before')
        if after is not None and after >= REST_SECONDS:
            features.add('rest_after')
        structural_notes.append({
            'note_id': note['id'], 'phrase_ids': [row['id'] for row in memberships],
            'word_ids': [row['word_id'] for row in word_rows],
            'phrase_position': phrase_position, 'word_positions': word_positions,
            'duration': note['end'] - note['start'], 'rest_before': before,
            'rest_after': after, 'features': sorted(features),
            'continuation': note['lyric'] in _CONTINUATIONS})
    structural_by_id = {row['note_id']: row for row in structural_notes}

    cues = []
    patterns = {
        'zh': {
            'negation': ('不是', '没有', '不能', '不想', '不要', '不愿', '别再', '别走', '别哭'),
            'contrast': ('但是', '可是', '然而', '不过'),
            'addressee': ('你', '您'), 'conditional': ('如果', '假如', '倘若')},
        'ja': {
            'negation': ('ない', 'ません'), 'contrast': ('でも', 'けれど', 'しかし'),
            'addressee': ('君', 'あなた'), 'conditional': ('もし', 'なら', 'たら')}}
    for kind, tokens in patterns.get(language, {}).items():
        for token in tokens:
            for found in re.finditer(re.escape(token), text):
                span = [found.start(), found.end()]
                phrase = next((row for row in phrases if _within(span, row['span'])), None)
                if phrase is None:
                    continue
                exact_word = any(word['span'] == span for word in words)
                at_phrase_start = span[0] == phrase['span'][0]
                at_phrase_end = not any(unit['span'][0] >= span[1] and _within(unit['span'], phrase['span']) for unit in units)
                if kind == 'addressee' and not (exact_word or at_phrase_start or at_phrase_end):
                    continue
                if language == 'ja':
                    if token == 'もし' and (text[span[0]:span[1] + 2] == 'もしもし'
                                          or text[max(0, span[0] - 2):span[1]] == 'もしもし'):
                        continue
                    if kind == 'contrast' and not (exact_word or at_phrase_start):
                        continue
                    if token in ('なら', 'たら') and not (exact_word or at_phrase_end):
                        continue
                    if token == 'ない' and text[span[1]:span[1] + 2] in ('しょ', 'しも'):
                        continue
                    if token == '君' and span[1] < len(text) and '\u4e00' <= text[span[1]] <= '\u9fff' and not exact_word:
                        continue
                cues.append({'id': _id('cue', evidence_id, [kind, span]), 'kind': kind,
                             'span': span, 'text': text[span[0]:span[1]],
                             'phrase_id': phrase['id'], 'note_ids': linked(span),
                             'status': 'surface_cue', 'scope': 'unknown'})
    for index, char in enumerate(text):
        if char not in '?？':
            continue
        span = [index, index + 1]
        phrase = next((row for row in phrases if _within(span, row['span'])), None)
        if phrase:
            cues.append({'id': _id('cue', evidence_id, ['question', span]), 'kind': 'question',
                         'span': span, 'text': char, 'phrase_id': phrase['id'], 'note_ids': [],
                         'status': 'surface_cue', 'scope': 'unknown'})
    cues.sort(key=lambda row: (row['span'], row['kind']))

    for phrase in phrases:
        features = set()
        for note_id in phrase['note_ids']:
            features.update(structural_by_id[note_id]['features'])
        if phrase['repeat_count'] > 1:
            features.add('repeat')
        features.update(cue['kind'] for cue in cues if cue['phrase_id'] == phrase['id'])
        phrase['features'] = sorted(features)
    covered_words = {unit_id for word in evidence['words'] for unit_id in word['unit_ids']}
    unknowns = [{'code': 'semantic_scope_unknown',
                 'message': 'Surface cues do not establish negation scope, emotion, speaker, addressee identity, or stanza role.'},
                {'code': 'surface_rule_coverage_partial',
                 'message': 'Cue rules are conservative lexical hints, not a tokenizer or complete grammar.'}]
    if len(covered_words) < len(units):
        unknowns.append({'code': 'word_segmentation_unavailable_or_partial',
                         'message': 'Only explicitly supplied spans are words; missing spans stay unsegmented.'})
    if evidence['coverage']['status'] != 'complete':
        unknowns.append({'code': 'text_note_alignment_unavailable',
                         'message': 'No guessed Hanzi/Kanji or semantic note association was created.'})
    return {'schema_version': SCHEMA_VERSION, 'kind': KIND, 'evidence_id': evidence_id,
            'voice': voice, 'language': language, 'phrases': phrases, 'stanzas': stanzas,
            'words': words, 'notes': structural_notes, 'cues': cues, 'unknowns': unknowns,
            'metadata': {'offset_unit': 'unicode_code_point',
                         'long_note_seconds': LONG_NOTE_SECONDS, 'rest_seconds': REST_SECONDS,
                         'rest_basis': 'score_zero_and_union_of_preceding_note_releases',
                         'final_rest': 'unknown_without_score_end',
                         'repeat_basis': 'exact_singable_text_ignoring_punctuation_and_whitespace',
                         'segmentation': 'explicit_word_spans_only', 'semantic_inference': False}}


__all__ = ('analyze_structure',)

