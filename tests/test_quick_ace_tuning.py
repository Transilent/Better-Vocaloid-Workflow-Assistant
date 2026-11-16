"""Exercise ACEP, UFData and VPR score/pitch editing and save controls in Qt Quick."""
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt, QUrl, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, file_hash, native_path
from operation_diagnostics import close_journal
from quick_app import create_workspace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ace_tuning import ace_score, editable_ace_score, editable_uf_score, uf_score, wrap_ace
from test_vpr_tuning import editable_score as editable_vpr_score
from test_vpr_tuning import project as vpr_score, write_source as write_vpr
import tuning_workflow as workflow


assert os.environ.get('VWA_TESTING') == '1'
wide = os.environ.get('VWA_TEST_LARGE_VIEWPORT') == '1'
viewport = (1920,1080) if wide else (1440,900)
app = QGuiApplication([])
warnings = []
qInstallMessageHandler(lambda kind, _context, message: warnings.append(message)
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)
engine, window, owner, modes = create_workspace(app, viewport=viewport, scale=125 if wide else 100)
owner.navigate('tuning')
tune = owner.tuning


def wait(subject, predicate, seconds=30):
    deadline = monotonic()+seconds
    while monotonic()<deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError((subject,tune.view,warnings))


def lookup(name):
    obj = window.findChild(QObject,name)
    if obj:
        return obj
    pending=[window.contentItem()]
    while pending:
        item=pending.pop()
        if item.objectName()==name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(name)


def open_file(dialog,path):
    # Exercise the QML onAccepted handler without accepting a native dialog
    # on the user's desktop. The file itself belongs to this test.
    assert dialog.setProperty('selectedFile',QUrl.fromLocalFile(str(path)))
    dialog.accepted.emit()
    wait('reads chosen project',lambda: not tune.view['busy'] and tune.view['source']==str(path))


button = Qt.MouseButton.LeftButton


def tap(name):
    tool = lookup(name)
    assert tool.isVisible() and tool.isEnabled(), tune.view
    QTest.mouseClick(window, button, pos=tool.mapToScene(QPointF(tool.width()/2, tool.height()/2)).toPoint())
    app.processEvents()


def note_point(roll, target, fraction, up=0):
    top = roll.property('pitchOffset')
    height = roll.property('pitchRowHeight')
    y = roll.property('plotTop') + (127-target['pitch']-top+.5)*height
    t = target['start'] + (target['end']-target['start'])*fraction
    x = roll.property('plotLeft') + roll.property('plotWidth') * (
        (t-roll.property('visibleStart')) / roll.property('visibleSpan'))
    point = roll.mapToScene(QPointF(x, y-height*up)).toPoint()
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (point, target)
    return point


def draw_on_vocal_note(lyric):
    tap('tuningPitchDrawTool')
    wait('pencil selected', lambda: tune.view['pitchTool'] == 'draw')
    roll = lookup('tuningTimelineCanvas')
    target = next(row for row in tune.project.matches(lyric) if row['trackId'] == tune.view['activeTrack'])
    assert not target['drawReason']
    start, end = note_point(roll, target, .17), note_point(roll, target, .47, .55)
    QTest.mousePress(window, button, pos=start)
    QTest.mouseMove(window, end, 24)
    assert tune.view['pitchStrokeCount'] == 0
    QTest.mouseRelease(window, button, pos=end)
    wait('stroke saved', lambda: tune.view['pitchStrokeCount'] == 1)
    assert tune.pitchCurves[0]['noteId'] == target['id']


def render_candidate(dialog, source, lyric, target, *, notes_edited=False):
    before = file_hash(source)
    tune.setQuery(lyric)
    wait('lyric is found', lambda: tune.view['lyricMatchCount']>0)
    tune.stepLyricMatch(1)
    wait('lyric time range selected', lambda: tune.view['end']>tune.view['start'])
    tune.setStylePitch(True)
    assert tune.view['stylePitch'] is True
    tune.addRegion()
    draw_on_vocal_note(lyric)
    assert tune.view['canFreeze']
    tune.freeze()
    wait('pitch plan frozen',lambda:tune.view['canRun'])
    assert '曲线有变化' in tune.view['previewSummary']
    assert '手绘 1 段' in tune.view['previewSummary']
    tune.run()
    wait('verified result',lambda: not tune.view['busy'] and tune.view['candidateVerified'])
    assert tune.view['candidateDetails']['pitchChanged']
    assert tune.view['candidateDetails']['notesPreserved'] is not notes_edited
    assert tune.view['candidateDetails']['noteChanges'] == int(notes_edited)
    assert tune.view['candidateDetails']['pitchSpans'] == 1
    assert dialog.property('defaultSuffix') == source.suffix[1:]
    assert source.suffix in dialog.property('nameFilters')[0]
    assert dialog.setProperty('selectedFile',QUrl.fromLocalFile(str(target)))
    dialog.accepted.emit()
    wait('saved using same extension',lambda:not tune.view['busy'] and tune.view['savedCopy']==str(target))
    assert file_hash(source)==before and file_hash(target)==file_hash(tune.view['candidate'])
    assert workflow.inspect_project(target)['candidate_origin']['verified']


