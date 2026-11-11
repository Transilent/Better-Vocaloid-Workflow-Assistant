"""Appearance preferences shared across desktop frontends."""

from common import ROOT, atomic_json, workspace_state
from i18n import normalize_language


DEFAULTS = {'ui_scale': 100, 'material': 'glass', 'reduce_motion': False, 'ui_language': 'zh-CN'}


def normalize(values):
    values = values if isinstance(values, dict) else {}
    result = dict(DEFAULTS)
    result['ui_language'] = normalize_language(values.get('ui_language'))
    if type(values.get('ui_scale')) is int and values['ui_scale'] in (100, 115, 130):
        result['ui_scale'] = values['ui_scale']
    if values.get('material') in ('glass', 'solid'):
        result['material'] = values['material']
    if type(values.get('reduce_motion')) is bool:
        result['reduce_motion'] = values['reduce_motion']
    return result


def load_appearance():
    return normalize(workspace_state().get('appearance', {}))


def merge_appearance(state, values):
    state = dict(state)
    previous = state.get('appearance')
    appearance = dict(previous) if isinstance(previous, dict) else {}
    updates = values if isinstance(values, dict) else {}
    appearance.update(normalize({**appearance, **updates}))
    state['appearance'] = appearance
    return state


def save_appearance(values):
    atomic_json(ROOT / 'cache/workspace.json', merge_appearance(workspace_state(), values))
