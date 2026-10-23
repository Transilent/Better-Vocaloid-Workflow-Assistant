"""Pure experimental adaptation of flat Synthesizer V main groups.

This module performs no file, network, model or synthesis I/O. It validates an
explicit application/profile/evidence binding and changes only four supported
controls in selected phase intervals. Notes, pitch, retakes, references, mixer
settings, unknown controls and unaffected tracks are protected by deep-copy and
structural equality checks.

The loudness range mirrors the current project's ``expression_calibration``
implementation. It is an application safety boundary, not a claim that every
Synthesizer V release defines one universal range.
"""
from __future__ import annotations


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value



import bisect
import copy
import hashlib
import json
import math

from context_evidence import verify_evidence_fingerprint
from vocal_expression import TempoMap

SCHEMA_VERSION = 1
APPLICATION_KIND = 'vwa.context-application'
PROFILE_KIND = 'vwa.context-adapter-profile'
RECEIPT_KIND = 'vwa.context-adapter-receipt'
PHASES = frozenset(('onset', 'sustain', 'release'))
CONTROLS = {
    'loudness': {'low': -48., 'high': 12., 'step': 1.5, 'neutral': 0.},
    'tension': {'low': -1., 'high': 1., 'step': .08, 'neutral': 0.},
    'breathiness': {'low': -1., 'high': 1., 'step': .08, 'neutral': 0.},
    'voicing': {'low': 0., 'high': 1., 'step': .04, 'neutral': 1.},
}
GRID_SECONDS = .005
MAX_SELECTIONS = 256
MAX_SELECTED_NOTES = 1024
MAX_POINTS = 2_000_000


def _fail(path, message):
    raise ValueError(f'{path}：{message}')


def _fields(value, path, required, optional=()):
    if not isinstance(value, dict):
        _fail(path, '必须为对象。')
    required, optional = frozenset(required), frozenset(optional)
    unknown = [key for key in value if key not in required | optional]
    if unknown:
        _fail(path, '包含未知字段：' + ', '.join(sorted(map(repr, unknown))) + '。')
    missing = [key for key in required if key not in value]
    if missing:
        _fail(path, '缺少字段：' + ', '.join(sorted(missing)) + '。')
    return value


def _string(value, path):
    if not isinstance(value, str) or not value.strip():
        _fail(path, '必须为非空字符串。')
    return value.strip()


