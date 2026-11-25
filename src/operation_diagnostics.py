"""Small, local operation journal. Shared reports contain structured fields only."""
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
import errno
import json
import math
from pathlib import Path
from queue import Empty, Full, Queue
import re
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

from common import ROOT, direct_urlopen, exclusive_sidecar

LOG_DIR = Path('cache/diagnostics')
LOG_NAME = 'operations.jsonl'
SCHEMA = 1
LOG_LIMIT = 512 * 1024
RECENT_LIMIT = 120
_CONTEXT = ContextVar('vwa_operation', default=None)
_JOURNALS = {}
_JOURNALS_LOCK = Lock()

# Operation IDs are application commands, never paths, titles or user text.
_LABELS = {
    'preparation': ('准备工作流', 'processing'),
    'component': ('模型与组件', 'components'),
    'component-scan': ('检查组件状态', 'components'),
    'lyrics': ('查找歌词', 'processing'),
    'scan': ('扫描任务', 'results'),
    'detail': ('读取任务详情', 'results'),
    'artifact-check': ('核验任务文件', 'results'),
    'mixdown-handoff': ('核验混音结果', 'audio_mixdown'),
    'update-check': ('检查更新', 'updates'),
    'update-download': ('下载程序更新', 'updates'),
    'mixdown-preview': ('连续试听', 'audio_mixdown'),
    'mixdown-level-match': ('测量 A/B 响度', 'audio_mixdown'),
    'mixdown-waveform': ('读取对齐波形', 'audio_mixdown'),
    'startup': ('程序启动', 'updates'),
    'qt': ('界面运行', 'updates'),
}
_PREFIXES = {
    'preparation-': ('准备工作流', 'processing'),
    'tuning-': ('调音工作流', 'tuning'),
    'mixdown-': ('简易混音', 'audio_mixdown'),
    'chorus-': ('修音工作台', 'chorus'),
    'video-': ('合成视频', 'video_mixdown'),
    'publication-': ('发布准备', 'publishing'),
    'calibration-': ('声库拟合', 'tuning_workshop'),
    'workshop-': ('调音工坊', 'tuning_workshop'),
    'task-': ('任务与结果', 'results'),
    'storage-': ('缓存管理', 'updates'),
    'preset-': ('预设库', 'updates'),
}
_ROUTES = frozenset(route for _, route in (*_LABELS.values(), *_PREFIXES.values()))
_PHASES = frozenset(('metadata', 'download', 'audio', 'separation', 'midi', 'subtitles', 'exports',
                     'prepare', 'decode', 'mix', 'render', 'encode', 'verify', 'write', 'finalize',
                     'measuring', 'muxing', 'validation', 'hashing', 'waiting', 'fitting',
                     'install', 'probe', 'resume', 'load', 'check', 'analyze', 'export'))
_UNITS = frozenset(('bytes', 'items', 'media', 'fraction'))
_EVENTS = frozenset(('started', 'progress', 'cancel_requested', 'finished', 'download_attempt',
                     'download_fallback', 'failure', 'session_start', 'session_end', 'qt_warning',
                     'session_interrupted'))
_STATES = frozenset(('succeeded', 'partial', 'failed', 'cancelled', 'interrupted'))
_CODES = frozenset(('unexpected', 'invalid_input', 'cancelled', 'network_timeout', 'network',
                    'http', 'checksum', 'range', 'disk_full', 'file_busy', 'permission',
                    'file_missing', 'file_exists', 'dependency', 'device', 'memory', 'child_exit',
                    'io', 'runtime', 'callback', 'qt', 'log_unavailable'))
_COMPONENTS = ('roformer', 'chinese', 'expression', 'builtin', 'pedalboard')
_SOURCES = ('auto', 'direct', 'mirror', 'modelscope', 'github-release', 'hf-mirror',
            'pypi-tuna', 'pypi-aliyun', 'pypi-ustc', 'pytorch-sjtu', 'gitproxy', 'gh-proxy', 'ghfast')
