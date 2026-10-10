"""Structured progress: ratios describe a subtask, never guessed total time."""
import json
import re
import time
from pathlib import Path
from common import atomic_json

TITLES = {'metadata': '准备资料', 'download': '下载 / 导入', 'audio': '解码音频',
          'separation': '分离声部', 'midi': '生成 MIDI', 'subtitles': '导出字幕'}


def publish(job, stage, detail, done=None, total=None, **extra):
    data = {'stage': stage, 'detail': detail, 'updated': time.time(), **extra}
    if done is not None and total and total > 0:
        data.update(done=max(0, min(done, total)), total=total)
    atomic_json(Path(job) / 'progress.json', data)


def from_log(job, text, voice):
    patterns = [(r'ASR batch (\d+)/(\d+) done', '歌词识别'),
                (r'(?:GAME|HFA).*?(\d+)\s*/\s*(\d+)', '音符 / 歌词对齐')]
    for pattern, title in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            publish(job, 'midi', f'{voice} · {title}', int(match[1]), int(match[2]))
            return
    if any(word in text for word in ('Loading ', 'Stage ', 'Extracting pitches', 'forced alignment', 'Sliced into')):
        phase = '加载模型' if 'Loading ' in text else '音高提取' if 'pitches' in text else '歌词对齐' if 'alignment' in text else '准备片段'
        publish(job, 'midi', f'{voice} · {phase}')


def snapshot(job):
    job = Path(job)
    try:
        status = json.loads((job / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        status = {}
    try:
        event = json.loads((job / 'progress.json').read_text(encoding='utf-8'))
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
            elapsed += max(0, event.get('updated', status.get('run_started', time.time())) - status.get('run_started', time.time()))
    return {'status': status, 'stages': stages, 'completed': completed,
            'current': current, 'event': event, 'elapsed': int(elapsed)}
