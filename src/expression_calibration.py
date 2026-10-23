"""Optional measured voicebank responses for local SVP parameter fitting."""
import copy
import json
import math
from pathlib import Path
import hashlib
import re

CONTROLS = {
    'loudness': (-48., 12., 0., 3.), 'tension': (-1., 1., 0., .3),
    'breathiness': (-1., 1., 0., .3), 'voicing': (0., 1., 1., -.2),
    'gender': (-1., 1., 0., .25), 'toneShift': (-800., 800., 0., 200.)}
SCHEMA = 1


def score_identity(track, data):
    notes=[{key:n.get(key,'' if key in ('lyrics','phonemes') else 0) for key in ('onset','duration','pitch','lyrics','phonemes')}
           for n in track.get('mainGroup',{}).get('notes',[])]
    parameters=track.get('mainGroup',{}).get('parameters',{})
    values = [notes, data.get('time',{}).get('tempo'),
              track.get('mainRef', {}).get('blickOffset', 0), track.get('mainRef',{}).get('pitchOffset',0),
              {name:parameters[name] for name in ('pitchDelta','vibratoEnv') if name in parameters}]
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def read_profile(path, *, require_audio=True):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA or not isinstance(data.get('tracks'),dict) or not data['tracks']:
        raise ValueError('声库校准配置格式无效。')
    result = copy.deepcopy(data)
    if result.get('fit_strategy','detailed') not in ('detailed','preserve','match'):
        raise ValueError('校准拟合方式无效。')
    for voice, row in result['tracks'].items():
        if voice not in ('lead','backing','vocals') or not isinstance(row,dict):
            raise ValueError('声库校准声部无效。')
        if row.get('render_channel_mode','standard') not in ('standard','sv-extra','sv-isolated'):
            raise ValueError('校准配置中的气声输出方式无效。')
        if (not isinstance(row.get('score_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',row['score_sha256'])
                or not isinstance(row.get('database'),dict) or not isinstance(row['database'].get('name'),str)
                or not row['database']['name'].strip() or not isinstance(row.get('voice',{}),dict)):
            raise ValueError('校准配置缺少音符身份或目标声库。')
        probes = row.get('probes')
        if not isinstance(probes,list) or not 1<=len(probes)<=len(CONTROLS):
            raise ValueError('校准配置需要 1–6 个参数试音。')
        names = []
        for probe in probes:
            name = probe.get('parameter') if isinstance(probe,dict) else None
            step = probe.get('step') if isinstance(probe,dict) else None
            if name not in CONTROLS or name in names or type(step) not in (int,float) or not math.isfinite(step):
                raise ValueError('参数试音名称或数值无效。')
            lo,hi,neutral,_ = CONTROLS[name]
            if abs(step)<1e-6 or not lo<=neutral+step<=hi:
                raise ValueError('参数试音超出有效范围。')
            names.append(name)
        for entry,key in [(row,'baseline')]+[(probe,'audio') for probe in probes]:
            value=entry.get(key)
            if not isinstance(value,str) or not value.strip(): raise ValueError('校准音频路径为空。')
            audio=(path.parent/value).resolve()
            if require_audio and not audio.is_file():
                raise FileNotFoundError('缺少校准试音：'+audio.name+'。请先在同一声库中渲染校准工程。')
            entry[key]=str(audio)
    return result


def signature(path):
    from common import file_hash
    data=read_profile(path)
    import calibration_refinement
    return {'profile':file_hash(path),'adapter':file_hash(Path(__file__)),
            'refinement':file_hash(Path(calibration_refinement.__file__)),
            'audio':[(voice,file_hash(row['baseline']),[(p['parameter'],file_hash(p['audio'])) for p in row['probes']])
                     for voice,row in sorted(data['tracks'].items())]}


def create_probe_set(project, destination, voice='lead', track_index=0):
    """Make neutral/one-control projects; the user renders them in their SV."""
    if voice not in ('lead','backing','vocals'): raise ValueError('请选择主唱、和声或人声。')
    data=json.loads(Path(project).read_bytes().removesuffix(b'\x00').decode('utf-8-sig'))
    vocals=[t for t in data.get('tracks',[]) if not t.get('mainRef',{}).get('isInstrumental')]
    if type(track_index) is not int or not 0<=track_index<len(vocals): raise ValueError('请选择存在的演唱轨。')
    track=vocals[track_index]
    if not track.get('mainGroup',{}).get('notes') or track.get('groups'):
        raise ValueError('校准首轮需要音符位于主音符组的演唱轨。')
    if not track.get('mainRef',{}).get('database',{}).get('name'):
        raise ValueError('请先在 Synthesizer V 中给工程指定目标声库并保存，再生成校准工程。')
    destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=False)
    track=copy.deepcopy(track)
    _clear_control_offsets(track)
    profile={'schema':SCHEMA,'tracks':{voice:{'score_sha256':score_identity(track,data),
        'voice':copy.deepcopy(track.get('mainRef',{}).get('voice',{})),
        'database':copy.deepcopy(track['mainRef']['database']),'baseline':voice+'-neutral.wav','probes':[]}}}
    for name in ('neutral',*CONTROLS):
        variant=copy.deepcopy(data);solo=copy.deepcopy(track)
        variant['tracks']=[solo]
        solo['mainRef']['mute']=False
        solo['mixer']={'mute':False,'solo':False,'gainDecibel':0,'pan':0};solo['renderEnabled']=True
        variant.pop('projectMixer',None)
        params=solo['mainGroup'].setdefault('parameters',{})
        for control,(_,_,neutral,_) in CONTROLS.items():
            params[control]={'mode':'linear','points':[0,neutral]}
        if name!='neutral':
            _,_,neutral,step=CONTROLS[name]
            params[name]['points']=[0,neutral+step]
            profile['tracks'][voice]['probes'].append({'parameter':name,'step':step,'audio':voice+'-'+name+'.wav'})
        (destination/(voice+'-'+name+'.svp')).write_text(json.dumps(variant,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    path=destination/'calibration.json'
    path.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


def _clear_control_offsets(track):
    keys=['param'+name[0].upper()+name[1:] for name in CONTROLS]
    voice=track.get('mainRef',{}).get('voice',{})
    removed_voice=[key for key in keys if key in voice]
    for key in removed_voice: voice.pop(key,None)
    notes=[]
    for index,note in enumerate(track.get('mainGroup',{}).get('notes',[])):
        attributes=note.get('attributes',{});removed=[key for key in keys if key in attributes]
        for key in removed: attributes.pop(key,None)
        if removed:notes.append({'note_index':index,'keys':removed})
    return {'track_voice':removed_voice,'notes':notes,
            'removed_count':len(removed_voice)+sum(len(row['keys']) for row in notes)}


def acoustic_features(path, notes, cancel=lambda:None, *, channel_mode='standard'):
    """Bounded WORLD cues plus spectral-envelope cepstra on one 20 ms grid."""
    import numpy as np
    import soundfile as sf
    from scipy.fft import dct
    from scipy.signal import resample_poly
    from vocal_timbre import feature_frames,select_channels
    frames,metadata=feature_frames(path,notes,cancel,channel_mode=channel_mode)
    cepstra=np.zeros((len(frames),8))
    with sf.SoundFile(str(path)) as audio:
        rate=audio.samplerate
        for first in range(0,audio.frames,8*rate):
            cancel()
            left,right=max(0,first-rate//10),min(audio.frames,first+8*rate+rate//10)
            audio.seek(left); block=audio.read(right-left,dtype='float64',always_2d=True)
            block=select_channels(block,channel_mode)
            block-=block.mean(axis=0)
            channel=block[:,int(np.argmax(np.mean(block*block,axis=0)))]
            divisor=math.gcd(rate,24000);x=resample_poly(channel,24000//divisor,rate//divisor)
            ids=np.flatnonzero((frames[:,0]>=first/rate)&(frames[:,0]<min(audio.frames,first+8*rate)/rate))
            centers=np.rint((frames[ids,0]-left/rate)*24000).astype(int)
            windows=np.pad(x,(720,720))[np.clip(centers[:,None]+np.arange(1440)[None,:],0,len(x)+1439)]
            spectrum=abs(np.fft.rfft(windows*np.hanning(1440),n=2048))**2
            frequency=np.fft.rfftfreq(2048,1/24000)
            anchors=700*(10**(np.linspace(2595*np.log10(1+80/700),2595*np.log10(1+8000/700),34)/2595)-1)
            bank=np.maximum(0,np.minimum((frequency[None,:]-anchors[:-2,None])/(anchors[1:-1,None]-anchors[:-2,None]),
                                         (anchors[2:,None]-frequency[None,:])/(anchors[2:,None]-anchors[1:-1,None])))
            power=np.maximum(spectrum@bank.T,1e-20)
            cepstra[ids]=dct(10*np.log10(power),type=2,norm='ortho',axis=1)[:,1:9]
    features=np.column_stack([frames[:,4],frames[:,1],frames[:,2],frames[:,3],cepstra])
    return frames[:,0],features,frames[:,6].astype(bool),metadata


def fit_response(reference, baseline, probes, steps, *, strength=.6, active=None, cancel=lambda:None,
                 feature_weights=None,control_penalties=None):
    """Fit bounded local control deltas, rejecting weak or redundant probes."""
    import numpy as np
    from scipy.linalg import qr
    from scipy.optimize import lsq_linear
    from scipy.ndimage import median_filter,uniform_filter1d
    reference,baseline=np.asarray(reference,float),np.asarray(baseline,float)
    if (reference.shape!=baseline.shape or reference.ndim!=2 or len(reference)<10
            or not np.isfinite(reference).all() or not np.isfinite(baseline).all()
            or type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=1):
        raise ValueError('校准特征尺寸或强度无效。')
    active=np.ones(len(reference),bool) if active is None else np.asarray(active,bool)
    if active.shape!=(len(reference),) or active.sum()<10: raise ValueError('可靠的校准帧不足。')
    names=list(probes)
    if not names or set(names)!=set(steps) or any(name not in CONTROLS for name in names):
        raise ValueError('参数响应表无效。')
    weights=np.ones(reference.shape[1]) if feature_weights is None else np.asarray(feature_weights,float)
    penalties={} if control_penalties is None else control_penalties
    if (weights.shape!=(reference.shape[1],) or not np.isfinite(weights).all() or np.any(weights<=0)
            or not isinstance(penalties,dict) or set(penalties)-set(CONTROLS)
            or any(type(v) not in (int,float) or not math.isfinite(v) or v<1 for v in penalties.values())):
        raise ValueError('校准特征权重或参数约束无效。')
    scales=np.maximum(np.std(baseline[active],axis=0),1.)
    # Periodicity is dimensionless; keep it visible beside dB and cepstra.
    if reference.shape[1]>=4: scales[3]=max(float(np.std(baseline[active,3])),.05)
    derivatives=[]
    for name in names:
        probe=np.asarray(probes[name],float)
        if probe.shape!=reference.shape or not np.isfinite(probe).all(): raise ValueError('参数试音的特征不完整。')
        step=steps[name];lo,hi,neutral,_=CONTROLS[name]
        if type(step) not in (int,float) or not math.isfinite(step) or abs(step)<1e-6 or not lo<=neutral+step<=hi:
            raise ValueError('参数试音步长无效。')
        derivatives.append((probe-baseline)/scales*weights)
    matrix=np.stack(derivatives,axis=-1)
    stacked=matrix[active].reshape(-1,len(names))
    _,r,pivots=qr(stacked,mode='economic',pivoting=True)
    diagonal=abs(np.diag(r));limit=max(.05,diagonal.max(initial=0)*.025)
    rank=int(np.count_nonzero(diagonal>limit));chosen=sorted(pivots[:rank].tolist())
    if not chosen:
        return {},{'status':'skipped','reason':'no_identifiable_response','parameters':{name:'unresponsive_or_correlated' for name in names}}
    selected=[names[i] for i in chosen]
    lower,upper=[],[]
    for name in selected:
        lo,hi,neutral,_=CONTROLS[name];a,b=sorted(((lo-neutral)/steps[name],(hi-neutral)/steps[name]))
        lower.append(max(-1.,a));upper.append(min(1.,b))
    target=(reference-baseline)/scales*weights; coefficients=np.zeros((len(reference),len(chosen)))
    regularizer=np.diag([penalties.get(name,1.) for name in selected])
    for index in np.flatnonzero(active):
        if index%64==0: cancel()
        j=matrix[index][:,chosen]
        regularization=max(.05,float(np.linalg.norm(j,ord='fro'))*.15)
        result=lsq_linear(np.vstack([j,regularization*regularizer]),
            np.r_[target[index],np.zeros(len(chosen))],bounds=(lower,upper),tol=1e-5)
        coefficients[index]=result.x
    # Smooth only within supported runs, without carrying gestures into silence.
    transitions=np.diff(np.r_[False,active,False].astype(int))
    for left,right in zip(np.where(transitions==1)[0],np.where(transitions==-1)[0]):
        coefficients[left:right]=uniform_filter1d(median_filter(coefficients[left:right],size=(3,1),mode='nearest'),size=3,axis=0,mode='nearest')
    coefficients=np.clip(coefficients,lower,upper)*strength
    curves={}
    for index,name in enumerate(selected):
        delta=coefficients[:,index]*steps[name]
        threshold=.05 if name=='loudness' else 1. if name=='toneShift' else .004
        supported=active&(abs(delta)>threshold)
        edges=np.diff(np.r_[False,supported,False].astype(int))
        runs=np.where(edges==-1)[0]-np.where(edges==1)[0]
        if runs.max(initial=0)>=3: curves[name]=CONTROLS[name][2]+delta
        else: coefficients[:,index]=0
    predicted=np.einsum('tfc,tc->tf',matrix[:,:,chosen],coefficients)
    before=float(np.mean(target[active]**2));after=float(np.mean((target-predicted)[active]**2))
    improvement=1-after/max(before,1e-12)
    if before<1e-8 or improvement<.05:
        return {},{'status':'skipped','reason':'no_predicted_improvement','predicted_reduction':improvement}
    return curves,{'status':'applied','identifiable_parameters':selected,'requested_parameters':names,
        'skipped_parameters':[name for name in names if name not in selected],
        'below_output_resolution':[name for name in selected if name not in curves],
        'supported_frames':int(active.sum()),'predicted_reduction':round(improvement,5),
        'scope':'local_acoustic_response_fit','perceptual_improvement_verified':False}


def apply_project(data, sources, profile, strength=.6, cancel=lambda:None, cache=None, progress=lambda text:None,*,strategy=None,density_mode='dense',finalize=True):
    import numpy as np
    import soundfile as sf
    from vocal_expression import TempoMap,automation,simplify
    if not isinstance(data,dict):raise ValueError('SVP 工程数据格式无效。')
    if type(finalize) is not bool:raise ValueError('SVP 参数后处理开关必须为布尔值。')
    output=copy.deepcopy(data);sources=list(sources);spec=read_profile(profile)
    strategy=spec.get('fit_strategy','detailed') if strategy is None else strategy
    if strategy not in ('detailed','preserve','match'):raise ValueError('校准拟合方式无效。')
    clock=TempoMap(output['time']['tempo'])
    vocals=[track for track in output['tracks'] if not track.get('mainRef',{}).get('isInstrumental')]
    if len(vocals)!=len(sources): raise ValueError('校准声部与工程轨道不一致。')
    reports=[]
    for track,source in zip(vocals,sources):
        cancel();voice=source['voice']
        cache_key=source.get('cache_key',voice)
        if not isinstance(cache_key,str) or not cache_key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in cache_key):
            raise ValueError('校准缓存标识无效。')
        if voice not in spec['tracks']:
            reports.append({'voice':voice,'status':'skipped','reason':'no_profile_for_voice'});continue
        row=spec['tracks'][voice]
        if score_identity(track,output)!=row['score_sha256']:
            raise ValueError('校准试音对应的音符或速度表已改变，请为当前工程重新生成校准试音。')
        current=track.get('mainRef',{}).get('database',{})
        if current.get('name') and current['name']!=row['database']['name']:
            raise ValueError('工程声库与校准声库不一致。')
        offset=track.get('mainRef',{}).get('blickOffset',0)
        notes=[(clock.to_seconds(n['onset']+offset),clock.to_seconds(n['onset']+n['duration']+offset),n['pitch']) for n in track['mainGroup']['notes']]
        if not notes: continue
        def features(audio,key,channel_mode='standard'):
            return cached_features(audio,notes,None if cache is None else Path(cache)/(cache_key+'-'+key+'.npz'),cancel,
                                    channel_mode=channel_mode)
        progress('分析参考演唱 · 1 / '+str(2+len(row['probes'])))
        times,reference,mask,_=features(source['path'],'reference')
        def measured(audio):
            duration=sf.info(str(audio)).duration
            if duration<max(end for start,end,pitch in notes)-.1:
                raise ValueError('校准试音缺少句尾，请从工程起点导出完整演唱轨。')
            t,x,valid,_=features(audio,Path(audio).stem,row.get('render_channel_mode','standard'))
            # A reference can include an instrumental outro after the final
            # written note. Missing rendered tail frames carry no vocal evidence.
            return np.column_stack([np.interp(times,t,x[:,i]) for i in range(x.shape[1])]),np.interp(times,t,valid.astype(float),left=0,right=0)>.8
        progress('分析基准试音 · 2 / '+str(2+len(row['probes'])))
        baseline,valid=measured(row['baseline']);mask &= valid
        probes,steps={},{}
        for index,probe in enumerate(row['probes'],3):
            progress('分析参数试音 '+probe['parameter']+' · '+str(index)+' / '+str(2+len(row['probes'])))
            x,valid=measured(probe['audio']);mask &= valid
            probes[probe['parameter']]=x;steps[probe['parameter']]=probe['step']
        if np.count_nonzero(mask)<10:
            reports.append({'voice':voice,'status':'skipped','reason':'insufficient_supported_frames'});continue
        progress('联合拟合参数…')
        from calibration_refinement import refine_curves,FEATURE_WEIGHTS,CONTROL_PENALTIES
        curves,report=fit_response(reference,baseline,probes,steps,strength=strength,active=mask,cancel=cancel,
            feature_weights=FEATURE_WEIGHTS if strategy=='preserve' else None,
            control_penalties=CONTROL_PENALTIES if strategy=='preserve' else None)
        support=mask
        if curves and strategy=='detailed':
            from calibration_refinement import detail_curves
            curves,support,detail=detail_curves(curves,times,mask,notes,reference,cancel=cancel)
            report['detail']=detail
        if curves and strategy=='preserve':
            progress('稳定长音与音色参数…')
            curves,support,refinement=refine_curves(curves,times,mask,notes,reference,baseline,strength,cancel=cancel)
            # Report the constrained curves' predicted residual, not the raw
            # solver's residual. This remains a local linear estimate.
            scales=np.maximum(np.std(baseline[mask],axis=0),1.);scales[3]=max(float(np.std(baseline[mask,3])),.05)
            target=(reference-baseline)/scales*np.asarray(FEATURE_WEIGHTS)
            predicted=np.zeros_like(target)
            for name,values in curves.items():
                predicted+=(probes[name]-baseline)/scales*np.asarray(FEATURE_WEIGHTS)*((values-CONTROLS[name][2])/steps[name])[:,None]
            before=float(np.mean(target[mask]**2));after=float(np.mean((target-predicted)[mask]**2))
            report['raw_predicted_reduction']=report.get('predicted_reduction')
            report['predicted_reduction']=round(1-after/max(before,1e-12),5)
            report['refinement']=refinement
            if not curves or after>=before:
                curves={};report.update(status='skipped',reason='no_supported_conservative_change')
        if curves:
            previous_voice=copy.deepcopy(track.get('mainRef',{}).get('voice',{}))
            track.setdefault('mainRef',{})['database']=copy.deepcopy(row['database'])
            track['mainRef']['voice']=copy.deepcopy(row.get('voice',{}))
            report['bias_cleanup']=_clear_control_offsets(track)
            offset_keys=['param'+name[0].upper()+name[1:] for name in CONTROLS]
            cleanup=report['bias_cleanup']
            cleanup['track_voice']=sorted(set(cleanup['track_voice'])|
                                           {key for key in offset_keys if isinstance(previous_voice,dict) and key in previous_voice})
            cleanup['removed_count']=len(cleanup['track_voice'])+sum(len(row['keys']) for row in cleanup['notes'])
            parameters=track['mainGroup'].setdefault('parameters',{})
            cleanup['control_curves_removed']=[name for name in CONTROLS if name in parameters]
            for name in CONTROLS:parameters.pop(name,None)
            for name,values in curves.items():
                neutral=CONTROLS[name][2];values=np.where(support,values,neutral)
                if strategy=='detailed':
                    points=list(zip(times.tolist(),values.tolist()))
                else:
                    points=simplify(list(zip(times.tolist(),values.tolist())),tolerance=.05 if name=='loudness' else 1. if name=='toneShift' else .004)
                track['mainGroup'].setdefault('parameters',{})[name]=automation(points,clock,offset)
            report['written_points']={name:len(track['mainGroup']['parameters'][name]['points'])//2 for name in curves}
        report['voice']=voice;report['strategy']=strategy;reports.append(report)
    if finalize:
        from calibration_refinement import finalize_expression
        density=finalize_expression(output,mode=density_mode,cancel=cancel)
        deferred=[]
        for track,report in zip(vocals,reports):
            if report.get('status')=='applied':
                report['written_points']={name:len(track['mainGroup'].get('parameters',{}).get(name,{}).get('points',[]))//2
                                          for name in report.get('written_points',{})}
    else:
        density={'status':'deferred','deferred':True,'mode':density_mode};deferred=['expression_density']
    cancel()
    return output,{'schema':SCHEMA,'tracks':reports,'strength':strength,'strategy':strategy,
            'expression_density':density,'deferred':deferred,
            'voicebank_calibrated':any(row.get('status')=='applied' for row in reports),
            'time_unchanged':output.get('time')==data.get('time'),'project_writes':0}


def apply_svp(path, sources, profile, strength=.6, cancel=lambda:None, cache=None, progress=lambda text:None,*,strategy=None,density_mode='dense',finalize=True):
    path=Path(path);raw=path.read_bytes()
    data=json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
    output,report=apply_project(data,sources,profile,strength,cancel,cache=cache,progress=progress,
        strategy=strategy,density_mode=density_mode,finalize=finalize)
    Path(path).write_bytes(json.dumps(output,ensure_ascii=False,separators=(',',':')).encode('utf-8')+
                           (b'\x00' if raw.endswith(b'\x00') else b''))
    return report

def cached_features(path, notes, cache=None, cancel=lambda:None, *, channel_mode='standard'):
    """One small feature snapshot per render; never copy calibration audio."""
    if cache is None: return acoustic_features(path,notes,cancel,channel_mode=channel_mode)
    import os
    import tempfile
    import zipfile
    import numpy as np
    from common import file_hash,native_path
    from vocal_timbre import _feature_code_signature
    from expression_component import SPEC
    cancel();digest=file_hash(path)
    # Fitting-only edits must not reanalyse unchanged audio. Fingerprint the
    # acoustic extractor independently from the solver and temporal guards.
    import ast
    module=ast.parse(Path(__file__).read_text(encoding='utf-8-sig'))
    extractor=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='acoustic_features')
    identity=[digest,notes,hashlib.sha256(ast.dump(extractor,include_attributes=False).encode()).hexdigest(),
              _feature_code_signature(),SPEC['sha256'],channel_mode]
    key=hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()
    try:
        with np.load(native_path(cache),allow_pickle=False) as stored:
            times,x,valid=stored['times'],stored['features'],stored['valid']
            metadata=json.loads(str(stored['metadata']))
            if (str(stored['signature'])==key and times.ndim==1 and len(times)>0
                    and x.shape==(len(times),12) and valid.shape==times.shape and valid.dtype.kind=='b'
                    and np.isfinite(x).all() and np.isfinite(times).all() and np.all(np.diff(times)>0)
                    and isinstance(metadata,dict)):
                cancel();return times,x,valid,{**metadata,'cache_hit':True}
    except (OSError,ValueError,KeyError,EOFError,zipfile.BadZipFile): pass
    times,x,valid,metadata=acoustic_features(path,notes,cancel,channel_mode=channel_mode)
    cancel()
    if digest!=file_hash(path): raise ValueError('校准分析期间音频发生变化，请重试。')
    cache=Path(cache);Path(native_path(cache.parent)).mkdir(parents=True,exist_ok=True)
    pending=None
    try:
        with tempfile.NamedTemporaryFile(dir=native_path(cache.parent),suffix='.pending',delete=False) as stream:
            pending=Path(stream.name)
            np.savez_compressed(stream,signature=key,times=times,features=x,valid=valid,
                                metadata=json.dumps(metadata,ensure_ascii=False,allow_nan=False))
        cancel();os.replace(pending,native_path(cache))
    finally:
        if pending is not None: pending.unlink(missing_ok=True)
    return times,x,valid,{**metadata,'cache_hit':False}
