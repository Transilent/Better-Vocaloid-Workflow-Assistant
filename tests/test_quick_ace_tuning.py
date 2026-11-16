"""Exercise ACEP, UFData and VPR pitch editing and candidate controls in Qt Quick."""
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt, QUrl, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, file_hash, native_path
from operation_diagnostics import close_journal
from quick_app import create_workspace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ace_tuning import ace_score, uf_score, wrap_ace
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


def draw_on_vocal_note(lyric):
    tool = lookup('tuningPitchDrawTool')
    assert tool.isVisible() and tool.isEnabled(), tune.view
    button = Qt.MouseButton.LeftButton
    QTest.mouseClick(window, button, pos=tool.mapToScene(QPointF(tool.width()/2, tool.height()/2)).toPoint())
    wait('pencil selected', lambda: tune.view['pitchTool'] == 'draw')
    roll = lookup('tuningTimelineCanvas')
    target = next(row for row in tune.project.matches(lyric) if row['trackId'] == tune.view['activeTrack'])
    assert not target['drawReason']
    top = roll.property('pitchOffset')
    height = roll.property('pitchRowHeight')
    y = roll.property('plotTop') + (127-target['pitch']-top+.5)*height

    def where(fraction, up):
        t = target['start'] + (target['end']-target['start'])*fraction
        x = roll.property('plotLeft') + roll.property('plotWidth') * (
            (t-roll.property('visibleStart')) / roll.property('visibleSpan'))
        point = roll.mapToScene(QPointF(x, y-height*up)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height()
        return point

    start, end = where(.17, 0), where(.47, .55)
    QTest.mousePress(window, button, pos=start)
    QTest.mouseMove(window, end, 24)
    assert tune.view['pitchStrokeCount'] == 0
    QTest.mouseRelease(window, button, pos=end)
    wait('stroke saved', lambda: tune.view['pitchStrokeCount'] == 1)
    assert tune.pitchCurves[0]['noteId'] == target['id']


def render_candidate(dialog, source, lyric, target):
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
    assert tune.view['candidateDetails']['pitchChanged'] and tune.view['candidateDetails']['notesPreserved']
    assert tune.view['candidateDetails']['pitchSpans'] == 1
    assert dialog.property('defaultSuffix') == source.suffix[1:]
    assert source.suffix in dialog.property('nameFilters')[0]
    assert dialog.setProperty('selectedFile',QUrl.fromLocalFile(str(target)))
    dialog.accepted.emit()
    wait('saved using same extension',lambda:not tune.view['busy'] and tune.view['savedCopy']==str(target))
    assert file_hash(source)==before and file_hash(target)==file_hash(tune.view['candidate'])
    assert workflow.inspect_project(target)['candidate_origin']['verified']


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
        open_file(source,binary)
        assert '2 / 9' in tune.view['projectFormat'] and '仅可浏览' in tune.view['formatNotice']
        assert not tune.view['canFreeze'] and all(not row['editable'] for row in tune.tracks)
        assert not lookup('tuningStylePitch').property('enabled')
        assert not lookup('tuningPitchDrawTool').property('enabled')
        readonly = lookup('tuningFormatReadOnly')
        assert readonly.property('visible') and 'UFData' in readonly.property('text')
        assert not warnings, warnings
        assert window.grabWindow().save(str(out/f'acep2-read-only-{viewport[0]}.png'))
        print(f'PASS {viewport[0]}: VPR, ACEP and UFData styling and pointer strokes, safe export and ACEP2 read-only')
finally:
    modes.close()
    owner.close()
    close_journal()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
