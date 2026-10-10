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
                   'voice_mode': 'dual', 'midi_steps': 16, 'roformer_device': 'auto',
                   'separation_device': 'dml', 'midi_device': 'dml'}


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
    choices = {'language': ('zh', 'ja'), 'voice_mode': ('dual', 'single', 'import', 'roformer'),
               'zh_lyric_mode': ('hanzi', 'pinyin'), 'midi_steps': (8, 16, 32),
               'roformer_device': ('auto', 'cpu', 'cuda'),
               'separation_device': ('cpu', 'dml'), 'midi_device': ('cpu', 'dml')}
    if any(result[key] not in values for key, values in choices.items()) or type(result['recognize_lyrics']) is not bool:
        raise ValueError('预设中的语言、模型或设备参数无效。')
    return result


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
