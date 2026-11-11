"""Task-results projection tests with synthetic, isolated task directories."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import common
import i18n
import quick_results
import task_contract
import task_store


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def make_job(root, name, title, state='failed', message=''):
    job = root / 'jobs' / name
    (job / 'audio').mkdir(parents=True)
    write_json(job / 'request.json', {'source_title': '测试歌曲', 'bv': 'BV1TEST'})
    status = {'state': state}
    if message:
        status['message'] = message
    write_json(job / 'status.json', status)
    write_json(job / 'task.json', {'name': title})
    artifact = job / 'audio' / 'lead.wav'
    artifact.write_bytes((name + '-audio').encode('ascii'))
    task_contract.register(job, [{'path': artifact, 'role': 'audio'}], 'audio')
    return job, artifact


i18n.set_language('zh-CN')
(APP / 'work').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='quick-results-') as temp:
    root = Path(temp)
    (root / 'cache').mkdir()
    failed, failed_audio = make_job(root, '20261111_001_bvtest', '测试任务', message='合成失败')
    running, running_audio = make_job(root, '20261111_002_bvtest', '运行任务', state='running')
    removable, _ = make_job(root, '20261111_003_bvtest', '待删除任务', state='done')
    broken, _ = make_job(root, '20261111_004_bvtest', '损坏任务', state='done')
    jobs = (failed, running, removable, broken)

    def directories():
        return [job for job in jobs if job.exists()]

    with patch.object(task_store, 'ROOT', root), \
            patch.object(task_store, 'job_directories', side_effect=directories):
        rows = quick_results.scan()
        failed_row = next(row for row in rows if row['jobId'] == str(failed.resolve()))
        assert set(('jobId', 'title', 'statusText', 'summary')).issubset(failed_row)
        assert failed_row['title'] == '测试任务' and failed_row['state'] == 'failed'
        assert 'BV1TEST' in failed_row['searchText'] and '2026-' in failed_row['summary']
        try:
            quick_results.scan(check=lambda: True)
        except InterruptedError:
            pass
        else:
            raise AssertionError('Cancelled scan exposed a partial task list')

        result = quick_results.detail(failed_row['jobId'])
        assert result['jobId'] == str(failed.resolve())
        assert result['state'] == 'failed' and result['handoff'] == 'retry'
        assert result['canResume'] and result['canRename'] and result['canDelete']
        assert result['recovery'] == {'state': 'failed', 'message': '合成失败'}
        artifact = result['artifacts'][0]
        assert set(artifact) == {'id', 'label', 'statusText', 'available', 'openable',
                                 'path', 'displayPath', 'role', 'state', 'sha256', 'revision'}
        assert artifact['available'] is True and Path(artifact['path']) == failed_audio.resolve()
        assert artifact['role'] == 'audio' and artifact['state'] == 'verified'
        assert len(artifact['sha256']) == 64 and artifact['revision']
        assert '原曲音频' in artifact['label'] and Path(artifact['displayPath']) == Path('audio/lead.wav')
        mixdown = failed / 'finished.wav'
        mixdown.write_bytes(b'distinct mixdown output')
        task_contract.bind(failed, 'mixdown', mixdown, validate=False)
        write_json(failed / 'status.json', {'state': 'done'})
        finished = quick_results.detail(failed)
        assert finished['handoff'] == 'mixdown_ready' and finished['canDispatch']
        selected = next(item for item in finished['artifacts'] if item['id'] == finished['delivery']['openArtifactId'])
        assert selected['role'] == 'mixdown' and Path(selected['path']) == mixdown.resolve()

        failed_audio.write_bytes(b'changed upstream')
        stale = quick_results.detail(failed)
        assert stale['handoff'] == 'needs_review' and not stale['canDispatch']
        assert next(item for item in stale['artifacts'] if item['role'] == 'mixdown')['state'] == 'stale'
        write_json(failed / 'status.json', {'state': 'failed', 'message': '合成失败'})

        failed_audio.write_bytes(b'changed-audio')
        changed = quick_results.detail(failed)
        assert changed['state'] == 'failed' and changed['statusText'] == '失败'
        stem = next(item for item in changed['artifacts'] if item['role'] == 'audio')
        assert stem['statusText'] == '文件已修改'
        assert stem['available'] is False and changed['canResume'] is True

        failed_audio.unlink()
        missing = quick_results.detail(failed)
        stem = next(item for item in missing['artifacts'] if item['role'] == 'audio')
        assert stem['statusText'] == '文件缺失'
        assert stem['available'] is False and missing['canResume'] is True
        assert missing['canRename'] and missing['canDelete']

        run_lock = running / 'run.lock'
        run_lock.touch()
        with common.exclusive_sidecar(run_lock):
            running_row = next(row for row in quick_results.scan() if row['jobId'] == str(running.resolve()))
            assert running_row['state'] == 'running'
            running_detail = quick_results.detail(running)
            assert running_detail['state'] == 'running'
            assert not running_detail['canResume']
            assert not running_detail['canRename']
            assert not running_detail['canDelete']
            for command in (lambda: quick_results.rename(running, '新名字'), lambda: quick_results.delete(running)):
                try:
                    command()
                except ValueError:
                    pass
                else:
                    raise AssertionError('Running-task protection was bypassed')

        quick_results.rename(removable, '已重命名')
        renamed = next(row for row in quick_results.scan() if row['jobId'] == str(removable.resolve()))
        assert renamed['title'] == '已重命名'
        quick_results.delete(removable)
        assert not removable.exists()
        assert all(row['jobId'] != str(removable.resolve()) for row in quick_results.scan())

        (broken / 'artifacts.json').write_bytes(b'not-json')
        broken_detail = quick_results.detail(broken)
        assert broken_detail['state'] == 'done' and broken_detail['statusText'] == '已完成'
        assert broken_detail['artifacts'] == []
        assert not broken_detail['canResume']
        assert not broken_detail['canRename']
        assert not broken_detail['canDelete']
        assert broken_detail['recovery']['message']

        for action in (lambda: quick_results.detail(failed, check=lambda: True),
                       lambda: quick_results.detail(root / 'missing-task')):
            try:
                action()
            except (InterruptedError, ValueError):
                pass
            else:
                raise AssertionError('Invalid or cancelled detail request was accepted')

qml = (APP / 'src/quick_ui/ResultsPage.qml').read_text(encoding='utf-8')
assert qml.count('property string expectedJobId: ""') == 2
assert 'renameSelectedTask(text.trim(), renameDialog.expectedJobId)' in qml
assert 'renameSelectedTask(renameField.text.trim(), renameDialog.expectedJobId)' in qml
assert 'deleteSelectedTask(deleteDialog.expectedJobId)' in qml
assert 'openArtifact(artifactRow.modelData.id)' in qml

print('PASS: stable task IDs, query metadata, artifacts, recovery, locks, rename and delete')
