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
    before = root / 'before.ustx'
    comparison = project(True)
    comparison['voice_parts'][0]['curves'][0]['ys'] = [0, 12, 0]
    comparison['voice_parts'][0]['curves'][1]['ys'] = [0] * 6
    before.write_bytes(encode(comparison, b''))
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
        QTest.qWait(40)
        control = item(name)
        assert control and control.isEnabled(), (name, scale, tune.view['message'])
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()

    def edit_number(name, keys):
        click(name)
        wait(f'{name} has keyboard focus', lambda: item(name) and item(name).hasActiveFocus())
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for key in keys:
            QTest.keyClick(window, key)
        QTest.keyClick(window, Qt.Key.Key_Return)

    try:
        bridge.navigate('tuning')
        tune.chooseProject(QUrl.fromLocalFile(str(target)))
        wait('loaded target', lambda: tune.project and not tune.view['busy'])
        tune.setSessionLocation(QUrl.fromLocalFile(str(root / 'sessions')))
        tune.selectSpan(0., .625)
        click('nav_tuning_workshop')
        assert workshop.view['method'] == 'design' and not workshop.view['design']['hasChanges']
        click('styleMethodReference')
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

        click('nav_tuning_workshop')
        workshop.edit('mode', 'compare')
        workshop.loadComparison(QUrl.fromLocalFile(str(before)))
        wait('comparison project loaded', lambda: workshop.view['canAnalyze'] and not workshop.view['busy'])
        click('styleAuthorAnalyze')
        wait('same-score difference analyzed', lambda: workshop.view['ready'] and
             workshop.view['report'].get('source') == 'native_difference')
        assert abs(workshop.view['points'][4][1] - 2.3) < .01
        click('styleUseLoudness')
        wait('reference guide retained', lambda: workshop.view['method'] == 'design' and
             bool(workshop.view['design']['preview'].get('guide')))
        assert workshop.view['design']['template'] == 'none'
        assert workshop.view['design']['draft']['controls']['loudness']['phrase']['body'] == 0
        click('styleMethodReference')
        click('styleUseEdges')
        assert workshop.view['design']['draft']['pitch']['onset']['cents'] < 0
        assert workshop.view['design']['draft']['pitch']['vibrato']['depth'] == 0
        click('designPanelExpression')
        click('designShowTemplates')
        click('designTemplate_bold')
        assert workshop.view['design']['draft']['controls']['loudness']['phrase']['body'] > 0
        click('designChannel_tension')
        edit_number('designValue_tension.note_sustain', (Qt.Key.Key_0, Qt.Key.Key_Period, Qt.Key.Key_3))
        wait('real template numeric edit', lambda: workshop.view['design']['draft']['controls']['tension']['note']['sustain'] == .3)
        click('designTemplate_sad')
        click('designTemplate_bold')
        assert workshop.view['design']['draft']['controls']['tension']['note']['sustain'] == .3
        click('styleMethodReference')
        assert abs(workshop.view['points'][4][1] - 2.3) < .01
        click('styleMethodDesign')
        click('designShowTemplates')
        click('designTemplate_chinese_opera')
        click('designChannel_loudness')
        assert window.grabWindow().save(str(captures / f'workshop-envelope-{viewport[0]}.png'))
        click('designPanelPitch')
        edit_number('designValue_vibrato_depth', (Qt.Key.Key_4, Qt.Key.Key_2))
        wait('real vibrato edit', lambda: workshop.view['design']['draft']['pitch']['vibrato']['depth'] == 42)
        assert window.grabWindow().save(str(captures / f'workshop-gestures-{viewport[0]}.png'))
        workshop.edit('name', f'Opera authoring {scale}')
        workshop.edit('strength', 0)
        click('styleAuthorTrial')
        wait('zero-effect trial is reported', lambda: bool(workshop.view['trial']))
        assert not item('styleAuthorApply').isEnabled()
        workshop.edit('strength', .7)
        click('styleAuthorTrial')
        wait('template trial has an effect', lambda: workshop.view['canApplyTrial'])
        click('styleAuthorSave')
        wait('template saved', lambda: not tune.view['busy']
             and workshop.view['selectedPreset'].get('name') == f'Opera authoring {scale}', timeout=35)
        entry = workshop.view['selectedPreset']
        frozen = workshop.library.get_snapshot(entry['id'], entry['version'])
        assert frozen['document']['requires']['engine_contract'] == 'style-recipe/v3'
        assert frozen['document']['payload']['pitch']['vibrato']['depth'] == 42
        assert not any(name.encode() in frozen['raw'] for name in ('reference.ustx', 'target.ustx'))
        click('workshopTab_presets')
        wait('template can be reopened', lambda: item('workshopEditStructure'), timeout=35)
        click('workshopEditStructure')
        wait('preset reopened as draft', lambda: workshop.view['pane'] == 'style' and
             workshop.view['design'].get('template') == 'library')
        assert workshop.view['design']['draft']['pitch']['vibrato']['depth'] == 42
        click('styleAuthorTrial')
        wait('reopened template has an effect', lambda: workshop.view['canApplyTrial'])
        click('styleAuthorApply')
        wait('template replaces the selected segment',
             lambda: len(tune.regions) == 1 and 'Opera authoring' in tune.regions[0]['label'])
        tune.freeze()
        wait('template candidate plan', lambda: tune.view['canRun'])
        tune.run()
        wait('template candidate verified', lambda: tune.view['candidateVerified'] and not tune.view['busy'])
        part = read_project(Path(tune.view['candidate']))['voice_parts'][0]
        curves = {row['abbr']: row for row in part['curves']}
        assert min(curves['pitd']['ys']) < -70
        assert part['notes'] == original['voice_parts'][0]['notes']
        assert all(y == 0 for x, y in zip(curves['pitd']['xs'], curves['pitd']['ys']) if x >= 480)
        assert workshop.library.get_snapshot(entry['id'], entry['version'])['raw'] == frozen['raw']
        assert (file_hash(source), file_hash(target)) == hashes
        print('PASS editable style templates, no-effect guard, detached preset editing and native candidate', viewport, scale, flush=True)
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
