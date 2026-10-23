"""Build evidence-bound alternative context proposals without selecting or applying one.

The strict ``context_plan`` remains the executable advisory contract. This
module adds research provenance, stanza packets, semantic claims and conditional
method references around that plan. It never writes score or audio data.
"""
from __future__ import annotations


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value



import copy
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from context_plan import EVIDENCE_KIND, MAX_CANDIDATES, MAX_GESTURES, MAX_NOTES
from context_plan import make_plan, validate_plan

SCHEMA_VERSION = 1
PROPOSAL_KIND = 'vwa.context-proposal'
PROPOSAL_INPUT_KIND = 'vwa.context-proposal-input'
SEMANTIC_KIND = 'vwa.context-semantic-response'
STRUCTURE_KIND = 'vwa.context-structure'
MAX_GOAL_LENGTH = 1000
MAX_CANDIDATES_TOTAL = min(MAX_CANDIDATES, 32)
MAX_LOCAL_SCOPES = 6
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
_CONTINUATIONS = frozenset(('-', '+~'))

_LEVEL_DIRECTIONS = {
    'onset': ('clear_onset', '在所选词组首个实际演唱音符上试验清晰进入；不移动音符、音高或时值。'),
    'sustain': ('supported_sustain', '在所选词组内部的延续音上试验连贯支撑；保留原有旋律轮廓。'),
    'release': ('restrained_release', '在所选词组末尾及后续延音上试验克制收束；不生成数值参数。'),
}
_TAIL_DIRECTIONS = {
    'sustain': ('preserve_contour', '在所选词组尾部试验连贯维持；延音随所属单元保留，不按单字重复施力。'),
    'release': ('restrained_release', '在所选词组尾部试验有控制的释放；不生成数值参数。'),
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _text(value, path, limit, *, empty=False):
    if not isinstance(value, str):
        raise ValueError(path + ' must be text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(path + ' contains invalid Unicode') from error
    value = unicodedata.normalize('NFC', value).strip()
    if not value and not empty:
        raise ValueError(path + ' must not be empty')
    if len(value) > limit:
        raise ValueError(path + ' is too long')
    return value


def _evidence_ids(evidences):
    from context_evidence import verify_evidence_fingerprint
    if not isinstance(evidences, dict):
        raise ValueError('evidences must be an object')
    result = {}
    for voice, raw in evidences.items():
        if not isinstance(voice, str) or not voice or not isinstance(raw, dict):
            raise ValueError('evidence voices must be nonempty objects')
        if raw.get('schema_version') != 1 or canonical_kind(raw.get('kind')) != EVIDENCE_KIND:
            raise ValueError('evidence kind/schema is unsupported: ' + voice)
        if raw.get('voice') != voice:
            raise ValueError('evidence voice does not match its key: ' + voice)
        verify_evidence_fingerprint(raw)
        evidence_id = raw.get('evidence_id')
        if not isinstance(evidence_id, str) or re.fullmatch('[0-9a-f]{64}', evidence_id) is None:
            raise ValueError('evidence_id must be a lowercase SHA-256: ' + voice)
        result[voice] = evidence_id
    return result


def _alignment_complete(evidence):
    coverage = evidence.get('coverage')
    if not isinstance(coverage, dict) or coverage.get('status') != 'complete':
        return False
    notes = evidence.get('notes')
    return isinstance(notes, list) and any(
        isinstance(note, dict) and note.get('text_unit_ids') for note in notes)


def _analyze(evidences):
    from context_structure import analyze_structure
    structures = {}
    for voice, evidence in evidences.items():
        value = analyze_structure(copy.deepcopy(evidence))
        if not isinstance(value, dict):
            raise ValueError('analyze_structure returned an invalid value: ' + voice)
        if canonical_kind(value.get('kind')) != STRUCTURE_KIND:
            raise ValueError('structure kind is unsupported: ' + voice)
        if value.get('evidence_id') != evidence.get('evidence_id'):
            raise ValueError('structure evidence_id is stale: ' + voice)
        if value.get('voice') != voice:
            raise ValueError('structure voice does not match: ' + voice)
        structures[voice] = copy.deepcopy(value)
    return structures


def _obtain_semantics(evidences, structures, *, goal, semantic_response, provider):
    from context_semantics import obtain_semantics
    result = obtain_semantics(
        copy.deepcopy(evidences), copy.deepcopy(structures), goal=goal,
        provider=provider, response=copy.deepcopy(semantic_response))
    if not isinstance(result, dict):
        raise ValueError('semantic layer returned an invalid result')
    return copy.deepcopy(result)


def _response_digest(response):
    return _digest(response) if isinstance(response, dict) else None


def _code_identity():
    folder = Path(__file__).resolve().parent
    names = (
        'context_evidence.py', 'context_plan.py', 'context_structure.py',
        'gesture_catalog.py', 'context_semantics.py', 'context_planner.py',
    )
    return {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
        for name in names if (folder / name).is_file()
    }

def _note_map(evidence):
    notes = evidence.get('notes')
    if not isinstance(notes, list):
        return {}
    return {
        note['id']: note for note in notes
        if isinstance(note, dict) and isinstance(note.get('id'), str)
    }


def _ordered_notes(note_ids, by_id):
    unique = list(dict.fromkeys(note_ids))
    return sorted(
        unique, key=lambda note_id: (
            float(by_id[note_id].get('start', 0)),
            float(by_id[note_id].get('end', 0)),
            note_id,
        )) if all(note_id in by_id for note_id in unique) else unique


def _actual_note(note):
    if not isinstance(note, dict):
        return False
    if note.get('lyric') is not None and str(note.get('lyric')).strip() in _CONTINUATIONS:
        return False
    return bool(note.get('text_unit_ids'))


def _slice_notes(note_ids, limit=MAX_NOTES):
    note_ids = list(dict.fromkeys(note_ids))
    if len(note_ids) <= limit:
        return note_ids, False
    head = limit // 2
    tail = limit - head
    return list(dict.fromkeys(note_ids[:head] + note_ids[-tail:])), True


def _scope(voice, kind, identifier, span, quote, note_ids, *, focus_role=None,
           features=(), source='structure'):
    if (not isinstance(span, list) or len(span) != 2
            or any(type(value) is not int for value in span)
            or span[0] < 0 or span[1] <= span[0]):
        raise ValueError('scope span is invalid')
    return {
        'voice': voice,
        'kind': kind,
        'id': str(identifier),
        'span': list(span),
        'quote': str(quote),
        'note_ids': list(dict.fromkeys(map(str, note_ids))),
        'focus_role': focus_role,
        'features': sorted(set(map(str, features))),
        'source': source,
    }


def _phrase_scopes(voice, structure, evidence):
    notes = _note_map(evidence)
    scopes = []
    for phrase in structure.get('phrases') or []:
        if not isinstance(phrase, dict):
            continue
        note_ids = _ordered_notes(phrase.get('note_ids') or [], notes)
        if not note_ids or not any(_actual_note(notes.get(item)) for item in note_ids):
            continue
        scopes.append(_scope(
            voice, 'phrase', phrase.get('id', ''), phrase.get('span'),
            phrase.get('text', ''), note_ids,
            features=phrase.get('features') or ()))
    return scopes


def _word_scopes(voice, structure, evidence):
    notes = _note_map(evidence)
    scopes = []
    for word in structure.get('words') or []:
        if not isinstance(word, dict):
            continue
        note_ids = _ordered_notes(word.get('note_ids') or [], notes)
        if not note_ids:
            continue
        scopes.append(_scope(
            voice, 'word', word.get('word_id', word.get('id', '')), word.get('span'),
            word.get('text', ''), note_ids,
            features=(word.get('position'),) if word.get('position') else ()))
    return scopes


def _note_ids_for_span(evidence, span):
    if not isinstance(span, list) or len(span) != 2:
        return []
    units = {
        unit.get('id') for unit in evidence.get('text_units') or []
        if (isinstance(unit, dict) and isinstance(unit.get('span'), list)
            and len(unit['span']) == 2 and span[0] <= unit['span'][0]
            and unit['span'][1] <= span[1])
    }
    if not units:
        return []
    result = []
    for note in evidence.get('notes') or []:
        if isinstance(note, dict) and units.intersection(note.get('text_unit_ids') or []):
            result.append(note.get('id'))
    return _ordered_notes(result, _note_map(evidence))


def _closest_phrase_scope(voice, structure, evidence, span):
    candidates = []
    for scope in _phrase_scopes(voice, structure, evidence):
        start, end = scope['span']
        overlap = max(0, min(end, span[1]) - max(start, span[0]))
        if overlap:
            candidates.append((-(overlap / max(1, span[1] - span[0])), start, end, scope))
    return min(candidates, default=(None, None, None, None))[-1]


def _semantic_scopes(claim, structure, evidence):
    voice = claim['voice']
    span = claim['span']
    focus_rows = claim.get('focus') or []
    scopes = []
    problems = []
    if focus_rows:
        for index, focus in enumerate(focus_rows):
            focus_span = focus.get('span')
            note_ids = _note_ids_for_span(evidence, focus_span)
            if not note_ids:
                phrase = _closest_phrase_scope(voice, structure, evidence, focus_span)
                if phrase and phrase.get('span') == focus_span:
                    note_ids = phrase['note_ids']
            if not note_ids:
                problems.append({
                    'interpretation_id': claim.get('id'),
                    'focus_index': index,
                    'span': list(focus_span) if isinstance(focus_span, list) else None,
                    'status': 'focus_alignment_unknown',
                })
                continue
            scopes.append(_scope(
                voice, 'semantic_focus', '%s.focus-%d' % (claim.get('id'), index),
                list(focus_span), focus.get('quote', ''), note_ids,
                focus_role=focus.get('role'), source='semantic_claim'))
        # An explicitly supplied but unmapped focus is never broadened to the
        # whole interpretation or containing phrase.
        return scopes, problems
    note_ids = _note_ids_for_span(evidence, span)
    if note_ids:
        scopes.append(_scope(
            voice, 'semantic_span', claim.get('id'), list(span),
            claim.get('quote', ''), note_ids, source='semantic_claim'))
    else:
        phrase = _closest_phrase_scope(voice, structure, evidence, span)
        if phrase and phrase.get('span') == list(span):
            scopes.append(_scope(
                voice, 'semantic_phrase', claim.get('id'), list(span),
                claim.get('quote', ''), phrase['note_ids'], source='semantic_claim'))
        else:
            problems.append({
                'interpretation_id': claim.get('id'),
                'span': list(span),
                'status': 'interpretation_alignment_unknown',
            })
    return scopes, problems


def _stanza_packets(voice, structure, evidence):
    phrases = {row.get('id'): row for row in structure.get('phrases') or []
               if isinstance(row, dict)}
    notes = _note_map(evidence)
    words = [row for row in structure.get('words') or [] if isinstance(row, dict)]
    cues = [row for row in structure.get('cues') or [] if isinstance(row, dict)]
    packets = []
    for stanza in structure.get('stanzas') or []:
        if not isinstance(stanza, dict):
            continue
        span = stanza.get('span') or [0, 0]
        phrase_ids = list(stanza.get('phrase_ids') or [])
        stanza_phrases = [copy.deepcopy(phrases[item]) for item in phrase_ids if item in phrases]
        note_ids = []
        for phrase in stanza_phrases:
            note_ids.extend(phrase.get('note_ids') or [])
        note_ids = _ordered_notes(note_ids, notes)
        stanza_words = [
            copy.deepcopy(word) for word in words
            if isinstance(word.get('span'), list) and len(word['span']) == 2
            and span[0] <= word['span'][0] and word['span'][1] <= span[1]
        ]
        stanza_cues = [copy.deepcopy(cue) for cue in cues if cue.get('phrase_id') in phrase_ids]
        packets.append({
            'voice': voice,
            'id': stanza.get('id'),
            'span': list(span),
            'text': stanza.get('text', ''),
            'phrase_ids': phrase_ids,
            'phrases': stanza_phrases,
            'words': stanza_words,
            'notes': [copy.deepcopy(notes[item]) for item in note_ids if item in notes],
            'structure_notes': [
                copy.deepcopy(row) for row in structure.get('notes') or []
                if isinstance(row, dict) and row.get('note_id') in note_ids
            ],
            'cues': stanza_cues,
            'alignment_status': evidence.get('coverage', {}).get('status', 'unknown'),
        })
    return packets


def _voice_features(structure):
    features = set()
    for phrase in structure.get('phrases') or []:
        if isinstance(phrase, dict):
            features.update(str(item) for item in phrase.get('features') or [])
    for note in structure.get('notes') or []:
        if isinstance(note, dict):
            features.update(str(item) for item in note.get('features') or [])
    for cue in structure.get('cues') or []:
        if isinstance(cue, dict) and cue.get('kind'):
            features.add(str(cue['kind']))
    if structure.get('words'):
        features.update(('word_start', 'word_end'))
    return features


def _matches(structures, evidences, goal):
    from gesture_catalog import FEATURE_VOCABULARY, retrieve_methods
    rows = []
    for voice, structure in structures.items():
        evidence = evidences[voice]
        if not _alignment_complete(evidence):
            rows.append({
                'voice': voice,
                'language': structure.get('language') or evidence.get('text', {}).get('language', ''),
                'features': [],
                'methods': [],
                'status': 'alignment_unavailable',
                'use': 'whole_voice_summary_only',
            })
            continue
        features = sorted(_voice_features(structure).intersection(FEATURE_VOCABULARY))
        language = structure.get('language') or evidence.get('text', {}).get('language', '')
        methods = retrieve_methods(
            language=language, features=features, goal='', limit=5)
        rows.append({
            'voice': voice,
            'language': language,
            'features': features,
            'methods': copy.deepcopy(methods),
            'status': 'retrieved_conditional_references',
            'use': 'whole_voice_summary_only_not_applied',
        })
    return rows


def _scope_features(scope, structure, evidence):
    from gesture_catalog import FEATURE_VOCABULARY
    note_ids = set(scope.get('note_ids') or [])
    structure_notes = {
        note.get('note_id'): note for note in structure.get('notes') or []
        if isinstance(note, dict) and isinstance(note.get('note_id'), str)
    }
    features = set()
    for note_id in note_ids:
        note = structure_notes.get(note_id)
        if isinstance(note, dict):
            features.update(str(item) for item in note.get('features') or [])
    for phrase in structure.get('phrases') or []:
        if not isinstance(phrase, dict):
            continue
        phrase_notes = list(phrase.get('note_ids') or [])
        if not set(phrase_notes).intersection(note_ids):
            continue
        if canonical_kind(scope.get('kind')) == 'phrase' and str(phrase.get('id')) == str(scope.get('id')):
            features.update(str(item) for item in phrase.get('features') or [])
        if phrase_notes and phrase_notes[0] in note_ids:
            features.add('phrase_start')
        if phrase_notes and phrase_notes[-1] in note_ids:
            features.add('phrase_end')
    # Word boundaries derive from reviewed word membership. position is a
    # phrase-relative label and cannot reveal first/last note of a word.
    for word in structure.get('words') or []:
        if not isinstance(word, dict):
            continue
        word_notes = list(word.get('note_ids') or [])
        if not word_notes or not set(word_notes).intersection(note_ids):
            continue
        if word_notes[0] in note_ids:
            features.add('word_start')
        if word_notes[-1] in note_ids:
            features.add('word_end')
    for cue in structure.get('cues') or []:
        if not isinstance(cue, dict):
            continue
        cue_notes = set(cue.get('note_ids') or [])
        cue_span = cue.get('span')
        overlaps = bool(cue_notes.intersection(note_ids))
        if (isinstance(cue_span, list) and len(cue_span) == 2
                and isinstance(scope.get('span'), list) and len(scope['span']) == 2):
            overlaps = overlaps or (
                cue_span[0] < scope['span'][1] and scope['span'][0] < cue_span[1])
        if overlaps and cue.get('kind'):
            features.add(str(cue['kind']))
    return sorted(features.intersection(FEATURE_VOCABULARY))


def _scope_matches(scope, structure, evidence, goal):
    from gesture_catalog import retrieve_methods
    features = _scope_features(scope, structure, evidence)
    language = structure.get('language') or evidence.get('text', {}).get('language', '')
    methods = retrieve_methods(
        language=language, features=features, goal='', limit=3)
    return {
        'scope_id': scope.get('id'),
        'span': list(scope.get('span') or []),
        'note_ids': list(scope.get('note_ids') or []),
        'features': features,
        'methods': copy.deepcopy(methods),
        'status': 'local_conditional_references',
        'use': 'research_reference_only_not_applied',
    }


def _cue_ids_for_scopes(scopes, structures):
    result = []
    for scope in scopes:
        structure = structures[scope['voice']]
        note_ids = set(scope.get('note_ids') or [])
        for cue in structure.get('cues') or []:
            if isinstance(cue, dict) and set(cue.get('note_ids') or []).intersection(note_ids):
                if cue.get('id') not in result:
                    result.append(cue.get('id'))
    return result


def _gestures(scope, organization, evidence):
    notes = _note_map(evidence)
    ordered = _ordered_notes(scope.get('note_ids') or [], notes)
    actual_indices = [index for index, item in enumerate(ordered) if _actual_note(notes.get(item))]
    if not actual_indices:
        return [], False
    result = []
    if organization == 'tail-only':
        last = actual_indices[-1]
        sustain_ids, cut = _slice_notes(ordered[:last])
        release_ids, cut2 = _slice_notes(ordered[last:])
        if sustain_ids:
            direction, text = _TAIL_DIRECTIONS['sustain']
            result.append({'voice': scope['voice'], 'note_ids': sustain_ids,
                           'phase': 'sustain', 'direction': direction + '：' + text})
        if release_ids:
            direction, text = _TAIL_DIRECTIONS['release']
            result.append({'voice': scope['voice'], 'note_ids': release_ids,
                           'phase': 'release', 'direction': direction + '：' + text})
        return result, bool(cut or cut2)
    first, last = actual_indices[0], actual_indices[-1]
    result.append({'voice': scope['voice'], 'note_ids': [ordered[first]],
                   'phase': 'onset', 'direction': '%s：%s' % _LEVEL_DIRECTIONS['onset']})
    sustain_ids, cut = _slice_notes(ordered[first + 1:last])
    if sustain_ids:
        result.append({'voice': scope['voice'], 'note_ids': sustain_ids,
                       'phase': 'sustain',
                       'direction': '%s：%s' % _LEVEL_DIRECTIONS['sustain']})
    release_ids, cut2 = _slice_notes(ordered[last:])
    if release_ids:
        result.append({'voice': scope['voice'], 'note_ids': release_ids,
                       'phase': 'release',
                       'direction': '%s：%s' % _LEVEL_DIRECTIONS['release']})
    return result, bool(cut or cut2)


def _candidate_id(base, used):
    base = re.sub('[^A-Za-z0-9._-]+', '-', str(base)).strip('.-_').lower()
    if not base or not _SAFE_ID.fullmatch(base):
        base = 'candidate-' + hashlib.sha256(str(base).encode('utf-8')).hexdigest()[:12]
    base = base[:64]
    value, index = base, 2
    while value in used:
        suffix = '-' + str(index)
        value = base[:64-len(suffix)] + suffix
        index += 1
    used.add(value)
    return value


def _signature(candidate):
    return _digest(candidate.get('gestures') or [])


def _add_candidate(plan, provenance, used_ids, *, identifier, intent, rationale,
                   scopes, organization, evidences, basis, local_matches,
                   source_references, cue_ids, interpretation_ids=()):
    gestures = []
    truncated = False
    used_notes = set()
    for scope in scopes:
        rows, cut = _gestures(scope, organization, evidences[scope['voice']])
        for gesture in rows:
            if len(gestures) >= MAX_GESTURES:
                truncated = True
                break
            gestures.append(gesture)
            used_notes.update(gesture['note_ids'])
        truncated = truncated or cut
        if len(gestures) >= MAX_GESTURES:
            break
    if not gestures:
        return False
    candidate = {
        'id': _candidate_id(identifier, used_ids),
        'intent': _text(intent, 'candidate.intent', 500),
        'rationale': _text(rationale, 'candidate.rationale', 1200),
        'gestures': gestures,
    }
    plan['candidates'].append(candidate)
    provenance.append({
        'id': candidate['id'],
        'basis': basis,
        'organization': organization,
        'scopes': copy.deepcopy(scopes),
        'interpretation_ids': list(interpretation_ids),
        'cue_ids': list(cue_ids),
        'source_references': copy.deepcopy(source_references),
        'local_matches': copy.deepcopy(local_matches),
        'gesture_count': len(gestures),
        'note_count': len(used_notes),
        'truncated': truncated,
        'selection_status': 'candidate_not_selected',
    })
    return True


def _semantic_claim_rows(semantics):
    status = semantics.get('status')
    if status not in ('imported_unverified', 'validated_claims'):
        return []
    response = semantics.get('response')
    rows = response.get('interpretations') if isinstance(response, dict) else None
    return list(rows) if isinstance(rows, list) else []


def propose_plan(evidences, *, goal='', semantic_response=None, provider=None):
    """Return an unselected proposal with strict plan and research provenance.

    ``goal`` only guides lexical method retrieval. Provider/imported claims stay
    separate from literal structural cues and from the strict executable plan.
    """
    goal = _text(goal, 'goal', MAX_GOAL_LENGTH, empty=True)
    evidence_ids = _evidence_ids(evidences)
    structures = _analyze(evidences)
    semantics = _obtain_semantics(
        evidences, structures, goal=goal,
        semantic_response=semantic_response, provider=provider)
    matches = _matches(structures, evidences, goal)
    plan = make_plan(evidences)
    provenance = []
    used_ids = set()
    limitations = list(semantics.get('limitations') or [])
    focus_problems = []
    limit_events = []
    scope_cache = {}

    complete = {
        voice for voice, evidence in evidences.items()
        if _alignment_complete(evidence)
    }
    incomplete = [voice for voice in evidences if voice not in complete]
    for voice in incomplete:
        status = (evidences[voice].get('coverage') or {}).get('status', 'unknown')
        limitations.append(
            'Voice %s has alignment status %r; semantic gestures are withheld.' % (voice, status))
        limit_events.append({'code': 'alignment_incomplete', 'voice': voice, 'status': status})

    first_scopes, last_scopes, word_scopes = [], [], []
    for voice in sorted(complete):
        structure, evidence = structures[voice], evidences[voice]
        phrases = _phrase_scopes(voice, structure, evidence)
        words = _word_scopes(voice, structure, evidence)
        if phrases:
            first_scopes.append(phrases[0])
            last_scopes.append(phrases[-1])
        if words:
            word_scopes.append(words[0])

    def local_references(scopes):
        rows = []
        for scope in scopes:
            key = (scope['voice'], scope['kind'], scope['id'], tuple(scope['note_ids']))
            if key not in scope_cache:
                scope_cache[key] = _scope_matches(
                    scope, structures[scope['voice']], evidences[scope['voice']], goal)
            rows.append(copy.deepcopy(scope_cache[key]))
        flat = []
        for local in rows:
            for method in local.get('methods') or []:
                item = {
                    'id': method.get('id'),
                    'source_id': method.get('source_id'),
                    'source_url': method.get('source_url'),
                    'source_language': method.get('source_language'),
                    'match_reasons': list(method.get('match_reasons') or []),
                    'applicability': copy.deepcopy(method.get('applicability') or {}),
                    'limitations': list(method.get('limitations') or []),
                    'scope_id': local.get('scope_id'),
                    'use': 'conditional_reference_only',
                }
                if item not in flat:
                    flat.append(item)
        return rows, flat

    def add(**kwargs):
        scopes = list(kwargs['scopes'])
        omitted_scopes = max(0, len(scopes) - MAX_LOCAL_SCOPES)
        if omitted_scopes:
            limit_events.append({
                'code': 'max_local_scopes',
                'candidate': kwargs.get('identifier'),
                'omitted': omitted_scopes,
            })
            scopes = scopes[:MAX_LOCAL_SCOPES]
            truncate_scopes = True
        else:
            truncate_scopes = False
        local, flat = local_references(scopes)
        kwargs['scopes'] = scopes
        kwargs['local_matches'] = local
        kwargs['source_references'] = flat
        kwargs['cue_ids'] = _cue_ids_for_scopes(scopes, structures)
        added = _add_candidate(plan, provenance, used_ids, evidences=evidences, **kwargs)
        if added and truncate_scopes:
            provenance[-1]['truncated'] = True
            provenance[-1]['scope_cap_omitted'] = omitted_scopes
        return added

    if first_scopes:
        add(
            identifier='structural-local-arc',
            intent='结构假设：按原文乐句的首个实际演唱音符建立局部进入，再保留句内和句尾轮廓。',
            rationale='以显式短语和完整字音关联定位局部门组；起音只落在该组首个实际音符，延续标记随所属组保留。',
            scopes=first_scopes, organization='onset-internal-tail',
            basis='literal_structure', interpretation_ids=[])
    if first_scopes:
        second_scopes = word_scopes or last_scopes
        organization = (
            'tail-only'
            if [item['note_ids'] for item in second_scopes] == [item['note_ids'] for item in first_scopes]
            else 'onset-internal-tail')
        add(
            identifier='structural-local-alternative',
            intent='结构假设：改为手动词组或尾部局部进入，并用不同于上一候选的句内/句尾组织。',
            rationale='第二个候选由明确的词范围或后置短语定位；没有词范围时只保留句尾维持与释放，不伪造新词。',
            scopes=second_scopes, organization=organization,
            basis='literal_structure', interpretation_ids=[])

    if complete and len(plan['candidates']) < 2 and first_scopes:
        add(
            identifier='structural-tail-fallback',
            intent='结构假设：只保留同一已对齐局部门的尾部维持与释放，避免重复起音。',
            rationale='当其他明确局部门不足时，提供与起音方案不同的尾部组织；仍限定在已对齐原文短语内。',
            scopes=first_scopes, organization='tail-only',
            basis='literal_structure', interpretation_ids=[])

    claim_rows = _semantic_claim_rows(semantics)
    for index, claim in enumerate(claim_rows):
        if len(plan['candidates']) >= MAX_CANDIDATES_TOTAL:
            limit_events.append({'code': 'max_plan_candidates', 'omitted_semantic_claims': len(claim_rows) - index})
            limitations.append('Semantic candidates were truncated at the plan candidate limit.')
            break
        voice = claim.get('voice')
        if voice not in structures or voice not in complete:
            limitations.append('A semantic claim referenced unknown or incomplete evidence and was not bound.')
            continue
        scopes, problems = _semantic_scopes(claim, structures[voice], evidences[voice])
        focus_problems.extend(problems)
        if not scopes:
            limitations.append(
                'Semantic interpretation %s had no exact focus-to-note binding; no semantic gesture was created.'
                % claim.get('id'))
            continue
        status = semantics.get('status')
        label = '导入语义假设' if status == 'imported_unverified' else '提供方报告假设'
        intent = _text(claim.get('intent', ''), 'semantic.intent', 500)
        basis = 'imported_semantic_claim' if status == 'imported_unverified' else 'provider_semantic_claim'
        add(
            identifier='semantic-' + str(claim.get('id', index)),
            intent=intent,
            rationale=label + '。仅在解释声明的原文范围、焦点和当前完整字音关联内定位；格式校验不代表语义事实或听感效果。',
            scopes=scopes, organization='onset-internal-tail',
            basis=basis, interpretation_ids=[claim.get('id')])

    if complete and len(plan['candidates']) < 2:
        limitations.append('Fewer than two local alternative hypotheses were possible from the current aligned evidence.')

    plan = validate_plan(plan, evidences)
    semantic_fingerprint = _response_digest(semantics.get('response'))
    input_fingerprint = _digest({
        'evidence_ids': evidence_ids,
        'goal': goal,
        'semantic_response': semantics.get('response'),
    })
    structures_packet = {
        voice: {
            'structure': copy.deepcopy(structures[voice]),
            'stanzas': _stanza_packets(voice, structures[voice], evidences[voice]),
            'provenance': {
                'thresholds': copy.deepcopy(structures[voice].get('metadata') or {}),
                'versions': {
                    'structure_schema': structures[voice].get('schema_version'),
                    'catalog': __import__('gesture_catalog').CATALOG_VERSION,
                    'semantic_request': (semantics.get('request') or {}).get('kind'),
                },
            },
        }
        for voice in evidences
    }
    all_cues = [
        {'voice': voice, **copy.deepcopy(cue)}
        for voice, structure in structures.items()
        for cue in structure.get('cues') or [] if isinstance(cue, dict)
    ]

    available_note_ids = {
        voice: {
            note.get('id') for note in evidences[voice].get('notes') or []
            if isinstance(note, dict) and isinstance(note.get('id'), str)
        }
        for voice in evidences
    }
    selected_note_refs = [
        {'voice': gesture['voice'], **copy.deepcopy(gesture)}
        for candidate in plan['candidates'] for gesture in candidate['gestures']
    ]
    selected_unique = {
        voice: {note_id for row in selected_note_refs if row['voice'] == voice
                for note_id in row['note_ids']}
        for voice in evidences
    }
    selected_scopes = []
    for item in provenance:
        for scope in item.get('scopes') or []:
            key = (scope['voice'], scope['kind'], scope['id'], tuple(scope['note_ids']))
            if key not in selected_scopes:
                selected_scopes.append(key)
    available_scopes = {
        voice: {
            'phrases': len(_phrase_scopes(voice, structures[voice], evidences[voice])),
            'words': len(_word_scopes(voice, structures[voice], evidences[voice])),
        }
        for voice in evidences
    }
    cap_hit = any(item['truncated'] for item in provenance) or bool(limit_events)
    if not complete:
        coverage_status = 'unavailable'
    elif incomplete:
        coverage_status = 'partial'
    elif cap_hit or any(
            selected_unique[voice] != available_note_ids[voice] for voice in evidences):
        coverage_status = 'local_subset'
    else:
        coverage_status = 'complete'
    coverage = {
        'status': coverage_status,
        'voices': {voice: copy.deepcopy(evidences[voice].get('coverage') or {})
                   for voice in evidences},
        'scopes': {
            'available_by_voice': available_scopes,
            'selected_unique': len(selected_scopes),
            'per_candidate_limit': MAX_LOCAL_SCOPES,
        },
        'notes': {
            'available_by_voice': {voice: len(values) for voice, values in available_note_ids.items()},
            'planned_references': len(selected_note_refs),
            'planned_unique_by_voice': {voice: len(values) for voice, values in selected_unique.items()},
        },
        'candidates': [
            {'id': item['id'], 'gesture_count': item['gesture_count'],
             'note_count': item['note_count'], 'truncated': item['truncated']}
            for item in provenance
        ],
        'limit_events': limit_events,
    }
    if coverage_status == 'local_subset':
        limitations.append('The proposal covers a deliberate local subset; available stanzas or notes outside its scopes remain unplanned.')
    if cap_hit:
        limitations.append('A local-scope, gesture or note cap limited coverage; omissions are recorded in analysis.coverage.limit_events.')
    limitations.append('goal_not_used_for_gestures: the user goal was not parsed and did not select candidate gestures.')
    limitations.append('Method matches are conditional structural references and were not applied as authored gestures.')
    limitations.append('No candidate is selected and no numerical score parameter or audio output was produced.')

    analysis = {
        'goal': goal,
        'goal_handling': 'unparsed_user_goal',
        'target_performance': {'status': 'not_supplied', 'claims': []},
        'evidence_ids': evidence_ids,
        'structures': structures_packet,
        'literal_cues': all_cues,
        'alignment': {
            voice: (evidences[voice].get('coverage') or {}).get('status', 'unknown')
            for voice in evidences
        },
        'coverage': coverage,
        'candidate_provenance': provenance,
        'semantic_focus_fallbacks': focus_problems,
        'semantic_gesture_candidates': sum(
            1 for item in provenance if item['basis'].endswith('semantic_claim')),
        'method_match_summary': 'Whole-voice rows are summaries only; candidate references are local scope matches.',
        'inputs': {
            'goal': goal,
            'goal_is_user_context_only': True,
            'goal_not_used_for_gestures': True,
            'semantic_status': semantics.get('status'),
            'semantic_goal_binding': semantics.get('goal_binding', 'no_semantic_claims'),
            'semantic_inference': semantics.get('inference'),
            'semantic_response_sha256': semantic_fingerprint,
            'semantic_privacy': copy.deepcopy(semantics.get('privacy') or {}),
            'proposal_input_fingerprint': input_fingerprint,
        },
    }
    proposal = {
        'schema_version': SCHEMA_VERSION,
        'kind': PROPOSAL_KIND,
        'status': 'advisory_only',
        'selected_candidate': None,
        'plan': plan,
        'analysis': analysis,
        'semantics': semantics,
        'matches': matches,
        'limitations': list(dict.fromkeys(str(item) for item in limitations)),
        'provenance': {
            'planner': 'deterministic_structure_and_conditional_retrieval',
            'selection': 'manual_required',
            'parameter_application': 'not_applied_advisory_only',
            'project_written': False,
            'audio_rendered': False,
            'code_sha256': _code_identity(),
        },
    }
    validate_proposal(proposal, evidences)
    return proposal


def semantic_response_fingerprint(response):
    return _response_digest(copy.deepcopy(response))


def proposal_input_fingerprint(evidences, *, goal='', semantic_response=None):
    return _digest({
        'evidence_ids': _evidence_ids(evidences),
        'goal': _text(goal, 'goal', MAX_GOAL_LENGTH, empty=True),
        'semantic_response': copy.deepcopy(semantic_response),
    })


def proposal_fingerprint(proposal):
    """Return a deterministic fingerprint independent of transient commit state."""
    if not isinstance(proposal, dict):
        raise ValueError('proposal must be an object')
    value = copy.deepcopy(proposal)
    value.pop('commit', None)
    return _digest(value)


def code_fingerprints():
    """Current hashes for modules that determine proposal content."""
    return _code_identity()


def validate_proposal(proposal, evidences, *, require_ready=False,
                      verify_code=False, semantic_response='__proposal__'):
    """Validate proposal, plan, stanza and semantic bindings without mutation."""
    if not isinstance(proposal, dict):
        raise ValueError('proposal must be an object')
    required = (
        'schema_version', 'kind', 'status', 'selected_candidate', 'plan',
        'analysis', 'semantics', 'matches', 'limitations', 'provenance')
    missing = [name for name in required if name not in proposal]
    if missing:
        raise ValueError('proposal is missing fields: ' + ', '.join(missing))
    if type(proposal['schema_version']) is not int or proposal['schema_version'] != SCHEMA_VERSION:
        raise ValueError('proposal.schema_version must be integer 1')
    if canonical_kind(proposal['kind']) != PROPOSAL_KIND:
        raise ValueError('proposal.kind is unsupported')
    if proposal['status'] != 'advisory_only':
        raise ValueError('proposal.status must be advisory_only')
    if proposal['selected_candidate'] is not None:
        raise ValueError('proposal.selected_candidate must remain null')
    commit = proposal.get('commit')
    if require_ready and (not isinstance(commit, dict) or commit.get('state') != 'ready'):
        raise ValueError('proposal commit is incomplete; rerun propose')

    evidence_ids = _evidence_ids(evidences)
    plan = validate_plan(copy.deepcopy(proposal['plan']), evidences)
    if plan.get('selected_candidate') is not None:
        raise ValueError('embedded plan must not select a candidate')
    analysis = proposal['analysis']
    if not isinstance(analysis, dict):
        raise ValueError('proposal.analysis must be an object')
    if analysis.get('evidence_ids') != evidence_ids:
        raise ValueError('proposal evidence_ids are stale')
    goal = _text(analysis.get('goal', ''), 'analysis.goal', MAX_GOAL_LENGTH, empty=True)
    if analysis.get('goal_handling') != 'unparsed_user_goal':
        raise ValueError('proposal goal handling is invalid')
    structures = analysis.get('structures')
    if not isinstance(structures, dict) or set(structures) != set(evidences):
        raise ValueError('proposal structure voices do not match evidence')
    for voice, packet in structures.items():
        if not isinstance(packet, dict) or not isinstance(packet.get('structure'), dict):
            raise ValueError('proposal stanza packet is malformed: ' + voice)
        structure = packet['structure']
        if canonical_kind(structure.get('kind')) != STRUCTURE_KIND or structure.get('evidence_id') != evidence_ids[voice]:
            raise ValueError('proposal structure is stale: ' + voice)
        if structure.get('voice') != voice or not isinstance(packet.get('stanzas'), list):
            raise ValueError('proposal stanza packet is invalid: ' + voice)
    provenance = analysis.get('candidate_provenance')
    if not isinstance(provenance, list):
        raise ValueError('candidate provenance must be a list')
    provenance_ids = []
    for item in provenance:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise ValueError('candidate provenance is malformed')
        provenance_ids.append(item['id'])
        if not isinstance(item.get('local_matches'), list):
            raise ValueError('candidate local matches are missing: ' + item['id'])
        for local in item['local_matches']:
            if (not isinstance(local, dict)
                    or local.get('status') != 'local_conditional_references'
                    or not isinstance(local.get('scope_id'), str)
                    or not isinstance(local.get('features'), list)
                    or not isinstance(local.get('note_ids'), list)):
                raise ValueError('candidate local match is malformed: ' + item['id'])
    if len(provenance_ids) != len(set(provenance_ids)):
        raise ValueError('candidate provenance IDs must be unique')
    plan_ids = [item['id'] for item in plan['candidates']]
    if provenance_ids != plan_ids:
        raise ValueError('candidate provenance must cover every plan candidate in order')

    semantics = proposal['semantics']
    if (not isinstance(semantics, dict)
            or not isinstance(semantics.get('status'), str)
            or not semantics.get('status')):
        raise ValueError('proposal semantics result is invalid')
    stored_response = semantics.get('response')
    if stored_response is not None:
        from context_semantics import validate_semantic_response
        stored_response = validate_semantic_response(copy.deepcopy(stored_response), evidences)
        if semantics.get('status') not in ('imported_unverified', 'validated_claims'):
            raise ValueError('semantic response is present for a rejected status')
    elif semantics.get('status') in ('imported_unverified', 'validated_claims'):
        raise ValueError('accepted semantic status requires a response')
    if semantic_response == '__proposal__':
        fresh_response = stored_response
    else:
        fresh_response = copy.deepcopy(semantic_response)
        if fresh_response is not None:
            from context_semantics import validate_semantic_response
            fresh_response = validate_semantic_response(fresh_response, evidences)
        if fresh_response != stored_response:
            raise ValueError('persisted semantic proposal input is stale')
    inputs = analysis.get('inputs')
    if not isinstance(inputs, dict):
        raise ValueError('proposal inputs are missing')
    if inputs.get('goal') != goal:
        raise ValueError('proposal goal input is inconsistent')
    response_fingerprint = semantic_response_fingerprint(fresh_response)
    if inputs.get('semantic_response_sha256') != response_fingerprint:
        raise ValueError('proposal semantic response fingerprint is stale')
    expected_input = proposal_input_fingerprint(
        evidences, goal=goal, semantic_response=fresh_response)
    if inputs.get('proposal_input_fingerprint') != expected_input:
        raise ValueError('proposal input fingerprint is stale')
    if not isinstance(proposal.get('matches'), list):
        raise ValueError('proposal matches must be a list')
    if not isinstance(proposal.get('limitations'), list):
        raise ValueError('proposal limitations must be a list')
    if not any(str(item).startswith('goal_not_used_for_gestures:') for item in proposal['limitations']):
        raise ValueError('proposal must declare goal_not_used_for_gestures')
    provenance_meta = proposal.get('provenance')
    if (not isinstance(provenance_meta, dict)
            or provenance_meta.get('parameter_application') != 'not_applied_advisory_only'
            or provenance_meta.get('project_written') is not False
            or provenance_meta.get('audio_rendered') is not False):
        raise ValueError('proposal output status is invalid')
    if verify_code and provenance_meta.get('code_sha256') != _code_identity():
        raise ValueError('proposal code fingerprint is stale; rerun propose')
    result = copy.deepcopy(proposal)
    result['plan'] = plan
    return result


__all__ = (
    'PROPOSAL_KIND', 'PROPOSAL_INPUT_KIND', 'propose_plan',
    'validate_proposal', 'proposal_fingerprint', 'proposal_input_fingerprint',
    'semantic_response_fingerprint', 'code_fingerprints',
)
