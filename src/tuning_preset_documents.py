"""Portable tuning documents and explicit, side-effect-free application plans.

The adapter owns no Qt, filesystem, network or acoustic work. It converts the
current tuning controls into a media-free preset document and freezes the local
project/bindings needed by the existing tuning transaction. Validation rebuilds
the plan from those frozen values; it never treats stored hashes as proof that a
file exists or still has the same contents.
"""
from __future__ import annotations

import copy
import json
import uuid

from preset_documents import PresetError, decode_document, document_digest, encode_document, validate_document
from style_catalog import TECHNIQUE_IDS, VISIBLE_STYLES
from tuning_engine import DEFAULTS, settings

PLAN_SCHEMA = 'vwa.tuning-preset-plan/v1'
PRESET_VERSION = '0.1.0'
REFERENCE_VOICES = frozenset({'lead', 'backing', 'vocals'})
SOURCE_CAPABILITIES = {
    'reference': 'tuning.reference-audio',
    'calibrated': 'tuning.calibrated',
    'retained': 'tuning.retained',
}

# New source capabilities live here. Every capability emitted by make_document
# is understood by this adapter; callers must add an implementation before they
# admit another execution capability.
SUPPORTED_CAPABILITIES = frozenset({
    'tuning.svp-main-group',
    'tuning.reference-audio',
    'tuning.calibrated',
    'tuning.retained',
    *(f'style.builtin.{name}' for name in VISIBLE_STYLES if name != 'none'),
    *(f'technique.builtin.{name}' for name in TECHNIQUE_IDS if name != 'none'),
})

_BINDING_KEYS = frozenset({
    'vocal_tracks', 'reference_audio', 'calibration_profile', 'retained_project',
    'confirm_candidate_baseline',
})
_PLAN_KEYS = frozenset({
    'schema', 'document', 'document_text', 'preset', 'input_sha256', 'project',
    'bindings', 'controls', 'options', 'missing', 'changes',
})
_PRESET_KEYS = frozenset({'id', 'version', 'sha256', 'canonical_sha256', 'name'})
_PROJECT_KEYS = frozenset({'path', 'sha256', 'candidate_origin', 'tracks'})


def _invalid(code, message, **details):
    raise PresetError(code, message, **details)


def _canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise PresetError('invalid_document', '调音计划包含无法编码的数据。') from error


