"""Focused contracts for source selection, cancellation doors and stem commits."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
import common
# Focus this script on the canonical source tree before generated root copies
# are refreshed by the main workspace prepare step.
common.ROOT = APP
import agent_preparation
import pipeline
import roformer
agent_preparation.ROOT = APP
pipeline.ROOT = APP
from lyrics_sources import fetch
from multi_file_commit import (
    FileTransactionError,
    OUTPUT_COMMIT_ROLLBACK_FAILED,
    STALE_OUTPUT_BACKUP,
    commit_file_pair,
)
from source_contract import (
    BILIBILI_SUBPAGE_QUERY_KEYS,
    UNSUPPORTED_BILIBILI_SUBPAGE,
    SourceContractError,
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='pipeline-safety-') as folder:
    root = Path(folder)

    # The GUI and preparation boundary use one explicit subpage denylist. A URL
    # containing one of these selectors is rejected instead of becoming p=1.
    try:
        agent_preparation._bilibili_query_pairs('p=2&vd_source=fixture')
    except SourceContractError as error:
        assert error.code == UNSUPPORTED_BILIBILI_SUBPAGE
    else:
        raise AssertionError('preparation subpage wrapper bypassed the shared rule')
    rejected_sources = (
        'https://www.bilibili.com/video/BV1r668YbEyp/?p=2',
        'https://www.bilibili.com/video/BV1r668YbEyp/?cid=111',
        'https://www.bilibili.com/video/BV1r668YbEyp/?page=2',
        'BV1r668YbEyp?p=2',
        'https://www.bilibili.com/festival/example?bvid=BV1r668YbEyp&p=2',
    )
    for source in rejected_sources:
        try:
            pipeline.create_job(source, parent=root / 'rejected', remember=False)
        except SourceContractError as error:
            assert error.code == UNSUPPORTED_BILIBILI_SUBPAGE
            assert error.details['parameters']
        else:
            raise AssertionError(f'unsupported Bilibili subpage accepted: {source}')
    allowed = pipeline.create_job(
        'https://www.bilibili.com/video/BV1r668YbEyp/?vd_source=fixture',
        parent=root / 'allowed', remember=False)
    request = json.loads((allowed / 'request.json').read_text(encoding='utf-8'))
    assert request['bv'] == 'BV1r668YbEyp'
    assert request['url'] == 'https://www.bilibili.com/video/BV1r668YbEyp/'

    # A flag older than the last terminal status is stale. A flag created after
    # that status is a current stop request even if the child runner has not yet
    # entered its first stage.
    job = pipeline.create_job('BV1000000000', parent=root / 'cancel', remember=False)
    common.atomic_json(job / 'status.json', {'state': 'cancelled', 'steps': {}})
    flag = job / 'cancel.flag'
    status_ns = (job / 'status.json').stat().st_mtime_ns
    flag.touch()
    os.utime(flag, ns=(status_ns - 2_000_000_000, status_ns - 2_000_000_000))
    seen = []
    with patch.object(pipeline, '_run', side_effect=lambda *_: seen.append(flag.exists())):
        pipeline.run(job)
    assert seen == [False]
    flag.touch()
    os.utime(flag, ns=(status_ns + 2_000_000_000, status_ns + 2_000_000_000))
    with patch.object(pipeline, '_run', side_effect=lambda *_: seen.append(flag.exists())):
        pipeline.run(job)
    assert seen == [False, True]
    flag.unlink()

    # Inject a new request after the old flag has been claimed but before its
    # terminal-state comparison. Cleanup must remove only the claim, never the
    # newly written cancel.flag.
    flag.touch()
    os.utime(flag, ns=(status_ns - 2_000_000_000, status_ns - 2_000_000_000))
    original_json_loads = pipeline.json.loads
    injected = [False]

    def load_status_with_late_cancel(raw, *args, **kwargs):
        if 'cancelled' in raw and not injected[0]:
            injected[0] = True
            common.atomic_json(flag, {'source': 'late-cancel-fixture'})
        return original_json_loads(raw, *args, **kwargs)

    with patch.object(pipeline.json, 'loads', new=load_status_with_late_cancel):
        assert pipeline.discard_obsolete_cancel_request(job)
    assert injected[0] and flag.exists()
    assert json.loads(flag.read_text(encoding='utf-8'))['source'] == 'late-cancel-fixture'
    assert not list(job.glob('cancel.claim-*.flag'))
    flag.unlink()

    # The two MDX/RoFormer outputs publish as one transaction. A second rename
    # failure restores both old files and clears only transaction-owned staging.
    overlap_dir = root / 'overlap'
    overlap_dir.mkdir()
    overlap_staging = [overlap_dir / 'lead.pending.wav', overlap_dir / 'backing.pending.wav']
    for item in overlap_staging:
        item.write_bytes(b'fixture')
    try:
        commit_file_pair(overlap_staging, [overlap_staging[0], overlap_dir / 'backing.wav'])
    except FileTransactionError as error:
        assert error.code == 'invalid_staging_output'
        assert error.details['overlap']
    else:
        raise AssertionError('staging/target intersection was accepted')

    tx = root / 'tx'
    tx.mkdir()
    targets = [tx / 'vocals.wav', tx / 'instrumental.wav']
    for target, value in zip(targets, (b'old-vocals', b'old-instrumental')):
        target.write_bytes(value)
    staging = [tx / 'vocals.pending.wav', tx / 'instrumental.pending.wav']
    for source, value in zip(staging, (b'new-vocals', b'new-instrumental')):
        source.write_bytes(value)
    old_hashes = [digest(target) for target in targets]
    original_replace = Path.replace

    def fail_second_pending(source, target):
        if source.name == 'instrumental.pending.wav' and target.name == 'instrumental.wav':
            raise OSError('fixture second output failure')
        return original_replace(source, target)

    with patch.object(Path, 'replace', new=fail_second_pending):
        try:
            commit_file_pair(staging, targets)
        except OSError as error:
            assert 'fixture second output failure' in str(error)
        else:
            raise AssertionError('second output failure was hidden')
    assert [digest(target) for target in targets] == old_hashes
    assert not list(tx.glob('*.pending.wav'))
    assert not list(tx.glob('*.previous.wav'))

    for source, value in zip(staging, (b'new-vocals', b'new-instrumental')):
        source.write_bytes(value)
    receipt = commit_file_pair(staging, targets)
    assert targets[0].read_bytes() == b'new-vocals'
    assert targets[1].read_bytes() == b'new-instrumental'
    assert receipt['schema'] == 1 and len(receipt['files']) == 2
    assert not list(tx.glob('*.pending.wav')) and not list(tx.glob('*.previous.wav'))

    # An interrupted recovery keeps its backups and staging rather than deleting
    # the only remaining copies.
    rollback = root / 'rollback-incomplete'
    rollback.mkdir()
    rt = [rollback / 'lead.wav', rollback / 'backing.wav']
    rt[0].write_bytes(b'old-lead'); rt[1].write_bytes(b'old-backing')
    rs = [rollback / 'lead.pending.wav', rollback / 'backing.pending.wav']
    rs[0].write_bytes(b'new-lead'); rs[1].write_bytes(b'new-backing')

    def fail_second_and_restore(source, target):
        if source.name == 'backing.pending.wav':
            raise OSError('fixture publish failure')
        if source.name == 'lead.previous.wav':
            raise OSError('fixture rollback failure')
        return original_replace(source, target)

    with patch.object(Path, 'replace', new=fail_second_and_restore):
        try:
            commit_file_pair(rs, rt)
        except FileTransactionError as error:
            assert error.code == OUTPUT_COMMIT_ROLLBACK_FAILED
            assert error.preserve_staging
        else:
            raise AssertionError('incomplete rollback was hidden')
    assert (rollback / 'lead.previous.wav').exists()
    assert (rollback / 'backing.pending.wav').exists()

    stale = root / 'stale'
    stale.mkdir()
    st = [stale / 'lead.wav', stale / 'backing.wav']
    ss = [stale / 'lead.pending.wav', stale / 'backing.pending.wav']
    for path in (*ss, stale / 'lead.previous.wav'):
        path.write_bytes(b'fixture')
    try:
        commit_file_pair(ss, st)
    except FileTransactionError as error:
        assert error.code == STALE_OUTPUT_BACKUP
    else:
        raise AssertionError('stale output backup was overwritten')

    # RoFormer's independent combination uses the same transaction boundary.
    sources = root / 'roformer-sources'; sources.mkdir()
    destinations = root / 'roformer-output'; destinations.mkdir()
    total = np.stack([np.linspace(-.5, .5, 441, dtype='float32')] * 2, axis=1)
    foreground = total * .4
    sf.write(sources / 'vocals.wav', total, 44100, subtype='FLOAT')
    sf.write(sources / 'lead.wav', foreground, 44100, subtype='FLOAT')
    for name in ('lead.wav', 'backing.wav'):
        sf.write(destinations / name, total * 0, 44100, subtype='FLOAT')
    output_hashes = {name: digest(destinations / name) for name in ('lead.wav', 'backing.wav')}

    def fail_roformer_backing(source, target):
        if source.name == 'backing.pending.wav' and target.name == 'backing.wav':
            raise OSError('fixture RoFormer second output failure')
        return original_replace(source, target)

    with patch.object(Path, 'replace', new=fail_roformer_backing):
        try:
            roformer.combine_independent(sources / 'vocals.wav', sources / 'lead.wav', destinations, destinations)
        except OSError:
            pass
        else:
            raise AssertionError('RoFormer second output failure was hidden')
    assert {name: digest(destinations / name) for name in output_hashes} == output_hashes
    assert not list(destinations.glob('*.pending.wav')) and not list(destinations.glob('*.previous.wav'))

    # Credit-only YRC is not usable; the same song's valid LRC must be selected.
    fallback = fetch('12345', reader=lambda *args: {
        'code': 200,
        'yrc': {'lyric': '[1000,500](1000,200,0)作词：作者'},
        'lrc': {'lyric': '[00:01.00]有效歌词'},
    })
    assert fallback.text == '有效歌词'
    assert fallback.origin['format'] == 'lrc'
    assert '有效歌词' in fallback.raw

print('PASS: source subpage rejection, cancellation generation, paired stem rollback and YRC-to-LRC fallback')
