"""Standalone calibration: checks, repair, bank import, recovery and comparisons."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from common import ROOT,atomic_json,file_hash
from vocal_expression import SV_QUARTER as Q
from calibration_session import create_session,read_session,project_data,wait_and_fit,fit_session
from calibration_workflow import inspect_inputs,prepare_copy,latest_result,recovery_hint
from v2m_runtime import configure
configure(ROOT/'dependencies/vocal2midi2')
from inference.API.rmvpe_api import RmvpeResult


def raises(kind,call,*args,**kwargs):
    try:call(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)


with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='self-service-') as directory:
    root=Path(directory);job=root/'job';(job/'exports').mkdir(parents=True);(job/'audio').mkdir()
    rate=24000;wave=.08*np.sin(np.arange(rate*4)*2*np.pi*220/rate)
    audio=job/'audio/lead.wav';sf.write(audio,wave,rate,subtype='FLOAT')
    sf.write(job/'audio/source.wav',wave,rate,subtype='FLOAT')
    sf.write(job/'audio/instrumental.wav',wave*0,rate,subtype='FLOAT')
    atomic_json(job/'request.json',{'language':'zh','lyrics':'因果与对错','voice_mode':'single'})
    xs=np.arange(300)*Q//20;ys=np.zeros(300);ys[::12]=1200
    track={'name':'Lead','mainRef':{'database':{'languageOverride':'mandarin','phonesetOverride':'xsampa'},'voice':{'tF0Left':.02,'paramTension':.1}},
        'mainGroup':{'notes':[{'onset':0,'duration':2*Q,'pitch':57,'lyrics':'因'},
                              {'onset':3*Q,'duration':3*Q,'pitch':57,'lyrics':'错'}],
                     'parameters':{'pitchDelta':{'mode':'linear','points':np.column_stack((xs,ys)).ravel().tolist()}}}}
    data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
          'tracks':[track,{'mainRef':{'isInstrumental':True,'audio':{'filename':'../audio/instrumental.wav','duration':4}}}]}
    original=job/'exports/voices.svp';atomic_json(original,data);original_hash=file_hash(original)
    before=inspect_inputs(original,audio,job=job)
    assert not before['ready'] and {i['code'] for i in before['issues']}=={'bank_missing','pitch_quality','lyrics_review'}
    candidate=before['lyrics']['candidates'][0]
    assert candidate['expected']=='果与对' and candidate['start']==1. and candidate['end']==1.5
    missing=inspect_inputs(original,root/'missing.wav');assert not missing['ready']
    template=root/'bank.svp';sample=copy.deepcopy(data)
    sample['tracks'][0]['mainRef']={'database':{'name':'Demo Bank','languageOverride':'japanese'},
        'voice':{'vocalModePreset':'Clear','paramTension':.9,'tF0Left':.8}}
    sample['tracks'][0]['mainGroup']['notes'][0]['lyrics']='其他歌曲';atomic_json(template,sample)
    assigned=prepare_copy(original,audio,root/'assigned',template=template,language='zh')
    bank=project_data(assigned['project'])['tracks'][0]
    assert bank['mainRef']['database']['name']=='Demo Bank' and bank['mainRef']['database']['languageOverride']=='mandarin'
    assert bank['mainRef']['voice']['paramTension']==.1 and bank['mainRef']['voice']['tF0Left']==.02
    assert bank['mainRef']['voice']['vocalModePreset']=='Clear'
    assert bank['mainGroup']==track['mainGroup'] and file_hash(original)==original_hash
    no_context=prepare_copy(original,audio,root/'no-context',template=template)
    assert project_data(no_context['project'])['tracks'][0]['mainRef']['database']['languageOverride']=='mandarin'
    assert Path(project_data(assigned['project'])['tracks'][1]['mainRef']['audio']['filename'])==job/'audio/instrumental.wav'
    raises(FileExistsError,prepare_copy,original,audio,root/'assigned')
    times=np.arange(400)*.01;pitch=(57+.25*np.sin(times*2*np.pi*5.5)).astype('float32')
    mask=np.ones(400,bool);mask[100:150]=False;mask[300:]=False
    measured=RmvpeResult(.01,pitch,mask)
    with patch('calibration_workflow._infer_pitch',return_value=measured):
        repaired=prepare_copy(assigned['project'],audio,root/'repaired',repair_pitch=True)
    repaired_data=project_data(repaired['project']);after=inspect_inputs(repaired['project'],audio,job=job)
    assert after['ready'] and after['pitch']['status']=='ok'
    assert repaired_data['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']!=track['mainGroup']['parameters']['pitchDelta']['points']
    for a,b in zip(track['mainGroup']['notes'],repaired_data['tracks'][0]['mainGroup']['notes']):
        assert all(a[k]==b[k] for k in ('onset','duration','pitch','lyrics'))
    assert file_hash(original)==original_hash
    raises(InterruptedError,prepare_copy,original,audio,root/'cancelled',cancel=lambda:(_ for _ in ()).throw(InterruptedError('cancel')))
    assert not (root/'cancelled/prepared.svp').exists()
    # Exercise the actual embedded CPU model, not only the synthetic result.
    real=prepare_copy(assigned['project'],audio,root/'real-repair',repair_pitch=True)
    assert inspect_inputs(real['project'],audio)['pitch']['status']=='ok'

    session=create_session(repaired['project'],audio,root/'session');spec=read_session(session)
    for control,token in spec['outputs'].items():
        sf.write(Path(spec['render_folder'])/(token+'.wav'),wave*.6*(10**(3/20) if control=='loudness' else 1),rate,subtype='FLOAT')
    states=[];fit=wait_and_fit(session,timeout=15,interval=.01,on_state=states.append)
    assert fit['calibration_fit']['voicebank_calibrated'] and Path(fit['neutral']).is_file()
    assert any('writing' in s['files'].values() for s in states) and states[-1]['phase']=='fitting'
    baseline=project_data(fit['neutral']);fitted=project_data(fit['output'])
    assert baseline['tracks'][0]['mainGroup']['notes']==fitted['tracks'][0]['mainGroup']['notes']
    assert baseline['tracks'][0]['mainGroup']['parameters']['pitchDelta']==fitted['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    assert not baseline['tracks'][0]['mainGroup']['parameters'].get('loudness')
    second=fit_session(session,output_name='calibrated-new.svp',strength=.4)
    assert latest_result(session)['output']==second['output'] and second['neutral']==fit['neutral']
    Path(second['neutral']).write_text('user edited comparison')
    third=fit_session(session,output_name='calibrated-third.svp',strength=.3)
    registered=json.loads((job/'expression-result.json').read_text(encoding='utf-8'))
    assert registered['tracks']['lead']['sha256']==file_hash(third['output'])
    assert third['neutral']!=second['neutral'] and Path(second['neutral']).read_text()=='user edited comparison'
    assert '气声输出' in recovery_hint('4 个声道') and '重新准备' in recovery_hint('原工程已改变')

    from PyQt5.QtWidgets import QApplication
    from app import Window
    from calibration_ui import CalibrationDialog
    qt=QApplication([]);owner=Window();owner.job=job;dialog=CalibrationDialog(owner);owner.calibration_dialog=dialog
    dialog.folder.setText(str(root));dialog.show();qt.processEvents()
    assert not dialog.review_inputs()['ready']
    dialog.start_session();assert dialog.worker is None and '准备条件' in dialog.status.text()
    with patch('calibration_ui.QFileDialog.getOpenFileName',return_value=(str(template),'')):
        dialog.choose_bank();assert dialog.worker.wait(8000);qt.processEvents()
    assert project_data(dialog.project.text())['tracks'][0]['mainRef']['database']['name']=='Demo Bank'
    imported=dialog.project.text()
    with patch('calibration_workflow._infer_pitch',return_value=measured):
        dialog.prepare_input(repair=True);assert dialog.worker.wait(8000);qt.processEvents()
    assert dialog.project.text()!=imported and dialog.review_inputs()['ready']
    def simulate_sv(*args,**kwargs):
        kwargs['on_state']({'phase':'fitting','files':{n:'validated' for n in spec['outputs']},'done':7,'total':7})
        return third
    with patch('calibration_session.wait_and_fit',side_effect=simulate_sv),patch('calibration_ui.QDesktopServices.openUrl',return_value=True) as opened:
        dialog.start_session();assert dialog.worker.wait(8000);qt.processEvents()
        assert dialog.open_neutral.isEnabled() and dialog.open_result.isEnabled()
        assert all(dialog.file_status.item(i,1).text()=='校验通过' for i in range(7))
        dialog.show_neutral();assert opened.called
    dialog.failed('试音有 4 个声道');assert '气声输出' in dialog.recovery.text()
    with patch('calibration_ui.QFileDialog.getExistingDirectory',return_value=str(session.parent)):
        dialog.resume_directory()
    assert dialog.result['output']==third['output'] and dialog.open_neutral.isEnabled() and dialog.recovery.isHidden()
    assert all(dialog.file_status.item(i,1).text()=='已找到（继续时复核）' for i in range(7))
    dialog.grab().save(str(ROOT/'work/test-results/calibration-self-service.png'))
    dialog.close();owner.close();qt.processEvents()
print('PASS: user preflight and lyric locations, bank-template isolation, real CPU pitch repair, immutable source, per-file states, neutral comparisons, registered parameter result, retry/latest-result recovery and GUI-only operation')
