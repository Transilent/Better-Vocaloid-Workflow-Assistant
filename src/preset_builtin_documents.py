"""Read-only projections of existing built-ins into portable preset documents.

These are library views, not a new audio/tuning apply path. Bump the document
version when intentionally changing their frozen portable meaning.
"""
from __future__ import annotations

import copy

from preset_documents import CONTRACTS, PREPARATION_KEYS, validate_document

BUILTIN_VERSION = '1.0.0'


def _document(identifier, kind, name, payload, capabilities, slots=()):
    value = {
        'format': 'vwa.preset', 'format_version': 1,
        'id': identifier, 'type': kind, 'version': BUILTIN_VERSION,
        'metadata': {'name': name, 'author': 'VWA', 'license': 'See application LICENSE',
                     'description': '内置参数的本机库副本；试听后按素材调整。'},
        'requires': {'preset_api': 1, 'engine_contract': CONTRACTS[kind],
                     'capabilities': list(capabilities)},
        'binding_slots': list(slots), 'payload': payload,
    }
    return validate_document(value, allow_builtin=True)


def builtin_documents():
    """Return detached built-ins with no filesystem, network or GUI effects."""
    from mixdown_presets import catalog
    from workflow import builtins
    result = []
    for recipe in catalog():
        payload = {
            'apply_scope': 'vocal_processing_and_output_width',
            'eq_enabled': True, 'eq_bands': copy.deepcopy(recipe['eq_bands']),
            'compression_enabled': recipe['compression_strength'] > 0,
            'compression_strength': recipe['compression_strength'],
            'reverb_enabled': recipe['reverb_mix'] > 0,
            'reverb_profile': recipe['reverb_profile'], 'reverb_mix': recipe['reverb_mix'],
            'reverb_predelay_ms': recipe['reverb_predelay_ms'], 'width': recipe['width'],
            'track_template': {key: recipe[key] for key in ('pan_mode', 'pan', 'harmony_pan', 'reverb_send')},
        }
        result.append(_document('builtin.mixdown.' + recipe['key'], 'mixdown', recipe['label'], payload,
                                ('mix.eq4', 'mix.compression', 'mix.reverb', 'mix.width')))
    names = ('zh_dual', 'ja_dual', 'separation_only', 'midi_subtitles')
    for name, (label, recipe) in zip(names, builtins().items(), strict=True):
        options = {key: copy.deepcopy(recipe['options'][key]) for key in PREPARATION_KEYS}
        result.append(_document('builtin.preparation.' + name, 'preparation', label,
                                {'graph': copy.deepcopy(recipe['graph']), 'options': options},
                                ('preparation.parameters',)))
    return result


def example_documents():
    """Four valid examples for docs, CLI templates and integration fixtures."""
    mixing = builtin_documents()[0]
    mixing['id'] = 'user.example.clear_vocal'
    mixing['version'] = '0.1.0'
    mixing['metadata'] = {'name': '人声混音示例', 'author': 'Example', 'license': 'CC0-1.0',
                          'description': '文档示例，未完成声学试听。'}
    preparation = builtin_documents()[6]
    preparation['id'] = 'user.example.zh_preparation'
    preparation['version'] = '0.1.0'
    preparation['metadata'] = {'name': '中文准备流程示例'}
    tuning = {
        'format': 'vwa.preset', 'format_version': 1, 'id': 'user.example.gentle_tuning',
        'type': 'tuning', 'version': '0.1.0', 'metadata': {'name': '保留原表现与柔和组合'},
        'requires': {'preset_api': 1, 'engine_contract': CONTRACTS['tuning'],
                     'capabilities': ['tuning.svp-main-group', 'style.builtin.gentle']},
        'binding_slots': [{'id': 'target_vocals', 'kind': 'vocal_tracks', 'required': True}],
        'payload': {
            'expression_mode': 'preserve', 'expression_density': 'original',
            'style': {'ref': 'builtin:gentle', 'strength': .5, 'pitch_enabled': False,
                      'detail': dict.fromkeys(('onset', 'sustain', 'connection', 'release'), 1.)},
            'technique': {'ref': 'builtin:none', 'strength': .6, 'pitch_enabled': False},
        },
    }
    style = {
        'format': 'vwa.preset', 'format_version': 1, 'id': 'user.example.soft_ending',
        'type': 'style_recipe', 'version': '0.1.0', 'metadata': {'name': '柔和收尾配方示例', 'color': '#438C9B'},
        'requires': {'preset_api': 1, 'engine_contract': CONTRACTS['style_recipe'],
                     'capabilities': ['style.note-envelope']},
        'binding_slots': [],
        'payload': {'role': 'style', 'coordinate_system': 'normalized_note_phase',
                    'default_strength': .5, 'pitch': {'enabled': False, 'gestures': []},
                    'controls': [{'channel': 'tension', 'operation': 'add',
                                  'points': [[0, 0], [.2, -.03], [.7, -.03], [1, 0]]}]},
    }
    return [validate_document(x) for x in (preparation, mixing, tuning, style)]
