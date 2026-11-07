"""Versioned, media-free preset documents. Standard library only, no execution.

The schema exported here and the built-in structural validator use the same
trusted definition. Business checks add cross-field constraints. Package data
never supplies schemas, callbacks, module names, filesystem paths or commands.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from common import native_path

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_DEPTH = 16
MAX_CURVE_POINTS = 1024
FORMAT_VERSION = 1
TYPES = ('preparation', 'mixdown', 'tuning', 'style_recipe')
CONTRACTS = dict(zip(TYPES, ('preparation-parameters/v1', 'mixdown-parameters/v1',
                           'tuning-pipeline/v1', 'style-recipe/v1')))
ID_PATTERN = r'^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+$'
EXTERNAL_ID_PATTERN = r'^(?!builtin\.)[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+$'
VERSION_PATTERN = (
    r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    r'(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
    r'(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?'
    r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$'
)
SHA_PATTERN = r'^[0-9a-f]{64}$'
PREPARATION_KEYS = (
    'language', 'recognize_lyrics', 'zh_lyric_mode', 'voice_mode', 'midi_steps',
    'midi_backend', 'alignment_engine', 'project_formats', 'audio_formats',
    'project_export_enabled', 'audio_export_enabled', 'export_pitch',
    'include_instrumental', 'separation_flow', 'reference_mode', 'subtitles_mode',
)


class PresetError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _object(fields, required=None):
    return {'type': 'object', 'properties': fields,
            'required': list(fields) if required is None else list(required),
            'additionalProperties': False}


def _string(maximum=80, *, pattern=None):
    result = {'type': 'string', 'minLength': 1, 'maxLength': maximum}
    if pattern:
        # JSON Schema searches rather than fullmatches; make final-newline
        # handling agree with the runtime validator as well.
        result['pattern'] = pattern + r'(?![\s\S])'
    return result


def _number(low, high):
    return {'type': 'number', 'minimum': low, 'maximum': high}


def _enum(*values):
    return {'enum': list(values)}


def _array(items, low=0, high=16, *, unique=False):
    result = {'type': 'array', 'items': items, 'minItems': low, 'maxItems': high}
    if unique:
        result['uniqueItems'] = True
    return result


_BOOL = {'type': 'boolean'}
_REF = _object({'id': _string(128, pattern=EXTERNAL_ID_PATTERN),
                'version': _string(80, pattern=VERSION_PATTERN),
                'sha256': _string(64, pattern=SHA_PATTERN)})
_DETAIL = _object({name: _number(0, 1) for name in ('onset', 'sustain', 'connection', 'release')})
_PAIR = {'type': 'array', 'minItems': 2, 'maxItems': 2,
         'prefixItems': [_number(-10000, 10000), _number(-10000, 10000)], 'items': False}
_GRAPH = _object({
    'nodes': _object({key: _PAIR for key in ('source', 'separation', 'midi', 'subtitles')},
                     ('source', 'separation')),
    'edges': _array(_array(_enum('source', 'separation', 'midi', 'subtitles'), 2, 2), 1, 3, unique=True),
})
_PREPARATION_OPTIONS = _object({
    'language': _enum('zh', 'ja'), 'recognize_lyrics': _BOOL,
    'zh_lyric_mode': _enum('hanzi', 'pinyin'), 'voice_mode': _enum('single', 'dual', 'import', 'roformer'),
    'midi_steps': _enum(8, 16, 32), 'midi_backend': _enum('v1', 'v2'),
    'alignment_engine': _enum('hfa', 'tifa'),
    'project_formats': _array(_enum('ustx', 'vsqx', 'svp'), 0, 3, unique=True),
    'audio_formats': _array(_enum('wav', 'flac', 'mp3', 'ogg'), 0, 4, unique=True),
    'project_export_enabled': _BOOL, 'audio_export_enabled': _BOOL,
    'export_pitch': _BOOL, 'include_instrumental': _BOOL,
    'separation_flow': _enum('cascade', 'independent'),
    'reference_mode': _enum('local', 'netease'), 'subtitles_mode': _enum('midi', 'netease'),
})
_BAND = _object({'type': _enum('highpass', 'lowshelf', 'bell', 'highshelf'),
                 'freq_hz': _number(10, 20000), 'gain_db': _number(-24, 24),
                 'q': _number(.1, 18), 'enabled': _BOOL})
_MIX = _object({
    'apply_scope': _enum('vocal_processing', 'vocal_processing_and_output_width'),
    'eq_enabled': _BOOL, 'eq_bands': _array(_BAND, 4, 4),
    'compression_enabled': _BOOL, 'compression_strength': _number(0, 1),
    'reverb_enabled': _BOOL, 'reverb_profile': _enum('small', 'natural', 'wide'),
    'reverb_mix': _number(0, 1), 'reverb_predelay_ms': _number(0, 120),
    'width': _number(0, 2),
    'track_template': _object({'pan_mode': _enum('center', 'alternate', 'lead_harmony'),
                               'pan': _number(-1, 1), 'harmony_pan': _number(0, 1),
                               'reverb_send': _number(0, 1)}),
}, ('apply_scope', 'eq_enabled', 'eq_bands', 'compression_enabled', 'compression_strength',
    'reverb_enabled', 'reverb_profile', 'reverb_mix', 'reverb_predelay_ms'))
_STYLE_REF = {'oneOf': [_enum('builtin:none', 'builtin:gentle', 'builtin:sad', 'builtin:bold'), _REF]}
_TECHNIQUE_REF = {'oneOf': [_enum('builtin:none', 'builtin:chinese_opera'), _REF]}
_TUNING = _object({
    'expression_mode': _enum('preserve', 'reference', 'calibrated', 'retained'),
    'style': _object({'ref': _STYLE_REF, 'strength': _number(0, 1),
                      'pitch_enabled': _BOOL, 'detail': _DETAIL}),
    'technique': _object({'ref': _TECHNIQUE_REF, 'strength': _number(0, 1), 'pitch_enabled': _BOOL}),
    'expression_density': _enum('original', 'dense'),
    'reference_controls': _array(_enum('dynamics', 'tension', 'breathiness', 'voicing'), 1, 4, unique=True),
    'reference_strength': _number(0, 1),
}, ('expression_mode', 'style', 'technique', 'expression_density'))
_POINT = {'type': 'array', 'minItems': 2, 'maxItems': 2,
          'prefixItems': [_number(0, 1), _number(-12, 12)], 'items': False}
_RECIPE = _object({
    'role': _enum('style', 'technique'),
    'coordinate_system': _enum('normalized_note_phase'),
    'default_strength': _number(0, 1),
    # Pitch recipes deliberately need a future explicit capability/contract.
    'pitch': _object({'enabled': {'type': 'boolean', 'const': False}, 'gestures': _array({}, 0, 0)}),
    'controls': _array(_object({
        'channel': _enum('loudness', 'tension', 'breathiness', 'voicing'),
        'operation': _enum('add'), 'points': _array(_POINT, 2, 64),
    }), 1, 4),
})
PAYLOAD_SCHEMAS = {
    'preparation': _object({'graph': _GRAPH, 'options': _PREPARATION_OPTIONS}),
    'mixdown': _MIX, 'tuning': _TUNING, 'style_recipe': _RECIPE,
}
_FIELDS = {
    'format': {'type': 'string', 'const': 'vwa.preset'},
    'format_version': {'type': 'integer', 'const': FORMAT_VERSION},
    'id': _string(128, pattern=EXTERNAL_ID_PATTERN),
    'type': _enum(*TYPES), 'version': _string(80, pattern=VERSION_PATTERN),
    'metadata': _object({
        'name': _string(), 'description': _string(2000), 'author': _string(120),
        'license': _string(120), 'tags': _array(_string(40), 0, 16, unique=True),
        'color': _string(7, pattern=r'^#[0-9a-fA-F]{6}$'),
    }, ('name',)),
    'requires': _object({
        'preset_api': {'type': 'integer', 'const': 1},
        'engine_contract': _enum(*CONTRACTS.values()),
        'capabilities': _array(_string(80, pattern=r'^[a-z][a-z0-9_.-]*$'), 0, 32, unique=True),
        'dependencies': _array(_REF, 0, 16, unique=True),
    }, ('preset_api', 'engine_contract', 'capabilities')),
    'binding_slots': _array(_object({
        'id': _string(64, pattern=r'^[a-z][a-z0-9_]*$'),
        'kind': _enum('vocal_tracks', 'reference_audio', 'calibration_profile', 'retained_project', 'separated_stems'),
        'required': {'type': 'boolean', 'const': True},
    }), 0, 8),
    'payload': {'type': 'object'},
}


def schema_document():
    """Return a fresh Draft 2020-12 schema; never resolve external schemas."""
    schema = _object(copy.deepcopy(_FIELDS))
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    schema['title'] = 'VWA portable preset v1'
    schema['oneOf'] = [
        {'properties': {'type': {'const': kind}, 'payload': copy.deepcopy(payload),
                        'requires': {'properties': {'engine_contract': {'const': CONTRACTS[kind]}}}}}
        for kind, payload in PAYLOAD_SCHEMAS.items()
    ]
    return schema


def _invalid(path, message):
    raise PresetError('invalid_document', message, field=path)


def _structural(value, schema, path='$'):
    """Validate our trusted, finite schema vocabulary, never package schemas."""
    if schema is False:
        _invalid(path, '该位置不允许额外数据。')
    if schema is True:
        return
    kind = schema.get('type')
    matches = {
        'object': lambda: type(value) is dict,
        'array': lambda: type(value) is list,
        'string': lambda: type(value) is str,
        'number': lambda: type(value) in (int, float) and math.isfinite(value),
        'integer': lambda: type(value) is int,
        'boolean': lambda: type(value) is bool,
    }
    if kind and not matches[kind]():
        _invalid(path, '预设字段类型无效。')
    # Enum equality must not treat True as numeric 1.
    if 'enum' in schema and not any(type(value) is type(x) and value == x for x in schema['enum']):
        _invalid(path, '预设字段包含不支持的值。')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        _invalid(path, '预设字段的固定值无效。')
    if type(value) is dict:
        fields = schema.get('properties', {})
        for name in schema.get('required', []):
            if name not in value:
                _invalid(path + '.' + name, '预设缺少必填字段。')
        if schema.get('additionalProperties') is False and set(value) - set(fields):
            _invalid(path, '预设包含未知字段。')
        for name, child in fields.items():
            if name in value:
                _structural(value[name], child, path + '.' + name)
    if type(value) is str:
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', len(value)):
            _invalid(path, '预设文本长度超出范围。')
        if 'pattern' in schema and re.fullmatch(schema['pattern'], value) is None:
            _invalid(path, '预设文本格式无效。')
        if 'minLength' in schema and not value.strip():
            _invalid(path, '预设文本不能为空。')
    if type(value) in (int, float):
        if not math.isfinite(value):
            _invalid(path, '预设不能包含非有限数值。')
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            _invalid(path, '预设数值超出范围。')
    if type(value) is list:
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', len(value)):
            _invalid(path, '预设数组长度超出范围。')
        if schema.get('uniqueItems'):
            keys = [json.dumps(x, sort_keys=True, ensure_ascii=True) for x in value]
            if len(keys) != len(set(keys)):
                _invalid(path, '预设数组不能包含重复项。')
        prefix = schema.get('prefixItems', [])
        for i, child in enumerate(value):
            child_schema = prefix[i] if i < len(prefix) else schema.get('items', True)
            _structural(child, child_schema, path + '[' + str(i) + ']')
    if 'oneOf' in schema:
        passed = 0
        for option in schema['oneOf']:
            try:
                _structural(value, option, path)
                passed += 1
            except PresetError:
                pass
        if passed != 1:
            _invalid(path, '预设类型与参数结构不匹配。')


def _json_guard(value):
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise PresetError('too_deep', '预设嵌套过深。')
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _invalid('$', 'JSON 对象键必须是文本。')
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in '\n\r\t' for c in item):
                _invalid('$', '预设包含无效文本字符。')
        elif type(item) in (int, float):
            try:
                valid = math.isfinite(item)
            except OverflowError:
                valid = False
            if not valid:
                _invalid('$', '预设不能包含非有限数值。')
        elif item is not None and type(item) is not bool:
            _invalid('$', '预设包含不能保存为 JSON 的值。')


def validate_document(value, *, allow_builtin=False):
    """Validate and detach portable data; does not resolve or execute references."""
    _json_guard(value)
    schema = schema_document()
    if allow_builtin:
        schema['properties']['id']['pattern'] = ID_PATTERN + r'(?![\s\S])'
    _structural(value, schema)
    kind, payload = value['type'], value['payload']
    slots = value['binding_slots']
    if len({s['id'] for s in slots}) != len(slots):
        _invalid('$.binding_slots', '输入槽位标识不能重复。')
    slot_kinds = {s['kind'] for s in slots}
    if len(slot_kinds) != len(slots):
        _invalid('$.binding_slots', '同一种输入来源只能声明一个槽位。')
    dependencies = value['requires'].get('dependencies', [])
    dep_keys = [(d['id'], d['version']) for d in dependencies]
    if len(set(dep_keys)) != len(dep_keys):
        _invalid('$.requires.dependencies', '同一依赖版本不能重复或指向不同内容。')
    own = (value['id'], value['version'])
    if own in dep_keys:
        _invalid('$.requires.dependencies', '预设不能依赖自身版本。')
    used_refs = []
    if kind == 'mixdown':
        if [band['type'] for band in payload['eq_bands']] != ['highpass', 'lowshelf', 'bell', 'highshelf']:
            _invalid('$.payload.eq_bands', '首版 EQ 顺序须为高通、低架、钟形、高架。')
        if payload['eq_bands'][0]['gain_db'] != 0 or payload['eq_bands'][0]['q'] != .707:
            _invalid('$.payload.eq_bands[0]', '高通增益与 Q 须使用固定有效值。')
        has_width = payload['apply_scope'] == 'vocal_processing_and_output_width'
        if ('width' in payload) != has_width:
            _invalid('$.payload.width', '总输出宽度必须与应用范围一致。')
        if slots:
            _invalid('$.binding_slots', '混音预设不保存工程输入槽位。')
    elif kind == 'preparation':
        from workflow import validate_graph
        try:
            validate_graph(payload['graph'])
        except (ValueError, TypeError, KeyError) as error:
            _invalid('$.payload.graph', str(error))
        options = payload['options']
        if options['alignment_engine'] == 'tifa' and options['midi_backend'] != 'v2':
            _invalid('$.payload.options', 'TiFA 需要 v2 后端。')
        if options['separation_flow'] == 'independent' and options['voice_mode'] != 'roformer':
            _invalid('$.payload.options', '独立分离需要 RoFormer。')
        expected = {'separated_stems'} if options['voice_mode'] == 'import' else set()
        if slot_kinds != expected:
            _invalid('$.binding_slots', '准备预设输入槽与分离模式不一致。')
    elif kind == 'tuning':
        mode = payload['expression_mode']
        source = {'reference': 'reference_audio', 'calibrated': 'calibration_profile',
                  'retained': 'retained_project'}.get(mode)
        if slot_kinds != {'vocal_tracks', *([source] if source else [])}:
            _invalid('$.binding_slots', '调音参数来源与输入槽不一致。')
        if sum(s['kind'] == 'vocal_tracks' for s in slots) != 1:
            _invalid('$.binding_slots', '调音模板须有一个目标演唱轨槽。')
        needs_strength = mode in ('reference', 'calibrated')
        if ('reference_strength' in payload) != needs_strength:
            _invalid('$.payload.reference_strength', '参考强度与参数来源不一致。')
        if ('reference_controls' in payload) != (mode == 'reference'):
            _invalid('$.payload.reference_controls', '参考通道与参数来源不一致。')
        used_refs = [row['ref'] for row in (payload['style'], payload['technique'])
                     if type(row['ref']) is dict]
    elif kind == 'style_recipe':
        if slots:
            _invalid('$.binding_slots', '风格配方不保存工程输入槽位。')
        controls = payload['controls']
        if len({c['channel'] for c in controls}) != len(controls):
            _invalid('$.payload.controls', '控制通道不能重复。')
        if sum(len(c['points']) for c in controls) > MAX_CURVE_POINTS:
            _invalid('$.payload.controls', '风格配方控制点过多。')
        for i, row in enumerate(controls):
            points = row['points']
            if points[0][0] != 0 or points[-1][0] != 1 or any(a[0] >= b[0] for a, b in zip(points, points[1:])):
                _invalid('$.payload.controls[' + str(i) + ']', '相位须从 0 到 1 严格递增。')
            limit = 12 if row['channel'] == 'loudness' else 1
            if any(abs(p[1]) > limit for p in points):
                _invalid('$.payload.controls[' + str(i) + ']', '通道增量超出配方范围。')
    if sorted(json.dumps(x, sort_keys=True) for x in used_refs) != sorted(json.dumps(x, sort_keys=True) for x in dependencies):
        _invalid('$.requires.dependencies', '依赖清单须与配方引用完全一致。')
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PresetError('invalid_document', '预设无法编码。') from error
    if size > MAX_DOCUMENT_BYTES:
        raise PresetError('too_large', '预设超过 256 KiB。')
    return copy.deepcopy(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PresetError('duplicate_key', '预设不能包含重复 JSON 键。', field=key)
        result[key] = value
    return result


def _reject_constant(value):
    raise PresetError('invalid_document', '预设不能包含非有限数值。')


def decode_document(raw, *, allow_builtin=False):
    if type(raw) is not bytes:
        raise PresetError('invalid_document', '预设输入须为 UTF-8 字节。')
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise PresetError('too_large', '预设超过 256 KiB。')
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, PresetError):
            raise
        raise PresetError('invalid_json', '预设不是有效的 UTF-8 JSON。') from error
    return validate_document(value, allow_builtin=allow_builtin)


def encode_document(value, *, allow_builtin=False):
    value = validate_document(value, allow_builtin=allow_builtin)
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise PresetError('too_large', '编码后的预设超过 256 KiB。')
    return raw


def document_digest(raw):
    if type(raw) is not bytes:
        raise PresetError('invalid_document', '摘要输入须为字节。')
    return hashlib.sha256(raw).hexdigest()


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _stable_identities(before, opened, after_read, after):
    identities = [_file_identity(info) for info in (before, opened, after_read, after)]
    if os.name != 'nt':
        return all(value == identities[0] for value in identities)
    # Windows Python 3.12 can expose different ctime values through a path
    # and an open handle immediately after rename. Compare each channel's
    # ctime before/after, while file ID, size and mtime must agree everywhere.
    return (all(value[:4] == identities[0][:4] for value in identities)
            and identities[0][4] == identities[3][4]
            and identities[1][4] == identities[2][4])


def _physical_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    for part in (path, *path.parents):
        try:
            info = Path(native_path(part)).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise PresetError('unsafe_path', '预设路径不能经过链接或重解析点。', path=str(part))
    return path


def read_regular_bytes(path, *, limit=MAX_DOCUMENT_BYTES):
    """Read a bounded stable regular file, rejecting reparse points at all levels."""
    if type(limit) is not int or limit < 0:
        raise PresetError('invalid_limit', '文件大小预算无效。')
    path = _physical_path(path)
    native = Path(native_path(path))
    before = native.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise PresetError('unsafe_path', '预设路径不是普通文件。', path=str(path))
    if before.st_size > limit:
        raise PresetError('too_large', '预设或库记录超出大小限制。', path=str(path))
    with native.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after_read = os.fstat(stream.fileno())
    _physical_path(path)
    after = native.lstat()
    if len(raw) > limit:
        raise PresetError('too_large', '预设或库记录超出大小限制。', path=str(path))
    if not _stable_identities(before, opened, after_read, after) or len(raw) != before.st_size:
        raise PresetError('file_changed', '读取期间文件发生变化，请重新选择。', path=str(path))
    return raw


def read_document(path, *, allow_builtin=False):
    return decode_document(read_regular_bytes(path), allow_builtin=allow_builtin)
