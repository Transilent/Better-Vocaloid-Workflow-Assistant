"""Cross-process shared style drafts with strict compare-and-swap writes.

The on-disk draft remains the flat schema-version-1 object used by the tuning
desktop UI.  This module owns only the storage and normalization boundary; it
does not import Qt, MCP, or any inference/runtime component.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from contextlib import contextmanager

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

from common import native_path
from style_presets import detail_settings, pitch_default, region_settings, technique_settings


MAX_DRAFT_BYTES = 1024 * 1024
MAX_RECOVERY_BYTES = 3 * MAX_DRAFT_BYTES
MAX_DRAFT_REGIONS = 128
MAX_SYNC_SEQ = (1 << 63) - 1
DEFAULT_LOCK_TIMEOUT = 5.0
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x0400)
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_ALLOWED_FIELDS = frozenset({
    'schema_version', 'input_sha256', 'style_regions', 'mode', 'selection',
    'selected_index', 'track_indices', 'tool', 'editor_intent', 'next_tool',
    '_sync',
})
_TOOL_FIELDS = frozenset({
    'performance_style', 'style_strength', 'style_detail', 'style_pitch',
    'style_technique', 'technique_strength', 'technique_pitch',
})
_VISIBLE_STYLES = frozenset({'none', 'gentle', 'sad', 'bold'})
_SYNC_FIELDS = frozenset({'seq', 'writer'})
_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()

if os.name == "nt":
    class _WinFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_WinFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _WIN_INVALID_HANDLE = wintypes.HANDLE(-1).value
    _WIN_FILE_READ_ATTRIBUTES = 0x00000080
    _WIN_GENERIC_READ = 0x80000000
    _WIN_GENERIC_WRITE = 0x40000000
    _WIN_SHARE_READ = 0x00000001
    _WIN_SHARE_WRITE = 0x00000002
    _WIN_SHARE_DELETE = 0x00000004
    _WIN_OPEN_EXISTING = 3
    _WIN_OPEN_ALWAYS = 4
    _WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _WIN_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WIN_FLAG_OPEN_REPARSE_POINT = 0x00200000


def _win_open_handle(path, desired, share, disposition, *, directory=False):
    flags = _WIN_FLAG_OPEN_REPARSE_POINT | _WIN_FLAG_BACKUP_SEMANTICS
    ctypes.set_last_error(0)
    handle = _kernel32.CreateFileW(native_path(path), desired, share, None, disposition,
                                  flags, None)
    if handle == _WIN_INVALID_HANDLE:
        code = ctypes.get_last_error()
        error = OSError(code, ctypes.FormatError(code), str(path))
        error.winerror = code
        raise error
    info = _WinFileInformation()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        code = ctypes.get_last_error()
        _kernel32.CloseHandle(handle)
        error = OSError(code, ctypes.FormatError(code), str(path))
        error.winerror = code
        raise error
    attributes = info.dwFileAttributes
    is_directory = bool(attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT or is_directory != directory:
        _kernel32.CloseHandle(handle)
        raise DraftError("unsafe_path", "路径必须是普通目录或普通文件，不能经过重解析点。",
                         {"path": str(path)})
    return handle


def _close_pin(token):
    kind, handle = token
    if kind == "win":
        _kernel32.CloseHandle(handle)
    else:
        os.close(handle)


def _pin_directory(path):
    if os.name == "nt":
        handle = _win_open_handle(
            path, _WIN_FILE_READ_ATTRIBUTES,
            _WIN_SHARE_READ | _WIN_SHARE_WRITE | _WIN_SHARE_DELETE,
            _WIN_OPEN_EXISTING, directory=True)
        return "win", handle
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(native_path(path), flags)
    try:
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DraftError("unsafe_path", "路径必须是普通目录。", {"path": str(path)})
    except Exception:
        os.close(fd)
        raise
    return "fd", fd

@contextmanager
def _pinned_path(root, *, create=False):
    """Validate and hold every existing directory with no-follow opens."""
    root = Path(root)
    _safe_root(root)
    pins = []
    try:
        for current in reversed(_ancestors(root)):
            if _lstat(current) is None:
                if not create:
                    break
                try:
                    os.mkdir(native_path(current))
                except FileExistsError:
                    pass
            pins.append(_pin_directory(current))
        _safe_root(root)
        yield
    finally:
        for token in reversed(pins):
            _close_pin(token)



def _open_read_stream(path):
    if os.name == "nt":
        handle = _win_open_handle(path, _WIN_GENERIC_READ,
                                  _WIN_SHARE_READ | _WIN_SHARE_WRITE | _WIN_SHARE_DELETE,
                                  _WIN_OPEN_EXISTING)
        try:
            import msvcrt
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except Exception:
            _kernel32.CloseHandle(handle)
            raise
        return os.fdopen(fd, "rb")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(native_path(path), flags)
    try:
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DraftError("unsafe_path", "草稿文件必须是普通文件。", {"path": str(path)})
    except Exception:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def _open_lock_stream(path):
    if os.name == "nt":
        handle = _win_open_handle(path, _WIN_GENERIC_READ | _WIN_GENERIC_WRITE,
                                  _WIN_SHARE_READ | _WIN_SHARE_WRITE,
                                  _WIN_OPEN_ALWAYS)
        try:
            import msvcrt
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except Exception:
            _kernel32.CloseHandle(handle)
            raise
        return os.fdopen(fd, "r+b", buffering=0)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(native_path(path), flags, 0o600)
    try:
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DraftError("unsafe_path", "草稿锁必须是普通文件。", {"path": str(path)})
    except Exception:
        os.close(fd)
        raise
    return os.fdopen(fd, "r+b", buffering=0)


class DraftError(ValueError):
    """A stable, machine-readable draft storage error."""

    def __init__(self, code, message, details=None):
        if details is not None and not isinstance(details, dict):
            raise TypeError('details must be a dict or None')
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(self.message)


class DraftConflict(DraftError):
    """The caller's expected revision no longer matches the stored draft."""

    def __init__(self, message, details=None):
        super().__init__('draft_conflict', message, details)


