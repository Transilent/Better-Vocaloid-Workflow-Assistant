"""Pure metadata registry for style cards, techniques, and compatibility IDs.

Recipes and their execution stay in :mod:`style_presets`.  This module owns the
compact user-facing catalogue that GUI, MCP, drafts, and workflow validation can
share without importing Qt, models, calibration, or a voicebank.  Legacy IDs are
listed with their original metadata and a UI-only merge target; execution must
keep resolving the legacy ID itself so an old recipe is never silently replaced.
"""
from __future__ import annotations

from types import MappingProxyType


DEFAULT_STYLE_ID = 'none'
DEFAULT_NEW_STYLE_ID = 'gentle'
DEFAULT_TECHNIQUE_ID = 'none'
_DEFAULT_DESCRIPTION = '在原工程上添加所选风格的局部增量，生成后请在声库中试听。'
_STYLE_METADATA = MappingProxyType({
    'none': MappingProxyType({
        'label': '保留原表现',
        'card_label': '不叠加风格',
        'category': 'singing_style',
        'card_category': None,
        'pitch_default': False,
        'description': _DEFAULT_DESCRIPTION,
        'card_description': '保留输入工程现有参数，不添加所选风格。',
        'region_label': '风格区域',
        'color': '#8190a8',
        'visible': True,
        'merge_to': None,
    }),
    'gentle': MappingProxyType({
        'label': '柔和〔实验〕',
        'category': 'singing_style',
        'card_category': '声线（柔和）',
        'pitch_default': True,
        'description': '轻柔起音、稳定的有声核心与渐弱收尾；可分别调整各段表现。',
        'region_label': '柔和',
        'color': '#187f78',
        'visible': True,
        'merge_to': None,
    }),
    'sad': MappingProxyType({
        'label': '悲伤〔实验〕',
        'category': 'emotion',
        'card_category': '情绪（悲伤）',
        'pitch_default': True,
        'description': '保持清楚的有声核心，长音中段收敛，句尾缓慢下落；与柔和声线分开控制。',
        'region_label': '悲伤',
        'color': '#8a5ba8',
        'visible': True,
        'merge_to': None,
    }),
    'bold': MappingProxyType({
        'label': '明亮欢快〔实验〕',
        'category': 'singing_style',
        'card_category': '演唱表现（明亮欢快）',
        'pitch_default': True,
        'description': '轻快起音、清晰衔接与明亮的持续段；可分别调整各段表现。',
        'region_label': '明亮欢快',
        'color': '#b26519',
        'visible': True,
        'merge_to': None,
    }),
    'soft_ballad': MappingProxyType({
        'label': '柔和抒情〔实验〕',
        'category': 'singing_style',
        'card_category': None,
        'pitch_default': False,
        'description': _DEFAULT_DESCRIPTION,
        'region_label': '柔和抒情',
        'color': None,
        'visible': False,
        'merge_to': 'gentle',
    }),
    'intimate_narration': MappingProxyType({
        'label': '轻声叙述〔实验〕',
        'category': 'singing_style',
        'card_category': None,
        'pitch_default': False,
        'description': _DEFAULT_DESCRIPTION,
        'region_label': '轻声叙述',
        'color': None,
        'visible': False,
        'merge_to': 'gentle',
    }),
    'power_pop': MappingProxyType({
        'label': '有力流行〔实验〕',
        'category': 'singing_style',
        'card_category': None,
        'pitch_default': False,
        'description': _DEFAULT_DESCRIPTION,
        'region_label': '有力流行',
        'color': None,
        'visible': False,
        'merge_to': 'bold',
    }),
    'melancholy': MappingProxyType({
        'label': '忧伤克制〔实验〕',
        'category': 'emotion',
        'card_category': None,
        'pitch_default': True,
        'description': '较轻的起音、克制的句中力度与下行收尾；保留原有长音起伏。',
        'region_label': '忧伤克制',
        'color': None,
        'visible': False,
        'merge_to': 'sad',
    }),
    'bright_joy': MappingProxyType({
        'label': '明亮轻快〔实验〕',
        'category': 'emotion',
        'card_category': None,
        'pitch_default': True,
        'description': '轻快的起音与清晰的有声核心，局部音高更利落；不改变音符节奏。',
        'region_label': '明亮轻快',
        'color': None,
        'visible': False,
        'merge_to': 'bold',
    }),
    'fragile_tender': MappingProxyType({
        'label': '脆弱含泪〔实验〕',
        'category': 'emotion',
        'card_category': None,
        'pitch_default': True,
        'description': '含泪感的轻声起收与局部音高上挑；请按所用声库试听。',
        'region_label': '脆弱含泪',
        'color': None,
        'visible': False,
        'merge_to': 'sad',
    }),
})
_TECHNIQUE_METADATA = MappingProxyType({
    'none': MappingProxyType({
        'label': '原唱法',
        'category': 'singing_technique',
        'pitch_default': False,
        'description': '不添加独立唱腔技法。',
        'region_label': '',
        'color': None,
    }),
    'chinese_opera': MappingProxyType({
        'label': '中文戏腔〔实验〕',
        'category': 'singing_technique',
        'pitch_default': True,
        'description': '字头倚滑、音间衔接、长音颤音与收腔；保留已检测到的原有颤音。',
        'region_label': '戏腔',
        'color': '#b74f55',
    }),
})

