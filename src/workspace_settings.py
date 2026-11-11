"""Settings and startup choices shared by the Quick application and its views."""

from pathlib import Path

import appearance_state
from common import ROOT, atomic_json, load_config, output_directory
from support_info import QQ_GROUP
import startup_preferences


_CHOICES = {
    'theme': ('light', 'dark', 'system'),
    'material': ('solid', 'glass'),
    'ui_scale': (100, 115, 130),
    'ui_language': ('zh-CN', 'en', 'ja'),
    'download_workers': tuple(range(1, 9)),
}
_BOOL = {'reduce_motion', 'check_for_updates', 'auto_restart_updates'}
_CONFIG = {'theme', 'download_source', 'download_workers', 'check_for_updates', 'auto_restart_updates'}


class SettingsState:
    def __init__(self, sources):
        config = load_config(False)
        self.sources = tuple(value for _, value in sources)
        self.values = appearance_state.load_appearance()
        self.values['theme'] = config.get('theme', 'light') if config.get('theme') in _CHOICES['theme'] else 'light'
        workers = config.get('download_workers', 4)
        self.values['download_workers'] = workers if type(workers) is int and 1 <= workers <= 8 else 4
        source = config.get('download_source', 'auto')
        self.values['download_source'] = source if source in self.sources else self.sources[0]
        for key in ('check_for_updates', 'auto_restart_updates'):
            self.values[key] = config.get(key, True) is True
        self.values['output_directory'] = str(output_directory())
        self.preferences = startup_preferences.load_preferences(ROOT)
        self.release = {}
        self.update_plan = None
        self.update = {'update_open': False, 'update_state': 'idle', 'message': '', 'done': 0, 'total': 0}
        self.tour_open = False
        self.tour_step = 0

    @property
    def startup_view(self):
        return {
            **self.update,
            'tour_open': self.tour_open,
            'tour_step': self.tour_step,
            'version': self.release.get('version') or '',
            'notes': str(self.release.get('notes') or '')[:60000],
            'url': self.release.get('url') or '',
            'can_update': bool(self.release.get('update')),
            'can_cancel': self.update['update_state'] == 'downloading',
            'apply_ready': self.update_plan is not None,
            'qq_group': QQ_GROUP,
        }

    def save(self, name, value):
        if name in _CHOICES:
            if isinstance(value, bool) or value not in _CHOICES[name]:
                raise ValueError('设置选项无效。')
        elif name in _BOOL:
            if type(value) is not bool:
                raise ValueError('设置开关无效。')
        elif name == 'download_source':
            if value not in self.sources:
                raise ValueError('下载来源无效。')
        else:
            raise ValueError('未知的设置选项。')
        if self.values.get(name) == value:
            return False
        if name in _CONFIG:
            config = load_config(False)
            config[name] = value
            config.pop('startup_auto_update', None)
            atomic_json(ROOT / 'config.json', config)
        else:
            appearance_state.save_appearance({name: value})
        self.values[name] = value
        return True

    def show_tour(self):
        self.tour_step = 0
        self.tour_open = True

    def finish_tour(self, result):
        self.preferences = startup_preferences.finish_tour(ROOT, result)
        self.tour_open = False

    def set_release(self, release, *, startup):
        from updater import version_key

        version = release.get('version')
        if version:
            version_key(version)
        self.release = dict(release)
        available = bool(version and release.get('available'))
        # This separately shipped Quick trial uses the official Release page.
        # In-place installation requires a Quick-aware official launcher and update route.
        if not self.installed_quick_frontend():
            self.release['update'] = None
        self.update.update(
            update_state='available' if available else 'current',
            update_open=available and (not startup or startup_preferences.reminder_due(version, self.preferences)),
            message=(f'发现新版本 {version}。' if self.release.get('update') else
                     f'发现新版本 {version}，请从发布页面获取适用的完整包。') if available else '暂无可用更新。',
            done=0, total=0,
        )

    @staticmethod
    def installed_quick_frontend():
        from task_store import read_json

        version = read_json(ROOT / 'version.json')
        return version.get('desktop_frontend') == 'quick' and (ROOT / 'VWA.exe').is_file()

    def defer(self, seconds):
        if self.release.get('version'):
            self.preferences = startup_preferences.defer_reminder(ROOT, self.release['version'], seconds)
        self.update['update_open'] = False
