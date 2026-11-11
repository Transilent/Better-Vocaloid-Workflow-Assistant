"""Exercise recovery routes through the Quick controls in a private app copy."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import mido
import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication

import calibration_session
from common import ROOT, app_path, atomic_json, file_hash
import lyrics_sources
import pipeline
import preparation_commands
from quick_app import create_workspace
from subtitles import Cue
from vocal_expression import SV_QUARTER as Q


assert os.environ.get('VWA_TESTING') == '1', 'Run only in a private app copy'
app = QGuiApplication([])
warnings = []
qInstallMessageHandler(lambda kind, _ctx, msg: warnings.append(msg)
                       if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else None)
engine, window, bridge, modes = create_workspace(app, viewport=(1440, 900), scale=100)
cal, sub, sources = bridge.calibration, bridge.subtitles, bridge.preparationSources


def url(path):
    return QUrl.fromLocalFile(str(path))


def wait(label, predicate, timeout=15):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.009)
    assert predicate(), (label, cal.view['message'], sub.view['error'], bridge.preparationTask, bridge.notice, warnings)
    print('PASS', label, flush=True)


def dialog_item(dialog, name):
    node = dialog.findChild(QObject, name)
    assert node is not None, name
    return node


def click(dialog, name):
    node = dialog_item(dialog, name)
    assert node.property('visible') and node.property('enabled'), name
    assert QMetaObject.invokeMethod(node, 'click'), name
    app.processEvents()


try:
    (ROOT / 'work').mkdir(exist_ok=True)
    (ROOT / 'jobs').mkdir(exist_ok=True)
    with (tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='parity-') as work,
          tempfile.TemporaryDirectory(dir=ROOT / 'jobs', prefix='cal-') as job):
        work, job = Path(work), Path(job)
        (job / 'audio').mkdir()
        request = {'source_kind': 'local', 'source_title': '校准验证', 'language': 'zh', 'lyrics': '因果与对错'}
        atomic_json(job / 'request.json', request)
        rate = 24000
        audio = job / 'audio/lead.wav'
        t = np.arange(rate * 4) / rate
        wave = (.08 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        sf.write(audio, wave, rate, subtype='FLOAT')
        sf.write(job / 'audio/source.wav', wave, rate, subtype='FLOAT')
        sf.write(job / 'audio/instrumental.wav', wave * 0, rate, subtype='FLOAT')
        group = {'notes': [{'onset': 0, 'duration': 2*Q, 'pitch': 57, 'lyrics': '因'},
                           {'onset': 3*Q, 'duration': 3*Q, 'pitch': 57, 'lyrics': '错'}],
                 'parameters': {}}
        original = work / 'input.svp'
        score = {'version': 153,
                 'time': {'tempo': [{'position': 0, 'bpm': 120}],
                          'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}]},
                 'tracks': [{'mainRef': {'isInstrumental': True,
                                         'audio': {'filename': str(job / 'audio/instrumental.wav'), 'duration': 4}}},
                            {'name': 'Lead', 'mainRef': {'database': {'languageOverride': 'mandarin'}},
                             'mainGroup': group}]}
        atomic_json(original, score)
        original_sha = file_hash(original)
        template = work / 'bank.svp'
        model = copy.deepcopy(score)
        model['tracks'][1]['mainRef']['database'] = {'name': 'Fixture Bank', 'languageOverride': 'japanese'}
        model['tracks'][1]['mainGroup']['notes'][0]['lyrics'] = '另一首歌'
        atomic_json(template, model)

        dialog = dialog_item(window, 'voiceCalibrationDialog')
        QMetaObject.invokeMethod(dialog, 'open')
        cal.chooseProject(url(original))
        wait('select vocal track when instrumental is first', lambda: not cal.worker.kind and cal.view['canInspect'] is False
             and len(cal.view['tracks']) == 1 and cal.view['tracks'][0]['index'] == 1)
        cal.chooseReference(url(audio))
        cal.selectVoice('lead')
        cal.chooseFolder(url(work))
        click(dialog, 'calibrationInspect')
        wait('lyric review includes a real interval', lambda: not cal.worker.kind
             and bool(cal.view['review'].get('lyrics', {}).get('candidates'))
             and cal.view['review']['lyrics']['candidates'][0]['start'] == 1.)
        cal.prepareInputs(url(template), False)
        wait('bank copy completes in an isolated worker', lambda: not cal.worker.kind and bool(cal.view['prepared']))
        bank = Path(cal.view['prepared']['project'])
        data = calibration_session.project_data(bank)
        assert data['tracks'][1]['mainRef']['database'] == {'name': 'Fixture Bank', 'languageOverride': 'mandarin',
                                                          'phonesetOverride': 'xsampa'}
        assert data['tracks'][1]['mainGroup'] == group and file_hash(original) == original_sha
        cal.prepareInputs(QUrl(), True)
        wait('CPU RMVPE pitch copy completes without Qwen', lambda: not cal.worker.kind and cal.view['project'] != str(bank),
             timeout=110)
        receipt = cal.view['prepared']
        repaired = calibration_session.project_data(receipt['project'])
        assert receipt['repair_pitch'] and receipt['sha256'] == file_hash(receipt['project'])
        notes = repaired['tracks'][1]['mainGroup']['notes']
        assert [row['lyrics'] for row in notes] == ['因', '错']
        assert len(repaired['tracks'][1]['mainGroup']['parameters']['pitchDelta']['points']) > 8
        assert file_hash(original) == original_sha

        # Use the local reference instead of the deliberately mismatched task
        # lyrics; the real timeline, input and result hashes still get checked.
        atomic_json(job / 'request.json', {**request, 'lyrics': '因错'})
        renders = work / 'trial-output'
        renders.mkdir()
        cal.chooseFolder(url(job))
        cal.chooseRenders(url(renders))
        click(dialog, 'calibrationPrepare')
        wait('create seven-track session with a selected render directory', lambda: not cal.worker.kind
             and bool(cal.view['session']))
        session = Path(cal.view['session'])
        spec = calibration_session.read_session(session)
        assert Path(spec['render_folder']) == renders and len(spec['outputs']) == 7 and spec['track_index'] == 1
        cal.newSession()
        click(dialog, 'calibrationFindTaskSessions')
        wait('task catalogue discovers owned session', lambda: not cal.worker.kind
             and any(Path(row['path']) == session for row in cal.view['sessionChoices']))
        session.write_bytes(session.read_bytes() + b'\n')
        cal.resumeListedSession(str(session))
        wait('changed catalogue row cannot silently resume', lambda: not cal.worker.kind and '变化' in cal.view['message'])
        cal.findSessions(url(session.parent))
        wait('a local calibration folder can be rediscovered', lambda: not cal.worker.kind
             and any(Path(row['path']) == session for row in cal.view['sessionChoices']))
        cal.resumeListedSession(str(session))
        wait('verified session restores the render location', lambda: not cal.worker.kind
             and Path(cal.view['session']) == session and Path(cal.view['renders']) == renders)
        for control, token in spec['outputs'].items():
            sf.write(renders / (token + '.wav'), wave * (.5 if control == 'loudness' else .35), rate, subtype='FLOAT')
        click(dialog, 'calibrationFit')
        wait('synthetic SV render handoff fits and verifies the result', lambda: not cal.worker.kind
             and cal.view['measured'], timeout=35)
        click(dialog, 'calibrationRegisterTask')
        wait('explicit calibration registration writes task receipts', lambda: not cal.worker.kind
             and (job / 'expression-result.json').is_file()
             and bool(json.loads((job / 'task.json').read_text(encoding='utf-8')).get('calibration_sessions')))
        QMetaObject.invokeMethod(dialog, 'close')

        bridge.openSubtitleTool()
        subtitle = dialog_item(window, 'subtitleDialog')
        lrc = work / 'local.lrc'
        lrc.write_text('[00:00.20]本地第一句\n[00:01.40]本地第二句', encoding='utf-8')
        assert sub.loadLocal(url(lrc)) and sub.updateCue(0, .2, 1.2, '编辑过的 LRC')
        midi = work / 'voices.mid'
        sequence = mido.MidiFile(charset='utf8')
        track = sequence.add_track('Voice')
        track.append(mido.MetaMessage('lyrics', text='MIDI draft', time=0))
        track.append(mido.MetaMessage('lyrics', text='Next line', time=480))
        sequence.save(midi)
        assert sub.loadLocal(url(midi))
        sub.setOffset(.35)
        bridge.setPreparationField('lyrics', '准备页原稿')
        app.processEvents()
        click(subtitle, 'importSubtitleNetease')
        net = dialog_item(window, 'subtitleSourcesDialog')
        lyric = lyrics_sources.LyricReference('测试', [Cue(.5, 1.4, '网易云稿')],
                                               {'provider': 'netease', 'song_id': '456'})
        with patch.object(lyrics_sources, 'fetch', return_value=lyric):
            sources.previewSong('456')
            wait('separate subtitle version preview is ready', lambda: sources.view['canApply'])
        sub.setOffset(.45)
        assert not sources.applyLyrics(), 'Changed target draft must invalidate the preview'
        with patch.object(lyrics_sources, 'fetch', return_value=lyric):
            sources.previewSong('456')
            wait('refreshed subtitle version is bound to the new revision', lambda: sources.view['canApply'])
        click(net, 'applyNeteaseLyrics')
        assert sub.view['sourceKind'] == 'netease' and bridge.state.draft.view['lyrics'] == '准备页原稿'
        srt = sub.export(work / 'sub.srt')
        assert '00:00:00,500 --> 00:00:01,400' in srt.read_text(encoding='utf-8')
        assert '网易云稿' in srt.read_text(encoding='utf-8')
        assert sub.restoreSource('lrc') and sub.cues[0]['text'] == '编辑过的 LRC'
        assert sub.restoreSource('midi') and abs(sub.view['offset'] - .45) < .001
        QMetaObject.invokeMethod(subtitle, 'close')
        print('PASS three subtitle sources, separate drafts and verified SRT', flush=True)

        old = pipeline.create_job(local_music=audio, lyrics='上一次', remember=False)
        old_request = file_hash(old / 'request.json')
        atomic_json(old / 'status.json', {'state': 'failed', 'current_step': 'midi', 'error': 'no space',
                    'planned_steps': ['metadata', 'midi'], 'steps': {'metadata': {'state': 'done'}}})
        (old / 'pipeline.log').write_text('x' * 24000 + '\n尾部日志', encoding='utf-8')
        bridge.navigate('results')
        wait('task catalogue contains the old local input',
             lambda: any(row['jobId'] == str(old) for row in bridge.state.task_rows))
        bridge.selectTask(str(job))
        wait('results protects the still-loaded calibration task',
             lambda: bridge.taskDetail.get('jobId') == str(job) and bridge.taskDetail.get('taskBlocked')
             and '声库校准' in bridge.taskDetail.get('taskBlockReason', ''))
        bridge.deleteSelectedTask(str(job))
        assert job.is_dir() and '声库校准' in bridge.notice, bridge.notice
        bridge.selectTask(str(old))
        wait('task detail enables current-settings reprocess', lambda: bridge.taskDetail.get('canReprocess'))
        assert not bridge.taskDetail.get('taskBlocked')
        bridge.inspectTaskProgress(str(old))
        wait('historical progress shows completed stage and bounded log', lambda: bridge.route == 'processing'
             and bridge.preparationTask.get('log_tail', '').strip() == '尾部日志'
             and bridge.preparationTask.get('recovery_action') == 'storage'
             and bridge.preparationTask.get('stages', [])[0]['state'] == 'done')
        bridge.navigate('results')
        bridge.setPreparationField('target', 'separation')
        bridge.setPreparationField('voice_mode', 'single')
        bridge.setPreparationField('lyrics', '本轮新设置')
        bridge.setPreparationField('output_directory', str(work))

        def finish_job(new, **_):
            atomic_json(new / 'status.json', {'state': 'done', 'steps': {}})
            return 0

        with patch.object(preparation_commands, 'run_process', side_effect=finish_job):
            bridge.reprocessSelectedTask(str(old))
            wait('reprocess creates a distinct job with current settings', lambda: not bridge.preparationBusy
                 and bridge.preparationTask.get('state') == 'done')
        new = Path(bridge.preparationTask['job'])
        current = json.loads((new / 'request.json').read_text(encoding='utf-8'))
        assert new != old and current['lyrics'] == '本轮新设置'
        assert file_hash(app_path(current['source_audio'])) == file_hash(audio)
        assert file_hash(old / 'request.json') == old_request
        assert not warnings, warnings
        print('PASS reprocess preserves the previous job', flush=True)
finally:
    modes.close()
    bridge.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qInstallMessageHandler(None)
