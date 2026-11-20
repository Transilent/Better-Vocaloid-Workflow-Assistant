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
                {'position': 0, 'duration': 480, 'tone': 60, 'lyric': '你', 'tuning': 0,
                 'vibrato': {'length': 0, 'period': 175, 'depth': 25, 'in': 10, 'out': 10}},
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
    try:
        engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=scale)
    except RuntimeError as error:
        raise AssertionError(warnings) from error
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
    assert visual('tuningNoteTool').isEnabled() and visual('tuningNoteTool').property('selected')
    assert tune.view['noteTool'] and not tune.view['styleTool']
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
    QTest.keyClick(window, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    wait('Shift+Tab selects the previous lyric',
         lambda: visual('tuningInlineLyric') and visual('tuningInlineLyric').property('text') == '春')
    QTest.keyClick(window, Qt.Key.Key_Tab)
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
    assert len(tune.regions) == 1 and tune.regions[0]['end'] == resized['end']

    # The inspector edit goes through the same score transaction as a drag.
    assert visual('tuningInspector_note').property('selected')
    proof = ROOT / 'work/test-results'
    assert window.grabWindow().save(str(proof / f'note-properties-{scale}.png'))
    for key, value in (('pitch', 63), ('start', .180), ('end', .815)):
        name = 'tuningNoteField_' + key
        field = visual(name)
        tap(name)
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        event = QInputMethodEvent()
        event.setCommitString(f'{value:.3f}' if key != 'pitch' else str(value))
        assert app.sendEvent(field, event)
        QTest.keyClick(window, Qt.Key.Key_Return)
        changed = tune.project.notes[identity]
        assert abs(changed[key] - value) < .002, (key, changed, tune.view['message'])
        QTest.mouseClick(window, left, pos=note_point(changed))
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert tune.project.notes[identity] == resized and tune.view['selectedNote']['pitch'] == 61

    # Marquee and Ctrl-click select notes without applying the active style.
    control = Qt.KeyboardModifier.ControlModifier
    tap('tuningNoteDetail_fine_tune')
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert tune.project.note_patches[identity]['fine_tune'] == 1
    assert window.grabWindow().save(str(proof / f'native-note-properties-{scale}.png'))
    QTest.mouseClick(window, left, pos=note_point(resized))
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert 'fine_tune' not in tune.project.note_patches[identity]

    corner = note_point(resized, 0) - QPointF(12, 1.8 * roll.property('pitchRowHeight')).toPoint()
    far = note_point(second, 1) + QPointF(8, 1.8 * roll.property('pitchRowHeight')).toPoint()
    drag(corner, far.x() - corner.x(), far.y() - corner.y())
    selected = {identity, second['id']}
    assert set(tune.view['selectedNoteIds']) == selected and len(tune.regions) == 1
    assert visual('tuningNoteField_pitch') is None
    assert window.grabWindow().save(str(proof / f'group-selected-{scale}.png'))
    QTest.mouseClick(window, left, control, pos=note_point(second))
    assert tune.view['selectedNoteIds'] == [identity]
    QTest.mouseClick(window, left, Qt.KeyboardModifier.ShiftModifier, pos=note_point(second))
    assert set(tune.view['selectedNoteIds']) == selected
    drag(note_point(resized), .92 * scale_x, 0)
    assert tune.project.notes[identity] == resized and tune.project.notes[second['id']] == second
    assert '重叠' in tune.view['message']
    drag(note_point(resized), .07 * scale_x, -roll.property('pitchRowHeight'))
    for note in (resized, second):
        after = tune.project.notes[note['id']]
        assert .058 < after['start'] - note['start'] < .082 and after['pitch'] == note['pitch'] + 1
    assert set(tune.view['selectedNoteIds']) == selected and len(tune.regions) == 1
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.notes[identity] == resized and tune.project.notes[second['id']] == second
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert tune.project.notes[identity]['pitch'] == 62 and tune.project.notes[second['id']]['pitch'] == 63
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.notes[identity] == resized and tune.project.notes[second['id']] == second
    drag(note_point(resized, .99), .06 * scale_x, 0)
    for note in (resized, second):
        after = tune.project.notes[note['id']]
        assert .048 < after['end'] - note['end'] < .072 and after['start'] == note['start']
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.notes[identity] == resized and tune.project.notes[second['id']] == second
    QTest.mouseClick(window, left, pos=corner)
    assert not tune.view['selectedNoteIds']

    # Move over a note in the explicit style tool, then adjust its saved region.
    tap('tuningStyleTool')
    assert tune.view['styleTool'] and not tune.view['noteTool'] and tune.view['autoApplySelection']
    drag(note_point(second, .05), .20 * scale_x, 0)
    assert len(tune.regions) == 2 and tune.project.notes[second['id']] == second
    second_region = next(row for row in tune.regions if row['id'] != region_id)
    assert second_region['start'] == second['start'] and second_region['end'] == second['end']
    tap('tuningStyleBand_' + second_region['id'])
    assert tune.view['selectedRegion'] == second_region['id']
    tap('tuningStyleDetailToggle')
    wait('detail slider laid out', lambda: visual('tuningStyleDetail_onset')
         and visual('tuningStyleDetail_onset').width() >= 65 * scale / 100)
    detail = visual('tuningStyleDetail_onset')
    assert detail.isEnabled()
    handle = detail.property('handle')
    start = pointer(handle, handle.width() / 2, handle.height() / 2)
    span = detail.property('availableWidth') - handle.width()
    initial = tune.view['styleBrush']['detail']['onset']

    def assert_detail_uncommitted():
        assert tune.view['styleBrush']['detail']['onset'] == initial
        assert 18 <= detail.property('value') <= 42, (scale, detail.width(), detail.property('value'), warnings)

    drag(start, -.72 * span, 0, assert_detail_uncommitted)
    amount = tune.view['styleBrush']['detail']['onset']
    assert .18 < amount < .42 and tune.view['styleDetail']['onset'] == initial, (amount, warnings)
    assert window.grabWindow().save(str(proof / f'style-region-properties-{scale}.png'))
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(tune.regions) == 2 and tune.view['styleBrush']['detail']['onset'] == initial
    tap('tuningNewRegionBrush')
    assert not tune.view['selectedRegion'] and tune.view['styleBrush']['detail']['onset'] == initial
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(tune.regions) == 1 and tune.regions[0]['id'] == region_id
    tap('tuningNoteTool')

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
    print('PASS', viewport, scale, 'note marquee, grouped move/transpose, inspector, scope, draft and verified export')

    tune.setViewport(0., 2.6)
    tap('tuningParameterToggle')
    wait('open parameter lane', lambda: visual('tuningParameterGraph') is not None)
    lane = visual('tuningParameterLane')
    graph = visual('tuningParameterGraph')
    origin = graph.mapToScene(QPointF(lane.property('leftInset'), 0)).x()
    roll_origin = roll.mapToScene(QPointF(roll.property('plotLeft'), 0)).x()
    assert abs(origin - roll_origin) < 1, (origin, roll_origin, scale)
    assert lane.property('rightInset') - lane.property('leftInset') == roll.property('plotWidth')

    tune.setBeatDivision(4)
    wait('tempo grid', lambda: visual('tuningBeatDivision').property('currentIndex') == 1)
    beats = [mark[0] for mark in tune.beatMarks if mark[1]]
    assert len(beats) >= 5 and beats[:4] == [0., .5, 1., 1.5] and abs(beats[4] - 13 / 6) < 1e-6, beats
    tune.setBeatDivision(8)
    assert abs(tune.snapBeat(1.81) - 11 / 6) < 1e-6

    tune.freeze()
    wait('real style layer', lambda: tune.view['canRun'] and bool(tune.parameterPlot.get('style')))
    assert any(row['noteId'] == identity and any(pair and pair[1] for pair in row['points'])
               for row in tune.parameterPlot['style'])
    assert not tune.parameterPlot['original'] and tune.project.pitch_strokes == strokes
    proof = ROOT / 'work/test-results'
    assert window.grabWindow().save(str(proof / f'parameter-lane-{scale}.png'))
    tap('tuningParameterStyle')
    assert not lane.property('showStyle')
    tap('tuningParameterStyle')

    def plot_x(left, width, seconds):
        return left + width * (seconds - roll.property('visibleStart')) / roll.property('visibleSpan')

    def parameter_point(seconds, cents):
        left_x = lane.property('leftInset')
        x = plot_x(left_x, lane.property('rightInset') - left_x, seconds)
        pad, extent = 10 * scale / 100, lane.property('extent')
        return pointer(graph, x, pad + (extent - cents) / (2 * extent) * (graph.height() - 2 * pad))

    def parameter_drag(a, b):
        QTest.mousePress(window, left, pos=a)
        QTest.mouseMove(window, b, 25)
        assert tune.project.pitch_strokes == strokes
        QTest.mouseRelease(window, left, pos=b)
        app.processEvents()

    # The freshly opened lane is ready to draw. A click selects a note, and
    # an overshooting drag clips to that note's guarded end in one transaction.
    assert visual('tuningParameterLine').property('selected')
    QTest.mouseClick(window, left, pos=parameter_point(1.05, 30))
    assert tune.project.pitch_strokes == strokes and tune.view['selectedNote']['id'] == second['id']
    style_preview = tune.parameterPlot['style']
    parameter_drag(parameter_point(1.05, 30), parameter_point(1.60, 50))
    clipped = next(row for row in tune.pitchCurves if row['noteId'] == second['id'])
    assert .015 < second['end'] - clipped['offsets'][-1][0] < .024, clipped
    assert not tune.view['canRun'] and tune.parameterPlot['style'] == style_preview
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.pitch_strokes == strokes
    parameter_drag(parameter_point(1.05, 30), parameter_point(1.32, 80))
    assert tune.view['pitchStrokeCount'] == 2 and tune.parameterPlot['style'] == style_preview
    fresh = next(row for row in tune.pitchCurves if row['noteId'] == second['id'])
    assert len(fresh['offsets']) == 2
    for (x, y), (seconds, cents) in zip(fresh['offsets'], ((1.05, 30), (1.32, 80))):
        assert abs(x - seconds) < .008 and abs(y - cents) <= 10, fresh

    tap('tuningParameterNodes')
    QTest.mouseClick(window, left, pos=parameter_point(1.18, -135))
    assert next(row for row in tune.pitchCurves if row['noteId'] == second['id']) == fresh
    a, b = fresh['offsets']
    middle_time = 1.18
    middle_cents = a[1] + (b[1] - a[1]) * (middle_time - a[0]) / (b[0] - a[0])
    mid = parameter_point(middle_time, middle_cents)
    QTest.mouseClick(window, left, pos=mid)
    wait('insert pitch node', lambda: any(row['noteId'] == second['id'] and len(row['offsets']) == 3
                                         for row in tune.pitchCurves))
    inserted = next(row['offsets'][1] for row in tune.pitchCurves if row['noteId'] == second['id'])
    assert abs(inserted[1] - middle_cents) <= 2, inserted
    mid = parameter_point(*inserted)
    dest = parameter_point(1.21, -50)
    drag(mid, dest.x() - mid.x(), dest.y() - mid.y())
    node = next(row['offsets'][1] for row in tune.pitchCurves if row['noteId'] == second['id'])
    assert abs(node[0] - 1.21) < .008 and node[1] == -50, node
    assert window.grabWindow().save(str(proof / f'pitch-nodes-{scale}.png'))
    QTest.mouseClick(window, Qt.MouseButton.RightButton, pos=parameter_point(*node))
    wait('node context action', lambda: visual('tuningDeletePitchNode') is not None)
    tap('tuningDeletePitchNode')
    assert next(row for row in tune.pitchCurves if row['noteId'] == second['id']) == fresh
    for _ in range(4):
        QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.pitch_strokes == strokes and tune.regions[0]['id'] == region_id

    tap('tuningParameterFreehand')
    QTest.mousePress(window, left, pos=parameter_point(1.04, 10))
    QTest.mouseMove(window, parameter_point(1.16, 90), 20)
    QTest.mouseMove(window, parameter_point(1.24, -60), 20)
    QTest.mouseRelease(window, left, pos=parameter_point(1.32, 20))
    freehand = next(row for row in tune.pitchCurves if row['noteId'] == second['id'])
    assert len(freehand['offsets']) >= 3
    tap('tuningParameterManual')
    assert visual('tuningParameterInspect').property('selected')
    QTest.mouseClick(window, left, pos=parameter_point(1.16, 90))
    assert next(row for row in tune.pitchCurves if row['noteId'] == second['id']) == freehand
    tap('tuningParameterErase')
    QTest.mouseClick(window, left, pos=parameter_point(1.16, 90))
    assert tune.project.pitch_strokes == strokes

    tap('tuningParameterFreehand')
    QTest.mousePress(window, left, pos=parameter_point(resized['end'] - .15, -10))
    QTest.mouseMove(window, parameter_point(resized['end'] - .06, 70), 20)
    QTest.mouseMove(window, parameter_point(1.05, -30), 20)
    QTest.mouseRelease(window, left, pos=parameter_point(1.30, 50))
    assert tune.project.pitch_strokes != strokes
    assert {row['noteId'] for row in tune.pitchCurves} == {identity, second['id']}
    QTest.keyClick(window, Qt.Key.Key_Z, control)
    assert tune.project.pitch_strokes == strokes
    tune.setBeatDivision(0)

    tap('tuningPitchDrawTool')

    def range_point(seconds):
        return pointer(roll, plot_x(roll.property('plotLeft'), roll.property('plotWidth'), seconds),
                       roll.property('plotTop') + roll.property('plotHeight') / 2)

    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(window, left, shift, pos=range_point(.02))
    QTest.mouseMove(window, range_point(1.44), 30)
    gesture = roll.property('pressTime'), roll.property('dragTime'), roll.property('selectOnly')
    QTest.mouseRelease(window, left, shift, pos=range_point(1.44))
    assert tune.project.pitch_strokes == strokes and tune.regions[0]['id'] == region_id
    left_time, right_time = roll.property('selectionStart'), roll.property('selectionEnd')
    assert left_time <= resized['start'] and right_time >= second['end'], (left_time, right_time, gesture, warnings)
    tap('tuningLyricBatch')
    wait('paste dialog', lambda: visual('tuningLyricBatchText') is not None)
    paste = visual('tuningLyricBatchText')
    paste.setProperty('text', 'きゃー春山')
    tap('tuningLyricBatchPreview')
    assert tune.lyricBatch['extra'] == ['山'] and not visual('tuningLyricBatchApply').isEnabled()
    paste.setProperty('text', 'きゃー')
    tap('tuningLyricBatchPreview')
    assert tune.lyricBatch['blocked'] == 1 and not visual('tuningLyricBatchApply').isEnabled()
    wait('editable lyric slots', lambda: visual('tuningLyricBatchSlot_1') is not None)
    tap('tuningLyricBatchSlot_1')
    event = QInputMethodEvent()
    event.setCommitString('春')
    assert app.sendEvent(visual('tuningLyricBatchSlot_1'), event)
    wait('lyrics entered in second slot', lambda: visual('tuningLyricBatchApply').isEnabled())
    assert [row['after'] for row in tune.lyricBatch['rows']] == ['きゃー', '春']
    before_peek = tune.view['start'], tune.view['end']
    tap('tuningLyricBatchLocate_1')
    wait('peek at the matching note in the roll', lambda: visual('tuningLyricBatchText') is None
         and roll.property('focusedNoteId') == second['id'])
    resume_button = visual('tuningLyricBatch')
    assert resume_button.property('text') == '继续贴词'
    assert (tune.view['start'], tune.view['end']) == before_peek and tune.lyricBatch['canApply']
    tap('tuningLyricBatch')
    wait('existing review reopened', lambda: visual('tuningLyricBatchApply') and visual('tuningLyricBatchApply').isEnabled())
    paste = visual('tuningLyricBatchText')
    assert paste.property('text') == 'きゃー' and [row['after'] for row in tune.lyricBatch['rows']] == ['きゃー', '春']
    harmony = next(key for key in tune.project.tracks if key != track)
    tune.focusTrack(harmony)
    assert not tune.lyricBatch['canApply'] and not tune.applyLyricBatch()
    tune.focusTrack(track)
    assert not visual('tuningLyricBatchApply').isEnabled()
    paste.setProperty('text', 'きゃー春')
    tap('tuningLyricBatchPreview')
    assert window.grabWindow().save(str(proof / f'lyric-review-{scale}.png'))
    tap('tuningLyricBatchApply')
    wait('applied both lyrics', lambda: tune.project.notes[identity]['lyric'] == 'きゃー'
         and tune.project.notes[second['id']]['lyric'] == '春' and visual('tuningLyricBatchText') is None)
    assert tune.project.visible([harmony], 0., 10.)[0]['lyric'] == '和'
    if scale == 100:
        tune.freeze()
        wait('freeze reviewed batch', lambda: tune.view['canRun'])
        tune.run()
        wait('verified batch candidate', lambda: tune.view['candidateVerified'] and not tune.view['candidateStale']
             and not tune.view['busy'])
        output = read_project(Path(tune.view['candidate']))
        assert [note['lyric'] for note in output['voice_parts'][0]['notes']] == ['きゃー', '春', '呀']
        assert output['voice_parts'][1] == original['voice_parts'][1]
        batch_copy = directory / 'phrase.ustx'
        tune.exportCandidate(QUrl.fromLocalFile(str(batch_copy)))
        wait('export reviewed phrase', lambda: tune.view['savedCopy'] == str(batch_copy))
        assert read_project(batch_copy) == output
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [tune.project.notes[i]['lyric'] for i in (identity, second['id'])] == ['山', '好']
    tap('tuningParameterToggle')
    assert visual('tuningParameterGraph') is None and file_hash(source) == original_sha
    tap('tuningNoteTool')
    tune.setViewport(0., tune.view['duration'])
    tail = tune.project.visible([track], 1.9, 3.)[-1]
    QTest.mouseClick(window, left, pos=note_point(tune.project.notes[identity]))
    QTest.mouseClick(window, left, control, pos=note_point(tail))
    scope = tune.view['start'], tune.view['end']
    review = tune.previewLyricBatch('雨雪')
    assert [row['id'] for row in review['rows']] == [identity, tail['id']] and review['canApply']
    QTest.mouseClick(window, left, control, pos=note_point(tune.project.notes[second['id']]))
    assert scope == (tune.view['start'], tune.view['end']) and not tune.lyricBatch['canApply']
    tune.dismissLyricBatch()
    print('PASS', viewport, scale, 'line clipping, node editing, erase, exact lyric scope and resumable review')

    sparse_source = directory / 'long-song.ustx'
    sparse = score('Sparse phrases')
    sparse['voice_parts'][0].update(duration=70000, notes=[
        {'position': 0, 'duration': 480, 'tone': 45, 'lyric': '光'},
        {'position': 24000, 'duration': 480, 'tone': 88, 'lyric': '唱'},
        {'position': 60000, 'duration': 480, 'tone': 38, 'lyric': '光'},
    ])
    sparse_source.write_bytes(encode(sparse, b''))
    tune.chooseProject(QUrl.fromLocalFile(str(sparse_source)))
    wait('long score shown', lambda: tune.view['title'] == sparse_source.stem and not tune.view['busy'])
    low, high, last = tune.project.visible([tune.view['activeTrack']], 0., 100.)
    wait('first note rendered', lambda: any(box['note']['id'] == low['id']
         for box in roll.property('noteRects').toVariant()))
    QTest.mouseClick(window, left, pos=note_point(low))
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert tune.view['selectedNote']['id'] == high['id']
    QTest.keyClick(window, Qt.Key.Key_F2)
    wait('edit adjacent note outside the former viewport',
         lambda: visual('tuningInlineLyric') and visual('tuningInlineLyric').property('text') == '唱')
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.keyClick(window, Qt.Key.Key_F, control)
    search = visual('tuningLyricSearch')
    assert search and search.hasActiveFocus()
    event = QInputMethodEvent()
    event.setCommitString('光')
    assert app.sendEvent(search, event)
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert tune.view['lyricMatchIndex'] >= 0
    tap('tuningPreviousLyricOccurrence')
    wait('search jumps across time and pitch', lambda: roll.property('focusedNoteId') == low['id'])
    tap('tuningNextLyricOccurrence')
    wait('second lyric match brought into view', lambda: roll.property('focusedNoteId') == last['id'])
    before_view = tune.view['viewportStart']
    before_range = tune.view['start'], tune.view['end']
    tap('tuningParameterToggle')
    wait('parameter lane restored', lambda: visual('tuningParameterGraph') is not None)
    graph = visual('tuningParameterGraph')
    point = pointer(graph, graph.width() * .5, graph.height() * .5)
    destination = point + QPointF(130, 0).toPoint()
    middle = Qt.MouseButton.MiddleButton
    QTest.mousePress(window, middle, pos=point)
    QTest.mouseMove(window, destination, 30)
    QTest.mouseRelease(window, middle, pos=destination)
    assert tune.view['viewportStart'] < before_view - .5 and before_range == (tune.view['start'], tune.view['end'])
    assert tune.view['noteEditCount'] == tune.view['pitchStrokeCount'] == 0
    tap('tuningParameterToggle')
    print('PASS', viewport, scale, 'cross-viewport arrows, lyric search reveal, shared middle-button pan')

    owner.close()
    window.close()
    app.processEvents()


with tempfile.TemporaryDirectory(prefix='quick-tuning-score-', dir=native_path(ROOT / 'work')) as temp:
    for viewport, scale in [((1440, 900), 100), ((1920, 1080), 125)]:
        exercise(ROOT / 'work' / Path(temp).name, viewport, scale)
assert not warnings, json.dumps(warnings, ensure_ascii=False)
