"""Pure adapter transforms, cancellation, equivalence and retained SVP files."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'src'))
import numpy as np
import soundfile as sf
import calibration_workflow
import expression_calibration as calibration
import vocal_expression
from calibration_workflow import retain_parameters,retain_project
from expression_calibration import CONTROLS
from vocal_expression import SV_QUARTER as Q


def raises(kind,function,*args,**kwargs):
    try:function(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)


def stop(*args,**kwargs):
    raise InterruptedError('cancelled')


_real_json_dumps=json.dumps
def serialized(data,suffix=False):
    return _real_json_dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf-8')+(b'\x00' if suffix else b'')


def reject_project_serialization(*args,**kwargs):
    if kwargs.get('ensure_ascii') is False:raise AssertionError('pure API serialized its project')
    return _real_json_dumps(*args,**kwargs)


def reference_project():
    note={'onset':0,'duration':8*Q,'pitch':60,'lyrics':'ah','attributes':{'voiceColor':7}}
    lead={'name':'Lead','renderEnabled':True,
        'mainRef':{'blickOffset':0,'database':{'name':'Reference Bank'},'voice':{'keep':'lead'}},
        'mainGroup':{'notes':[note],'parameters':{
            'pitchDelta':{'mode':'linear','points':[0,0,4*Q,18,8*Q,0]},
            'vibratoEnv':{'mode':'linear','points':[0,.2,8*Q,.7]}}}}
    instrumental={'name':'Piano','mainRef':{'isInstrumental':True,'audio':{'filename':'piano.wav'}}}
    return {'version':153,'time':{'tempo':[{'position':0,'bpm':120}],
        'meter':[{'index':0,'numerator':4,'denominator':4}]},'tracks':[lead,instrumental]}


def calibration_project():
    lead={'name':'Lead','renderEnabled':True,
        'mainRef':{'blickOffset':0,'database':{'name':'Target Bank'},
            'voice':{'paramLoudness':.8,'paramTension':.4,'keepVoice':True}},
        'mainGroup':{'notes':[{'onset':0,'duration':8*Q,'pitch':60,'lyrics':'ah',
            'attributes':{'paramTension':.3,'paramGender':.2,'voiceColor':3}}],
            'parameters':{
                'pitchDelta':{'mode':'linear','points':[0,0,4*Q,12,8*Q,0]},
                'vibratoEnv':{'mode':'linear','points':[0,.3,8*Q,.6]},
                'loudness':{'mode':'linear','points':[0,-2,8*Q,1]},
                'tension':{'mode':'linear','points':[0,-.2,8*Q,.3]},
                'gender':{'mode':'linear','points':[0,-.1,8*Q,.2]}}}}
    backing={'name':'Backing','renderEnabled':True,
        'mainRef':{'blickOffset':0,'database':{'name':'Other Bank'},
            'voice':{'paramLoudness':-.4,'keepBacking':True}},
        'mainGroup':{'notes':[{'onset':0,'duration':4*Q,'pitch':55,'lyrics':'oh',
            'attributes':{'paramBreathiness':.25,'voiceColor':9}}],
            'parameters':{'pitchDelta':{'mode':'linear','points':[0,0,4*Q,5]},
                'tension':{'mode':'linear','points':[0,-.4,4*Q,.2]},
                'unknownBacking':{'mode':'linear','points':[0,1,4*Q,2]}}}}
    instrumental={'name':'Piano','mainRef':{'isInstrumental':True,'audio':{'filename':'piano.wav'}}}
    return {'version':153,'time':{'tempo':[{'position':0,'bpm':120}],
        'meter':[{'index':0,'numerator':4,'denominator':4}]},'tracks':[lead,backing,instrumental]}


def retained_project(*,source=False):
    note={'onset':0,'duration':4*Q,'pitch':62,'lyrics':'la'}
    if source:note['attributes']={'paramTension':.35,'paramGender':.2,'keepNote':4}
    voice={'paramLoudness':.1,'paramTension':.25,'keepVoice':'source' if source else 'target'}
    curves={'pitchDelta':{'mode':'linear','points':[0,0,4*Q,9]},
        'loudness':{'mode':'linear','points':[0,.5,4*Q,.9]},
        'tension':{'mode':'linear','points':[0,-.3,4*Q,-.1]}}
    if not source:
        curves['gender']={'mode':'linear','points':[0,.2,4*Q,.4]}
    track={'name':'Lead','renderEnabled':True,
        'mainRef':{'blickOffset':0,'database':{'name':'Retained Bank','language':'japanese'},'voice':voice},
        'mainGroup':{'notes':[note],'parameters':curves}}
    instrumental={'name':'Piano','mainRef':{'isInstrumental':True,'audio':{'filename':'piano.wav'}}}
    return {'version':153,'time':{'tempo':[{'position':0,'bpm':120}],
        'meter':[{'index':0,'numerator':4,'denominator':4}]},'tracks':[instrumental,track]}


temp_root=Path(os.environ.get('BVWA_TEST_TEMP',APP/'work/temp'))
temp_root.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='bvwa-tuning-transforms-',dir=temp_root) as folder:
    root=Path(folder)
    rate=24000
    seconds=5
    times=np.arange(rate*seconds)/rate
    level=np.full(len(times),-100.)
    for start,end,low,high in ((.15,.95,-28.,-17.),(1.15,1.95,-19.,-10.),(2.15,2.95,-27.,-13.),(3.15,3.95,-22.,-11.)):
        active=(times>=start)&(times<end)
        level[active]=np.linspace(low,high,int(active.sum()))
    wave=(10**(level/20)*np.sin(times*2*np.pi*220)).astype('float32')
    source=root/'reference.wav'
    sf.write(source,wave,rate,subtype='FLOAT')

    # Real sine-wave energy frames exercise the production loudness analyser;
    # only the later voicebank calibration extraction is mocked below.
    project=reference_project()
    untouched=copy.deepcopy(project)
    sources=[{'voice':'lead','path':source}]
    with patch('calibration_refinement.finalize_expression',side_effect=AssertionError('deferred finalizer ran')), \
         patch.object(vocal_expression.json,'dumps',side_effect=AssertionError('pure API serialized')):
        transformed,report=vocal_expression.apply_project(project,sources,density_mode='dense',finalize=False)
    assert project==untouched and transformed is not project
    assert report['deferred']==['expression_density']
    assert report['expression_density']['status']=='deferred'
    assert report['tracks'][0]['parameter_status']['loudness']['status']=='applied'
    assert report['tracks'][0]['parameter_status']['loudness']['has_variation']
    assert report['tracks'][0]['samples_preserved']
    assert transformed['time']==project['time']
    assert transformed['tracks'][0]['mainGroup']['notes']==project['tracks'][0]['mainGroup']['notes']
    assert transformed['tracks'][0]['mainGroup']['parameters']['pitchDelta']==project['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    assert transformed['tracks'][0]['mainGroup']['parameters']['vibratoEnv']==project['tracks'][0]['mainGroup']['parameters']['vibratoEnv']
    assert transformed['tracks'][1]==project['tracks'][1]
    assert transformed['tracks'][0]['mainRef']==project['tracks'][0]['mainRef']

    transform_file=root/'reference-transform.svp'
    original_bytes=serialized(project,True)
    transform_file.write_bytes(original_bytes)
    serializations=[]
    def count_dumps(*args,**kwargs):
        if kwargs.get('ensure_ascii') is False:serializations.append(1)
        return _real_json_dumps(*args,**kwargs)
    with patch('calibration_refinement.finalize_expression',side_effect=AssertionError('deferred finalizer ran')), \
         patch.object(vocal_expression.json,'dumps',side_effect=count_dumps):
        wrapped_report=vocal_expression.apply_svp(transform_file,sources,density_mode='dense',finalize=False)
    assert transform_file.read_bytes()==serialized(transformed,True)
    assert len(serializations)==1
    assert wrapped_report==report

    transform_file.write_bytes(original_bytes)
    raises(InterruptedError,vocal_expression.apply_svp,transform_file,sources,.6,stop,
        density_mode='dense',finalize=False)
    assert transform_file.read_bytes()==original_bytes
    raises(InterruptedError,vocal_expression.apply_project,project,sources,.6,stop,
        density_mode='dense',finalize=False)
    assert project==untouched

    # Default wrapper behavior still performs one real final density pass.
    with patch.object(vocal_expression.json,'dumps',side_effect=AssertionError('pure API serialized')):
        finalized,finished=vocal_expression.apply_project(project,sources,density_mode='dense')
    assert finished['deferred']==[] and finished['expression_density']['status']!='deferred'
    transform_file.write_bytes(original_bytes)
    default_report=vocal_expression.apply_svp(transform_file,sources,density_mode='dense')
    assert transform_file.read_bytes()==serialized(finalized,True)
    assert default_report==finished
    assert project==untouched

    profile_path=root/'calibration.json'
    lead_audio=root/'lead-neutral.wav'
    loudness_audio=root/'lead-loudness.wav'
    sf.write(lead_audio,np.zeros(rate*seconds,dtype='float32'),rate,subtype='FLOAT')
    sf.write(loudness_audio,np.zeros(rate*seconds,dtype='float32'),rate,subtype='FLOAT')
    calibrated_project=calibration_project()
    calibrated_untouched=copy.deepcopy(calibrated_project)
    lead=calibrated_project['tracks'][0]
    profile={'schema':1,'fit_strategy':'match','tracks':{'lead':{
        'score_sha256':calibration.score_identity(lead,calibrated_project),
        'database':{'name':'Target Bank','language':'japanese'},'voice':{'vocalModePreset':'clear'},
        'render_channel_mode':'standard','baseline':lead_audio.name,
        'probes':[{'parameter':'loudness','step':3.,'audio':loudness_audio.name}]}}}
    profile_path.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    feature_times=np.arange(200)*.02
    feature_active=(feature_times>=.2)&(feature_times<3.8)
    baseline=np.zeros((len(feature_times),12))
    probe={'loudness':baseline+np.eye(12)[0]*3.}
    target=baseline.copy();target[:,0]=2.
    def fake_features(audio,*args,**kwargs):
        stem=Path(audio).stem
        values=target if stem=='reference' else baseline if stem=='lead-neutral' else probe['loudness']
        return feature_times,values,feature_active.copy(),{}
    calibrated_sources=[{'voice':'lead','path':source},{'voice':'backing','path':source}]
    # WORLD/spectral extraction is mocked here because the solver, curve
    # installation, bias cleanup and finalize deferral are the adapter contract.
    with patch.object(calibration,'cached_features',side_effect=fake_features), \
         patch('calibration_refinement.finalize_expression',side_effect=AssertionError('deferred finalizer ran')), \
         patch.object(calibration.json,'dumps',side_effect=reject_project_serialization):
        calibrated,fit_report=calibration.apply_project(calibrated_project,calibrated_sources,profile_path,
            density_mode='dense',finalize=False)
    assert calibrated_project==calibrated_untouched and calibrated is not calibrated_project
    assert fit_report['deferred']==['expression_density'] and fit_report['time_unchanged']
    assert fit_report['voicebank_calibrated'] and fit_report['tracks'][0]['status']=='applied'
    assert calibrated['time']==calibrated_project['time']
    assert calibrated['tracks'][2]==calibrated_project['tracks'][2]
    assert calibrated['tracks'][1]==calibrated_project['tracks'][1]
    old_params=calibrated_project['tracks'][0]['mainGroup']['parameters']
    new_params=calibrated['tracks'][0]['mainGroup']['parameters']
    assert new_params['pitchDelta']==old_params['pitchDelta']
    assert new_params['vibratoEnv']==old_params['vibratoEnv']
    assert new_params['loudness']['points']
    assert 'tension' not in new_params and 'gender' not in new_params
    cleanup=fit_report['tracks'][0]['bias_cleanup']
    assert set(cleanup['control_curves_removed'])=={'loudness','tension','gender'}
    assert cleanup['track_voice']==['paramLoudness','paramTension']
    assert cleanup['removed_count']>=4
    assert calibrated['tracks'][0]['mainRef']['database']['name']=='Target Bank'
    assert calibrated['tracks'][0]['mainGroup']['notes'][0]['attributes']=={'voiceColor':3}

    calibration_file=root/'calibration-transform.svp'
    calibration_bytes=serialized(calibrated_project,True)
    calibration_file.write_bytes(calibration_bytes)
    serializations=[]
    with patch.object(calibration,'cached_features',side_effect=fake_features), \
         patch('calibration_refinement.finalize_expression',side_effect=AssertionError('deferred finalizer ran')), \
         patch.object(calibration.json,'dumps',side_effect=count_dumps):
        wrapped_fit=calibration.apply_svp(calibration_file,calibrated_sources,profile_path,
            density_mode='dense',finalize=False)
    assert calibration_file.read_bytes()==serialized(calibrated,True)
    assert len(serializations)==1
    assert wrapped_fit==fit_report
    calibration_file.write_bytes(calibration_bytes)
    raises(InterruptedError,calibration.apply_svp,calibration_file,calibrated_sources,profile_path,.6,stop,
        density_mode='dense',finalize=False)
    assert calibration_file.read_bytes()==calibration_bytes
    raises(InterruptedError,calibration.apply_project,calibrated_project,calibrated_sources,profile_path,.6,stop,
        density_mode='dense',finalize=False)
    assert calibrated_project==calibrated_untouched

    # Real .svp files exercise retain_parameters' hash/atomic-I/O wrapper; the
    # target notes deliberately omit attributes so source offsets must setdefault.
    target_project,prior_project=retained_project(),retained_project(source=True)
    target_untouched,prior_untouched=copy.deepcopy(target_project),copy.deepcopy(prior_project)
    retained,retain_report=retain_project(target_project,prior_project)
    assert target_project==target_untouched and prior_project==prior_untouched
    assert retained is not target_project
    assert retain_report['target_track_index']==1 and retain_report['source_track_index']==1
    assert retained['tracks'][0]==target_project['tracks'][0]
    assert retained['tracks'][1]['mainGroup']['notes'][0]['onset']==target_project['tracks'][1]['mainGroup']['notes'][0]['onset']
    assert retained['tracks'][1]['mainGroup']['notes'][0]['pitch']==62
    assert retained['tracks'][1]['mainGroup']['notes'][0]['attributes']=={
        'paramTension':.35,'paramGender':.2}
    assert retained['tracks'][1]['mainRef']['voice']=={
        'keepVoice':'target','paramLoudness':.1,'paramTension':.25}
    assert retained['tracks'][1]['mainGroup']['parameters']['pitchDelta']==prior_project['tracks'][1]['mainGroup']['parameters']['pitchDelta']
    assert retained['tracks'][1]['mainGroup']['parameters']['loudness']==prior_project['tracks'][1]['mainGroup']['parameters']['loudness']
    assert retained['tracks'][1]['mainGroup']['parameters']['gender']==target_project['tracks'][1]['mainGroup']['parameters']['gender']
    assert set(retain_report['controls'])=={'loudness','tension'} and not retain_report['new_fit']
    assert set(retain_report['bias_cleanup']['track_voice_removed'])=={'paramLoudness','paramTension'}
    assert set(retain_report['bias_cleanup']['track_voice_restored'])=={'paramLoudness','paramTension'}
    assert retain_report['bias_cleanup']['note_offsets_removed']==[]
    assert {row['key'] for row in retain_report['bias_cleanup']['note_offsets_restored']}=={
        'paramTension','paramGender'}

    target_file=root/'retained-target.svp'
    prior_file=root/'retained-source.svp'
    target_file.write_bytes(serialized(target_project,True))
    prior_file.write_bytes(serialized(prior_project))
    prior_bytes=prior_file.read_bytes()
    wrapper_retain=retain_parameters(target_file,prior_file)
    assert json.loads(target_file.read_bytes().removesuffix(b'\x00'))==retained
    assert prior_file.read_bytes()==prior_bytes
    assert wrapper_retain['source_sha256'] and wrapper_retain['controls']==retain_report['controls']
    assert wrapper_retain['bias_cleanup']==retain_report['bias_cleanup']

    before_target=target_file.read_bytes()
    def changed_during_retain(*args,**kwargs):
        prior_file.write_bytes(prior_file.read_bytes()+b' ')
        return retained,retain_report
    with patch.object(calibration_workflow,'retain_project',side_effect=changed_during_retain):
        raises(ValueError,retain_parameters,target_file,prior_file)
    assert target_file.read_bytes()==before_target

print('PASS: real reference loudness, pure/wrapper equivalence, NUL preservation, finalize deferral, cancellation, mocked calibration solver wiring, non-target safety, bias audit and retained real-file fixtures')
