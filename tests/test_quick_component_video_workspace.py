"""Exercise both workspace layouts with pointer input and a real FFmpeg round trip."""
from contextlib import ExitStack
import json
from pathlib import Path
import os
import subprocess
import tempfile
from time import monotonic, sleep
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

import component_operations as components
from common import ROOT, atomic_json, file_hash, load_config
from quick_app import create_workspace
import task_contract


assert os.environ.get('VWA_TESTING') == '1', 'Use the isolated test runner'
width = int(os.environ.get('VWA_QA_LAYOUT_WIDTH', 1440))
viewport, scale = {1440: ((1440, 900), 100), 1920: ((1920, 1080), 125)}[width]
evidence = ROOT / 'work/test-results'
evidence.mkdir(parents=True, exist_ok=True)
warnings = []
geometry = {}


def qt_message(kind, _context, message):
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        warnings.append(message)


app = QGuiApplication([])
qInstallMessageHandler(qt_message)
engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
video = owner.video


def wait(label, ready, timeout=22):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        app.processEvents()
        if ready():
            return
        QTest.qWait(18)
    raise AssertionError((label, owner.route, owner.notice, video.view['message'], warnings))


def visual(name, *, required=True):
    pending = [window.contentItem()]
    while pending:
        node = pending.pop()
        if node.objectName() == name and node.isVisible():
            return node
        pending.extend(reversed(node.childItems()))
    if required:
        raise AssertionError(name)


def assert_on_screen(node, x, y):
    point = node.mapToScene(QPointF(x, y))
    assert 0 <= point.x() <= window.width() and 0 <= point.y() <= window.height(), (node.objectName(), point)
    ancestor = node.parentItem()
    while ancestor is not None:
        if ancestor.clip():
            local = ancestor.mapFromScene(point)
            assert 0 <= local.x() <= ancestor.width() and 0 <= local.y() <= ancestor.height(), (
                'clipped control', node.objectName(), ancestor.objectName(), local)
        ancestor = ancestor.parentItem()
    return point


def tap(name):
    QTest.qWait(60)
    node = visual(name)
    assert node.isEnabled() and node.width() > 0 and node.height() > 0, name
    point = assert_on_screen(node, node.width() / 2, node.height() / 2)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
    app.processEvents()


def capture(name):
    if os.environ.get('VWA_QA_LAYOUT_CAPTURE') == '1':
        QTest.mouseMove(window, QPointF(8, 8).toPoint())
        QTest.qWait(120)
        image = window.grabWindow()
        assert not image.isNull() and (image.width(), image.height()) == viewport
        assert image.save(str(evidence / f'{name}-{width}.png'))


def file_url(path):
    return QUrl.fromLocalFile(str(path))