_EVENT_TEXT = {
    'network_timeout': ('连接超时', '检查网络；下载组件时可切换到该组件的其他来源。'),
    'network': ('连接未完成', '检查网络后重试。'),
    'http': ('下载服务返回错误', '检查所选来源或稍后重试。'),
    'checksum': ('文件校验未通过', '重试或切换来源；有问题的下载不会启用。'),
    'range': ('服务器未能续传', '可以更换下载来源后继续。'),
    'disk_full': ('磁盘空间不足', '在设置与支持中检查磁盘占用，释放空间后重试。'),
    'file_busy': ('文件正在使用', '关闭占用该文件的程序后重试。'),
    'permission': ('无法读写文件', '检查所选目录的权限，或换到可写目录。'),
    'file_missing': ('找不到所需文件', '检查文件是否移动、模型是否已安装。'),
    'file_exists': ('目标文件已存在', '选用另一个文件名。'),
    'dependency': ('组件或运行库未就绪', '到“模型与组件”检查状态。'),
    'device': ('设备处理未完成', '检查设备状态；准备任务可在支持时尝试 CPU。'),
    'memory': ('可用内存不足', '关闭其他占用内存的程序后重试。'),
    'child_exit': ('处理进程提前结束', '查看任务日志或复制本机诊断。'),
    'invalid_input': ('输入或设置需要检查', '查看工作区中的错误详情并检查当前输入。'),
}
_HOSTS = ('huggingface.co', 'hf.co', 'hf-mirror.com', 'modelscope.cn',
          'github.com', 'githubusercontent.com', 'gitproxy.dev', 'gh-proxy.com', 'ghfast.top',
          'tsinghua.edu.cn', 'ustc.edu.cn', 'aliyun.com', 'sjtu.edu.cn', 'pytorch.org',
          'pythonhosted.org', 'bilibili.com', 'bilivideo.com')
_PUBLIC_FILE = re.compile(
    r'(?:bs_roformer_karaoke_frazer_becruily\.ckpt|config\.yaml|'
    r'qwen3_asr_(?:encoder_(?:back|front)end\.fp16\.onnx|llm\.f16\.gguf)|'
    r'VWA-(?:App-Update|Runtime-Upgrade)(?:-v[0-9.]+)?\.zip|'
    r'(?:pymss(?:_core)?|av|filelock|networkx|jinja2|markupsafe|fsspec|sympy|torch|pyworld|pedalboard)'
    r'-[0-9][a-zA-Z0-9_.+\-]{1,96}\.whl)\Z')
_TOKEN = re.compile(r'[a-z][a-z0-9-]{0,47}\Z')
_ID = re.compile(r'[a-f0-9]{12}\Z')
_STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z')
_EXCEPTION = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]{0,60}\Z')
_VERSION = re.compile(r'[0-9][0-9A-Za-z.+-]{0,31}\Z')


def describe(kind):
    if kind in _LABELS:
        return _LABELS[kind]
    return next((value for prefix, value in _PREFIXES.items() if kind.startswith(prefix)),
                ('后台操作', 'results'))


def phase(value):
    return value if isinstance(value, str) and value in _PHASES else ''


def public_host(url):
    try:
        host = (urlsplit(url).hostname or '').casefold().rstrip('.')
    except (TypeError, ValueError, AttributeError):
        return 'other'
    return host if _is_public_host(host) else 'other'


def _is_public_host(host):
    return (isinstance(host, str) and len(host) <= 100 and re.fullmatch('[a-z0-9.-]+', host)
            and any(host == domain or host.endswith('.' + domain) for domain in _HOSTS))


def number(value, *, maximum=10**15):
    if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
        return value
    return None


def meter(done, total, unit):
    done, total = number(done), number(total)
    if done is None or total is None or not total or done > total or not isinstance(unit, str) or unit not in _UNITS:
        return {}
    return {'done': done, 'total': total, 'unit': unit}