def edit_note(previous_lyric, new_lyric):
    roll = lookup('tuningTimelineCanvas')
    tune.setQuery(previous_lyric)
    tune.stepLyricMatch(1)
    wait('editable note is located', lambda: tune.view['lyricMatchCount']>0)
    QTest.qWait(65)
    row = next(note for note in tune.project.matches(previous_lyric) if note['trackId'] == tune.view['activeTrack'])
    assert not any(row[k] for k in ('lyricReason', 'pitchReason', 'timingReason')), row
    identity = row['id']
    QTest.mouseDClick(window, button, pos=note_point(roll, row, .42))
    field = lookup('tuningInlineLyric')
    wait('inline lyric focus', field.hasActiveFocus)
    event = QInputMethodEvent()
    event.setCommitString(new_lyric)
    assert app.sendEvent(field, event)
    QTest.keyClick(window, Qt.Key.Key_Return)
    wait('new lyric in score', lambda: tune.project.notes[identity]['lyric'] == new_lyric)
    tap('tuningNoteTool')
    assert tune.view['noteTool']
    pixels = roll.property('plotWidth') / roll.property('visibleSpan')

    def drag(point, dx, dy):
        end = point + QPointF(dx, dy).toPoint()
        QTest.mousePress(window, button, pos=point)
        QTest.mouseMove(window, end, 24)
        QTest.mouseRelease(window, button, pos=end)
        app.processEvents()

    drag(note_point(roll, row, .4), .04*pixels, -roll.property('pitchRowHeight'))
    moved = tune.project.notes[identity]
    assert moved['start'] > row['start']+.02 and moved['pitch'] == row['pitch']+1, (moved, tune.view)
    drag(note_point(roll, moved, .99), .045*pixels, 0)
    resized = tune.project.notes[identity]
    assert resized['end'] > moved['end']+.02, (resized, tune.view)
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tune.project.notes[identity] == moved
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert tune.project.notes[identity] == resized
    return resized


try:
    out = ROOT/'work/test-results'
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='quick-ink-',dir=native_path(ROOT/'work')) as folder:
        root=ROOT/'work'/Path(folder).name
        native=root/'原始工程.acep'
        native.write_bytes(wrap_ace(ace_score()))
        exchange=root/'官方交换谱.ufdata'
        atomic_json(exchange,uf_score())
        binary=root/'新版只读.acep'
        binary.write_bytes(wrap_ace(ace_score(),binary=True))
        vpr=write_vpr(root/'原始唱段.vpr',vpr_score())
        source=lookup('tuningProjectDialog')
        output=lookup('tuningCandidateSaveDialog')
        wait('tuning visible',lambda:lookup('tuningOpenProject').property('visible'))
        filters=source.property('nameFilters')
        assert '*.acep' in filters[0] and '*.ufdata' in filters[0]
        open_file(source,native)
        assert 'JSON / 9' in tune.view['projectFormat']
        assert not lookup('openVoiceCalibration').property('enabled')
        target=root/'原始工程-VWA.acep'
        assert Path(tune.suggestedCandidatePath().toLocalFile())==target
        assert 'Samples' in tune.view['formatNotice']
        render_candidate(output,native,'窗',target)
        with patch('quick_tuning.QDesktopServices.openUrl',return_value=True) as open_url:
            tune.openCandidate()
            wait('opens same directory copy',lambda:not tune.view['busy'] and open_url.called)
            assert Path(open_url.call_args.args[0].toLocalFile())==target
        open_file(source,exchange)
        assert tune.view['projectSuffix']=='ufdata'
        render_candidate(output,exchange,'江',root/'ACE 导入片段.ufdata')
        tune.openCandidate()
        assert '导入另存的 UFData' in tune.view['message']
        open_file(source,vpr)
        render_candidate(output,vpr,'青',root/'合成手绘.vpr')
        ace_notes = root/'编辑歌词.acep'
        ace_notes.write_bytes(wrap_ace(editable_ace_score()))
        uf_notes = root/'编辑音符.ufdata'
        atomic_json(uf_notes, editable_uf_score())
        vpr_notes = write_vpr(root/'更新歌词.vpr', editable_vpr_score())
        for project, before, lyric in ((ace_notes, '窗', '星'), (uf_notes, '春', '青'), (vpr_notes, 'ま', 'め')):
            print(f'Check {project.suffix}: inline lyric, move, resize, undo and mixed candidate', flush=True)
            open_file(source,project)
            resized = edit_note(before, lyric)
            render_candidate(output,project,lyric,project.with_stem(project.stem+'-VWA'), notes_edited=True)
            tune.resumeSession(QUrl.fromLocalFile(str(tune.session)))
            wait('draft and verified edited candidate', lambda: not tune.view['busy'] and tune.view['candidateVerified']
                 and tune.view['noteEditCount'] == 1 and tune.view['pitchStrokeCount'] == 1)
            assert tune.project.notes[resized['id']] == resized
        open_file(source,binary)
        assert '2 / 9' in tune.view['projectFormat'] and '仅可浏览' in tune.view['formatNotice']
        assert not tune.view['canFreeze'] and all(not row['editable'] for row in tune.tracks)
        assert not lookup('tuningStylePitch').property('enabled')
        assert not lookup('tuningPitchDrawTool').property('enabled')
        assert not lookup('tuningNoteTool').property('enabled')
        readonly = lookup('tuningFormatReadOnly')
        assert readonly.property('visible') and 'UFData' in readonly.property('text')
        assert not warnings, warnings
        assert window.grabWindow().save(str(out/f'acep2-read-only-{viewport[0]}.png'))
        print(f'PASS {viewport[0]}: VPR, ACEP and UFData score + style + pointer strokes, draft, safe export and ACEP2 read-only')
finally:
    modes.close()
    owner.close()
    close_journal()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
