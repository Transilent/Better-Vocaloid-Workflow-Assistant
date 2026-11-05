"""Cross-surface style registry contracts over private micro fixtures."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import expression_controls
import style_catalog
import style_drafts
import style_presets
import workflow


VISIBLE = ('none', 'gentle', 'sad', 'bold')
TECHNIQUES = ('none', 'chinese_opera')
LEGACY = (
    'soft_ballad', 'intimate_narration', 'power_pop',
    'melancholy', 'bright_joy', 'fragile_tender',
)
MERGES = {
    'soft_ballad': 'gentle',
    'intimate_narration': 'gentle',
    'melancholy': 'sad',
    'fragile_tender': 'sad',
    'power_pop': 'bold',
    'bright_joy': 'bold',
}

assert style_catalog.VISIBLE_STYLES == VISIBLE
assert style_catalog.TECHNIQUE_IDS == TECHNIQUES
assert style_catalog.STYLE_IDS == VISIBLE + LEGACY
assert tuple(style_catalog.STYLE_MERGES.items()) == tuple(MERGES.items())
assert style_catalog.DEFAULT_STYLE_ID == 'none'
assert style_catalog.DEFAULT_TECHNIQUE_ID == 'none'
assert style_catalog.DEFAULT_NEW_STYLE_ID == 'gentle'
assert [style_catalog.merged_style(value) for value in LEGACY] == [
    MERGES[value] for value in LEGACY
]
assert all(style_catalog.is_style(value) for value in style_catalog.STYLE_IDS)
assert not any(style_catalog.is_visible_style(value) for value in LEGACY)
assert all(style_catalog.is_technique(value) for value in TECHNIQUES)

# The registry is deliberately pure metadata: importing it cannot pull Qt,
# NumPy, a model, a voicebank, or an execution module into consumers.
registry_source = Path(style_catalog.__file__).read_text(encoding='utf-8')
imported = set()
for node in ast.walk(ast.parse(registry_source)):
    if isinstance(node, ast.Import):
        imported.update(alias.name.split('.')[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split('.')[0])
assert imported <= {'__future__', 'types'}, imported

assert style_catalog.style_label('none', card=True) == '不叠加风格'
assert style_catalog.style_label('sad', card=True) == '悲伤〔实验〕'
assert style_catalog.style_card_category('gentle') == '声线（柔和）'
assert style_catalog.style_card_category('sad') == '情绪（悲伤）'
assert style_catalog.style_card_category('bold') == '演唱表现（明亮欢快）'
assert style_catalog.style_category('sad') == 'emotion'
assert style_catalog.style_category('gentle') == 'singing_style'
assert style_catalog.style_pitch_default('gentle') is True
assert style_catalog.style_pitch_default('soft_ballad') is False
assert style_catalog.style_pitch_default('power_pop') is False
assert style_catalog.technique_label('none') == '原唱法'
assert style_catalog.technique_label('chinese_opera') == '中文戏腔〔实验〕'
assert style_catalog.technique_description('chinese_opera') == (
    '字头倚滑、音间衔接、长音颤音与收腔；保留已检测到的原有颤音。')
assert style_catalog.technique_pitch_default('chinese_opera') is True
assert all(style_catalog.technique_descriptor(value)['category'] == 'singing_technique'
           for value in TECHNIQUES)
assert all(row['merge_to'] == MERGES.get(row['id'])
           for row in (dict(id=value, **style_catalog.style_metadata(value)) for value in LEGACY))

assert style_presets.visible_presets() == VISIBLE
assert all(style_presets.merged_preset(value) == MERGES[value] for value in LEGACY)
assert style_presets.CONTROLS == expression_controls.control_limits(
    expression_controls.STYLE_CONTROL_NAMES)
assert list(style_presets.CONTROLS) == list(expression_controls.STYLE_CONTROL_NAMES)
assert style_presets.PRESETS['none']['controls'] == {}
assert style_presets.PRESETS['gentle']['controls'] == {
    'loudness': (-3.2, -.8, -2.4), 'tension': (-.36, -.10, -.26),
    'breathiness': (.20, .03, .20), 'voicing': (-.04, 0., -.08)}
assert style_presets.PRESETS['soft_ballad']['controls'] == {
    'loudness': (-.8, .5, -1.5), 'tension': (-.18, -.08, -.18),
    'breathiness': (.06, .02, .12), 'voicing': (0., 0., -.06)}
assert style_presets.PRESETS['power_pop']['controls'] == {
    'loudness': (2.5, 1.8, .5), 'tension': (.30, .20, .10),
    'breathiness': (-.10, -.06, -.02)}
assert all(style_presets.PRESETS[value]['label'] == style_catalog.style_label(value)
           for value in style_presets.PRESETS)
assert all(style_presets.PRESETS[value]['category'] == style_catalog.style_category(value)
           for value in style_presets.PRESETS)

# Workflow restores both new and old IDs without applying the display mapping.
for value in VISIBLE + LEGACY:
    options = dict(workflow.DEFAULT_OPTIONS, performance_style=value, style_pitch=False)
    checked = workflow.validate_options(options)
    assert checked['performance_style'] == value
assert workflow.DEFAULT_OPTIONS['performance_style'] == 'none'
assert workflow.DEFAULT_OPTIONS['style_technique'] == 'none'

# MCP metadata is the same catalogue and keeps technique `none` independent.
from agent_style import PRESET_IDS, TECHNIQUE_IDS, _preset_catalog
presets, techniques = _preset_catalog()
assert PRESET_IDS == VISIBLE and TECHNIQUE_IDS == TECHNIQUES
assert [row['id'] for row in presets] == list(VISIBLE)
assert [row['id'] for row in techniques] == list(TECHNIQUES)
assert techniques[0] == {
    'id': 'none', 'label': '原唱法', 'category': 'singing_technique',
    'pitch_default': False, 'description': '不添加独立唱腔技法。',
}

# GUI cards consume the registry projections rather than local copies.
from tuning_ui import (
    style_card_description, style_card_keys, style_card_label,
    style_pitch_default, technique_card_keys, technique_card_label,
    technique_card_description, ui_merged_preset,
)
assert style_card_keys() == VISIBLE
assert technique_card_keys() == TECHNIQUES
assert style_card_label('bold') == '明亮欢快〔实验〕'
assert '情绪（悲伤）' in style_card_description('sad')
assert style_card_label('none') == '不叠加风格'
assert technique_card_label('none') == '原唱法'
assert technique_card_label('chinese_opera') == '中文戏腔〔实验〕'
assert technique_card_description('chinese_opera') == (
    '字头倚滑、音间衔接、长音颤音与收腔；保留已检测到的原有颤音。')
assert all(ui_merged_preset(value) == MERGES[value] for value in LEGACY)
assert all(style_pitch_default(value) == style_catalog.style_pitch_default(value)
           for value in style_catalog.STYLE_IDS + TECHNIQUES)

# Drafts accept visible style IDs and the independent technique only.
INPUT_SHA = 'a' * 64
for value in VISIBLE:
    normalized = style_drafts.normalize_draft(INPUT_SHA, {
        'input_sha256': INPUT_SHA,
        'tool': {'performance_style': value, 'style_technique': 'none'},
    })
    assert normalized['tool']['performance_style'] == value
for value in LEGACY:
    try:
        style_drafts.normalize_draft(INPUT_SHA, {
            'input_sha256': INPUT_SHA,
            'tool': {'performance_style': value},
        })
    except style_drafts.DraftError as error:
        assert error.code == 'invalid_draft'
    else:
        raise AssertionError('draft accepted legacy style ' + value)
technique_draft = style_drafts.normalize_draft(INPUT_SHA, {
    'input_sha256': INPUT_SHA,
    'tool': {'performance_style': 'none', 'style_technique': 'chinese_opera'},
})
assert technique_draft['tool']['style_technique'] == 'chinese_opera'
for value in LEGACY:
    try:
        style_presets.region_settings([
            {'start': 0., 'end': .25, 'preset': value}])
    except ValueError:
        pass
    else:
        raise AssertionError('new region accepted legacy style ' + value)

# Frozen execution curves from the pre-registry baseline. These are expression
# outputs only (pitch disabled), so canonical UI merges cannot hide old recipes.
Q = 705600000
CURVE = {'mode': 'linear', 'points': [0, 0.0, Q, -.1, 2 * Q, .12, 4 * Q, .02], 'custom': 'keep'}
project_data = {
    'version': 125,
    'time': {'tempo': [{'position': 0, 'bpm': 120.0}]},
    'tracks': [{
        'name': 'Lead Vocal',
        'mainRef': {'blickOffset': 0, 'pitchOffset': 0, 'isInstrumental': False,
                    'database': {'name': 'test bank'}, 'voice': {'preset': 'keep'}},
        'mainGroup': {
            'uuid': 'kept',
            'notes': [{'onset': 0, 'duration': 4 * Q, 'pitch': 60, 'lyrics': '啊'}],
            'parameters': {
                'loudness': copy.deepcopy(CURVE), 'tension': copy.deepcopy(CURVE),
                'breathiness': copy.deepcopy(CURVE),
                'voicing': {'mode': 'linear', 'points': [0, .9, 4 * Q, .92]},
                'pitchDelta': {'mode': 'linear', 'points': [0, 0, Q, 12, 4 * Q, 0]},
            },
        },
    }],
    'library': [{'keep': True}],
}
frozen = copy.deepcopy(project_data)
EXPECTED = {
    ('soft_ballad', .3): 'c0c2a0c07a9dcbfdcfcfe7e1db63f52f6c4d7a04f27360c95de17f122bd59394',
    ('soft_ballad', .6): 'f6f12fd5a901eec1033d87ab3dc2d04e3ab4891b99e40fadd16eed5e4d9f1cce',
    ('intimate_narration', .3): 'e303bab6272258fc6a55a84bc3965efe0a59d25df11fac44650705a37ef69747',
    ('intimate_narration', .6): 'b4652cef13f104d1e4d0a798383fc4a89adba3dcda95656c6ce94ed9558e9687',
    ('power_pop', .3): '09d2c160d0b6f27d3f68d326693b4d1a24d150814d333847090dcab7c0bbf9c2',
    ('power_pop', .6): '4b5ef15de8cfc7b127c3f675b0529daf0fdecb898ededbc1ff38db07b3f8cbf3',
    ('melancholy', .3): 'c353970190302b4b2804fd5cbc9451085910c6a0a6e8a2557d22840ecce3b057',
    ('melancholy', .6): '116865b7c1e59f373f63cf167a3fd6f5e044a342ad78c3a77d12decda7317972',
    ('bright_joy', .3): '06a1f4c64da61151356cd5527976756350f1a88d6b81ca1f78dc7cd87f5f8816',
    ('bright_joy', .6): '9589a8cb7ed87703e783d5f288745d0613a3234d88da2670d8e22a76d742818f',
    ('fragile_tender', .3): 'f8d27a00f8757fe614265b041e4c6ddd6bfca0c3d9214771a5b080641385dfd3',
    ('fragile_tender', .6): 'c320032f4c6984ec35b874b9e6ec3793e42e255717e689a02ce773a132d26fe7',
    ('chinese_opera', .3): '413cbb03f98e720af1fdfa7e50dbf1c1c0f51ccdb95d75b7afdb1a5148dff120',
    ('chinese_opera', .6): 'b9ee0c9bfa004fecf49be51e3456565c2f5625c4d47dd127bb7f21f0f79cb41e',
}
receipts = {}
for style_id in LEGACY + ('chinese_opera',):
    for strength in (.3, .6):
        out, receipt = style_presets.apply_style(
            project_data, style_id, strength=strength, track_indices=[0],
            density_mode='original', pitch_enabled=False,
        )
        payload = json.dumps(out, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        assert hashlib.sha256(payload).hexdigest() == EXPECTED[(style_id, strength)], (style_id, strength)
        assert receipt['preset'] == style_id
        assert receipt['label'] == style_presets.PRESETS[style_id]['label']
        assert receipt['category'] == style_presets.PRESETS[style_id]['category']
        receipts[(style_id, strength)] = receipt
    deltas_low = receipts[(style_id, .3)]['tracks'][0]['controls']
    deltas_high = receipts[(style_id, .6)]['tracks'][0]['controls']
    for channel in deltas_low:
        assert abs(deltas_high[channel]['maximum_applied_delta']
                   - 2 * deltas_low[channel]['maximum_applied_delta']) < 1e-10
assert project_data == frozen
assert style_presets.apply_style(
    project_data, 'soft_ballad', strength=.6, track_indices=[0], pitch_enabled=False)[0] != (
        style_presets.apply_style(
            project_data, 'gentle', strength=.6, track_indices=[0], pitch_enabled=False)[0])

print('PASS: pure style registry, canonical GUI/MCP/draft IDs, independent technique, '
      'legacy UI-only merges, shared expression limits, and frozen old-ID curves')