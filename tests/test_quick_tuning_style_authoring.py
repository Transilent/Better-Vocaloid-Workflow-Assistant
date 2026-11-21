"""Exercise the reference-to-recipe flow in the two supported Quick window modes."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

import numpy as np
import soundfile as sf

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from common import ROOT, atomic_json, file_hash
from quick_app import create_workspace
from tuning_workflow import read_project
from ustx_tuning import encode
import task_contract


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
    different_clock = root / 'different-clock.ustx'
    comparison['tempos'] = [{'position': 0, 'bpm': 240}, {'position': 240, 'bpm': 80},
                            {'position': 480, 'bpm': 120}]
    different_clock.write_bytes(encode(comparison, b''))
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
        workshop.loadComparison(QUrl.fromLocalFile(str(different_clock)))
        wait('different clock loaded', lambda: workshop.view['canAnalyze'] and not workshop.view['busy'])
        click('styleCheckReference')
        wait('clock mismatch detected before extraction', lambda: workshop.view['referenceReview'].get('error'))
        assert not item('styleAuthorAnalyze').isEnabled()
        workshop.loadComparison(QUrl.fromLocalFile(str(before)))
        wait('comparison project loaded', lambda: workshop.view['canAnalyze'] and not workshop.view['busy'])
        click('styleCheckReference')
        wait('same-score comparison checked', lambda: workshop.view['referenceReview'].get('usable'))
        assert workshop.view['referenceReview']['comparison_notes'] == 1
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

        job = root / '20261121_prepared_source'
        audio_dir = job / 'audio'
        audio_dir.mkdir(parents=True)
        rate = 16000
        times = np.arange(rate) / rate
        tone = np.sin(2 * np.pi * 261.63 * times) * (.11 + .08 * np.sin(np.pi * 2 * times)**2)
        for name, wave in (('source.wav', tone), ('lead.wav', tone), ('backing.wav', tone * .15),
                           ('instrumental.wav', tone * .06)):
            sf.write(audio_dir / name, wave, rate)
        atomic_json(job / 'request.json', {'source_kind': 'local', 'source_title': 'Workshop vocal fixture',
                                          'target': 'separation', 'voice_mode': 'dual'})
        atomic_json(job / 'status.json', {'state': 'done'})
        task_contract.register(job, [{'path': audio_dir / 'source.wav', 'role': 'audio'}], 'audio', parents=())
        task_contract.register(job, [{'path': audio_dir / name, 'role': 'audio'}
                                    for name in ('lead.wav', 'backing.wav', 'instrumental.wav')], 'separation')
        click('nav_tuning_workshop')
        click('styleMethodReference')
        workshop.edit('mode', 'audio')
        workshop.edit('measurePitch', False)
        click('styleGoToPreparation')
        wait('separation is configured', lambda: bridge.route == 'processing'
             and bridge.preparation['target'] == 'separation')
        assert not bridge.preparationBusy
        bridge.chooseMusic(QUrl.fromLocalFile(str(audio_dir / 'source.wav')))

        def separation_fixture(draft, _existing, _retry, _stop, _send, **_reuse):
            assert draft.view['target'] == 'separation'
            assert Path(draft.view['music_path']) == audio_dir / 'source.wav'
            return {'job': str(job), 'state': 'done', 'message': 'Fixture separation completed'}

        with patch('quick_bridge._run_preparation', separation_fixture):
            bridge.startPreparation()
            wait('separation results registered', lambda: not bridge.preparationBusy
                 and bridge.preparationTask['state'] == 'done')
        click('preparationChooseWorkshopVocal')
        wait('only registered vocal stems are offered', lambda: bridge.route == 'results'
             and set(bridge.taskDetail.get('referenceVocalRoles', {}).values()) == {'lead', 'backing'},
             timeout=25)
        assert item('workshopVocalArtifact').property('currentText').endswith('lead.wav')
        click('useWorkshopVocal')
        wait('prepared vocal handed back to the same score', lambda: bridge.route == 'tuning_workshop'
             and workshop.view['preparedVocal'].get('role') == 'lead')
        assert Path(workshop.view['reference']['path']) == source
        assert Path(workshop.view['fields']['audio']) == audio_dir / 'lead.wav'
        click('styleCheckReference')
        wait('prepared audio checked', lambda: workshop.view['referenceReview'].get('usable'))
        checked = workshop.view['referenceReview']['alignment']
        assert checked['accepted_notes'] == 1 and checked['notes'][0]['state'] == 'covered'
        assert checked['audio_duration_seconds'] == 1.
        click('styleReviewNoteCoverage')
        QTest.keyClick(window, Qt.Key.Key_Escape)
        workshop.edit('offset', '2')
        click('styleCheckReference')
        wait('misplaced audio is marked out of range', lambda: workshop.view['referenceReview'].get('usable') is False)
        assert workshop.view['referenceReview']['alignment']['notes'][0]['state'] == 'outside_audio'
        assert not item('styleAuthorAnalyze').isEnabled()
        workshop.edit('offset', '0')
        click('styleAuthorAnalyze')
        wait('approved audio can supply style loudness', lambda: workshop.view['ready'] and
             workshop.view['report'].get('source') == 'aligned_dry_vocal')
        assert window.grabWindow().save(str(captures / f'workshop-reference-{viewport[0]}.png'))

        click('styleChoosePreparedVocal')
        wait('workshop can browse existing preparation results', lambda: bridge.route == 'results'
             and bridge.workshopVocalPick and not bridge.taskLoading)
        assert window.grabWindow().save(str(captures / f'workshop-choose-vocal-{viewport[0]}.png'))
        bridge.openTuningWorkshop('style')
        wait('reference workspace visible again', lambda: item('styleCheckReference') and bridge.route == 'tuning_workshop')
        sf.write(audio_dir / 'lead.wav', tone * .5, rate)
        click('styleCheckReference')
        wait('changed vocal rejected by task identity', lambda: workshop.view['referenceReview'].get('error'))
        assert not workshop.view['ready']
        print('PASS workshop reference checks, preparation routing, identified handoff and drift rejection',
              viewport, scale, flush=True)
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
