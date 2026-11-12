"""Synthetic task-reference protection for the Quick results boundary."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import i18n
import quick_results
import task_contract
import task_store
from quick_bridge import QuickBridge, _task_reference_groups


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def make_job(root, name):
    job = root / 'jobs' / name
    (job / 'audio').mkdir(parents=True)
    write_json(job / 'request.json', {'source_title': name, 'bv': 'BV1SAFETY'})
    write_json(job / 'status.json', {'state': 'done'})
    write_json(job / 'task.json', {'name': name})
    audio = job / 'audio' / 'lead.wav'
    audio.write_bytes(('audio-' + name).encode('ascii'))
    task_contract.register(job, [{'path': audio, 'role': 'audio'}], 'audio')
    return job, audio


i18n.set_language('zh-CN')
(APP / 'work').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='quick-task-safety-') as temp:
    root = Path(temp)
    (root / 'cache').mkdir()
    job_a, audio_a = make_job(root, 'task-a')
    job_b, audio_b = make_job(root, 'task-b')
    job_c, _ = make_job(root, 'task-c')
    outside = root / 'outside'
    outside.mkdir()

    groups = _task_reference_groups(
        mixdown=SimpleNamespace(view={'job': str(job_a), 'playing': True, 'busy': False}),
        mixdown_tracks=[{'path': str(audio_a)}],
        tuning=SimpleNamespace(view={'job': str(job_a), 'source': str(job_a / 'exports/a.svp')}),
        video=SimpleNamespace(view={'job': str(job_b), 'mixdown': str(audio_b)}),
        publication=SimpleNamespace(view={'job': str(job_a / 'external.svp')}),
        calibration=SimpleNamespace(view={'job': str(job_a), 'project': str(job_a / 'cal.svp')}),
        subtitles=SimpleNamespace(view={'taskJobId': str(job_b), 'sourcePath': str(audio_b)}),
        subtitle_drafts={
            'midi': {'_task_job_id': str(job_a), '_source_path': str(audio_a)},
            'lrc': {'_source_path': str(job_a / 'subs/lyrics.lrc')},
            'netease': {'_task_job_id': str(job_b), '_source_path': ''},
        },
        preparation={'job': str(job_a)},
        operation={'label': '任务产物 · 正在核验', 'paths': [str(job_a)]},
    )
    signature = QuickBridge._task_safety_signature(job_a, groups, [])
    signature_repeat = QuickBridge._task_safety_signature(job_a, [dict(row) for row in groups], [])
    changed_label = [dict(row) for row in groups]
    changed_label[1] = {**changed_label[1], 'label': '调音 · 正在处理'}
    assert signature == signature_repeat
    assert signature != QuickBridge._task_safety_signature(job_a, changed_label, [])
    blockers_a = quick_results.task_reference_blockers(job_a, groups)
    labels_a = {row['label'] for row in blockers_a}
    assert '简易混音 · 正在播放' in labels_a
    assert '调音 · 已载入' in labels_a
    assert '声库校准 · 已载入' in labels_a
    assert '字幕工具 · MIDI 草稿' in labels_a
    assert '字幕工具 · LRC 草稿' in labels_a
    assert '准备任务 · 正在运行' in labels_a
    assert '任务产物 · 正在核验' in labels_a
    blockers_b = quick_results.task_reference_blockers(job_b, groups)
    labels_b = {row['label'] for row in blockers_b}
    assert '视频 · 已载入' in labels_b and '字幕工具 · 已载入来源' in labels_b
    assert '字幕工具 · NETEASE 草稿' in labels_b
    assert not any('midi' in row['label'].casefold() for row in blockers_b)
    assert quick_results.task_reference_blockers(job_c, groups) == []

    base = {capability: True for capability in quick_results.TASK_MUTATION_CAPABILITIES}
    base.update(canResume=True, canReprocess=True, canDispatch=True, canRead=True)
    protected = quick_results.protect_task_detail(base, blockers_a)
    assert protected['taskBlocked'] is True
    assert protected['taskBlockers'] == blockers_a
    assert '简易混音' in protected['taskBlockReason']
    assert all(protected[name] is False for name in quick_results.TASK_MUTATION_CAPABILITIES)
    assert protected['canResume'] and protected['canReprocess'] and protected['canDispatch']
    assert '不能删除任务' in quick_results.task_mutation_error('删除任务', blockers_a)
    assert quick_results.protect_task_detail(base, [])['canDelete'] is True

    for suffix in ('.svp', '.ustx', '.vpr'):
        assert quick_results.is_tuning_project({'available': True, 'role': 'tuned_project', 'path': 'a' + suffix})
    assert not quick_results.is_tuning_project({'available': True, 'role': 'tuned_project', 'path': 'a.vsqx'})
    assert not quick_results.is_tuning_project({'available': False, 'role': 'tuned_project', 'path': 'a.svp'})

    ustx = outside / 'manual.ustx'
    ustx.write_text('voice_parts:\n  - notes:\n      - lyric: a\n      - lyric: b\n', encoding='utf-8')
    vsqx = outside / 'manual.vsqx'
    vsqx.write_text('<vsq4><note/><note/></vsq4>', encoding='utf-8')
    with patch.object(task_store, 'job_directories', return_value=[job_a, job_b, job_c]):
        first = quick_results.register_external(
            job_a, 'project', ustx, expected_binding_id='', expected_binding_revision='')
        after_ustx = quick_results.detail(job_a)
        project = after_ustx['bindings']['project']
        assert first == project['id'] and project['path'] == str(ustx.resolve())
        assert quick_results.is_tuning_project(
            next(row for row in after_ustx['artifacts'] if row['id'] == first))
        second = quick_results.register_external(
            job_a, 'project', vsqx, expected_binding_id=project['id'],
            expected_binding_revision=project['revision'])
        after_vsqx = quick_results.detail(job_a)
        assert second != first
        assert after_vsqx['bindings']['project']['path'] == str(vsqx.resolve())
        assert ustx.is_file() and vsqx.is_file()

qml = (APP / 'src/quick_ui/ResultsPage.qml').read_text(encoding='utf-8')
bridge = (APP / 'src/quick_bridge.py').read_text(encoding='utf-8')
subtitles = (APP / 'src/quick_subtitles.py').read_text(encoding='utf-8')
assert 'readonly property bool mutationIdle' in qml
assert 'taskBlockReason' in qml
assert '编辑器工程 (*.svp *.ustx *.vsqx)' in qml
assert 'inspectTaskProgress' in qml and 'reprocessSelectedTask' in qml
assert 'signal.connect(self.taskSafetyChanged)' not in bridge
assert 'signal.connect(self._schedule_task_safety)' in bridge
assert 'subtitle_drafts=drafts' in bridge
assert 'self._subtitles._drafts' in bridge
assert 'self._hold_tuning_handoff_lock(job)' in bridge
assert 'quick_results.is_tuning_project(row)' in bridge
assert '_DRAFT_FIELDS' in subtitles and 'def restoreSource' in subtitles

print('PASS: task-scoped blockers, cached edge notifications, dormant drafts and SVP/USTX/VPR handoff guard')
