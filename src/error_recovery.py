"""User-facing failure classification and safe retry choices."""
import json
import re
from pathlib import Path
from common import atomic_json


def explain(error, stage='', cancelled=False):
    raw = str(error)
    text = raw.casefold()
    if cancelled:
        return {'title': '任务已停止', 'message': '已完成的阶段已保留，可以继续处理。', 'action': 'resume'}
    rules = [
        (('没有可用的 midi 歌词事件',), '字幕缺少歌词时间',
         'MIDI 音符已经保留。可以打开字幕工具，导入歌词后编辑时间再导出。', 'subtitles'),
        (('out of memory', '显存', '0x8007000e', 'bad allocation'), '内存或显存不足',
         '关闭占用显存的软件后重试，或改用 CPU。已完成的音频会保留。', 'cpu'),
        (('cuda', 'directml', 'dmlexecution', 'device removed', 'd3d12'), '推理设备不可用',
         '可以切换为 CPU 后继续，也可以在工作流中检查设备设置。', 'cpu'),
        (('no space', 'disk full', '磁盘', 'winerror 112'), '磁盘空间不足',
         '先在“缓存与磁盘”检查占用；清理后可从失败阶段继续。', 'storage'),
        (('timed out', 'timeout', 'urlopen', 'connection', 'http error', '403', '412', '读取 b站', '下载'), '网络请求未完成',
         '检查网络或视频访问权限后重试。组件下载可在模型页面切换下载源。', 'resume'),
        (('路径不存在', 'no such file', '找不到', 'not found', '权重', '组件', '模型路径'), '缺少文件或组件',
         '检查模型是否安装、输入文件是否被移动。修改配置后可用原输入重新处理。', 'workflow'),
        (('permission', '拒绝访问', '权限', 'winerror 5'), '无法读写文件',
         '关闭正在占用文件的程序，并检查保存目录的写入权限。', 'resume'),
        (('长度不一致', '音频格式', '解码', '音频验证'), '输入音频需要检查',
         '确认音频可播放。外部分轨应从同一起点导出完整长度，包含开头静音。', 'workflow'),
    ]
    for words, title, message, action in rules:
        if any(word in text for word in words):
            return {'title': title, 'message': message, 'action': action}
    return {'title': '处理未完成', 'message': '已完成的阶段已保留。可重试，或查看日志了解失败原因。', 'action': 'resume'}


def failure(job):
    job = Path(job)
    try:
        state = json.loads((job / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    raw = state.get('error', '处理进程提前结束，请查看 pipeline.log。')
    # Child-process failures use a short outer message; classify the actual cause.
    details = raw
    label = re.search(r'([a-zA-Z_][a-zA-Z_0-9]*) 失败（退出码', raw)
    if state.get('state') != 'cancelled' and label:
        for name in (label[1]+'.log',):
            path = job / name
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 16000))
                    details += '\n' + stream.read().decode('utf-8', errors='replace')
    return {**explain(details, state.get('current_step', ''), state.get('state') == 'cancelled'),
            'raw': raw, 'stage': state.get('current_step', '')}


def retry_with_cpu(job):
    path = Path(job) / 'request.json'
    request = json.loads(path.read_text(encoding='utf-8'))
    request['tools'].update(separation_device='cpu', midi_device='cpu')
    if request.get('voice_mode') == 'roformer':
        request['roformer_device'] = 'cpu'
    atomic_json(path, request)