def failure_fields(error=None, *, code='', raw=''):
    # Raw text is used only to classify known failure patterns. It is never
    # written to the shareable journal or added to an exported report.
    reason = getattr(error, 'reason', error)
    cause = reason if isinstance(reason, BaseException) else error
    win = getattr(cause, 'winerror', None)
    os_error = getattr(cause, 'errno', None)
    http = getattr(error, 'code', None)
    message = (raw or str(error or ''))[:4096].casefold()
    if not win:
        found = re.search(r'\bwinerror\s+(\d{1,5})\b', message)
        win = int(found[1]) if found else None
    if not http:
        found = re.search(r'\bhttp(?: error)?\s+([45]\d\d)\b', message)
        http = int(found[1]) if found else None
    if code not in _CODES:
        if isinstance(error, InterruptedError):
            code = 'cancelled'
        elif (win in (10060, 12002)
                or (type(error).__name__ == 'URLError' and isinstance(cause, TimeoutError))):
            code = 'network_timeout'
        elif type(http) is int and 400 <= http <= 599:
            code = 'http'
        elif os_error == errno.ENOSPC or win == 112 or '空间不足' in message:
            code = 'disk_full'
        elif win in (32, 33):
            code = 'file_busy'
        elif isinstance(error, PermissionError) or win == 5:
            code = 'permission'
        elif isinstance(error, FileExistsError):
            code = 'file_exists'
        elif isinstance(error, FileNotFoundError):
            code = 'file_missing'
        elif isinstance(error, ImportError) or 'dll load failed' in message or '组件未就绪' in message:
            code = 'dependency'
        elif 'sha-256' in message or type(error).__name__ == 'ChecksumMismatch':
            code = 'checksum'
        elif type(error).__name__ == 'RangeUnsupported' or '续传范围' in message:
            code = 'range'
        elif isinstance(error, MemoryError) or 'out of memory' in message:
            code = 'memory'
        elif any(word in message for word in ('887a0005', '887a0006', '887a0007', '887a0020', 'cuda error', 'directml 推理中断')):
            code = 'device'
        elif '退出码' in message or '进程提前结束' in message:
            code = 'child_exit'
        elif isinstance(cause, ConnectionError) or type(error).__name__ == 'URLError':
            code = 'network'
        elif isinstance(cause, TimeoutError):
            code = 'runtime'
        elif isinstance(error, ValueError):
            code = 'invalid_input'
        elif isinstance(error, OSError):
            code = 'io'
        else:
            code = 'unexpected'
    fields = {'code': code}
    if error is not None:
        fields['exception'] = type(error).__name__
        tb = error.__traceback__
        frames = []
        while tb is not None:
            name = Path(tb.tb_frame.f_code.co_filename)
            if name.is_absolute() and name.parent in (ROOT, ROOT / 'src'):
                frames.append([name.name, tb.tb_lineno])
            tb = tb.tb_next
        if frames:
            fields['frames'] = frames[-5:]
    for key, value, low, high in (('http', http, 400, 599), ('winerror', win, 1, 65535),
                                   ('errno', os_error, 1, 65535)):
        if type(value) is int and low <= value <= high:
            fields[key] = value
    return fields


def error_text(code):
    return _EVENT_TEXT.get(code, ('操作未完成', '查看工作区中的错误详情，或复制本机诊断。'))


