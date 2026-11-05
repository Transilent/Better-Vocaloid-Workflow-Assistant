"""Small all-or-restore transaction for a related set of output files."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid


STALE_OUTPUT_BACKUP = 'stale_output_backup'
OUTPUT_COMMIT_ROLLBACK_FAILED = 'output_commit_rollback_failed'
INVALID_STAGING_OUTPUT = 'invalid_staging_output'


class FileTransactionError(RuntimeError):
    """A failed output transaction with recovery state suitable for reports."""

    def __init__(self, message, *, code, details=None, preserve_staging=False):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.preserve_staging = preserve_staging


def _backup_path(target):
    return target.with_name(target.stem + '.previous' + target.suffix)


def _identity(path):
    return str(Path(path).resolve(strict=False)).casefold()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _path_set_error(staging, targets, backups, *, overlap=None, duplicate=None):
    details = {
        'staging': [str(path) for path in staging],
        'targets': [str(path) for path in targets],
        'backups': [str(path) for path in backups],
    }
    if overlap:
        details['overlap'] = overlap
    if duplicate:
        details['duplicate'] = duplicate
    return FileTransactionError(
        '多文件提交的暂存、目标和备份路径存在重复或交叉。',
        code=INVALID_STAGING_OUTPUT,
        details=details,
    )


def commit_file_pair(staging_paths, target_paths, *, backup_paths=None, validate=None):
    """Publish every staged file or restore every previous target.

    ``backup_paths`` defaults to the historical ``.previous`` sidecar names.
    When supplied, ``validate`` receives the published targets as a tuple and
    must raise to reject the transaction. Backups remain in place until that
    callback returns successfully. Staging files are removed after a successful
    commit or rollback; they remain beside their backups when rollback itself
    cannot be completed.
    """
    staging = [Path(path) for path in staging_paths]
    targets = [Path(path) for path in target_paths]
    backups = (([_backup_path(target) for target in targets])
               if backup_paths is None else [Path(path) for path in backup_paths])
    if len(staging) < 2 or len(staging) != len(targets) or len(targets) != len(backups):
        raise FileTransactionError(
            '多文件提交必须提供数量相同的至少两个暂存、目标与备份文件。',
            code=INVALID_STAGING_OUTPUT,
            details={'staging': [str(path) for path in staging],
                     'targets': [str(path) for path in targets],
                     'backups': [str(path) for path in backups]})
    staging_identities = [_identity(path) for path in staging]
    target_identities = [_identity(path) for path in targets]
    backup_identities = [_identity(path) for path in backups]
    duplicate = {}
    for name, identities in (('staging', staging_identities),
                             ('targets', target_identities),
                             ('backups', backup_identities)):
        repeated = sorted({value for value in identities if identities.count(value) > 1})
        if repeated:
            duplicate[name] = repeated
    overlaps = {}
    for left_name, left, right_name, right in (
            ('staging', staging_identities, 'targets', target_identities),
            ('staging', staging_identities, 'backups', backup_identities),
            ('targets', target_identities, 'backups', backup_identities)):
        shared = sorted(set(left) & set(right))
        if shared:
            overlaps[left_name + '/' + right_name] = shared
    if duplicate or overlaps:
        all_overlap = sorted({value for values in overlaps.values() for value in values})
        raise _path_set_error(
            staging, targets, backups,
            overlap=all_overlap or None,
            duplicate=duplicate or None,
        )
    invalid = [str(path) for path in staging if not path.is_file() or path.stat().st_size <= 0]
    invalid_targets = [str(path) for path in targets if path.exists() and not path.is_file()]
    if invalid or invalid_targets:
        raise FileTransactionError(
            '多文件提交的暂存或目标格式无效。', code=INVALID_STAGING_OUTPUT,
            details={'invalid_staging': invalid, 'invalid_targets': invalid_targets})

    stale = [str(path) for path in backups if path.exists() or path.is_symlink()]
    if stale:
        raise FileTransactionError(
            '发现未清理的输出提交备份，已停止覆盖。', code=STALE_OUTPUT_BACKUP,
            details={'files': stale,
                     'repair_hint': '核对 .previous 文件与当前输出后再移走备份；不要直接删除唯一副本。'})

    moved_originals = []
    published = []
    try:
        for target, backup in zip(targets, backups):
            if target.exists():
                target.replace(backup)
                moved_originals.append((target, backup))
        for source, target in zip(staging, targets):
            source.replace(target)
            published.append(target)
        if validate is not None:
            validate(tuple(targets))
        # Receipt construction is part of the commit: keep rollback copies
        # until every output can also be read and identified successfully.
        committed_files = [{'path': target.name, 'sha256': _sha256(target)} for target in targets]
    except BaseException as commit_error:
        rollback_errors = []
        moved_targets = {target for target, _ in moved_originals}
        for target in reversed(published):
            if target not in moved_targets:
                try:
                    target.unlink(missing_ok=True)
                except BaseException as rollback:
                    rollback_errors.append({'target': str(target), 'error': str(rollback)})
        for target, backup in reversed(moved_originals):
            try:
                if not backup.exists():
                    raise FileNotFoundError(str(backup))
                backup.replace(target)
            except BaseException as rollback:
                rollback_errors.append({'target': str(target), 'backup': str(backup),
                                        'error': str(rollback)})
        if rollback_errors:
            raise FileTransactionError(
                '多文件提交失败且自动回滚未完整完成。',
                code=OUTPUT_COMMIT_ROLLBACK_FAILED,
                details={'rollback_errors': rollback_errors,
                         'staging': [str(path) for path in staging if path.exists()],
                         'backups': [str(path) for path in backups if path.exists()],
                         'repair_hint': '保留 .previous 与 .pending 文件，核对后手动恢复；不要覆盖现有输出。'},
                preserve_staging=True) from commit_error
        for source in staging:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                # The old targets are already restored; leave this staging file
                # for the caller to inspect instead of hiding the commit error.
                pass
        raise

    cleanup_errors = []
    for backup in backups:
        try:
            backup.unlink(missing_ok=True)
        except OSError as error:
            cleanup_errors.append({'path': str(backup), 'error': str(error)})
    staging_cleanup_errors = []
    for source in staging:
        try:
            source.unlink(missing_ok=True)
        except OSError as error:
            staging_cleanup_errors.append({'path': str(source), 'error': str(error)})
    return {
        'schema': 1,
        'transaction_id': uuid.uuid4().hex,
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'strategy': 'backup_then_replace_all',
        'files': committed_files,
        'backup_cleanup_errors': cleanup_errors,
        'staging_cleanup_errors': staging_cleanup_errors,
    }