def _valid_sha(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdef' for character in value))


def _canonical_style(name):
    if name not in VISIBLE_STYLES:
        raise PresetError('unsupported_reference', '仅支持内置 canonical 演唱风格。', reference=name)
    return f'builtin:{name}'


def _canonical_technique(name):
    if name not in TECHNIQUE_IDS:
        raise PresetError('unsupported_reference', '仅支持内置 canonical 唱腔。', reference=name)
    return f'builtin:{name}'


def _builtin_name(reference):
    if isinstance(reference, str) and reference.startswith('builtin:'):
        return reference[8:]
    return None


def _validated_current_options(options):
    """Use tuning_engine.settings while allowing an unbound source to stay unbound."""
    if not isinstance(options, dict):
        raise PresetError('invalid_document', '调音参数必须是对象。')
    if 'confirm_candidate_baseline' in options:
        _invalid('invalid_document', '候选基线确认属于本地绑定，不能写入预设。')
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        _invalid('invalid_document', '未知调音参数：' + ','.join(sorted(map(str, unknown))))

    regions = options.get('style_regions', DEFAULTS['style_regions'])
    if not isinstance(regions, list):
        _invalid('invalid_document', '风格时间片段必须为列表。')
    if regions:
        _invalid('unsupported_style_regions', '调音预设不能导出时间片段。')

    mode = options.get('expression_mode', DEFAULTS['expression_mode'])
    style_name = options.get('performance_style', DEFAULTS['performance_style'])
    technique_name = options.get('style_technique', DEFAULTS['style_technique'])
    if style_name not in VISIBLE_STYLES:
        raise PresetError('unsupported_reference', '仅支持内置 canonical 演唱风格。', reference=style_name)
    if technique_name not in TECHNIQUE_IDS:
        raise PresetError('unsupported_reference', '仅支持内置 canonical 唱腔。', reference=technique_name)

    value = copy.deepcopy(options)
    value['style_regions'] = []
    # settings() requires a truthful source path for calibrated/retained modes.
    # A preset may be saved before that local binding exists, so validation uses
    # placeholders only on this detached settings input. They never reach a
    # document or returned engine options.
    if mode == 'calibrated':
        profile = value.get('reference_calibration')
        if profile is None or isinstance(profile, str) and not profile.strip():
            value['reference_calibration'] = 'bound:calibration_profile'
        value['retained_project'] = None
        value['retained_sources'] = []
    elif mode == 'retained':
        value['reference_calibration'] = None
        value['reference_sources'] = []
        retained_project = value.get('retained_project')
        if ((retained_project is None or isinstance(retained_project, str) and not retained_project.strip())
                and not value.get('retained_sources')):
            value['retained_project'] = 'bound:retained_project'
    else:
        value['reference_calibration'] = None
        value['retained_project'] = None
        value['retained_sources'] = []
        if mode == 'preserve':
            value['reference_sources'] = []
    try:
        return settings(value)
    except (TypeError, ValueError, KeyError) as error:
        raise PresetError('invalid_document', str(error)) from error


def make_document(options, name, *, identifier=None):
    """Create a new user document containing only portable tuning intent."""
    normalized = _validated_current_options(options)
    if identifier is not None:
        if not isinstance(identifier, str) or not identifier.startswith('user.'):
            _invalid('invalid_identifier', '新调音预设必须使用全新的 user 标识。')
    payload = {
        'expression_mode': normalized['expression_mode'],
        'style': {
            'ref': _canonical_style(normalized['performance_style']),
            'strength': normalized['style_strength'],
            'pitch_enabled': normalized['style_pitch'],
            'detail': copy.deepcopy(normalized['style_detail']),
        },
        'technique': {
            'ref': _canonical_technique(normalized['style_technique']),
            'strength': normalized['technique_strength'],
            'pitch_enabled': normalized['technique_pitch'],
        },
        'expression_density': normalized['expression_density'],
    }
    if normalized['expression_mode'] == 'reference':
        payload['reference_controls'] = [
            name for name in ('dynamics', 'tension', 'breathiness', 'voicing')
            if normalized['reference_' + name]
        ]
        payload['reference_strength'] = normalized['dynamics_strength']
    elif normalized['expression_mode'] == 'calibrated':
        payload['reference_strength'] = normalized['dynamics_strength']

    slots = [{'id': 'target_vocals', 'kind': 'vocal_tracks', 'required': True}]
    if normalized['expression_mode'] in ('reference', 'calibrated'):
        slots.append({'id': 'reference_audio', 'kind': 'reference_audio', 'required': True})
    if normalized['expression_mode'] == 'calibrated':
        slots.append({'id': 'calibration_profile', 'kind': 'calibration_profile', 'required': True})
    elif normalized['expression_mode'] == 'retained':
        slots.append({'id': 'retained_project', 'kind': 'retained_project', 'required': True})

    capabilities = ['tuning.svp-main-group']
    source = SOURCE_CAPABILITIES.get(normalized['expression_mode'])
    if source:
        capabilities.append(source)
    if normalized['performance_style'] != 'none':
        capabilities.append('style.builtin.' + normalized['performance_style'])
    if normalized['style_technique'] != 'none':
        capabilities.append('technique.builtin.' + normalized['style_technique'])

    document = {
        'format': 'vwa.preset',
        'format_version': 1,
        'id': identifier or 'user.' + str(uuid.uuid4()),
        'type': 'tuning',
        'version': PRESET_VERSION,
        'metadata': {'name': name},
        'requires': {
            'preset_api': 1,
            'engine_contract': 'tuning-pipeline/v1',
            'capabilities': capabilities,
        },
        'binding_slots': slots,
        'payload': payload,
    }
    return validate_document(document)


def _verified_document(document):
    document = validate_document(document, allow_builtin=True)
    if document.get('type') != 'tuning' or document.get('requires', {}).get('engine_contract') != 'tuning-pipeline/v1':
        raise PresetError('unsupported_preset', '请选择调音预设。')
    requires = document['requires']
    unsupported = set(requires['capabilities']) - SUPPORTED_CAPABILITIES
    if unsupported:
        raise PresetError('unsupported_capability', '当前调音流程不支持此预设要求的能力。',
                          capabilities=sorted(unsupported))
    if requires.get('dependencies'):
        raise PresetError('unsupported_dependency', '当前调音流程不支持外部依赖。')
    for layer, allowed in (('style', VISIBLE_STYLES), ('technique', TECHNIQUE_IDS)):
        reference = document['payload'][layer]['ref']
        name = _builtin_name(reference)
        if name not in allowed:
            raise PresetError('unsupported_reference', '当前调音流程不支持此外部引用。',
                              reference=copy.deepcopy(reference))
    return document


def _exact_raw_document(document, raw_document):
    if raw_document is None:
        raw = encode_document(document, allow_builtin=True)
    elif isinstance(raw_document, str):
        try:
            raw = raw_document.encode('utf-8')
        except UnicodeError as error:
            raise PresetError('invalid_document', '调音预设原文不是有效 UTF-8。') from error
    elif isinstance(raw_document, bytes):
        raw = bytes(raw_document)
    else:
        raise PresetError('invalid_document', '调音预设原文须为 UTF-8 字符串或字节。')
    parsed = decode_document(raw, allow_builtin=True)
    if parsed != document:
        raise PresetError('raw_document_mismatch', '库保存原文与调音文档不一致。')
    try:
        text = raw.decode('utf-8')
    except UnicodeError as error:
        raise PresetError('invalid_document', '调音预设原文不是有效 UTF-8。') from error
    return raw, text, document_digest(raw)


def _project_parts(project):
    if project is None:
        return None
    if not isinstance(project, dict):
        raise PresetError('invalid_target', '当前工程快照无效。')
    path = project.get('path')
    sha256 = project.get('sha256')
    tracks = project.get('tracks')
    if not isinstance(path, str) or not path:
        raise PresetError('invalid_target', '当前工程缺少路径身份。')
    if not _valid_sha(sha256):
        raise PresetError('invalid_target', '当前工程 SHA-256 无效。')
    if not isinstance(tracks, list):
        raise PresetError('invalid_target', '当前工程轨道列表无效。')
    mapping = {}
    for track in tracks:
        if not isinstance(track, dict) or type(track.get('track_index')) is not int or track['track_index'] < 0:
            raise PresetError('invalid_target', '当前工程包含无效轨道索引。')
        index = track['track_index']
        if index in mapping or type(track.get('instrumental')) is not bool:
            raise PresetError('invalid_target', '当前工程轨道索引重复或类型无效。')
        _canonical_bytes(track)
        mapping[index] = copy.deepcopy(track)
    candidate = project.get('candidate_origin')
    if candidate is not None:
        _canonical_bytes(candidate)
    return {
        'path': path,
        'sha256': sha256,
        'candidate_origin': copy.deepcopy(candidate),
        'tracks': mapping,
    }


def _project_reference(project, selected):
    if project is None:
        return None
    tracks = []
    if selected is not None:
        tracks = [copy.deepcopy(project['tracks'][index]) for index in sorted(selected)]
    return {
        'path': project['path'],
        'sha256': project['sha256'],
        'candidate_origin': copy.deepcopy(project['candidate_origin']),
        'tracks': tracks,
    }


def _selected_tracks(bindings, project, missing):
    if project is None:
        missing.append('尚未载入目标工程。')
    if 'vocal_tracks' not in bindings or bindings['vocal_tracks'] is None:
        missing.append('请选择目标演唱轨。')
        return None
    values = bindings['vocal_tracks']
    if not isinstance(values, list):
        raise PresetError('invalid_binding', '目标演唱轨必须为列表。')
    if not values:
        missing.append('请选择目标演唱轨。')
        return None
    selected = []
    for index in values:
        if type(index) is not int or index < 0:
            raise PresetError('invalid_binding', '目标演唱轨索引无效。')
        if project is not None:
            track = project['tracks'].get(index)
            if track is None:
                raise PresetError('invalid_target', f'目标轨道 {index} 不存在。')
            if track['instrumental']:
                raise PresetError('invalid_target', f'目标轨道 {index} 是伴奏，不能调音。')
        if index in selected:
            raise PresetError('invalid_binding', '目标演唱轨不能重复。')
        selected.append(index)
    return selected


def _reference_rows(bindings, selected, missing):
    rows = bindings.get('reference_audio')
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise PresetError('invalid_binding', '参考音频绑定必须为列表。')
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'track_index', 'voice', 'path'}:
            raise PresetError('invalid_binding', '参考音频绑定字段无效。')
        index, voice, path = row['track_index'], row['voice'], row['path']
        if type(index) is not int or index < 0:
            raise PresetError('invalid_binding', '参考音频轨道索引无效。')
        if index in seen:
            raise PresetError('invalid_binding', '每条目标轨只能绑定一个参考音频。')
        if voice not in REFERENCE_VOICES and voice not in (None, ''):
            raise PresetError('invalid_binding', '参考音频声部无效。')
        if path is None or path == '':
            path = ''
        elif not isinstance(path, str):
            raise PresetError('invalid_binding', '参考音频路径必须为字符串。')
        seen.add(index)
        normalized.append({'track_index': index, 'voice': voice, 'path': path})
    if selected is None:
        return sorted(normalized, key=lambda item: item['track_index'])
    selected_set = set(selected)
    if not seen <= selected_set:
        raise PresetError('invalid_binding', '参考音频绑定包含未选中的轨道。')
    available = {
        row['track_index'] for row in normalized
        if row['voice'] in REFERENCE_VOICES and row['path'].strip()
    }
    for index in selected:
        if index not in available:
            missing.append(f'轨道 {index + 1} 缺少参考音频。')
    order = {index: position for position, index in enumerate(selected)}
    return sorted(normalized, key=lambda item: order[item['track_index']])