# Public order and compatibility projections have one source of truth: the
# metadata mappings just above. Keep these constants stable for existing callers.
STYLE_IDS = tuple(_STYLE_METADATA)
VISIBLE_STYLES = tuple(value for value, entry in _STYLE_METADATA.items()
                       if entry['visible'])
LEGACY_STYLES = tuple(value for value, entry in _STYLE_METADATA.items()
                      if not entry['visible'])
_MERGE_ROWS = sorted(
    ((value, entry['merge_to']) for value, entry in _STYLE_METADATA.items()
     if entry.get('merge_to') is not None),
    key=lambda row: (VISIBLE_STYLES.index(row[1]), STYLE_IDS.index(row[0])),
)
STYLE_MERGES = MappingProxyType(dict(_MERGE_ROWS))
TECHNIQUE_IDS = tuple(_TECHNIQUE_METADATA)
_VISIBLE_SET = frozenset(VISIBLE_STYLES)
_TECHNIQUE_SET = frozenset(TECHNIQUE_IDS)
if any(target not in VISIBLE_STYLES for target in STYLE_MERGES.values()):
    raise RuntimeError('style_catalog legacy merge map is invalid')


def _metadata(table, value):
    if not isinstance(value, str):
        return {}
    entry = table.get(value)
    return dict(entry) if entry is not None else {}


def style_metadata(value):
    """Return an independent metadata copy for any known stored style ID."""
    return _metadata(_STYLE_METADATA, value)


def technique_metadata(value):
    """Return an independent metadata copy for any known technique ID."""
    return _metadata(_TECHNIQUE_METADATA, value)


def recipe_metadata(value):
    """Return metadata projected onto style and technique execution recipes."""
    entry = style_metadata(value) or technique_metadata(value)
    return {key: entry.get(key) for key in ('label', 'category', 'pitch_default', 'description')}


def style_descriptor(value):
    """Return the compact public MCP descriptor for a visible style."""
    entry = style_metadata(value)
    if not entry or value not in _VISIBLE_SET:
        return {}
    return {key: entry.get(key) for key in ('label', 'category', 'pitch_default', 'description')}


def technique_descriptor(value):
    """Return the compact public MCP descriptor for an independent technique."""
    entry = technique_metadata(value)
    if not entry:
        return {}
    return {key: entry.get(key) for key in ('label', 'category', 'pitch_default', 'description')}


def style_descriptors(values=VISIBLE_STYLES):
    return [dict(id=value, **style_descriptor(value)) for value in values if value in _VISIBLE_SET]


def technique_descriptors(values=TECHNIQUE_IDS):
    return [dict(id=value, **technique_descriptor(value)) for value in values if value in _TECHNIQUE_SET]


def is_style(value):
    return isinstance(value, str) and value in _STYLE_METADATA


def is_visible_style(value):
    return isinstance(value, str) and value in _VISIBLE_SET


def is_technique(value):
    return isinstance(value, str) and value in _TECHNIQUE_SET


def style_label(value, *, card=False):
    entry = style_metadata(value) or technique_metadata(value)
    key = 'card_label' if card else 'label'
    return entry.get(key, entry.get('label', ''))


def technique_label(value, *, card=False):
    entry = technique_metadata(value)
    key = 'card_label' if card else 'label'
    return entry.get(key, entry.get('label', ''))


def style_description(value, *, card=False):
    entry = style_metadata(value) or technique_metadata(value)
    key = 'card_description' if card else 'description'
    return entry.get(key, entry.get('description', _DEFAULT_DESCRIPTION))


def technique_description(value):
    return technique_metadata(value).get('description', '')


def style_card_category(value):
    return style_metadata(value).get('card_category')


def style_pitch_default(value):
    return (style_metadata(value) or technique_metadata(value)).get('pitch_default') is True


def technique_pitch_default(value):
    return technique_metadata(value).get('pitch_default') is True


def style_category(value):
    return (style_metadata(value) or technique_metadata(value)).get('category')


def merged_style(value):
    """Return the card a legacy choice may be shown as; execution stays untouched."""
    return STYLE_MERGES.get(value, value) if isinstance(value, str) else value


def style_color(value):
    return style_metadata(value).get('color')


def technique_color(value):
    return technique_metadata(value).get('color')


def style_region_label(value):
    return style_metadata(value).get('region_label') or '风格区域'


def technique_region_label(value):
    return technique_metadata(value).get('region_label') or ''