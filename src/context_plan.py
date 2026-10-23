"""Validate advisory context plans without changing score or audio inputs.

A plan describes suggested gestures only.  This module does not interpret
lyrics, estimate probabilities, write SVP data, or read external files.
"""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import hashlib
import json
import re
import unicodedata

SCHEMA_VERSION = 1
EVIDENCE_KIND = 'vwa.context-evidence'
PLAN_KIND = 'vwa.context-plan'
PHASES = frozenset(('onset', 'sustain', 'release'))

MAX_CANDIDATES = 32
MAX_GESTURES = 32
MAX_NOTES = 128
MAX_ID_LENGTH = 64
MAX_EVIDENCE_ID_LENGTH = 160
MAX_VOICE_LENGTH = 64
MAX_INTENT_LENGTH = 500
MAX_RATIONALE_LENGTH = 1200
MAX_DIRECTION_LENGTH = 160

_HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
_CANDIDATE_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


def _fail(path, message):
    raise ValueError(f'{path}：{message}')


def _object(value, path):
    if not isinstance(value, dict):
        _fail(path, '必须为字典。')
    return value


def _required_fields(value, path, required):
    missing = [key for key in required if key not in value]
    if missing:
        labels = ', '.join(sorted(missing))
        _fail(path, '缺少字段：' + labels + '。')


def _fields(value, path, required, optional=()):
    required = frozenset(required)
    optional = frozenset(optional)
    unknown = [key for key in value if key not in required | optional]
    if unknown:
        labels = ', '.join(sorted(repr(key) for key in unknown))
        _fail(path, '包含未知字段：' + labels + '。')
    missing = [key for key in required if key not in value]
    if missing:
        labels = ', '.join(sorted(missing))
        _fail(path, '缺少字段：' + labels + '。')


def _utf8(value, path):
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        _fail(path, '包含无效 Unicode 字符。')


def _text(value, path, limit):
    if not isinstance(value, str):
        _fail(path, '必须为文本。')
    _utf8(value, path)
    value = unicodedata.normalize('NFC', value).strip()
    if not value:
        _fail(path, '不能为空。')
    if len(value) > limit:
        _fail(path, f'长度不能超过 {limit} 个字符。')
    return value


def _identifier(value, path, limit=MAX_EVIDENCE_ID_LENGTH):
    if not isinstance(value, str):
        _fail(path, '必须为字符串。')
    _utf8(value, path)
    if value != value.strip() or not value:
        _fail(path, '不能为空或包含首尾空白。')
    if len(value) > limit:
        _fail(path, f'长度不能超过 {limit} 个字符。')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(path, '包含控制字符。')
    return value


def _evidence_id(value, path):
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(path, '必须为 64 位小写十六进制 SHA-256。')
    return value


def _evidence_index(evidences):
    evidences = _object(evidences, 'evidences')
    evidence_ids = {}
    note_ids = {}
    for raw_voice, raw_evidence in evidences.items():
        voice = _identifier(raw_voice, 'evidences 的声部键', MAX_VOICE_LENGTH)
        path = f'evidences[{voice!r}]'
        evidence = _object(raw_evidence, path)
        _required_fields(evidence, path, ('schema_version', 'kind', 'evidence_id', 'voice', 'notes'))
        if type(evidence['schema_version']) is not int or evidence['schema_version'] != SCHEMA_VERSION:
            _fail(path + '.schema_version', '仅支持整数 1。')
        if canonical_kind(evidence['kind']) != EVIDENCE_KIND:
            _fail(path + '.kind', f'必须为 {EVIDENCE_KIND!r}。')
        evidence_id = _evidence_id(evidence['evidence_id'], path + '.evidence_id')
        declared_voice = _identifier(evidence['voice'], path + '.voice', MAX_VOICE_LENGTH)
        if declared_voice != voice:
            _fail(path + '.voice', '必须与 evidence 集合中的声部键一致。')
        notes = evidence['notes']
        if not isinstance(notes, list):
            _fail(path + '.notes', '必须为列表。')
        seen = set()
        for index, note in enumerate(notes):
            note = _object(note, f'{path}.notes[{index}]')
            if 'id' not in note:
                _fail(f'{path}.notes[{index}]', '缺少字段：id。')
            note_id = _identifier(note['id'], f'{path}.notes[{index}].id')
            if note_id in seen:
                _fail(f'{path}.notes[{index}].id', '在同一 evidence 中重复。')
            seen.add(note_id)
        evidence_ids[voice] = evidence_id
        note_ids[voice] = seen
    return evidence_ids, note_ids


def make_plan(evidences):
    """Return an empty advisory plan tied to the current evidence revisions."""
    evidence_ids, _ = _evidence_index(evidences)
    return {
        'schema_version': SCHEMA_VERSION,
        'kind': PLAN_KIND,
        'evidence_ids': {voice: evidence_id for voice, evidence_id in evidence_ids.items()},
        'candidates': [],
        'selected_candidate': None,
    }


def _exact_evidence_ids(value, current, path):
    value = _object(value, path)
    supplied = set(value)
    expected = set(current)
    unknown = supplied - expected
    missing = expected - supplied
    if unknown:
        _fail(path, '包含未知声部：' + ', '.join(sorted(map(str, unknown))) + '。')
    if missing:
        _fail(path, '缺少声部：' + ', '.join(sorted(map(str, missing))) + '。')
    normalized = {}
    for voice in current:
        normalized_id = _evidence_id(value[voice], f'{path}[{voice!r}]')
        if normalized_id != current[voice]:
            _fail(f'{path}[{voice!r}]', 'evidence_id 已过时，请基于当前 evidence 重做计划。')
        normalized[voice] = normalized_id
    return normalized


