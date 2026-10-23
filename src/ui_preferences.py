"""Appearance-only preferences, merged with the latest workspace state."""

from common import environment_value
from i18n import tr as _tr
import os
from PyQt5.QtWidgets import QCheckBox, QComboBox, QVBoxLayout, QWidget
from common import ROOT, atomic_json, workspace_state


DEFAULTS = {'ui_scale': 100, 'material': 'glass', 'reduce_motion': False, 'ui_language': 'zh-CN'}


def normalize(values):
    values = values if isinstance(values, dict) else {}
    result = dict(DEFAULTS)
    from i18n import normalize_language
    result['ui_language'] = normalize_language(values.get('ui_language'))
    if type(values.get('ui_scale')) is int and values['ui_scale'] in (100, 115, 130):
        result['ui_scale'] = values['ui_scale']
    if values.get('material') in ('glass', 'solid'):
        result['material'] = values['material']
    for key in ('reduce_motion',):
        if type(values.get(key)) is bool:
            result[key] = values[key]
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
    atomic_json(ROOT/'cache/workspace.json', merge_appearance(workspace_state(), values))


class AppearancePanel(QWidget):
    def __init__(self, owner):
        super().__init__()
        from desktop_theme import card, label
        from ui_components import choice
        self.owner, self.loading = owner, True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame, body = card(_tr('外观'), _tr('调整主题、材质、缩放和动效。'))
        body.addWidget(label(_tr('主题'), 'muted'))
        # Observe the same selector used by the existing config/theme writer.
        self.theme = QComboBox()
        self.theme.addItem(_tr('浅色'), 'light'); self.theme.addItem(_tr('深色'), 'dark')
        self.theme.setCurrentIndex(self.theme.findData(owner.theme_picker.currentData()))
        self.theme.currentIndexChanged.connect(self.change_theme)
        owner.theme_picker.currentIndexChanged.connect(self.sync_theme)
        body.addWidget(choice(self.theme, columns=2, compact=True))
        values = getattr(owner, '_vwa_appearance', load_appearance())
        from i18n import LANGUAGES, current_language
        body.addWidget(label(_tr('界面语言'), 'muted'))
        self.language = QComboBox()
        for title, code in LANGUAGES:
            self.language.addItem(title, code)
        self.language.setCurrentIndex(self.language.findData(values['ui_language']))
        body.addWidget(choice(self.language, columns=3, compact=True))
        self.language_hint = label(_tr('修改界面语言后，下次启动生效。歌词识别语言在准备页单独选择。'), 'muted')
        body.addWidget(self.language_hint)
        self.language.currentIndexChanged.connect(self.changed)
        body.addWidget(label(_tr('材质'), 'muted'))
        self.material = QComboBox()
        self.material.addItem(_tr('月光玻璃'), 'glass'); self.material.addItem(_tr('纯色阅读'), 'solid')
        self.material.setCurrentIndex(self.material.findData(values['material']))
        body.addWidget(choice(self.material, columns=2, compact=True))
        body.addWidget(label(_tr('界面缩放'), 'muted'))
        self.scale = QComboBox()
        for value in (100, 115, 130): self.scale.addItem(str(value)+'%', value)
        self.scale.setCurrentIndex(self.scale.findData(values['ui_scale']))
        body.addWidget(choice(self.scale, columns=3, compact=True))
        self.motion = QCheckBox(_tr('减少动效'))
        self.motion.setChecked(values['reduce_motion']);body.addWidget(self.motion)
        body.addWidget(label(_tr('月光玻璃使用柔和透色背景；纯色阅读可减少背景色彩。'), 'muted'))
        outer.addWidget(frame)
        for control in (self.material, self.scale):control.currentIndexChanged.connect(self.changed)
        self.motion.toggled.connect(self.changed)
        self.loading = False

    def change_theme(self):
        self.owner.theme_picker.setCurrentIndex(self.owner.theme_picker.findData(self.theme.currentData()))

    def sync_theme(self):
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(self.theme.findData(self.owner.theme_picker.currentData()))
        self.theme.blockSignals(False)
        view = getattr(self.theme, '_vwa_choice', None)
        if view:view.sync()

    def changed(self, *args):
        if self.loading:return
        values = normalize({'material': self.material.currentData(), 'ui_scale': self.scale.currentData(),
                            'reduce_motion': self.motion.isChecked(), 'ui_language': self.language.currentData()})
        self.owner._vwa_appearance = values
        if environment_value('VWA_TESTING') != '1':save_appearance(values)
        from desktop_theme import apply_theme
        apply_theme(self.owner, self.owner.theme_picker.currentData())