def _retained_rows(bindings, selected, missing):
    rows = bindings.get('retained_project')
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise PresetError('invalid_binding', '沿用工程绑定必须为列表。')
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'track_index', 'project', 'source_track_index'}:
            raise PresetError('invalid_binding', '沿用工程绑定字段无效。')
        index, source = row['track_index'], row['source_track_index']
        path = row['project']
        if type(index) is not int or index < 0:
            raise PresetError('invalid_binding', '沿用工程目标轨索引无效。')
        if source is not None and (type(source) is not int or source < -1):
            raise PresetError('invalid_binding', '沿用工程源轨索引无效。')
        if index in seen:
            raise PresetError('invalid_binding', '每条目标轨只能绑定一个沿用工程声部。')
        if path is None or path == '':
            path = ''
        elif not isinstance(path, str):
            raise PresetError('invalid_binding', '沿用工程路径必须为字符串。')
        seen.add(index)
        normalized.append({'track_index': index, 'project': path, 'source_track_index': source})
    if selected is None:
        return sorted(normalized, key=lambda item: item['track_index'])
    selected_set = set(selected)
    if not seen <= selected_set:
        raise PresetError('invalid_binding', '沿用工程绑定包含未选中的轨道。')
    available = {
        row['track_index'] for row in normalized
        if row['source_track_index'] is not None
        and row['source_track_index'] >= 0 and row['project'].strip()
    }
    for index in selected:
        if index not in available:
            missing.append(f'轨道 {index + 1} 缺少沿用工程映射。')
    order = {index: position for position, index in enumerate(selected)}
    return sorted(normalized, key=lambda item: order[item['track_index']])