def _finite(value, path):
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail(path, '必须为有限数字。')
    return float(value)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _profile(value):
    value = _fields(value, 'profile',
        ('schema_version', 'kind', 'id', 'voicebank',
         'allow_unbound_voicebank', 'status', 'recipes'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        _fail('profile.schema_version', '仅支持整数 1。')
    if canonical_kind(value['kind']) != PROFILE_KIND:
        _fail('profile.kind', f'必须为 {PROFILE_KIND!r}。')
    _string(value['id'], 'profile.id')
    _string(value['voicebank'], 'profile.voicebank')
    if type(value['allow_unbound_voicebank']) is not bool:
        _fail('profile.allow_unbound_voicebank', '必须为布尔值。')
    if value['status'] != 'experimental_unrendered':
        _fail('profile.status', "必须为 'experimental_unrendered'。")
    recipes = value['recipes']
    if not isinstance(recipes, dict) or not recipes:
        _fail('profile.recipes', '必须为非空对象。')
    result = {}
    for recipe_id, raw in recipes.items():
        path = f'profile.recipes[{recipe_id!r}]'
        _string(recipe_id, path)
        raw = _fields(raw, path, ('phase', 'controls'))
        if raw['phase'] not in PHASES:
            _fail(path + '.phase', "必须为 onset、sustain 或 release。")
        controls = raw['controls']
        if not isinstance(controls, dict) or not controls:
            _fail(path + '.controls', '必须为非空对象。')
        unknown = set(controls) - set(CONTROLS)
        if unknown:
            _fail(path + '.controls',
                  '包含不支持的通道：' + ', '.join(sorted(unknown)) + '。')
        normalized = {}
        for name, delta in controls.items():
            delta = _finite(delta, f'{path}.controls.{name}')
            limit = CONTROLS[name]['step']
            if abs(delta) > limit + 1e-12:
                _fail(f'{path}.controls.{name}',
                      f'单次变化绝对值不得超过 {limit}。')
            if delta == 0:
                _fail(f'{path}.controls.{name}', '零增量不能形成可审阅变化。')
            normalized[name] = delta
        result[recipe_id] = {'phase': raw['phase'], 'controls': normalized}
    return result


def _evidence_index(evidences, application):
    if not isinstance(evidences, dict) or not evidences:
        _fail('evidences', '必须为非空对象。')
    ids = application['evidence_ids']
    if not isinstance(ids, dict) or set(ids) != set(evidences):
        _fail('application.evidence_ids', '必须精确映射当前 evidence 声部。')
    for voice, evidence in evidences.items():
        _string(voice, 'evidences 的声部键')
        actual = verify_evidence_fingerprint(evidence)
        if actual != ids[voice]:
            _fail(f'application.evidence_ids[{voice!r}]', 'evidence_id 已过时。')
        if evidence.get('voice') != voice:
            _fail(f'evidences[{voice!r}].voice', '必须与声部键一致。')
        notes = evidence.get('notes')
        if not isinstance(notes, list):
            _fail(f'evidences[{voice!r}].notes', '必须为列表。')
        seen = set()
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                _fail(f'evidences[{voice!r}].notes[{index}]', '必须为对象。')
            note_id = note.get('id')
            if not isinstance(note_id, str) or not note_id:
                _fail(f'evidences[{voice!r}].notes[{index}].id', '必须为非空字符串。')
            if note_id in seen:
                _fail(f'evidences[{voice!r}].notes[{index}].id', '音符 ID 不能重复。')
            seen.add(note_id)
        if not isinstance(evidence.get('text'), dict):
            _fail(f'evidences[{voice!r}].text', '必须为对象。')
        if not isinstance(evidence['text'].get('original'), str):
            _fail(f'evidences[{voice!r}].text.original', '必须为字符串。')
    return evidences


def _application(value, evidences):
    value = _fields(value, 'application',
        ('schema_version', 'kind', 'evidence_ids', 'track_bindings', 'selections'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        _fail('application.schema_version', '仅支持整数 1。')
    if canonical_kind(value['kind']) != APPLICATION_KIND:
        _fail('application.kind', f'必须为 {APPLICATION_KIND!r}。')
    _evidence_index(evidences, value)
    bindings = value['track_bindings']
    if not isinstance(bindings, dict) or set(bindings) != set(evidences):
        _fail('application.track_bindings', '必须精确映射每个 evidence 声部。')
    normalized_bindings = {}
    for voice, index in bindings.items():
        if type(index) is not int or index < 0:
            _fail(f'application.track_bindings[{voice!r}]', '必须为非负整数。')
        normalized_bindings[voice] = index
    raw_items = value['selections']
    if not isinstance(raw_items, list) or not raw_items:
        _fail('application.selections', '必须为非空列表。')
    if len(raw_items) > MAX_SELECTIONS:
        _fail('application.selections', f'最多 {MAX_SELECTIONS} 项。')
    seen_ids, result = set(), []
    for index, raw in enumerate(raw_items):
        path = f'application.selections[{index}]'
        raw = _fields(raw, path,
            ('id', 'voice', 'note_ids', 'phase', 'recipe', 'amount',
             'rationale', 'source_span', 'source_quote'))
        selection_id = _string(raw['id'], path + '.id')
        if selection_id in seen_ids:
            _fail(path + '.id', '选择 ID 必须唯一。')
        seen_ids.add(selection_id)
        voice = _string(raw['voice'], path + '.voice')
        if voice not in evidences:
            _fail(path + '.voice', '引用了不存在的证据声部。')
        note_ids = raw['note_ids']
        if (not isinstance(note_ids, list) or not note_ids
                or len(note_ids) > MAX_SELECTED_NOTES):
            _fail(path + '.note_ids', '必须为非空且不超限的列表。')
        if any(not isinstance(note_id, str) or not note_id for note_id in note_ids):
            _fail(path + '.note_ids', '音符 ID 必须为非空字符串。')
        if len(set(note_ids)) != len(note_ids):
            _fail(path + '.note_ids', '不能重复引用同一音符。')
        if raw['phase'] not in PHASES:
            _fail(path + '.phase', "必须为 onset、sustain 或 release。")
        amount = _finite(raw['amount'], path + '.amount')
        if not 0 < amount <= 1:
            _fail(path + '.amount', '必须在 0 到 1 之间且产生变化。')
        recipe = _string(raw['recipe'], path + '.recipe')
        rationale = _string(raw['rationale'], path + '.rationale')
        text = evidences[voice]['text']['original']
        span = raw['source_span']
        if (not isinstance(span, list) or len(span) != 2
                or any(type(item) is not int for item in span)):
            _fail(path + '.source_span', '必须为两个整数。')
        start, end = span
        if start < 0 or end <= start or end > len(text):
            _fail(path + '.source_span', '必须为原文内非空半开区间。')
        quote = raw['source_quote']
        if not isinstance(quote, str) or quote != text[start:end]:
            _fail(path + '.source_quote', '必须与 source_span 原文逐字一致。')
        result.append({
            'id': selection_id, 'voice': voice, 'note_ids': list(note_ids),
            'phase': raw['phase'], 'recipe': recipe, 'amount': amount,
            'rationale': rationale, 'source_span': [start, end],
            'source_quote': quote,
        })
    return normalized_bindings, result
def _linear_curve(curve, name, path):
    if not isinstance(curve, dict):
        _fail(path, '必须为对象。')
    unknown = set(curve) - {'mode', 'points'}
    if unknown:
        _fail(path, '曲线包含未知字段：' + ', '.join(sorted(unknown)) + '。')
    points = curve.get('points')
    if not isinstance(points, list) or len(points) % 2:
        _fail(path + '.points', '必须为横纵坐标交替列表。')
    # Empty automation means the control's neutral value. It has no spline to
    # replace and is valid even if an older editor retained a mode field.
    if not points:
        return []
    if curve.get('mode', 'cubic') != 'linear':
        _fail(path, '所选曲线必须为 linear；拒绝替换非线性曲线。')
    if (any(type(value) is not int for value in points[::2])
            or any(type(value) not in (int, float)
                   or not math.isfinite(value) for value in points[1::2])
            or any(a >= b for a, b in zip(points[::2], points[2::2]))):
        _fail(path + '.points', '必须为升序整数横坐标与有限数值交替列表。')
    low, high = CONTROLS[name]['low'], CONTROLS[name]['high']
    if any(not low <= value <= high for value in points[1::2]):
        _fail(path + '.points', '原值超出当前项目安全范围。')
    return points


def _sample(xs, ys, position):
    right = bisect.bisect_right(xs, position)
    if right == 0:
        return float(ys[0])
    if right == len(xs):
        return float(ys[-1])
    left = right - 1
    return float(ys[left] + (position - xs[left]) / (xs[right] - xs[left])
                 * (ys[right] - ys[left]))


def _phase_window(start, end, phase):
    """Return the single audio-second window for one grouped selection."""
    duration = end - start
    if duration <= 0:
        _fail('selection', '音频时段必须大于零。')
    if phase == 'onset':
        return start, min(end, start + .160)
    if phase == 'release':
        return max(start, end - .200), end
    # Sustain occupies the middle. The edge exclusions use the requested
    # onset/release limits, adapting for notes shorter than those limits.
    left = min(.160, duration * .20)
    right = min(.200, duration * .20)
    window_start, window_end = start + left, end - right
    if window_end <= window_start:
        window_start, window_end = start + duration*.25, end - duration*.25
    return window_start, window_end


def _envelope(seconds, window_start, window_end):
    """Single smooth 0 -> 1 -> 0 lobe; endpoints have zero delta."""
    if seconds <= window_start or seconds >= window_end:
        return 0.
    ratio = (seconds - window_start) / (window_end - window_start)
    return math.sin(math.pi * ratio) ** 2


def _track_facts(project, evidences, bindings, profile, clock):
    targets = {}
    for voice, track_index in bindings.items():
        if track_index >= len(project['tracks']):
            _fail(f'application.track_bindings[{voice!r}]', '轨道索引越界。')
        track = project['tracks'][track_index]
        if not isinstance(track, dict):
            _fail(f'project.tracks[{track_index}]', '必须为对象。')
        ref, group = track.get('mainRef'), track.get('mainGroup')
        if not isinstance(ref, dict) or not isinstance(group, dict):
            _fail(f'project.tracks[{track_index}]', '缺少 mainRef 或 mainGroup。')
        if (ref.get('isInstrumental') or track.get('groups')
                or ref.get('blickAbsoluteBegin', 0) != 0
                or ref.get('blickAbsoluteEnd', -1) != -1):
            _fail(f'project.tracks[{track_index}]',
                  '仅支持 flat、未裁剪的演唱 mainGroup。')
        database = ref.get('database')
        actual_bank = database.get('name') if isinstance(database, dict) else None
        if actual_bank is None or actual_bank == '':
            if not profile['allow_unbound_voicebank']:
                _fail(f'project.tracks[{track_index}].mainRef.database.name',
                      '未绑定声库，且 profile 不允许未绑定声库。')
        elif actual_bank != profile['voicebank']:
            _fail(f'project.tracks[{track_index}].mainRef.database.name',
                  '与 profile.voicebank 不一致。')
        notes = group.get('notes')
        if not isinstance(notes, list):
            _fail(f'project.tracks[{track_index}].mainGroup.notes', '必须为列表。')
        offset, pitch_offset = ref.get('blickOffset', 0), ref.get('pitchOffset', 0)
        if type(offset) is not int or type(pitch_offset) is not int:
            _fail(f'project.tracks[{track_index}].mainRef', '偏移必须为整数。')
        raw_notes = list(enumerate(notes))
        for note_index, note in raw_notes:
            if (not isinstance(note, dict)
                    or any(type(note.get(key)) is not int
                           for key in ('onset', 'duration', 'pitch'))
                    or note['duration'] <= 0):
                _fail(f'project.tracks[{track_index}].mainGroup.notes[{note_index}]',
                      'onset、duration、pitch 无效。')
        # Stable original indexes break exact-time ties. IDs are validation
        # references, never an ordering key, so same-position notes cannot be
        # rebound merely because their content-derived IDs sort differently.
        ordered = sorted(raw_notes, key=lambda row: (
            row[1]['onset'], row[1]['duration'], row[1]['pitch'], row[0]))
        expected = [row for _, row in sorted(
            enumerate(evidences[voice]['notes']),
            key=lambda pair: (pair[1]['start'], pair[1]['end'],
                              pair[1]['pitch'], pair[0]))]
        if len(ordered) != len(expected):
            _fail(f'evidences[{voice!r}].notes', '音符数量与当前 SVP 不一致。')
        facts = []
        for (note_index, note), evidence_note in zip(ordered, expected):
            actual = {
                'lyric': note.get('lyrics', ''),
                'start': clock.to_seconds(note['onset'] + offset),
                'end': clock.to_seconds(note['onset'] + note['duration'] + offset),
                'pitch': note['pitch'] + pitch_offset,
            }
            for field in ('lyric', 'start', 'end', 'pitch'):
                if actual[field] != evidence_note[field]:
                    _fail(f'evidences[{voice!r}].notes[{evidence_note.get("id")!r}]',
                          f'与当前 SVP 的 {field} 不一致。')
            facts.append({'index': note_index, 'note': note, 'evidence': evidence_note,
                          'start': actual['start'], 'end': actual['end']})
        targets[voice] = {'index': track_index, 'track': track,
                          'offset': offset, 'notes': facts}
    if len({row['index'] for row in targets.values()}) != len(targets):
        _fail('application.track_bindings', '不同声部不能绑定到同一轨道。')
    return targets


def _selected_effects(selections, recipes, evidences, targets):
    unit_rows = {
        voice: {unit['id']: unit for unit in evidence.get('text_units', [])}
        for voice, evidence in evidences.items()
    }
    result, owners = [], {}
    for item in selections:
        definition = recipes.get(item['recipe'])
        path = f'application.selections[{item["id"]!r}]'
        if definition is None:
            _fail(path + '.recipe', '未在 profile.recipes 中定义。')
        if definition['phase'] != item['phase']:
            _fail(path + '.phase', '与 recipe.phase 不一致。')
        facts = []
        for note_id in item['note_ids']:
            fact = next((row for row in targets[item['voice']]['notes']
                         if row['evidence']['id'] == note_id), None)
            if fact is None:
                _fail(path + '.note_ids', '引用了当前 evidence 中不存在的音符。')
            evidence_note = fact['evidence']
            if not evidence_note.get('text_unit_ids'):
                _fail(path + '.note_ids', '未知文本映射禁止修改对应音符。')
            spans = []
            for unit_id in evidence_note['text_unit_ids']:
                unit = unit_rows[item['voice']].get(unit_id)
                if unit is None:
                    _fail(path + '.note_ids', '文本单元引用无效。')
                spans.append(unit['span'])
            start, end = item['source_span']
            if not spans or any(left < start or right > end
                                for left, right in spans):
                _fail(path + '.source_span', '未完整包含所选音符的文本单元。')
            key = (item['voice'], note_id)
            if key in owners:
                _fail(path + '.note_ids',
                      f'与选择 {owners[key]!r} 重叠；不静默平均或合并。')
            owners[key] = item['id']
            facts.append(fact)
        # An explicit selection gets exactly one window across all its notes.
        facts.sort(key=lambda row: (row['start'], row['end'], row['index']))
        if any(right['start'] < left['end'] - 1e-12
               for left, right in zip(facts, facts[1:])):
            _fail(path + '.note_ids', '包含重叠音符；请拆成明确选择。')
        if any(right['start'] - left['end'] > .150001
               for left, right in zip(facts, facts[1:])):
            _fail(path + '.note_ids', '音符间真实空隙超过 150 ms；请拆成明确选择。')
        row = {'item': item, 'definition': definition, 'facts': facts,
               'start': facts[0]['start'], 'end': facts[-1]['end']}
        window_start, window_end = _phase_window(row['start'], row['end'], item['phase'])
        selected_ids = set(item['note_ids'])
        for other in targets[item['voice']]['notes']:
            if (other['evidence']['id'] not in selected_ids
                    and other['start'] < window_end - 1e-12
                    and window_start < other['end'] - 1e-12):
                _fail(path + '.note_ids', '相位窗口覆盖同轨未选音符；不能只给重叠音符之一调参。')
        for prior in result:
            if (prior['item']['voice'] == item['voice']
                    and row['start'] < prior['end'] - 1e-12
                    and prior['start'] < row['end'] - 1e-12):
                _fail(path + '.note_ids',
                      f'与选择 {prior["item"]["id"]!r} 的时间区间重叠；'
                      '拒绝静默平均。')
        result.append(row)
    return result


def _protected(project, removable):
    snapshot = copy.deepcopy(project)
    for track_index, name in removable:
        group = snapshot['tracks'][track_index].get('mainGroup', {})
        parameters = group.get('parameters')
        if isinstance(parameters, dict):
            parameters.pop(name, None)
    return _canonical(snapshot)


def adapt_svp(project, evidences, application, *, profile):
    """Apply an explicit experiment and return ``(deepcopy, receipt)``.

    The function never mutates its inputs and writes no files. Every selected
    control curve remains linear. Existing control-point positions are retained;
    a 5 ms audio-time grid adds samples only within an audio-second phase window.
    One explicit selection contributes one smooth lobe, not one onset per note.
    """
    if not isinstance(project, dict):
        _fail('project', '必须为对象。')
    if not isinstance(project.get('tracks'), list) or not project['tracks']:
        _fail('project.tracks', '必须为非空列表。')
    time = project.get('time')
    if not isinstance(time, dict):
        _fail('project.time', '必须为对象。')
    recipes = _profile(profile)
    bindings, selections = _application(application, evidences)
    try:
        clock = TempoMap(time.get('tempo'))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('project.time.tempo：速度表无效。') from error

    targets = _track_facts(project, evidences, bindings, profile, clock)
    selected = _selected_effects(selections, recipes, evidences, targets)
    output = copy.deepcopy(project)
    effects = []
    allowed = set()
    for group_selection in selected:
        item, definition = group_selection['item'], group_selection['definition']
        target = targets[item['voice']]
        track_index = target['index']
        output_track = output['tracks'][track_index]
        audio_start, audio_end = group_selection['start'], group_selection['end']
        window_start, window_end = _phase_window(audio_start, audio_end, item['phase'])
        sample_seconds = []
        count = int(math.ceil((window_end - window_start) / GRID_SECONDS))
        if count > MAX_POINTS:
            _fail(f'application.selections[{item["id"]!r}]', '选中区域过长。')
        for step in range(count + 1):
            sample_seconds.append(min(window_end, window_start + step * GRID_SECONDS))
        sample_seconds.append(window_end)
        if window_start <= audio_start:
            sample_seconds.append(audio_start)
        if window_end >= audio_end:
            sample_seconds.append(audio_end)
        sample_positions = sorted({int(clock.to_position(seconds) - target['offset'])
                                   for seconds in sample_seconds})
        for name, delta in definition['controls'].items():
            allowed.add((track_index, name))
            group = output_track.setdefault('mainGroup', {})
            parameters = group.setdefault('parameters', {})
            original_curve = parameters.get(name)
            if original_curve is None:
                neutral = CONTROLS[name]['neutral']
                points = []
            else:
                points = _linear_curve(
                    original_curve, name,
                    f'project.tracks[{track_index}].mainGroup.parameters.{name}')
            xs, ys = points[::2], points[1::2]
            neutral = CONTROLS[name]['neutral']
            original_xs = list(xs)
            dense_xs = sorted(set(original_xs + sample_positions))
            baseline, values = [], []
            low, high = CONTROLS[name]['low'], CONTROLS[name]['high']
            for position in dense_xs:
                original_value = _sample(xs, ys, position) if xs else neutral
                seconds = clock.to_seconds(position + target['offset'])
                weight = _envelope(seconds, window_start, window_end)
                value = original_value + delta * item['amount'] * weight
                if not low - 1e-12 <= value <= high + 1e-12:
                    _fail(f'application.selections[{item["id"]!r}]',
                          f'{name} 结果越界；拒绝静默截断。')
                baseline.append([position, original_value])
                values.extend([position, value])
            parameters[name] = {'mode': 'linear', 'points': values}
            if not set(original_xs).issubset(set(values[::2])):
                raise AssertionError('原始曲线横坐标未完整保留。')
            effects.append({
                'selection_id': item['id'], 'voice': item['voice'],
                'track_index': track_index, 'note_ids': list(item['note_ids']),
                'phase': item['phase'], 'recipe': item['recipe'],
                'control': name, 'delta_at_full_amount': delta,
                'amount': item['amount'], 'effective_delta': delta * item['amount'],
                'start_seconds': audio_start, 'end_seconds': audio_end,
                'window_start_seconds': window_start,
                'window_end_seconds': window_end,
                'window_duration_seconds': window_end - window_start,
                'duration_seconds': audio_end - audio_start,
                'point_count': len(values) // 2,
                'original_x_preserved': True,
                'baseline': baseline,
                'selected_values_changed': True,
                'outside_selected_region_unchanged': True,
                'group_count': 1,
            })
    # Preserve both timing order and original serialized order.
    note_order = []
    for voice, row in targets.items():
        for fact in sorted(row['notes'], key=lambda r: (r['start'], r['end'], r['index'])):
            note_order.append(fact['index'])
    expected = _protected(project, allowed)
    if _protected(output, allowed) != expected:
        raise AssertionError('适配器意外修改了未选内容。')
    target_indices = {row['index'] for row in targets.values()}
    for index, before in enumerate(project['tracks']):
        after = output['tracks'][index]
        if index not in target_indices:
            if _canonical(before) != _canonical(after):
                raise AssertionError('适配器意外修改了非目标轨道。')
            continue
        before_group = before.get('mainGroup', {})
        after_group = after.get('mainGroup', {})
        if _canonical(before_group.get('notes')) != _canonical(after_group.get('notes')):
            raise AssertionError('适配器意外修改了音符。')
        before_ref = {key: value for key, value in before.get('mainRef', {}).items()}
        after_ref = {key: value for key, value in after.get('mainRef', {}).items()}
        if _canonical(before_ref) != _canonical(after_ref):
            raise AssertionError('适配器意外修改了音高、retake 或引用。')
        before_params = before_group.get('parameters', {})
        after_params = after_group.get('parameters', {})
        for name in set(before_params) | set(after_params):
            if (index, name) not in allowed:
                if _canonical(before_params.get(name)) != _canonical(after_params.get(name)):
                    raise AssertionError('适配器意外修改了未选控制通道。')
        for key in set(before_group) | set(after_group):
            if key not in ('parameters', 'notes') and _canonical(before_group.get(key)) != _canonical(after_group.get(key)):
                raise AssertionError('适配器意外修改了 mainGroup 的其他内容。'
                                     )
        for key in set(before) | set(after):
            if key not in ('mainGroup', 'mainRef') and _canonical(before.get(key)) != _canonical(after.get(key)):
                raise AssertionError('适配器意外修改了轨道的其他内容。')

    receipt = {
        'schema_version': 1, 'kind': RECEIPT_KIND,
        'status': 'experimental_unrendered', 'trial': True,
        'application_id': _digest(application),
        'profile_id': profile['id'], 'profile_sha256': _digest(profile),
        'voicebank': profile['voicebank'], 'audio_rendered': False,
        'acoustic_evaluation': 'not_run', 'calibration_status': 'unrendered_experiment',
        'input_sha256': _digest(project), 'output_sha256': _digest(output),
        'input_evidence_ids': copy.deepcopy(application['evidence_ids']),
        'effects': effects,
        'protections': {
            'notes': True, 'pitch': True, 'retakes': True,
            'reference': True, 'mixer': True, 'unselected_controls': True,
            'unselected_regions': True, 'unrelated_tracks': True,
            'original_x_positions': True, 'long_note_detail': True,
        },
        'notes': ('选中区域的基线值与显式增量见 effects[].baseline；'
                  '区域外内容保持原样。'),
        'not_implemented': [
            'synthesis', 'audio_render', 'acoustic_measurement',
            'automatic_emotion_mapping', 'nonlinear_curve_replacement',
        ],
    }
    return output, receipt


__all__ = ('adapt_svp', 'CONTROLS', 'APPLICATION_KIND', 'PROFILE_KIND')
