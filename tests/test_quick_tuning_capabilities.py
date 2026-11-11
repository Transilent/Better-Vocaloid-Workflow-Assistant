"""Run the Quick draft and calibration workflows on real private SVP/WAV files."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication

import calibration_session as calibration
from common import ROOT, atomic_json, file_hash
from expression_calibration import score_identity
from quick_app import create_workspace
from quick_tuning import QuickTuning
from quick_work import WorkerLane
from style_drafts import DraftError


assert os.environ.get('VWA_TESTING') == '1', 'Use an isolated test application'
app = QGuiApplication([])
warnings = []
qInstallMessageHandler(lambda kind, _ctx, message: warnings.append(message)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)
wide = os.environ.get('VWA_TEST_LARGE_VIEWPORT') == '1'
viewport = (1920, 1080) if wide else (1440, 900)
engine, window, bridge, modes = create_workspace(app, viewport=viewport, scale=125 if wide else 100)
tune, cal = bridge.tuning, bridge.calibration
lane = WorkerLane(None, 'test-concurrent-tuning')
other = QuickTuning(None, lane)
proof = ROOT / 'work/test-results'
proof.mkdir(parents=True, exist_ok=True)


def tick(seconds=.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.004)


def expect(label, condition):
    assert condition, (label, tune.view['message'], cal.view['message'], warnings)
    print('PASS', label, flush=True)


def wait(label, condition, *, timeout=15):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        tick(.04)
    expect(label, condition())


def find(name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    obj = window.findChild(QObject, name)
    assert obj is not None, name
    return obj


def click(name):
    node = find(name)
    assert node.property('visible') and node.property('enabled'), name
    assert QMetaObject.invokeMethod(node, 'click'), name
    tick()


def url(path):
    return QUrl.fromLocalFile(str(path))


try:
    with tempfile.TemporaryDirectory(prefix='quick-capabilities-', dir=proof) as directory:
        root = Path(directory)
        source = root / 'calibration-source.svp'
        quarter = 705600000
        lead = {'name': '主唱', 'mainRef': {'blickOffset': 0, 'mute': True, 'database': {'name': 'Fixture Voice'},
                                        'groupID': 'source-group'},
                'mainGroup': {'uuid': 'source-group', 'notes': [
                    {'onset': quarter // 2, 'duration': 5 * quarter, 'pitch': 57, 'lyrics': '春'}],
                    'parameters': {'pitchDelta': {'mode': 'linear', 'points': [0, 0, quarter, 4]}}}}
        backing = deepcopy(lead)
        backing['name'] = '和声'
        backing['mainGroup']['notes'][0]['pitch'] = 60
        score = {'version': 153, 'test_id': root.name, 'time': {'tempo': [{'position': 0, 'bpm': 120}],
                                         'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}]},
                 'tracks': [{'name': '伴奏', 'mainRef': {'isInstrumental': True,
                             'audio': {'filename': 'instrumental.wav', 'duration': 5}}}, lead, backing]}
        atomic_json(source, score)
        source_hash = file_hash(source)
        bridge.navigate('tuning')
        tune.chooseProject(url(source))
        other.chooseProject(url(source))
        wait('two Quick editors load the same source revision', lambda: tune.project and other.project
             and not (bridge.workBusy or lane.kind))

        other.setStyle('gentle')
        other.setStyleStrength(.41)
        wait('first editor autosaves', lambda: not other._dirty)
        tune.setStyle('gentle')
        tune.setStyleStrength(.76)
        wait('second editor keeps its work as a conflict copy', lambda: tune.view['draftConflict'])
        backup = Path(tune.view['draftConflict'])
        expect('shared and local strengths differ without data loss',
               tune._drafts.read(source_hash)['data']['options']['style_strength'] == .41
               and tune._drafts.read_recovery(source_hash, backup)['options']['style_strength'] == .76)
        tune.setStyleStrength(.9)
        expect('conflict prevents unsaved edits from bypassing recovery', tune.view['styleStrength'] == .76)
        click('reviewDraftConflict')
        wait('Quick compares both real draft versions', lambda: tune.view['draftReview'].get('path'))
        other.setStyleStrength(.53)
        wait('other editor advances after the review', lambda: not other._dirty)
        click('restoreLocalTuningDraft')
        wait('stale confirmation refreshes the comparison without writing',
             lambda: not bridge.workBusy and '53%' in tune.view['draftReview'].get('shared', ''))
        expect('third revision remains current', tune._drafts.read(source_hash)['data']['options']['style_strength'] == .53)
        click('restoreLocalTuningDraft')
        wait('explicit reconfirmation restores the local edit', lambda: not bridge.workBusy and not tune.view['draftConflict'])
        expect('outgoing shared revision was archived',
               any(tune._drafts.read_recovery(source_hash, p)['options']['style_strength'] == .53
                   for p in backup.parent.glob(f'{source_hash}.conflict-*.json')))
        QMetaObject.invokeMethod(find('tuningDraftRecoveryDialog'), 'close')
        tick()

        current = tune._drafts.read(source_hash)
        newer = deepcopy(current['data'])
        newer['options']['style_strength'] = .43
        tune._drafts.write(source_hash, current['revision'], newer, writer='fixture')
        tune.setStyleStrength(.68)
        wait('later local edits can also conflict', lambda: bool(tune.view['draftConflict']))
        second_backup = Path(tune.view['draftConflict'])
        click('reviewDraftConflict')
        wait('second conflict opens comparison', lambda: tune.view['draftReview'].get('path'))
        click('loadSharedTuningDraft')
        wait('load latest preserves local backup', lambda: not bridge.workBusy and tune.view['styleStrength'] == .43)
        expect('local recovery remains available', second_backup.is_file())
        QMetaObject.invokeMethod(find('tuningDraftRecoveryDialog'), 'close')
        tick()
        other.chooseProject(url(source))
        wait('reopened editor reads latest', lambda: not lane.kind and other.view['styleStrength'] == .43)
        other.reviewDraftRecovery(url(second_backup))
        wait('closed conflict copy can be reviewed again', lambda: '68%' in other.view['draftReview'].get('local', ''))
        try:
            tune._drafts.read_recovery('f' * 64, second_backup)
            raise AssertionError('wrong source accepted')
        except DraftError:
            pass
        print('PASS recovery copy is bound to source identity', flush=True)

        click('openVoiceCalibration')
        click('calibrationUseTuningTrack')
        wait('calibration takes current track from a real project', lambda: not cal.view['busy'] and cal.view['project'])
        rate = 24000
        signal = .1 * np.sin(np.arange(rate * 4) * 2 * np.pi * 220 / rate)
        sf.write(root / 'instrumental.wav', signal, rate, subtype='FLOAT')
        job = root / 'synthetic-task'
        (job / 'audio').mkdir(parents=True)
        atomic_json(job / 'request.json', {'voice_mode': 'single'})
        reference = job / 'audio/lead.wav'
        short = root / 'too-short.wav'
        sf.write(short, signal[:rate], rate, subtype='FLOAT')
        sf.write(reference, np.r_[signal * 10 ** (1.5 / 20), np.zeros(rate)], rate, subtype='FLOAT')
        task_files = sorted(p.relative_to(job).as_posix() for p in job.rglob('*'))
        cal.chooseFolder(url(root))
        cal.chooseReference(url(short))
        click('calibrationPrepare')
        wait('preflight refuses truncated reference', lambda: not cal.view['busy'] and bool(cal.view['review']))
        expect('bad input does not produce a session', not cal.session and not any(
               path.is_dir() for path in root.glob('calibration-*')))
        cal.chooseReference(url(reference))
        click('calibrationInspect')
        wait('preflight reads bank and actual note count', lambda: not cal.view['busy'] and cal.view['review'].get('ready'))
        expect('correct track is selected past instrumental track', cal.view['review']['bank'] == 'Fixture Voice')
        click('calibrationPrepare')
        wait('prepare commits seven real SVP probes', lambda: cal.session and not cal.view['busy'])
        session = cal.session
        spec = calibration.read_session(session)
        batch = calibration.project_data(session.parent / 'calibration.svp')
        expect('seven probes bind exact track and names', spec['track_index'] == 1 and len(batch['tracks']) == 7
               and {t['name'] for t in batch['tracks']} == set(spec['outputs'].values()))
        expect('calibration fits inside the normal window',
               find('voiceCalibrationDialog').property('height') < window.height()
               and find('calibrationFit').property('visible'))
        click('calibrationFit')
        click('calibrationClose')
        bridge.navigate('audio_mixdown')
        tick()
        expect('calibration continues through page navigation',
               cal.view['busy'] and find('backgroundCalibration').property('visible') and not bridge.workBusy)
        renders = Path(spec['render_folder'])
        trials = list(spec['outputs'].items())
        for control, token in trials[:3]:
            sf.write(renders / f'calibration_{token}.wav',
                     signal * 10 ** (3 / 20) if control == 'loudness' else signal, rate, subtype='FLOAT')
        wait('background displays the actual stable WAV count', lambda: cal.view['stable'] == 3)
        try:
            calibration.fit_session(session)
            raise AssertionError('second writer was allowed')
        except ValueError as error:
            expect('same session rejects concurrent fitting', '其他窗口' in str(error))
        click('returnBackgroundCalibration')
        click('calibrationStop')
        wait('stop preserves the session and trial WAVs', lambda: not cal.view['busy'])
        expect('cancel before fitting creates no result', not cal.view['result'] and len(list(renders.glob('*.wav'))) == 3)
        click('calibrationNewSession')
        cal.resumeSession(url(session))
        wait('resume reads the original session and folder', lambda: cal.session == session and not cal.view['busy'])
        for control, token in trials[3:]:
            sf.write(renders / f'calibration_{token}.wav',
                     signal * 10 ** (3 / 20) if control == 'loudness' else signal, rate, subtype='FLOAT')
        click('calibrationFit')
        wait('all seven WAVs fit and produce a verified project', lambda: not cal.view['busy'] and cal.view['measured'],
             timeout=40)
        result = cal.result
        fitted = calibration.project_data(result['output'])
        expect('report, project and comparison match actual bytes',
               file_hash(result['output']) == result['sha256'] and result['neutral']
               and file_hash(result['profile']) == result['profile_sha256'] and cal.view['stable'] == 7)
        expect('fit preserves score, untouched track and task files', file_hash(source) == source_hash
               and score_identity(fitted['tracks'][1], fitted) == score_identity(lead, score)
               and fitted['tracks'][2] == backing
               and sorted(p.relative_to(job).as_posix() for p in job.rglob('*')) == task_files)
        assert window.grabWindow().save(str(proof / f'calibration-result-{viewport[0]}.png'))
        click('calibrationClose')
        bridge.navigate('tuning')
        tune.focusTrack(tune.project.editable[1])
        tick()
        click('openVoiceCalibration')
        expect('result cannot bind to the wrong track', not cal.view['canBind'])
        click('calibrationClose')
        tune.focusTrack(tune.project.editable[0])
        tick()
        click('openVoiceCalibration')
        click('calibrationBindResult')
        wait('verified fit binds and saves actual profile and WAV', lambda: not bridge.workBusy
             and tune.view['calibration'] == result['profile'])
        expect('binding persisted to the correct source track',
               tune._drafts.read(source_hash)['data']['options']['reference_sources'] ==
               [{'track_index': 1, 'path': str(reference), 'voice': 'lead'}])
        click('freezeTuning')
        wait('calibrated tuning plan is usable', lambda: tune.view['canRun'])
        click('runTuning')
        wait('normal Quick tuning produces a verified candidate', lambda: not bridge.workBusy and tune.candidate,
             timeout=40)
        expect('normal tuning actually uses calibrated audio', tune.candidate['receipt_verified']
               and tune.candidate['report']['primary']['voicebank_calibrated']
               and file_hash(tune.candidate['output']) == tune.candidate['output_sha256'])
        expect('actual candidate summary and export are available',
               tune.view['candidateDetails']['tracks'] == 1 and find('saveTuningCandidate').property('enabled'))
        saved = root / '独立导出.svp'
        tune.exportCandidate(url(saved))
        wait('explicit output location receives the exact verified bytes',
             lambda: not bridge.workBusy and tune.view['savedCopy'] == str(saved))
        expect('export preserves candidate byte identity', file_hash(saved) == tune.candidate['output_sha256'])
        saved.write_bytes(b'fixture edits after export')
        tune.exportCandidate(url(saved))
        wait('export refuses overwrite of an existing project', lambda: not bridge.workBusy and tune.view['state'] == 'failed')
        expect('existing project remains intact', saved.read_bytes() == b'fixture edits after export')
        sf.write(next(renders.glob('*.wav')), signal * .8, rate, subtype='FLOAT')
        cal.resumeSession(url(session))
        wait('resume finishes after a modified trial', lambda: not cal.view['busy'])
        expect('changed render makes the old result unbindable', not cal.view['canBind'] and not cal.view['result'])
        bridge.setPreparationField('voice_mode', 'dual')
        bridge.setPreparationField('separator_model', 'roformer')
        bridge.navigate('components')
        wait('component catalog loads actual local facts', lambda: not bridge.loading and bridge.componentModel.rowCount())
        expect('current preparation choices reach component page',
               'BS-RoFormer' in bridge.componentTaskPlan['summary']
               and 'BS-RoFormer' in find('componentTaskPlan').property('text'))
        bridge.selectComponent('expression')
        tick()
        expect('component detail shows verified WORLD installation bytes',
               bridge.selected['status'] == 'ready' and bridge.selected['installed_bytes'] > 0
               and '0.3.5' in find('componentHealth').property('text'))
        click('verifyComponent')
        wait('full WORLD verification feeds its actual receipt to component page',
             lambda: not bridge.workBusy and bridge.selected.get('last_check', {}).get('verified'))
        expect('verification result is visible', find('componentLastVerification').property('visible'))
        assert window.grabWindow().save(str(proof / f'component-verification-{viewport[0]}.png'))
        expect('no Quick runtime warnings', not warnings)
        print('Quick draft and seven-track calibration capabilities passed', viewport, flush=True)
finally:
    lane.close()
    other.close()
    modes.close()
    bridge.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qInstallMessageHandler(None)
