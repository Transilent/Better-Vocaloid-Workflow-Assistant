"""Small validated workflow graphs and private, portable processing presets."""
import copy
import math
from common import ROOT, atomic_json, workspace_state, load_config

NODES = {'source': '音乐输入', 'separation': '分离声部', 'midi': '生成 MIDI', 'subtitles': '导出字幕'}
LINKS = {('source', 'separation'), ('separation', 'midi'), ('midi', 'subtitles')}
NODE_WIDTH = 192
SNAP_GAP = 32
SNAP_DISTANCE = 100
DISCONNECT_DISTANCE = 150
DEFAULT_OPTIONS = {'language': 'zh', 'recognize_lyrics': True, 'zh_lyric_mode': 'hanzi',
                   'voice_mode': 'dual', 'midi_steps': 16, 'bpm':None, 'roformer_device': 'auto', 'separation_flow':'cascade',
                   'separation_device': 'dml', 'midi_device': 'dml', 'midi_backend':'v2',
                   'alignment_engine':'hfa','project_formats':['ustx','vsqx','svp'],
                   'audio_formats':['wav'],'project_export_enabled':True,'audio_export_enabled':True,
                   'export_pitch':True,'include_instrumental':True,'expression_density':'dense',
                   'performance_style':'none','style_strength':.6,'style_scope':'lead','style_pitch':False,
                   'reference_dynamics':False,'dynamics_strength':.6,
                   'reference_tension':False,'reference_breathiness':False,'reference_voicing':False,
                   'reference_mode':'local','subtitles_mode':'midi','dml_adapter':'auto','cpu_threads':0}
EXPORT_PREFERENCE_KEYS = ('project_export_enabled', 'audio_export_enabled', 'project_formats', 'audio_formats',
                          'export_pitch', 'include_instrumental', 'reference_dynamics', 'reference_tension',
                          'reference_breathiness', 'reference_voicing', 'dynamics_strength', 'expression_density',
                          'performance_style', 'style_strength', 'style_scope','style_pitch')


def default_graph(subtitles=False):
    names = ['source', 'separation', 'midi'] + (['subtitles'] if subtitles else [])
    return {'nodes': {name: [60 + index * 235, 150] for index, name in enumerate(names)},
            'edges': [list(pair) for pair in zip(names, names[1:])]}


def connection_distance(graph, pair):
    """Distance from an adjacent pair's aligned, left-to-right snap position."""
    left, right = (graph['nodes'][name] for name in pair)
    if right[0] - left[0] < NODE_WIDTH * .55:
        return math.inf
    return math.hypot(right[0] - left[0] - NODE_WIDTH - SNAP_GAP, right[1] - left[1])


def nearby_connection(graph, name):
    candidates = []
    for pair in sorted(LINKS):
        if name not in pair or any(n not in graph['nodes'] for n in pair):
            continue
        distance = connection_distance(graph, pair)
        if distance <= SNAP_DISTANCE:
            other = graph['nodes'][pair[0] if name == pair[1] else pair[1]]
            x = other[0] + NODE_WIDTH + SNAP_GAP if name == pair[1] else other[0] - NODE_WIDTH - SNAP_GAP
            candidates.append({'pair': list(pair), 'position': [x, other[1]], 'distance': distance})
    return min(candidates, key=lambda candidate: candidate['distance'], default=None)


def reconnect_nearby(graph, name):
    """On drop, connect compatible neighbors and detach pairs pulled well apart."""
    edges = [edge for edge in graph['edges']
             if name not in edge or connection_distance(graph, edge) <= DISCONNECT_DISTANCE]
    for pair in sorted(LINKS):
        if (name in pair and all(n in graph['nodes'] for n in pair) and
                connection_distance(graph, pair) <= SNAP_DISTANCE and list(pair) not in edges):
            edges.append(list(pair))
    graph['edges'] = edges