def _safe_row(row):
    if (not isinstance(row, dict) or row.get('schema') != SCHEMA or not isinstance(row.get('event'), str)
            or row.get('event') not in _EVENTS):
        return {}
    clean = {'schema': SCHEMA, 'event': row['event']}
    for key, pattern in (('at', _STAMP), ('session', _ID), ('id', _ID), ('kind', _TOKEN),
                         ('version', _VERSION), ('exception', _EXCEPTION)):
        value = row.get(key)
        if isinstance(value, str) and pattern.fullmatch(value):
            clean[key] = value
    for key, allowed in (('route', _ROUTES), ('phase', _PHASES), ('state', _STATES), ('code', _CODES),
                         ('component', _COMPONENTS), ('runtime', ('cpu', 'cuda')), ('source', _SOURCES),
                         ('action', ('install', 'verify', 'check', 'download')),
                         ('mode', ('parallel', 'single')), ('level', ('warning', 'critical', 'fatal'))):
        value = row.get(key)
        if isinstance(value, str) and value in allowed:
            clean[key] = value
    for key, limit in (('elapsed', 10**8), ('since_progress', 10**8), ('cached_bytes', 10**15),
                       ('media_seconds', 10**7),
                       ('http', 599), ('winerror', 65535), ('errno', 65535), ('exit_code', 2**32)):
        value = number(row.get(key), maximum=limit)
        if value is not None:
            clean[key] = value
    unit = row.get('unit')
    if isinstance(unit, str):
        clean.update(meter(row.get('done'), row.get('total'), unit))
    file = row.get('file')
    if isinstance(file, str) and _PUBLIC_FILE.fullmatch(file):
        clean['file'] = file
    for key in ('origin', 'target'):
        host = row.get(key)
        if host == 'other' or _is_public_host(host):
            clean[key] = host
    hosts = row.get('redirects')
    if isinstance(hosts, list):
        clean['redirects'] = [h for h in hosts[:6] if h == 'other' or _is_public_host(h)]
    frames = row.get('frames')
    if isinstance(frames, list):
        kept = [f for f in frames[-5:] if isinstance(f, list) and len(f) == 2
                and isinstance(f[0], str) and re.fullmatch(r'[a-z_][a-z0-9_]{0,55}\.py', f[0])
                and type(f[1]) is int and 0 < f[1] < 10**6]
        if kept:
            clean['frames'] = kept
    return clean


def _reparse(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 1024)


