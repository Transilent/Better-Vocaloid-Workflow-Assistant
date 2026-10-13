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
        (('notesoverlappederror', 'notes overlapped near bar'), '工程含有重叠音符',
         'MIDI 和分轨音频已保留。请导入已生成的 MIDI，核对同一声部中相邻音符的起止位置。', 'workflow'),
        (('没有可用的 midi 歌词事件',), '字幕缺少歌词时间',
         'MIDI 音符已经保留。可以打开字幕工具，导入歌词后编辑时间再导出。', 'subtitles'),
        (('out of memory', '显存', '0x8007000e', 'bad allocation'), '内存或显存不足',
         '关闭占用显存的软件后重试，或改用 CPU。已完成的音频会保留。', 'cpu'),
        (('887a0005', '887a0006', '887a0007', '887a0020'), '显卡推理被中断',
         '已完成的阶段已保留。可以重试当前阶段；若仍失败，改用 CPU 继续，并查看日志中的设备错误。', 'cpu'),
        (('cuda error', 'cuda runtime error', 'cuda is not available', 'cuda unavailable',
          'no cuda gpus', 'not compiled with cuda', 'invalid device ordinal',
          'cuda initialization', 'cudaexecutionprovider failed', 'directml error',
          'dmlexecutionprovider failed', 'failed to create dml', 'device removed', 'd3d12 error'), '推理设备不可用',
         '可以切换为 CPU 后继续，也可以在工作流中检查设备设置。', 'cpu'),
        (('no space', 'disk full', '磁盘', 'winerror 112'), '磁盘空间不足',
         '先在“设置与更新”的缓存管理中检查占用；清理后可从失败阶段继续。', 'storage'),
        (('no module named', 'dll load failed', '运行库缺失', '模型缺失', '组件尚未安装', '模型路径不存在'), '所需组件未就绪',
         '在“模型与组件”检查安装状态，恢复模型后可继续当前任务。运行库文件可通过依赖检查工具核对。', 'components'),
        (('timed out', 'timeout', 'urlopen', 'connection', 'http error', '403', '412', '读取 b站', '下载'), '网络请求未完成',
         '检查网络或视频访问权限后重试。组件下载可在模型页面切换下载源。', 'resume'),
        (('路径不存在', 'no such file', '找不到', 'not found', '权重', '组件', '模型路径'), '缺少文件或组件',
         '检查模型是否安装、输入文件是否被移动。修改配置后可用原输入重新处理。', 'workflow'),
        (('permission', '拒绝访问', '权限', 'winerror 5'), '无法读写文件',
         '关闭正在占用文件的程序，并检查保存目录的写入权限。', 'resume'),
        (('长度不一致', '音频格式', '解码', '音频验证', '无效采样', '无效数值', '音频缓存'), '输入音频需要检查',
         '确认音频可播放。外部分轨应从同一起点导出完整长度，包含开头静音。', 'workflow'),
    ]
    for words, title, message, action in rules:
        if any(word in text for word in words):
            if title == '网络请求未完成' and stage in ('metadata', 'download', 'audio'):
                return {'title': 'B站连接未完成',
                        'message': 'B站资料和媒体使用直连。请检查网络是否能访问 B站，再继续／重试；视频访问限制可在日志中查看。',
                        'action': 'resume'}
            return {'title': title, 'message': message, 'action': action}
    return {'title': '处理未完成', 'message': '已完成的阶段已保留。可重试，或查看日志了解失败原因。', 'action': 'resume'}


def _log_cause(text):
    # Windows native libraries can write UTF-16 diagnostics into an otherwise
    # UTF-8 child stream. Null removal recovers their ASCII HRESULT without
    # interpreting normal provider-selection messages as failures.
    text = text.replace('\x00', '')
    exceptions = re.findall(
        r'(?m)^((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception):[^\r\n]*)', text,
    )
    if not exceptions:
        return ''
    cause = exceptions[-1].strip()
    device = re.search(r'(?i)\b(?:0x)?(887a000[567]|887a0020)\b', text)
    # ONNX Runtime sometimes masks the actual device error with a decoding
    # exception when the driver emits a localized message. Recover that code
    # only for the failing exception, not for unrelated earlier log messages.
    if device and ("'utf-8' codec can't decode byte" in cause or device[1].casefold() in cause.casefold()):
        code = '0x' + device[1].upper()
        reason = {'887a0005': '显卡设备连接中断', '887a0006': '显卡执行停止响应',
                  '887a0007': '显卡设备被重置', '887a0020': '显卡驱动内部错误'}[device[1].casefold()]
        return f'DirectML 推理中断：{reason}（{code}）。'
    return cause


def failure(job):
    job = Path(job)
    try:
        state = json.loads((job / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    raw = state.get('error', '处理进程提前结束，请查看 pipeline.log。')
    # Child-process failures use a short outer message; classify the actual cause.
    cause = ''
    label = re.search(r'([a-zA-Z_][a-zA-Z_0-9]*) 失败（退出码', raw)
    if state.get('state') != 'cancelled' and label:
        for name in (label[1]+'.log',):
            path = job / name
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 16000))
                    cause = _log_cause(stream.read().decode('utf-8', errors='replace'))
    return {**explain(cause or raw, state.get('current_step', ''), state.get('state') == 'cancelled'),
            'raw': cause + '\n' + raw if cause else raw, 'summary': raw,
            'stage': state.get('current_step', '')}


def retry_with_cpu(job):
    path = Path(job) / 'request.json'
    request = json.loads(path.read_text(encoding='utf-8'))
    request['tools'].update(separation_device='cpu', midi_device='cpu')
    if request.get('voice_mode') == 'roformer':
        request['roformer_device'] = 'cpu'
    atomic_json(path, request)