def validate_graph(graph):
    if not isinstance(graph, dict) or set(graph) != {'nodes', 'edges'}:
        raise ValueError('工作流格式无效。')
    nodes, edges = graph['nodes'], graph['edges']
    if not isinstance(nodes, dict) or not nodes or set(nodes) - set(NODES):
        raise ValueError('工作流包含未知节点。')
    for position in nodes.values():
        if (not isinstance(position, list) or len(position) != 2 or
                any(not isinstance(v, (float, int)) or not math.isfinite(v) or abs(v) > 10000 for v in position)):
            raise ValueError('节点坐标无效。')
    if not isinstance(edges, list) or any(not isinstance(e, list) or len(e) != 2 or any(not isinstance(n, str) for n in e) for e in edges):
        raise ValueError('工作流连接无效。')
    pairs = [tuple(e) for e in edges]
    if any(p not in LINKS or any(n not in nodes for n in p) for p in pairs) or len(set(pairs)) != len(pairs):
        raise ValueError('连接顺序应为：音乐输入 → 分离声部 → MIDI → 字幕。')
    names = ['source', 'separation', 'midi', 'subtitles'][:len(nodes)]
    if set(nodes) != set(names) or set(pairs) != set(zip(names, names[1:])) or len(nodes) < 2:
        raise ValueError('请连接音乐输入和分离节点；后续节点需接入同一条流程。')
    return 'subtitles' if 'subtitles' in nodes else 'midi' if 'midi' in nodes else 'separation'


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError('预设参数格式无效。')
    result = {key: options.get(key, value) for key, value in DEFAULT_OPTIONS.items()}
    # Legacy presets encode disabled exports only as an empty format list.
    for kind in ('project', 'audio'):
        key = kind + '_export_enabled'
        if key not in options:
            result[key] = bool(result[kind + '_formats'])
        if type(result[key]) is not bool:
            raise ValueError('导出开关必须为开启或关闭。')
    choices = {'language': ('zh', 'ja'), 'voice_mode': ('dual', 'single', 'import', 'roformer'),
               'zh_lyric_mode': ('hanzi', 'pinyin'), 'midi_steps': (8, 16, 32),
               'roformer_device': ('auto', 'cpu', 'cuda'),
               'separation_device': ('cpu', 'dml'), 'midi_device': ('cpu', 'dml')}
    choices.update(midi_backend=('v1','v2'),alignment_engine=('hfa','tifa'))
    choices.update(reference_mode=('local','netease'),subtitles_mode=('midi','netease'))
    choices.update(separation_flow=('cascade','independent'),
                   performance_style=('none','soft_ballad','intimate_narration','power_pop'),
                   style_scope=('lead','all_vocals'))
    if any(result[key] not in values for key, values in choices.items()) or type(result['recognize_lyrics']) is not bool:
        raise ValueError('预设中的语言、模型或设备参数无效。')
    strength = result['style_strength']
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('风格强度必须是 0 到 1 之间的有限数值。')
    result['style_strength'] = float(strength)
    if type(result['style_pitch']) is not bool:raise ValueError('风格音高开关必须为布尔值。')
    from project_exports import validate_options
    validate_options(result['project_formats'],result['audio_formats'])
    from music_timing import validate_bpm
    result['bpm'] = validate_bpm(result['bpm'])
    if type(result['export_pitch']) is not bool or type(result['include_instrumental']) is not bool:
        raise ValueError('工程导出参数无效。')
    from vocal_expression import expression_settings
    expression_settings(result)
    from calibration_refinement import density_settings
    density_settings(result)
    from runtime_devices import validate
    validate(result['dml_adapter'], result['cpu_threads'])
    return result


def execution_options(preferences, *, midi_enabled=True):
    """Compile selected preferences without discarding inactive UI choices."""
    result = copy.deepcopy(validate_options(preferences))
    result['project_export_enabled'] &= bool(midi_enabled)
    if not result['project_export_enabled']:
        result['project_formats'] = []
    if not result['audio_export_enabled']:
        result['audio_formats'] = []
    for name in ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing'):
        result[name] = result[name] and bool(midi_enabled) and 'svp' in result['project_formats']
    if not result['project_export_enabled'] or 'svp' not in result['project_formats']:
        result['performance_style'] = 'none'
    # This surface prepares sources/scores. Tuning preferences remain stored but
    # run only in the separate transaction, never implicitly during preparation.
    result['prepare_only']=True
    for name in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','style_pitch'):
        result[name]=False
    result['performance_style']='none';result['expression_density']='original'
    return result


def export_preferences(preferences):
    values = validate_options(preferences)
    return {key: copy.deepcopy(values[key]) for key in EXPORT_PREFERENCE_KEYS}


def request_options(request):
    """Restore a task's saved choices; old tasks retain their effective settings."""
    values = {key: request[key] for key in DEFAULT_OPTIONS if key in request}
    values.update({key: request.get('tools', {})[key] for key in ('separation_device', 'midi_device')
                   if key in request.get('tools', {})})
    saved = request.get('export_preferences', {})
    if not isinstance(saved, dict):
        raise ValueError('任务中的导出偏好格式无效。')
    values.update({key: saved[key] for key in EXPORT_PREFERENCE_KEYS if key in saved})
    return validate_options(values)


def builtins():
    return {'内置 · 中文双轨': {'graph': default_graph(), 'options': dict(DEFAULT_OPTIONS)},
            '内置 · 日语双轨': {'graph': default_graph(), 'options': dict(DEFAULT_OPTIONS, language='ja')},
            '内置 · 仅分离音频': {'graph': {'nodes': {'source': [60, 150], 'separation': [310, 150]},
                                 'edges': [['source', 'separation']]}, 'options': dict(DEFAULT_OPTIONS)},
            '内置 · MIDI 与字幕': {'graph': default_graph(True), 'options': dict(DEFAULT_OPTIONS)}}


def read_state():
    state = workspace_state().get('workflow', {})
    if not isinstance(state, dict):
        state = {}
    graph = copy.deepcopy(state.get('graph', default_graph()))
    try:
        validate_graph(graph)
        options = validate_options(state.get('options', {**DEFAULT_OPTIONS, **load_config(False)}))
    except (ValueError, TypeError):
        graph, options = default_graph(), dict(DEFAULT_OPTIONS)
    presets = {}
    for name, preset in state.get('presets', {}).items() if isinstance(state.get('presets', {}), dict) else []:
        try:
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or name.startswith('内置'):
                continue
            validate_graph(preset['graph'])
            presets[name] = {'graph': preset['graph'], 'options': validate_options(preset['options'])}
        except (ValueError, KeyError, TypeError):
            continue
    return {'graph': graph, 'options': options, 'presets': presets, 'preset': state.get('preset', '自定义')}


def save_state(graph, options, presets, name='自定义'):
    validate_graph(graph)
    state = workspace_state()
    state['workflow'] = {'graph': copy.deepcopy(graph), 'options': validate_options(options),
                         'presets': copy.deepcopy(presets), 'preset': name}
    atomic_json(ROOT / 'cache/workspace.json', state)