class Journal:
    def __init__(self, root, *, max_bytes=LOG_LIMIT, version=''):
        self.root = Path(root).resolve()
        self.folder = self.root / LOG_DIR
        self.session = uuid4().hex[:12]
        self.max_bytes = max(4096, max_bytes)
        self.version = version
        self.recent = deque(maxlen=RECENT_LIMIT)
        self.lock = Lock()
        self.queue = Queue(maxsize=256)
        self.writer = None
        self._closed = False
        self.available = None
        self._warnings = 0

    def event(self, event, *, operation=None, **fields):
        if event == 'qt_warning':
            with self.lock:
                if self._warnings >= 12:
                    return
                self._warnings += 1
        row = _safe_row({
            **(operation or {}), **fields, 'event': event, 'schema': SCHEMA, 'version': self.version,
            'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'session': self.session,
        })
        if not row:
            return
        with self.lock:
            if self._closed:
                return
            self.recent.append(row)
            if self.writer is None:
                self.writer = Thread(target=self._write, name='vwa-local-journal', daemon=True)
                self.writer.start()
            try:
                self.queue.put_nowait(row)
            except Full:
                # Slow or unavailable storage must not block the GUI or DSP.
                pass

    def problem(self, kind, error, *, route='', code='', raw=''):
        op = {'id': uuid4().hex[:12], 'kind': kind, 'route': route or describe(kind)[1]}
        self.event('failure', operation=op, state='failed', **failure_fields(error, code=code, raw=raw))

    def current(self):
        with self.lock:
            return list(self.recent)

    def latest_failure(self, dismissed=''):
        with self.lock:
            resolved = set()
            for entry in reversed(self.recent):
                if entry['event'] not in ('finished', 'failure'):
                    continue
                kind = (entry.get('kind'), entry.get('component'), entry.get('action'))
                if entry.get('state') in ('succeeded', 'partial'):
                    resolved.add(kind)
                elif entry.get('state') == 'failed' and entry.get('id') and kind not in resolved:
                    return {} if entry['id'] == dismissed else dict(entry)
            return {}

    def _folder(self, *, create=False):
        for path in (self.root / 'cache', self.folder):
            if not path.exists() and create:
                path.mkdir(exist_ok=True)
            if not path.is_dir() or _reparse(path):
                raise OSError('Unsafe diagnostic folder')
        return self.folder

    def _write(self):
        while True:
            first = self.queue.get()
            if first is None:
                break
            batch = [first]
            closing = False
            while len(batch) < 32:
                try:
                    next_row = self.queue.get_nowait()
                except Empty:
                    break
                if next_row is None:
                    closing = True
                    break
                batch.append(next_row)
            try:
                folder = self._folder(create=True)
                path, previous, lock = (folder / name for name in (LOG_NAME, LOG_NAME+'.1', 'write.lock'))
                for file in (path, previous, lock):
                    if (file.exists() or file.is_symlink()) and (_reparse(file) or not file.is_file() or file.stat().st_nlink > 1):
                        raise OSError('Unsafe diagnostic file')
                encoded = [(json.dumps(row, ensure_ascii=True, separators=(',', ':'))+'\n').encode('ascii')
                           for row in batch]
                with exclusive_sidecar(lock):
                    size = path.stat().st_size if path.is_file() else 0
                    chunk = bytearray()
                    for line in encoded:
                        if size + len(chunk) + len(line) > self.max_bytes:
                            if chunk:
                                with path.open('ab') as stream:
                                    stream.write(chunk)
                                chunk.clear()
                            if path.is_file():
                                path.replace(previous)
                            size = 0
                        chunk.extend(line)
                    if chunk:
                        with path.open('ab') as stream:
                            stream.write(chunk)
                self.available = True
            except (OSError, RuntimeError):
                self.available = False
            if closing:
                break

    def history(self, limit=70):
        """Read bounded journal data only; reject malformed rows and stray fields."""
        saved = []
        try:
            folder = self._folder()
            for name in (LOG_NAME+'.1', LOG_NAME):
                file = folder / name
                try:
                    if not file.is_file() or _reparse(file) or file.stat().st_nlink > 1:
                        continue
                    with file.open('rb') as stream:
                        size = stream.seek(0, 2)
                        stream.seek(max(0, size - self.max_bytes))
                        raw = stream.read(self.max_bytes)
                    for line in raw.splitlines():
                        try:
                            if len(line) <= 8192:
                                row = _safe_row(json.loads(line))
                                if row:
                                    saved.append(row)
                        except (ValueError, TypeError, RecursionError):
                            continue
                except OSError:
                    self.available = False
                    continue
        except OSError:
            if self.folder.exists() or self.folder.is_symlink():
                self.available = False
        seen = {}
        for row in (*saved, *self.current()):
            key = json.dumps(row, sort_keys=True)
            seen[key] = row
        return list(seen.values())[-limit:]

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            worker = self.writer
        if worker is not None:
            try:
                self.queue.put(None, timeout=.25)
            except Full:
                return
            worker.join(timeout=.8)


def journal_for(root=ROOT):
    root = Path(root).resolve()
    with _JOURNALS_LOCK:
        if root not in _JOURNALS:
            try:
                version = json.loads((root/'version.json').read_text(encoding='utf-8-sig'))['version']
            except (OSError, ValueError, TypeError, KeyError):
                version = ''
            _JOURNALS[root] = Journal(root, version=version)
        return _JOURNALS[root]


def close_journal(root=ROOT):
    with _JOURNALS_LOCK:
        journal = _JOURNALS.pop(Path(root).resolve(), None)
    if journal is not None:
        journal.close()


def bind(journal, operation):
    return _CONTEXT.set((journal, operation) if journal is not None else None)


def unbind(token):
    _CONTEXT.reset(token)


def operation_event(event, **fields):
    current = _CONTEXT.get()
    if current is not None:
        current[0].event(event, operation=current[1], **fields)