def _prepare_bindings(document, project, bindings):
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict):
        raise PresetError('invalid_binding', '调音输入绑定必须是对象。')
    unknown = set(bindings) - _BINDING_KEYS
    if unknown:
        raise PresetError('unsupported_binding', '未知本地绑定。', fields=sorted(unknown))

    missing = []
    selected = _selected_tracks(bindings, project, missing)
    mode = document['payload']['expression_mode']
    result = {}
    if selected is not None:
        result['vocal_tracks'] = list(selected)

    if mode in ('reference', 'calibrated'):
        reference = _reference_rows(bindings, selected, missing)
        if reference:
            result['reference_audio'] = reference
    if mode == 'calibrated':
        profile = bindings.get('calibration_profile')
        if profile is None:
            profile = ''
        if not isinstance(profile, str):
            raise PresetError('invalid_binding', '声库校准配置路径必须为字符串。')
        if 'calibration_profile' in bindings:
            result['calibration_profile'] = profile
        if not profile.strip():
            missing.append('缺少声库校准配置。')
    if mode == 'retained':
        retained = _retained_rows(bindings, selected, missing)
        if retained:
            result['retained_project'] = retained

    confirmation = bindings.get('confirm_candidate_baseline')
    if confirmation is not None and type(confirmation) is not bool:
        raise PresetError('invalid_binding', '候选基线确认必须为布尔值。')
    if project is not None and project['candidate_origin'] is not None:
        if confirmation is True:
            result['confirm_candidate_baseline'] = True
        else:
            missing.append('请确认将此候选作为新基线。')
    return result, selected, missing


