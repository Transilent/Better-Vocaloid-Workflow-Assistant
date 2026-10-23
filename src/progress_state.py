"""Structured progress: ratios describe a subtask, never guessed total time."""
import json
import math
import re
import threading
import time
from pathlib import Path
from common import atomic_json

TITLES = {'metadata': '准备资料', 'download': '下载 / 导入', 'audio': '解码音频',
          'separation': '分离声部', 'midi': '生成 MIDI', 'subtitles': '导出字幕', 'exports':'导出文件'}
_PUBLISHED = {}
_PUBLISH_LOCK = threading.Lock()
MIN_WRITE_INTERVAL = .2


def publish(job, stage, detail, done=None, total=None, **extra):
    data = {'stage': stage, 'detail': detail, 'updated': time.time(), **extra}
    if done is not None and total and total > 0:
        data.update(done=max(0, min(done, total)), total=total)
    path = Path(job) / 'progress.json'
    key, now = str(path.absolute()).casefold(), time.monotonic()
    with _PUBLISH_LOCK:
        previous = _PUBLISHED.get(key)
        if previous:
            stamp, last = previous
            same_phase = (last['stage'], last['detail']) == (stage, detail)
            completed = data.get('total') and data.get('done') == data['total']
            new_completion = completed and (last.get('done'), last.get('total')) != (data['done'], data['total'])
            if same_phase and not new_completion and 0 <= now - stamp < MIN_WRITE_INTERVAL:
                return False
        atomic_json(path, data)
        if len(_PUBLISHED) >= 128 and key not in _PUBLISHED:
            _PUBLISHED.pop(next(iter(_PUBLISHED)))
        _PUBLISHED[key] = (now, data)
    return True


def from_log(job, text, voice):
    if text.startswith(('[VWA Progress] ', '[BVWA Progress] ')):
        try:
            data = json.loads(text[text.index('] ') + 2:])
            detail, done, total = data['detail'], data['done'], data['total']
            if (not isinstance(detail, str) or not 1 <= len(detail) <= 80 or type(done) is not int
                    or type(total) is not int or not 0 <= done <= total or total <= 0):
                return
            name = {'lead': '主唱', 'backing': '和声', 'vocals': '人声'}.get(data.get('voice'), voice)
            publish(job, 'midi', f'{name} · {detail}', done, total)
        except (ValueError, KeyError, TypeError):
            pass
        return
    patterns = [(r'ASR batch (\d+)/(\d+) done', '歌词识别'),
                (r'(?:GAME|HFA).*?(\d+)\s*/\s*(\d+)', '音符 / 歌词对齐'),
                (r'Processing:\s*\d+%.*?\b(\d+)\s*/\s*(\d+)', '片段处理')]
    for pattern, title in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            publish(job, 'midi', f'{voice} · {title}', int(match[1]), int(match[2]))
            return
    if any(word in text for word in ('Loading ', 'Stage ', 'Extracting pitches', 'forced alignment', 'Sliced into')):
        phase = ('加载模型' if 'Loading ' in text else '音高提取' if 'pitches' in text
                 else '歌词对齐' if 'alignment' in text else '歌词识别' if 'ASR' in text else '准备片段')
        publish(job, 'midi', f'{voice} · {phase}')


def snapshot(job):
    job = Path(job)
    try:
        status = json.loads((job / 'status.json').read_text(encoding='utf-8'))
        if not isinstance(status, dict): status = {}
    except (OSError, ValueError):
        status = {}
    try:
        event = json.loads((job / 'progress.json').read_text(encoding='utf-8'))
        if not isinstance(event, dict): event = {}
    except (OSError, ValueError):
        event = {}
    stages = status.get('planned_steps', list(TITLES)[:-1])
    current = status.get('current_step')
    if event.get('stage') != current:
        event = {}
    steps = status.get('steps', {})
    completed = sum(steps.get(k, {}).get('state') == 'done' for k in stages)
    elapsed = status.get('elapsed_seconds', 0)
    if status.get('state') == 'running':
        from task_store import locked
        if locked(job):
            elapsed += max(0, time.time() - status.get('run_started', time.time()))
        else:
            status['state'] = 'interrupted'
            started, now = status.get('run_started', time.time()), time.time()
            observed = [started]
            for name in ('status.json', 'progress.json'):
                try:
                    stamp = (job / name).stat().st_mtime
                except OSError:
                    continue
                if name == 'progress.json':
                    if not event: continue
                    updated = event.get('updated', stamp)
                    if isinstance(updated, (int, float)) and math.isfinite(updated):
                        stamp = min(stamp, updated)
                if started <= stamp <= now:
                    observed.append(stamp)
            # An interrupted job cannot accrue time while merely being viewed.
            # This is the latest confirmed write, not an estimated crash time.
            elapsed += max(0, max(observed) - started)
    return {'status': status, 'stages': stages, 'completed': completed,
            'current': current, 'event': event, 'elapsed': int(elapsed)}
