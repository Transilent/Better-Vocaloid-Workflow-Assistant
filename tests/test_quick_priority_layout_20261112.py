
"""Exercise the real Quick task-to-video and lyric-to-track editing flows."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from time import monotonic, sleep
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, load_config
from quick_app import create_workspace
import task_contract


assert os.environ.get('VWA_TESTING') == '1', 'Use the isolated Quick test runner'
width = int(os.environ.get('VWA_QA_LAYOUT_WIDTH', 1440))
viewport, scale = {1440: ((1440, 900), 100), 1920: ((1920, 1080), 125)}[width]
evidence = ROOT / 'work/test-results'
evidence.mkdir(parents=True, exist_ok=True)
warnings = []


def qt_message(kind, _context, message):
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        warnings.append(message)


app = QGuiApplication([])
qInstallMessageHandler(qt_message)
engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)


def wait(description, ready, timeout=12):
    end = monotonic() + timeout
    while monotonic() < end:
        app.processEvents()
        if ready():
            return
        sleep(.01)
    raise AssertionError((description, owner.route, owner.notice, owner.tuning.view.get('message'), warnings))


def items():
    pending = [window.contentItem()]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.childItems()))


def visual(name, *, required=True):
    obj = next((node for node in items() if node.objectName() == name and node.isVisible()), None)
    if required:
        assert obj is not None, name
    return obj


def tap(obj, x=None, y=None):
    assert obj is not None and obj.isVisible() and obj.isEnabled(), (
        obj.objectName() if obj else '', owner.route, owner.notice)
    QTest.qWait(45)  # Let Quick lay out its new delegates before calculating a pointer position.
    point = obj.mapToScene(QPointF(obj.width() / 2 if x is None else x, obj.height() / 2 if y is None else y))
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (obj.objectName(), point)
    ancestor = obj.parentItem()
    while ancestor is not None:
        if ancestor.clip():
            local = ancestor.mapFromScene(point)
            assert 0 <= local.x() < ancestor.width() and 0 <= local.y() < ancestor.height(), (
                'clipped target', obj.objectName(), ancestor.objectName(), local)
        ancestor = ancestor.parentItem()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
    app.processEvents()


def drag(obj, x0, y, x1):
    QTest.qWait(45)
    start = obj.mapToScene(QPointF(x0, y)).toPoint()
    end = obj.mapToScene(QPointF(x1, y)).toPoint()
    button, keys = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mousePress(window, button, keys, start)
    QTest.mouseMove(window, end, 40)
    QTest.mouseRelease(window, button, keys, end)
    app.processEvents()


def capture(name):
    if os.environ.get('VWA_QA_LAYOUT_CAPTURE') == '1':
        QTest.mouseMove(window, QPointF(8., 8.).toPoint())
        app.processEvents()
        image = window.grabWindow()
        assert not image.isNull() and (image.width(), image.height()) == viewport
        assert image.save(str(evidence / f'{name}-{width}.png'))


def task_row(job):
    return next((node for node in items()
                 if node.isVisible() and node.width() > 0 and node.height() > 0
                 and node.property('jobId') == str(job)), None)


def file_url(path):
    return QUrl.fromLocalFile(str(path))


def write_wave(path, level):
    with wave.open(str(path), 'wb') as track:
        track.setnchannels(1)
        track.setsampwidth(2)
        track.setframerate(24000)
        track.writeframes((struct.pack('<4h', 0, level, 0, -level)) * 6000)


def prepare_job(job, state, title):
    atomic_json(job / 'request.json', {'source_kind': 'local', 'source_title': title})
    atomic_json(job / 'status.json', {'state': state, 'message': '合成夹具：分离阶段失败' if state == 'failed' else ''})
    atomic_json(job / 'task.json', {'name': '同名歌曲'})


def task_flow(job, failed):
    owner.navigate('results')
    wait('empty task library renders its next action', lambda: not owner.taskLoading and
         visual('emptyResultsGoPrepare', required=False) is not None)
    tap(visual('emptyResultsGoPrepare'))
    assert owner.route == 'processing'
    prepare_job(job, 'done', '主版本')
    prepare_job(failed, 'failed', '另一次处理')
    previous, current = (job / name for name in ('混音-旧版.wav', '混音-当前.wav'))
    write_wave(previous, 2000)
    write_wave(current, 4000)
    old_id = task_contract.bind(job, 'mixdown', previous, dependencies=[])
    current_id = task_contract.bind(job, 'mixdown', current, dependencies=[])
    source = job / 'video/source.mp4'
    source.parent.mkdir()
    subprocess.run([load_config()['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'testsrc2=s=160x96:r=12:d=1', '-c:v', 'libx264',
                    '-preset', 'ultrafast', str(source)], timeout=22, check=True, capture_output=True)
    source_id, = task_contract.register(job, [{'path': source, 'role': 'source_video'}], 'download', parents=())

    owner.navigate('results')
    wait('both same-name task rows',
         lambda: owner.taskModel.rowCount() == 2 and not owner.taskLoading and task_row(failed) and task_row(job))
    tap(task_row(failed))
    wait('selected failed task and its own action',
         lambda: owner.taskDetail.get('jobId') == str(failed) and owner.taskPrimaryAction['kind'] == 'retry')
    assert owner.selectedTaskArtifactId == ''
    tap(task_row(job))
    wait('selected other task', lambda: owner.taskDetail.get('jobId') == str(job) and not owner.taskLoading)
    assert owner.selectedTaskArtifactId == current_id
    action = owner.taskPrimaryAction
    assert action['kind'] == 'video_mixdown' and action['artifactIds'] == [current_id, source_id]
    assert visual('taskListPanel').width() < visual('taskDetailPanel').width()
    assert visual('taskArtifactInspector').width() > 240
    capture('results-current')
    tap(visual('taskArtifact_' + old_id), x=48)
    assert owner.selectedTaskArtifactId == old_id
    assert owner.selectedTaskArtifact['state'] == 'historical' and not owner.selectedTaskArtifact['openable']
    assert owner.taskPrimaryAction == action
    capture('results-history')
    tap(visual('taskPrimaryAction'))
    wait('checked WAV and source reach actual video controller',
         lambda: owner.route == 'video_mixdown' and owner.video.view['mixdown'] == str(current))
    assert owner.video.view['sourceVideo'] == str(source) and owner.video.view['canRender']
    print(f'PASS {width}: same-name tasks, historical selection, and the current WAV + source video handoff')

    owner.navigate('results')
    wait('task detail reload', lambda: owner.taskDetail.get('jobId') == str(job) and not owner.taskLoading)
    assert owner.selectedTaskArtifactId == old_id
    # Deliberately change a private fixture after inspection. The handoff must
    # recompute the identity before it can replace the destination's input.
    write_wave(current, 6000)
    tap(visual('taskPrimaryAction'))
    wait('revision check detects the edited WAV',
         lambda: not owner.workBusy and owner.taskPrimaryAction['kind'] == '' and
         any(row['id'] == current_id and row['state'] == 'modified' for row in owner.taskDetail['artifacts']))
    assert owner.route == 'results' and visual('taskPrimaryAction', required=False) is None
    assert owner.selectedTaskArtifactId == old_id
    print(f'PASS {width}: stale file does not transfer; the inspected historical row remains selected')


def write_svp(path):
    q = 705600000
    lyrics = tuple('沿着微光吹向远方微光回来')
    tracks = []
    for index, name in enumerate(('主唱 · 逐词', '和声 · 上声部', '和声 · 下声部')):
        notes = [{'onset': round((n * .92 + index * .12) * q), 'duration': round(.71 * q),
                  'pitch': 60 + index * 3 + (0, 2, 4, 7)[n % 4], 'lyrics': ch}
                 for n, ch in enumerate(lyrics)]
        tracks.append({
            'name': name, 'mainRef': {'blickOffset': 0, 'isInstrumental': False,
                                      'database': {'name': 'Synthetic'}},
            'mainGroup': {'uuid': f'group-layout-{index}', 'parameters': {}, 'notes': notes},
        })
    doc = {'version': '1.0.0', 'uuid': 'quick-priority-layout-synthetic-v1',
           'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
                    'tempo': [{'position': 0, 'bpm': 120}]},
           'tracks': tracks}
    path.write_bytes(json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\0')


def tuning_flow(folder):
    path = folder / '三轨逐词夹具.svp'
    write_svp(path)
    owner.navigate('tuning')
    tune = owner.tuning
    tune.chooseProject(file_url(path))
    wait('SVP loaded', lambda: tune.project is not None and not tune.view['busy'])
    tracks = tuple(tune.project.editable)
    assert len(tracks) == 3 and tune.view['mode'] == 'single'
    tune.setQuery('微光')
    assert len(tune.matches) == 6 and tune.view['lyricMatchCount'] == 2
    tap(visual('tuningNextLyricOccurrence'))
    wait('first lyric occurrence', lambda: tune.view['lyricMatchIndex'] >= 0)
    first = tune.view
    assert first['lyricMatchIndex'] == 0 and first['activeTrack'] == tracks[0], first
    assert visual('tuningCurrentLyricContext').property('text').find('微光') >= 0
    tap(visual('tuningNextLyricOccurrence'))
    assert tune.view['lyricMatchIndex'] == 1 and tune.view['activeTrack'] == tracks[0], tune.view
    tap(visual('tuningPreviousLyricOccurrence'))
    assert tune.view['lyricMatchIndex'] == 0 and tune.view['start'] == first['start']
    assert visual('tuningTrackLane_' + tracks[1], required=False) is None
    tap(visual('tuningStyle_gentle'))
    tap(visual('addTuningRegion'))
    assert len(tune.regions) == 1
    capture('tuning-single')
    saved = tune.regions[0]

    tap(visual('tuningModeMulti'))
    wait('all three editable track lanes on one timeline', lambda: all(
        visual('tuningTrackLane_' + track, required=False) is not None for track in tracks))
    chosen = list(tune.view['selectedTracks'])
    before = (tune.view['start'], tune.view['end'], tune.view['viewportStart'], tune.view['viewportSpan'])
    tap(visual('tuningTrackLane_' + tracks[1]), x=20)
    assert tune.view['activeTrack'] == tracks[1] and tune.view['selectedTracks'] == chosen
    assert before == (tune.view['start'], tune.view['end'], tune.view['viewportStart'], tune.view['viewportSpan'])
    # Target only the upper harmony, retaining the same selected phrase.
    for track in tracks:
        if (track in tune.view['selectedTracks']) != (track == tracks[1]):
            tap(visual('tuningTrackTarget_' + track))
    tap(visual('tuningStyle_sad'))
    tap(visual('addTuningRegion'))
    regions = {row['trackId']: row for row in tune.regions}
    assert set(regions) == set(tracks[:2]) and regions[tracks[0]] == saved
    assert regions[tracks[0]]['style'] != regions[tracks[1]]['style']
    capture('tuning-multi')
    tap(visual('undoTuningRegions'))
    assert tune.regions == [saved]
    tap(visual('redoTuningRegions'))
    assert len(tune.regions) == 2
    tap(visual('tuningTrackTarget_' + tracks[2]))
    tap(visual('tuningStyle_bold'))
    roll = visual('tuningTimelineCanvas')
    x = roll.property('plotLeft')
    w = roll.property('plotWidth')
    y = roll.property('plotTop') + roll.property('plotHeight') * .7
    drag(roll, x + w * .72, y, x + w * .89)
    wait('one drag applies the brush to both selected harmonies', lambda: len(tune.regions) == 4)
    assert {region['trackId'] for region in tune.regions if region['style'] == 'bold'} == set(tracks[1:])
    assert saved in tune.regions
    tap(visual('tuningModeSingle'))
    wait('single-track projection', lambda: tune.view['mode'] == 'single')
    assert tune.view['activeTrack'] == tracks[1]
    assert visual('tuningTrackLane_' + tracks[1], required=False) is None
    assert len(tune.regions) == 4 and tune.view['snapEnabled'] and tune.view['autoApplySelection']
    QTest.qWait(65)
    for row in tune.regions:
        shown = visual('tuningStyleBand_' + row['id'], required=False)
        assert (shown is not None) == (row['trackId'] == tracks[1]), row
    # Resizing the visible range by its right handle and panning by the middle
    # should have no effect on the lyric selection or stored regions.
    before_selection = tune.view['start'], tune.view['end']
    before_regions = list(tune.regions)
    before_lyric = tune.view['lyricContext']
    assert before_lyric.get('lyric'), before_lyric
    if tune.view['viewportSpan'] < tune.view['duration'] - .001:
        tap(visual('timelineFit'))
    assert abs(tune.view['viewportSpan'] - tune.view['duration']) < .001
    assert visual('timelineFit').property('text') == '回当前词'
    tap(visual('timelineFit'))
    assert tune.view['viewportSpan'] < tune.view['duration'] - .001
    assert tune.view['viewportStart'] <= before_lyric['start']
    assert tune.view['viewportStart'] + tune.view['viewportSpan'] >= before_lyric['end']
    assert before_selection == (tune.view['start'], tune.view['end']) and before_regions == tune.regions
    tap(visual('timelineFit'))
    assert abs(tune.view['viewportSpan'] - tune.view['duration']) < .001
    before_span = tune.view['viewportSpan']
    before_left = tune.view['viewportStart']
    strip = visual('tuningViewportInput')
    thumb = visual('tuningViewportThumb')
    grip = strip.mapFromScene(thumb.mapToScene(QPointF(thumb.width() - 14 * scale / 100, thumb.height() / 2)))
    drag(strip, grip.x(), grip.y(), strip.width() * .67)
    wait('right grip zooms', lambda: tune.view['viewportSpan'] < before_span * .85)
    assert tune.view['viewportStart'] == before_left
    old_end = tune.view['viewportStart'] + tune.view['viewportSpan']
    left_grip = strip.mapFromScene(thumb.mapToScene(QPointF(14 * scale / 100, thumb.height() / 2)))
    drag(strip, left_grip.x(), left_grip.y(), strip.width() * .1)
    assert abs(tune.view['viewportStart'] + tune.view['viewportSpan'] - old_end) < .001
    left = tune.view['viewportStart']
    span = tune.view['viewportSpan']
    center = strip.mapFromScene(thumb.mapToScene(QPointF(thumb.width() / 2, thumb.height() / 2)))
    drag(strip, center.x(), center.y(), center.x() + strip.width() * .11)
    wait('thumb pans', lambda: tune.view['viewportStart'] > left + .01)
    assert abs(tune.view['viewportSpan'] - span) < .001
    assert '–' in visual('tuningViewportRange').property('text')
    capture('tuning-viewport-controls')
    assert before_selection == (tune.view['start'], tune.view['end']) and before_regions == tune.regions
    assert tune.view['lyricContext'] == before_lyric
    before_return = (tune.view['start'], tune.view['end'], tune.view['viewportStart'], tune.view['viewportSpan'])
    owner.navigate('processing')
    owner.navigate('tuning')
    wait('same editing state after the page is rebuilt', lambda:
         visual('tuningCurrentLyricContext', required=False) is not None)
    assert tune.view['activeTrack'] == tracks[1] and tune.view['selectedTracks'] == list(tracks[1:])
    assert (tune.view['start'], tune.view['end'], tune.view['viewportStart'], tune.view['viewportSpan']) == before_return
    assert len(tune.regions) == 4
    print(f'PASS {width}: repeat lyrics, independent styles, batched drag, undo/redo, zoom/pan and preserved view')


(ROOT / 'jobs').mkdir(exist_ok=True)
with ExitStack() as fixtures:
    job, failed = [Path(fixtures.enter_context(tempfile.TemporaryDirectory(
        dir=ROOT / 'jobs', prefix=prefix))).resolve() for prefix in ('layout-current-', 'layout-failed-')]
    folder = Path(fixtures.enter_context(tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='layout-score-')))
    try:
        tuning_flow(folder)
        task_flow(job, failed)
        assert not [msg for msg in warnings if 'ResultsPage.qml' in msg or 'Tuning' in msg], warnings
    except Exception:
        capture('failure-' + owner.route)
        raise
    finally:
        modes.close()
        owner.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qInstallMessageHandler(None)