def _safe_controls(document, selected):
    payload = document['payload']
    reference = set(payload.get('reference_controls', []))
    style = _builtin_name(payload['style']['ref'])
    technique = _builtin_name(payload['technique']['ref'])
    return {
        'expression_mode': payload['expression_mode'],
        'track_indices': list(selected) if selected is not None else None,
        'reference_dynamics': 'dynamics' in reference,
        'reference_tension': 'tension' in reference,
        'reference_breathiness': 'breathiness' in reference,
        'reference_voicing': 'voicing' in reference,
        'dynamics_strength': payload.get('reference_strength', .6),
        'performance_style': style,
        'style_strength': payload['style']['strength'],
        'style_scope': 'lead',
        'style_pitch': payload['style']['pitch_enabled'],
        'style_detail': copy.deepcopy(payload['style']['detail']),
        'style_regions': [],
        'expression_density': payload['expression_density'],
        'style_technique': technique,
        'technique_strength': payload['technique']['strength'],
        'technique_pitch': payload['technique']['pitch_enabled'],
    }


def _engine_options(controls, bindings):
    options = copy.deepcopy(controls)
    options.update({
        'reference_sources': copy.deepcopy(bindings.get('reference_audio', [])),
        'reference_calibration': copy.deepcopy(bindings.get('calibration_profile')),
        'retained_project': None,
        'retained_track_index': 0,
        'retained_sources': copy.deepcopy(bindings.get('retained_project', [])),
        'retained_match_policy': 'exact',
    })
    try:
        return settings(options)
    except (TypeError, ValueError, KeyError) as error:
        raise PresetError('invalid_binding', str(error)) from error


def _changes(before, after):
    if before is None:
        return []
    if not isinstance(before, dict):
        raise PresetError('invalid_target', '当前控件参数必须是对象。')
    previous = {}
    for key, value in after.items():
        previous[key] = copy.deepcopy(before.get(key, value))
    return [
        {'field': key, 'before': copy.deepcopy(value), 'after': copy.deepcopy(after[key])}
        for key, value in previous.items()
        if value != after[key]
    ]


def _build_plan(document, project, bindings, raw_document, before):
    document = _verified_document(document)
    raw, document_text, raw_sha256 = _exact_raw_document(document, raw_document)
    canonical_sha256 = document_digest(encode_document(document, allow_builtin=True))
    if raw_sha256 != document_digest(raw):  # Keep this local recomputation explicit.
        raise AssertionError('raw tuning document digest changed while freezing')
    project = _project_parts(project)
    prepared, selected, missing = _prepare_bindings(document, project, bindings)
    controls = _safe_controls(document, selected)
    options = None if missing else _engine_options(controls, prepared)
    project_reference = _project_reference(project, selected)
    preset = {
        'id': document['id'],
        'version': document['version'],
        'sha256': raw_sha256,
        'canonical_sha256': canonical_sha256,
        'name': document['metadata']['name'],
    }
    return {
        'schema': PLAN_SCHEMA,
        'document': copy.deepcopy(document),
        'document_text': document_text,
        'preset': preset,
        # This names the current project's raw input bytes exactly; the host
        # hashes the full canonical plan separately for execution dependencies.
        # A project-free preview has no input identity until it becomes ready.
        'input_sha256': project_reference['sha256'] if project_reference is not None else None,
        'project': project_reference,
        'bindings': copy.deepcopy(prepared),
        'controls': copy.deepcopy(controls),
        'options': copy.deepcopy(options),
        'missing': list(missing),
        'changes': _changes(before, controls),
    }


