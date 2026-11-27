"""Small validated workflow graphs and private, portable processing presets."""
import copy
import math
from common import ROOT, atomic_json, workspace_state, load_config
from preparation_languages import LANGUAGES, effective_preparation_options
import style_catalog
from workflow_graph import LINKS, NODES, default_graph, validate_graph

DEFAULT_OPTIONS = {'language': 'zh', 'recognize_lyrics': True, 'zh_lyric_mode': 'hanzi',
                   'voice_mode': 'dual', 'midi_steps': 16, 'bpm':None, 'roformer_device': 'auto', 'separation_flow':'cascade',
                   'separation_device': 'dml', 'midi_device': 'dml', 'midi_backend':'v2',
                   'alignment_engine':'hfa','project_formats':['ustx','vsqx','svp'],
                   'audio_formats':['wav'],'project_export_enabled':True,'audio_export_enabled':True,
                   'export_pitch':True,'include_instrumental':True,'expression_density':'dense',
                   'performance_style':style_catalog.DEFAULT_STYLE_ID,'style_strength':.6,'style_scope':'lead','style_pitch':False,'style_detail':None,
                   'style_technique':style_catalog.DEFAULT_TECHNIQUE_ID,'technique_strength':.6,'technique_pitch':True,
                   'reference_dynamics':False,'dynamics_strength':.6,
                   'reference_tension':False,'reference_breathiness':False,'reference_voicing':False,
                   'reference_mode':'local','subtitles_mode':'midi','dml_adapter':'auto','cpu_threads':0}
EXPORT_PREFERENCE_KEYS = ('project_export_enabled', 'audio_export_enabled', 'project_formats', 'audio_formats',
                          'export_pitch', 'include_instrumental', 'reference_dynamics', 'reference_tension',
                          'reference_breathiness', 'reference_voicing', 'dynamics_strength', 'expression_density',
                          'performance_style', 'style_strength', 'style_scope','style_pitch','style_detail',
                          'style_technique','technique_strength','technique_pitch')


def validate_options(options):
    from style_presets import PRESETS,detail_settings,technique_settings
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
    choices = {'language': LANGUAGES, 'voice_mode': ('dual', 'single', 'import', 'roformer', 'roformer_onnx'),
               'zh_lyric_mode': ('hanzi', 'pinyin'), 'midi_steps': (8, 16, 32),
               'roformer_device': ('auto', 'cpu', 'cuda', 'webgpu'),
               'separation_device': ('cpu', 'dml'), 'midi_device': ('cpu', 'dml')}
    choices.update(midi_backend=('v1','v2'),alignment_engine=('hfa','tifa'))
    choices.update(reference_mode=('local','netease'),subtitles_mode=('midi','netease'))
    choices.update(separation_flow=('cascade','independent'),
                   performance_style=tuple(PRESETS),
                   style_scope=('lead','all_vocals'))
    if any(result[key] not in values for key, values in choices.items()) or type(result['recognize_lyrics']) is not bool:
        raise ValueError('预设中的语言、模型或设备参数无效。')
    strength = result['style_strength']
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('风格强度必须是 0 到 1 之间的有限数值。')
    result['style_strength'] = float(strength)
    result['style_detail'] = detail_settings(result['style_detail'])
    result.update(technique_settings(result))
    if type(result['style_pitch']) is not bool:raise ValueError('风格音高开关必须为布尔值。')
    from project_io import validate_export_options
    validate_export_options(result['project_formats'],result['audio_formats'])
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
    result = effective_preparation_options(copy.deepcopy(validate_options(preferences)))
    result['project_export_enabled'] &= bool(midi_enabled)
    if not result['project_export_enabled']:
        result['project_formats'] = []
    if not result['audio_export_enabled']:
        result['audio_formats'] = []
    for name in ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing'):
        result[name] = result[name] and bool(midi_enabled) and 'svp' in result['project_formats']
    if not result['project_export_enabled'] or 'svp' not in result['project_formats']:
        result['performance_style'] = style_catalog.DEFAULT_STYLE_ID
    # This surface prepares sources/scores. Tuning preferences remain stored but
    # run only in the separate transaction, never implicitly during preparation.
    result['prepare_only']=True
    for name in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','style_pitch'):
        result[name]=False
    result['performance_style']=style_catalog.DEFAULT_STYLE_ID;result['expression_density']='original'
    # These belong to the independent tuning transaction. Preparation's public
    # create_job API keeps its existing signature; export_preferences stores
    # the user's technique choices separately for later tuning.
    for key in ('style_technique','technique_strength','technique_pitch'):result.pop(key,None)
    return result


def export_preferences(preferences):
    values = validate_options(preferences)
    return {key: copy.deepcopy(values[key]) for key in EXPORT_PREFERENCE_KEYS}


def request_options(request):
    """Restore a task's saved choices; old tasks retain their effective settings."""
    values = {key: request[key] for key in DEFAULT_OPTIONS if key in request}
    tools = request.get('tools', {})
    if not isinstance(tools, dict):
        raise ValueError('任务中的工具设置格式无效。')
    values.update({key: tools[key] for key in (
        'separation_device', 'midi_device', 'dml_adapter', 'cpu_threads') if key in tools})
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