def component_flow(mixdown):
    owner.chooseMusic(file_url(mixdown))
    owner.setPreparationField('lyrics', '夜色与远方')
    owner.setPreparationField('voice_mode', 'dual')
    owner.setPreparationField('separator_model', 'roformer')
    owner.setPreparationField('recognize_lyrics', False)
    before = owner.state.draft
    # Control the installed-model response without depending on GB of models.
    # The rest of the catalog, volume probe and UI still run through the worker.
    builtin = components._row('builtin', 'ready', detail='内置分离的工具、模型和关键运行文件已通过文件检查。',
                              can_use=True, model='dual', installed=384 * 1024**2, required=384 * 1024**2)
    with patch.object(components, '_builtin_row', return_value=builtin), patch.object(
            components.optional_components, 'status', return_value={'ready': False, 'state': 'missing'}):
        owner.navigate('components')
        wait('loaded catalog and actual plan', lambda: not owner.loading and owner.componentTaskPlan['ready'])
        plan = owner.componentTaskPlan
        assert plan['builtinReady'] and plan['missingIds'] == ['roformer'], plan
        assert 'expression' not in plan['components']
        assert 'BS-RoFormer' in visual('componentTaskPlan').property('text')
        tap('component_roformer')
        wait('RoFormer install plan', lambda: owner.selectedId == 'roformer' and
             visual('componentPlan', required=False) is not None)
        volume = owner.selected['health']['volume']
        assert Path(volume['path']) == components.optional_components.COMPONENT
        assert type(volume['free_bytes']) is int and volume['free_bytes'] > 0, volume
        catalog = visual('componentList')
        assert 205 <= catalog.width() / (scale / 100) <= 310, catalog.width()
        geometry['componentCatalogPx'] = round(catalog.width())
        assert_on_screen(visual('componentPlan'), 8, 8)
        assert_on_screen(visual('installComponent'), 8, 8)
        capture('components-install-plan')
        tap('returnToPreparation')
        wait('return to untouched draft', lambda: owner.route == 'processing')
        assert owner.state.draft == before
        owner.navigate('components')
        tap('useBuiltinComponent')
        wait('explicit built-in fallback returns to preparation', lambda: owner.route == 'processing')
        draft = owner.state.draft_view
        assert draft['separator_model'] == 'dual' and draft['roformer_device'] == 'auto'
        assert owner.state.draft.music_path == str(mixdown) and draft['lyrics'] == '夜色与远方'

        def cancellable_download(_component_id, *, progress, check, **_options):
            progress(1, 3, '夹具下载中')
            deadline = monotonic() + 8
            while not check() and monotonic() < deadline:
                sleep(.02)
            assert check(), 'The UI did not request cancellation'
            raise InterruptedError('夹具下载已取消')

        owner.navigate('components')
        tap('component_roformer')
        with patch.object(components, 'install', side_effect=cancellable_download):
            tap('installComponent')
            wait('installer progress appears', lambda: owner.operation.get('done') == 1)
            tap('componentPlanCancel')
            wait('cancel returns to an idle catalog', lambda: not owner.workBusy and not owner.loading
                 and owner.operation['state'] == 'cancelled')
    print(f'PASS {width}: required model, volume estimate, optional components, draft-preserving fallback and cancel')


def generate_inputs(folder, job):
    reference = folder / '参考音频.wav'
    mixdown = job / '混音-当前.wav'
    rate, duration = 16000, 12
    times = np.arange(rate * duration) / rate
    knots = np.arange(0., duration + .1001, .1)
    levels = np.interp(times, knots, np.random.default_rng(20).uniform(.02, .35, len(knots)))
    signal = (np.sin(2 * np.pi * 317 * times) * levels).astype('float32')
    sf.write(reference, signal, rate)
    sf.write(mixdown, signal[rate:] * .75, rate)
    source = job / 'video/原视频-夜色.mp4'
    source.parent.mkdir()
    silent = folder / '无声原片.mp4'
    ffmpeg = load_config()['ffmpeg']
    for output, inputs in ((source, ['-i', str(reference), '-c:a', 'aac']), (silent, [])):
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                        '-i', 'testsrc2=s=480x270:r=12:d=12', *inputs,
                        '-c:v', 'libx264', '-preset', 'ultrafast', str(output)],
                       timeout=24, check=True, capture_output=True)
    return source, mixdown, silent


