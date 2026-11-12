"""Two production Quick viewports: preparation, mix, delivery and settings."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from time import monotonic, sleep
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, file_hash, load_config
from quick_app import create_workspace
import quick_bridge
import task_contract

assert os.environ.get('VWA_TESTING') == '1', 'Use an isolated copy'
# Exercise the real render/transport worker without playing sound on the user's device.
os.environ['VWA_REALTIME_SILENT_PROBE'] = '1'
width = int(os.environ.get('VWA_QA_LAYOUT_WIDTH', 1440))
viewport, scale = {1440: ((1440, 900), 100), 1920: ((1920, 1080), 125)}[width]
evidence = ROOT / 'work/test-results'
evidence.mkdir(parents=True, exist_ok=True)
warnings, geometry = [], {}


def qt_message(kind, _context, message):
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        warnings.append(message)


app = QGuiApplication([])
qInstallMessageHandler(qt_message)
engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
mix, pub = owner.mixdown, owner.publication


def wait(label, condition, timeout=20):
    end = monotonic() + timeout
    while monotonic() < end:
        app.processEvents()
        if condition():
            return
        QTest.qWait(18)
    raise AssertionError((label, owner.route, owner.notice, mix.view.get('message'), pub.view.get('message'), warnings))


def visual(name, required=True):
    nodes = [window.contentItem()]
    while nodes:
        item = nodes.pop()
        if item.isVisible() and item.objectName() == name:
            return item
        nodes.extend(reversed(item.childItems()))
    if required:
        raise AssertionError(name)


def point(name, fraction=.5):
    QTest.qWait(50)
    item = visual(name)
    assert item.isEnabled() and item.width() > 0 and item.height() > 0, name
    pos = item.mapToScene(QPointF(item.width() * fraction, item.height() / 2))
    assert 0 <= pos.x() < window.width() and 0 <= pos.y() < window.height(), (name, pos)
    ancestor = item.parentItem()
    while ancestor is not None:
        if ancestor.clip():
            local = ancestor.mapFromScene(pos)
            assert 0 <= local.x() <= ancestor.width() and 0 <= local.y() <= ancestor.height(), (
                'clipped control', name, ancestor.objectName(), local)
        ancestor = ancestor.parentItem()
    return pos.toPoint()


def tap(name, fraction=.5):
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(name, fraction))
    app.processEvents()


def escape():
    QTest.keyClick(window, Qt.Key.Key_Escape)
    app.processEvents()


def capture(name):
    if os.environ.get('VWA_QA_LAYOUT_CAPTURE') == '1':
        QTest.mouseMove(window, QPointF(8, 8).toPoint())
        QTest.qWait(95)
        image = window.grabWindow()
        assert not image.isNull() and (image.width(), image.height()) == viewport
        assert image.save(str(evidence / f'{name}-{width}.png'))


def url(path):
    return QUrl.fromLocalFile(str(path))


def inputs(folder):
    rate, seconds = 44100, 10
    times = np.arange(rate * seconds, dtype=np.float32) / rate
    envelope = .6 + .4 * np.sin(2 * np.pi * 4.3 * times)
    audio = {}
    for name, frequency, amplitude in [('主唱', 440, .55), ('和声一', 550, .19), ('和声二', 660, .15), ('伴奏', 220, .15)]:
        audio[name] = folder / (name + ' · 带中文的音轨.wav')
        sf.write(audio[name], amplitude * envelope * (np.sin(2 * np.pi * frequency * times) +
                                                       .22 * np.sin(2 * np.pi * 1600 * times)), rate)
    return audio


def preparation_flow(audio):
    assert owner.route == 'processing'
    assert not visual('startPreparation').isEnabled()
    assert '本地音频' in visual('preparationFooterStatus').property('text')
    owner.chooseMusic(url(audio['主唱']))
    owner.setPreparationField('lyrics', '窗边微光\n照向远方')
    owner.setPreparationField('backing_lyrics', '向远方')
    owner.setPreparationField('alignment_engine', 'hfa')
    owner.setPreparationField('voice_mode', 'single')
    assert visual('backingLyricsInput', required=False) is None
    assert owner.state.draft_view['backing_lyrics'] == '向远方'
    owner.setPreparationField('voice_mode', 'dual')
    assert visual('backingLyricsInput').property('text') == '向远方'
    capture('preparation-draft')
    assert 300 <= visual('preparationPlanInspector').width() * 100 / scale <= 360
    geometry['preparationFooterTop'] = round(visual('preparationFooter').mapToScene(QPointF(0, 0)).y())
    tap('checkPreparationComponents')
    wait('visible preflight belongs to the draft',
         lambda: owner.preparationSources.view['componentCheck']['state'] == 'passed')
    escape()
    owner.setPreparationField('separator_model', 'roformer')
    assert owner.preparationSources.view['componentCheck']['state'] == 'unchecked'
    tap('checkPreparationComponents')
    wait('actual optional model check reports unavailable',
         lambda: owner.preparationSources.view['componentCheck']['state'] == 'issues')
    escape()
    assert not visual('startPreparation').isEnabled()
    assert 'BS-RoFormer' in visual('preparationComponentStatus').property('text')
    capture('preparation-blocked')
    owner.setPreparationField('separator_model', 'dual')
    assert owner.preparationSources.view['componentCheck']['state'] == 'unchecked'

    def controlled_preparation(draft, _existing, _cpu, stop, send, **_kw):
        assert draft.music_path == str(audio['主唱'])
        send({'completed': 1, 'total': 3, 'current': 'separation', 'message': '夹具 · 处理音频'})
        assert stop.wait(9), 'Footer cancellation never reached the running worker'
        raise InterruptedError('夹具任务已取消')

    with patch.object(quick_bridge, '_run_preparation', side_effect=controlled_preparation):
        tap('startPreparation')
        wait('live progress from the worker', lambda: owner.preparationTask.get('completed') == 1)
        assert point('preparationProgress').y() > geometry['preparationFooterTop']
        tap('cancelPreparation')
        wait('cancelled', lambda: owner.preparationTask['state'] == 'cancelled' and not owner.preparationBusy)
    assert owner.state.draft_view['lyrics'] == '窗边微光\n照向远方'
    print(f'PASS {width}: fixed preparation, real component check, draft invalidation, and worker cancellation')


def mix_flow(folder, audio, job):
    owner.navigate('audio_mixdown')
    mix.addTrack()
    mix.addTrack()
    vocal_ids = [row for row in mix.trackIds if row.startswith('vocal-')]
    assert len(vocal_ids) == 3
    for key, name in zip([*vocal_ids, 'instrumental'], audio):
        mix.chooseSource(key, url(audio[name]))
    mix.setTrack(vocal_ids[0], 'pan', -.24)
    mix.setTrack(vocal_ids[0], 'offset', .125)
    mix.setOption('compressionEnabled', True)
    mix.setOption('compressionStrength', .82)
    mix.setOption('reverbEnabled', True)
    mix.setOption('reverbMix', .2)
    wait('actual realtime engine and three vocals', lambda: mix.view['canPlay'])
    readout = 'mixdownTrackReadout_' + vocal_ids[0]
    assert 'L24' in visual(readout).property('text') and '+125ms' in visual(readout).property('text')
    tap(readout)
    dialog = window.findChild(QObject, 'mixdownTrackSettings')
    assert dialog is not None and dialog.property('opened')
    escape()
    fader_tops = [visual('mixdownGain_' + key).mapToScene(QPointF(0, 0)).y()
                  for key in [*vocal_ids, 'instrumental']]
    assert max(fader_tops) - min(fader_tops) < 2, fader_tops
    tap('mixdownPlayPause')
    wait('real DSP packets without audible output', lambda: mix.position > 100
         and mix._player.snapshot.get('silent') is True
         and len(mix.spectrum.get('freqs', [])) > 8
         and mix.meters.get('source') and any(row.get('rms') is not None for row in mix.meters['lanes']))
    tap('mixdownBefore')
    wait('actual A/B controller selects dry', lambda: mix.view['selection'] == 'A' and mix.view['playing'])
    tap('mixdownAfter')
    wait('processed view is active', lambda: mix.view['selection'] == 'B' and mix.spectrum.get('source'))
    tap('mixdownPlayPause')
    wait('paused before scrub', lambda: not mix.view['playing'])
    start_point, end_point = point('mixdownProgressGrab', .25), point('mixdownProgressGrab', .39)
    button, keys = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mousePress(window, button, keys, start_point)
    QTest.mouseMove(window, end_point, 35)
    QTest.mouseRelease(window, button, keys, end_point)
    wait('seek confirmed without losing pause',
         lambda: not mix._player.seek_pending and abs(mix.position - .39 * mix.view['duration']) < 310
         and not mix.view['playing'])
    position = mix.position
    tap('mixdownPlayPause')
    wait('resume from the scrubbed position', lambda: mix.view['playing'] and mix.position > position + 95)
    tap('mixdownExpand')
    assert mix.view['expanded']
    start = mix.position
    owner.navigate('updates')
    wait('playback and telemetry persist off page', lambda: mix.position > start + 125 and mix.meters.get('source'))
    owner.navigate('audio_mixdown')
    wait('expanded state returns and meters remain available',
         lambda: mix.view['expanded'] and mix.spectrum.get('source') and visual('mixdownProgressGrab', False))
    tap('mixdownExpand')
    capture('mixdown-live')
    geometry['mixSeekWidth'] = round(visual('mixdownProgressGrab').width())
    tap('mixdownPlayPause')
    wait('paused', lambda: not mix.view['playing'])
    output = folder / '独立混音 · 本轮.wav'
    mix.chooseOutput(url(output))
    tap('mixdownExport')
    wait('actual WAV exported and valid for independent video', lambda: mix.view.get('videoHandoffMode') == 'independent',
         timeout=35)
    assert output.is_file() and sf.info(output).frames > 100
    assert not mix.view['canRegister'] and visual('mixdownRegister', False) is None
    capture('mixdown-independent')
    digest = file_hash(output)
    mix.setTrack(vocal_ids[0], 'gain', -1.5)
    assert mix.view['resultStale'] and file_hash(output) == digest
    assert visual('mixdownComposeVideo', False) is None
    assert visual('mixdownExport').property('text') in ('选择新路径', '选择路径')
    mix.chooseOutput(url(folder / '独立混音 · 新版本.wav'))
    tap('mixdownExport')
    wait('fresh replacement is eligible', lambda: not mix.view['resultStale'] and mix.view['canComposeVideo'],
         timeout=35)
    tap('mixdownComposeVideo')
    wait('independent WAV handoff to video', lambda: owner.route == 'video_mixdown')
    assert owner.video.view['mixdown'].endswith('新版本.wav') and not owner.video.view['job']

    atomic_json(job / 'request.json', {'source_kind': 'local', 'source_title': '练习 · 窗边微光', 'voice_mode': 'single'})
    atomic_json(job / 'status.json', {'state': 'done'})
    atomic_json(job / 'task.json', {'name': '练习 · 窗边微光'})
    (job / 'audio').mkdir()
    for name, source in [('vocals', '主唱'), ('instrumental', '伴奏')]:
        shutil.copyfile(audio[source], job / 'audio' / (name + '.wav'))
    task_contract.register(job, [{'path': path, 'role': 'audio'} for path in (job / 'audio').glob('*.wav')],
                           'separation', parents=())
    owner.navigate('results')
    wait('verified split WAVs offer the task mix path',
         lambda: owner.taskPrimaryAction.get('kind') == 'audio_mixdown')
    tap('taskPrimaryAction')
    wait('actual task mix is ready', lambda: owner.route == 'audio_mixdown'
         and mix.view['job'] == str(job) and mix.view['canExport'])
    output = job / '任务混音.wav'
    mix.chooseOutput(url(output))
    wait('task preview finishes initializing before export',
         lambda: not mix.view['busy'] and visual('mixdownExport').isEnabled())
    tap('mixdownExport')
    wait('task mix awaits explicit registration', lambda: mix.view['canRegister'], timeout=35)
    assert visual('mixdownComposeVideo', False) is None
    tap('mixdownRegister')
    wait('registered result changes the one primary action', lambda: mix.view['videoHandoffMode'] == 'registered')
    assert visual('mixdownComposeVideo').isEnabled() and visual('mixdownRegister', False) is None
    capture('mixdown-registered')
    print(f'PASS {width}: 3 vocals, A/B, seek, expand, resident DSP, real export/staleness, standalone and task registration')
    return output


def publication_flow(job, mixed):
    owner.navigate('publishing')
    wait('empty publishing workspace', lambda: visual('publicationUseTaskVideo', False) is not None)
    assert all(visual(name, False) is None for name in ('publicationTitle', 'publicationDescription', 'publicationAI'))
    assert visual('publicationBundleImport') and visual('publicationLocalOptions')
    capture('publication-entry')
    video = job / '合成成品.mp4'
    subprocess.run([load_config()['ffmpeg'], '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=12:d=2',
                    '-i', str(mixed), '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', str(video)], check=True, capture_output=True, timeout=25)
    mix_ref = next(row for row in task_contract.inspect(job)
                   if row['role'] == 'mixdown' and row['state'] == 'verified')
    task_contract.bind(job, 'video', video, dependencies=[{'id': mix_ref['id'], 'revision': mix_ref['revision']}])
    tap('publicationUseTaskVideo')
    wait('task has a verified video publishing action', lambda: owner.route == 'results'
         and owner.taskPrimaryAction.get('kind') == 'publishing')
    tap('taskPrimaryAction')
    wait('verified video unlocks the editors', lambda: owner.route == 'publishing' and not pub.view['busy']
         and visual('publicationTitle', False) is not None)
    stepper = visual('publicationStepper')
    strip = visual('publicationReviewStrip')
    assert strip.mapToScene(QPointF()).y() >= stepper.mapToScene(QPointF(0, stepper.height())).y() - 1
    assert all(visual('publicationStep_' + str(index)).width() >= 150 for index in range(3))
    tap('publicationReview_package')
    assert pub.view['step'] == 0 and '标题' in pub.view['message']
    for platform, title in [('bilibili', 'B站独立文稿'), ('xiaohongshu', '小红书独立文稿')]:
        pub.editPlatform(platform)
        assert pub.setField('title', title)
        assert pub.setField('description', title + ' · 本家与 STAFF')
        assert pub.setField('ai', False)
        assert pub.setField('rights', '核对原创、署名与授权')
    tap('publicationReview_copy')
    assert pub.view['step'] == 0 and pub.view['title'] == '小红书独立文稿'
    tap('publicationReview_cover')
    wait('review item navigates through validated steps', lambda: pub.view['step'] == 1)
    assert pub.setField('coverMode', 'frame') and pub.setField('coverSeconds', .35)
    assert pub.previewCover()
    wait('actual cover extraction', lambda: not pub.view['busy'] and pub.view['review']['coverState'] == 'previewed')
    capture('publication-cover-review')
    tap('publicationReview_package')
    tap('publicationPrepare')
    wait('actual local two-platform bundle', lambda: pub.view['verified'] and not pub.view['busy'], timeout=50)
    manifest = json.loads((Path(pub.view['bundle']) / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['content']['bilibili']['title'] == 'B站独立文稿'
    assert manifest['content']['xiaohongshu']['title'] == '小红书独立文稿'
    assert manifest['final_publish'] == 'human_only'
    assert '本地已核验' in visual('publicationReview_package').property('text')
    tap('publicationReview_copy')
    assert pub.setField('title', '小红书文稿已改')
    assert pub.view['bundleStale'] and not pub.view['verified']
    assert '已过期' in visual('publicationReview_package').property('text')
    capture('publication-stale')
    print(f'PASS {width}: distinct publication entries, real frame, two drafts and local bundle staleness')


def settings_flow():
    owner.navigate('updates')
    for category in ('appearance', 'update', 'storage', 'presets', 'support', 'about'):
        tap('settingsCategory_' + category)
    tap('settingsCategory_storage')
    first = visual('downloadSection').mapToScene(QPointF(0, 0))
    second = visual('storageSection').mapToScene(QPointF(0, 0))
    assert (second.x() > first.x() + 200 and abs(first.y() - second.y()) < 4) if width == 1920 \
        else (abs(first.x() - second.x()) < 4 and second.y() > first.y() + 100), (first, second)
    geometry['storageSideBySide'] = width == 1920
    capture('settings-storage')
    tap('settingsCategory_support')
    assert visual('openLogGuide') and visual('supportSidebarOpenFeedback') and visual('supportSidebarReplayTutorial')
    assert visual('openFeedback', False) is None and visual('replayTutorial', False) is None
    assert '490619616' in visual('supportSidebarQQGroup').property('text')
    assert abs(visual('settingsContentFlickable').property('contentY')) < 3
    tap('showDiagnostics')
    wait('diagnostic dialog', lambda: visual('closeDiagnostics', False) is not None)
    tap('closeDiagnostics')
    tap('settingsCategory_appearance')
    tap('settingsThemeLight')
    tap('settingsThemeDark')
    tap('settingsCategory_support')
    capture('settings-support')
    print(f'PASS {width}: six categories, actual storage geometry, unique help, diagnostic and theme')


(ROOT / 'jobs').mkdir(exist_ok=True)
with ExitStack() as stack:
    job = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'jobs', prefix='layout-delivery-')))
    folder = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='layout-p2-')))
    try:
        audio = inputs(folder)
        preparation_flow(audio)
        mixed = mix_flow(folder, audio, job)
        publication_flow(job, mixed)
        settings_flow()
        assert not warnings, warnings
        (evidence / f'workflow-layout-geometry-{width}.json').write_text(
            json.dumps(geometry, indent=2) + '\n', encoding='utf-8')
    except Exception:
        capture('failure-' + owner.route)
        raise
    finally:
        modes.close()
        owner.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qInstallMessageHandler(None)
