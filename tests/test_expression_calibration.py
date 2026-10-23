"""Measured local response fitting: bounds, identifiability and score safety."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
import expression_calibration as cal
from common import ROOT
from vocal_expression import SV_QUARTER as Q

def raises(kind,fn,*args,**kwargs):
    try:fn(*args,**kwargs)
    except kind:return
    raise AssertionError('Expected '+kind.__name__)

n=200;t=np.arange(n)*.02
baseline=np.zeros((n,12));a=np.sin(t*1.5)*.7
directions={'loudness':np.eye(12)[0]*3,'tension':np.eye(12)[1]*2,
            'breathiness':np.eye(12)[2]*2,'voicing':np.eye(12)[3]*-.15,
            'gender':np.eye(12)[5]*4,'toneShift':np.eye(12)[6]*3}
probes={key:baseline+direction for key,direction in directions.items()}
steps={key:cal.CONTROLS[key][3] for key in probes}
reference=baseline+a[:,None]*directions['tension']+.4*directions['gender']+.6*directions['voicing']
active=(t>=.2)&(t<3.8)
curves,report=cal.fit_response(reference,baseline,probes,steps,strength=.6,active=active)
assert report['status']=='applied' and set(curves)=={'tension','gender','voicing'} and report['predicted_reduction']>.7
assert set(report['below_output_resolution'])=={'loudness','breathiness','toneShift'}
assert report['perceptual_improvement_verified'] is False
for name,values in curves.items():
    lo,hi,neutral,step=cal.CONTROLS[name]
    assert np.isfinite(values).all() and min(values)>=lo and max(values)<=hi
    assert np.all(values[~active]==neutral)
assert np.corrcoef(curves['tension'][active],a[active])[0,1]>.99
assert cal.fit_response(baseline,baseline,probes,steps)[0]=={}
assert cal.fit_response(reference,baseline,{'tension':baseline},{'tension':.3})[0]=={}
redundant={'tension':probes['tension'],'breathiness':probes['tension']}
_,r=cal.fit_response(reference,baseline,redundant,{'tension':.3,'breathiness':.3})
assert len(r['identifiable_parameters'])==1
raises(ValueError,cal.fit_response,reference,baseline,probes,steps,strength=True)
raises(RuntimeError,cal.fit_response,reference,baseline,probes,steps,cancel=lambda:(_ for _ in ()).throw(RuntimeError('cancel')))

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='calibration-') as folder:
    root=Path(folder)
    track={'name':'Lead','mainRef':{'blickOffset':0,'database':{'name':'Test Voice'},'voice':{'paramTension':.7}},
           'mainGroup':{'notes':[{'onset':0,'duration':8*Q,'pitch':57,'lyrics':'la','attributes':{'paramGender':.5}}],
                        'parameters':{'pitchDelta':{'mode':'linear','points':[0,0,Q,15]}}},
           'mixer':{'mute':True,'gainDecibel':4,'fxParams':{'test':1}}}
    score={'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
           'tracks':[track,{'mainRef':{'isInstrumental':True}}]}
    project=root/'original.svp';project.write_text(json.dumps(score),encoding='utf-8')
    profile=cal.create_probe_set(project,root/'probes')
    raises(FileNotFoundError,cal.read_profile,profile)
    spec=cal.read_profile(profile,require_audio=False)
    for name in ('neutral',*cal.CONTROLS):
        data=json.loads((profile.parent/('lead-'+name+'.svp')).read_text(encoding='utf-8'))
        voice=data['tracks'][0];params=voice['mainGroup']['parameters']
        assert len(data['tracks'])==1 and voice['renderEnabled']
        assert voice['mixer']=={'mute':False,'solo':False,'gainDecibel':0,'pan':0}
        assert cal.score_identity(voice,data)==cal.score_identity(track,score)
        assert params['pitchDelta']==track['mainGroup']['parameters']['pitchDelta']
        assert not voice['mainRef']['voice'] and not voice['mainGroup']['notes'][0]['attributes']
        changed=[key for key,(_,_,neutral,_) in cal.CONTROLS.items() if params[key]['points'][1]!=neutral]
        assert changed==([] if name=='neutral' else [name])
        sf.write(profile.parent/('lead-'+name+'.wav'),np.sin(np.arange(24000*4)*2*np.pi*220/24000)*.1,24000,subtype='FLOAT')
    sf.write(root/'reference.wav',np.sin(np.arange(24000*4)*2*np.pi*220/24000)*.1,24000,subtype='FLOAT')
    valid=cal.read_profile(profile);assert len(valid['tracks']['lead']['probes'])==6
    bad=json.loads(profile.read_text(encoding='utf-8'));bad['tracks']['lead']['database']=[]
    (root/'bad.json').write_text(json.dumps(bad));raises(ValueError,cal.read_profile,root/'bad.json')
    # Real WORLD/spectral extraction with independently measured level changes.
    notes=[(0,4,57)]
    times,x,mask,metadata=cal.cached_features(root/'reference.wav',notes,root/'cache.npz')
    assert x.shape==(len(times),12) and mask.sum()>100 and np.isfinite(x).all()
    with patch.object(cal,'acoustic_features',side_effect=AssertionError('Cache was ignored')):
        assert cal.cached_features(root/'reference.wav',notes,root/'cache.npz')[3]['cache_hit']
    sample,rate=sf.read(root/'reference.wav')
    sf.write(root/'gain-probe.wav',sample*10**(3/20),rate,subtype='FLOAT')
    sf.write(root/'gain-target.wav',sample*10**(1.5/20),rate,subtype='FLOAT')
    _,gain,gm,_=cal.acoustic_features(root/'gain-probe.wav',notes)
    _,target,tm,_=cal.acoustic_features(root/'gain-target.wav',notes)
    loudness,measurement=cal.fit_response(target,x,{'loudness':gain},{'loudness':3.},strength=1.,active=mask&gm&tm)
    assert measurement['status']=='applied' and abs(np.median(loudness['loudness'][mask&gm&tm])-1.5)<.1
    # Apply known responses, then verify the actual serialized score and pitch.
    def features(path,*args,**kwargs):
        name=Path(path).stem
        values=reference if name=='reference' else baseline if name=='lead-neutral' else probes[name.removeprefix('lead-')]
        return t,values,active.copy(),{}
    with patch.object(cal,'cached_features',side_effect=features):
        result=cal.apply_svp(project,[{'voice':'lead','path':root/'reference.wav'}],profile)
    written=json.loads(project.read_text(encoding='utf-8'))
    assert result['voicebank_calibrated'] and result['tracks'][0]['status']=='applied'
    assert cal.score_identity(written['tracks'][0],written)==cal.score_identity(track,score)
    assert written['tracks'][0]['mainGroup']['parameters']['pitchDelta']==track['mainGroup']['parameters']['pitchDelta']
    assert written['tracks'][1]==score['tracks'][1]
    quiet=root/'quiet.svp';quiet.write_text(json.dumps(score),encoding='utf-8')
    def silence(*args,**kwargs):return t,baseline,np.zeros(n,bool),{}
    with patch.object(cal,'cached_features',side_effect=silence):
        skipped=cal.apply_svp(quiet,[{'voice':'lead','path':root/'reference.wav'}],profile)
    assert not skipped['voicebank_calibrated'] and skipped['tracks'][0]['reason']=='insufficient_supported_frames'
    assert json.loads(quiet.read_text(encoding='utf-8'))==score
    wrong=copy.deepcopy(written);wrong['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points'][-1]=30
    assert cal.score_identity(wrong['tracks'][0],wrong)!=cal.score_identity(track,score)
    wrong=copy.deepcopy(written);wrong['tracks'][0]['mainGroup']['notes'][0]['pitch']=58
    project.write_text(json.dumps(wrong));raises(ValueError,cal.apply_svp,project,[{'voice':'lead','path':root/'reference.wav'}],profile)

    # Full native-score -> three editor exports -> measured SVP fit, using real
    # gain probes. No editor or voicebank is needed for this contract test.
    from types import SimpleNamespace
    from v2m_runtime import configure
    configure(ROOT/'dependencies/vocal2midi2')
    from inference.API.ustx_api import save_ustx
    from project_exports import export_job,project_signatures
    from common import load_config,file_hash
    job=root/'export-job';(job/'midi').mkdir(parents=True);(job/'audio').mkdir()
    wave=np.sin(np.arange(24000*4)*2*np.pi*220/24000)*.1
    sf.write(job/'audio/vocals.wav',wave*10**(1.5/20),24000,subtype='FLOAT')
    sf.write(job/'audio/instrumental.wav',wave*0,24000,subtype='FLOAT')
    save_ustx([SimpleNamespace(onset=.2,offset=3.8,pitch=57,lyric='la')],job/'midi/vocals.ustx',tempo=120)
    options={'voice_mode':'single','language':'zh','project_formats':['ustx','vsqx','svp'],
             'audio_formats':['wav'],'export_pitch':False,'include_instrumental':False,'tools':load_config(False)}
    export_job(job,options)
    saved=json.loads((job/'exports/voices.svp').read_bytes().removesuffix(b'\x00').decode('utf-8'))
    saved['tracks'][0]['mainRef']['database']['name']='Test Voice'
    selected=root/'assigned.svp';selected.write_text(json.dumps(saved),encoding='utf-8')
    fitting=cal.create_probe_set(selected,root/'integration-probes',voice='vocals')
    definition=json.loads(fitting.read_text(encoding='utf-8'))
    definition['tracks']['vocals']['probes']=[p for p in definition['tracks']['vocals']['probes'] if p['parameter']=='loudness']
    fitting.write_text(json.dumps(definition),encoding='utf-8')
    sf.write(fitting.parent/'vocals-neutral.wav',wave,24000,subtype='FLOAT')
    sf.write(fitting.parent/'vocals-loudness.wav',wave*10**(3/20),24000,subtype='FLOAT')
    old=project_signatures(job,options);native=file_hash(job/'midi/vocals.ustx')
    options['reference_calibration']=str(fitting)
    current=project_signatures(job,options)
    assert current['svp']!=old['svp'] and current['ustx']==old['ustx'] and current['vsqx']==old['vsqx']
    report=export_job(job,options)
    item=next(row for row in report['projects']['outputs'] if row['file'].endswith('.svp'))
    assert item['calibration_fit']['voicebank_calibrated']
    assert item['calibration_fit']['expression_density']['step_seconds']==.005
    density=item['expression_density']['tracks'][0]['controls']['loudness']
    assert density['max_changing_gap_ms']<=5.00001
    calibrated=json.loads((job/'exports/voices.svp').read_bytes().removesuffix(b'\x00').decode('utf-8'))
    params=calibrated['tracks'][0]['mainGroup']['parameters']
    assert params['loudness']['points'] and not params.get('pitchDelta',{}).get('points')
    assert file_hash(job/'midi/vocals.ustx')==native
    sf.write(job/'audio/vocals.wav',wave*10**(1./20),24000,subtype='FLOAT')
    assert project_signatures(job,options)['svp']!=current['svp'],'Reference audio missing from fit receipt'
print('PASS: six-control measured fit, response rank/weakness rejection, neutral guards, real features/cache, single-control probes and score/pitch preservation')