def video_flow(job, source, mixdown, silent):
    owner.navigate('video_mixdown')
    video.chooseFile('sourceVideo', file_url(silent))
    tap('videoGeneratePreview')
    wait('silent original has a decoded frame', lambda: video.view['previewTime'] == 0 and not video.view['busy'])
    assert QUrl(video.view['preview']).isLocalFile()
    assert not video.view['mixdownAvailable'] and not video.view['canRender']

    atomic_json(job / 'request.json', {'source_kind': 'local', 'source_title': '夜色'})
    atomic_json(job / 'status.json', {'state': 'done'})
    atomic_json(job / 'task.json', {'name': '练习 · 夜色'})
    task_contract.bind(job, 'mixdown', mixdown, dependencies=[])
    task_contract.register(job, [{'path': source, 'role': 'source_video'}], 'download', parents=())
    owner.navigate('results')
    wait('verified task handoff ready', lambda: owner.taskPrimaryAction['kind'] == 'video_mixdown')
    tap('taskPrimaryAction')
    wait('verified inputs and fresh frame state', lambda: owner.route == 'video_mixdown'
         and video.view['mixdown'] == str(mixdown) and not video.view['busy'])
    assert video.view['sourceVideo'] == str(source) and video.view['previewTime'] is None
    tap('videoFooterPrimary')  # Analyze, from the fixed footer.
    wait('actual audio correlation', lambda: video.view['canAdoptSuggestion'])
    assert abs(float(video._alignment['offset_seconds']) - 1) <= .02, video._alignment
    tap('videoAdoptSuggestion')
    wait('explicit signed offset accepted', lambda: not video.view['autoAlign'] and not video.view['busy'])
    assert abs(video.view['offset'] - 1) <= .02
    tap('videoOffset')
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    for key in (Qt.Key.Key_0, Qt.Key.Key_Period, Qt.Key.Key_7, Qt.Key.Key_5):
        QTest.keyClick(window, key)
    QTest.keyClick(window, Qt.Key.Key_Return)
    wait('keyboard edit updates the adopted value without changing analysis',
         lambda: video.view['offset'] == .75 and abs(video.view['alignmentOffset'] - 1) <= .02)
    tap('videoGeneratePreview')
    wait('static frame from task source', lambda: video.view['previewTime'] == 0 and not video.view['busy'])
    frame = visual('videoPreview')
    wait('image displayed by Quick', lambda: frame.property('paintedHeight') > 50)
    assert frame.height() >= 290 * scale / 100, (frame.width(), frame.height())
    assert_on_screen(frame, 3, 3)
    assert_on_screen(frame, frame.width() - 3, frame.height() - 3)
    for name in ('videoAdoptedOffset', 'videoOffset', 'videoFooterPrimary', 'videoOutputOptionsToggle'):
        control = visual(name)
        assert_on_screen(control, 6, control.height() / 2)
    geometry.update({'previewPx': [round(frame.width()), round(frame.height())],
                     'paintedFramePx': [round(frame.property('paintedWidth')), round(frame.property('paintedHeight'))]})
    capture('video-frame-alignment')
    tap('videoFooterPrimary')  # Render.
    wait('actual output verified and ready to register', lambda: video.view['canRegister'], timeout=45)
    result = Path(video.view['result'])
    assert result.is_file() and result.stat().st_size > 1000
    assert file_hash(result) == video.view['resultSha256']
    assert video.view['receipt']['alignment']['offset_seconds'] == video.view['offset']
    tap('videoFooterPrimary')  # Register.
    wait('same output is eligible to hand off to publication', lambda: video.view['canPublish'])
    assert visual('videoResultState').property('text') == owner.text('已登记')
    capture('video-registered')
    tap('videoFooterPrimary')  # Local transfer to the publication workspace.
    wait('publication handoff', lambda: owner.route == 'publishing')
    owner.navigate('video_mixdown')
    video.setOption('offset', .5)
    assert video.view['resultStale'] and result.is_file()
    assert not video.view['canPublish'] and not video.view['canRegister']
    print(f'PASS {width}: silent-frame support, verified handoff, measured alignment, render, register, publish transfer and stale guard')


(ROOT / 'jobs').mkdir(exist_ok=True)
with ExitStack() as fixtures:
    job = Path(fixtures.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'jobs', prefix='layout-video-')))
    folder = Path(fixtures.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='layout-video-inputs-')))
    try:
        source, mixdown, silent = generate_inputs(folder, job)
        component_flow(mixdown)
        video_flow(job, source, mixdown, silent)
        assert not warnings, warnings
        (evidence / f'component-video-geometry-{width}.json').write_text(
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
