"""Persistent first-run and per-release reminder choices, independent of Qt."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import time

from common import atomic_json

TOUR_VERSION = 1
_VERSION = re.compile(r'v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z')


def _config(root):
    path=Path(root)/'config.json'
    try:value=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return {}
    if not isinstance(value,dict):raise ValueError('程序设置格式无效。')
    return value


def load_preferences(root):
    try:value=_config(root).get('startup_experience',{})
    except (OSError,ValueError):return {}
    return dict(value) if isinstance(value,dict) else {}


def tour_due(preferences):
    value=preferences.get('tour_version')
    return type(value) is not int or value<TOUR_VERSION


def _save(root,changes):
    config=_config(root)
    previous=config.get('startup_experience',{})
    value=dict(previous) if isinstance(previous,dict) else {}
    value.update(changes);config['startup_experience']=value
    atomic_json(Path(root)/'config.json',config)
    return value


def finish_tour(root,outcome):
    if outcome not in ('completed','skipped'):return load_preferences(root)
    return _save(root,{'tour_version':TOUR_VERSION,'tour_outcome':outcome})


def _version(value):
    if not isinstance(value,str) or len(value)>80 or _VERSION.fullmatch(value) is None:
        raise ValueError('更新提醒缺少有效版本号。')
    return value.removeprefix('v')


def reminder_due(version,preferences,*,now=None):
    version=_version(version)
    reminder=preferences.get('update_reminder')
    if not isinstance(reminder,dict) or reminder.get('version')!=version:return True
    if reminder.get('ignored') is True:return False
    until=reminder.get('until',0)
    if type(until) not in (int,float) or not math.isfinite(until):return True
    return until<=(time.time() if now is None else now)


def defer_reminder(root,version,seconds,*,now=None):
    version=_version(version)
    if type(seconds) is not int or seconds < -1 or seconds>366*86400:
        raise ValueError('更新提醒的延后时间无效。')
    instant=time.time() if now is None else now
    if type(instant) not in (int,float) or not math.isfinite(instant):raise ValueError('更新时间无效。')
    reminder={'version':version,'ignored':seconds==-1,'until':instant+seconds if seconds>0 else 0}
    return _save(root,{'update_reminder':reminder})


def reset_reminder(root):
    return _save(root,{'update_reminder':{}})
