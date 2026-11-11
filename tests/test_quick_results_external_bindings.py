"""Synthetic contract for Quick external SVP/Mixdown registration and unlink."""
import hashlib
import json
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import i18n
import quick_results
import task_contract
import task_store


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_svp(path, pitch):
    write_json(path, {
        'tracks': [
            {'mainGroup': {'notes': [{'pitch': pitch}]}, 'mainRef': {}},
            {'mainGroup': {'notes': []}, 'mainRef': {'isInstrumental': True}},
        ]
    })


def make_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(22050)
        stream.writeframes(b'\0\0' * 2205)


def make_job(root):
    job = root / 'jobs' / '20261111_quick_binding_fixture'
    (job / 'audio').mkdir(parents=True)
    write_json(job / 'request.json', {'source_title': '合成登记夹具', 'bv': 'BV1BINDING'})
    write_json(job / 'status.json', {'state': 'done'})
    write_json(job / 'task.json', {'name': '合成登记夹具'})
    audio = job / 'audio' / 'lead.wav'
    audio.write_bytes(b'synthetic source audio')
    task_contract.register(job, [{'path': audio, 'role': 'audio'}], 'audio')
    return job


def rejected(action, expected=(task_contract.ContractError, ValueError)):
    try:
        action()
    except expected:
        return
    raise AssertionError('Expected the quick task contract to reject the mutation')


i18n.set_language('zh-CN')
(APP / 'work').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='quick-results-bindings-') as temp:
    root = Path(temp)
    (root / 'cache').mkdir()
    job = make_job(root)
    external = root / 'external-user-files'
    external.mkdir()
    svp = external / 'outside-edit.svp'
    mixdown = external / 'outside-mix.wav'
    make_svp(svp, 60)
    make_wav(mixdown)
    svp_hash, mixdown_hash = digest(svp), digest(mixdown)

    with patch.object(task_store, 'job_directories', return_value=[job]):
        initial = quick_results.detail(job)
        assert initial['bindings'] == {}
        assert initial['canRegisterProject'] and initial['canRegisterMixdown']
        assert initial['canRebuildArtifacts'] and initial['artifactIndexRevision']

        rejected(lambda: quick_results.register_external(
            job, 'video', mixdown, expected_binding_id='', expected_binding_revision=''))
        project_id = quick_results.register_external(
            job, 'project', svp, expected_binding_id='', expected_binding_revision='')
        after_project = quick_results.detail(job)
        project = after_project['bindings']['project']
        assert project_id == project['id']
        assert Path(project['path']) == svp.resolve() and project['available']
        assert after_project['canUnbindProject'] and not after_project['hasMixdownBinding']
        assert digest(svp) == svp_hash

        mixdown_id = quick_results.register_external(
            job, 'mixdown', mixdown, expected_binding_id='', expected_binding_revision='')
        after_mixdown = quick_results.detail(job)
        mix = after_mixdown['bindings']['mixdown']
        assert mixdown_id == mix['id'] and mix['available']
        assert after_mixdown['canUnbindMixdown']
        assert any(Path(item['path']) == svp.resolve()
                   for item in after_mixdown['artifacts'] if item['role'] == 'tuned_project')
        assert digest(mixdown) == mixdown_hash

        rejected(lambda: quick_results.unlink_external(
            job, 'project', expected_binding_id=project_id,
            expected_binding_revision='0' * 64))
        assert quick_results.detail(job)['bindings']['project']['id'] == project_id

        make_svp(svp, 64)
        revised_hash = digest(svp)
        quick_results.register_external(
            job, 'project', svp, expected_binding_id=project_id,
            expected_binding_revision=project['revision'])
        revised = quick_results.detail(job)
        assert revised['bindings']['project']['revision'] != project['revision']
        assert revised['bindings']['mixdown']['state'] == 'stale'
        assert digest(svp) == revised_hash and digest(mixdown) == mixdown_hash

        index_revision = revised['artifactIndexRevision']
        backup = quick_results.rebuild_artifacts(job, expected_index_revision=index_revision)
        assert Path(backup).is_file() and Path(backup).parent == job
        rebuilt = quick_results.detail(job)
        assert set(rebuilt['bindings']) == {'project', 'mixdown'}
        assert Path(rebuilt['bindings']['project']['path']) == svp.resolve()
        assert Path(rebuilt['bindings']['mixdown']['path']) == mixdown.resolve()
        assert digest(svp) == revised_hash and digest(mixdown) == mixdown_hash

        rejected(lambda: quick_results.rebuild_artifacts(
            job, expected_index_revision=index_revision))
        mix = rebuilt['bindings']['mixdown']
        quick_results.unlink_external(
            job, 'mixdown', expected_binding_id=mix['id'],
            expected_binding_revision=mix['revision'])
        assert 'mixdown' not in quick_results.detail(job)['bindings']
        assert digest(mixdown) == mixdown_hash
        project = rebuilt['bindings']['project']
        quick_results.unlink_external(
            job, 'project', expected_binding_id=project['id'],
            expected_binding_revision=project['revision'])
        assert quick_results.detail(job)['bindings'] == {}
        assert svp.is_file() and mixdown.is_file()


qml = (APP / 'src/quick_ui/ResultsPage.qml').read_text(encoding='utf-8')
assert 'registerSelectedTaskFile(' in qml
assert 'unlinkSelectedTaskBinding(' in qml
assert 'rebuildSelectedTaskArtifacts(' in qml
assert 'objectName: "registerTaskProject"' in qml
assert 'objectName: "registerTaskMixdown"' in qml
assert 'objectName: "rebuildTaskArtifacts"' in qml
assert '取消登记和重建只修改任务记录' in qml
assert '外部文件未被修改' not in qml

print('PASS: synthetic Quick SVP/Mixdown registration, revision CAS, unlink and rebuild preserve external files')
