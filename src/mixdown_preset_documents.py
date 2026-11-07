"""Portable mixing documents and explicit, side-effect-free application plans."""
from __future__ import annotations

import copy
from decimal import Decimal, ROUND_HALF_UP
import math
import uuid

from mixdown_contract import project_eq_band, valid_sample_rate
from preset_documents import PresetError, validate_document

SUPPORTED_CAPABILITIES = frozenset({
    'mix.eq4', 'mix.compression', 'mix.reverb', 'mix.width', 'mix.track_template',
})


def make_document(payload, name, *, include_width=False, identifier=None):
    """Save controls as a new identity. Never include media or live track state."""
    if type(include_width) is not bool:
        raise PresetError('invalid_scope', '输出宽度范围必须明确选择。')
    if type(payload) is not dict:
        raise PresetError('invalid_document', '混音参数必须是对象。')
    payload = copy.deepcopy(payload)
    payload['apply_scope'] = ('vocal_processing_and_output_width' if include_width
                              else 'vocal_processing')
    if not include_width:
        payload.pop('width', None)
    capabilities = ['mix.eq4', 'mix.compression', 'mix.reverb']
    if include_width:
        capabilities.append('mix.width')
    if 'track_template' in payload:
        capabilities.append('mix.track_template')
    return validate_document({
        'format': 'vwa.preset', 'format_version': 1,
        'id': identifier or 'user.' + str(uuid.uuid4()), 'type': 'mixdown', 'version': '0.1.0',
        'metadata': {'name': name},
        'requires': {'preset_api': 1, 'engine_contract': 'mixdown-parameters/v1',
                     'capabilities': capabilities},
        'binding_slots': [], 'payload': payload,
    })


def _snapshot(before):
    if type(before) is not dict or set(before) != {'processing', 'tracks'}:
        raise PresetError('invalid_target', '当前混音快照无效。')
    processing = make_document(before['processing'], 'Snapshot', include_width=True,
                               identifier='user.snapshot.mixdown')['payload']
    processing.pop('track_template', None)
    tracks = before['tracks']
    if type(tracks) is not list or not 1 <= len(tracks) <= 3:
        raise PresetError('invalid_target', '当前演唱轨数量无效。')
    for track in tracks:
        if type(track) is not dict or set(track) != {'pan', 'reverb_send'}:
            raise PresetError('invalid_target', '当前演唱轨参数无效。')
        for field, low, high in (('pan', -1, 1), ('reverb_send', 0, 1)):
            value = track[field]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise PresetError('invalid_target', '当前演唱轨参数超出范围。', field=field)
    return {'processing': processing, 'tracks': copy.deepcopy(tracks)}


def _percentage(value):
    # QDoubleSpinBox with decimals=0 uses decimal half-up rounding.
    return float((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) / 100


def plan_application(document, before, *, sample_rate=44100,
                     include_width=False, include_tracks=False):
    """Preview only. Applying always needs the host's later explicit commit."""
    document = validate_document(document, allow_builtin=True)
    if document['type'] != 'mixdown':
        raise PresetError('unsupported_preset', '请选择混音预设。')
    unsupported = set(document['requires']['capabilities']) - SUPPORTED_CAPABILITIES
    if unsupported or document['requires'].get('dependencies'):
        raise PresetError('unsupported_capability', '当前混音台不支持此预设要求的能力或依赖。',
                          capabilities=sorted(unsupported))
    if type(include_width) is not bool or type(include_tracks) is not bool:
        raise PresetError('invalid_scope', '附加应用范围必须明确选择。')
    source = document['payload']
    if include_width and 'width' not in source or include_tracks and 'track_template' not in source:
        raise PresetError('invalid_scope', '预设没有声明所选的附加参数。')
    before = _snapshot(before)
    after = copy.deepcopy(before)
    processing = after['processing']
    processing.update(copy.deepcopy({k: v for k, v in source.items()
                                    if k not in ('width', 'track_template', 'apply_scope')}))
    try:
        rate = valid_sample_rate(sample_rate)
        processing['eq_bands'] = [project_eq_band(band, sample_rate=rate)
                                  for band in source['eq_bands']]
    except (ValueError, TypeError) as error:
        raise PresetError('invalid_target', '当前采样率或 EQ 参数无效。') from error
    processing['compression_strength'] = _percentage(source['compression_strength'])
    processing['reverb_mix'] = _percentage(source['reverb_mix'])
    processing['reverb_predelay_ms'] = int(round(source['reverb_predelay_ms']))
    if include_width:
        processing['width'] = int(round(source['width'] * 100)) / 100
    if include_tracks:
        template = source['track_template']
        for index, track in enumerate(after['tracks']):
            mode, harmony = template['pan_mode'], template['harmony_pan']
            if mode == 'alternate' or mode == 'lead_harmony' and index > 0:
                pan = harmony if index % 2 else -harmony
            else:
                pan = template['pan']
            track['pan'] = int(round(pan * 100)) / 100
            track['reverb_send'] = template['reverb_send']
    changes = [{'field': 'processing.' + key, 'before': before['processing'][key],
                'after': value} for key, value in processing.items()
               if before['processing'][key] != value]
    if before['tracks'] != after['tracks']:
        changes.append({'field': 'tracks', 'before': before['tracks'], 'after': after['tracks']})
    adjusted = any(processing[k] != source[k] for k in (
        'eq_bands', 'compression_strength', 'reverb_mix', 'reverb_predelay_ms'))
    if include_width:
        adjusted = adjusted or processing['width'] != source['width']
    return {'before': before, 'after': after, 'document': document, 'changes': changes,
            'adjusted': adjusted, 'include_width': include_width, 'include_tracks': include_tracks}
