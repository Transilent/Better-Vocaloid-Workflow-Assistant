"""Contract tests for advisory context plans and evidence revision binding."""
import copy
import hashlib
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from context_plan import make_plan, plan_fingerprint, validate_plan


def digest(label):
    return hashlib.sha256(label.encode('utf-8')).hexdigest()


def note(note_id, lyric, start, pitch):
    return {'id': note_id, 'lyric': lyric, 'start': start, 'end': start + 1,
            'pitch': pitch, 'text_unit_ids': ['unit-1'],
            'syllable_ids': ['syllable-1']}


def evidence(voice, note_ids, revision):
    labels = {'l1': ('你', .0, 60), 'l2': ('好', 1., 62),
              'b1': ('啊', .0, 55), 'b2': ('', 1., 57)}
    return {
        'schema_version': 1,
        'kind': 'vwa.context-evidence',
        'evidence_id': digest(voice + '-' + revision),
        'voice': voice,
        'notes': [note(note_id, *labels[note_id]) for note_id in note_ids],
        'text_units': [{'id': 'unit-1', 'text': '你好'}],
        'words': [{'id': 'word-1', 'text': '你好'}],
        'syllables': [{'id': 'syllable-1', 'text': 'ni'}],
        'issues': [],
        'future_evidence_field': {'kept': 'for compatibility'},
    }


evidences = {
    'lead': evidence('lead', ('l1', 'l2'), 'r1'),
    'backing': evidence('backing', ('b1', 'b2'), 'r1'),
}
plan = {
    'schema_version': 1,
    'kind': 'vwa.context-plan',
    'evidence_ids': {'lead': evidences['lead']['evidence_id'],
                     'backing': evidences['backing']['evidence_id']},
    'candidates': [
        {'id': 'gentle', 'intent': '温柔地回应', 'gestures': [
            {'voice': 'lead', 'note_ids': ['l1'], 'phase': 'onset',
             'direction': 'soft_onset'}]},
        {'id': 'firm-2', 'intent': '坚定地结束', 'rationale': '供人工采纳的建议',
         'gestures': [
             {'voice': 'lead', 'note_ids': ['l2'], 'phase': 'release',
              'direction': 'firm_release'},
             {'voice': 'backing', 'note_ids': ['b1', 'b2'], 'phase': 'sustain',
              'direction': 'steady_support'}]},
    ],
    'selected_candidate': 'gentle',
}


def rejected(call, fragment):
    try:
        call()
    except ValueError as error:
        assert fragment in str(error), (fragment, str(error))
        return error
    raise AssertionError('Expected ValueError containing ' + fragment)


def rejected_unchanged(candidate_plan, candidate_evidences, fragment):
    plan_before = copy.deepcopy(candidate_plan)
    evidence_before = copy.deepcopy(candidate_evidences)
    rejected(lambda: validate_plan(candidate_plan, candidate_evidences), fragment)
    assert candidate_plan == plan_before
    assert candidate_evidences == evidence_before


# A draft is empty by construction and carries only current evidence revisions.
evidence_before = copy.deepcopy(evidences)
draft = make_plan(evidences)
assert draft == {
    'schema_version': 1,
    'kind': 'vwa.context-plan',
    'evidence_ids': {'lead': evidences['lead']['evidence_id'],
                     'backing': evidences['backing']['evidence_id']},
    'candidates': [],
    'selected_candidate': None,
}
assert evidences == evidence_before
assert draft is not make_plan(evidences)
json.dumps(draft, ensure_ascii=False, allow_nan=False)

# Valid candidates normalize to a fresh deep copy without changing inputs.
plan_before = copy.deepcopy(plan)
evidence_before = copy.deepcopy(evidences)
normalized = validate_plan(plan, evidences)
assert normalized == plan
assert normalized is not plan
assert normalized['candidates'][0]['gestures'][0]['note_ids'] is not plan['candidates'][0]['gestures'][0]['note_ids']
normalized['candidates'][0]['gestures'][0]['note_ids'].append('l2')
normalized['candidates'][0]['intent'] = 'changed'
assert plan == plan_before and evidences == evidence_before
spaced = copy.deepcopy(plan)
spaced['candidates'][0]['intent'] = '  温柔地回应  '
spaced['candidates'][0]['gestures'][0]['direction'] = '  soft_onset  '
clean = validate_plan(spaced, evidences)
assert clean['candidates'][0]['intent'] == '温柔地回应'
assert clean['candidates'][0]['gestures'][0]['direction'] == 'soft_onset'

# Fingerprints ignore mapping order, normalize text, and bind both plan and evidence.
first = plan_fingerprint(plan, evidences)
second = plan_fingerprint(plan, evidences)
assert first == second and len(first) == 64 and all(char in '0123456789abcdef' for char in first)
reordered = {key: copy.deepcopy(plan[key]) for key in reversed(tuple(plan))}
assert plan_fingerprint(reordered, evidences) == first
changed_text = copy.deepcopy(plan)
changed_text['candidates'][0]['gestures'][0]['direction'] = 'held_onset'
assert plan_fingerprint(changed_text, evidences) != first
new_evidences = copy.deepcopy(evidences)
new_evidences['lead']['evidence_id'] = digest('lead-r2')
new_plan = copy.deepcopy(plan)
new_plan['evidence_ids']['lead'] = new_evidences['lead']['evidence_id']
assert plan_fingerprint(new_plan, new_evidences) != first
rejected(lambda: plan_fingerprint(plan, new_evidences), '已过时')

