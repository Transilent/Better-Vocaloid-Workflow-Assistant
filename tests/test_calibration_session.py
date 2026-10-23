"""Seven-track preparation, stable-file collection, real fitting and UI lifecycle."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from common import ROOT,file_hash
from vocal_expression import SV_QUARTER as Q
from expression_calibration import CONTROLS,read_profile,score_identity
from calibration_session import create_session,read_session,project_data,RenderCollector,fit_session,wait_and_fit,writer_closed,matches_task

def raises(kind,function,*args,**kwargs):
    try:function(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='auto-calibration-') as directory:
    root=Path(directory);rate=24000
    wave=.1*np.sin(np.arange(rate*4)*2*np.pi*220/rate)
    source=root/'original.svp';reference=root/'reference.wav'
    sf.write(reference,np.r_[wave*10**(1.5/20),np.zeros(rate)],rate,subtype='FLOAT')
    track={'name':'Lead','mainRef':{'blickOffset':0,'mute':True,'database':{'name':'Test Voice'},'groupID':'source-group'},
           'mainGroup':{'uuid':'source-group','notes':[{'onset':Q//2,'duration':5*Q,'pitch':57,'lyrics':'la'}],
                        'parameters':{'pitchDelta':{'mode':'linear','points':[0,0,Q,4]}}}}
    second=copy.deepcopy(track);second['name']='Backing';second['mainGroup']['notes'][0]['pitch']=60
    score={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
           'tracks':[track,second,{'mainRef':{'isInstrumental':True,'audio':{'filename':'instrumental.wav','duration':5}}}]}
    source.write_text(json.dumps(score),encoding='utf-8');original=file_hash(source)
    session=create_session(source,reference,root/'session',strength=.6)
    spec=read_session(session);batch=project_data(session.parent/'calibration.svp')
    assert len(batch['tracks'])==7 and not list(session.parent.glob('lead-*.svp'))
    assert len({t['mainGroup']['uuid'] for t in batch['tracks']})==7
    assert len({t['name'] for t in batch['tracks']})==7
    assert batch['renderConfig']['exportMixDown'] is False and batch['renderConfig']['bypassEffects'] is True
    from project_exports import library
    converters,_,SingingTrack,_=library()
    parsed=converters['svp'].load(session.parent/'calibration.svp',{'import_pitch':True,'import_instrumental_track':False})
    assert len(parsed.track_list)==7 and all(isinstance(t,SingingTrack) and len(t.note_list)==1 for t in parsed.track_list)
    for name,trial in zip(('neutral',*CONTROLS),batch['tracks']):
        assert trial['renderEnabled'] and not trial['mainRef']['mute'] and not trial['mixer']['mute']
        assert trial['mainRef']['groupID']==trial['mainGroup']['uuid']
        assert score_identity(trial,batch)==score_identity(track,score)
        assert trial['mainGroup']['parameters']['pitchDelta']==track['mainGroup']['parameters']['pitchDelta']
        changed=[key for key,(_,_,neutral,_) in CONTROLS.items() if trial['mainGroup']['parameters'][key]['points'][1]!=neutral]
        assert changed==([] if name=='neutral' else [name])
    now=[0.];collector=RenderCollector(session,clock=lambda:now[0])
    assert len(collector.scan()[1])==7
    folder=Path(spec['render_folder'])
    def render_files(where,tokens):
        for control,token in tokens.items():
            sf.write(where/('prefix_'+token+'.wav'),wave*10**(3/20) if control=='loudness' else wave,rate,subtype='FLOAT')
    render_files(folder,spec['outputs']);sf.write(folder/'unrelated_MixDown.wav',wave,rate)
    assert not collector.scan()[0]
    now[0]=2.1;found,waiting=collector.scan();assert not waiting and len(found)==7
    test_file=found['neutral']
    if sys.platform=='win32':
        with test_file.open('rb'):assert not writer_closed(test_file)
        assert writer_closed(test_file)
    duplicate=folder/('duplicate_'+spec['outputs']['neutral']+'.wav');shutil.copy2(test_file,duplicate)
    raises(ValueError,collector.scan);duplicate.unlink()
    # Same names with a truncated final note are rejected before fitting.
    bad=found['gender'];sf.write(bad,wave[:rate],rate,subtype='FLOAT')
    collector.scan();now[0]+=3;raises(ValueError,collector.collect)
    sf.write(bad,wave,rate,subtype='FLOAT');collector.scan();now[0]+=3
    receipt,waiting=collector.collect();assert receipt and not waiting
    profile=read_profile(session.parent/'calibration.json');assert profile['tracks']['lead']['baseline']==str(test_file)
    result=fit_session(session)
    assert result['calibration_fit']['voicebank_calibrated'] and file_hash(source)==original
    fitted=project_data(result['output']);assert score_identity(fitted['tracks'][0],fitted)==score_identity(track,score)
    assert fitted['tracks'][1]==score['tracks'][1]
    assert Path(fitted['tracks'][2]['mainRef']['audio']['filename'])==root/'instrumental.wav'
    assert fitted['tracks'][0]['mainGroup']['parameters']['loudness']['points']
    assert not list(session.parent.glob('*.pending.svp'))
    task=root/'task';(task/'exports').mkdir(parents=True);(task/'audio').mkdir()
    shutil.copy2(source,task/'exports/voices.svp');shutil.copy2(reference,task/'audio/lead.wav')
    (task/'request.json').write_text(json.dumps({'voice_mode':'dual'}))
    assert matches_task(session.parent/'calibration.json',task)
    assert not matches_task(session.parent/'calibration.json',None)
    existing=file_hash(result['output']);raises(FileExistsError,fit_session,session)
    assert file_hash(result['output'])==existing
    reused=fit_session(session,output_name='calibrated-preserve.svp',strength=.4)
    assert reused['calibration_fit']['strength']==.4 and Path(reused['output']).is_file()
    assert file_hash(result['output'])==existing and (session.parent/'calibrated-preserve.fit-report.json').is_file()
    raises(FileExistsError,fit_session,session,output_name='calibrated-preserve.svp')
    for name in ('../escape.svp','nested/escape.svp','C:escape.svp','not-midi.mid'):
        raises(ValueError,fit_session,session,output_name=name)
    raises(ValueError,fit_session,session,output_name='bad.svp',strength=True)
    auto=create_session(source,reference,root/'auto',render_folder=root/'custom-renders')
    auto_spec=read_session(auto);render_files(Path(auto_spec['render_folder']),auto_spec['outputs'])
    progress=[];automatic=wait_and_fit(auto,progress=progress.append,interval=.05,timeout=15)
    assert Path(automatic['output']).is_file() and any('7 / 7' in message for message in progress)
    missing=create_session(source,reference,root/'missing')
    raises(TimeoutError,wait_and_fit,missing,timeout=.05,interval=.01)
    raises(InterruptedError,wait_and_fit,missing,cancel=lambda:(_ for _ in ()).throw(InterruptedError('cancel')))
    assert not (missing.parent/'calibrated.svp').exists()
    sf.write(reference,wave*.2,rate,subtype='FLOAT');raises(ValueError,RenderCollector(missing).collect)

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtTest import QTest
    from app import Window
    from calibration_ui import CalibrationDialog
    app=QApplication([]);owner=Window();dialog=CalibrationDialog(owner);owner.calibration_dialog=dialog
    dialog.project.setText(str(source));dialog.reference.setText(str(reference));dialog.folder.setText(str(root))
    dialog.show();app.processEvents()
    dialog.grab().save(str(ROOT/'work/test-results/calibration-dialog.png'))
    complete={'output':result['output'],'profile':result['profile'],'calibration_fit':{'voicebank_calibrated':True}}
    def fake_wait(*args,**kwargs):
        for _ in range(12):time.sleep(.01);kwargs['cancel']()
        return complete
    with patch('calibration_session.create_session',return_value=session),patch('calibration_session.wait_and_fit',side_effect=fake_wait),patch('calibration_ui.QDesktopServices.openUrl',return_value=True):
        dialog.start_session();assert dialog.busy() and not dialog.form.isEnabled()
        assert owner.updates.active_operations()
        owner.storage.start();assert owner.storage.worker is None
        assert dialog.worker.wait(2000);app.processEvents()
        assert dialog.result and not owner.calibration_enabled.isChecked() and owner.calibration_file.text()==result['profile']
    dialog.close();app.processEvents()
    restored=CalibrationDialog(owner)
    with patch('calibration_ui.QFileDialog.getOpenFileName',return_value=(str(missing),'')):
        restored.resume_session()
    assert restored.session==missing and restored.start.text()=='继续等待并拟合'
    with patch('calibration_ui.QFileDialog.getOpenFileName',return_value=(str(session),'')):
        restored.resume_session()
    assert restored.result and restored.open_result.isEnabled() and restored.start.isEnabled()
    restored.strength.setValue(40)
    assert restored.session==session and restored.start.text()=='重新拟合 · 复用试音'
    restored.close()
    cancelled=CalibrationDialog(owner);owner.calibration_dialog=cancelled
    cancelled.project.setText(str(source));cancelled.reference.setText(str(reference));cancelled.folder.setText(str(root))
    with patch('calibration_session.create_session',return_value=session),patch('calibration_session.wait_and_fit',side_effect=fake_wait),patch('calibration_ui.QDesktopServices.openUrl',return_value=True):
        cancelled.start_session();cancelled.reject()
        assert cancelled.worker.wait(2000);app.processEvents()
        assert cancelled.result is None and not cancelled.busy()
    owner.close();app.processEvents()
print('PASS: one seven-track project, unique render tokens, stable/closed WAV detection, truncation/duplicate guards, actual WORLD fitting with outro, automatic collection-to-fit, cancellation and UI interlocks')
