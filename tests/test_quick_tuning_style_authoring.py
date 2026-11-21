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
    original['name'] = f'Test target {root.parent.name} {scale}'
    target.write_bytes(encode(original, b''))
    hashes = file_hash(source), file_hash(target)
    try:
        engine, window, bridge, modes = create_workspace(app, viewport=viewport, scale=scale)
    except RuntimeError as error:
        raise AssertionError(warnings) from error
    tune = bridge.tuning
    workshop = bridge.workshop

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
        raise AssertionError((label, scale, tune.worker.kind, tune.view['message'], workshop.view, warnings))

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
        click('nav_tuning_workshop')
        wait('current-file reference', lambda: workshop.view['reference'] and not tune.view['busy'])
        workshop.loadReference(QUrl.fromLocalFile(str(source)))
        wait('external reference selected', lambda: workshop.view['reference'].get('path') == str(source))
        workshop.edit('start', '0')
        workshop.edit('end', '0.5')
        click('styleAuthorMeasurePitch')
        click('styleAuthorAnalyze')
        wait('reference envelope', lambda: item('styleAuthorEnvelope') and workshop.view['gestures'])
        workshop.edit('name', f'Reference style {scale}')
        points = workshop.view['points']
        click('workshopReturnTuning')
        click('nav_tuning_workshop')
        assert workshop.view['ready'] and workshop.view['points'] == points
        assert not item('styleAuthorApply').isEnabled()
        click('styleAuthorTrial')
        wait('non-destructive preview', lambda: bool(workshop.view['trial']))
        assert not tune.regions
        captures = ROOT / 'work/test-results'
        captures.mkdir(exist_ok=True)
        assert window.grabWindow().save(str(captures / f'workshop-style-{viewport[0]}.png'))
        click('styleAuthorSave')
        wait('append to library', lambda: not tune.view['busy']
             and workshop.view['selectedPreset'].get('name') == f'Reference style {scale}', timeout=35)
        click('workshopTab_presets')
        wait('style in workbench library', lambda: item('workshopPresetRow_' + workshop.view['selectedPreset']['id']),
             timeout=35)
        wait('style details loaded', lambda: bool(bridge.support.presetLibrary['inspect_text']), timeout=35)
        assert window.grabWindow().save(str(captures / f'workshop-presets-{viewport[0]}.png'))
        click('workshopTab_style')
        assert workshop.view['trial']
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