# Evidence mappings must match exactly and stale revisions remain invalid.
missing_voice = copy.deepcopy(plan)
del missing_voice['evidence_ids']['backing']
rejected_unchanged(missing_voice, evidences, '缺少声部')
unknown_voice = copy.deepcopy(plan)
unknown_voice['evidence_ids']['ghost'] = digest('ghost')
rejected_unchanged(unknown_voice, evidences, '未知声部')
rejected_unchanged(plan, new_evidences, '已过时')

# Gesture voices and notes must exist in the matching current evidence.
unknown_gesture_voice = copy.deepcopy(plan)
unknown_gesture_voice['candidates'][0]['gestures'][0]['voice'] = 'ghost'
rejected_unchanged(unknown_gesture_voice, evidences, '不存在的声部')
unknown_note = copy.deepcopy(plan)
unknown_note['candidates'][0]['gestures'][0]['note_ids'] = ['missing-note']
rejected_unchanged(unknown_note, evidences, '不存在的音符')
wrong_voice_note = copy.deepcopy(plan)
wrong_voice_note['candidates'][0]['gestures'][0]['note_ids'] = ['b1']
rejected_unchanged(wrong_voice_note, evidences, '不存在的音符')

# Candidate and in-gesture IDs are unique; selection must resolve.
duplicate_candidate = copy.deepcopy(plan)
duplicate_candidate['candidates'].append(copy.deepcopy(plan['candidates'][0]))
rejected_unchanged(duplicate_candidate, evidences, '候选 ID 必须唯一')
duplicate_note = copy.deepcopy(plan)
duplicate_note['candidates'][0]['gestures'][0]['note_ids'] = ['l1', 'l1']
rejected_unchanged(duplicate_note, evidences, '音符 ID 不能重复')
duplicate_evidence_note = copy.deepcopy(evidences)
duplicate_evidence_note['lead']['notes'].append(copy.deepcopy(duplicate_evidence_note['lead']['notes'][0]))
rejected_unchanged(plan, duplicate_evidence_note, '在同一 evidence 中重复')
invalid_select = copy.deepcopy(plan)
invalid_select['selected_candidate'] = 'missing'
rejected_unchanged(invalid_select, evidences, '必须引用当前计划中的候选')
empty_select = copy.deepcopy(plan)
empty_select['selected_candidate'] = ''
rejected_unchanged(empty_select, evidences, '安全短 ID')

# Raw parameters, pseudo-probabilities and all other unknown fields are forbidden.
for location in ('top', 'candidate', 'gesture'):
    parameterized = copy.deepcopy(plan)
    if location == 'top':
        parameterized['parameters'] = {'pitch': 0.5}
    elif location == 'candidate':
        parameterized['candidates'][0]['confidence'] = .91
    else:
        parameterized['candidates'][0]['gestures'][0]['curve'] = [[0, 1]]
    rejected_unchanged(parameterized, evidences, '未知字段')

# JSON-compatible strict types and non-empty bounded text are required.
wrong_schema = copy.deepcopy(plan)
wrong_schema['schema_version'] = True
rejected_unchanged(wrong_schema, evidences, '整数 1')
wrong_intent = copy.deepcopy(plan)
wrong_intent['candidates'][0]['intent'] = 1.5
rejected_unchanged(wrong_intent, evidences, '必须为文本')
wrong_gestures = copy.deepcopy(plan)
wrong_gestures['candidates'][0]['gestures'] = tuple(wrong_gestures['candidates'][0]['gestures'])
rejected_unchanged(wrong_gestures, evidences, '必须为列表')
wrong_note_type = copy.deepcopy(plan)
wrong_note_type['candidates'][0]['gestures'][0]['note_ids'] = [1]
rejected_unchanged(wrong_note_type, evidences, '必须为字符串')
wrong_phase = copy.deepcopy(plan)
wrong_phase['candidates'][0]['gestures'][0]['phase'] = 'attack'
rejected_unchanged(wrong_phase, evidences, "必须为 'onset'、'sustain' 或 'release'")
wrong_select_type = copy.deepcopy(plan)
wrong_select_type['selected_candidate'] = 0
rejected_unchanged(wrong_select_type, evidences, '必须为字符串')
empty_intent = copy.deepcopy(plan)
empty_intent['candidates'][0]['intent'] = '   '
rejected_unchanged(empty_intent, evidences, '不能为空')
empty_direction = copy.deepcopy(plan)
empty_direction['candidates'][0]['gestures'][0]['direction'] = ''
rejected_unchanged(empty_direction, evidences, '不能为空')

too_many = copy.deepcopy(plan)
too_many['candidates'] = []
for index in range(33):
    candidate = copy.deepcopy(plan['candidates'][0])
    candidate['id'] = 'candidate-' + str(index)
    too_many['candidates'].append(candidate)
too_many['selected_candidate'] = None
rejected_unchanged(too_many, evidences, '最多允许 32 个候选')

long_direction = copy.deepcopy(plan)
long_direction['candidates'][0]['gestures'][0]['direction'] = 'x' * 161
rejected_unchanged(long_direction, evidences, '长度不能超过')

# Evidence provides the revision authority but remains extensible by its owner.
missing_note_list = copy.deepcopy(evidences)
del missing_note_list['lead']['notes']
rejected_unchanged(plan, missing_note_list, '缺少字段')
bad_evidence_id = copy.deepcopy(evidences)
bad_evidence_id['lead']['evidence_id'] = 'not-a-sha256'
rejected_unchanged(plan, bad_evidence_id, 'SHA-256')
mismatched_evidence_voice = copy.deepcopy(evidences)
mismatched_evidence_voice['lead']['voice'] = 'backing'
rejected_unchanged(plan, mismatched_evidence_voice, '必须与 evidence 集合中的声部键一致')

print('PASS: context plan draft, revision binding, strict fields, references and fingerprints', flush=True)