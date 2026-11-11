"""Exact, cancellable media handoffs shared by the Quick workspaces."""
from pathlib import Path

import task_contract
import task_store


def artifact_identity(row):
    return tuple(row.get(key) for key in ('id', 'revision', 'sha256', 'path', 'role'))


def verify_task_artifact(job, row, role, check=None):
    """Recheck one published identity and its complete dependency chain."""
    if not isinstance(row, dict) or row.get('role') != role or not row.get('id'):
        raise ValueError('交接产物的类型或身份不匹配。')
    job = task_store.known_job(job)
    rows = task_contract.inspect(job, check=check, artifact_ids=(row['id'],))
    actual = next((item for item in rows if item['id'] == row['id']), None)
    if (not actual or actual['state'] != 'verified'
            or artifact_identity(actual) != artifact_identity(row)):
        raise ValueError('文件或上游修订已变化，请回到任务与结果重新选择。')
    if role in ('mixdown', 'video') and task_contract.selected(job, role, rows=rows) != Path(actual['path']):
        raise ValueError('任务已登记其他成品，请重新交接。')
    return actual


def verify_publication_bundle(job, bundle, check=None):
    """Resolve the registered manifest, then verify only its dependency chain."""
    job = task_store.known_job(job)
    manifest = (Path(bundle) / 'manifest.json').resolve()
    rows = task_contract.inspect(job, verify=False, check=check)
    row = next((item for item in rows if item['role'] == 'publication'
                and item['state'] == 'unchecked' and Path(item['path']) == manifest), None)
    if row is None:
        raise ValueError('该发布资料没有登记，或已被替换。')
    return verify_task_artifact(job, row, 'publication', check=check)


def studio_targets(job, rows):
    """Return exact target IDs from the same inspection as the result list."""
    verified = [row for row in rows if row.get('state') == 'verified']

    def binding(kind):
        try:
            path = task_contract.selected(job, kind, rows=rows)
        except task_contract.ContractError:
            return None
        return next((row for row in verified if row['role'] == kind and Path(row['path']) == path), None)

    mix = binding('mixdown')
    video = binding('video')
    targets = {}
    if mix:
        sources = [row for row in verified if row['role'] == 'source_video'
                   and Path(row['path']).suffix.casefold() in ('.mp4', '.mkv', '.mov', '.webm')]
        primary = next((row for row in sources if Path(row['path']) == job / 'video/source.mp4'), None)
        if primary is None and len(sources) == 1:
            primary = sources[0]
        targets['video_mixdown'] = [mix['id']] + ([primary['id']] if primary else [])
    if video:
        pair = {'id': video['id'], 'revision': video['revision']}
        bundle = next((row for row in verified if row['role'] == 'publication'
                       and pair in row.get('dependencies', ())), None)
        targets['publishing'] = [video['id']] + ([bundle['id']] if bundle else [])
    return targets
