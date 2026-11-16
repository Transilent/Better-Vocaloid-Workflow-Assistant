"""Exercise score editing with real pointer, key and input-method events."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from time import monotonic

from PySide6.QtCore import QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtTest import QTest

from common import ROOT, file_hash, native_path
from quick_app import create_workspace
from tuning_workflow import read_project, verified_result
from ustx_tuning import encode


assert os.environ.get('VWA_TESTING') == '1'
app = QGuiApplication([])
app.setQuitOnLastWindowClosed(False)
warnings = []
qInstallMessageHandler(lambda kind, _, message: warnings.append(message)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)


def score(title):
    return {
        'name': title, 'ustx_version': '0.10', 'resolution': 480,
        'time_signatures': [{'bar_position': 0, 'beat_per_bar': 4, 'beat_unit': 4}],
        'tempos': [{'position': 0, 'bpm': 120}, {'position': 1440, 'bpm': 90}],
        'tracks': [{'track_name': 'Lead'}, {'track_name': 'Harmony'}],
        'voice_parts': [
            {'track_no': 0, 'position': 0, 'duration': 2880, 'notes': [
                {'position': 0, 'duration': 480, 'tone': 60, 'lyric': '你'},
                {'position': 960, 'duration': 360, 'tone': 62, 'lyric': '好'},
                {'position': 1920, 'duration': 480, 'tone': 64, 'lyric': '呀'}]},
            {'track_no': 1, 'position': 0, 'duration': 2880, 'notes': [
                {'position': 800, 'duration': 480, 'tone': 55, 'lyric': '和'}]},
        ],
        'wave_parts': [],
    }


def exercise(root, viewport, scale):
    directory = root / str(scale)
    directory.mkdir()
    source, saved = directory / 'source.ustx', directory / 'edited.ustx'
    original = score(f'Edit test {scale}')
    source.write_bytes(encode(original, b''))
    original_sha = file_hash(source)
    engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
    tune = owner.tuning
    left = Qt.MouseButton.LeftButton

    def visual(name):
        queue = [window.contentItem()]
        while queue:
            node = queue.pop()
            if node.objectName() == name and node.isVisible():
                return node
            queue.extend(node.childItems())
        return None

    def wait(label, until, timeout=20):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            app.processEvents()
            if until():
                return
            QTest.qWait(10)
        raise AssertionError((scale, label, tune.view.get('message'), warnings))

    def pointer(item, x, y):
        point = item.mapToScene(QPointF(x, y)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (scale, point)
        return point

    def tap(name):
        item = visual(name)
        assert item and item.isEnabled(), (name, tune.view.get('message'), warnings)
        QTest.mouseClick(window, left, pos=pointer(item, item.width() / 2, item.height() / 2))
        app.processEvents()

    def text(value):
        item = visual('tuningInlineLyric')
        assert item and item.hasActiveFocus(), (value, warnings)
        event = QInputMethodEvent()
        event.setCommitString(value)
        assert app.sendEvent(item, event)

    owner.navigate('tuning')
    tune.chooseProject(QUrl.fromLocalFile(str(source)))
    wait('load', lambda: tune.project is not None and visual('tuningNoteTool') and not tune.view['busy'])
    QTest.qWait(80)
    roll = visual('tuningTimelineCanvas')
    assert roll and (window.width(), window.height()) == viewport
    assert visual('tuningNoteTool').isEnabled()
    tune.setSessionLocation(QUrl.fromLocalFile(str(directory / 'sessions')))
    track = tune.view['activeTrack']
    first, second, *_ = tune.project.visible([track], 0., 10.)
    identity = first['id']

    def note_point(note, fraction=.45):
        start_x = roll.property('plotLeft') + roll.property('plotWidth') * (
            (note['start'] + (note['end'] - note['start']) * fraction - roll.property('visibleStart'))
            / roll.property('visibleSpan'))
        y = roll.property('plotTop') + (127 - note['pitch'] - roll.property('pitchOffset') + .5) * roll.property('pitchRowHeight')
        return pointer(roll, start_x, y)

    QTest.mouseDClick(window, left, pos=note_point(first))
    wait('double-click opens inline lyric', lambda: visual('tuningInlineLyric') is not None)
    text('春')
    QTest.keyClick(window, Qt.Key.Key_Tab)
    wait('Tab applies and selects next note', lambda: tune.project.notes[identity]['lyric'] == '春'
         and visual('tuningInlineLyric') is not None and visual('tuningInlineLyric').property('text') == '好')
    text('取消')
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert visual('tuningInlineLyric') is None and tune.project.notes[second['id']]['lyric'] == '好'

    tune.setQuery('春好')
    assert len(tune.matches) == 1
    tune.setQuery('')
    tune.selectSpan(.01, .4)
    tune.setStyle('gentle')
    tune.setStylePitch(False)
    tune.addRegion()
    region_id = tune.regions[0]['id']
    tap('tuningNoteTool')
    assert tune.view['noteTool'] and tune.view['noteEditCount'] == 1

    def drag(point, dx, dy, before_release=None):
        destination = point + QPointF(dx, dy).toPoint()
        QTest.mousePress(window, left, pos=point)
        QTest.mouseMove(window, destination, 30)
        if before_release:
            before_release()
        QTest.mouseRelease(window, left, pos=destination)
        app.processEvents()

    def assert_uncommitted():
        assert first['start'] == tune.project.notes[identity]['start']
        assert tune.project.notes[identity]['pitch'] == 60

    scale_x = roll.property('plotWidth') / roll.property('visibleSpan')
    drag(note_point(first), .12 * scale_x, -roll.property('pitchRowHeight'), assert_uncommitted)
    moved = tune.project.notes[identity]
    assert .10 < moved['start'] < .14 and moved['pitch'] == 61, (moved, tune.view['message'], warnings)
    assert tune.regions[0]['id'] == region_id and tune.regions[0]['start'] == moved['start']
    edge = note_point(moved, .99)
    drag(edge, .13 * scale_x, 0)
    resized = tune.project.notes[identity]
    assert resized['end'] > moved['end'] + .10 and resized['pitch'] == moved['pitch']
    assert tune.regions[0]['end'] == resized['end']

    drag(note_point(resized), .58 * scale_x, 0)
    assert tune.project.notes[identity] == resized and '重叠' in tune.view['message']
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tune.project.notes[identity] == moved and tune.regions[0]['end'] == moved['end']
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert tune.project.notes[identity] == resized and tune.regions[0]['end'] == resized['end']
    tap('tuningModeMulti')
    assert not tune.view['noteTool'] and not visual('tuningNoteTool').isEnabled()
    tap('tuningModeSingle')
    assert tune.project.notes[identity] == resized and tune.view['noteEditCount'] == 1

    tap('tuningPitchDrawTool')
    assert tune.view['pitchTool'] == 'draw' and not tune.view['noteTool']
    draw_a = note_point(resized, .24)
    draw_b = note_point(resized, .72) - QPointF(0, roll.property('pitchRowHeight') * .55).toPoint()
    QTest.mousePress(window, left, pos=draw_a)
    QTest.mouseMove(window, draw_b, 30)
    assert tune.view['pitchStrokeCount'] == 0 and visual('tuningHandPitchInk')
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.mouseRelease(window, left, pos=draw_b)
    assert tune.view['pitchStrokeCount'] == 0

    QTest.mousePress(window, left, pos=draw_a)
    QTest.mouseMove(window, draw_b, 30)
    QTest.mouseRelease(window, left, pos=draw_b)
    assert tune.view['pitchStrokeCount'] == 1 and visual('tuningHandPitchLegend')
    strokes = tune.project.pitch_strokes
    assert tune.pitchCurves[0]['noteId'] == identity
    assert tune.pitchCurves[0]['points'][0][1] < tune.pitchCurves[0]['points'][-1][1]

    tune.setStyle('bold')
    tune.setStylePitch(True)
    tune.selectSpan(resized['start'], resized['end'])
    tune.updateRegion(region_id)
    assert tune.regions[0]['style'] == 'bold' and tune.project.pitch_strokes == strokes
    tap('tuningPitchEraseTool')
    assert tune.view['pitchTool'] == 'erase'
    QTest.mouseClick(window, left, pos=note_point(resized, .50))
    assert tune.view['pitchStrokeCount'] == 0 and not tune.pitchCurves and len(tune.regions) == 1
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tune.project.pitch_strokes == strokes and tune.regions[0]['style'] == 'bold'

    before = tune.pitchCurves[0]['points'][0][0]
    tap('tuningNoteTool')
    drag(note_point(resized), .04 * scale_x, 0)
    resized = tune.project.notes[identity]
    assert tune.project.pitch_strokes == strokes
    assert tune.pitchCurves[0]['points'][0][0] > before + .02
    assert tune.regions[0]['start'] == resized['start'] and tune.regions[0]['end'] == resized['end']

    tune.freeze()
    wait('preview includes note, scoped style and manual pitch', lambda: tune.view['canRun']
         and '1 个音符' in tune.view['previewSummary'] and '手绘 1 段' in tune.view['previewSummary'])
    tune.run()
    wait('verified candidate', lambda: tune.view['candidateVerified'] and not tune.view['busy'])
    first_candidate = Path(tune.view['candidate'])
    tune.exportCandidate(QUrl.fromLocalFile(str(saved)))
    wait('same-format save', lambda: tune.view['savedCopy'] == str(saved))
    verified = verified_result(tune.session)
    payload = read_project(saved)
    expected = deepcopy(original['voice_parts'][0]['notes'][0])
    edit = verified['options']['note_edits'][0]
    expected.update(tone=61, lyric='春', position=edit['onset'], duration=edit['duration'])
    assert payload['voice_parts'][0]['notes'] == [expected, *original['voice_parts'][0]['notes'][1:]]
    assert payload['voice_parts'][1] == original['voice_parts'][1]
    assert verified['report']['note_changes'][0]['note_id'] == identity
    assert verified['report']['hand_pitch']['spans'] == 1
    assert {c['abbr'] for c in payload['voice_parts'][0]['curves']} == {'pitd', 'dyn'}
    assert file_hash(source) == original_sha and file_hash(saved) == verified['output_sha256']

    tune.resumeSession(QUrl.fromLocalFile(str(tune.session)))
    wait('resume restores edited notes and anchored style', lambda: not tune.view['busy'] and
         tune.view['noteEditCount'] == 1 and tune.project.notes[identity] == resized)
    assert tune.regions[0]['end'] == resized['end'] and not tune.view['candidateStale']
    assert tune.project.pitch_strokes == strokes and tune.pitchCurves[0]['noteId'] == identity
    QTest.qWait(70)
    QTest.mouseDClick(window, left, pos=note_point(resized))
    wait('edit reopened note', lambda: visual('tuningInlineLyric') is not None)
    text('山')
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert tune.project.notes[identity]['lyric'] == '山' and tune.view['candidateStale']
    assert file_hash(first_candidate) == verified['output_sha256']
    print('PASS', viewport, scale, 'note edits, style replacement, draw/erase and cancel, undo, draft, verified export')

    owner.close()
    window.close()
    app.processEvents()


with tempfile.TemporaryDirectory(prefix='quick-tuning-score-', dir=native_path(ROOT / 'work')) as temp:
    for viewport, scale in [((1440, 900), 100), ((1920, 1080), 125)]:
        exercise(ROOT / 'work' / Path(temp).name, viewport, scale)
assert not warnings, json.dumps(warnings, ensure_ascii=False)
