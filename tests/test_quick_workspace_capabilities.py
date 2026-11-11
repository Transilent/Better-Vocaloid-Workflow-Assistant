"""Exercise restored preparation, task/video and settings capabilities offline."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication

import artifact_cache
from common import ROOT, atomic_json, file_hash, load_config, native_path
import lyrics_sources
import pipeline
import preparation_commands
from preset_library import PresetLibrary
from quick_app import create_workspace
import quick_support
import storage
from subtitles import Cue
import task_contract


assert os.environ.get('VWA_TESTING') == '1', 'Run only in an isolated test copy'
app = QGuiApplication([])
warnings = []
qInstallMessageHandler(lambda kind, _ctx, message: warnings.append(message)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)
engine, window, bridge, modes = create_workspace(app, viewport=(1440, 900), scale=100)
sources, video, support = bridge.preparationSources, bridge.video, bridge.support
jobs = ROOT / 'jobs'
jobs.mkdir(exist_ok=True)


def settle(seconds=.13):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.004)


def check(label, predicate):
    assert predicate, (label, bridge.notice, sources.view['message'], video.view['message'], warnings)
    print('PASS', label, flush=True)


def wait(label, predicate, timeout=16):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        settle(.04)
    check(label, predicate())


def item(name):
    stack = [window.contentItem()]
    while stack:
        node = stack.pop()
        if node.objectName() == name:
            return node
        stack.extend(node.childItems())
    node = window.findChild(QObject, name)
    assert node is not None, name
    return node


def click(name):
    target = item(name)
    assert target.property('visible') and target.property('enabled'), name
    assert QMetaObject.invokeMethod(target, 'click'), name
    settle()


def url(path):
    return QUrl.fromLocalFile(str(path))

@contextmanager
def private_directory(parent, prefix):
    parent = Path(parent).resolve()
    path = Path(tempfile.mkdtemp(dir=parent, prefix=prefix)).resolve()
    try:
        yield path
    finally:
        if path.parent == parent and path.name.startswith(prefix) and not path.is_symlink() and not path.is_junction():
            shutil.rmtree(native_path(path))


try:
    with (
        private_directory(jobs, 'capability-task-') as job,
        private_directory(ROOT / 'work', 'capability-files-') as private,
    ):
        bv = 'BV1Fixture01'
        request = {'source_kind': 'bilibili', 'bv': bv, 'url': f'https://www.bilibili.com/video/{bv}/',
                   'source_title': '合成原视频', 'voice_mode': 'single', 'tools': load_config(resolve_paths=False)}
        atomic_json(job / 'request.json', request)
        atomic_json(job / 'task.json', {'name': '合成原视频'})
        (job / 'video').mkdir()
        source = job / 'video/source.mp4'
        subprocess.run([load_config()['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=128x96:r=10:d=1', '-c:v', 'libx264', str(source)],
                       check=True, timeout=20, capture_output=True)
        atomic_json(job / 'source-info.json', {'kind': 'bilibili', 'bvid': bv, 'title': '合成原视频'})
        atomic_json(job / 'upstream-info.json', {})
        (job / '发布简介草稿.txt').write_text('fixture', encoding='utf-8')
        atomic_json(job / 'download-report.json', {'file': str(source), 'sha256': file_hash(source)})
        steps = {name: {'state': 'done', 'receipt': artifact_cache.receipt(
                 job, request, name, pipeline.requirements(name, request))} for name in ('metadata', 'download')}
        atomic_json(job / 'status.json', {'state': 'done', 'steps': steps, 'planned_steps': ['metadata', 'download']})
        mix = private / 'mix.wav'
        sf.write(mix, np.zeros(24000, dtype=np.float32), 24000)
        task_contract.register(job, [{'path': source, 'role': 'source_video'}], 'download', parents=())
        task_contract.bind(job, 'mixdown', mix, dependencies=[])

        bridge.navigate('processing')
        settle()
        song_rows = [
            {'id': '123', 'title': '测试曲', 'artists': '原唱', 'album': '原版', 'duration': 245},
            {'id': '456', 'title': '测试曲', 'artists': '翻唱', 'album': '现场', 'duration': 191},
        ]
        ref = lyrics_sources.LyricReference('春风', [Cue(0, .7, '春风')],
                {'provider': 'netease', 'song_id': '456', 'format': 'lrc'})
        click('browseNeteaseVersions')
        with patch.object(lyrics_sources, 'search', return_value=song_rows) as search:
            item('neteaseSearchQuery').setProperty('text', '测试曲')
            click('searchNeteaseVersions')
            wait('search exposes both distinct song versions', lambda: len(sources.view['songs']) == 2)
            search.assert_called_once_with('测试曲')
        with patch.object(lyrics_sources, 'fetch', return_value=ref) as fetch:
            sources.previewSong('456')
            wait('preview shows selected version without applying lyrics', lambda: sources.view['preview'] == '春风')
            check('lyric preview reached the actual Quick dialog',
                  item('neteaseLyricsPreview').property('text') == '春风' and not bridge.state.draft.view['lyrics'])
            click('applyNeteaseLyrics')
            fetch.assert_called_once_with('456')
        check('apply preserves lyric origin and draft version',
              bridge.state.draft.view['lyrics'] == '春风' and bridge.state.draft.lyric_reference['origin']['song_id'] == '456')
        bridge.setPreparationField('target', 'midi')
        bridge.setPreparationField('midi_backend', 'v1')
        bridge.setPreparationField('alignment_engine', 'tifa')
        click('checkPreparationComponents')
        wait('selected missing prerequisite is reported before task creation',
             lambda: not sources.view['busy'] and any('Vocal2Midi 2.0' in value for value in sources.view['issues']))
        QMetaObject.invokeMethod(item('preparationSourcesDialog'), 'close')
        bridge.setPreparationField('alignment_engine', 'hfa')
        bridge.setPreparationField('voice_mode', 'single')
        bridge.setPreparationField('target', 'separation')
        bridge.setPreparationField('input_mode', 'bilibili')
        bridge.setPreparationField('source', bv)
        bridge.setPreparationField('output_directory', str(private))
        settle()
        click('bilibiliDownloadOptions')
        wait('Bilibili settings load from local config', lambda: sources.view['networkLoaded'] and not sources.view['busy'])
        sources.saveDownloadSettings(False, 22, 2)
        wait('Bilibili settings persist through the shared service',
             lambda: not sources.view['busy'] and sources.view['network']['timeout'] == 22)
        click('findReusableBilibiliDownload')
        wait('same-BV lookup reads real acquisition receipts',
             lambda: not sources.view['busy'] and any(Path(row['job']) == job for row in sources.view['downloads']))
        check('same-BV source can be explicitly selected', sources.selectDownload(str(job)))
        reused = preparation_commands.create_from_draft(bridge.state.draft, reuse_job=sources.reusable_job())
        check('new task receives byte-identical original video with no network',
              file_hash(reused / 'video/source.mp4') == file_hash(source)
              and json.loads((reused / 'status.json').read_text(encoding='utf-8'))['steps']['download']['state'] == 'done')
        QMetaObject.invokeMethod(item('preparationSourcesDialog'), 'close')

        bridge.navigate('video_mixdown')
        settle()
        item('videoSourceIdentity').setProperty('text', bv)
        click('videoFindTask')
        wait('video page finds the exact BV task without network',
             lambda: not bridge.workBusy and any(Path(row['jobId']) == job for row in video.view['taskChoices']))
        video.restoreTask(str(job), 'BV1Other0001')
        wait('wrong BV cannot replace current inputs', lambda: not bridge.workBusy and video.view['state'] == 'failed')
        video.restoreTask(str(job), bv)
        wait('restore revalidates original video and bound Mixdown',
             lambda: not bridge.workBusy and video.view['sourceVideo'] == str(source))
        check('video pins both artifacts and displays exact BV',
              video.view['sourceBv'] == bv and video.view['mixdown'] == str(mix) and video.view['canRender'])
        changed_info = {'kind': 'bilibili', 'bvid': 'BV1Other0001', 'title': 'changed'}
        atomic_json(job / 'source-info.json', changed_info)
        video.analyze()
        wait('changed task source cannot silently render', lambda: not bridge.workBusy and video.view['state'] == 'failed')

        bridge.navigate('updates')
        settle()
        cache = private / 'cache/numba'
        cache.mkdir(parents=True)
        (cache / 'small.tmp').write_bytes(b'known disposable fixture')
        protected = private / 'cache/accounts/session-fixture.json'
        atomic_json(protected, {'safe': True})
        with patch.object(storage, 'ROOT', private), patch.object(quick_support, 'ROOT', private):
            click('scanStorage')
            wait('storage scan finds real disposable bytes',
                 lambda: not support.storage['busy'] and any(Path(row['path']) == cache for row in support.storage['rows']))
            support.cleanStorage([str(cache), str(protected)])
            wait('cleanup rejects anything outside the verified candidates',
                 lambda: not support.storage['busy'] and bool(support.storage['error']))
            check('rejected multi-item cleanup preserves all files', cache.is_dir() and protected.is_file())
            support.cleanStorage([str(cache)])
            wait('cleanup removes only the requested cache', lambda: not support.storage['busy'] and not cache.exists())
            check('protected account file remains untouched', protected.is_file() and support.storage['freed'] > 0)

        support._preset_library = PresetLibrary(private / 'presets')
        click('refreshPresetLibrary')
        wait('settings loads the shared built-in library', lambda: support.presetLibrary['loaded']
             and any(row['builtin'] for row in support.presetLibrary['entries']))
        original = next(entry for entry in support.presetLibrary['entries'] if entry['builtin'])
        support.copyPreset(original['id'], original['version'], '测试副本')
        wait('copy creates a local entry without modifying built-in', lambda: not support.presetLibrary['busy']
             and any(not entry['builtin'] for entry in support.presetLibrary['entries']))
        local = next(entry for entry in support.presetLibrary['entries'] if not entry['builtin'])
        exported = private / 'preset-copy.json'
        support.exportPreset(local['id'], local['version'], url(exported))
        wait('settings export writes verified local preset', lambda: not support.presetLibrary['busy'] and exported.is_file())
        new_preset = private / 'preset-import.json'
        raw = json.loads(exported.read_text(encoding='utf-8'))
        raw['id'] = 'fixture.capability-import'
        atomic_json(new_preset, raw)
        check('import preview accepts external document', support.previewPresetImport(url(new_preset)))
        support.importPreset()
        wait('default append keeps existing local preset',
             lambda: not support.presetLibrary['busy'] and sum(not row['builtin'] for row in support.presetLibrary['entries']) == 2)
        raw['metadata']['name'] = '同版本冲突测试'
        atomic_json(new_preset, raw)
        support.previewPresetImport(url(new_preset))
        support.importPreset()
        wait('different same-version content triggers conflict without overwriting', lambda: support.presetLibrary['conflict'])
        check('default append leaves two existing entries untouched',
              sum(not row['builtin'] for row in support.presetLibrary['entries']) == 2)
        from preset_builtin_documents import example_documents
        from preset_documents import encode_document
        for example in example_documents():
            example_path = private / (example['type'] + '.json')
            example_path.write_bytes(encode_document(example))
            check('read external ' + example['type'], support.previewPresetImport(url(example_path)))
            support.importPreset()
            wait('append external ' + example['type'], lambda: not support.presetLibrary['busy']
                 and any(row['id'] == example['id'] for row in support.presetLibrary['entries']))
        check('Quick pages reported no runtime warnings', not warnings)
finally:
    modes.close()
    bridge.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qInstallMessageHandler(None)
