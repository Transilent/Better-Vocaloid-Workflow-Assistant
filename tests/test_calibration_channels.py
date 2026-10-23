"""SV appended aspiration is interleaved, never an ordinary stereo downmix."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
import expression_calibration as calibration
from vocal_timbre import select_channels
from vocal_expression import SV_QUARTER as Q
from calibration_session import create_session,read_session,RenderCollector,fit_session,project_data
from common import ROOT,file_hash

def raises(kind,fn,*args,**kwargs):
    try:fn(*args,**kwargs)
    except kind as error:return str(error)
    raise AssertionError('Expected '+kind.__name__)

rate=24000;t=np.arange(rate*4)/rate
main=.1*np.sin(2*np.pi*220*t);noise=.3*np.random.default_rng(42).normal(size=len(t))
quad=np.column_stack([main,noise,main*.8,-noise*.7]).astype('float32')
assert np.array_equal(select_channels(quad,'sv-extra'),quad[:,[0,2]])
assert np.array_equal(select_channels(quad,'sv-isolated'),quad[:,[0,2]]+quad[:,[1,3]])
assert np.array_equal(select_channels(quad[:,:2],'sv-extra'),quad[:,:1])
assert np.array_equal(select_channels(quad[:,:2],'sv-isolated'),quad[:,:1]+quad[:,1:2])
raises(ValueError,select_channels,quad)
raises(ValueError,select_channels,quad,'guess')

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='sv-channels-') as folder:
    root=Path(folder);extra=root/'extra.wav';plain=root/'plain.wav'
    sf.write(extra,quad,rate,subtype='FLOAT');sf.write(plain,quad[:,[0,2]],rate,subtype='FLOAT')
    before=file_hash(extra)
    notes=[(.25,3.75,57)]
    a=calibration.acoustic_features(extra,notes,channel_mode='sv-extra')
    b=calibration.acoustic_features(plain,notes)
    assert np.array_equal(a[0],b[0]) and np.allclose(a[1],b[1],atol=1e-8) and np.array_equal(a[2],b[2])
    cache=root/'features.npz'
    assert not calibration.cached_features(extra,notes,cache,channel_mode='sv-extra')[3]['cache_hit']
    assert calibration.cached_features(extra,notes,cache,channel_mode='sv-extra')[3]['cache_hit']
    assert not calibration.cached_features(extra,notes,cache,channel_mode='sv-isolated')[3]['cache_hit']
    assert file_hash(extra)==before
    score={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
           'tracks':[{'name':'Lead','mainRef':{'database':{'name':'Test Voice'},'blickOffset':0},
                      'mainGroup':{'notes':[{'onset':Q//2,'duration':6*Q,'pitch':57,'lyrics':'la'}],
                                   'parameters':{'pitchDelta':{'mode':'linear','points':[0,0,Q,5]}}}}]}
    project=root/'source.svp';project.write_text(json.dumps(score),encoding='utf-8')
    target=root/'reference.wav';sf.write(target,quad[:,[0,2]]*10**(1.5/20),rate,subtype='FLOAT')
    session=create_session(project,target,root/'session')
    spec=read_session(session);renders=Path(spec['render_folder'])
    for control,token in spec['outputs'].items():
        sf.write(renders/('calibration_'+token+'.wav'),quad*10**(3/20) if control=='loudness' else quad,rate,subtype='PCM_24',format='WAVEX')
    error=raises(ValueError,RenderCollector(session,stable_seconds=0).collect)
    assert '4 个声道' in error and '试音过短' not in error
    original={p:file_hash(p) for p in renders.glob('*.wav')}
    receipt,waiting=RenderCollector(session,stable_seconds=0,channel_mode='sv-extra').collect()
    assert receipt and not waiting
    assert read_session(session)['render_channel_mode']=='sv-extra'
    profile=calibration.read_profile(session.parent/'calibration.json')
    assert profile['tracks']['lead']['render_channel_mode']=='sv-extra'
    fitted=fit_session(session)
    assert fitted['calibration_fit']['voicebank_calibrated']
    result=project_data(fitted['output'])
    assert result['tracks'][0]['mainGroup']['notes']==score['tracks'][0]['mainGroup']['notes']
    assert result['tracks'][0]['mainGroup']['parameters']['pitchDelta']==score['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    assert all(file_hash(p)==digest for p,digest in original.items())
    assert not list(session.parent.glob('*.wav')),'No converted audio copies should be created'
    from PyQt5.QtWidgets import QApplication
    from app import Window
    from calibration_ui import CalibrationDialog
    app=QApplication([]);owner=Window();dialog=CalibrationDialog(owner)
    assert dialog.strength.value()==60 and dialog.strength.suffix()==' %'
    with patch('calibration_ui.QFileDialog.getOpenFileName',return_value=(str(session),'')):
        dialog.resume_session()
    assert dialog.channels.currentData()=='sv-extra'
    assert dialog.result and dialog.open_result.isEnabled()
    assert dialog.rect().contains(dialog.open_result.geometry().bottomRight())
    dialog.failed('试音有 4 个声道');assert dialog.status.property('role')=='error'
    dialog.set_status('继续分析');assert dialog.status.property('role')=='muted'
    dialog.show();app.processEvents()
    dialog.grab().save(str(ROOT/'work/test-results/calibration-channels.png'))
    dialog.close();owner.close();app.processEvents()
print('PASS: interleaved 2/4-channel main/aspiration mapping, no double counting, real WORLD equivalence, cache-mode invalidation, explicit channel errors, real WAVEX fitting, no audio copies and UI restore')
