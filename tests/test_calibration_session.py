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
from common import ROOT,file_hash,native_path
from vocal_expression import SV_QUARTER as Q
from expression_calibration import CONTROLS,read_profile,score_identity,signature
from calibration_session import create_session,read_session,project_data,RenderCollector,fit_session,wait_and_fit,writer_closed,matches_task,verified_fit_result
from calibration_presets import LocalCalibrationBindings,resolve_trials,save_fit_preset
from preset_library import PresetLibrary

def raises(kind,function,*args,**kwargs):
    try:function(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)

with tempfile.TemporaryDirectory(dir=native_path(ROOT/'work'),prefix='auto-calibration-') as directory:
    root=ROOT/'work'/Path(directory).name;rate=24000
    wave=.1*np.sin(np.arange(rate*4)*2*np.pi*220/rate)
    source=root/'original.svp';reference=root/'reference.wav'
    sf.write(reference,np.r_[wave*10**(1.5/20),np.zeros(rate)],rate,subtype='FLOAT')
    track={'name':'Lead','mainRef':{'blickOffset':0,'mute':False,'database':{'name':'Test Voice'},'groupID':'source-group'},
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
    library_store=PresetLibrary(root/'presets')
    bindings=LocalCalibrationBindings(library_store.root)
    ready=verified_fit_result(session)
    entry=save_fit_preset(library_store,bindings,session,ready,'Measured voicebank')
    doc=library_store.get_document(entry['id'],entry['version'])
    assert doc['payload']['voicebank']=='Test Voice'
    assert bindings.get(entry['sha256'])['reference']==str(reference)
    bundle=root/'test-voice.vwa-preset.json'
    library_store.export_file(entry['id'],entry['version'],bundle)
    assert all(str(path).encode() not in bundle.read_bytes() for path in (source,reference,folder))
    imported_store=PresetLibrary(root/'imported-presets')
    copied=imported_store.import_file(bundle,expected_revision=imported_store.snapshot()['revision'])['entry']
    imported=LocalCalibrationBindings(imported_store.root)
    assert copied['sha256']==entry['sha256'] and not imported.get(copied['sha256'])
    new_renders=root/'moved-renders'
    new_renders.mkdir()
    for path in found.values():
        shutil.copy2(path,new_renders/path.name)
    assert len(resolve_trials(doc,new_renders))==7
    imported.put(copied['sha256'],new_renders)
    profile_path,reference_path=imported.profile(doc,copied['sha256'],score,0)
    assert not reference_path and imported.profile(doc,copied['sha256'],score,0)[0]==profile_path
    assert signature(profile_path)['audio'][0][1]==receipt['neutral']['sha256']
    wrong_bank=copy.deepcopy(score)
    wrong_bank['tracks'][0]['mainRef']['database']['name']='Other Voice'
    raises(ValueError,imported.profile,doc,copied['sha256'],wrong_bank,0)
    wrong_score=copy.deepcopy(score)
    wrong_score['tracks'][0]['mainGroup']['notes'][0]['pitch']+=1
    raises(ValueError,imported.profile,doc,copied['sha256'],wrong_score,0)
    changed_path=new_renders/test_file.name
    sf.write(changed_path,wave*.9,rate,subtype='FLOAT')
    raises(ValueError,signature,profile_path)
    raises(ValueError,resolve_trials,doc,new_renders)
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

print('PASS: seven-track session, stable WAV detection, fitting, portable preset bindings, retry and cancellation')
