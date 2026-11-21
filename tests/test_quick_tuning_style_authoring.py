"""Exercise the reference-to-recipe flow in the two supported Quick window modes."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, file_hash
from quick_app import create_workspace
from tuning_workflow import read_project
from ustx_tuning import encode


assert os.environ.get('VWA_TESTING') == '1'
app = QGuiApplication([])
app.setQuitOnLastWindowClosed(False)
warnings = []
qInstallMessageHandler(lambda kind, _, text: warnings.append(text)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)


def project(reference):
    curves = [
        {'abbr': 'dyn', 'xs': [0, 240, 480], 'ys': [0, 35, 0]},
        {'abbr': 'pitd', 'xs': [0, 55, 168, 312, 433, 480], 'ys': [0, -48, 0, 0, 32, 0]},
    ] if reference else []
    return {'name': 'Test reference' if reference else 'Test target', 'ustx_version': '0.10', 'resolution': 480,
            'tempos': [{'position': 0, 'bpm': 120 if reference else 96}],
            'time_signatures': [{'bar_position': 0, 'beat_per_bar': 4, 'beat_unit': 4}],
            'tracks': [{'track_name': 'Reference' if reference else 'Target'}], 'wave_parts': [],
            'voice_parts': [{'track_no': 0, 'position': 0, 'duration': 1200, 'curves': curves, 'notes': [
                {'position': 0, 'duration': 480, 'tone': 60 if reference else 68, 'lyric': 'la'},
                {'position': 600, 'duration': 360, 'tone': 63, 'lyric': 'old'},
            ]}]}


def exercise(root, viewport, scale):
    root.mkdir()
    source, target = root / 'reference.ustx', root / 'target.ustx'
    source.write_bytes(encode(project(True), b''))
    original = project(False)
    original['name'] = f'Test target {scale}'
    target.write_bytes(encode(original, b''))
    hashes = file_hash(source), file_hash(target)
    try:
        engine, window, bridge, modes = create_workspace(app, viewport=viewport, scale=scale)
    except RuntimeError as error:
        raise AssertionError(warnings) from error
    tune = bridge.tuning

    def item(name):
        pending = [window.contentItem()]
        while pending:
            current = pending.pop()
            if current.objectName() == name and current.isVisible():
                return current
            pending.extend(current.childItems())
        return None

    def wait(label, predicate, timeout=9):
        end = monotonic() + timeout
        while monotonic() < end:
            app.processEvents()
            if predicate():
                return
            sleep(.015)
        raise AssertionError((label, scale, tune.worker.kind, tune.view['message'], tune.styleAuthor, warnings))

    def click(name):
        control = item(name)
        assert control and control.isEnabled(), (name, scale, tune.view['message'])
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()

    try:
        bridge.navigate('tuning')
        tune.chooseProject(QUrl.fromLocalFile(str(target)))
        wait('loaded target', lambda: tune.project and not tune.view['busy'])
        tune.setSessionLocation(QUrl.fromLocalFile(str(root / 'sessions')))
        tune.selectSpan(0., .625)
        click('tuningInspector_presets')
        click('openTuningStyleAuthor')
        wait('current-file reference', lambda: tune.styleAuthor['reference'] and not tune.view['busy'])
        tune.loadStyleReference(QUrl.fromLocalFile(str(source)))
        wait('external reference selected', lambda: tune.styleAuthor['reference'].get('path') == str(source))
        item('styleAuthorFrom').setProperty('text', '0')
        item('styleAuthorTo').setProperty('text', '0.5')
        click('styleAuthorMeasurePitch')
        click('styleAuthorAnalyze')
        wait('reference envelope', lambda: item('styleAuthorEnvelope') and tune.styleAuthor.get('gestures'))
        item('styleAuthorName').setProperty('text', f'Reference style {scale}')
        assert not item('styleAuthorApply').isEnabled()
        click('styleAuthorTrial')
        wait('non-destructive preview', lambda: bool(tune.styleAuthor['trial']))
        assert not tune.regions
        click('styleAuthorSave')
        wait('append to library', lambda: not tune.view['busy'] and any(
            f'Reference style {scale} · v' in entry['label'] for entry in tune.presets), timeout=35)
        click('styleAuthorApply')
        wait('accepted region', lambda: len(tune.regions) == 1)
        tune.freeze()
        wait('frozen candidate plan', lambda: tune.view['canRun'])
        tune.run()
        wait('verified candidate', lambda: tune.view['candidateVerified'] and not tune.view['busy'])
        result = read_project(Path(tune.view['candidate']))
        part = result['voice_parts'][0]
        assert part['notes'] == original['voice_parts'][0]['notes']
        curves = {row['abbr']: row for row in part['curves']}
        assert min(curves['pitd']['ys']) < -5 and max(curves['dyn']['ys']) > 5
        assert all(y == 0 for x, y in zip(curves['pitd']['xs'], curves['pitd']['ys']) if x >= 480)
        assert (file_hash(source), file_hash(target)) == hashes
        print('PASS reference style authoring', viewport, scale, flush=True)
    finally:
        modes.close()
        bridge.close()
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


with TemporaryDirectory(prefix='quick-style-author-', dir=ROOT / 'work') as folder:
    for viewport, scale in (((1440, 900), 100), ((1920, 1080), 125)):
        exercise(Path(folder) / str(scale), viewport, scale)
assert not warnings, warnings