def plan_application(document, project, bindings, *, raw_document=None, before=None):
    """Freeze a preview/application plan; absent local inputs remain pending."""
    return _build_plan(document, project, bindings, raw_document, before)


def _validate_shape(plan):
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        raise PresetError('invalid_plan', '调音计划字段不完整或包含未知字段。')
    if plan.get('schema') != PLAN_SCHEMA:
        raise PresetError('invalid_plan', '调音计划版本无效。')
    if not isinstance(plan.get('document_text'), str):
        raise PresetError('invalid_plan', '调音计划原文必须是字符串。')
    if not isinstance(plan.get('preset'), dict) or set(plan['preset']) != _PRESET_KEYS:
        raise PresetError('invalid_plan', '调音计划预设身份无效。')
    if plan.get('project') is not None and (
            not isinstance(plan['project'], dict) or set(plan['project']) != _PROJECT_KEYS):
        raise PresetError('invalid_plan', '调音计划工程身份无效。')
    if not isinstance(plan.get('bindings'), dict):
        raise PresetError('invalid_plan', '调音计划绑定无效。')
    if not isinstance(plan.get('controls'), dict):
        raise PresetError('invalid_plan', '调音计划控件参数无效。')
    if plan.get('options') is not None and not isinstance(plan.get('options'), dict):
        raise PresetError('invalid_plan', '调音计划执行参数无效。')
    if not isinstance(plan.get('missing'), list) or not all(isinstance(item, str) for item in plan['missing']):
        raise PresetError('invalid_plan', '调音计划缺项列表无效。')
    if not isinstance(plan.get('changes'), list):
        raise PresetError('invalid_plan', '调音计划展示差异必须是列表。')
    if plan.get('input_sha256') is not None and not _valid_sha(plan.get('input_sha256')):
        raise PresetError('invalid_plan', '调音计划输入摘要无效。')


def _mismatch(field):
    raise PresetError('plan_modified', '调音计划在冻结后被改写。', field=field)


def validate_plan(plan, *, project=None, options=None):
    """Rebuild and verify a frozen plan, returning a canonical ready copy.

    ``changes`` is display-only and intentionally absent from the identity. The
    returned ready plan always uses ``changes=[]`` so later execution signatures
    do not depend on form values that were present during preview.
    """
    _validate_shape(plan)
    project_input = plan['project'] if project is None else project
    rebuilt = _build_plan(
        copy.deepcopy(plan['document']),
        copy.deepcopy(project_input),
        copy.deepcopy(plan['bindings']),
        plan['document_text'],
        before=None,
    )
    for field in ('schema', 'document', 'document_text', 'preset', 'input_sha256',
                  'project', 'bindings', 'controls', 'options', 'missing'):
        if plan[field] != rebuilt[field]:
            _mismatch(field)
    if rebuilt['missing']:
        raise PresetError('plan_incomplete', '调音计划仍有未绑定的必需输入。',
                          missing=list(rebuilt['missing']))
    if rebuilt['project'] is None or not _valid_sha(rebuilt['input_sha256']):
        raise PresetError('invalid_plan', '就绪调音计划缺少实际输入工程身份。')
    if options is not None:
        if not isinstance(options, dict):
            raise PresetError('invalid_options', '用于核验的调音参数必须是对象。')
        if options != rebuilt['options']:
            raise PresetError('options_mismatch', '调音计划执行参数与当前参数不一致。')
    rebuilt['changes'] = []
    return rebuilt