def _candidate_id(value, path):
    if not isinstance(value, str):
        _fail(path, '必须为字符串。')
    _utf8(value, path)
    if _CANDIDATE_ID_RE.fullmatch(value) is None:
        _fail(path, '必须为 1-64 位英文字母、数字、点、下划线或连字符组成的安全短 ID。')
    return value


def validate_plan(plan, evidences):
    """Return a normalized deep copy of a valid plan or raise ``ValueError``.

    The evidence revision and every voice/note reference must still be
    current.  Validation never mutates either argument.
    """
    evidence_ids, available_notes = _evidence_index(evidences)
    plan = _object(plan, 'plan')
    _fields(plan, 'plan', (
        'schema_version', 'kind', 'evidence_ids', 'candidates', 'selected_candidate'))
    if type(plan['schema_version']) is not int or plan['schema_version'] != SCHEMA_VERSION:
        _fail('plan.schema_version', '仅支持整数 1。')
    if canonical_kind(plan['kind']) != PLAN_KIND:
        _fail('plan.kind', f'必须为 {PLAN_KIND!r}。')

    normalized_ids = _exact_evidence_ids(plan['evidence_ids'], evidence_ids, 'plan.evidence_ids')
    raw_candidates = plan['candidates']
    if not isinstance(raw_candidates, list):
        _fail('plan.candidates', '必须为列表。')
    if len(raw_candidates) > MAX_CANDIDATES:
        _fail('plan.candidates', f'最多允许 {MAX_CANDIDATES} 个候选。')

    candidates = []
    candidate_ids = set()
    for index, raw_candidate in enumerate(raw_candidates):
        path = f'plan.candidates[{index}]'
        raw_candidate = _object(raw_candidate, path)
        _fields(raw_candidate, path, ('id', 'intent', 'gestures'), ('rationale',))
        candidate_id = _candidate_id(raw_candidate['id'], path + '.id')
        if candidate_id in candidate_ids:
            _fail(path + '.id', '候选 ID 必须唯一。')
        candidate_ids.add(candidate_id)
        intent = _text(raw_candidate['intent'], path + '.intent', MAX_INTENT_LENGTH)

        raw_gestures = raw_candidate['gestures']
        if not isinstance(raw_gestures, list):
            _fail(path + '.gestures', '必须为列表。')
        if len(raw_gestures) > MAX_GESTURES:
            _fail(path + '.gestures', f'最多允许 {MAX_GESTURES} 个手势。')
        gestures = []
        for gesture_index, raw_gesture in enumerate(raw_gestures):
            gesture_path = f'{path}.gestures[{gesture_index}]'
            raw_gesture = _object(raw_gesture, gesture_path)
            _fields(raw_gesture, gesture_path, ('voice', 'note_ids', 'phase', 'direction'))
            voice = _identifier(raw_gesture['voice'], gesture_path + '.voice', MAX_VOICE_LENGTH)
            if voice not in available_notes:
                _fail(gesture_path + '.voice', '引用了 evidence 中不存在的声部。')
            raw_note_ids = raw_gesture['note_ids']
            if not isinstance(raw_note_ids, list):
                _fail(gesture_path + '.note_ids', '必须为列表。')
            if not raw_note_ids:
                _fail(gesture_path + '.note_ids', '不能为空。')
            if len(raw_note_ids) > MAX_NOTES:
                _fail(gesture_path + '.note_ids', f'最多允许 {MAX_NOTES} 个音符。')
            normalized_note_ids = []
            gesture_notes = set()
            for note_index, raw_note_id in enumerate(raw_note_ids):
                note_id = _identifier(
                    raw_note_id, f'{gesture_path}.note_ids[{note_index}]')
                if note_id in gesture_notes:
                    _fail(f'{gesture_path}.note_ids[{note_index}]', '同一手势中的音符 ID 不能重复。')
                if note_id not in available_notes[voice]:
                    _fail(f'{gesture_path}.note_ids[{note_index}]', '引用了当前 evidence 中不存在的音符。')
                gesture_notes.add(note_id)
                normalized_note_ids.append(note_id)
            phase = raw_gesture['phase']
            if not isinstance(phase, str) or phase not in PHASES:
                _fail(gesture_path + '.phase', "必须为 'onset'、'sustain' 或 'release'。")
            direction = _text(
                raw_gesture['direction'], gesture_path + '.direction', MAX_DIRECTION_LENGTH)
            gestures.append({
                'voice': voice,
                'note_ids': normalized_note_ids,
                'phase': phase,
                'direction': direction,
            })

        candidate = {'id': candidate_id, 'intent': intent, 'gestures': gestures}
        if 'rationale' in raw_candidate:
            candidate['rationale'] = _text(
                raw_candidate['rationale'], path + '.rationale', MAX_RATIONALE_LENGTH)
        candidates.append(candidate)

    selected = plan['selected_candidate']
    if selected is None:
        normalized_selected = None
    else:
        normalized_selected = _candidate_id(selected, 'plan.selected_candidate')
        if normalized_selected not in candidate_ids:
            _fail('plan.selected_candidate', '必须引用当前计划中的候选 ID。')

    return {
        'schema_version': SCHEMA_VERSION,
        'kind': PLAN_KIND,
        'evidence_ids': normalized_ids,
        'candidates': candidates,
        'selected_candidate': normalized_selected,
    }


def plan_fingerprint(plan, evidences):
    """Return a deterministic SHA-256 over the normalized plan and evidence IDs."""
    normalized = validate_plan(plan, evidences)
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


__all__ = ('make_plan', 'validate_plan', 'plan_fingerprint')