def _invalid(message, **details):
    raise DraftError('invalid_draft', message, details)


def _require_sha256(value, *, field='input_sha256'):
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise DraftError(
            'invalid_' + field,
            '草稿的 ' + field + ' 必须是 64 位小写十六进制 SHA-256。',
            {'field': field},
        )
    return value


def _normalise_expected_revision(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise DraftError(
            'invalid_revision',
            '期望修订必须是 64 位小写十六进制 SHA-256 或 null。',
        )
    return value


def _normalise_writer(value):
    if not isinstance(value, str):
        raise DraftError('invalid_writer', 'writer 必须是非空字符串。')
    writer = value.strip()
    if not writer or len(writer) > 128 or any(ord(char) < 32 for char in writer):
        raise DraftError('invalid_writer', 'writer 必须是 1–128 个可打印字符。')
    return writer


def _finite_number(value):
    return (type(value) in (int, float) and math.isfinite(value))


def _normalise_selection(value):
    # A cleared or not-yet-completed timeline selection is represented by null.
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        _invalid('草稿选区必须为 null 或两个有限数值。', field='selection')
    if not all(_finite_number(part) for part in value):
        _invalid('草稿选区端点必须是有限数值。', field='selection')
    first, second = sorted((float(value[0]), float(value[1])))
    return [first, second]


def _normalise_tracks(value):
    if value is None:
        return []
    if not isinstance(value, list):
        _invalid('草稿轨道列表必须为数组。', field='track_indices')
    tracks = []
    seen = set()
    for item in value:
        if type(item) is not int or item < 0:
            _invalid('草稿轨道索引必须为非负整数。', field='track_indices')
        if item in seen:
            _invalid('草稿轨道索引不能重复。', field='track_indices')
        seen.add(item)
        tracks.append(item)
    return tracks


def _normalise_tool(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        _invalid('草稿工具设置必须为对象。', field='tool')
    unknown = set(value) - _TOOL_FIELDS
    if unknown:
        _invalid('草稿工具设置包含未知字段。', field='tool', unknown=sorted(unknown))

    preset = value.get('performance_style', 'none')
    if not isinstance(preset, str) or preset not in _VISIBLE_STYLES:
        _invalid('草稿工具的风格预设无效。', field='tool.performance_style')
    strength = value.get('style_strength', .6)
    if not _finite_number(strength) or not 0 <= strength <= 1:
        _invalid('草稿工具的风格强度应为 0–100%。', field='tool.style_strength')
    pitch = value.get('style_pitch', pitch_default(preset))
    if type(pitch) is not bool:
        _invalid('草稿工具的音高开关必须为布尔值。', field='tool.style_pitch')
    try:
        detail = detail_settings(value.get('style_detail'))
        technique = technique_settings(value)
    except (TypeError, ValueError) as error:
        _invalid(str(error), field='tool')

    return {
        'performance_style': preset,
        'style_strength': float(strength),
        'style_pitch': pitch,
        'style_detail': detail,
        'style_technique': technique['style_technique'],
        'technique_strength': technique['technique_strength'],
        'technique_pitch': technique['technique_pitch'],
    }


def normalize_draft(input_sha256, data):
    """Return the canonical flat schema-1 draft payload.

    Any supplied ``_sync`` object is deliberately ignored.  Sync sequence and
    writer fields are storage metadata and are assigned only by ``write``.
    """
    input_sha256 = _require_sha256(input_sha256)
    if not isinstance(data, dict):
        _invalid('草稿必须为 JSON 对象。')
    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        _invalid('草稿包含未知字段。', unknown=sorted(unknown))

    schema_version = data.get('schema_version', 1)
    if type(schema_version) is not int or schema_version != 1:
        _invalid('草稿 schema_version 必须为 1。', field='schema_version')
    stored_input = data.get('input_sha256')
    if stored_input != input_sha256:
        _invalid('草稿 input_sha256 与目标文件不一致。', field='input_sha256')

    raw_regions = data.get('style_regions', [])
    if not isinstance(raw_regions, list) or len(raw_regions) > MAX_DRAFT_REGIONS:
        _invalid('草稿风格片段数量必须在 0–128 之间。', field='style_regions')
    selected_index = data.get('selected_index', -1)
    if type(selected_index) is not int or selected_index < -1 or selected_index >= len(raw_regions):
        _invalid('草稿所选片段索引越界。', field='selected_index')
    try:
        regions = region_settings(copy.deepcopy(raw_regions))
    except (TypeError, ValueError) as error:
        _invalid(str(error), field='style_regions')

    # region_settings sorts by time.  Preserve the semantic selected region
    # instead of accidentally selecting whichever row moved into that position.
    if selected_index >= 0:
        chosen = raw_regions[selected_index]
        try:
            chosen_start = float(chosen['start'])
            chosen_end = float(chosen['end'])
        except (KeyError, TypeError, ValueError):
            _invalid('草稿所选片段没有有效时间范围。', field='selected_index')
        selected_index = next((index for index, row in enumerate(regions)
                               if row['start'] == chosen_start and row['end'] == chosen_end), -1)
        if selected_index < 0:
            _invalid('草稿所选片段无法在规范化后定位。', field='selected_index')

    mode = data.get('mode', 'whole')
    if mode not in ('whole', 'regions'):
        _invalid('草稿模式必须为 whole 或 regions。', field='mode')
    selection = _normalise_selection(data.get('selection'))
    tracks = _normalise_tracks(data.get('track_indices', []))
    tool = _normalise_tool(data.get('tool'))
    editor_intent = data.get('editor_intent', 'new')
    if editor_intent not in ('new', 'edit'):
        _invalid('草稿编辑意图必须为 new 或 edit。', field='editor_intent')
    if selected_index < 0:
        editor_intent = 'new'
    next_tool = _normalise_tool(data['next_tool']) if 'next_tool' in data else copy.deepcopy(tool)

    return {
        'schema_version': 1,
        'input_sha256': input_sha256,
        'style_regions': regions,
        'mode': mode,
        'selection': selection,
        'selected_index': selected_index,
        'track_indices': tracks,
        'tool': tool,
        'editor_intent': editor_intent,
        'next_tool': next_tool,
    }


def _reject_json_constant(value):
    raise ValueError('non-finite JSON number: ' + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _check_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('non-finite JSON number')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('JSON object key is not a string')
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def _sync_metadata(data):
    if '_sync' not in data:
        return None
    sync = data['_sync']
    if not isinstance(sync, dict) or set(sync) - _SYNC_FIELDS:
        raise DraftError('draft_corrupt', '草稿同步 metadata 格式无效。')
    seq = sync.get('seq')
    writer = sync.get('writer')
    if type(seq) is not int or not 1 <= seq <= MAX_SYNC_SEQ:
        raise DraftError('draft_corrupt', '草稿同步序号无效。')
    if not isinstance(writer, str) or not writer or len(writer) > 128:
        raise DraftError('draft_corrupt', '草稿写入者标记无效。')
    return {'seq': seq, 'writer': writer}


def _lstat(path):
    try:
        return os.lstat(native_path(path))
    except (FileNotFoundError, NotADirectoryError):
        return None


def _ancestors(path):
    path = Path(path)
    values = []
    while True:
        values.append(path)
        if path.parent == path:
            return values
        path = path.parent


def _safe_root(root):
    root = Path(root)
    unsafe = None
    for current in reversed(_ancestors(root)):
        info = _lstat(current)
        if info is None:
            continue
        if (getattr(info, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT
                or stat.S_ISLNK(info.st_mode)):
            unsafe = current
            break
        if not stat.S_ISDIR(info.st_mode):
            unsafe = current
            break
    if unsafe is not None:
        raise DraftError('unsafe_path', '草稿目录或祖先目录不能是文件、符号链接或重解析点。',
                         {'path': str(unsafe), 'root': str(root)})
    return root


def _thread_lock(path):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
    return lock


@contextmanager
def _draft_lock(path, timeout=DEFAULT_LOCK_TIMEOUT):
    if not _finite_number(timeout) or timeout <= 0:
        raise DraftError('invalid_lock_timeout', '草稿锁等待时间必须为正数。')
    lock = _thread_lock(path)
    if not lock.acquire(timeout=timeout):
        raise DraftError('draft_busy', '共享草稿正在被其他任务写入，请稍后重试。')
    handle = None
    locked = False
    try:
        try:
            handle = _open_lock_stream(path)
        except DraftError:
            raise
        except OSError as error:
            raise DraftError('draft_io', '无法打开共享草稿锁。',
                             {'path': str(path), 'reason': str(error)}) from error
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as error:
                retry = getattr(error, 'errno', None) in (11, 13, 35, 36)
                retry = retry or getattr(error, 'winerror', None) in (33, 36)
                if not retry or time.monotonic() >= deadline:
                    if retry:
                        raise DraftError('draft_busy', '共享草稿正在被其他任务写入，请稍后重试。')
                    raise
                time.sleep(.02)
        yield
    finally:
        if handle is not None and locked:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        elif handle is not None:
            handle.close()
        lock.release()


class DraftStore:
    """Read and write flat style draft files under one explicit safe root."""

    def __init__(self, root, *, lock_timeout=DEFAULT_LOCK_TIMEOUT):
        if not isinstance(root, (str, os.PathLike)):
            raise DraftError('invalid_root', '草稿根目录必须是显式绝对路径。')
        text = os.fspath(root)
        if not isinstance(text, str) or not text or not Path(text).is_absolute():
            raise DraftError('invalid_root', '草稿根目录必须是显式绝对路径。')
        self.root = Path(os.path.abspath(text))
        if not _finite_number(lock_timeout) or lock_timeout <= 0:
            raise DraftError('invalid_lock_timeout', '草稿锁等待时间必须为正数。')
        self.lock_timeout = float(lock_timeout)
        _safe_root(self.root)

    def path_for(self, input_sha256):
        input_sha256 = _require_sha256(input_sha256)
        return str(self.root / (input_sha256 + '.json'))

    def _path(self, input_sha256):
        return Path(self.path_for(input_sha256))

    def _lock_path(self, input_sha256):
        return self.root / ('.' + input_sha256 + '.lock')

    def _read_raw(self, path):
        info = _lstat(path)
        if info is None:
            return None
        if (not stat.S_ISREG(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT):
            raise DraftError('unsafe_path', '草稿文件必须是普通文件。', {'path': str(path)})
        if info.st_size > MAX_DRAFT_BYTES:
            raise DraftError('draft_too_large', '草稿文件超过 1 MiB 限额。',
                             {'path': str(path), 'max_bytes': MAX_DRAFT_BYTES})
        try:
            with _open_read_stream(path) as stream:
                opened = os.fstat(stream.fileno())
                if opened.st_size > MAX_DRAFT_BYTES:
                    raise DraftError('draft_too_large', '草稿文件超过 1 MiB 限额。',
                                     {'path': str(path), 'max_bytes': MAX_DRAFT_BYTES})
                raw = stream.read(MAX_DRAFT_BYTES + 1)
        except FileNotFoundError:
            return None
        except DraftError:
            raise
        except OSError as error:
            raise DraftError('draft_io', '无法读取共享草稿。',
                             {'path': str(path), 'reason': str(error)}) from error
        return raw

    def _decode(self, input_sha256, raw, path):
        if raw is None or raw == b'':
            return None
        try:
            text = raw.decode('utf-8-sig')
            data = json.loads(text, object_pairs_hook=_unique_object,
                              parse_constant=_reject_json_constant)
            _check_finite(data)
            if not isinstance(data, dict):
                raise ValueError('draft root must be a JSON object')
            normalize_draft(input_sha256, data)
            _sync_metadata(data)
        except DraftError as error:
            raise DraftError('draft_corrupt', '共享草稿内容无效。', {
                'path': str(path), 'reason_code': error.code, 'reason': error.message,
            }) from error
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as error:
            raise DraftError('draft_corrupt', '共享草稿不是有效的有限 JSON 对象。',
                             {'path': str(path), 'reason': str(error)}) from error
        return data

    def _snapshot(self, input_sha256, raw, data):
        path = self._path(input_sha256)
        exists = raw is not None and raw != b''
        return {
            'exists': exists,
            'input_sha256': input_sha256,
            'revision': hashlib.sha256(raw).hexdigest() if exists else None,
            'data': data if exists else None,
            'path': str(path),
            'legacy': bool(exists and '_sync' not in data),
        }

    def read(self, input_sha256):
        """Read without creating or modifying the draft root or file."""
        input_sha256 = _require_sha256(input_sha256)
        path = self._path(input_sha256)
        with _pinned_path(self.root, create=False):
            raw = self._read_raw(path)
            data = self._decode(input_sha256, raw, path)
        return self._snapshot(input_sha256, raw, data)

    @staticmethod
    def _encoded(data, *, max_bytes=MAX_DRAFT_BYTES):
        try:
            payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False).encode('utf-8') + b'\n'
        except (TypeError, ValueError, OverflowError) as error:
            raise DraftError('invalid_draft', '草稿无法编码为有限 JSON。',
                             {'reason': str(error)}) from error
        if len(payload) > max_bytes:
            raise DraftError('draft_too_large', '编码后的草稿超过允许的字节限额。',
                             {'bytes': len(payload), 'max_bytes': max_bytes})
        return payload

    def _atomic_write(self, path, payload):
        temporary = None
        _safe_root(self.root)
        try:
            with tempfile.NamedTemporaryFile(mode='w+b', dir=native_path(path.parent),
                                             prefix='.' + path.name + '.', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _safe_root(self.root)
            _replace_file(temporary, path)
            _safe_root(self.root)
            temporary = None
        except DraftError:
            raise
        except OSError as error:
            raise DraftError('draft_io', '无法原子保存共享草稿。',
                             {'path': str(path), 'reason': str(error)}) from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(native_path(temporary))
                except FileNotFoundError:
                    pass

    def _write_recovery(self, input_sha256, expected_revision, current_revision,
                        current_data, incoming_data):
        _safe_root(self.root)
        recovery = {
            'schema_version': 1,
            'kind': 'style_draft_conflict',
            'input_sha256': input_sha256,
            'expected_revision': expected_revision,
            'current_revision': current_revision,
            'current_data': copy.deepcopy(current_data),
            'incoming_data': copy.deepcopy(incoming_data),
        }
        try:
            payload = self._encoded(recovery, max_bytes=MAX_RECOVERY_BYTES)
        except DraftError as error:
            raise DraftError('recovery_failed', '草稿冲突恢复包无法编码；原草稿未改变。',
                             {'reason_code': error.code, 'reason': error.message}) from error
        stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
        _safe_root(self.root)
        for _ in range(8):
            target = self.root / (input_sha256 + '.conflict-' + stamp + '-'
                                  + secrets.token_hex(8) + '.json')
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
            fd = None
            try:
                fd = os.open(native_path(target), flags, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    fd = None
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                _safe_root(self.root)
                return target
            except FileExistsError:
                continue
            except OSError as error:
                if fd is not None:
                    os.close(fd)
                try:
                    os.unlink(native_path(target))
                except FileNotFoundError:
                    pass
                raise DraftError('recovery_failed', '草稿冲突恢复包无法保存；原草稿未改变。',
                                 {'path': str(target), 'reason': str(error)}) from error
        raise DraftError('recovery_failed', '草稿冲突恢复包名称重复；原草稿未改变。')

    def _conflict(self, input_sha256, expected_revision, current_revision,
                  current_data, incoming_data):
        recovery = self._write_recovery(input_sha256, expected_revision, current_revision,
                                        current_data, incoming_data)
        details = {
            'input_sha256': input_sha256,
            'expected_revision': expected_revision,
            'current_revision': current_revision,
            'recovery_path': str(recovery),
        }
        raise DraftConflict('共享草稿已在其他任务中更新；双方内容均已保留。', details)

    def write(self, input_sha256, expected_revision, data, *, writer='unknown'):
        """CAS-write a normalized draft and return the new snapshot."""
        input_sha256 = _require_sha256(input_sha256)
        expected_revision = _normalise_expected_revision(expected_revision)
        writer = _normalise_writer(writer)
        incoming = normalize_draft(input_sha256, data)
        path = self._path(input_sha256)
        with _pinned_path(self.root, create=True):
            return self._write_locked(input_sha256, expected_revision, incoming, path, writer)

    def _write_locked(self, input_sha256, expected_revision, incoming, path, writer):
        with _draft_lock(self._lock_path(input_sha256), self.lock_timeout):
            return self._write_guarded(input_sha256, expected_revision,
                                       incoming, path, writer)

    def _write_guarded(self, input_sha256, expected_revision, incoming, path, writer):
        _safe_root(self.root)
        raw = self._read_raw(path)
        current_data = self._decode(input_sha256, raw, path)
        current_exists = raw is not None and raw != b''
        current_revision = hashlib.sha256(raw).hexdigest() if current_exists else None
        if current_exists:
            if (expected_revision is None
                    or not hmac.compare_digest(expected_revision, current_revision)):
                self._conflict(input_sha256, expected_revision, current_revision,
                               current_data, incoming)
            current = normalize_draft(input_sha256, current_data)
            sync = _sync_metadata(current_data)
            if sync is not None and current == incoming:
                return self._snapshot(input_sha256, raw, current_data)
            seq = sync['seq'] + 1 if sync is not None else 1
            if seq > MAX_SYNC_SEQ:
                raise DraftError('invalid_draft', '草稿同步序号已用尽。')
        else:
            if expected_revision is not None:
                self._conflict(input_sha256, expected_revision, None, None, incoming)
            seq = 1

        output = copy.deepcopy(incoming)
        output['_sync'] = {'seq': seq, 'writer': writer}
        payload = self._encoded(output)
        self._atomic_write(path, payload)
        return self._snapshot(input_sha256, payload, output)

    def resolve_local(self, input_sha256, expected_revision, data, *, writer='unknown'):
        """Explicit local-write entry point; conflicts follow the same safe path."""
        return self.write(input_sha256, expected_revision, data, writer=writer)


def _replace_file(source, destination):
    for attempt in range(8):
        try:
            os.replace(native_path(source), native_path(destination))
            return
        except OSError as error:
            if (os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33)
                    or attempt == 7):
                raise
            time.sleep(.01 * (2 ** attempt))
