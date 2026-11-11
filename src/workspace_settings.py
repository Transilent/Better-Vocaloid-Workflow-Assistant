"""Settings and startup choices shared by the Quick application and its views."""

import json
from pathlib import Path

import appearance_state
from common import ROOT, atomic_json, load_config, output_directory
from support_info import QQ_GROUP
import startup_preferences
import updater


_CHOICES = {
    'theme': ('light', 'dark', 'system'),
    'material': ('solid', 'glass'),
    'ui_scale': (100, 115, 130),
    'ui_language': ('zh-CN', 'en', 'ja'),
    'download_workers': tuple(range(1, 9)),
}
_BOOL = {'reduce_motion', 'check_for_updates', 'auto_restart_updates'}
_CONFIG = {'theme', 'download_source', 'download_workers', 'check_for_updates', 'auto_restart_updates'}
_UPDATE_RECEIPT_LIMIT = 64 * 1024
_UPDATE_NOTICE_LIMIT = 256 * 1024
_TEXT_LIMIT = 60000


def _bounded_json(path, *, limit):
    """Read one bounded regular JSON object without following links outside its folder."""
    path = Path(path)
    try:
        parent = path.parent.resolve()
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(parent) or not resolved.is_file() or resolved.stat().st_size > limit:
            return {}
        data = json.loads(resolved.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _bounded_text(value, limit=_TEXT_LIMIT):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    return str(value)[:limit] if isinstance(value, str) else ''


def load_update_recovery(root=ROOT):
    """Return only validated executable pending state and a bounded failure receipt."""
    root = Path(root).resolve()
    receipt = updater.pending_update(root)
    pending = None
    if isinstance(receipt, dict):
        version = receipt.get('version')
        plan = receipt.get('plan')
        if isinstance(version, str) and isinstance(plan, str) and Path(plan).is_absolute():
            notice = _bounded_json(root / 'cache/updates/last-notice.json', limit=_UPDATE_NOTICE_LIMIT)
            notes = ''
            if notice.get('version') == version:
                notes = _bounded_text(notice.get('notes'))
            pending = {'version': version, 'plan': plan, 'notes': notes}
    last = _bounded_json(root / 'cache/updates/last-result.json', limit=_UPDATE_RECEIPT_LIMIT)
    last_result = None
    if last.get('state') == 'failed':
        last_result = {
            'state': 'failed',
            'version': _bounded_text(last.get('version'), 80),
            'error': _bounded_text(last.get('error'), 1600),
            'reason': _bounded_text(last.get('reason'), 160),
        }
    return {'pending': pending, 'last_result': last_result}


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
        self.pending_version = ''
        self.pending_notes = ''
        self.last_update_result = {}
        self.restart_pending = False
        self.auto_restart_active = False
        self.restart_deferred = False
        self.restart_remaining = None
        self.operation_blockers = ()
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
            'notes': _bounded_text(self.release.get('notes')),
            'url': self.release.get('url') or '',
            'can_update': bool(self.release.get('update')),
            'can_cancel': self.update['update_state'] == 'downloading' or self.auto_restart_active,
            'apply_ready': self.update_plan is not None,
            'restart_pending': self.restart_pending,
            'auto_restart_active': self.auto_restart_active,
            'restart_deferred': self.restart_deferred,
            'restart_remaining': self.restart_remaining,
            'operation_blockers': list(self.operation_blockers),
            'last_update_state': self.last_update_result.get('state', ''),
            'qq_group': QQ_GROUP,
        }

    def restore_update_recovery(self, recovery):
        """Load a pending installer plan or the last bounded update failure."""
        recovery = recovery if isinstance(recovery, dict) else {}
        self.last_update_result = dict(recovery.get('last_result') or {})
        pending = recovery.get('pending')
        if isinstance(pending, dict):
            version = pending.get('version')
            plan = pending.get('plan')
            if isinstance(version, str) and isinstance(plan, str):
                self.update_plan = Path(plan)
                self.pending_version = version
                self.pending_notes = _bounded_text(pending.get('notes'))
                self.release = {
                    'version': version,
                    'available': True,
                    'update': None,
                    'notes': self.pending_notes,
                    'url': updater.RELEASES + '/tag/v' + version.removeprefix('v'),
                    'pending_plan': plan,
                }
                self.restart_pending = True
                self.auto_restart_active = False
                self.restart_deferred = False
                self.restart_remaining = None
                self.update.update(
                    update_open=False,
                    update_state='downloaded',
                    message=f'更新 {version} 已下载并通过校验；确认后会重启安装。',
                    done=0,
                    total=0,
                )
                return True
        if self.last_update_result.get('state') == 'failed':
            error = self.last_update_result.get('error') or self.last_update_result.get('reason') or '请重新检查更新。'
            self.update.update(update_state='failed', message=f'上次更新未完成：{error}')
        return False

    def open_recovered_pending(self):
        if not self.restart_pending or not self.pending_version:
            return False
        if not startup_preferences.reminder_due(self.pending_version, self.preferences):
            return False
        self.update['update_open'] = True
        return True

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
        version = release.get('version')
        if version:
            updater.version_key(version)
        available = bool(version and release.get('available'))
        if self.restart_pending and self.pending_version and (not available or version == self.pending_version):
            self.release = {
                'version': self.pending_version,
                'available': True,
                'update': None,
                'notes': self.pending_notes,
                'url': updater.RELEASES + '/tag/v' + self.pending_version.removeprefix('v'),
                'pending_plan': str(self.update_plan) if self.update_plan else '',
            }
            self.update.update(
                update_state='downloaded',
                update_open=(not startup or startup_preferences.reminder_due(self.pending_version, self.preferences)),
                message=f'更新 {self.pending_version} 已下载并通过校验；确认后会重启安装。',
                done=0,
                total=0,
            )
            return
        self.release = dict(release)
        if available and version != self.pending_version:
            self.update_plan = None
            self.restart_pending = False
            self.auto_restart_active = False
            self.restart_deferred = False
            self.restart_remaining = None
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

    def set_update_plan(self, plan, version):
        self.update_plan = Path(plan)
        self.pending_version = str(version or '')
        self.restart_pending = True
        self.auto_restart_active = False
        self.restart_deferred = False
        self.restart_remaining = None
        self.update.update(update_state='downloaded', message='下载完成，可重启安装更新。')

    def begin_auto_restart(self):
        if not self.restart_pending or self.update_plan is None:
            return False
        self.auto_restart_active = True
        self.restart_deferred = False
        self.restart_remaining = 10
        self.update.update(
            update_state='downloaded',
            message='更新已校验，将在 10 秒后自动重启安装。',
        )
        return True

    def defer_auto_restart(self, reason=''):
        if not self.restart_pending or self.update_plan is None:
            return False
        self.auto_restart_active = False
        self.restart_deferred = True
        self.restart_remaining = None
        message = '自动安装已暂缓；更新文件会保留，可稍后手动重启安装。'
        if reason:
            message = reason + ' ' + message
        self.update.update(update_state='downloaded', message=message)
        return True

    def block_manual_restart(self, blockers):
        self.operation_blockers = tuple(blockers or ())
        self.update.update(
            update_state='downloaded',
            message='请等待' + '、'.join(self.operation_blockers) + '结束后再重启安装。',
        )

    def tick_auto_restart(self, blockers):
        blockers = tuple(blockers or ())
        self.operation_blockers = blockers
        if not self.auto_restart_active or not self.restart_pending or self.update_plan is None:
            return False
        if blockers:
            self.defer_auto_restart('当前操作已开始（' + '、'.join(blockers) + '），自动安装已暂缓。')
            return False
        if self.restart_remaining is None:
            self.restart_remaining = 10
        if self.restart_remaining > 0:
            current = self.restart_remaining
            self.restart_remaining -= 1
            self.update.update(
                update_state='downloaded',
                message=f'更新已校验，将在 {current} 秒后自动重启安装。',
            )
            return False
        self.auto_restart_active = False
        self.update.update(update_state='applying', message='正在重启并安装更新…')
        return True

    @staticmethod
    def installed_quick_frontend():
        from task_store import read_json

        version = read_json(ROOT / 'version.json')
        return version.get('desktop_frontend') == 'quick' and (ROOT / 'VWA.exe').is_file()

    def defer(self, seconds):
        if self.release.get('version'):
            self.preferences = startup_preferences.defer_reminder(ROOT, self.release['version'], seconds)
        self.update['update_open'] = False
