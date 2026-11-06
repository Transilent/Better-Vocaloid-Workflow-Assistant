"""Layered BS-RoFormer integrity checks that never load a model or use the network.

The regular component status is intentionally metadata-only. Full verification
is explicit, cancellable, and reports expected, actual, corrupt, and changed
entries for each manifest weight and receipt-critical runtime file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

from common import native_path


_CHUNK_SIZE = 1024 * 1024
MANIFEST_MAX_BYTES = 64 * 1024
RECEIPT_MAX_BYTES = 256 * 1024
_REPARSE_ATTRIBUTE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
_HEX = frozenset('0123456789abcdef')


class _Cancelled(Exception):
    pass


class PathBoundaryError(ValueError):
    def __init__(self, code, path, message):
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _expected_sha(value):
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    return value if len(value) == 64 and set(value) <= _HEX else None


def _expected_bytes(value):
    """Accept only a real non-negative integer; bool, float and strings are metadata errors."""
    return value if type(value) is int and value >= 0 else None


def _is_reparse(info):
    return bool(stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & _REPARSE_ATTRIBUTE)


def _identity(info):
    return {
        'device': int(getattr(info, 'st_dev', 0)),
        'inode': int(getattr(info, 'st_ino', 0)),
        'bytes': int(info.st_size),
        'mtime_ns': int(getattr(info, 'st_mtime_ns', int(info.st_mtime * 1_000_000_000))),
        'ctime_ns': int(getattr(info, 'st_ctime_ns', int(info.st_ctime * 1_000_000_000))),
    }


def _absolute_without_resolve(value):
    return Path(os.path.abspath(os.fspath(value)))


def _ancestor_reparse_issue(path):
    """Reject a reparse point at the leaf or any existing ancestor."""
    current = _absolute_without_resolve(path)
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return {'code': 'ancestor_unreadable', 'message': str(exc), 'path': str(current)}
        else:
            if _is_reparse(info):
                return {'code': 'reparse_point',
                        'message': '路径本身或已存在祖先包含符号链接或重解析点。',
                        'path': str(current)}
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _lstat_regular(path, max_bytes=None):
    issue = _ancestor_reparse_issue(path)
    if issue:
        return _absolute_without_resolve(path), None, issue
    target = _absolute_without_resolve(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target, None, {'code': 'missing', 'message': '文件不存在。', 'path': str(target)}
    except OSError as exc:
        return target, None, {'code': 'unreadable', 'message': str(exc), 'path': str(target)}
    if not stat.S_ISREG(info.st_mode):
        return target, info, {'code': 'not_regular_file', 'message': '路径不是普通文件。',
                              'path': str(target)}
    if max_bytes is not None and info.st_size > max_bytes:
        return target, info, {
            'code': 'metadata_too_large',
            'message': f'元数据文件超过 {max_bytes} 字节预算。',
            'path': str(target),
            'actual_bytes': int(info.st_size),
            'limit_bytes': max_bytes,
        }
    return target, info, None


def _lstat_directory(path):
    issue = _ancestor_reparse_issue(path)
    if issue:
        return _absolute_without_resolve(path), None, issue
    target = _absolute_without_resolve(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target, None, {'code': 'missing', 'message': '组件目录不存在。', 'path': str(target)}
    except OSError as exc:
        return target, None, {'code': 'unreadable', 'message': str(exc), 'path': str(target)}
    if not stat.S_ISDIR(info.st_mode):
        return target, info, {'code': 'not_directory', 'message': '组件路径不是普通目录。',
                              'path': str(target)}
    return target, info, None


def component_lock_path(root):
    """Validate the installer/inference sidecar without following a redirect."""
    path, _, issue = _lstat_regular(Path(root) / 'run.lock')
    if issue and issue['code'] != 'missing':
        raise PathBoundaryError(issue['code'], issue['path'], issue['message'])
    return path


def _relative_parts(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise PathBoundaryError('invalid_path', value, '组件文件路径无效。')
    if '\\' in value or ':' in value:
        raise PathBoundaryError('invalid_path', value, '组件文件路径必须是受管相对 POSIX 路径。')
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts
            or any(part in ('', '.', '..') for part in path.parts)):
        raise PathBoundaryError('path_traversal', value, '组件文件路径超出受管目录。')
    return path.parts


def _root_state(directory):
    root = _absolute_without_resolve(directory)
    try:
        info = root.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        return root, None, ({'code': 'root_unreadable', 'message': str(exc), 'path': str(root)}, None)
    issue = _ancestor_reparse_issue(root)
    if issue:
        return root, None, (issue, None)
    if info is None:
        return root, None, (None, None)
    if not stat.S_ISDIR(info.st_mode):
        return root, None, ({'code': 'root_not_directory', 'message': '组件目录不是普通目录。',
                             'path': str(root)}, None)
    return root, root, (None, _identity(info))


def _inspect_physical(root, relative):
    """Return (path, stat, issue), rejecting traversal and every reparse point below root."""
    try:
        parts = _relative_parts(relative)
    except PathBoundaryError as exc:
        return None, None, {'code': exc.code, 'message': exc.message, 'path': relative}

    current = root
    info = None
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return current, None, {'code': 'missing', 'message': '文件不存在。', 'path': relative}
        except OSError as exc:
            return current, None, {'code': 'unreadable', 'message': str(exc), 'path': relative}
        if _is_reparse(info):
            return current, info, {'code': 'reparse_point',
                                   'message': '路径包含符号链接或重解析点。', 'path': relative}
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return current, info, {'code': 'parent_not_directory',
                                   'message': '上级路径不是普通目录。', 'path': relative}
    if not stat.S_ISREG(info.st_mode):
        return current, info, {'code': 'not_regular_file', 'message': '组件路径不是普通文件。',
                               'path': relative}
    return current, info, None


def _read_json_regular(path, max_bytes):
    target, info, issue = _lstat_regular(path, max_bytes)
    if issue:
        return None, None, issue, None
    snapshot = _identity(info)
    try:
        with open(native_path(target), 'rb') as stream:
            raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return None, None, {
                    'code': 'metadata_too_large',
                    'message': f'元数据文件超过 {max_bytes} 字节预算。',
                    'path': str(target),
                    'actual_bytes': len(raw),
                    'limit_bytes': max_bytes,
                }, snapshot
            if stream.read(1):
                return None, None, {
                    'code': 'metadata_too_large',
                    'message': f'元数据文件超过 {max_bytes} 字节预算。',
                    'path': str(target),
                    'actual_bytes': max_bytes + 1,
                    'limit_bytes': max_bytes,
                }, snapshot
    except OSError as exc:
        return None, None, {'code': 'metadata_unreadable', 'message': str(exc),
                            'path': str(target)}, snapshot
    _, after, after_issue = _lstat_regular(target)
    if after_issue or _identity(after) != snapshot:
        return None, None, {'code': 'changed_during_verification',
                            'message': '读取元数据期间文件身份发生变化。',
                            'path': str(target)}, snapshot
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, None, {'code': 'metadata_invalid_json', 'message': str(exc),
                            'path': str(target)}, snapshot
    return raw, value, None, snapshot


def _problem(record, code, message):
    return {
        'path': record['expected']['path'],
        'kind': record['expected']['kind'],
        'reason': code,
        'message': message,
        'expected_bytes': record['expected']['bytes'],
        'actual_bytes': record['actual'].get('bytes'),
        'expected_sha256': record['expected']['sha256'],
        'actual_sha256': record['actual'].get('sha256'),
    }


def _mark_file_changed(record, report, message='校验期间已读文件身份发生变化。'):
    if record['changed']:
        return
    record['changed'] = True
    row = _problem(record, 'changed_during_verification', message)
    report['corrupted'].append(row)
    report['changed'].append(row)


def _hash_file(path, relative, record, report, progress, check, total):
    digest = hashlib.sha256()
    with open(native_path(path), 'rb') as stream:
        while True:
            if check and check():
                raise _Cancelled
            block = stream.read(_CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
            report['progress']['hash_bytes'] += len(block)
            if progress:
                progress(report['progress']['hash_bytes'], max(total, 1),
                         f"正在完整校验 · {relative}")
    if check and check():
        raise _Cancelled
    record['actual']['sha256'] = digest.hexdigest()
    record['actual']['sha256_checked'] = True
    record['hashed'] = True
    report['progress']['sha256_checked'] += 1
    # This hook runs after the handle is closed, allowing a deterministic test
    # or caller to observe progress without racing the file read itself.
    if progress:
        progress(report['progress']['hash_bytes'], max(total, 1),
                 f"已读取并哈希 · {relative}")


def inspect_component(directory, manifest_path, *, full=False, progress=None, check=None):
    """Inspect one installed component.

    ``full=False`` performs only cheap metadata checks. ``full=True`` hashes
    every item with a trusted expected SHA-256 in one MiB chunks. No files are
    modified and neither the model nor a runtime subprocess is loaded.
    """
    report = {
        'component': 'bs-roformer',
        'level': 'full' if full else 'metadata',
        'state': 'not_ready',
        'verified': False,
        'metadata_ready': False,
        'runtime': None,
        'probe': None,
        'manifest_sha256': None,
        'expected': [],
        'actual': [],
        'corrupted': [],
        'changed': [],
        'missing': [],
        'unverified': [],
        'errors': [],
        'progress': {'hash_bytes': 0, 'total_hash_bytes': 0,
                     'sha256_checked': 0, 'files_expected': 0},
    }
    manifest_absolute = _absolute_without_resolve(manifest_path)
    manifest_raw, spec, manifest_issue, manifest_snapshot = _read_json_regular(
        manifest_absolute, MANIFEST_MAX_BYTES)
    if manifest_issue:
        report['errors'].append(manifest_issue)
        return report
    if not isinstance(spec, dict):
        report['errors'].append({'code': 'metadata_invalid_json',
                                 'message': '组件清单必须是 JSON 对象。',
                                 'path': str(manifest_absolute)})
        return report
    report['component'] = str(spec.get('component') or report['component'])
    report['manifest_sha256'] = _sha256_bytes(manifest_raw)

    root, ready_root, (root_issue, root_snapshot) = _root_state(directory)
    if root_issue:
        report['errors'].append(root_issue)

    receipt = None
    receipt_path = root / 'installed.json'
    receipt_snapshot = None
    if ready_root is not None:
        _, receipt, receipt_issue, receipt_snapshot = _read_json_regular(
            receipt_path, RECEIPT_MAX_BYTES)
        if receipt_issue:
            report['errors'].append(receipt_issue)
            receipt = None
        elif not isinstance(receipt, dict):
            report['errors'].append({'code': 'metadata_invalid_json',
                                     'message': '安装记录必须是 JSON 对象。',
                                     'path': str(receipt_path)})
            receipt = None

    if receipt is not None:
        report['runtime'] = receipt.get('runtime')
        report['probe'] = receipt.get('probe')
        if report['runtime'] not in ('cpu', 'cuda'):
            report['errors'].append({'code': 'receipt_runtime_invalid',
                                     'message': '安装记录缺少有效运行库。',
                                     'path': 'installed.json'})
        if not isinstance(report['probe'], dict):
            report['errors'].append({'code': 'receipt_probe_missing',
                                     'message': '安装记录缺少环境探针结果。',
                                     'path': 'installed.json'})
        recorded_manifest = _expected_sha(receipt.get('manifest_sha256'))
        if recorded_manifest is None:
            report['errors'].append({'code': 'receipt_manifest_sha256_missing',
                                     'message': '安装记录缺少有效清单 SHA-256。',
                                     'path': 'installed.json'})
        elif recorded_manifest != report['manifest_sha256']:
            report['errors'].append({
                'code': 'manifest_sha256_mismatch',
                'message': '安装记录与当前组件清单版本不一致。',
                'path': 'installed.json',
                'recorded': recorded_manifest,
                'actual': report['manifest_sha256'],
            })

    weights = spec.get('weights')
    if not isinstance(weights, list):
        report['errors'].append({'code': 'manifest_weights_invalid',
                                 'message': '组件清单缺少权重列表。',
                                 'path': str(manifest_absolute)})
        weights = []
    elif not weights:
        report['errors'].append({'code': 'manifest_weights_empty',
                                 'message': '组件清单的权重列表为空。',
                                 'path': str(manifest_absolute)})
    critical = []
    if receipt is not None:
        critical_value = receipt.get('critical_files')
        if not isinstance(critical_value, list):
            report['errors'].append({'code': 'receipt_critical_files_invalid',
                                     'message': '安装记录缺少关键文件列表。',
                                     'path': 'installed.json'})
        elif not critical_value:
            report['errors'].append({'code': 'receipt_critical_files_empty',
                                     'message': '安装记录的关键文件列表为空。',
                                     'path': 'installed.json'})
        else:
            critical = critical_value

    records = []
    for kind, rows, path_key in (('weight', weights, 'name'), ('critical', critical, 'file')):
        for item in rows:
            if not isinstance(item, dict):
                report['errors'].append({'code': 'manifest_entry_invalid',
                                         'message': '组件文件条目无效。', 'kind': kind})
                continue
            relative = item.get(path_key)
            expected_size = _expected_bytes(item.get('bytes'))
            expected_sha = _expected_sha(item.get('sha256'))
            expected = {'path': relative, 'kind': kind, 'bytes': expected_size,
                        'sha256': expected_sha}
            actual = {'path': relative, 'kind': kind, 'exists': None, 'bytes': None,
                      'sha256': None, 'sha256_checked': False}
            record = {'expected': expected, 'actual': actual, 'path': None,
                      'size': None, 'metadata_bad': False, 'snapshot': None,
                      'hashed': False, 'changed': False}
            records.append(record)
            report['expected'].append(expected)
            report['actual'].append(actual)
            report['progress']['files_expected'] += 1
            if expected_size is None:
                record['metadata_bad'] = True
                report['errors'].append({'code': 'expected_bytes_missing',
                                         'message': '组件文件缺少有效的非负整数大小。',
                                         'path': relative})
            if expected_sha is None:
                report['unverified'].append({
                    'path': relative,
                    'kind': kind,
                    'reason': 'expected_sha256_missing',
                    'message': '缺少可信的预期 SHA-256，不能确认内容完整。',
                })

    report['progress']['total_hash_bytes'] = sum(
        record['expected']['bytes'] or 0 for record in records
        if record['expected']['sha256'] and record['expected']['bytes'] is not None)
    if progress:
        progress(0, max(report['progress']['total_hash_bytes'], 1),
                 f"准备{'完整' if full else '元数据'}校验 · {len(records)} 个文件")

    for record in records:
        expected, actual = record['expected'], record['actual']
        relative = expected['path']
        if ready_root is None:
            actual['exists'] = False
            record['metadata_bad'] = True
            report['missing'].append(relative)
            report['corrupted'].append(_problem(record, 'missing', '组件未安装或目录不可用。'))
            continue
        path, info, issue = _inspect_physical(root, relative)
        record['path'] = path
        if issue:
            actual['exists'] = info is not None
            actual['bytes'] = info.st_size if info is not None and stat.S_ISREG(info.st_mode) else None
            if issue['code'] == 'missing':
                report['missing'].append(relative)
            record['metadata_bad'] = True
            report['corrupted'].append(_problem(record, issue['code'], issue['message']))
            continue
        record['size'] = info.st_size
        record['snapshot'] = _identity(info)
        actual['exists'] = True
        actual['bytes'] = info.st_size
        if expected['bytes'] is not None and info.st_size != expected['bytes']:
            record['metadata_bad'] = True
            report['corrupted'].append(_problem(record, 'size_mismatch',
                                                '文件大小与清单不一致。'))
            continue
        if not full or expected['sha256'] is None or expected['bytes'] is None:
            continue
        try:
            _hash_file(path, relative, record, report, progress, check,
                       report['progress']['total_hash_bytes'])
        except _Cancelled:
            report['state'] = 'cancelled'
            return report
        except OSError as exc:
            report['corrupted'].append(_problem(record, 'read_error', str(exc)))
            continue
        _, after, after_issue = _inspect_physical(root, relative)
        if after_issue or after is None or _identity(after) != record['snapshot']:
            _mark_file_changed(record, report)
            continue
        if actual['sha256'] != expected['sha256']:
            report['corrupted'].append(_problem(record, 'sha256_mismatch',
                                                '文件内容 SHA-256 与清单不一致。'))
        if check and check():
            report['state'] = 'cancelled'
            return report

    if full:
        metadata_paths = (
            ('manifest', manifest_absolute, manifest_snapshot),
            ('receipt', receipt_path, receipt_snapshot),
        )
        for kind, path, snapshot in metadata_paths:
            if snapshot is None:
                continue
            _, current, issue = _lstat_regular(path)
            if issue or current is None or _identity(current) != snapshot:
                row = {'code': 'changed_during_verification',
                       'message': '校验结束前元数据文件身份发生变化。',
                       'path': str(path), 'kind': kind}
                report['errors'].append(row)
                report['changed'].append(row)
        if root_snapshot is not None:
            _, current_root, issue = _lstat_directory(root)
            if issue or current_root is None or _identity(current_root) != root_snapshot:
                row = {'code': 'changed_during_verification',
                       'message': '校验结束前组件目录身份发生变化。',
                       'path': str(root), 'kind': 'component_root'}
                report['errors'].append(row)
                report['changed'].append(row)
        for record in records:
            if not record['hashed'] or record['changed']:
                continue
            _, final_info, issue = _inspect_physical(root, record['expected']['path'])
            if issue or final_info is None or _identity(final_info) != record['snapshot']:
                _mark_file_changed(record, report, '校验结束前已读文件身份发生变化。')

    report['metadata_ready'] = not report['errors'] and not any(
        record['metadata_bad'] for record in records)
    content_corrupt = any(row['reason'] in ('sha256_mismatch', 'read_error',
                                            'changed_during_verification')
                          for row in report['corrupted'])
    if not full:
        report['state'] = 'metadata_ready' if report['metadata_ready'] else 'not_ready'
    elif report['changed']:
        report['state'] = 'changed_during_verification'
        report['verified'] = False
    elif not report['metadata_ready']:
        report['state'] = 'not_ready'
    elif content_corrupt:
        report['state'] = 'corrupt'
    elif report['unverified']:
        report['state'] = 'unverified'
    else:
        report['state'] = 'verified'
        report['verified'] = True
    if progress:
        progress(report['progress']['total_hash_bytes'] or 1,
                 report['progress']['total_hash_bytes'] or 1,
                 '完整校验通过' if report['verified'] else '完整校验结束')
    return report


def verify_component(directory, manifest_path, progress=None, check=None):
    """Explicit full verification with per-file SHA-256 checks."""
    return inspect_component(directory, manifest_path, full=True,
                             progress=progress, check=check)


def metadata_status(directory, manifest_path):
    """Cheap metadata status used by the normal application path."""
    return inspect_component(directory, manifest_path, full=False)