class DownloadTrace:
    """One candidate transfer, including failed redirects and reusable bytes."""
    def __init__(self, opener, route, item, mode):
        self.opener = opener
        self.context = _CONTEXT.get()
        self.file = download_file(item)
        self.mode = mode
        self.origin = self.target = public_host(route)
        self.redirects = []
        self.lock = Lock()
        self.started = monotonic()

    def open(self, request, timeout):
        if self.context is None or not self.file:
            return self.opener(request, timeout=timeout)
        trace = self

        class Redirects(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                target = public_host(newurl)
                with trace.lock:
                    trace.target = target
                    if target not in trace.redirects and len(trace.redirects) < 6:
                        trace.redirects.append(target)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = (build_opener(ProxyHandler({}), Redirects()).open
                  if self.opener is direct_urlopen else self.opener)
        result = opener(request, timeout=timeout)
        with self.lock:
            self.target = public_host(result.geturl())
        return result

    def record(self, state, cached_bytes, error=None):
        if self.context is None or not self.file:
            return
        if error is not None and getattr(error, 'geturl', None):
            self.target = public_host(error.geturl())
        evidence = failure_fields(error) if state == 'failed' else {}
        if isinstance(error, TimeoutError) and evidence.get('code') == 'runtime':
            evidence['code'] = 'network_timeout'
        journal, operation = self.context
        journal.event('download_attempt', operation=operation, phase='download', file=self.file,
                      state=state, mode=self.mode, origin=self.origin, target=self.target,
                      redirects=list(self.redirects), cached_bytes=cached_bytes,
                      elapsed=int(monotonic() - self.started), **evidence)


def download_file(item):
    # Callers pass the public, pinned manifest entry rather than an input path.
    name = item.get('name')
    return name if isinstance(name, str) and _PUBLIC_FILE.fullmatch(name) else ''


def summary_rows(history, session=''):
    """Prefer actionable endpoints and results over repeated measurements."""
    history = list(history)
    # A start with no closing event means the session ended without its final
    # record. This can be a force close or missing log data, not a crash diagnosis.
    starts, ends = {}, set()
    for row in history:
        if row['event'] == 'session_start':
            starts[row.get('session')] = row
        elif row['event'] == 'session_end':
            ends.add(row.get('session'))
    missing = [row for key, row in starts.items() if key and key != session and key not in ends]
    if missing:
        history.append({**missing[-1], 'event': 'session_interrupted', 'state': 'interrupted'})
    important = {'finished', 'failure', 'download_attempt', 'download_fallback', 'session_start',
                 'session_end', 'session_interrupted', 'cancel_requested', 'qt_warning'}
    rows = [r for r in history if r['event'] in important or 'exit_code' in r]
    # Reserve room for both failure outcomes and route evidence when a large
    # download has many per-file success records.
    selected = set(range(max(0, len(rows) - 16), len(rows)))
    failed = [i for i, r in enumerate(rows) if r.get('state') == 'failed']
    selected.update(failed[-8:])
    return [rows[i] for i in sorted(selected)]


def support_lines(history, session=''):
    """Render the portable text summary without echoing free-form data."""
    for event in summary_rows(history, session):
        parts = [event.get('at', ''), f'[{event.get("id", event.get("session", ""))}]',
                 event.get('kind', ''), event['event'], event.get('state', '')]
        for key in ('version', 'component', 'runtime', 'source', 'action', 'phase', 'mode', 'code',
                    'file', 'origin', 'target', 'http', 'winerror', 'errno', 'exit_code',
                    'cached_bytes', 'elapsed', 'since_progress', 'media_seconds', 'exception'):
            if key in event:
                parts.append(f'{key}={event[key]}')
        if event.get('total'):
            parts.append(f'{event["done"]:g}/{event["total"]:g} {event["unit"]}')
        if event.get('redirects'):
            parts.append('redirects=' + '→'.join(event['redirects']))
        if event.get('frames'):
            parts.append('at=' + ','.join(f'{f}:{line}' for f, line in event['frames']))
        yield event['event'], ' '.join(filter(None, parts))


def clock_text(seconds):
    seconds = int(max(0, seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
